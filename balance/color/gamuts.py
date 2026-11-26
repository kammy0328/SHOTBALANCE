"""색역(primaries)과 DaVinci Wide Gamut 으로의 3x3 변환 행렬."""
from __future__ import annotations

import numpy as np

D65 = (0.3127, 0.3290)
D60_ACES = (0.32168, 0.33767)

#: 이름 → (R xy, G xy, B xy, white xy)
PRIMARIES = {
    "dwg": ((0.8000, 0.3130), (0.1682, 0.9877), (0.0790, -0.1155), D65),
    "rec709": ((0.640, 0.330), (0.300, 0.600), (0.150, 0.060), D65),
    "rec2020": ((0.708, 0.292), (0.170, 0.797), (0.131, 0.046), D65),
    "p3d65": ((0.680, 0.320), (0.265, 0.690), (0.150, 0.060), D65),
    "sgamut3": ((0.730, 0.280), (0.140, 0.855), (0.100, -0.050), D65),
    "sgamut3cine": ((0.766, 0.275), (0.225, 0.800), (0.089, -0.087), D65),
    "awg3": ((0.6840, 0.3130), (0.2210, 0.8480), (0.0861, -0.1020), D65),
    "awg4": ((0.7347, 0.2653), (0.1424, 0.8576), (0.0991, -0.0308), D65),
    "vgamut": ((0.730, 0.280), (0.165, 0.840), (0.100, -0.030), D65),
    "cinemagamut": ((0.740, 0.270), (0.170, 1.140), (0.080, -0.100), D65),
    "rwg": ((0.780308, 0.304253), (0.121595, 1.493994), (0.095612, -0.084589), D65),
    "bwg": ((0.7177215, 0.3171181), (0.2280410, 0.8615690), (0.1005841, -0.0820452), (0.3127170, 0.3290312)),
    "ap0": ((0.7347, 0.2653), (0.0000, 1.0000), (0.0001, -0.0770), D60_ACES),
    "ap1": ((0.713, 0.293), (0.165, 0.830), (0.128, 0.044), D60_ACES),
}

_BRADFORD = np.array([[0.8951, 0.2664, -0.1614],
                      [-0.7502, 1.7135, 0.0367],
                      [0.0389, -0.0685, 1.0296]])


def _xy_to_xyz(xy):
    x, y = xy
    return np.array([x / y, 1.0, (1.0 - x - y) / y])


def rgb_to_xyz(name: str) -> np.ndarray:
    r, g, b, w = PRIMARIES[name]
    m = np.stack([_xy_to_xyz(r), _xy_to_xyz(g), _xy_to_xyz(b)], axis=1)
    s = np.linalg.solve(m, _xy_to_xyz(w))
    return m * s[None, :]


def _adapt(src_white, dst_white) -> np.ndarray:
    if np.allclose(src_white, dst_white, atol=1e-4):
        return np.eye(3)
    s = _BRADFORD @ _xy_to_xyz(src_white)
    d = _BRADFORD @ _xy_to_xyz(dst_white)
    return np.linalg.inv(_BRADFORD) @ np.diag(d / s) @ _BRADFORD


def matrix(src: str, dst: str) -> np.ndarray:
    """선형 RGB(src) → 선형 RGB(dst). 화이트가 다르면 Bradford 순응."""
    if src == dst:
        return np.eye(3)
    cat = _adapt(PRIMARIES[src][3], PRIMARIES[dst][3])
    return np.linalg.inv(rgb_to_xyz(dst)) @ cat @ rgb_to_xyz(src)


_CACHE: dict[tuple[str, str], np.ndarray] = {}


def matrix32(src: str, dst: str) -> np.ndarray:
    key = (src, dst)
    if key not in _CACHE:
        _CACHE[key] = matrix(src, dst).astype(np.float32)
    return _CACHE[key]


def apply(rgb: np.ndarray, m: np.ndarray) -> np.ndarray:
    """(..., 3) 배열에 3x3 행렬 적용.

    행렬곱(@)을 쓰지 않고 채널별로 계산한다. 영상 크기 배열의 @ 는 BLAS 로 넘어가는데,
    분석 스레드 여러 개가 동시에 부르면 Windows 의 OpenBLAS 가 access violation 으로 죽는다."""
    m = np.asarray(m, rgb.dtype)
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    out = np.empty_like(rgb)
    for i in range(3):
        out[..., i] = r * m[i, 0] + g * m[i, 1] + b * m[i, 2]
    return out


def weighted(rgb: np.ndarray, w) -> np.ndarray:
    """(..., 3) 배열의 채널 가중합 (BLAS 를 거치지 않는다)."""
    return rgb[..., 0] * w[0] + rgb[..., 1] * w[1] + rgb[..., 2] * w[2]


#: DWG 선형에서의 CIE Y 계수 (참고용. 파란 계수가 음수라 LED 조명에서 불안정하다)
DWG_Y = rgb_to_xyz("dwg")[1].copy()

#: 노출 측정에 쓰는 양수 가중치. 순수 노출 차이(전 채널 동일 게인)에는 어떤 양수 가중합이든
#: 같은 비율이 나오고, 채도 높은 LED 조명에서도 0 이나 음수가 되지 않는다.
EXPOSURE_W = np.array([0.25, 0.60, 0.15], dtype=np.float32)
