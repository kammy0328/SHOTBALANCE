"""MATCH 노드에 넣는 3D LUT: 밀도 연동 채도.

노출 · WB · 블랙은 선형 게인과 오프셋이라 EXPOSURE 노드의 CDL 로 정확히 쓸 수 있다.
채도는 채널을 섞는 연산이라 CDL 로는 쓸 수 없어서 (CDL 의 Saturation 은 밀도를 다루지 못한다)
작은 3D LUT 로 만들어 두 번째 노드에 넣는다.

LUT 는 (채도, 밀도) 두 값으로만 정해진다. 그래서 컷마다 파일을 만들지 않고 값마다 파일 하나를
만들어 여러 컷 · 여러 프로젝트가 함께 쓴다 (파일 이름이 곧 내용이다).
입력 · 출력은 타임라인 색공간인 DaVinci WG / Intermediate 다.
"""
from __future__ import annotations

import numpy as np

from .gamuts import EXPOSURE_W, weighted

SIZE = 33
SAT_MIN, SAT_MAX = 0.40, 1.80
DENSITIES = (0.0, 0.75, 1.25, 2.0)      # 밝기 그대로 두지 않음 / 밝기 유지 / 조금 진하게 / 진하게


KAPPA = 40.0        # 소프트 맥스의 날카로움 (DI 코드 단위. 1 스톱 = 0.0733)


def saturate(code: np.ndarray, sat: float, density: float) -> np.ndarray:
    """밀도 연동 채도. DaVinci Intermediate 코드값(로그)에서 계산한다.

        out = P + sat·(code − P),   P = (1 − density)·평균 + density·가장 큰 채널(부드러운 최댓값)

    로그에서 채널 차이를 sat 배 하는 것이므로 선형으로 보면 색 비율의 거듭제곱이다:
    R/G 가 2^x 이던 색은 2^(sat·x) 가 된다. 중성색은 그대로이고, 값이 음수로 뒤집히지 않으며,
    노출을 먼저 바꾸든 나중에 바꾸든 결과가 같다 (DI 의 로그 구간에서).

    P 가 채도를 바꿀 때 밝기가 어디로 가는지를 정한다.
    density 0   : 채널 평균이 축이다. 흔한 채도 조절과 같아서 채도를 20% 올리면 진한 색이 0.2~0.4 스톱 밝아진다.
    density 0.75: 진한 색의 밝기가 거의 그대로 남는다 (같은 조건에서 ±0.07 스톱 안).
    density 1.25: 축이 가장 큰 채널 근처다. 채도를 올리면 나머지 채널이 내려가서 색이 진해진다
                  (필름의 감산 혼합처럼 밀도가 올라간다. 0.1~0.35 스톱 어두워진다).
    density 2   : 더 진하게 (0.25~0.8 스톱).
    """
    code = np.asarray(code, np.float32)
    if abs(sat - 1.0) < 1e-6:
        return code
    mean = weighted(code, EXPOSURE_W)
    p = mean
    if density > 0:
        # 부드러운 최댓값: 채널 값을 exp(κ·값) 으로 가중 평균한다. 중성색에서는 그 값 그대로,
        # 한 채널이 두드러지면 그 채널 값에 가까워지고, max() 와 달리 꺾이는 곳이 없어 LUT 보간 오차가 작다
        mx = code.max(axis=-1)
        a = np.exp(KAPPA * (code - mx[..., None]))
        soft = (code * a).sum(axis=-1) / a.sum(axis=-1)
        p = mean + np.float32(density) * (soft - mean)
    return (p[..., None] + np.float32(sat) * (code - p[..., None])).astype(np.float32)


def quantize(sat: float, density: float) -> tuple[float, float]:
    """파일로 만들 값: 채도는 1% 단위, 밀도는 정해진 단계 중 가장 가까운 것."""
    s = round(float(np.clip(sat, SAT_MIN, SAT_MAX)), 2)
    d = min(DENSITIES, key=lambda v: abs(v - float(density)))
    return s, d


def is_identity(sat: float) -> bool:
    return abs(round(float(sat), 2) - 1.0) < 0.005


def lut_name(sat: float, density: float) -> str:
    s, d = quantize(sat, density)
    return f"SB_sat{int(round(s * 100)):03d}_den{int(round(d * 100)):03d}.cube"


def build(sat: float, density: float, size: int = SIZE) -> np.ndarray:
    """(size, size, size, 3) 테이블. 축 순서는 [B][G][R] (.cube 파일은 R 이 가장 빨리 변한다)."""
    s, d = quantize(sat, density)
    ax = np.linspace(0.0, 1.0, size, dtype=np.float32)
    b, g, r = np.meshgrid(ax, ax, ax, indexing="ij")
    code = np.stack([r, g, b], axis=-1)
    return np.clip(saturate(code, s, d), 0.0, 1.0).astype(np.float32)


def cube_text(table: np.ndarray, title: str) -> str:
    size = table.shape[0]
    rows = table.reshape(-1, 3)
    head = [f'TITLE "{title}"', f"LUT_3D_SIZE {size}", "DOMAIN_MIN 0.0 0.0 0.0", "DOMAIN_MAX 1.0 1.0 1.0", ""]
    body = "\n".join(f"{r:.6f} {g:.6f} {b:.6f}" for r, g, b in rows.tolist())
    return "\n".join(head) + body + "\n"


def apply_table(table: np.ndarray, code: np.ndarray) -> np.ndarray:
    """테이블을 삼선형 보간으로 적용한다 (검증용. Resolve 가 LUT 를 읽는 방식과 같다)."""
    n = table.shape[0] - 1
    x = np.clip(np.asarray(code, np.float64), 0.0, 1.0) * n
    i0 = np.minimum(np.floor(x).astype(int), n - 1)
    f = x - i0
    r0, g0, b0 = i0[..., 0], i0[..., 1], i0[..., 2]
    fr, fg, fb = f[..., 0:1], f[..., 1:2], f[..., 2:3]
    out = 0.0
    for db in (0, 1):
        for dg in (0, 1):
            for dr in (0, 1):
                w = (fr if dr else 1 - fr) * (fg if dg else 1 - fg) * (fb if db else 1 - fb)
                out = out + w * table[b0 + db, g0 + dg, r0 + dr]
    return out
