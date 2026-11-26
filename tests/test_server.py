"""HTTP 서버: 정적 파일, API, 보호 장치, 이벤트 스트림."""
import json
import threading
import time
import urllib.error
import urllib.request

import pytest

from balance.core import Engine
from balance.server import AppServer


@pytest.fixture(scope="module")
def srv(tmp_path_factory):
    root = tmp_path_factory.mktemp("srv")
    s = AppServer(lambda notify: Engine(notify, data_root=root, workers=1))
    s.start()
    s.engine.connect(demo=True)
    t = time.time()
    while s.engine.conn["status"] != "demo" and time.time() - t < 60:
        time.sleep(0.05)
    yield s
    s.stop()


def get(srv, path, headers=None):
    req = urllib.request.Request(srv.url + path, headers=headers or {})
    with urllib.request.urlopen(req, timeout=10) as r:
        return r.status, r.headers, r.read()


def post(srv, name, body=None, headers=None):
    h = {"Content-Type": "application/json", "X-Balance": "1", **(headers or {})}
    req = urllib.request.Request(srv.url + "api/" + name, data=json.dumps(body or {}).encode(), headers=h, method="POST")
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())


def test_static_and_live_flag(srv):
    code, hdr, body = get(srv, "")
    assert code == 200 and b'<body data-live="1">' in body
    for path, ctype in (("css/app.css", "text/css"), ("js/app.js", "javascript"), ("fonts/BarlowSemiCondensed-Medium.woff2", "font/woff2")):
        code, hdr, body = get(srv, path)
        assert code == 200 and ctype in hdr["Content-Type"] and len(body) > 1000
    with pytest.raises(urllib.error.HTTPError) as e:
        get(srv, "../store.py")
    assert e.value.code == 404
    with pytest.raises(urllib.error.HTTPError) as e:
        get(srv, "%2e%2e/%2e%2e/setup.py")
    assert e.value.code == 404


def test_state_and_thumbs(srv):
    t = time.time()
    while time.time() - t < 60:
        st = json.loads(get(srv, "api/state")[2])
        if all(c["thumb"] for c in st["cuts"]):
            break
        time.sleep(0.2)
    assert len(st["cuts"]) == 28 and st["conn"]["status"] == "demo"
    code, hdr, body = get(srv, st["cuts"][0]["thumb"])
    assert code == 200 and hdr["Content-Type"] == "image/jpeg" and body[:2] == b"\xff\xd8"


def test_guards(srv):
    for kw in ({"headers": {"X-Balance": "0"}}, {"headers": {"Host": "evil.example"}}):
        with pytest.raises(urllib.error.HTTPError) as e:
            post(srv, "undo", **kw)
        assert e.value.code == 403
    with pytest.raises(urllib.error.HTTPError) as e:
        get(srv, "api/state", {"Host": "evil.example"})
    assert e.value.code == 403
    with pytest.raises(urllib.error.HTTPError) as e:
        post(srv, "nope")
    assert e.value.code == 404


def test_actions_roundtrip_and_events(srv):
    events = []

    def listen():
        req = urllib.request.Request(srv.url + "api/events")
        with urllib.request.urlopen(req, timeout=30) as r:
            for raw in r:
                line = raw.decode().strip()
                if line.startswith("data:"):
                    events.append(json.loads(line[5:]))
                if len(events) >= 3:
                    return

    th = threading.Thread(target=listen, daemon=True)
    th.start()
    time.sleep(0.3)
    st = json.loads(get(srv, "api/state")[2])
    key = st["cuts"][0]["key"]
    r = post(srv, "intent", {"scope": "cut", "keys": [key], "exp": 1 / 3})
    assert r["ok"] and abs(r["state"]["cuts"][0]["total"]["cut_exp"] - 1 / 3) < 1e-3
    r = post(srv, "group/create", {"keys": [c["key"] for c in st["cuts"] if c["group"] is None], "name": "밤"})
    assert r["ok"] and r["result"].startswith("g")
    r = post(srv, "undo")
    r = post(srv, "undo")
    assert r["state"]["cuts"][0]["total"]["cut_exp"] == 0
    th.join(timeout=5)
    assert any(e.get("type") == "state" for e in events)
