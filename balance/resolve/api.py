"""DaVinci Resolve 스크립팅 API 연결.

Resolve 가 제공하는 것:  타임라인/클립 정보 읽기, 노드 라벨 읽기, SetCDL 로 노드에 값 쓰기.
제공하지 않는 것:        노드 추가 · 삭제, 프라이머리 값 읽기(GetCDL 없음), 노드 Gamma 설정.
그래서 EXPOSURE 노드는 사용자가 노드 트리에 미리 만들어 두고, 앱은 라벨로 찾아 값만 쓴다.
모든 호출은 한 스레드(Resolve 워커)에서만 한다.
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import os
import sys

from ..store import cut_key


class ResolveError(Exception):
    """사용자에게 그대로 보여줄 수 있는 연결/조작 실패 사유."""


_module = None


def _lib_candidates():
    env = os.environ.get("RESOLVE_SCRIPT_LIB")
    if env:
        yield env
    if sys.platform == "win32":
        for base in {os.environ.get("PROGRAMFILES", r"C:\Program Files"), r"C:\Program Files"}:
            yield os.path.join(base, "Blackmagic Design", "DaVinci Resolve", "fusionscript.dll")
    elif sys.platform == "darwin":
        for app in ("DaVinci Resolve/DaVinci Resolve.app", "DaVinci Resolve Studio.app"):
            yield f"/Applications/{app}/Contents/Libraries/Fusion/fusionscript.so"
    else:
        yield "/opt/resolve/libs/Fusion/fusionscript.so"
        yield "/home/resolve/libs/Fusion/fusionscript.so"


def load_resolve():
    """실행 중인 Resolve 에 붙는다. DaVinciResolveScript.py 를 거치지 않고 fusionscript 를
    직접 올린다 (구버전 모듈이 쓰는 imp 가 Python 3.12 에서 사라졌기 때문)."""
    global _module
    if _module is None:
        lib = next((p for p in _lib_candidates() if p and os.path.isfile(p)), None)
        if lib is None:
            raise ResolveError("DaVinci Resolve 설치 경로를 찾지 못했습니다.")
        if sys.platform == "win32" and hasattr(os, "add_dll_directory"):
            try:
                os.add_dll_directory(os.path.dirname(lib))
            except OSError:
                pass
        try:
            loader = importlib.machinery.ExtensionFileLoader("fusionscript", lib)
            spec = importlib.util.spec_from_loader("fusionscript", loader)
            mod = importlib.util.module_from_spec(spec)
            loader.exec_module(mod)
        except Exception as e:  # noqa: BLE001
            raise ResolveError(
                f"Resolve 스크립팅 모듈을 불러오지 못했습니다 ({type(e).__name__}). "
                f"이 Python({sys.version_info.major}.{sys.version_info.minor}) 을 Resolve 가 지원하는지 확인하세요.") from e
        _module = mod
    resolve = _module.scriptapp("Resolve")
    if resolve is None:
        raise ResolveError(
            "Resolve 에 연결하지 못했습니다. Resolve Studio 가 실행 중인지, "
            "환경설정 > 시스템 > 일반 > '외부 스크립트 사용' 이 '로컬' 인지 확인하세요.")
    return resolve


def frames_to_tc(frame: int, fps: float, drop: bool = False) -> str:
    base = int(round(fps))
    frame = int(frame)
    if drop and base in (30, 60):
        d = 2 if base == 30 else 4
        per10 = base * 600 - d * 9
        tens, rem = divmod(frame, per10)
        if rem >= d:
            frame += d * 9 * tens + d * ((rem - d) // (base * 60 - d))
        else:
            frame += d * 9 * tens
    ff = frame % base
    s = frame // base
    sep = ";" if drop and base in (30, 60) else ":"
    return f"{s // 3600:02d}:{(s // 60) % 60:02d}:{s % 60:02d}{sep}{ff:02d}"


def _call(obj, name, *args, default=None):
    fn = getattr(obj, name, None)
    if fn is None:
        return default
    try:
        r = fn(*args)
    except Exception:  # noqa: BLE001
        return default
    return default if r is None else r


_SKIP_TYPES = ("timeline", "compound", "generator", "fusion", "subtitle", "geometry", "multicam")


class ResolveSession:
    def __init__(self, resolve):
        self.resolve = resolve
        self._items: dict[str, object] = {}
        self.match_label = "MATCH"
        self._timeline = None
        self._fps = 24.0
        self._drop = False

    # ------------------------------------------------------------------ 정보
    def _project(self):
        pm = self.resolve.GetProjectManager()
        project = pm.GetCurrentProject() if pm else None
        if not project:
            raise ResolveError("열려 있는 프로젝트가 없습니다.")
        return project

    def describe(self) -> dict:
        project = self._project()
        tl = project.GetCurrentTimeline()
        if not tl:
            raise ResolveError("열려 있는 타임라인이 없습니다.")
        science = str(_call(project, "GetSetting", "colorScienceMode", default="") or "")
        tspace = str(_call(project, "GetSetting", "colorSpaceTimeline", default="") or "")
        fps = _call(tl, "GetSetting", "timelineFrameRate", default=None) or _call(project, "GetSetting", "timelineFrameRate", default="24")
        try:
            fps = float(fps)
        except (TypeError, ValueError):
            fps = 24.0
        ver = _call(self.resolve, "GetVersion", default=None) or []
        return dict(
            product=str(_call(self.resolve, "GetProductName", default="DaVinci Resolve")),
            resolve_version=str(_call(self.resolve, "GetVersionString", default="")),
            major=int(ver[0]) if ver else 0,
            project=str(project.GetName()), project_id=str(_call(project, "GetUniqueId", default=project.GetName())),
            timeline=str(tl.GetName()), timeline_id=str(_call(tl, "GetUniqueId", default=tl.GetName())),
            fps=fps, science=science, timeline_space=tspace,
            input_space=str(_call(project, "GetSetting", "colorSpaceInput", default="") or ""),
            rcm="managed" in science.lower(), aces="aces" in science.lower(),
            dwg=("davinci" in tspace.lower() and "wg" in tspace.lower().replace("wide gamut", "wg")) or not tspace)

    # ------------------------------------------------------------------ 스캔
    def _node_labels(self, item) -> list[str]:
        graph = _call(item, "GetNodeGraph", default=None)
        if graph:
            n = int(_call(graph, "GetNumNodes", default=0) or 0)
            return [str(_call(graph, "GetNodeLabel", i, default="") or "") for i in range(1, n + 1)]
        n = int(_call(item, "GetNumNodes", default=0) or 0)
        return [str(_call(item, "GetNodeLabel", i, default="") or "") for i in range(1, n + 1)]

    def scan(self, node_label: str) -> list[dict]:
        """현재 타임라인의 컷 목록. 이후의 write_cdl · goto 는 이 스캔 결과를 대상으로 한다."""
        project = self._project()
        tl = project.GetCurrentTimeline()
        if not tl:
            raise ResolveError("열려 있는 타임라인이 없습니다.")
        d = self.describe()
        self._timeline, self._fps = tl, d["fps"]
        self._drop = str(_call(tl, "GetSetting", "timelineDropFrameTimecode", default="0")) == "1"
        found, self._items = self._scan(tl, node_label, d["fps"])
        return found

    def _scan(self, tl, node_label: str, fps: float) -> tuple[list[dict], dict]:
        d = {"fps": fps}
        want = node_label.strip().lower()
        want_match = self.match_label.strip().lower()
        items: dict[str, object] = {}
        found = []
        for t in range(1, int(_call(tl, "GetTrackCount", "video", default=0) or 0) + 1):
            if _call(tl, "GetIsTrackEnabled", "video", t, default=True) is False:
                continue
            for item in (_call(tl, "GetItemListInTrack", "video", t, default=[]) or []):
                if _call(item, "GetClipEnabled", default=True) is False:
                    continue
                mpi = _call(item, "GetMediaPoolItem", default=None)
                if not mpi:
                    continue
                props = _call(mpi, "GetClipProperty", default={}) or {}
                typ = str(props.get("Type", "")).lower()
                if any(k in typ for k in _SKIP_TYPES) or (typ and "video" not in typ and "still" not in typ):
                    continue
                start = int(_call(item, "GetStart", default=0))
                dur = int(_call(item, "GetDuration", default=0) or (int(_call(item, "GetEnd", default=start)) - start))
                if dur <= 0:
                    continue
                left = int(_call(item, "GetLeftOffset", default=0) or 0)
                ss = _call(item, "GetSourceStartFrame", default=None)
                se = _call(item, "GetSourceEndFrame", default=None)
                retimed = False
                if isinstance(ss, (int, float)) and isinstance(se, (int, float)) and ss != se:
                    src_in, src_out = int(min(ss, se)), int(max(ss, se))
                    retimed = abs((src_out - src_in) - dur) > 1
                else:           # 0/0 이면 알 수 없음 → 편집 위치 기준 오프셋 사용
                    src_in, src_out = left, left + dur
                path = str(props.get("File Path", "") or "")
                proxy = str(props.get("Proxy Media Path", "") or "")
                name = str(props.get("File Name", "") or os.path.basename(path) or _call(item, "GetName", default="clip"))
                uid = str(_call(item, "GetUniqueId", default="") or f"v{t}:{start}")
                labels = self._node_labels(item)
                node = next((i + 1 for i, lb in enumerate(labels) if lb.strip().lower() == want), None)
                match = next((i + 1 for i, lb in enumerate(labels) if lb.strip().lower() == want_match), None)
                group = _call(item, "GetColorGroup", default=None)
                try:
                    cfps = float(str(props.get("FPS", "")).split()[0])
                except (ValueError, IndexError):
                    cfps = d["fps"]
                items[uid] = item
                found.append(dict(
                    id=uid, name=str(_call(item, "GetName", default=name)), track=t, start=start, dur=dur,
                    src_path=path, proxy_path=proxy, src_name=name, src_in=src_in, src_out=src_out, fps=cfps,
                    key=cut_key(name, src_in, src_out), retimed=retimed,
                    resolve_space=str(props.get("Input Color Space", "") or "") or None,
                    data_level=str(props.get("Data Level", "") or "") or None,
                    clip_color=str(_call(item, "GetClipColor", default="") or "") or None,
                    resolve_group=str(_call(group, "GetName", default="")) or None if group else None,
                    node_index=node, match_index=match, node_count=len(labels)))
        found.sort(key=lambda c: (c["start"], c["track"]))
        for i, c in enumerate(found, 1):
            c["index"] = i
        return found, items

    # ------------------------------------------------------------ 다른 타임라인
    def timelines(self) -> list[dict]:
        """프로젝트의 타임라인 목록 (이름만 읽으므로 빠르다)."""
        project = self._project()
        cur = project.GetCurrentTimeline()
        cur_id = str(_call(cur, "GetUniqueId", default="")) if cur else ""
        out = []
        for i in range(1, int(_call(project, "GetTimelineCount", default=0) or 0) + 1):
            tl = _call(project, "GetTimelineByIndex", i, default=None)
            if not tl:
                continue
            tid = str(_call(tl, "GetUniqueId", default="") or tl.GetName())
            out.append(dict(id=tid, name=str(tl.GetName()), current=tid == cur_id))
        return out

    def open_timeline(self, timeline_id: str, node_label: str) -> tuple[list[dict], dict] | None:
        """다른 타임라인을 현재 타임라인으로 바꾸고 컷을 읽는다 (스캔 상태는 건드리지 않는다).
        끝나면 restore_timeline 으로 원래 타임라인에 돌아와야 한다."""
        project = self._project()
        for i in range(1, int(_call(project, "GetTimelineCount", default=0) or 0) + 1):
            tl = _call(project, "GetTimelineByIndex", i, default=None)
            if tl and str(_call(tl, "GetUniqueId", default="") or tl.GetName()) == timeline_id:
                if not _call(project, "SetCurrentTimeline", tl, default=False):
                    return None
                fps = _call(tl, "GetSetting", "timelineFrameRate", default=None) or self._fps
                try:
                    fps = float(fps)
                except (TypeError, ValueError):
                    fps = self._fps
                return self._scan(tl, node_label, fps)
        return None

    def restore_timeline(self) -> bool:
        if self._timeline is None:
            return False
        return bool(_call(self._project(), "SetCurrentTimeline", self._timeline, default=False))

    # ------------------------------------------------------------------ 쓰기
    def write_cdl(self, cut_id: str, node_label: str, slope, offset, item=None) -> bool:
        """라벨이 node_label 인 노드에 CDL 을 쓴다. 노드 순서가 스캔 이후 바뀌었을 수 있으므로
        쓰기 직전에 라벨로 다시 찾는다 (엉뚱한 노드의 프라이머리를 덮어쓰지 않기 위해).
        item 을 주면 현재 스캔에 없는 클립(다른 타임라인)에 쓴다."""
        item = item if item is not None else self._items.get(cut_id)
        if item is None:
            return False
        want = node_label.strip().lower()
        index = next((i + 1 for i, lb in enumerate(self._node_labels(item)) if lb.strip().lower() == want), None)
        if index is None:
            return False
        cdl = {"NodeIndex": str(index),
               "Slope": " ".join(f"{float(v):.6f}" for v in slope),
               "Offset": " ".join(f"{float(v):.6f}" for v in offset),
               "Power": "1.0 1.0 1.0", "Saturation": "1.0"}
        return bool(_call(item, "SetCDL", cdl, default=False))

    def write_lut(self, cut_id: str, node_label: str, rel_path: str, item=None) -> bool:
        """라벨이 node_label 인 노드에 LUT 를 건다. rel_path 는 Resolve LUT 폴더 기준 경로, 빈 문자열이면 LUT 를 뗀다.
        SetLUT 의 반환값만으로는 실제로 걸렸는지 알 수 없어 GetLUT 으로 다시 읽어 확인한다."""
        item = item if item is not None else self._items.get(cut_id)
        if item is None:
            return False
        want = node_label.strip().lower()
        index = next((i + 1 for i, lb in enumerate(self._node_labels(item)) if lb.strip().lower() == want), None)
        if index is None:
            return False
        norm = lambda v: v.replace("\\", "/").strip("/").lower()          # noqa: E731
        graph = _call(item, "GetNodeGraph", default=None)
        ok = False
        # 버전에 따라 SetLUT 이 Graph 에 있거나 TimelineItem 에 있다. 경로 구분자도 두 가지를 다 시도한다.
        for target in (graph, item):
            if not target:
                continue
            for path in dict.fromkeys((rel_path, rel_path.replace("/", "\\"))):
                ok = bool(_call(target, "SetLUT", index, path, default=False))
                got = _call(target, "GetLUT", index, default=None)
                if isinstance(got, str):
                    if norm(got) == norm(rel_path) or (bool(rel_path) and norm(got).endswith(norm(rel_path))):
                        return True
                elif ok:
                    return True
        return False

    def refresh_luts(self) -> bool:
        """새로 만든 LUT 파일을 Resolve 가 찾도록 목록을 다시 읽게 한다."""
        return bool(_call(self._project(), "RefreshLUTList", default=False))

    def goto(self, cut_id: str, at_start: bool = False) -> bool:
        item = self._items.get(cut_id)
        if item is None or self._timeline is None:
            return False
        start = int(_call(item, "GetStart", default=0))
        dur = int(_call(item, "GetDuration", default=1) or 1)
        tc = frames_to_tc(start if at_start else start + dur // 2, self._fps, self._drop)
        return bool(_call(self._timeline, "SetCurrentTimecode", tc, default=False))
