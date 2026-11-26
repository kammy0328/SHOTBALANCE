"""정적 미리보기(web/demo)용 데이터를 실제 엔진으로 만들어 낸다.

    python tools/build_demo.py

합성 타임라인을 엔진에 연결 → 분석 전 상태(before) → 자동 매칭 후 상태(after)를 그대로 저장하고,
그룹을 바꿨을 때 브라우저 쪽 데모가 다시 계산할 수 있도록 컷별 정답(truth)도 함께 넣는다.
"""
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from balance.core import Engine  # noqa: E402
from balance.media.demo_shots import DEMO  # noqa: E402

OUT = ROOT / "balance" / "web" / "demo"


def wait(cond, timeout=600):
    t = time.time()
    while not cond():
        if time.time() - t > timeout:
            raise TimeoutError
        time.sleep(0.05)


def settle(eng):
    done = []
    eng.rworker.submit(lambda: done.append(1))
    wait(lambda: done)


def main():
    tmp = Path(tempfile.mkdtemp(prefix="balance-demo-"))
    eng = Engine(data_root=tmp, workers=2)
    eng.connect(demo=True)
    wait(lambda: eng.conn["status"] == "demo")
    wait(lambda: len(eng.quick) == len(eng.by_key))
    settle(eng)
    before = eng.state()

    eng.analyze()
    wait(lambda: eng.job is not None, 30)
    wait(lambda: eng.job is None)
    settle(eng)
    after = eng.state()

    # 미지정 컷도 한 번 분석해 종류 · 얼굴 수 · 썸네일을 얻는다 (데모에서 그룹으로 묶었을 때 쓴다)
    loose = [c["key"] for c in after["cuts"] if c["group"] is None]
    gid = eng.create_group(loose, "tmp")
    eng.analyze([gid])
    wait(lambda: eng.job is None)
    settle(eng)
    extra = eng.state()

    shutil.rmtree(OUT, ignore_errors=True)
    (OUT / "thumbs").mkdir(parents=True)
    meta = {}
    for c in extra["cuts"]:
        m = {"kind": c["kind"], "faces": c["faces"], "edge": c["edge"]}
        for field in ("thumb", "thumb_head", "thumb_tail"):
            name = c[field].split("/")[-1].split("?")[0]
            shutil.copyfile(eng.cache / "thumbs" / name, OUT / "thumbs" / name)
            m[field] = f"demo/thumbs/{name}"
        meta[c["key"]] = m

    def fix(state):
        for c in state["cuts"]:
            m = meta[c["key"]]
            c["thumb"] = m["thumb"]                   # 분석 전에도 같은 그림을 쓴다
            for field in ("thumb_head", "thumb_tail"):
                c[field] = m[field] if c["analyzed"] else None
            c["pending"] = False
        state["conn"]["product"] = "DaVinci Resolve Studio (데모)"
        state["job"] = None
        state["can_undo"] = state["can_redo"] = False
        return state

    shots = {s.name: s for s, *_ in DEMO}
    truth = {}
    for c in after["cuts"]:
        s = shots[c["name"]]
        truth[c["key"]] = {"exp": s.cam_stops, "wb": list(s.cam_wb), "space": s.space, "flare": list(s.flare), "sat": s.cam_sat,
                           "idents": [f.ident for f in s.figures]}

    data = {"before": fix(before), "after": fix(after), "truth": truth, "meta": meta}
    (OUT / "demo.json").write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    eng.shutdown()
    shutil.rmtree(tmp, ignore_errors=True)
    n = len(list((OUT / "thumbs").glob("*.jpg")))
    print(f"demo.json {(OUT / 'demo.json').stat().st_size / 1024:.0f} KB, thumbs {n}")


if __name__ == "__main__":
    main()
