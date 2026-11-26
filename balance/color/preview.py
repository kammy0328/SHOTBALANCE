"""미리보기용 변환.

썸네일은 '프리뷰 로그'(Rec.709 선형을 미드그레이 기준 -8..+6 스톱으로 8비트에 담은 것)로
저장한다. 화면에서는 JS 가 같은 상수로 풀어서 게인을 곱하고 뷰 톤커브를 입히므로,
노출/WB 값을 바꿀 때 서버 왕복 없이 선형 기준으로 다시 그릴 수 있다.
(web/js/thumbs.js 의 상수와 반드시 같아야 한다.)
"""
from __future__ import annotations

import cv2
import numpy as np

from . import curves, gamuts

PREVIEW_LO_STOPS = -8.0     # 미드그레이 대비 하한
PREVIEW_RANGE_STOPS = 14.0  # 8비트에 담는 범위
VIEW_K = 1.495              # 뷰 톤커브: y = (1 - exp(-k x)) ** g
VIEW_G = 1.15

_M709 = gamuts.matrix32("dwg", "rec709")


def to_rec709_linear(lin_dwg: np.ndarray) -> np.ndarray:
    return np.maximum(gamuts.apply(lin_dwg, _M709), 0.0)


def encode_preview_log(lin_dwg: np.ndarray) -> np.ndarray:
    """DWG 선형 → 프리뷰 로그 uint8 RGB."""
    x = to_rec709_linear(lin_dwg)
    with np.errstate(divide="ignore"):
        stops = np.log2(np.maximum(x, 1e-8) / 0.18)
    y = (stops - PREVIEW_LO_STOPS) / PREVIEW_RANGE_STOPS
    return np.clip(np.rint(y * 255.0), 0, 255).astype(np.uint8)


def decode_preview_log(img: np.ndarray) -> np.ndarray:
    """프리뷰 로그 uint8 → Rec.709 선형 (테스트·검증용)."""
    y = img.astype(np.float32) / 255.0
    x = 0.18 * np.power(2.0, y * PREVIEW_RANGE_STOPS + PREVIEW_LO_STOPS)
    return np.where(img == 0, 0.0, x).astype(np.float32)


def view(rec709_lin: np.ndarray) -> np.ndarray:
    """Rec.709 선형 → 화면용 sRGB (0..1)."""
    y = np.power(1.0 - np.exp(-VIEW_K * np.maximum(rec709_lin, 0.0)), VIEW_G)
    return curves.srgb_encode(y)


def thumb_jpeg(lin_dwg: np.ndarray, width: int = 320) -> bytes:
    h, w = lin_dwg.shape[:2]
    if w != width:
        hh = max(2, int(round(h * width / w)))
        lin_dwg = cv2.resize(lin_dwg, (width, hh), interpolation=cv2.INTER_AREA)
    img = encode_preview_log(lin_dwg)
    params = [cv2.IMWRITE_JPEG_QUALITY, 93]
    if hasattr(cv2, "IMWRITE_JPEG_SAMPLING_FACTOR"):
        params += [cv2.IMWRITE_JPEG_SAMPLING_FACTOR, cv2.IMWRITE_JPEG_SAMPLING_FACTOR_444]
    ok, buf = cv2.imencode(".jpg", img[..., ::-1], params)
    if not ok:
        raise RuntimeError("thumbnail encode failed")
    return buf.tobytes()


def detector_image(lin_dwg: np.ndarray) -> np.ndarray:
    """얼굴 검출기에 넣을 BGR uint8. 검출기는 디스플레이 영상으로 학습됐으므로
    프레임마다 노출을 자동으로 맞추고 뷰 커브를 입힌다 (측정에는 쓰지 않는다)."""
    x = to_rec709_linear(lin_dwg)
    lum = x[..., 0] * np.float32(0.2126) + x[..., 1] * np.float32(0.7152) + x[..., 2] * np.float32(0.0722)
    valid = lum[lum > 1e-4]
    med = float(np.median(valid)) if valid.size else 0.18
    hi = float(np.percentile(valid, 90)) if valid.size else 0.18
    # 중앙값을 0.16 근처로, 다만 밝은 쪽(얼굴이 있을 법한 영역)이 날아가지 않게
    gain = np.clip(min(0.16 / max(med, 1e-4), 0.75 / max(hi, 1e-4)), 1 / 32, 64)
    # 채널별로 강한 색조명을 조금 풀어준다 (검출률용)
    means = np.maximum(x.reshape(-1, 3).mean(axis=0), 1e-5)
    bal = np.clip(means.mean() / means, 0.5, 2.0) ** 0.5
    img = view(x * (gain * bal).astype(np.float32))
    return np.clip(np.rint(img[..., ::-1] * 255.0), 0, 255).astype(np.uint8)
