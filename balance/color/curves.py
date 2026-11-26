"""카메라 로그 · 디스플레이 전달함수.

모든 decode 함수는 정규화된 코드값(0..1, full-range 기준)을 scene-linear
(18% 그레이 = 0.18)로 바꾼다. encode 는 그 역함수.
numpy 배열을 그대로 받아 같은 shape 의 float32 배열을 돌려준다.

수식 출처는 각 제조사 백서이고, tests/test_color.py 에서 colour-science 구현과
수치 대조한다.
"""
from __future__ import annotations

import numpy as np

F = np.float32


def _a(x) -> np.ndarray:
    return np.asarray(x, dtype=F)


# ---------------------------------------------------------------- Sony S-Log3
_SL3_CUT_V = 171.2102946929 / 1023.0
_SL3_CUT_L = 0.01125


def slog3_decode(v):
    v = _a(v)
    with np.errstate(over="ignore", invalid="ignore"):
        hi = np.power(F(10.0), (v * F(1023.0) - F(420.0)) / F(261.5)) * F(0.19) - F(0.01)
    lo = (v * F(1023.0) - F(95.0)) * F(0.01125 / (171.2102946929 - 95.0))
    return np.where(v >= _SL3_CUT_V, hi, lo).astype(F)


def slog3_encode(l):
    l = _a(l)
    with np.errstate(divide="ignore", invalid="ignore"):
        hi = (F(420.0) + np.log10(np.maximum(l + F(0.01), F(1e-10)) / F(0.19)) * F(261.5)) / F(1023.0)
    lo = (l * F((171.2102946929 - 95.0) / 0.01125) + F(95.0)) / F(1023.0)
    return np.where(l >= _SL3_CUT_L, hi, lo).astype(F)


# ------------------------------------------------------- ARRI LogC3 (EI 800)
_C3 = dict(cut=0.010591, a=5.555556, b=0.052272, c=0.247190, d=0.385537, e=5.367655, f=0.092809)


def logc3_decode(v):
    v = _a(v)
    p = _C3
    with np.errstate(over="ignore"):
        hi = (np.power(F(10.0), (v - F(p["d"])) / F(p["c"])) - F(p["b"])) / F(p["a"])
    lo = (v - F(p["f"])) / F(p["e"])
    return np.where(v > p["e"] * p["cut"] + p["f"], hi, lo).astype(F)


def logc3_encode(l):
    l = _a(l)
    p = _C3
    with np.errstate(divide="ignore", invalid="ignore"):
        hi = F(p["c"]) * np.log10(np.maximum(F(p["a"]) * l + F(p["b"]), F(1e-10))) + F(p["d"])
    lo = F(p["e"]) * l + F(p["f"])
    return np.where(l > p["cut"], hi, lo).astype(F)


# ------------------------------------------------------------------ ARRI LogC4
_C4_A = (2.0 ** 18 - 16.0) / 117.45
_C4_B = (1023.0 - 95.0) / 1023.0
_C4_C = 95.0 / 1023.0
_C4_S = (7.0 * np.log(2.0) * 2.0 ** (7.0 - 14.0 * _C4_C / _C4_B)) / (_C4_A * _C4_B)
_C4_T = (2.0 ** (14.0 * (-_C4_C / _C4_B) + 6.0) - 64.0) / _C4_A


def logc4_decode(v):
    v = _a(v)
    with np.errstate(over="ignore"):
        hi = (np.power(F(2.0), F(14.0) * (v - F(_C4_C)) / F(_C4_B) + F(6.0)) - F(64.0)) / F(_C4_A)
    lo = v * F(_C4_S) + F(_C4_T)
    return np.where(v >= 0.0, hi, lo).astype(F)


def logc4_encode(l):
    l = _a(l)
    with np.errstate(divide="ignore", invalid="ignore"):
        hi = (np.log2(np.maximum(F(_C4_A) * l + F(64.0), F(1e-10))) - F(6.0)) / F(14.0) * F(_C4_B) + F(_C4_C)
    lo = (l - F(_C4_T)) / F(_C4_S)
    return np.where(l >= _C4_T, hi, lo).astype(F)


# ------------------------------------------------------------ Panasonic V-Log
def vlog_decode(v):
    v = _a(v)
    with np.errstate(over="ignore"):
        hi = np.power(F(10.0), (v - F(0.598206)) / F(0.241514)) - F(0.00873)
    lo = (v - F(0.125)) / F(5.6)
    return np.where(v < 0.181, lo, hi).astype(F)


def vlog_encode(l):
    l = _a(l)
    with np.errstate(divide="ignore", invalid="ignore"):
        hi = F(0.241514) * np.log10(np.maximum(l + F(0.00873), F(1e-10))) + F(0.598206)
    lo = F(5.6) * l + F(0.125)
    return np.where(l < 0.01, lo, hi).astype(F)


# ------------------------------------------------------- Fujifilm F-Log / F-Log2
def _flog_family(a, b, c, d, e, f, cut1, cut2):
    def dec(v):
        v = _a(v)
        with np.errstate(over="ignore"):
            hi = (np.power(F(10.0), (v - F(d)) / F(c)) - F(b)) / F(a)
        lo = (v - F(f)) / F(e)
        return np.where(v >= cut2, hi, lo).astype(F)

    def enc(l):
        l = _a(l)
        with np.errstate(divide="ignore", invalid="ignore"):
            hi = F(c) * np.log10(np.maximum(F(a) * l + F(b), F(1e-10))) + F(d)
        lo = F(e) * l + F(f)
        return np.where(l >= cut1, hi, lo).astype(F)

    return dec, enc


flog_decode, flog_encode = _flog_family(
    0.555556, 0.009468, 0.344676, 0.790453, 8.735631, 0.092864, 0.00089, 0.100537775223865)
flog2_decode, flog2_encode = _flog_family(
    5.555556, 0.064829, 0.245281, 0.384316, 8.799461, 0.092864, 0.000889, 0.100686685370811)


# ------------------------------------------------------------ Canon Log 2 / 3
def clog2_decode(v):
    v = _a(v)
    with np.errstate(over="ignore"):
        neg = -(np.power(F(10.0), (F(0.092864125) - v) / F(0.24136077)) - F(1.0)) / F(87.099375)
        pos = (np.power(F(10.0), (v - F(0.092864125)) / F(0.24136077)) - F(1.0)) / F(87.099375)
    return (np.where(v < 0.092864125, neg, pos) * F(0.9)).astype(F)


def clog3_decode(v):
    v = _a(v)
    with np.errstate(over="ignore"):
        neg = -(np.power(F(10.0), (F(0.12783901) - v) / F(0.36726845)) - F(1.0)) / F(14.98325)
        pos = (np.power(F(10.0), (v - F(0.12240537)) / F(0.36726845)) - F(1.0)) / F(14.98325)
    mid = (v - F(0.12512219)) / F(1.9754798)
    x = np.where(v < 0.097465473, neg, np.where(v <= 0.15277891, mid, pos))
    return (x * F(0.9)).astype(F)


# ------------------------------------------------------------- RED Log3G10
def log3g10_decode(v):
    v = _a(v)
    with np.errstate(over="ignore"):
        hi = (np.power(F(10.0), v / F(0.224282)) - F(1.0)) / F(155.975327)
    lo = v / F(15.1927)
    return (np.where(v < 0.0, lo, hi) - F(0.01)).astype(F)


def log3g10_encode(l):
    x = _a(l) + F(0.01)
    with np.errstate(divide="ignore", invalid="ignore"):
        hi = F(0.224282) * np.log10(np.maximum(x * F(155.975327) + F(1.0), F(1e-10)))
    lo = x * F(15.1927)
    return np.where(x < 0.0, lo, hi).astype(F)


# ---------------------------------------------------- Blackmagic Film Gen 5
_BM = dict(A=0.08692876065491224, B=0.005494072432257808, C=0.5300133392291939,
           D=8.283605932402494, E=0.09246575342465753, LIN_CUT=0.005)
_BM_LOG_CUT = _BM["D"] * _BM["LIN_CUT"] + _BM["E"]


def bmdfilm5_decode(v):
    v = _a(v)
    with np.errstate(over="ignore"):
        hi = np.exp((v - F(_BM["C"])) / F(_BM["A"])) - F(_BM["B"])
    lo = (v - F(_BM["E"])) / F(_BM["D"])
    return np.where(v < _BM_LOG_CUT, lo, hi).astype(F)


# ------------------------------------------------------ DaVinci Intermediate
DI_A, DI_B, DI_C, DI_M = 0.0075, 7.0, 0.07329248, 10.44426855
DI_LIN_CUT, DI_LOG_CUT = 0.00262409, 0.02740668


def di_decode(v):
    v = _a(v)
    with np.errstate(over="ignore"):
        hi = np.power(F(2.0), v / F(DI_C) - F(DI_B)) - F(DI_A)
    lo = v / F(DI_M)
    return np.where(v <= DI_LOG_CUT, lo, hi).astype(F)


def di_encode(l):
    l = _a(l)
    with np.errstate(divide="ignore", invalid="ignore"):
        hi = F(DI_C) * (np.log2(np.maximum(l + F(DI_A), F(1e-10))) + F(DI_B))
    lo = l * F(DI_M)
    return np.where(l <= DI_LIN_CUT, lo, hi).astype(F)


# ------------------------------------------------------------------ Apple Log
_AL = dict(R0=-0.05641088, Rt=0.01, c=47.28711236, beta=0.00964052, gamma=0.08550479, delta=0.69336945)
_AL_PT = _AL["c"] * (_AL["Rt"] - _AL["R0"]) ** 2


def applelog_decode(v):
    v = _a(v)
    with np.errstate(over="ignore", invalid="ignore"):
        hi = np.power(F(2.0), (v - F(_AL["delta"])) / F(_AL["gamma"])) - F(_AL["beta"])
        mid = np.sqrt(np.maximum(v, 0.0) / F(_AL["c"])) + F(_AL["R0"])
    return np.where(v >= _AL_PT, hi, np.where(v >= 0.0, mid, F(_AL["R0"]))).astype(F)


# -------------------------------------------------------------------- ACEScct
def acescct_decode(v):
    v = _a(v)
    with np.errstate(over="ignore"):
        hi = np.power(F(2.0), v * F(17.52) - F(9.72))
    lo = (v - F(0.0729055341958355)) / F(10.5402377416545)
    return np.where(v <= 0.155251141552511, lo, hi).astype(F)


# ------------------------------------------------------- display-referred
def gamma_decode(g: float):
    def dec(v):
        v = _a(v)
        return (np.sign(v) * np.power(np.abs(v), F(g))).astype(F)
    return dec


def gamma_encode(g: float):
    def enc(l):
        l = _a(l)
        return (np.sign(l) * np.power(np.abs(l), F(1.0 / g))).astype(F)
    return enc


def srgb_decode(v):
    v = _a(v)
    a = np.abs(v)
    lin = np.where(a <= 0.04045, a / F(12.92), np.power((a + F(0.055)) / F(1.055), F(2.4)))
    return (np.sign(v) * lin).astype(F)


def srgb_encode(l):
    l = _a(l)
    a = np.abs(l)
    enc = np.where(a <= 0.0031308, a * F(12.92), F(1.055) * np.power(a, F(1 / 2.4)) - F(0.055))
    return (np.sign(l) * enc).astype(F)


def bt709_scene_decode(v):
    """BT.709 OETF 의 역함수 (카메라가 709 로 찍은 scene-referred 신호)."""
    v = _a(v)
    a = np.abs(v)
    lin = np.where(a < 0.081, a / F(4.5), np.power((a + F(0.099)) / F(1.099), F(1 / 0.45)))
    return (np.sign(v) * lin).astype(F)


def linear(v):
    return _a(v)


#: 이름 → (decode, encode | None)
TRANSFERS = {
    "slog3": (slog3_decode, slog3_encode),
    "logc3": (logc3_decode, logc3_encode),
    "logc4": (logc4_decode, logc4_encode),
    "vlog": (vlog_decode, vlog_encode),
    "flog": (flog_decode, flog_encode),
    "flog2": (flog2_decode, flog2_encode),
    "clog2": (clog2_decode, None),
    "clog3": (clog3_decode, None),
    "log3g10": (log3g10_decode, log3g10_encode),
    "bmdfilm5": (bmdfilm5_decode, None),
    "di": (di_decode, di_encode),
    "applelog": (applelog_decode, None),
    "acescct": (acescct_decode, None),
    "gamma24": (gamma_decode(2.4), gamma_encode(2.4)),
    "gamma22": (gamma_decode(2.2), gamma_encode(2.2)),
    "srgb": (srgb_decode, srgb_encode),
    "bt709": (bt709_scene_decode, None),
    "linear": (linear, linear),
}

#: 로그 커브가 센서 블랙(선형 0)을 놓는 코드값. 입력 색공간 추정에 쓴다.
BLACK_CODE: dict[str, float] = {}


def _black_code(name: str) -> float:
    """선형 0 이 되는 코드값을 이분법으로 찾는다."""
    dec = TRANSFERS[name][0]
    lo, hi = -0.2, 0.6
    for _ in range(60):
        mid = (lo + hi) / 2
        if float(dec(np.array([mid], F))[0]) < 0.0:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


for _name in TRANSFERS:
    BLACK_CODE[_name] = round(_black_code(_name), 5)
