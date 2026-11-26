"""색 변환을 colour-science 기준 구현과 수치 대조한다."""
import numpy as np
import pytest

colour = pytest.importorskip("colour")

from balance.color import curves, gamuts, spaces

V = np.linspace(-0.02, 1.0, 2049).astype(np.float32)

REF = {
    "slog3": lambda v: colour.models.log_decoding_SLog3(v),
    "logc3": lambda v: colour.models.log_decoding_ARRILogC3(v),
    "logc4": lambda v: colour.models.log_decoding_ARRILogC4(v),
    "vlog": lambda v: colour.models.log_decoding_VLog(v),
    "flog": lambda v: colour.models.log_decoding_FLog(v),
    "flog2": lambda v: colour.models.log_decoding_FLog2(v),
    "clog2": lambda v: colour.models.log_decoding_CanonLog2(v),
    "clog3": lambda v: colour.models.log_decoding_CanonLog3(v),
    "log3g10": lambda v: colour.models.log_decoding_Log3G10(v),
    "bmdfilm5": lambda v: colour.models.oetf_inverse_BlackmagicFilmGeneration5(v),
    "di": lambda v: colour.models.oetf_inverse_DaVinciIntermediate(v),
    "applelog": lambda v: colour.models.log_decoding_AppleLogProfile(v),
    "acescct": lambda v: colour.models.log_decoding_ACEScct(v),
    "srgb": lambda v: colour.models.eotf_sRGB(v),
}


@pytest.mark.parametrize("name", sorted(REF))
def test_decode_matches_reference(name):
    ours = curves.TRANSFERS[name][0](V).astype(np.float64)
    ref = np.asarray(REF[name](V.astype(np.float64)))
    # 선형값이 매우 커지는 구간은 상대오차로, 블랙 근처는 절대오차로 본다
    err = np.abs(ours - ref) / np.maximum(np.abs(ref), 0.02)
    assert float(err.max()) < 2e-3, f"{name}: max rel err {err.max():.2e} at v={V[err.argmax()]:.4f}"


@pytest.mark.parametrize("name", [n for n, (_, e) in curves.TRANSFERS.items() if e is not None])
def test_encode_roundtrip(name):
    dec, enc = curves.TRANSFERS[name]
    v = np.linspace(0.0, 0.95, 513).astype(np.float32)
    back = enc(dec(v))
    assert float(np.abs(back - v).max()) < 2e-4, name


GAMUT_REF = {
    "sgamut3": "S-Gamut3", "sgamut3cine": "S-Gamut3.Cine", "awg3": "ARRI Wide Gamut 3",
    "awg4": "ARRI Wide Gamut 4", "vgamut": "V-Gamut", "cinemagamut": "Cinema Gamut",
    "rwg": "REDWideGamutRGB", "bwg": "Blackmagic Wide Gamut", "rec709": "ITU-R BT.709",
    "rec2020": "ITU-R BT.2020", "p3d65": "P3-D65", "ap1": "ACEScg", "ap0": "ACES2065-1",
}


@pytest.mark.parametrize("name", sorted(GAMUT_REF))
def test_gamut_matrix_to_dwg(name):
    src = colour.RGB_COLOURSPACES[GAMUT_REF[name]]
    dst = colour.RGB_COLOURSPACES["DaVinci Wide Gamut"]
    cat = "Bradford" if name in ("ap0", "ap1") else None
    ref = colour.matrix_RGB_to_RGB(src, dst, chromatic_adaptation_transform=cat)
    ours = gamuts.matrix(name, "dwg")
    assert np.abs(ours - ref).max() < 2e-3, f"{name}\n{ours}\n{ref}"


def test_white_is_preserved():
    for sid in spaces.SPACES:
        sp = spaces.SPACES[sid]
        if sp.gamut in ("ap0", "ap1"):
            continue
        m = gamuts.matrix(sp.gamut, "dwg")
        assert np.allclose(m @ np.ones(3), np.ones(3), atol=2e-4), sid


def test_space_roundtrip_and_stop_math():
    rng = np.random.default_rng(0)
    lin = rng.uniform(0.02, 2.0, size=(64, 3)).astype(np.float32)
    for sid in ("slog3_sgamut3cine", "logc3_awg3", "logc4_awg4", "di_dwg", "rec709_g24"):
        code = spaces.from_dwg_linear(lin, sid)
        back = spaces.to_dwg_linear(code, sid)
        assert np.abs(back / lin - 1).max() < 3e-3, sid
    # DI 오프셋은 (L + 0.0075) 에 대한 게인이다: 밝은 영역에선 1스톱 = 0.0733,
    # 미드그레이에선 0.0712 로 줄고 섀도로 갈수록 더 벌어진다 (Linear Gain 이 정확한 이유)
    hi = curves.di_encode(np.array([2.0, 4.0], np.float32))
    assert abs(float(hi[1] - hi[0]) - 0.0733) < 3e-4
    mid = curves.di_encode(np.array([0.18, 0.36], np.float32))
    assert abs(float(mid[1] - mid[0]) - 0.0712) < 3e-4


def test_resolve_name_mapping():
    f = spaces.from_resolve_name
    assert f("S-Gamut3.Cine/S-Log3") == "slog3_sgamut3cine"
    assert f("Sony S-Gamut3/S-Log3") == "slog3_sgamut3"
    assert f("ARRI LogC3") == "logc3_awg3"
    assert f("ARRI LogC4") == "logc4_awg4"
    assert f("Rec.709 Gamma 2.4") == "rec709_g24"
    assert f("Rec.709 (Scene)") == "rec709_scene"
    assert f("DaVinci WG/Intermediate") == "di_dwg"
    assert f("Project") is None and f("") is None and f(None) is None


def test_black_codes():
    assert abs(curves.BLACK_CODE["slog3"] - 95 / 1023) < 1e-4
    assert abs(curves.BLACK_CODE["logc4"] - 95 / 1023) < 1e-4
    assert abs(curves.BLACK_CODE["vlog"] - 0.125) < 1e-4
    assert abs(curves.BLACK_CODE["di"]) < 1e-4
