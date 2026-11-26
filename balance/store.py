"""프로젝트별 사이드카 저장소.

값은 타임라인 위치가 아니라 소스(파일명 + 소스 프레임 범위) 기준으로 저장한다.
재편집 · 리컨폼으로 컷 위치가 바뀌어도, 같은 소스 구간을 쓰는 다른 타임라인(본편 · 티저)에서도
같은 값이 따라온다.

값은 세 층으로 나뉜다:  최종 = 자동 매칭값 + 그룹 의도 + 컷 의도
"""
from __future__ import annotations

import copy
import json
import os
import re
import threading
import time
from pathlib import Path

from .config import DEFAULT_SETTINGS

SCHEMA = 1


def cut_key(src_name: str, src_in: int, src_out: int) -> str:
    return f"{os.path.basename(src_name).lower()}|{int(src_in)}|{int(src_out)}"


def split_key(key: str) -> tuple[str, int, int]:
    name, a, b = key.rsplit("|", 2)
    return name, int(a), int(b)


def new_cut() -> dict:
    return {"group": None, "intent": {"exp": 0.0, "temp": 0.0, "tint": 0.0}, "auto": None,
            "locked": False, "frozen": None, "excluded": False, "use_exp": None, "use_wb": None, "use_black": None,
            "space": None}


def new_intent() -> dict:
    return {"exp": 0.0, "temp": 0.0, "tint": 0.0}


def _slug(s: str) -> str:
    s = re.sub(r"[^\w\-]+", "_", s, flags=re.UNICODE).strip("_")
    return s[:60] or "project"


def _atomic_write(path: Path, data: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, path)


class ProjectStore:
    def __init__(self, root: Path, project_name: str, project_id: str):
        root.mkdir(parents=True, exist_ok=True)
        base = f"{_slug(project_name)}-{_slug(project_id)[:8]}"
        self.path = root / f"{base}.json"
        self.analysis_path = root / f"{base}.analysis.json"
        self._lock = threading.RLock()
        self._dirty = False
        self._dirty_analysis = False
        self.data = {"schema": SCHEMA, "project": {"name": project_name, "id": project_id},
                     "settings": dict(DEFAULT_SETTINGS), "groups": {}, "group_order": [], "cuts": {}, "next_group": 1}
        self.analysis: dict[str, dict] = {}
        self._load()

    # ---------------------------------------------------------------- io
    def _load(self) -> None:
        try:
            if self.path.is_file():
                d = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(d, dict) and d.get("schema") == SCHEMA:
                    d["settings"] = {**DEFAULT_SETTINGS, **d.get("settings", {})}
                    for c in d.get("cuts", {}).values():
                        for k, v in new_cut().items():
                            c.setdefault(k, copy.deepcopy(v))
                    self.data.update(d)
        except Exception:  # noqa: BLE001  손상된 파일은 옆에 남겨 두고 새로 시작
            try:
                os.replace(self.path, self.path.with_suffix(f".broken-{int(time.time())}.json"))
            except OSError:
                pass
        try:
            if self.analysis_path.is_file():
                self.analysis = json.loads(self.analysis_path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            self.analysis = {}

    def touch(self, analysis: bool = False) -> None:
        with self._lock:
            self._dirty = True
            self._dirty_analysis = self._dirty_analysis or analysis

    def flush(self) -> None:
        with self._lock:
            if self._dirty:
                _atomic_write(self.path, self.data)
                self._dirty = False
            if self._dirty_analysis:
                _atomic_write(self.analysis_path, self.analysis)
                self._dirty_analysis = False

    # ------------------------------------------------------------- access
    @property
    def settings(self) -> dict:
        return self.data["settings"]

    @property
    def groups(self) -> dict:
        return self.data["groups"]

    @property
    def cuts(self) -> dict:
        return self.data["cuts"]

    def cut(self, key: str) -> dict:
        """키에 해당하는 값. 처음 보는 키면, 같은 파일에서 소스 구간이 가장 많이 겹치는
        기존 값(재편집 전의 컷)을 물려받는다."""
        c = self.cuts.get(key)
        if c is None:
            c = self._inherit(key) or new_cut()
            self.cuts[key] = c
            self.touch()
        return c

    def find(self, key: str) -> str | None:
        """같은 소스 구간을 가리키는 저장된 키 (읽기 전용). 정확히 같은 키가 없으면,
        같은 파일에서 이 구간의 절반 이상이 겹치는 키 가운데 가장 많이 겹치는 것."""
        if key in self.cuts:
            return key
        name, a, b = split_key(key)
        best, best_ov = None, 0.0
        for k in self.cuts:
            n2, a2, b2 = split_key(k)
            if n2 != name:
                continue
            ov = max(0, min(b, b2) - max(a, a2)) / max(1, b - a)
            if ov > best_ov:
                best, best_ov = k, ov
        return best if best_ov >= 0.5 else None

    def _inherit(self, key: str) -> dict | None:
        best = self.find(key)
        if best is None:
            return None
        c = copy.deepcopy(self.cuts[best])
        c["auto"] = None          # 구간이 달라졌으니 측정은 다시 한다
        return c

    def snapshot(self) -> dict:
        """되돌리기용: 사용자 값만 복사한다 (분석 캐시는 제외)."""
        return copy.deepcopy({"groups": self.data["groups"], "group_order": self.data["group_order"],
                              "cuts": self.data["cuts"], "next_group": self.data["next_group"]})

    def restore(self, snap: dict) -> None:
        snap = copy.deepcopy(snap)
        for k in ("groups", "group_order", "cuts", "next_group"):
            self.data[k] = snap[k]
        self.touch()
