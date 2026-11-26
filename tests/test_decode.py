"""디코더 검증: 기준 구현(colour-science)이 만든 Y'CbCr 을 파일로 인코딩한 뒤
우리 디코더로 읽어 원래 R'G'B' 코드값과 비교한다."""
import numpy as np
import pytest

from balance.media.decode import VideoSource, fit_size
from conftest import H, N, PATCHES, SIZES, VARIANTS, W, frame_level

# Y'CbCr 정수 양자화가 RGB 에서 10비트 ±0.0016, 8비트 ±0.006 까지 번진다. 손실 코덱은 여유를 더 준다.
TOL = {"prores422": 0.003, "prores4444": 0.003, "h264_full": 0.007, "h264_tv_10": 0.002,
       "hevc_2020": 0.002, "untagged_sd": 0.007, "untagged_hd": 0.003}


def patch_means(img):
    pw = img.shape[1] // 8
    hh = img.shape[0] // 2
    return [img[4:hh - 4, k * pw + 6:(k + 1) * pw - 6].reshape(-1, 3).mean(axis=0) for k in range(8)]


@pytest.mark.parametrize("name", sorted(VARIANTS))
def test_code_values_roundtrip(clips, name):
    if name not in clips:
        pytest.skip("encoder unavailable")
    w, h, n = SIZES.get(name, (W, H, N))
    with VideoSource(clips[name]) as src:
        assert src.info.n_frames == n
        assert abs(src.info.fps - 24) < 1e-6
        img = src.read(min(3, n - 1), max_side=4096)
        assert img.shape == (h, w, 3) and img.dtype == np.float32
        for got, want in zip(patch_means(img), PATCHES):
            assert np.abs(got - np.array(want)).max() < TOL[name], (name, got, want)


@pytest.mark.parametrize("name", ["prores422", "h264_full", "h264_tv_10", "hevc_2020"])
def test_frame_accurate_seek(clips, name):
    if name not in clips:
        pytest.skip("encoder unavailable")
    order = [7, 2, 15, 0, 8, 9, 10, 4, 14, 1]
    with VideoSource(clips[name]) as src:
        for i in order:
            img = src.read(i, max_side=512)
            level = float(img[H // 2 + 8:, 8:-8].mean())
            assert abs(level - frame_level(i)) < 0.005, (name, i, level, frame_level(i))


def test_downscale_and_data_level_override(clips):
    with VideoSource(clips["prores422"]) as src:
        small = src.read(0, max_side=128)
        assert small.shape == (72, 128, 3)
        assert abs(float(small[4:30, 19:29].mean()) - 0.42) < 0.004
    # 같은 파일을 Full 로 강제하면 64..940 을 0..1023 기준으로 읽는다
    with VideoSource(clips["prores422"], data_level="Full") as src:
        img = src.read(0, max_side=512)
        want = (0.42 * 876 + 64) / 1023
        assert abs(float(img[10:60, 40:56, 1].mean()) - want) < 0.006


def test_fit_size():
    assert fit_size(3840, 2160, 960) == (960, 540)
    assert fit_size(4096, 1716, 480) == (480, 202)
    assert fit_size(640, 360, 960) == (640, 360)


def test_read_fast_uses_keyframes_on_long_gop(clips):
    # h264_tv_10 은 GOP 12: 프레임 0, 12 가 키프레임
    with VideoSource(clips["h264_tv_10"]) as src:
        assert not src.info.intra
        j, img = src.read_fast(15, lo=10, max_side=512)      # 키프레임 12 가 컷 범위 안 → 12 를 돌려준다
        assert j == 12
        assert abs(float(img[H // 2 + 8:, 8:-8].mean()) - frame_level(12)) < 0.005
        j, img = src.read_fast(15, lo=14, max_side=512)      # 키프레임이 범위 밖 → 정확히 15 까지 디코딩
        assert j == 15
        assert abs(float(img[H // 2 + 8:, 8:-8].mean()) - frame_level(15)) < 0.005
        j, _ = src.read_fast(13, lo=0, max_side=512)         # 직전에 15 까지 읽었으니 다시 seek → 12
        assert j == 12
    with VideoSource(clips["prores422"]) as src:             # 인트라 코덱은 항상 정확한 프레임
        assert src.info.intra
        assert src.read_fast(7, lo=0, max_side=512)[0] == 7


def test_analyze_real_files_end_to_end(clips):
    """실제 파일 디코더로 분석 전체가 도는지 (합성 소스가 아닌 PyAV 경로)."""
    from balance.analysis.clip import analyze_clip
    for name in ("prores422", "h264_tv_10"):
        a, thumb = analyze_clip(lambda: VideoSource(clips[name]), 2, 14, lambda g: "rec709_g24", None, name=name)
        assert thumb[""][:2] == b"\xff\xd8" and a["frames"] >= 4
        assert a["source"]["codec"] in ("prores", "h264") and a["source"]["bits"] == 10
        assert a["temporal"]["kind"] in ("steady", "ramp", "wave")   # 아랫단 회색이 프레임마다 밝아지는 테스트 영상
        assert a["faces"] == [] and a["detector"] == "none"
