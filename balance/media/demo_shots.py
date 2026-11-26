"""데모 타임라인: 합성 촬영본 28컷.

실제 MV 편집본처럼 세트 3곳 + 미지정 컷을 섞었고, 컷마다 노출 · WB 오차, 카메라 인코딩,
플리커, 인서트, 입력 색공간이 잘못 지정된 컷 등을 넣어 기능을 한 번에 볼 수 있게 했다.
"""
from __future__ import annotations

from .synthetic import Figure, Shot

M1 = dict(ident="m1", skin=(0.50, 0.36, 0.29), top=(0.09, 0.09, 0.10))
M2 = dict(ident="m2", skin=(0.56, 0.40, 0.31), top=(0.42, 0.40, 0.38), bangs=True)
M3 = dict(ident="m3", skin=(0.44, 0.31, 0.25), top=(0.20, 0.07, 0.08))

SLOG, C3, C4 = "slog3_sgamut3cine", "logc3_awg3", "logc4_awg4"


def _f(base: dict, x: float, y: float, size: float, **kw) -> Figure:
    return Figure(x=x, y=y, size=size, **{**base, **kw})


#: (컷, 그룹 이름 | None, 레퍼런스 여부, 편집 길이 프레임)
DEMO = [
    # ---- 세트 A: 화이트 스튜디오 (A캠 S-Log3, B캠 LogC3)
    (Shot("A001C003_241102_R1AB", "studio", [_f(M1, 0.50, 0.40, 0.30, turn=0.5), _f(M2, 0.92, 0.37, 0.32, turn=-0.3), _f(M3, 1.33, 0.41, 0.29, turn=-0.5)],
          space=SLOG, seed=1), "스튜디오", True, 52),
    (Shot("A001C007_241102_R1AB", "studio", [_f(M1, 0.86, 0.42, 0.50, turn=0.6, spec=True)], zoom=1.25, pan=(-0.2, 0.0),
          cam_stops=0.62, cam_wb=(1.06, 1.0, 0.94), space=SLOG, seed=2), "스튜디오", False, 31),
    (Shot("A001C009_241102_R1AB", "studio", [_f(M2, 0.95, 0.44, 0.62, turn=-0.5)], zoom=1.5, pan=(0.1, -0.02),
          cam_stops=-0.48, cam_wb=(0.95, 1.0, 1.07), space=SLOG, seed=3), "스튜디오", False, 24),
    (Shot("A001C012_241102_R1AB", "studio", [_f(M3, 0.78, 0.43, 0.46, turn=0.4)], zoom=1.2, pan=(0.25, 0.0),
          cam_stops=-0.90, flare=(0.010, 0.010, 0.011), space=SLOG, seed=4), "스튜디오", False, 38),
    (Shot("B002C004_241102_R2CD", "studio", [_f(M1, 0.62, 0.42, 0.40, turn=0.5), _f(M3, 1.20, 0.43, 0.38, turn=-0.4)], zoom=1.1,
          cam_stops=0.31, cam_wb=(1.035, 1.0, 0.975), space=C3, seed=5), "스튜디오", False, 44),
    (Shot("A001C015_241102_R1AB", "studio", [_f(M1, 0.90, 0.45, 0.66, turn=-0.6, spec=True, bangs=True)], zoom=1.6,
          cam_stops=-0.28, space=SLOG, seed=6), "스튜디오", False, 19),
    (Shot("A001C018_241102_R1AB", "studio", prop="mic", zoom=1.7, pan=(0.0, 0.05),
          cam_stops=-0.42, space=SLOG, seed=7), "스튜디오", False, 15),
    (Shot("B002C009_241102_R2CD", "studio", [_f(M2, 0.84, 0.43, 0.56, turn=0.3)], zoom=1.4, pan=(-0.15, 0.0),
          cam_stops=0.81, cam_wb=(1.03, 1.0, 0.97), flare=(0.006, 0.006, 0.006), space=C3, seed=8), "스튜디오", False, 27),
    (Shot("A001C021_241102_R1AB", "studio", [_f(M1, 0.42, 0.42, 0.26, turn=0.4), _f(M2, 0.90, 0.40, 0.27, turn=-0.3), _f(M3, 1.38, 0.43, 0.25, turn=-0.5)],
          zoom=0.92, cam_stops=-0.20, space=SLOG, seed=9), "스튜디오", False, 61),
    (Shot("A001C024_241102_R1AB", "studio", prop="shoes", zoom=1.9, pan=(0.0, 0.26),
          cam_stops=0.52, space=SLOG, seed=10), "스튜디오", False, 14),

    # ---- 세트 B: LED 스테이지 (플리커 · 스트로브 조명)
    (Shot("A003C002_241103_R1AB", "neon", [_f(M1, 0.66, 0.42, 0.36, turn=0.5), _f(M2, 1.16, 0.41, 0.37, turn=-0.4)],
          space=SLOG, seed=11), "LED 스테이지", True, 47),
    (Shot("A003C005_241103_R1AB", "neon", [_f(M1, 0.92, 0.44, 0.60, turn=0.6, spec=True)], zoom=1.4,
          cam_stops=0.70, space=SLOG, seed=12), "LED 스테이지", False, 22),
    (Shot("A003C008_241103_R1AB", "neon", [_f(M2, 0.88, 0.44, 0.58, turn=-0.5)], zoom=1.35, pan=(0.12, 0.0),
          cam_stops=-0.60, space=SLOG, flicker="pwm", seed=13), "LED 스테이지", False, 33),
    (Shot("A003C011_241103_R1AB", "neon", [_f(M1, 0.60, 0.43, 0.42, turn=0.4), _f(M2, 1.22, 0.42, 0.42, turn=-0.4)], zoom=1.1,
          cam_stops=-0.30, space=SLOG, flicker="strobe", seed=14), "LED 스테이지", False, 40),
    (Shot("A003C014_241103_R1AB", "neon", prop="panel", zoom=1.6, pan=(0.3, -0.05),
          cam_stops=0.22, space=SLOG, seed=15), "LED 스테이지", False, 12),
    (Shot("A003C016_241103_R1AB", "neon", [_f(M1, 0.95, 0.45, 0.64, turn=-0.5)], zoom=1.5, pan=(-0.1, 0.0),
          cam_stops=-1.02, cam_wb=(0.97, 1.0, 1.04), flare=(0.004, 0.003, 0.006), space=SLOG, seed=16), "LED 스테이지", False, 26),
    (Shot("A003C019_241103_R1AB", "neon", [_f(M2, 0.80, 0.43, 0.48, turn=0.5)], zoom=1.2,
          cam_stops=0.41, space=SLOG, flicker="wave", seed=17), "LED 스테이지", False, 35),
    (Shot("A003C022_241103_R1AB", "neon", [_f(M1, 0.90, 0.44, 0.52, turn=0.2)], zoom=1.3,
          cam_stops=-2.40, space=SLOG, seed=18), "LED 스테이지", False, 18),      # 의도된 실루엣
    (Shot("GFX_INSERT_709_v2", "neon", [_f(M2, 0.92, 0.44, 0.56, turn=-0.3)], zoom=1.35,
          cam_stops=0.30, space="rec709_g24", tagged=SLOG, seed=19), "LED 스테이지", False, 21),

    # ---- 세트 C: 골든아워 룸
    (Shot("A005C001_241104_R1AB", "sunset", [_f(M3, 0.70, 0.44, 0.44, turn=0.6)],
          space=SLOG, seed=21), "골든아워", True, 55),
    (Shot("A005C004_241104_R1AB", "sunset", [_f(M3, 0.86, 0.45, 0.62, turn=0.5, spec=True)], zoom=1.45, pan=(-0.1, 0.0),
          cam_stops=-0.72, cam_wb=(0.94, 1.0, 1.08), flare=(0.012, 0.010, 0.008), space=SLOG, seed=22), "골든아워", False, 29),
    (Shot("A005C006_241104_R1AB", "sunset", prop="lamp", zoom=1.4, pan=(-0.05, 0.0),
          cam_stops=0.33, space=SLOG, seed=23), "골든아워", False, 16),
    (Shot("C001C002_241104_A35X", "sunset", [_f(M3, 0.74, 0.44, 0.50, turn=-0.4)], zoom=1.2, pan=(0.05, 0.0),
          cam_stops=0.50, cam_wb=(1.04, 1.0, 0.96), space=C4, seed=24), "골든아워", False, 42),
    (Shot("A005C009_241104_R1AB", "sunset", [_f(M3, 0.80, 0.45, 0.56, turn=0.5)], zoom=1.3,
          cam_stops=-0.10, space=SLOG, flicker="ramp", seed=25), "골든아워", False, 48),
    (Shot("A005C012_241104_R1AB", "sunset", [_f(M3, 0.60, 0.42, 0.30, turn=0.6)], zoom=0.95,
          cam_stops=-0.41, flare=(0.005, 0.005, 0.005), space=SLOG, seed=26), "골든아워", False, 37),

    # ---- 미지정: 루프탑 야간
    (Shot("A007C001_241105_R1AB", "night", [_f(M2, 1.05, 0.44, 0.44, turn=-0.5)],
          space=SLOG, seed=31), None, False, 45),
    (Shot("A007C003_241105_R1AB", "night", [_f(M2, 0.90, 0.45, 0.60, turn=0.4)], zoom=1.4,
          cam_stops=0.55, space=SLOG, seed=32), None, False, 23),
    (Shot("A007C006_241105_R1AB", "night", [_f(M1, 0.70, 0.43, 0.40, turn=0.5), _f(M2, 1.18, 0.44, 0.40, turn=-0.4)], zoom=1.05,
          cam_stops=-0.65, cam_wb=(1.05, 1.0, 0.95), space=SLOG, seed=33), None, False, 34),
]

#: 편집 순서 (세트를 오가며 섞는다)
EDIT_ORDER = [0, 1, 10, 2, 11, 3, 19, 4, 12, 20, 5, 13, 6, 25, 14, 7, 21, 15, 8, 22, 16, 26, 9, 23, 17, 27, 24, 18]
