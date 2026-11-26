"""MATCH 노드용 LUT: 밀도 연동 채도 연산과 .cube 파일."""
import numpy as np

from balance.color import curves, lut
from balance.color.gamuts import EXPOSURE_W
from balance.resolve import luts


def _samples(n=60000, seed=3):
    rng = np.random.default_rng(seed)
    lum = 0.18 * 2.0 ** rng.uniform(-6, 4, (n, 1))
    lin = (lum * 2.0 ** rng.normal(0, 0.5, (n, 3))).astype(np.float32)
    code = curves.di_encode(lin)
    return code[code.max(axis=1) < 0.97]


def test_saturate_properties():
    grey = curves.di_encode(np.array([[0.02] * 3, [0.18] * 3, [2.0] * 3], np.float32))
    for d in lut.DENSITIES:
        assert np.allclose(lut.saturate(grey, 1.3, d), grey, atol=1e-6)          # 중성색은 그대로
    code = _samples(2000)
    for sat in (0.7, 1.25):
        for d in lut.DENSITIES:
            out = lut.saturate(code, sat, d)
            # 채널 사이의 차이(로그 색도)가 정확히 sat 배가 된다
            assert np.allclose(out[:, 0] - out[:, 1], sat * (code[:, 0] - code[:, 1]), atol=2e-5)
            assert np.allclose(out[:, 2] - out[:, 1], sat * (code[:, 2] - code[:, 1]), atol=2e-5)
    assert lut.saturate(code, 1.0, 1.25) is not None and np.array_equal(lut.saturate(code, 1.0, 1.25), code)
    # 밀도: 채도를 올릴 때 진한 색의 밝기가 가는 방향
    red = curves.di_encode(np.array([[0.5, 0.1, 0.05]], np.float32))
    e0 = float(curves.di_decode(red)[0] @ EXPOSURE_W)
    lv = [float(np.log2(curves.di_decode(lut.saturate(red, 1.2, d))[0] @ EXPOSURE_W / e0)) for d in lut.DENSITIES]
    assert lv[0] > 0.15 and abs(lv[1]) < 0.08 and lv[2] < -0.15 and lv[3] < lv[2] < lv[1] < lv[0]
    # 스킨처럼 옅은 색의 밝기는 거의 그대로다 (기본 밀도에서 채도 20% 에 0.03 스톱)
    skin = curves.di_encode(np.array([[0.30, 0.22, 0.17]], np.float32))
    es = float(curves.di_decode(skin)[0] @ EXPOSURE_W)
    assert abs(np.log2(curves.di_decode(lut.saturate(skin, 1.2, 0.75))[0] @ EXPOSURE_W / es)) < 0.035
    # 노출과 순서를 바꿔도 같다 (DI 의 로그 구간)
    mid = code[(code.min(axis=1) > 0.25)]
    up = mid + np.float32(curves.DI_C)                                             # +1 스톱
    assert np.allclose(lut.saturate(up, 1.2, 1.25), lut.saturate(mid, 1.2, 1.25) + curves.DI_C, atol=2e-5)


def test_lut_matches_operator():
    code = _samples()
    for sat, d in ((1.16, 1.25), (0.85, 0.75), (1.3, 2.0), (1.2, 0.0)):
        got = lut.apply_table(lut.build(sat, d), code)
        want = np.clip(lut.saturate(code, sat, d), 0, 1)
        err = np.abs(got - want)
        # DI 코드 0.0733 이 1 스톱이다. 평균 0.02 스톱, 99% 가 0.05 스톱 안
        assert err.mean() < 0.0016 and np.percentile(err, 99) < 0.004, (sat, d, err.mean(), np.percentile(err, 99))


def test_cube_file(tmp_path):
    assert lut.quantize(1.1649, 1.1) == (1.16, 1.25) and lut.quantize(9, -1) == (lut.SAT_MAX, 0.0)
    assert lut.lut_name(1.1649, 1.25) == "SB_sat116_den125.cube" and lut.lut_name(0.9, 0.0) == "SB_sat090_den000.cube"
    assert lut.is_identity(1.004) and not lut.is_identity(1.006)
    rel, created = luts.ensure(tmp_path, 1.1649, 1.25)
    assert rel == "ShotBalance/SB_sat116_den125.cube" and created
    text = (tmp_path / "ShotBalance" / "SB_sat116_den125.cube").read_text(encoding="ascii")
    lines = [ln for ln in text.splitlines() if ln and ln[0].isdigit()]
    assert "LUT_3D_SIZE 33" in text and len(lines) == 33 ** 3
    table = np.array([[float(v) for v in ln.split()] for ln in lines], np.float32).reshape(33, 33, 33, 3)
    assert np.allclose(table, lut.build(1.16, 1.25), atol=1e-6)
    assert np.allclose(table[16, 16, 16], [0.5, 0.5, 0.5], atol=1e-6)            # 회색 축
    assert table[0, 0, 32, 0] > 0.9 and table[0, 0, 32, 1] < 0.01                # R 이 가장 빨리 변하는 순서
    assert table[32, 0, 0, 2] > 0.9 and table[32, 0, 0, 0] < 0.01
    assert luts.ensure(tmp_path, 1.16, 1.25) == (rel, False)                      # 같은 값은 다시 만들지 않는다
