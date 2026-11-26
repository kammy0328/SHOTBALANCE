"""프레임 단위 측정: 밝기 분포, 색도, 블랙, 신호 통계, 스킨 샘플링."""
from __future__ import annotations

import numpy as np

from ..color.gamuts import EXPOSURE_W, weighted

EPS = 1e-6
PCTS = (1, 10, 25, 50, 75, 90, 95, 99)
ANCHOR_KEYS = ("p50", "p75", "p90", "p95")
_LUMA = np.array([0.2126, 0.7152, 0.0722], np.float32)
BLOCK = 8            # 블랙 플로어를 재는 블록 한 변 (통계용 프레임 기준 픽셀)


def clip_mask(code: np.ndarray) -> tuple[np.ndarray, float]:
    """센서 클리핑으로 보이는 픽셀. 코드값 최대 근처에 픽셀이 쌓여 있으면 클리핑으로 본다."""
    mx = code.max(axis=-1)
    top = float(np.percentile(mx, 99.98))
    if top < 0.55:
        return np.zeros(mx.shape, bool), 0.0
    m = mx >= top - 0.004
    frac = float(m.mean())
    if frac < 0.0015:          # 쌓임이 없으면 그냥 가장 밝은 점
        return np.zeros(mx.shape, bool), 0.0
    return m, frac


def content_slice(code: np.ndarray) -> tuple[slice, slice]:
    """구워진 레터박스 · 필러박스를 뺀 영역. 바는 줄 전체가 평평하고 가장 어둡다."""
    def run(flat: np.ndarray, dark: np.ndarray) -> int:
        n = 0
        while n < flat.size // 3 and flat[n] and dark[n]:
            n += 1
        return n
    luma = weighted(code, _LUMA)
    out = []
    for axis in (1, 0):                                   # 행 방향, 열 방향
        rng = luma.max(axis=axis) - luma.min(axis=axis)
        mean = luma.mean(axis=axis)
        flat, dark = rng < 0.006, mean < float(mean.min()) + 0.01
        a, b = run(flat, dark), run(flat[::-1], dark[::-1])
        n = flat.size
        if a + b < 0.04 * n:                              # 바라고 하기엔 너무 얇다
            a = b = 0
        out.append(slice(a, n - b))
    return out[0], out[1]


def floor_stats(lin: np.ndarray) -> tuple[list, float]:
    """블랙 플로어. 8×8 블록 평균(노이즈를 평균한 값) 중 가장 어두운 0.4% 의 평균색과,
    그 바로 위 띠(0.4~1%)와의 차이를 돌려준다. 차이가 작으면 블랙이 '면'으로 깔려 있다는 뜻이다."""
    h, w = lin.shape[:2]
    hb, wb = h // BLOCK, w // BLOCK
    if hb < 4 or wb < 4:
        px = lin.reshape(-1, 3)
        return [float(v) for v in px[np.argsort(weighted(px, EXPOSURE_W))[: max(4, px.shape[0] // 100)]].mean(axis=0)], 1.0
    blk = lin[: hb * BLOCK, : wb * BLOCK].reshape(hb, BLOCK, wb, BLOCK, 3).mean(axis=(1, 3)).reshape(-1, 3)
    e = weighted(blk, EXPOSURE_W)
    order = np.argsort(e)
    n1 = max(4, int(round(e.size * 0.004)))
    n2 = max(n1 + 4, int(round(e.size * 0.01)))
    floor = blk[order[:n1]].mean(axis=0)
    return [float(v) for v in floor], float(e[order[n1:n2]].mean() - float(floor @ EXPOSURE_W))


def frame_stats(lin: np.ndarray, code: np.ndarray) -> dict:
    """lin: DWG 선형, code: 같은 프레임의 코드값. 둘 다 (h, w, 3)."""
    rows, cols = content_slice(code)
    lin, code = lin[rows, cols], code[rows, cols]
    e = weighted(lin, EXPOSURE_W)
    clipped, clip_frac = clip_mask(code)
    valid = (~clipped) & (e > 2e-4)
    if valid.sum() < 64:
        valid = e > -1.0
    loge = np.log2(np.maximum(e[valid], EPS))
    pv = np.percentile(loge, PCTS)
    out = {f"p{p}": float(v) for p, v in zip(PCTS, pv)}
    out["level"] = float(loge.mean())
    out["clip"] = clip_frac

    # 색도: 미드톤에서 log2(R/G), log2(B/G)
    mid = valid & (e > 2.0 ** pv[2]) & (e < 2.0 ** pv[6])
    px = np.maximum(lin[mid], 1e-5)
    if px.shape[0] < 64:
        px = np.maximum(lin.reshape(-1, 3), 1e-5)
    lr = np.log2(px[:, 0] / px[:, 1])
    lb = np.log2(px[:, 2] / px[:, 1])
    sat = np.sqrt(lr * lr + lb * lb)
    out["chroma_mid"] = [float(np.median(lr)), float(np.median(lb))]
    k = max(32, int(0.2 * sat.size))
    idx = np.argpartition(sat, min(k, sat.size - 1))[:k]
    out["chroma_neutral"] = [float(np.median(lr[idx])), float(np.median(lb[idx]))]
    out["sat"] = float(np.median(sat))
    out["sat_neutral"] = float(np.median(sat[idx]))

    # 블랙 플로어 (블랙/헤이즈 매칭)
    out["black"], out["black_spread"] = floor_stats(lin)

    return out


def signal_stats(code: np.ndarray) -> dict:
    """코드값 신호 통계 (입력 색공간 추정용). 선형화 전의 값으로 계산한다."""
    luma = weighted(code, _LUMA)
    mx = code.max(axis=-1)
    lv = np.percentile(luma, (0.1, 1, 50))
    return dict(lo=float(lv[0]), p1=float(lv[1]), med=float(lv[2]),
                peak=float(np.percentile(mx, 99.9)), top=float((mx >= 0.985).mean()),
                sat=float(np.mean(mx - code.min(axis=-1))))


def merge_stats(stats: list[dict], offsets: list[float]) -> dict:
    """대표 프레임들의 통계를 합친다. offsets: 각 프레임을 대표 레벨로 옮기는 보정(스톱)."""
    out: dict = {}
    for p in PCTS:
        out[f"p{p}"] = float(np.median([s[f"p{p}"] + o for s, o in zip(stats, offsets)]))
    for key in ("chroma_mid", "chroma_neutral"):
        out[key] = [float(np.median([s[key][i] for s in stats])) for i in range(2)]
    out["sat"] = float(np.median([s["sat"] for s in stats]))
    out["sat_neutral"] = float(np.median([s["sat_neutral"] for s in stats]))
    out["clip"] = float(np.median([s["clip"] for s in stats]))
    out["black"] = [float(np.median([s["black"][i] * (2.0 ** o) for s, o in zip(stats, offsets)])) for i in range(3)]
    out["black_spread"] = float(np.median([s["black_spread"] * (2.0 ** o) for s, o in zip(stats, offsets)]))
    return out


# ------------------------------------------------------------------- 스킨
def _patch_pixels(lin, clipped, center, u, v, ru, rv):
    h, w = lin.shape[:2]
    r = max(ru, rv)
    x0, x1 = int(np.floor(center[0] - r)), int(np.ceil(center[0] + r)) + 1
    y0, y1 = int(np.floor(center[1] - r)), int(np.ceil(center[1] + r)) + 1
    x0, y0, x1, y1 = max(x0, 0), max(y0, 0), min(x1, w), min(y1, h)
    if x1 - x0 < 2 or y1 - y0 < 2:
        return None
    ys, xs = np.mgrid[y0:y1, x0:x1].astype(np.float32)
    dx, dy = xs - center[0], ys - center[1]
    a = (dx * u[0] + dy * u[1]) / ru
    b = (dx * v[0] + dy * v[1]) / rv
    m = (a * a + b * b) <= 1.0
    if m.sum() < 6:
        return None
    px = lin[y0:y1, x0:x1][m]
    cl = clipped[y0:y1, x0:x1][m] if clipped is not None else np.zeros(px.shape[0], bool)
    return px, cl


def _patch_center(px, cl):
    """패치의 대표색. 밝기 순으로 가운데 절반만 평균해 스페큘러 · 그림자 · 머리카락을 양쪽에서 걸러낸다.
    spec 은 패치 안에 하이라이트 구조가 얼마나 있는지(스톱)."""
    e = weighted(px, EXPOSURE_W)
    ok = (~cl) & (e > 1e-4)
    if ok.sum() < 6:
        return None
    p10, p25, p50, p75, p90 = np.percentile(e[ok], (10, 25, 50, 75, 90))
    keep = ok & (e >= p25) & (e <= p75)
    if keep.sum() < 3:
        return None
    rgb = px[keep].mean(axis=0)
    return dict(rgb=rgb, e=float(rgb @ EXPOSURE_W), n=int(ok.sum()), clip=float(cl.mean()),
                spec=float(np.log2(max(p90, 1e-6) / max(p50, 1e-6))),
                shade=float(np.log2(max(p50, 1e-6) / max(p10, 1e-6))))


def sample_skin(lin: np.ndarray, lm, clipped: np.ndarray | None = None, score: float = 1.0) -> dict | None:
    """얼굴 랜드마크 5점(오른눈, 왼눈, 코, 입 오른쪽, 입 왼쪽)으로 볼·이마 피부를 샘플링한다.

    돌려주는 rgb 는 DWG 선형 평균, e 는 노출 지표, quality 는 0..1.
    """
    lm = np.asarray(lm, np.float32).reshape(5, 2)
    re, le, nose, mr, ml = lm
    d = float(np.linalg.norm(le - re))
    if d < 6.0:
        return None
    u = (le - re) / d
    ec = (re + le) / 2.0
    vv = (mr + ml) / 2.0 - ec
    hgt = float(np.linalg.norm(vv))
    perp = np.array([-u[1], u[0]], np.float32)
    if hgt < 0.4 * d:
        v, hgt = (perp if perp[1] >= 0 else -perp), 1.05 * d
    else:
        v = vv / hgt
    yaw = float(np.dot(nose - ec, u)) / d          # +면 화면 오른쪽으로 돌린 얼굴

    cheeks = {}
    for name, eye, sgn in (("r", re, -1.0), ("l", le, 1.0)):
        if (name == "l" and yaw > 0.22) or (name == "r" and yaw < -0.22):
            continue                                  # 돌아간 쪽 볼은 배경이 섞인다
        c = eye + 0.55 * hgt * v + sgn * 0.10 * d * u
        got = _patch_pixels(lin, clipped, c, u, v, 0.22 * d, 0.20 * hgt)
        if got is not None:
            pc = _patch_center(*got)
            if pc is not None:
                cheeks[name] = pc
    if not cheeks:
        return None

    parts = list(cheeks.values())
    agree = 1.0
    if len(parts) == 2:
        bright, dark = sorted(parts, key=lambda p: -p["e"])
        if np.log2(bright["e"] / dark["e"]) > 0.4:
            if bright["spec"] > 0.30 and dark["spec"] < 0.20:
                parts, agree = [dark], 0.8            # 밝은 쪽은 광(스페큘러)이 덮인 볼
            else:
                parts, agree = [bright], 0.85         # 어두운 쪽은 그림자 볼
    if len(parts) == 1 and parts[0]["spec"] > 0.45:
        agree *= 0.7                                  # 하나뿐인 볼에 광이 넓게 있다
    base_e = float(np.mean([p["e"] for p in parts]))
    base_rgb = np.mean([p["rgb"] for p in parts], axis=0)

    got = _patch_pixels(lin, clipped, ec - 0.75 * hgt * v, u, v, 0.45 * d, 0.18 * hgt)
    if got is not None:
        fh = _patch_center(*got)
        if fh is not None and fh["spec"] < 0.30 and abs(np.log2(fh["e"] / base_e)) < 0.35:
            c0 = np.log2(np.maximum(base_rgb, 1e-5))
            c1 = np.log2(np.maximum(fh["rgb"], 1e-5))
            if abs((c1[0] - c1[1]) - (c0[0] - c0[1])) < 0.15 and abs((c1[2] - c1[1]) - (c0[2] - c0[1])) < 0.15:
                parts.append(fh)                      # 앞머리에 가려지지 않은 이마만 합친다

    n = sum(p["n"] for p in parts)
    rgb = sum(p["rgb"] * p["n"] for p in parts) / n
    clip = float(np.mean([p["clip"] for p in parts]))
    s_size = float(np.clip((d - 8.0) / 32.0, 0.0, 1.0))
    s_det = float(np.clip((score - 0.6) / 0.3, 0.0, 1.0))
    quality = (0.35 + 0.65 * s_size) * (0.5 + 0.5 * s_det) * agree * float(np.clip(1.0 - 2.0 * clip, 0.2, 1.0))
    return dict(rgb=[float(x) for x in rgb], e=float(rgb @ EXPOSURE_W), n=int(n), quality=float(quality),
                clip=clip, iod=d)
