"""로컬 HTTP 서버: 정적 UI, JSON API, 서버-전송 이벤트(SSE).

127.0.0.1 에만 열고, Host 헤더와 전용 헤더를 확인해 다른 사이트가 이 API 를 부르지 못하게 한다.
"""
from __future__ import annotations

import json
import mimetypes
import queue
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlparse

from . import APP_NAME, __version__, config
from .core import Engine

mimetypes.add_type("application/javascript", ".js")
mimetypes.add_type("font/woff2", ".woff2")
mimetypes.add_type("image/svg+xml", ".svg")


class Hub:
    """SSE 구독자 관리."""

    def __init__(self):
        self._lock = threading.Lock()
        self._subs: list[queue.Queue] = []
        self.last_seen = 0.0
        self.ever = False

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=256)
        with self._lock:
            self._subs.append(q)
            self.ever = True
            self.last_seen = time.time()
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            if q in self._subs:
                self._subs.remove(q)
            self.last_seen = time.time()

    def publish(self, event: dict) -> None:
        with self._lock:
            subs = list(self._subs)
        for q in subs:
            try:
                q.put_nowait(event)
            except queue.Full:
                pass

    @property
    def count(self) -> int:
        with self._lock:
            return len(self._subs)


def _actions(engine: Engine, quit_cb):
    E = ...

    def opt(d, k):
        return d[k] if k in d else E

    return {
        "connect": lambda d: engine.connect(bool(d.get("demo"))),
        "rescan": lambda d: engine.rescan(),
        "group/create": lambda d: engine.create_group(list(d.get("keys", [])), d.get("name")),
        "group/assign": lambda d: engine.assign(list(d.get("keys", [])), d.get("group")),
        "group/update": lambda d: engine.update_group(d["id"], name=d.get("name"), mode=d.get("mode"),
                                                      ref=opt(d, "ref"), color=d.get("color")),
        "group/delete": lambda d: engine.delete_group(d["id"]),
        "group/import": lambda d: engine.import_groups(d.get("source", "resolve")),
        "analyze": lambda d: engine.analyze(d.get("groups"), bool(d.get("force"))),
        "cancel": lambda d: engine.cancel(),
        "intent": lambda d: engine.intent(d.get("scope", "cut"), d.get("keys"), d.get("group"),
                                          float(d.get("exp", 0)), float(d.get("temp", 0)), float(d.get("tint", 0)),
                                          bool(d.get("reset"))),
        "cut": lambda d: engine.set_cut(list(d.get("keys", [])), locked=d.get("locked"), excluded=d.get("excluded"),
                                        use_exp=opt(d, "use_exp"), use_wb=opt(d, "use_wb"),
                                        use_black=opt(d, "use_black"), space=opt(d, "space")),
        "apply": lambda d: engine.apply(bool(d.get("force"))),
        "undo": lambda d: engine.undo(),
        "redo": lambda d: engine.redo(),
        "settings": lambda d: engine.set_settings(**d),
        "goto": lambda d: engine.goto(str(d.get("id", "")), bool(d.get("start"))),
        "models": lambda d: engine.download_models(),
        "quit": lambda d: quit_cb(),
    }


def make_handler(engine: Engine, hub: Hub, quit_cb):
    web = config.web_dir().resolve()
    actions = _actions(engine, quit_cb)

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = f"{APP_NAME}/{__version__}"

        def log_message(self, *args):  # 조용히
            pass

        # ------------------------------------------------------- helpers
        def _host_ok(self) -> bool:
            host = (self.headers.get("Host") or "").split(":")[0].strip("[]")
            return host in ("127.0.0.1", "localhost", "::1")

        def _send(self, code: int, body: bytes, ctype: str, cache: str = "no-store") -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", cache)
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json(self, obj, code: int = 200) -> None:
            self._send(code, json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
                       "application/json; charset=utf-8")

        # ----------------------------------------------------------- GET
        def do_GET(self):  # noqa: N802
            if not self._host_ok():
                return self._send(403, b"forbidden", "text/plain")
            path = unquote(urlparse(self.path).path)
            if path == "/api/state":
                return self._json(engine.state())
            if path == "/api/ping":
                return self._json({"app": APP_NAME, "version": __version__})
            if path == "/api/events":
                return self._events()
            if path.startswith("/thumb/"):
                p = engine.thumb_path(path[len("/thumb/"):])
                if p is None:
                    return self._send(404, b"", "text/plain")
                return self._send(200, p.read_bytes(), "image/jpeg", "public, max-age=31536000, immutable")
            rel = "index.html" if path in ("/", "") else path.lstrip("/")
            target = (web / rel).resolve()
            if web not in target.parents and target != web or not target.is_file():
                return self._send(404, b"not found", "text/plain")
            ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype == "application/javascript":
                ctype += "; charset=utf-8"
            cache = "public, max-age=604800" if target.suffix in (".woff2", ".png", ".svg", ".ico") else "no-cache"
            body = target.read_bytes()
            if target.name == "index.html":      # 서버가 붙어 있음을 UI 에 알린다 (없으면 UI 는 미리보기 모드로 뜬다)
                body = body.replace(b"<body>", b'<body data-live="1">', 1)
            return self._send(200, body, ctype, cache)

        do_HEAD = do_GET  # noqa: N815

        def _events(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "keep-alive")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            q = hub.subscribe()
            try:
                self.wfile.write(b"retry: 800\n\n")
                self.wfile.write(f"data: {json.dumps({'type': 'state', 'version': engine.version})}\n\n".encode())
                self.wfile.flush()
                while True:
                    try:
                        ev = q.get(timeout=2)      # 짧게: 창이 닫힌 것을 몇 초 안에 알아챈다
                    except queue.Empty:
                        self.wfile.write(b": keep-alive\n\n")
                        self.wfile.flush()
                        continue
                    if ev is None:
                        break
                    # 밀린 상태 알림은 마지막 것만 보낸다
                    if ev.get("type") == "state":
                        try:
                            while True:
                                nxt = q.get_nowait()
                                if nxt is None:
                                    return
                                if nxt.get("type") == "state":
                                    ev = nxt
                                else:
                                    self.wfile.write(f"data: {json.dumps(nxt, ensure_ascii=False)}\n\n".encode("utf-8"))
                        except queue.Empty:
                            pass
                    self.wfile.write(f"data: {json.dumps(ev, ensure_ascii=False)}\n\n".encode("utf-8"))
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
                pass
            finally:
                hub.unsubscribe(q)
                self.close_connection = True

        # ---------------------------------------------------------- POST
        def do_POST(self):  # noqa: N802
            if not self._host_ok() or self.headers.get("X-Balance") != "1":
                return self._send(403, b"forbidden", "text/plain")
            path = urlparse(self.path).path
            if not path.startswith("/api/"):
                return self._send(404, b"", "text/plain")
            name = path[len("/api/"):]
            fn = actions.get(name)
            if fn is None:
                return self._json({"ok": False, "error": "unknown action"}, 404)
            try:
                n = int(self.headers.get("Content-Length") or 0)
                data = json.loads(self.rfile.read(n) or b"{}") if n else {}
                if not isinstance(data, dict):
                    raise ValueError("body must be an object")
                result = fn(data)
            except (KeyError, ValueError, TypeError) as e:
                return self._json({"ok": False, "error": f"{type(e).__name__}: {e}"}, 400)
            out = {"ok": True, "state": engine.state()}
            if isinstance(result, str):
                out["result"] = result
            return self._json(out)

    return Handler


class _QuietServer(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):   # 창이 닫히며 끊긴 연결은 오류가 아니다
        import sys
        if isinstance(sys.exc_info()[1], (ConnectionError, TimeoutError, OSError)):
            return
        super().handle_error(request, client_address)


class AppServer:
    def __init__(self, engine_factory, port: int = 0):
        self.hub = Hub()
        self.engine: Engine = engine_factory(self.hub.publish)
        self._quit = threading.Event()
        handler = make_handler(self.engine, self.hub, self._quit.set)
        self.httpd = _QuietServer(("127.0.0.1", port), handler)
        self.port = self.httpd.server_address[1]
        self._thread = threading.Thread(target=self.httpd.serve_forever, daemon=True, name="http")

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/"

    def start(self) -> None:
        self._thread.start()

    def wait(self, idle_exit: float | None = None, proc=None, startup: float = 120.0) -> None:
        """끝나는 조건
          - UI 가 quit 을 요청했다
          - UI 가 한 번 붙었다가 모두 닫히고 idle_exit 초가 지났다 (새로고침은 그 안에 다시 붙는다)
          - 창 프로세스가 끝났고 붙어 있는 UI 가 없다 (창이 뜨기 전의 짧은 순간은 제외)
          - startup 초가 지나도록 UI 가 한 번도 붙지 않았다 (창을 띄우는 모드에서만)
        """
        t0 = time.time()
        trusted = False          # 창 프로세스가 실제로 떠 있는 것을 확인했는가 (기존 브라우저에 넘기고 바로 끝나는 경우 제외)
        gone = None
        try:
            while not self._quit.wait(0.5):
                now = time.time()
                if proc is not None:
                    alive = proc.poll() is None
                    if alive and now - t0 > 5.0:
                        trusted = True
                    if not alive and trusted:
                        gone = gone or now
                        if now - gone > 1.5:
                            break        # 창이 닫혔다
                if self.hub.count:
                    continue
                if self.hub.ever:
                    if idle_exit and now - self.hub.last_seen > idle_exit:
                        break
                elif idle_exit and now - t0 > startup:
                    break
        except KeyboardInterrupt:
            pass

    def stop(self) -> None:
        self.hub.publish(None)  # type: ignore[arg-type]
        self.engine.shutdown()
        self.httpd.shutdown()
        self.httpd.server_close()
