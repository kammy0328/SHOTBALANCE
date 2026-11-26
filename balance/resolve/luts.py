"""Resolve 가 읽는 LUT 폴더에 ShotBalance 의 LUT 파일을 둔다.

Graph.SetLUT 은 Resolve 의 마스터 LUT 폴더(와 사용자가 추가한 LUT 경로) 아래의 파일만 받는다.
다른 폴더의 절대 경로는 거절한다. 그래서 마스터 폴더 아래에 전용 하위 폴더를 만들고,
SetLUT 에는 그 폴더 기준의 상대 경로("ShotBalance/SB_sat108_den050.cube")를 넘긴다.
새 파일을 만든 뒤에는 Project.RefreshLUTList() 를 불러야 Resolve 가 파일을 찾는다.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from ..color import lut

SUBDIR = "ShotBalance"


def master_dir() -> Path:
    env = os.environ.get("RESOLVE_LUT_DIR")
    if env:
        return Path(env)
    if sys.platform == "win32":
        return Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData")) / "Blackmagic Design" / "DaVinci Resolve" / "Support" / "LUT"
    if sys.platform == "darwin":
        return Path("/Library/Application Support/Blackmagic Design/DaVinci Resolve/LUT")
    for cand in ("/opt/resolve/LUT", "/home/resolve/LUT"):
        if os.path.isdir(cand):
            return Path(cand)
    return Path("/opt/resolve/LUT")


def rel_path(sat: float, density: float) -> str:
    """SetLUT 에 넘기는 경로. 구분자는 Resolve 가 GetLUT 으로 돌려주는 형태(/)에 맞춘다."""
    return f"{SUBDIR}/{lut.lut_name(sat, density)}"


def ensure(root: Path, sat: float, density: float) -> tuple[str, bool]:
    """(상대 경로, 이번에 새로 만들었는지). 폴더에 쓸 수 없으면 OSError."""
    rel = rel_path(sat, density)
    path = root / SUBDIR / lut.lut_name(sat, density)
    if path.is_file() and path.stat().st_size > 1000:
        return rel, False
    path.parent.mkdir(parents=True, exist_ok=True)
    s, d = lut.quantize(sat, density)
    text = lut.cube_text(lut.build(s, d), f"ShotBalance saturation {s:.2f} density {d:.2f} (DaVinci WG/Intermediate)")
    tmp = path.with_suffix(".tmp")
    tmp.write_text(text, encoding="ascii")
    os.replace(tmp, path)
    return rel, True
