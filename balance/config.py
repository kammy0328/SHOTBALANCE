"""경로와 기본 설정."""
from __future__ import annotations

import os
import sys
from pathlib import Path

from . import APP_NAME


def _frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def resource_dir() -> Path:
    """번들된 리소스(web, models)의 루트. exe 로 묶였을 때와 소스 실행을 모두 처리한다."""
    if _frozen():
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent.parent


def web_dir() -> Path:
    return resource_dir() / "web" if _frozen() else Path(__file__).resolve().parent / "web"


_LEGACY_NAME = "Balance"      # 0.1 이 쓰던 폴더 이름


def _app_dir(root: Path) -> Path:
    """root 아래의 앱 폴더. 0.1 의 폴더만 있으면 이름을 바꿔 그대로 이어 쓴다."""
    p = root / APP_NAME
    old = root / _LEGACY_NAME
    if not p.exists() and old.is_dir():
        try:
            old.rename(p)
        except OSError:
            pass
    return p


def data_dir() -> Path:
    env = os.environ.get("BALANCE_DATA_DIR")
    if env:
        p = Path(env)
    elif sys.platform == "win32":
        p = _app_dir(Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")))
    elif sys.platform == "darwin":
        p = _app_dir(Path.home() / "Library" / "Application Support")
    else:
        p = _app_dir(Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")))
    p.mkdir(parents=True, exist_ok=True)
    return p


def cache_dir() -> Path:
    env = os.environ.get("BALANCE_DATA_DIR")
    if env:
        p = Path(env) / "cache"
    elif sys.platform == "win32":
        p = _app_dir(Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))) / "cache"
    elif sys.platform == "darwin":
        p = _app_dir(Path.home() / "Library" / "Caches")
    else:
        p = _app_dir(Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")))
    p.mkdir(parents=True, exist_ok=True)
    return p


def model_dirs() -> list[Path]:
    """얼굴 모델을 찾는 위치: 번들(exe 안 또는 프로젝트의 models/) → 사용자 데이터 폴더(앱에서 내려받는 곳)."""
    user = data_dir() / "models"
    user.mkdir(parents=True, exist_ok=True)
    return [resource_dir() / "models", user]


DEFAULT_SETTINGS = {
    "node_label": "EXPOSURE",          # 값을 써 넣을 노드의 라벨
    "apply_mode": "linear_gain",       # linear_gain: 노드 Gamma=Linear + Gain / di_offset: DI 에서 Offset
    "auto_apply": True,                # 값이 바뀌면 바로 Resolve 에 쓴다
    "apply_low": False,                # 신뢰도 낮은 자동값도 적용
    "wb_enabled": True,                # WB 매칭 사용
    "black_enabled": True,             # 블랙/헤이즈 매칭 사용 (Linear Gain 방식에서만)
    "default_log": "slog3_sgamut3cine",
    "default_display": "rec709_g24",
    "follow_selection": True,          # 컷을 고르면 Resolve 재생헤드도 이동
}

EXP_STEP = 1.0 / 3.0          # 기본 버튼 한 번
EXP_STEP_FINE = 0.1
EXP_STEP_COARSE = 1.0
WB_STEP = 0.04                # WB 버튼 한 번 (스톱 단위 채널 차이)

#: 그룹 색 (Resolve 클립 컬러 계열의 차분한 톤)
GROUP_COLORS = ["#d98b4a", "#5fa8a0", "#b9a44c", "#8f7fc4", "#c96f7e", "#6f9fd1",
                "#8fb267", "#c58fb8", "#a98a6b", "#58b0c9", "#d1a06a", "#7d8fb3"]
