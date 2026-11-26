"""절차적으로 그리는 합성 촬영본.

실제 파일 없이 전체 파이프라인을 돌리기 위한 소스다 (데모 모드, 자동 테스트).
장면을 scene-linear 로 그린 뒤 '카메라'(노출 · WB · 색역 · 로그 인코딩 · 노이즈 · 플리커)를
거쳐 코드값을 내놓으므로, 컷마다 정답(얼마나 틀어져 있는지)을 알고 있다.
얼굴 검출기는 그림 얼굴을 못 찾으므로 얼굴 위치와 인물 ID 를 힌트로 함께 넘긴다.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ..color import curves, gamuts, spaces

ASPECT = 16 / 9
_TO_DWG = gamuts.matrix32("rec709", "dwg")


def _c(rgb) -> np.ndarray:
    return np.asarray(rgb, np.float32)


@dataclass
class Figure:
    ident: str
    x: float                 # 머리 중심 (프레임 높이 = 1 기준, x 는 0..ASPECT)
    y: float
    size: float              # 머리 높이
    skin: tuple = (0.50, 0.36, 0.29)   # Rec.709 선형 반사율
    hair: tuple = (0.020, 0.016, 0.015)
    top: tuple = (0.10, 0.10, 0.11)
    bangs: bool = False
    turn: float = 0.4        # +면 화면 오른쪽 뺨이 키라이트 쪽
    spec: bool = False


@dataclass
class Shot:
    name: str
    scene: str
    figures: list = field(default_factory=list)
    zoom: float = 1.0
    pan: tuple = (0.0, 0.0)
    cam_stops: float = 0.0             # 촬영본에 구워진 노출 오차
    cam_wb: tuple = (1.0, 1.0, 1.0)    # 촬영본에 구워진 WB 오차 (채널 게인)
    flare: tuple = (0.0, 0.0, 0.0)     # 헤이즈 · 렌즈 플레어로 뜬 블랙 (장면 선형, DWG)
    space: str = "slog3_sgamut3cine"   # 실제 인코딩
    tagged: str | None = None          # Resolve 에 지정돼 있는 입력 색공간 (None = 올바름)
    n_frames: int = 72
    fps: float = 24.0
    flicker: str | None = None         # pwm | wave | strobe | ramp
    noise: float = 0.0035
    seed: int = 0
    prop: str | None = None            # 인서트 소품


def _aa(d: np.ndarray, px: float) -> np.ndarray:
    return np.clip(0.5 - d / px, 0.0, 1.0)


def _ellipse(X, Y, cx, cy, rx, ry):
    return (np.sqrt(((X - cx) / rx) ** 2 + ((Y - cy) / ry) ** 2) - 1.0) * min(rx, ry)


def _over(img, color, alpha, cover=None):
    img *= (1.0 - alpha)[..., None]
    img += alpha[..., None] * color
    if cover is not None:
        cover += (1.0 - cover) * alpha


# ------------------------------------------------------------------ 세트
SCENES = {
    "studio": dict(key=(0.92, 0.92, 0.94), rim=(0.25, 0.27, 0.32), amb=0.20),
    "neon": dict(key=(0.95, 0.42, 0.88), rim=(0.10, 0.75, 1.10), amb=0.10),
    "sunset": dict(key=(1.05, 0.74, 0.46), rim=(0.55, 0.30, 0.16), amb=0.16),
    "night": dict(key=(0.42, 0.52, 0.80), rim=(0.90, 0.55, 0.25), amb=0.08),
}


def _background(scene: str, X, Y, rng_seed: int) -> tuple[np.ndarray, np.ndarray]:
    """(조명을 받는 면, 스스로 빛나는 것) 을 Rec.709 선형으로."""
    h, w = X.shape
    lit = np.zeros((h, w, 3), np.float32)
    emi = np.zeros((h, w, 3), np.float32)
    t = np.clip(Y, 0, 1)[..., None]
    if scene == "studio":
        lit[:] = _c((0.60, 0.61, 0.64)) * (1 - t) + _c((0.30, 0.31, 0.34)) * t
        pool = np.exp(-(((X - 0.95) / 0.75) ** 2 + ((Y - 0.30) / 0.42) ** 2))
        lit += pool[..., None] * _c((0.22, 0.22, 0.23))
        floor = np.clip((Y - 0.74) / 0.02, 0, 1)
        lit *= (1.0 - 0.22 * floor)[..., None]
    elif scene == "neon":
        lit[:] = _c((0.020, 0.016, 0.030)) + _c((0.035, 0.012, 0.050)) * (1 - t)
        cols = [_c((1.0, 0.07, 0.72)), _c((0.05, 0.70, 1.0))]
        for k in range(9):
            x0 = -0.35 + k * 0.31
            col = cols[k % 2] * (1.9 if k % 3 else 2.6)
            dx = np.abs(X - x0)
            bar = np.clip((0.018 - dx) / 0.004, 0, 1) * np.clip((0.66 - Y) / 0.01, 0, 1) * np.clip((Y - 0.04) / 0.01, 0, 1)
            glow = np.exp(-dx / 0.07) * np.clip((0.72 - Y) / 0.2, 0, 1) * 0.10
            refl = np.exp(-dx / 0.05) * np.clip((Y - 0.72) / 0.05, 0, 1) * np.exp(-(Y - 0.72) / 0.18) * 0.14
            emi += (bar + glow + refl)[..., None] * col
    elif scene == "sunset":
        lit[:] = _c((0.50, 0.30, 0.17)) * (1 - t) + _c((0.20, 0.11, 0.075)) * t
        win = np.clip((X - 1.18) / 0.06, 0, 1) * np.clip((0.62 - Y) / 0.05, 0, 1) * np.clip((Y - 0.10) / 0.05, 0, 1)
        emi += win[..., None] * _c((1.9, 1.25, 0.70))
        emi += np.exp(-(np.maximum(1.18 - X, 0) / 0.35))[..., None] * _c((0.16, 0.09, 0.04)) * np.clip(1 - Y, 0, 1)[..., None]
        r = np.random.default_rng(7)
        for _ in range(7):
            cx, cy, rad = r.uniform(0.05, 1.0), r.uniform(0.08, 0.5), r.uniform(0.018, 0.04)
            d = np.sqrt((X - cx) ** 2 + (Y - cy) ** 2)
            emi += np.clip((rad - d) / (rad * 0.35), 0, 1)[..., None] * _c((1.5, 0.95, 0.42)) * r.uniform(0.5, 1.0)
    else:  # night
        lit[:] = _c((0.020, 0.032, 0.085)) * (1 - t) + _c((0.010, 0.014, 0.034)) * t
        sky = np.clip((0.58 - Y) / 0.5, 0, 1)
        emi += sky[..., None] * _c((0.012, 0.022, 0.060))
        r = np.random.default_rng(11)
        for _ in range(46):
            cx, cy, rad = r.uniform(-0.2, 2.0), r.uniform(0.40, 0.66), r.uniform(0.004, 0.011)
            col = _c((1.6, 1.0, 0.45)) if r.random() < 0.6 else _c((0.5, 0.8, 1.6))
            d = np.sqrt((X - cx) ** 2 + (Y - cy) ** 2)
            emi += np.clip((rad - d) / (rad * 0.5), 0, 1)[..., None] * col * r.uniform(0.3, 1.0)
        rail = np.clip((Y - 0.70) / 0.006, 0, 1) * np.clip((0.725 - Y) / 0.006, 0, 1)
        lit *= (1.0 - 0.5 * rail)[..., None]
    return lit, emi


def _prop(kind: str, X, Y, lit, cover):
    px = 1.5 / X.shape[0]
    if kind == "mic":
        body = np.clip((0.045 - np.abs(X - 0.92)) / px, 0, 1) * np.clip((Y - 0.52) / px, 0, 1)
        _over(lit, _c((0.07, 0.07, 0.08)), body, cover)
        _over(lit, _c((0.05, 0.05, 0.055)), _aa(_ellipse(X, Y, 0.92, 0.40, 0.12, 0.16), px), cover)
        _over(lit, _c((0.34, 0.34, 0.36)), _aa(_ellipse(X, Y, 0.90, 0.37, 0.07, 0.09), px) * 0.55, cover)
    elif kind == "shoes":
        for cx in (0.62, 1.16):
            _over(lit, _c((0.10, 0.10, 0.11)), _aa(_ellipse(X, Y, cx, 0.74, 0.25, 0.035), px), cover)
            _over(lit, _c((0.78, 0.76, 0.72)), _aa(_ellipse(X, Y, cx, 0.66, 0.24, 0.10), px), cover)
    elif kind == "lamp":
        stem = np.clip((0.02 - np.abs(X - 0.80)) / px, 0, 1) * np.clip((Y - 0.48) / px, 0, 1)
        _over(lit, _c((0.10, 0.07, 0.05)), stem, cover)
        _over(lit, _c((0.55, 0.36, 0.20)), _aa(_ellipse(X, Y, 0.80, 0.34, 0.20, 0.16), px), cover)
    elif kind == "panel":
        g = (np.sin(X * 40) * np.sin(Y * 40) > 0).astype(np.float32)
        _over(lit, _c((0.10, 0.08, 0.14)), g * 0.5)


# ------------------------------------------------------------------ 렌더
def _flicker_gain(kind: str | None, i: int, n: int) -> tuple[float, float]:
    """(조명 배수, 화이트아웃 여부용 추가 배수)."""
    if kind == "pwm":
        return (1.0 if (i % 5) < 3 else 0.34), 1.0
    if kind == "wave":
        return 1.0 + 0.34 * float(np.sin(2 * np.pi * i / 9.0)), 1.0
    if kind == "strobe":
        if i % 11 == 5:
            return 7.0, 1.0
        if i % 11 in (6, 7):
            return 0.04, 1.0
        return 1.0, 1.0
    if kind == "ramp":
        return 0.55 + 0.9 * (i / max(n - 1, 1)), 1.0
    return 1.0, 1.0


def _static_layers(shot: Shot, w: int, h: int) -> dict:
    """프레임과 무관한 레이어(세트, 인물, 하이라이트)를 한 번만 그린다."""
    ys = (np.arange(h, dtype=np.float32) + 0.5) / h
    xs = (np.arange(w, dtype=np.float32) + 0.5) / h
    Xf, Yf = np.meshgrid(xs, ys)                        # 프레임 좌표
    X = (Xf - ASPECT / 2) / shot.zoom + ASPECT / 2 + shot.pan[0]   # 세트 좌표
    Y = (Yf - 0.5) / shot.zoom + 0.5 + shot.pan[1]
    sc = SCENES[shot.scene]
    key, rim = _c(sc["key"]), _c(sc["rim"])

    lit, emi = _background(shot.scene, X, Y, shot.seed)
    cover = np.zeros((h, w), np.float32)                # 세트의 발광체를 가리는 정도
    if shot.prop:
        _prop(shot.prop, Xf, Yf, lit, cover)
    px = 1.5 / h
    faces = []
    extra = np.zeros((h, w, 3), np.float32)             # 키라이트에 비례해 더해지는 빛 (광, 림)
    for fg in shot.figures:
        a = fg.size / 2.0
        b = 0.76 * a
        fx, fy = fg.x, fg.y
        hairline = fy - (0.22 * a if fg.bangs else 0.70 * a)
        _over(lit, _c(fg.top), _aa(_ellipse(Xf, Yf, fx, fy + 2.75 * a, 2.05 * a, 1.75 * a), px), cover)
        neck = np.clip((0.34 * b - np.abs(Xf - fx)) / px, 0, 1) * np.clip((Yf - fy) / px, 0, 1) * np.clip((fy + 1.25 * a - Yf) / px, 0, 1)
        _over(lit, _c(fg.skin) * 0.82, neck, cover)
        _over(lit, _c(fg.hair), _aa(_ellipse(Xf, Yf, fx, fy - 0.06 * a, 1.14 * b, 1.12 * a), px), cover)
        head = _aa(_ellipse(Xf, Yf, fx, fy, b, a), px)
        shade = (1.0 + 0.30 * fg.turn * (Xf - fx) / b) * (1.0 - 0.10 * (Yf - fy) / a)
        skin = _c(fg.skin)[None, None, :] * np.clip(shade, 0.45, 1.6)[..., None]
        face_alpha = head * np.clip((Yf - hairline) / px, 0, 1)
        _over(lit, np.float32(0.0), face_alpha)
        lit += face_alpha[..., None] * skin
        if fg.spec:                                     # 광대 위의 광
            sgn = 1.0 if fg.turn >= 0 else -1.0
            d2 = (Xf - (fx + sgn * 0.44 * b)) ** 2 + (Yf - (fy + 0.10 * a)) ** 2
            extra += (np.exp(-d2 / (0.075 * b) ** 2) * face_alpha)[..., None] * key * 1.8
        edge = np.clip(1.0 - np.abs(_ellipse(Xf, Yf, fx, fy, b, a)) / (0.10 * b), 0, 1) * head
        side = np.clip(-np.sign(fg.turn or 1.0) * (Xf - fx) / b, 0, 1)
        extra += (edge * side)[..., None] * rim * 0.35
        faces.append(dict(
            ident=fg.ident, score=0.99,
            box=((fx - b) * h, (fy - a) * h, 2 * b * h, 2 * a * h),
            lm=[((fx - 0.38 * b) * h, (fy - 0.10 * a) * h), ((fx + 0.38 * b) * h, (fy - 0.10 * a) * h),
                (fx * h, (fy + 0.18 * a) * h),
                ((fx - 0.30 * b) * h, (fy + 0.50 * a) * h), ((fx + 0.30 * b) * h, (fy + 0.50 * a) * h)]))
    r2 = ((Xf - ASPECT / 2) / (ASPECT / 2)) ** 2 + ((Yf - 0.5) / 0.5) ** 2
    vig = (1.0 - 0.08 * r2)[..., None]
    return dict(lit=lit * vig, extra=extra * vig, emi=emi * (1.0 - cover)[..., None] * vig, faces=faces)


def render_scene(shot: Shot, index: int, w: int, h: int, layers: dict | None = None):
    """DWG 선형 장면(카메라 오차 적용 전)과 얼굴 기하를 돌려준다."""
    L = layers or _static_layers(shot, w, h)
    sc = SCENES[shot.scene]
    key, amb = _c(sc["key"]), sc["amb"]
    f_light, _ = _flicker_gain(shot.flicker, index, shot.n_frames)
    img = L["lit"] * (key[None, None, :] * (amb + (1.0 - amb) * f_light))
    img += L["extra"] * np.float32(f_light)
    img += L["emi"] * np.float32(0.55 + 0.45 * min(f_light, 1.5))
    return gamuts.apply(img.astype(np.float32), _TO_DWG), L["faces"]


def camera_encode(lin_dwg: np.ndarray, shot: Shot, index: int) -> np.ndarray:
    """장면 → 촬영본 코드값 (노출/WB 오차, 인코딩, 노이즈, 10비트 양자화)."""
    gain = (2.0 ** shot.cam_stops) * _c(shot.cam_wb)
    x = (lin_dwg + _c(shot.flare)) * gain
    sp = spaces.SPACES[shot.space]
    if sp.kind == "display":
        x = gamuts.apply(x, gamuts.matrix32("dwg", sp.gamut))
        x = np.clip(x, 0.0, 1.0)                       # 카메라 내부 709: 1.0 위는 잘린다
        code = curves.TRANSFERS[sp.transfer][1](x)
    else:
        code = spaces.from_dwg_linear(x, shot.space)
        peak = float(curves.TRANSFERS[sp.transfer][1](np.array([40.0], np.float32))[0])
        code = np.minimum(code, peak)                  # 센서 클립
    rng = np.random.default_rng((shot.seed * 100003 + index) & 0x7FFFFFFF)
    code = code + rng.normal(0.0, shot.noise, size=code.shape).astype(np.float32)
    return (np.rint(np.clip(code, 0.0, 1.0) * 1023.0) / 1023.0).astype(np.float32)


class SyntheticSource:
    """VideoSource 와 같은 인터페이스의 합성 소스."""

    def __init__(self, shot: Shot):
        self.shot = shot
        from .decode import SourceInfo
        self.fps = shot.fps
        self.info = SourceInfo(path=f"synthetic://{shot.name}", width=1920, height=1080, fps=shot.fps,
                               n_frames=shot.n_frames, codec="synthetic", pix_fmt="rgb10", bits=10, is_rgb=True,
                               container="synthetic", intra=True, tags={})
        self._layers: dict[tuple[int, int], dict] = {}

    def _size(self, max_side: int) -> tuple[int, int]:
        w = min(max_side, 960)
        w -= w % 2
        return w, int(round(w / ASPECT / 2)) * 2

    def _get_layers(self, w: int, h: int) -> dict:
        if (w, h) not in self._layers:
            if len(self._layers) >= 2:
                self._layers.clear()
            self._layers[(w, h)] = _static_layers(self.shot, w, h)
        return self._layers[(w, h)]

    def read(self, index: int, max_side: int = 960) -> np.ndarray:
        index = int(np.clip(index, 0, self.shot.n_frames - 1))
        w, h = self._size(max_side)
        lin, _ = render_scene(self.shot, index, w, h, self._get_layers(w, h))
        return camera_encode(lin, self.shot, index)

    def read_fast(self, index: int, lo: int, max_side: int = 960):
        return int(index), self.read(index, max_side)

    def face_hints(self, index: int, shape: tuple[int, int]):
        """요청한 프레임 크기 기준의 얼굴 위치 · 랜드마크 · 인물 ID."""
        h, w = shape
        faces = self._get_layers(w, h)["faces"] if (w, h) in self._layers else _static_layers(self.shot, w, h)["faces"]
        return [dict(ident=f["ident"], score=f["score"], box=tuple(f["box"]), lm=list(f["lm"])) for f in faces]

    def close(self) -> None:
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def truth_gain(shot: Shot, ref: Shot) -> np.ndarray:
    """shot 을 ref 에 맞추는 정답: 채널별 총 선형 게인 (노출 포함)."""
    return (2.0 ** ref.cam_stops * _c(ref.cam_wb)) / (2.0 ** shot.cam_stops * _c(shot.cam_wb))


def truth_black(shot: Shot, ref: Shot) -> np.ndarray:
    """shot 을 ref 에 맞추는 정답의 선형 오프셋 (게인 적용 뒤, ref 의 노출 단위). L' = gain·L + offset."""
    return (2.0 ** ref.cam_stops * _c(ref.cam_wb)) * (_c(ref.flare) - _c(shot.flare))
