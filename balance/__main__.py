"""실행 진입점:  python -m balance  [--demo] [--port N] [--no-window]"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from pathlib import Path

from . import APP_NAME, __version__, config


def _running_instance(lock: Path) -> str | None:
    """이미 떠 있는 인스턴스가 있으면 그 주소를 돌려준다."""
    try:
        port = json.loads(lock.read_text(encoding="utf-8"))["port"]
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/ping", timeout=0.7) as r:
            if json.loads(r.read()).get("app") == APP_NAME:
                return f"http://127.0.0.1:{port}/"
    except Exception:  # noqa: BLE001
        return None
    return None


def _setup_log() -> None:
    """콘솔이 없는 실행(pythonw, exe)에서는 출력이 갈 곳이 없으므로 로그 파일로 돌린다."""
    if sys.stdout is not None and sys.stderr is not None:
        return
    try:
        path = config.data_dir() / "balance.log"
        if path.is_file() and path.stat().st_size > 1_000_000:
            path.unlink()
        f = open(path, "a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stdout or f
        sys.stderr = sys.stderr or f
    except OSError:
        pass


def _fatal(text: str) -> None:
    print(text, file=sys.stderr, flush=True)
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, text[:1500], APP_NAME, 0x10)
        except Exception:  # noqa: BLE001
            pass


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog=APP_NAME.lower(), description=f"{APP_NAME} {__version__} — Resolve 샷 밸런싱")
    ap.add_argument("--demo", action="store_true", help="Resolve 없이 합성 타임라인으로 실행")
    ap.add_argument("--port", type=int, default=0, help="서버 포트 (기본: 자동)")
    ap.add_argument("--no-window", action="store_true", help="창을 띄우지 않고 서버만 실행")
    ap.add_argument("--data", type=str, default=None, help="설정 · 캐시를 둘 폴더")
    args = ap.parse_args(argv)
    if args.data:
        os.environ["BALANCE_DATA_DIR"] = args.data
    _setup_log()

    try:
        from .core import Engine
        from .server import AppServer
        from .window import open_window

        lock = config.data_dir() / "instance.json"
        if not args.demo and not args.port:
            url = _running_instance(lock)
            if url:
                open_window(url, config.data_dir() / "webview")
                return 0
        server = AppServer(lambda notify: Engine(notify), port=args.port)
    except Exception:  # noqa: BLE001
        import traceback
        _fatal("ShotBalance 를 시작하지 못했습니다.\n\n" + traceback.format_exc())
        return 1

    server.start()
    try:
        lock.write_text(json.dumps({"port": server.port, "pid": os.getpid()}), encoding="utf-8")
    except OSError:
        pass
    server.engine.connect(demo=args.demo)
    url = server.url + ("?demo=1" if args.demo else "")
    print(f"{APP_NAME} {__version__}  {url}", flush=True)
    proc = None
    if not args.no_window:
        proc = open_window(url, config.data_dir() / "webview")
    try:
        server.wait(idle_exit=None if args.no_window else 8.0, proc=proc)
    finally:
        server.stop()
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
            except OSError:
                pass
        try:
            lock.unlink()
        except OSError:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
