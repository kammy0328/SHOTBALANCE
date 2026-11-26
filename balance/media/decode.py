"""원본 파일 디코딩 (PyAV / 내장 FFmpeg).

Resolve 가 클립을 읽는 방식과 같은 수치가 나오도록, 색 변환은 swscale 에 맡기지 않고
YUV 평면을 그대로 받아 직접 계산한다:
  - 데이터 레벨: 파일 플래그(Auto) 또는 Resolve 클립 속성(Video/Full)
  - 매트릭스: 파일 태그, 없으면 HD 이상 709 · SD 601
결과는 클리핑하지 않은 float32 R'G'B' 코드값(0..1 기준).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

import av
import numpy as np
from av.video.reformatter import ColorRange, Colorspace, Interpolation


class DecodeError(Exception):
    """사용자에게 그대로 보여줄 수 있는 디코딩 실패 사유."""


RAW_EXT = {
    ".r3d": "RED R3D", ".braw": "Blackmagic RAW", ".ari": "ARRIRAW", ".arx": "ARRIRAW",
    ".crm": "Canon Cinema RAW Light", ".dng": "CinemaDNG", ".nev": "Nikon N-RAW",
}

_KR_KB = {"bt709": (0.2126, 0.0722), "bt601": (0.299, 0.114),
          "bt2020": (0.2627, 0.0593), "smpte240m": (0.212, 0.087)}
_SPC = {1: "bt709", 5: "bt601", 6: "bt601", 7: "smpte240m", 9: "bt2020", 10: "bt2020"}
_YUV_FMT = {8: "yuv444p", 10: "yuv444p10le", 12: "yuv444p12le", 16: "yuv444p16le"}


@dataclass
class SourceInfo:
    path: str
    width: int = 0
    height: int = 0
    fps: float = 0.0
    n_frames: int = 0
    codec: str = ""
    profile: str = ""
    pix_fmt: str = ""
    bits: int = 8
    is_rgb: bool = False
    range_flag: str = "unspecified"   # tv | pc | unspecified
    matrix_flag: str = "unspecified"
    container: str = ""
    intra: bool = False
    tags: dict = field(default_factory=dict)


def fit_size(w: int, h: int, max_side: int) -> tuple[int, int]:
    if max(w, h) <= max_side:
        return w - (w % 2), h - (h % 2)
    k = max_side / max(w, h)
    return max(2, int(round(w * k / 2)) * 2), max(2, int(round(h * k / 2)) * 2)


def _plane(p, dtype) -> np.ndarray:
    a = np.frombuffer(p, dtype=dtype)
    stride = p.line_size // np.dtype(dtype).itemsize
    return a.reshape(-1, stride)[: p.height, : p.width]


def yuv_to_rgb(y, u, v, depth: int, full: bool, matrix: str) -> np.ndarray:
    """정수 YUV 4:4:4 평면 → float32 R'G'B' (클리핑 없음)."""
    s = float(1 << (depth - 8))
    y = y.astype(np.float32)
    u = u.astype(np.float32)
    v = v.astype(np.float32)
    if full:
        peak = 256.0 * s - 1.0
        yn = y / peak
        cb = (u - 128.0 * s) / peak
        cr = (v - 128.0 * s) / peak
    else:
        yn = (y - 16.0 * s) / (219.0 * s)
        cb = (u - 128.0 * s) / (224.0 * s)
        cr = (v - 128.0 * s) / (224.0 * s)
    kr, kb = _KR_KB[matrix]
    r = yn + (2.0 * (1.0 - kr)) * cr
    b = yn + (2.0 * (1.0 - kb)) * cb
    g = (yn - kr * r - kb * b) / (1.0 - kr - kb)
    return np.stack([r, g, b], axis=-1)


class VideoSource:
    """프레임 인덱스로 읽는 디코더. 인스턴스 하나는 한 스레드에서만 쓴다."""

    #: 이 간격 이내의 앞쪽 프레임은 seek 하지 않고 이어서 디코딩한다
    MAX_FORWARD = 48

    def __init__(self, path: str, data_level: str | None = None):
        self.path = path
        lvl = (data_level or "auto").strip().lower()
        self.data_level = "full" if lvl.startswith("full") else "video" if lvl.startswith("video") else "auto"
        ext = os.path.splitext(path)[1].lower()
        if ext in RAW_EXT:
            raise DecodeError(f"{RAW_EXT[ext]} 은 직접 읽을 수 없습니다. Resolve 에서 프록시를 연결하면 분석됩니다.")
        if not os.path.exists(path):
            raise DecodeError("원본 파일을 찾을 수 없습니다.")
        try:
            self._c = av.open(path)
        except Exception as e:  # noqa: BLE001
            raise DecodeError(f"파일을 열 수 없습니다 ({type(e).__name__}).") from e
        if not self._c.streams.video:
            self._c.close()
            raise DecodeError("비디오 스트림이 없습니다.")
        self._s = self._c.streams.video[0]
        try:
            self._s.thread_type = "AUTO"
        except Exception:  # noqa: BLE001
            pass
        self._gen = None
        self._next = None  # 디코더가 다음에 내놓을 프레임 인덱스
        self._count = 0
        self.info = self._probe()

    # ------------------------------------------------------------------ info
    def _probe(self) -> SourceInfo:
        s, c = self._s, self._c
        cc = s.codec_context
        rate = s.average_rate or getattr(s, "guessed_rate", None) or getattr(s, "base_rate", None)
        fps = float(rate) if rate else 0.0
        if fps <= 0:
            raise DecodeError("프레임레이트를 알 수 없습니다.")
        self.fps = fps
        self._tb = float(s.time_base) if s.time_base else 1.0 / fps
        self._start = s.start_time or 0
        n = int(s.frames or 0)
        if n <= 0:
            if s.duration:
                n = int(round(float(s.duration) * self._tb * fps))
            elif c.duration:
                n = int(round(c.duration / 1e6 * fps))
        fmt = cc.format
        name = fmt.name if fmt is not None else ""
        bits = max((comp.bits for comp in fmt.components), default=8) if fmt is not None else 8
        tags = {}
        for src in (c.metadata, s.metadata):
            for k, v in (src or {}).items():
                tags[str(k).lower()] = str(v)[:200]
        codec = cc.codec
        intra = bool(getattr(codec, "intra_only", False)) or cc.name in {
            "prores", "dnxhd", "mjpeg", "jpeg2000", "cfhd", "rawvideo", "v210", "v410", "r210", "hap", "png", "tiff", "dpx", "exr"}
        return SourceInfo(
            path=self.path, width=cc.width, height=cc.height, fps=fps, n_frames=max(n, 0),
            codec=cc.name or "", profile=str(getattr(cc, "profile", "") or ""), pix_fmt=name, bits=bits,
            is_rgb=bool(getattr(fmt, "is_rgb", False)) if fmt is not None else False,
            range_flag={1: "tv", 2: "pc"}.get(int(getattr(cc, "color_range", 0) or 0), "unspecified"),
            matrix_flag=_SPC.get(int(getattr(cc, "colorspace", 2) or 2), "unspecified"),
            container=c.format.name if c.format else "", intra=intra, tags=tags)

    # ---------------------------------------------------------------- frames
    def _frame_index(self, frame) -> int:
        if frame.pts is None:
            return self._count
        return int(round((frame.pts - self._start) * self._tb * self.fps))

    def _seek(self, index: int) -> None:
        pts = self._start + int(round(index / self.fps / self._tb))
        try:
            self._c.seek(max(pts, self._start), stream=self._s, backward=True, any_frame=False)
        except Exception:  # noqa: BLE001
            self._c.seek(0)
        self._gen = self._c.decode(self._s)
        self._next = None

    def _frame(self, index: int):
        if self._gen is None or self._next is None or index < self._next or index - self._next > self.MAX_FORWARD:
            self._seek(index)
        last = None
        for frame in self._gen:
            fi = self._frame_index(frame)
            self._count = fi + 1
            self._next = fi + 1
            last = frame
            if fi >= index:
                return frame
        self._gen = None
        self._next = None
        if last is not None:
            return last     # 끝을 넘겨 요청한 경우 마지막 프레임
        raise DecodeError(f"프레임 {index} 을 디코딩하지 못했습니다.")

    def read(self, index: int, max_side: int = 960) -> np.ndarray:
        """프레임 하나를 R'G'B' 코드값 (h, w, 3) float32 로."""
        index = max(0, int(index))
        try:
            frame = self._frame(index)
        except DecodeError:
            raise
        except Exception as e:  # noqa: BLE001
            raise DecodeError(f"디코딩 오류 ({type(e).__name__}: {str(e)[:80]})") from e
        return self._convert(frame, max_side)

    def read_fast(self, index: int, lo: int, max_side: int = 960) -> tuple[int, np.ndarray]:
        """index 근처의 프레임을 싸게 읽는다. (실제 프레임 번호, 코드값) 을 돌려준다.

        롱GOP 코덱에서 프레임 하나를 정확히 집으려면 키프레임부터 GOP 를 통째로 디코딩해야 한다.
        통계용 표본은 정확한 위치가 중요하지 않으므로, seek 해서 도착한 키프레임이 컷 범위(lo 이상)
        안이면 그 프레임을 그대로 쓴다. 인트라 코덱(ProRes 등)은 원래 싸므로 정확히 읽는다.
        """
        index = max(0, int(index))
        if self.info.intra:
            return index, self.read(index, max_side)
        try:
            near = self._gen is not None and self._next is not None and 0 <= index - self._next <= self.MAX_FORWARD
            if near:
                return index, self._convert(self._frame(index), max_side)
            self._seek(index)
            last, first = None, True
            for frame in self._gen:
                fi = self._frame_index(frame)
                self._count = self._next = fi + 1
                last = (fi, frame)
                if fi >= index or (first and fi >= lo):     # 정확한 프레임이거나, 컷 안에 떨어진 키프레임
                    return fi, self._convert(frame, max_side)
                first = False
            self._gen = self._next = None
            if last is not None:
                return last[0], self._convert(last[1], max_side)
        except DecodeError:
            raise
        except Exception as e:  # noqa: BLE001
            raise DecodeError(f"디코딩 오류 ({type(e).__name__}: {str(e)[:80]})") from e
        raise DecodeError(f"프레임 {index} 을 디코딩하지 못했습니다.")

    def _convert(self, frame, max_side: int) -> np.ndarray:
        fmt = frame.format
        name = fmt.name
        bits = max((c.bits for c in fmt.components), default=8)
        w, h = fit_size(frame.width, frame.height, max_side)
        interp = Interpolation.AREA if (w < frame.width) else Interpolation.BILINEAR
        rgb_like = bool(getattr(fmt, "is_rgb", False)) or name.startswith(("gray", "pal8", "monob", "monow", "ya"))
        if rgb_like:
            tgt = "rgb24" if bits <= 8 else "rgb48le"
            f2 = frame.reformat(width=w, height=h, format=tgt, interpolation=interp)
            arr = f2.to_ndarray()
            code = arr.astype(np.float32) / (255.0 if bits <= 8 else 65535.0)
            if self.data_level == "video":
                code = (code - 16.0 / 255.0) / (219.0 / 255.0)
            return np.ascontiguousarray(code)

        flagged_full = name.startswith("yuvj") or int(getattr(frame, "color_range", 0) or 0) == 2
        full = flagged_full if self.data_level == "auto" else (self.data_level == "full")
        depth = 8 if bits <= 8 else 10 if bits <= 10 else 12 if bits <= 12 else 16
        rng = ColorRange.JPEG if flagged_full else ColorRange.MPEG
        f2 = frame.reformat(
            width=w, height=h, format=_YUV_FMT[depth], interpolation=interp,
            src_colorspace=Colorspace.ITU709, dst_colorspace=Colorspace.ITU709,
            src_color_range=rng, dst_color_range=rng)
        dtype = np.uint8 if depth == 8 else np.dtype("<u2")
        y, u, v = (_plane(f2.planes[i], dtype) for i in range(3))
        matrix = _SPC.get(int(getattr(frame, "colorspace", 2) or 2))
        if matrix is None:
            matrix = "bt709" if frame.height >= 700 or frame.width >= 1200 else "bt601"
        return yuv_to_rgb(y, u, v, depth, full, matrix)

    def close(self) -> None:
        try:
            self._c.close()
        except Exception:  # noqa: BLE001
            pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # 합성 소스와 인터페이스를 맞추기 위한 자리
    def face_hints(self, index: int, shape: tuple[int, int]):
        return None
