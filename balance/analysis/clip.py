"""컷 하나를 분석해 매칭에 필요한 측정값을 만든다."""
from __future__ import annotations

from typing import Callable

import numpy as np

from ..color import preview, spaces
from . import encoding, measure, temporal
from .faces import FaceEngine, similarity

#: 측정 방식이 바뀌면 올린다. 캐시된 분석 결과가 무효가 된다.
ANALYSIS_VERSION = 4

STAT_SIDE = 480        # 통계용 프레임 긴 변
FACE_SIDE = 1280       # 얼굴·스킨 측정용 프레임 긴 변
MAX_REP = 3            # 스킨을 재는 대표 프레임 수
MAX_FACES = 8          # 프레임당 다루는 얼굴 수


class Cancelled(Exception):
    pass


def _cluster(obs: list[dict]) -> list[dict]:
    """대표 프레임들에서 얻은 얼굴 관측을 컷 안의 인물 단위로 묶는다."""
    groups: list[list[dict]] = []
    for o in sorted(obs, key=lambda x: -x["quality"]):
        best, best_s = None, 0.0
        for g in groups:
            s = max(similarity(o, m) for m in g)
            if o.get("ident") is None and o.get("emb") is None:
                # 임베딩이 없으면 화면 위치로 묶는다
                s = max(_box_iou(o["box"], m["box"]) for m in g)
                s = 1.0 if s > 0.3 else 0.0
            if s > best_s:
                best, best_s = g, s
        if best is not None and best_s >= 0.5:
            best.append(o)
        else:
            groups.append([o])
    out = []
    for g in groups:
        w = np.array([m["quality"] * m["n"] for m in g], np.float64)
        w = w / w.sum()
        logs = np.log2(np.maximum(np.array([m["rgb"] for m in g], np.float64), 1e-6))
        rgb = 2.0 ** (logs * w[:, None]).sum(axis=0)
        spread = float(np.ptp(logs.mean(axis=1))) if len(g) > 1 else 0.0
        embs = [m["emb"] for m in g if m.get("emb") is not None]
        emb = None
        if embs:
            e = np.mean(np.asarray(embs, np.float32), axis=0)
            emb = (e / max(float(np.linalg.norm(e)), 1e-9)).tolist()
        q = float(np.mean([m["quality"] for m in g])) * (1.0 if spread < 0.15 else 0.8)
        out.append(dict(rgb=[float(v) for v in rgb], quality=round(q, 3), n=int(sum(m["n"] for m in g)),
                        count=len(g), emb=emb, ident=g[0].get("ident"),
                        size=float(np.median([m["size"] for m in g])),
                        box=[float(v) for v in g[0]["box"]], clip=float(np.mean([m["clip"] for m in g]))))
    out.sort(key=lambda c: -(c["size"] * c["quality"]))
    return out


def _box_iou(a, b) -> float:
    x0, y0 = max(a[0], b[0]), max(a[1], b[1])
    x1, y1 = min(a[0] + a[2], b[0] + b[2]), min(a[1] + a[3], b[1] + b[3])
    inter = max(0.0, x1 - x0) * max(0.0, y1 - y0)
    union = a[2] * a[3] + b[2] * b[3] - inter
    return inter / union if union > 0 else 0.0


def analyze_clip(open_source: Callable, src_in: int, src_out: int,
                 resolve_space: Callable[[dict], str], engine: FaceEngine | None = None,
                 name: str = "", cancel: Callable[[], bool] | None = None) -> dict:
    """측정 결과(dict, JSON 으로 저장 가능)와 썸네일 JPEG 들을 돌려준다.
    썸네일: {"": 대표 프레임, "h": 컷의 첫 프레임, "t": 컷의 끝 프레임}

    open_source()            → VideoSource 또는 같은 인터페이스의 객체
    resolve_space(추정결과)  → 이 컷에 쓸 입력 색공간 id
    """
    def check():
        if cancel is not None and cancel():
            raise Cancelled()

    src = open_source()
    try:
        n_total = src.info.n_frames or (src_out - src_in)
        if src.info.n_frames:
            src_out = min(src_out, src.info.n_frames)
            src_in = min(src_in, max(src_out - 1, 0))
        want_sparse, burst = temporal.sample_plan(src_in, src_out)
        fast = hasattr(src, "read_fast")
        codes: dict[int, np.ndarray] = {}
        sparse: list[int] = []
        for i in sorted(set(want_sparse) | set(burst)):
            check()
            if i in burst or not fast:
                codes[i] = src.read(i, STAT_SIDE)
                if i in want_sparse:
                    sparse.append(i)
                continue
            j, code = src.read_fast(i, src_in, STAT_SIDE)     # 롱GOP 는 근처 키프레임으로 대신한다
            if src_in <= j < src_out and j not in codes:
                codes[j] = code
                sparse.append(j)
        burst = [i for i in burst if i not in sparse]
        if not sparse:                                         # 아주 짧은 컷
            sparse, burst = burst[:1], burst[1:]
        order = sorted(codes)

        # 1) 신호 통계 → 입력 색공간 추정 → 사용할 색공간 결정
        sigs = [measure.signal_stats(codes[i]) for i in order]
        sig = encoding.merge_signal(sigs)
        g = encoding.guess(sig, src.info, name)
        space = resolve_space(g)

        # 2) 프레임별 선형 통계 → 시간 분류
        stats: dict[int, dict] = {}
        lins: dict[int, np.ndarray] = {}
        for i in order:
            lin = spaces.to_dwg_linear(codes[i], space)
            stats[i] = measure.frame_stats(lin, codes[i])
            lins[i] = lin
        tmp = temporal.classify(sparse, [stats[i]["level"] for i in sparse],
                                burst, [stats[i]["level"] for i in burst])
        rep = temporal.pick_spread(tmp["rep_frames"], MAX_REP)
        offs = {i: tmp["rep"] - stats[i]["level"] for i in rep}
        merged = measure.merge_stats([stats[i] for i in rep], [offs[i] for i in rep])

        # 3) 썸네일: 컷 가운데에 가장 가까운 대표 프레임, 대표 레벨로 보정
        mid = (src_in + src_out) // 2
        ti = min(rep, key=lambda i: abs(i - mid))
        thumbs = {"": preview.thumb_jpeg(lins[ti] * np.float32(2.0 ** offs[ti]))}

        # 3-1) 컷의 첫 프레임과 끝 프레임 (경계 리뷰용). 찍힌 그대로 둔다: 대표 레벨로 옮기지 않는다
        edges = {}
        for tag, idx in (("h", src_in), ("t", max(src_in, src_out - 1))):
            check()
            if idx in codes:
                code_e = codes[idx]
            elif tag == "t" and fast:
                _, code_e = src.read_fast(idx, src_in, STAT_SIDE)      # 롱GOP 는 끝에 가장 가까운 키프레임
            else:
                code_e = src.read(idx, STAT_SIDE)
            lin_e = spaces.to_dwg_linear(code_e, space)
            thumbs[tag] = preview.thumb_jpeg(lin_e)
            edges[tag] = round(float(measure.frame_stats(lin_e, code_e)["level"] - tmp["rep"]), 3)
        del codes

        # 4) 얼굴 · 스킨
        obs: list[dict] = []
        floors: list[tuple[list, float]] = []
        bgs: list[tuple[list, float]] = []
        detector = "none"
        for i in rep:
            check()
            code_hi = src.read(i, FACE_SIDE)
            lin_hi = spaces.to_dwg_linear(code_hi, space)
            clipped, _ = measure.clip_mask(code_hi)
            h, w = lin_hi.shape[:2]
            # 블랙 플로어는 해상도가 높을수록 작은 그늘까지 잡힌다 (와이드 샷에서 덜 뜬다)
            rows, cols = measure.content_slice(code_hi)
            fl, sp = measure.floor_stats(lin_hi[rows, cols])
            floors.append(([v * float(2.0 ** offs[i]) for v in fl], sp * float(2.0 ** offs[i])))
            hints = src.face_hints(i, (h, w))
            gain = np.float32(2.0 ** offs[i])
            boxes: list = []
            if hints is not None:
                detector = "hint"
                boxes = [f["box"] for f in hints[:MAX_FACES]]
                for f in hints[:MAX_FACES]:
                    sk = measure.sample_skin(lin_hi, f["lm"], clipped, f.get("score", 0.99))
                    if sk is None:
                        continue
                    obs.append(dict(rgb=[v * float(gain) for v in sk["rgb"]], quality=sk["quality"], n=sk["n"],
                                    clip=sk["clip"], ident=f.get("ident"), emb=None, size=sk["iod"] / w,
                                    box=[f["box"][0] / w, f["box"][1] / h, f["box"][2] / w, f["box"][3] / h]))
            elif engine is not None and engine.available:
                detector = "yunet"
                bgr = preview.detector_image(lin_hi)
                dets = sorted(engine.detect(bgr), key=lambda d: -(d["box"][2] * d["box"][3] * d["score"]))[:MAX_FACES]
                boxes = [d["box"] for d in dets]
                for d in dets:
                    sk = measure.sample_skin(lin_hi, d["lm"], clipped, d["score"])
                    if sk is None or sk["quality"] < 0.12:
                        continue
                    emb = engine.embed(bgr, d)
                    obs.append(dict(rgb=[v * float(gain) for v in sk["rgb"]], quality=sk["quality"], n=sk["n"],
                                    clip=sk["clip"], ident=None, emb=emb, size=sk["iod"] / w,
                                    box=[d["box"][0] / w, d["box"][1] / h, d["box"][2] / w, d["box"][3] / h]))
            bg = measure.background_chroma(lin_hi[rows, cols], clipped[rows, cols],
                                           [(b[0] - cols.start, b[1] - rows.start, b[2], b[3]) for b in boxes])
            if bg is not None:
                bgs.append(bg)
        faces = _cluster(obs)
        if bgs:
            merged["chroma_bg"] = [float(np.median([b[0][c] for b in bgs])) for c in range(2)]
            merged["bg_frac"] = float(np.median([b[1] for b in bgs]))
        if floors:
            merged["black"] = [float(np.median([f[0][c] for f in floors])) for c in range(3)]
            merged["black_spread"] = float(np.median([f[1] for f in floors]))

        info = src.info
        return dict(
            version=ANALYSIS_VERSION, space=space, guess=g, signal=sig,
            temporal=dict(kind=tmp["kind"], spread=tmp["spread"], rep=round(tmp["rep"], 4)),
            stats=merged, faces=faces, detector=detector, frames=len(order), edges=edges,
            source=dict(codec=info.codec, pix_fmt=info.pix_fmt, bits=info.bits, width=info.width,
                        height=info.height, fps=round(info.fps, 3), range=info.range_flag,
                        matrix=info.matrix_flag, n_frames=n_total),
        ), thumbs
    finally:
        src.close()
