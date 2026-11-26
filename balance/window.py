"""UI 창 열기.

별도 GUI 프레임워크 없이, 설치돼 있는 Edge/Chrome 을 앱 모드(--app)로 띄운다.
주소창 · 탭 없이 독립 창으로 뜨고, 전용 프로필을 쓰므로 창을 닫으면 프로세스가 끝나
그 시점에 앱도 함께 종료할 수 있다. 둘 다 없으면 기본 브라우저로 연다.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import webbrowser
from pathlib import Path


def _candidates() -> list[str]:
    out: list[str] = []
    if sys.platform == "win32":
        roots = [os.environ.get("PROGRAMFILES(X86)"), os.environ.get("PROGRAMFILES"), os.environ.get("LOCALAPPDATA")]
        for sub in (r"Microsoft\Edge\Application\msedge.exe", r"Google\Chrome\Application\chrome.exe"):
            out += [os.path.join(r, sub) for r in roots if r]
    elif sys.platform == "darwin":
        out += ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
                "/Applications/Chromium.app/Contents/MacOS/Chromium"]
    else:
        out += [shutil.which(n) or "" for n in ("google-chrome", "chromium", "chromium-browser", "microsoft-edge")]
    return [p for p in out if p and os.path.isfile(p)]


def open_window(url: str, profile_dir: Path, size: tuple[int, int] = (1520, 940)):
    """앱 창을 띄우고 프로세스 핸들을 돌려준다. 기본 브라우저로 열었으면 None."""
    for exe in _candidates():
        profile_dir.mkdir(parents=True, exist_ok=True)
        args = [exe, f"--app={url}", f"--user-data-dir={profile_dir}", f"--window-size={size[0]},{size[1]}",
                "--no-first-run", "--no-default-browser-check", "--disable-extensions", "--disable-sync",
                "--disable-background-mode",
                "--disable-features=Translate,msEdgeTranslate", "--autoplay-policy=no-user-gesture-required"]
        try:
            flags = 0x08000000 if sys.platform == "win32" else 0      # CREATE_NO_WINDOW
            return subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags)
        except OSError:
            continue
    webbrowser.open(url)
    return None
