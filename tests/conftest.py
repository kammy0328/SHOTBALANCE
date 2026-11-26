import os
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import balance  # noqa: E402,F401  numpy 보다 먼저: BLAS 스레드 수를 여기서 고정한다

import numpy as np  # noqa: E402
import pytest  # noqa: E402


def ffmpeg_exe() -> str | None:
    """시스템의 ffmpeg, 없으면 imageio-ffmpeg 가 들고 있는 실행 파일."""
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:  # noqa: BLE001
        return None

W, H, N = 256, 144, 16
PATCHES = [  # R'G'B' 코드값 (0..1)
    (0.10, 0.10, 0.10), (0.42, 0.42, 0.42), (0.80, 0.80, 0.80), (0.60, 0.30, 0.20),
    (0.20, 0.55, 0.35), (0.25, 0.30, 0.70), (0.70, 0.65, 0.20), (0.55, 0.25, 0.60),
]


def frame_level(i: int) -> float:
    return 0.20 + 0.03 * i


def make_frames(w: int = W, h: int = H, n: int = N) -> np.ndarray:
    """(n, h, w, 3) float. 윗줄 8개 패치는 고정색, 아랫줄은 프레임 번호를 담은 회색."""
    out = np.zeros((n, h, w, 3), np.float64)
    pw = w // 8
    for k, c in enumerate(PATCHES):
        out[:, : h // 2, k * pw:(k + 1) * pw] = c
    for i in range(n):
        out[i, h // 2:] = frame_level(i)
    return out


TAG709 = ["-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709", "-color_range", "tv"]

# prores_ks 는 프레임 속성에서 태그를 읽으므로 필터로 심어 준다
SET709 = "setparams=colorspace=bt709:color_primaries=bt709:color_trc=bt709:range=tv"

# name: (K 가중치, legal?, 비트, 입력 pix_fmt, 필터, 코덱 인자, 확장자)
VARIANTS = {
    "prores422": ("ITU-R BT.709", True, 10, "yuv444p10le", SET709 + ",format=yuv422p10le",
                  ["-c:v", "prores_ks", "-profile:v", "3", *TAG709], "mov"),
    "prores4444": ("ITU-R BT.709", True, 10, "yuv444p10le", SET709,
                   ["-c:v", "prores_ks", "-profile:v", "4", *TAG709], "mov"),
    "h264_full": ("ITU-R BT.709", False, 8, "yuvj444p", "format=yuvj420p",
                  ["-c:v", "libx264", "-qp", "0", "-g", "12", "-bf", "2", "-colorspace", "bt709", "-color_range", "pc"], "mp4"),
    "h264_tv_10": ("ITU-R BT.709", True, 10, "yuv444p10le", "format=yuv422p10le",
                   ["-c:v", "libx264", "-qp", "0", "-g", "12", "-bf", "2", "-colorspace", "bt709", "-color_range", "tv"], "mp4"),
    "hevc_2020": ("ITU-R BT.2020", True, 10, "yuv444p10le", "format=yuv420p10le",
                  ["-c:v", "libx265", "-x265-params", "lossless=1:log-level=error", "-colorspace", "bt2020nc", "-color_range", "tv"], "mp4"),
    # 태그 없는 파일: SD 크기는 601, HD 이상은 709 로 읽어야 한다
    "untagged_sd": ("ITU-R BT.601", True, 8, "yuv444p", "format=yuv420p",
                    ["-c:v", "libx264", "-qp", "0", "-g", "12"], "mp4"),
    "untagged_hd": ("ITU-R BT.709", True, 10, "yuv444p10le", "format=yuv422p10le",
                    ["-c:v", "prores_ks", "-profile:v", "3"], "mov"),
}
SIZES = {"untagged_hd": (1280, 720, 3)}


def _ycbcr(frames: np.ndarray, k: str, legal: bool, bits: int) -> np.ndarray:
    """기준 구현(colour-science)으로 R'G'B' → 정수 Y'CbCr 4:4:4 평면 (N, 3, H, W)."""
    import colour
    ycc = colour.RGB_to_YCbCr(frames, K=colour.WEIGHTS_YCBCR[k], in_bits=16, in_legal=False, in_int=False,
                              out_bits=bits, out_legal=legal, out_int=True)
    ycc = np.moveaxis(np.asarray(ycc), -1, 1)
    return ycc.astype(np.uint8 if bits == 8 else "<u2")


@pytest.fixture(scope="session")
def clips(tmp_path_factory):
    pytest.importorskip("colour")
    exe = ffmpeg_exe()
    if exe is None:
        pytest.skip("ffmpeg 실행 파일이 없습니다")
    d = tmp_path_factory.mktemp("clips")
    paths = {}
    for name, (k, legal, bits, pix, vf, codec, ext) in VARIANTS.items():
        w, h, n = SIZES.get(name, (W, H, N))
        raw = d / f"{name}.yuv"
        _ycbcr(make_frames(w, h, n), k, legal, bits).tofile(raw)
        out = d / f"{name}.{ext}"
        cmd = [exe, "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", pix, "-s", f"{w}x{h}",
               "-r", "24", "-i", str(raw), "-vf", vf, *codec, str(out)]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode == 0:
            paths[name] = str(out)
        else:
            print("ffmpeg failed", name, r.stderr[-300:])
    return paths
