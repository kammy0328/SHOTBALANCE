"""컷 안의 밝기 변화를 분류하고 '이 컷의 노출'로 삼을 대표 레벨을 정한다.

키프레임 없이 컷 하나에 값 하나만 쓰기 때문에, 플리커 컷은 사람이 그 컷의 노출로
인식하는 상태(켜진 상태)를 대표값으로 잡는다.

  steady  변화 없음            → 중앙값
  flicker 켜짐/꺼짐 반복        → 켜진 상태
  wave    부드럽게 출렁임       → 선형 밝기의 시간 평균
  strobe  순간 화이트아웃/암전  → 그 프레임들을 빼고 중앙값
  ramp    컷 안에서 서서히 변화 → 중앙값 (플래그)
"""
from __future__ import annotations

import numpy as np

STEADY_SPREAD = 0.10      # 스톱. 이보다 작으면 변화 없음으로 본다
REP_TOL = 0.06            # 대표 레벨에서 이 안에 드는 프레임을 측정에 쓴다 (모자라면 가까운 순)


def classify(sparse_idx, sparse_lv, burst_idx, burst_lv) -> dict:
    """sparse: 컷 전체에 고르게 뽑은 프레임, burst: 연속 프레임. 레벨 단위는 log2(스톱)."""
    si = np.asarray(sparse_idx, np.float64)
    sl = np.asarray(sparse_lv, np.float64)
    bl = np.asarray(burst_lv, np.float64)
    all_idx = np.concatenate([si, np.asarray(burst_idx, np.float64)])
    all_lv = np.concatenate([sl, bl])
    n = all_lv.size
    if n == 0:
        return dict(kind="steady", spread=0.0, rep=0.0, rep_frames=[])
    med = float(np.median(all_lv))
    spread = float(np.percentile(all_lv, 95) - np.percentile(all_lv, 5)) if n >= 4 else float(all_lv.max() - all_lv.min())
    kind, rep = "steady", med

    if spread >= STEADY_SPREAD and n >= 5:
        dev = all_lv - med
        inl = np.abs(dev) < 0.15
        srt = np.sort(all_lv)
        gaps = np.diff(srt)
        gi = int(np.argmax(gaps)) if gaps.size else 0
        gap = float(gaps[gi]) if gaps.size else 0.0
        lo_n, hi_n = gi + 1, n - gi - 1
        # 느린 변화: 고르게 뽑은 프레임이 시간에 따라 한 방향으로 움직이고, 연속 구간은 잔잔
        corr = 0.0
        if si.size >= 4 and np.ptp(sl) > 1e-6:
            corr = float(np.corrcoef(si, sl)[0, 1])
        burst_spread = float(np.ptp(bl)) if bl.size else 0.0
        if inl.mean() >= 0.6 and np.any(np.abs(dev) > 1.0):
            kind, rep = "strobe", float(np.median(all_lv[inl]))
        elif abs(corr) > 0.9 and np.ptp(sl) > 0.25 and burst_spread < 0.5 * np.ptp(sl):
            kind, rep = "ramp", med
        elif gap > max(0.15, 0.35 * (srt[-1] - srt[0])) and min(lo_n, hi_n) >= max(2, int(0.12 * n)):
            kind, rep = "flicker", float(np.median(srt[gi + 1:]))
        else:
            # 연속 구간이 부드럽게 변하면 출렁임, 프레임마다 튀면 플리커
            smooth = True
            if bl.size >= 4:
                step = np.abs(np.diff(bl))
                smooth = float(np.median(step)) < 0.45 * max(float(np.ptp(bl)), 1e-6)
            if smooth:
                # 출렁임은 선형 밝기의 시간 평균을 그 컷의 노출로 본다
                kind, rep = "wave", float(np.log2(np.mean(np.power(2.0, all_lv))))
            else:
                kind, rep = "flicker", float(np.percentile(all_lv, 85))

    order = np.argsort(np.abs(all_lv - rep))
    close = [int(all_idx[k]) for k in order if abs(all_lv[k] - rep) <= REP_TOL]
    if len(set(close)) < 3:
        close = [int(all_idx[k]) for k in order[:3]]
    return dict(kind=kind, spread=round(spread, 4), rep=rep, rep_frames=sorted(set(close)))


def pick_spread(frames: list[int], k: int) -> list[int]:
    """후보 프레임 중에서 시간상 고르게 k 개."""
    frames = sorted(set(frames))
    if len(frames) <= k:
        return frames
    pos = np.linspace(0, len(frames) - 1, k)
    return sorted({frames[int(round(p))] for p in pos})


def sample_plan(src_in: int, src_out: int, n_sparse: int = 9, n_burst: int = 12) -> tuple[list[int], list[int]]:
    """[src_in, src_out) 에서 (고르게 뽑을 프레임, 연속 프레임) 인덱스."""
    n = max(1, src_out - src_in)
    pad = int(n * 0.05) if n > 20 else 0     # 트랜지션이 걸릴 수 있는 양 끝은 피한다
    a, b = src_in + pad, src_out - 1 - pad
    if b <= a:
        return [src_in + n // 2], []
    sparse = sorted({int(round(v)) for v in np.linspace(a, b, min(n_sparse, b - a + 1))})
    nb = min(n_burst, b - a + 1)
    start = int(np.clip(src_in + n // 2 - nb // 2, a, b - nb + 1))
    burst = [i for i in range(start, start + nb) if i not in sparse] if nb >= 4 else []
    return sparse, burst
