"""입력 색공간 레지스트리: 코드값 → DaVinci Wide Gamut 선형."""
from __future__ import annotations

import re
from dataclasses import dataclass

import numpy as np

from . import curves, gamuts


@dataclass(frozen=True)
class InputSpace:
    id: str
    label: str
    transfer: str
    gamut: str
    kind: str      # "log" | "display" | "linear"
    family: str    # 추정·힌트 매칭용 계열 이름


SPACES: dict[str, InputSpace] = {s.id: s for s in [
    InputSpace("slog3_sgamut3cine", "S-Log3 / S-Gamut3.Cine", "slog3", "sgamut3cine", "log", "slog3"),
    InputSpace("slog3_sgamut3", "S-Log3 / S-Gamut3", "slog3", "sgamut3", "log", "slog3"),
    InputSpace("logc3_awg3", "ARRI LogC3 / AWG3", "logc3", "awg3", "log", "logc3"),
    InputSpace("logc4_awg4", "ARRI LogC4 / AWG4", "logc4", "awg4", "log", "logc4"),
    InputSpace("vlog_vgamut", "V-Log / V-Gamut", "vlog", "vgamut", "log", "vlog"),
    InputSpace("flog_fgamut", "F-Log / F-Gamut", "flog", "rec2020", "log", "flog"),
    InputSpace("flog2_fgamut", "F-Log2 / F-Gamut", "flog2", "rec2020", "log", "flog"),
    InputSpace("clog2_cgamut", "Canon Log 2 / Cinema Gamut", "clog2", "cinemagamut", "log", "clog"),
    InputSpace("clog3_cgamut", "Canon Log 3 / Cinema Gamut", "clog3", "cinemagamut", "log", "clog"),
    InputSpace("log3g10_rwg", "Log3G10 / REDWideGamutRGB", "log3g10", "rwg", "log", "red"),
    InputSpace("bmdfilm5_bwg", "BMD Film Gen 5 / Wide Gamut", "bmdfilm5", "bwg", "log", "bmd"),
    InputSpace("applelog_rec2020", "Apple Log / Rec.2020", "applelog", "rec2020", "log", "applelog"),
    InputSpace("di_dwg", "DaVinci WG / Intermediate", "di", "dwg", "log", "di"),
    InputSpace("acescct_ap1", "ACEScct / AP1", "acescct", "ap1", "log", "aces"),
    InputSpace("rec709_g24", "Rec.709 Gamma 2.4", "gamma24", "rec709", "display", "rec709"),
    InputSpace("rec709_g22", "Rec.709 Gamma 2.2", "gamma22", "rec709", "display", "rec709"),
    InputSpace("srgb", "sRGB", "srgb", "rec709", "display", "rec709"),
    InputSpace("rec709_scene", "Rec.709 (Scene)", "bt709", "rec709", "display", "rec709"),
    InputSpace("p3d65_g24", "P3-D65 Gamma 2.4", "gamma24", "p3d65", "display", "rec709"),
    InputSpace("rec2020_g24", "Rec.2020 Gamma 2.4", "gamma24", "rec2020", "display", "rec709"),
    InputSpace("linear_dwg", "Linear / DaVinci WG", "linear", "dwg", "linear", "linear"),
]}

DEFAULT_LOG = "slog3_sgamut3cine"
DEFAULT_DISPLAY = "rec709_g24"


def to_dwg_linear(code: np.ndarray, space_id: str) -> np.ndarray:
    """코드값 RGB (..., 3) → DWG 선형 RGB (float32)."""
    sp = SPACES[space_id]
    lin = curves.TRANSFERS[sp.transfer][0](code)
    if sp.gamut != "dwg":
        lin = gamuts.apply(lin, gamuts.matrix32(sp.gamut, "dwg"))
    return lin.astype(np.float32, copy=False)


def from_dwg_linear(lin: np.ndarray, space_id: str) -> np.ndarray:
    """DWG 선형 → 해당 색공간의 코드값 (encode 가 있는 커브만. 합성 영상·테스트용)."""
    sp = SPACES[space_id]
    enc = curves.TRANSFERS[sp.transfer][1]
    if enc is None:
        raise ValueError(f"{space_id}: encode 미지원")
    x = np.asarray(lin, dtype=np.float32)
    if sp.gamut != "dwg":
        x = gamuts.apply(x, gamuts.matrix32("dwg", sp.gamut))
    return enc(x)


def exposure_index(lin: np.ndarray) -> np.ndarray:
    """DWG 선형 RGB (..., 3) → 노출 지표 E (양수 가중합)."""
    return gamuts.weighted(lin, gamuts.EXPOSURE_W)


# ----------------------------------------------------- Resolve 이름 매핑
def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", s.lower())


_RULES: list[tuple[tuple[str, ...], tuple[str, ...], str]] = [
    # (모두 포함, 하나도 없어야 함, space id) — 위에서부터 먼저 맞는 것
    (("slog3", "cine"), (), "slog3_sgamut3cine"),
    (("slog3",), (), "slog3_sgamut3"),
    (("logc4",), (), "logc4_awg4"),
    (("awg4",), (), "logc4_awg4"),
    (("logc3",), (), "logc3_awg3"),
    (("logc",), (), "logc3_awg3"),
    (("vlog",), (), "vlog_vgamut"),
    (("flog2",), (), "flog2_fgamut"),
    (("flog",), (), "flog_fgamut"),
    (("canonlog2",), (), "clog2_cgamut"),
    (("clog2",), (), "clog2_cgamut"),
    (("canonlog3",), (), "clog3_cgamut"),
    (("clog3",), (), "clog3_cgamut"),
    (("log3g10",), (), "log3g10_rwg"),
    (("redwidegamut",), (), "log3g10_rwg"),
    (("filmgen5",), (), "bmdfilm5_bwg"),
    (("applelog",), (), "applelog_rec2020"),
    (("davinci", "intermediate"), (), "di_dwg"),
    (("davinciwg",), ("linear",), "di_dwg"),
    (("acescct",), (), "acescct_ap1"),
    (("srgb",), (), "srgb"),
    (("rec709", "scene"), (), "rec709_scene"),
    (("rec709", "22"), (), "rec709_g22"),
    (("rec709",), (), "rec709_g24"),
    (("p3d65",), (), "p3d65_g24"),
    (("rec2020", "24"), (), "rec2020_g24"),
]


def from_resolve_name(name: str | None) -> str | None:
    """Resolve 의 'Input Color Space' 문자열을 내부 id 로. 모르면 None."""
    if not name:
        return None
    n = _norm(name)
    if not n or n in ("project", "sameasproject", "bypass", "default", "none"):
        return None
    for must, must_not, sid in _RULES:
        if all(m in n for m in must) and not any(m in n for m in must_not):
            return sid
    return None


def options() -> list[dict]:
    return [{"id": s.id, "label": s.label, "kind": s.kind} for s in SPACES.values()]
