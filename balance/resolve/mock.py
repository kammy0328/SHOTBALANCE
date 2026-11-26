"""Resolve 스크립팅 API 를 흉내 내는 가짜 객체 (데모 모드, 자동 테스트).

실제 API 와 같은 메서드 이름 · 반환 형태를 쓰므로, 앱은 진짜 Resolve 와 같은 코드 경로로 동작한다.
SetCDL 로 받은 값은 calls 에 쌓아 두어 테스트에서 확인할 수 있다.
"""
from __future__ import annotations

from ..color import spaces
from ..media.demo_shots import DEMO, EDIT_ORDER
from ..media.synthetic import Shot

SCHEME = "synthetic://"
_SHOTS: dict[str, Shot] = {shot.name: shot for shot, *_ in DEMO}

_RESOLVE_NAMES = {
    "slog3_sgamut3cine": "S-Gamut3.Cine/S-Log3", "slog3_sgamut3": "S-Gamut3/S-Log3",
    "logc3_awg3": "ARRI LogC3", "logc4_awg4": "ARRI LogC4", "rec709_g24": "Rec.709 Gamma 2.4",
}


def shot_for(path: str) -> Shot | None:
    return _SHOTS.get(path[len(SCHEME):]) if path.startswith(SCHEME) else None


class MockGraph:
    def __init__(self, labels):
        self.labels = labels

    def GetNumNodes(self):
        return len(self.labels)

    def GetNodeLabel(self, i):
        return self.labels[i - 1] if 1 <= i <= len(self.labels) else ""


class MockColorGroup:
    def __init__(self, name):
        self._name = name

    def GetName(self):
        return self._name


class MockMediaPoolItem:
    def __init__(self, shot: Shot):
        self.shot = shot

    def GetName(self):
        return self.shot.name

    def GetUniqueId(self):
        return f"mpi-{self.shot.name}"

    def GetClipProperty(self, name=None):
        tag = self.shot.tagged or self.shot.space
        props = {"Type": "Video", "File Path": SCHEME + self.shot.name, "File Name": self.shot.name + ".mov",
                 "FPS": f"{self.shot.fps:.3f}", "Frames": str(self.shot.n_frames), "Data Level": "Auto",
                 "Input Color Space": _RESOLVE_NAMES.get(tag, spaces.SPACES[tag].label), "Proxy Media Path": ""}
        return props if name is None else props.get(name, "")


class MockTimelineItem:
    def __init__(self, uid, shot, start, dur, src_in, group, labels, track=1):
        self.uid, self.shot, self.start, self.dur, self.src_in = uid, shot, start, dur, src_in
        self.group, self.graph, self.track = group, MockGraph(labels), track
        self.mpi = MockMediaPoolItem(shot)
        self.cdl: dict | None = None
        self.calls: list[dict] = []

    def GetName(self):
        return self.shot.name

    def GetUniqueId(self):
        return self.uid

    def GetStart(self, *_):
        return self.start

    def GetEnd(self, *_):
        return self.start + self.dur

    def GetDuration(self, *_):
        return self.dur

    def GetLeftOffset(self, *_):
        return self.src_in

    def GetSourceStartFrame(self):
        return self.src_in

    def GetSourceEndFrame(self):
        return self.src_in + self.dur

    def GetMediaPoolItem(self):
        return self.mpi

    def GetClipEnabled(self):
        return True

    def GetClipColor(self):
        return ""

    def GetColorGroup(self):
        return self.group

    def GetNodeGraph(self, *args):
        if args and args[0] == 0:
            return False                 # 실제 API 도 0 을 주면 False 를 돌려준다
        return self.graph

    def SetCDL(self, cdl):
        idx = int(cdl.get("NodeIndex", 0))
        if not 1 <= idx <= self.graph.GetNumNodes():
            return False
        self.cdl = dict(cdl)
        self.calls.append(dict(cdl))
        return True


class MockTimeline:
    def __init__(self, name, items, fps=24.0, start_frame=86400):
        self.name, self.items, self.fps, self.start_frame = name, items, fps, start_frame
        self.playhead = None

    def GetName(self):
        return self.name

    def GetUniqueId(self):
        return f"tl-{self.name}"

    def GetSetting(self, key=None):
        return {"timelineFrameRate": str(self.fps), "timelineDropFrameTimecode": "0"}.get(key, "")

    def GetStartFrame(self):
        return self.start_frame

    def GetTrackCount(self, kind):
        return 1 if kind == "video" else 0

    def GetIsTrackEnabled(self, kind, idx):
        return True

    def GetItemListInTrack(self, kind, idx):
        return list(self.items) if kind == "video" and idx == 1 else []

    def SetCurrentTimecode(self, tc):
        self.playhead = tc
        return True


class MockProject:
    def __init__(self, name, timeline, settings):
        self.name, self.timeline, self.settings = name, timeline, settings

    def GetName(self):
        return self.name

    def GetUniqueId(self):
        return f"prj-{self.name}"

    def GetCurrentTimeline(self):
        return self.timeline

    def GetSetting(self, key=None):
        return self.settings.get(key, "")


class _PM:
    def __init__(self, project):
        self.project = project

    def GetCurrentProject(self):
        return self.project


class MockResolve:
    def __init__(self, project):
        self.project = project

    def GetProductName(self):
        return "DaVinci Resolve Studio (데모)"

    def GetVersion(self):
        return [20, 2, 0, 0, ""]

    def GetVersionString(self):
        return "20.2"

    def GetProjectManager(self):
        return _PM(self.project)


def demo_resolve() -> MockResolve:
    """28컷짜리 데모 타임라인. 한 컷은 일부러 EXPOSURE 노드 없이 둔다."""
    groups: dict[str, MockColorGroup] = {}
    items = []
    pos = 86400
    for n, k in enumerate(EDIT_ORDER):
        shot, gname, _isref, dur = DEMO[k]
        grp = groups.setdefault(gname, MockColorGroup(gname)) if gname else None
        labels = ["EXPOSURE", "BALANCE", "LOOK"] if shot.name != "A007C006_241105_R1AB" else ["BALANCE", "LOOK"]
        items.append(MockTimelineItem(f"ti-{n + 1:03d}", shot, pos, dur, 10, grp, labels))
        pos += dur
    tl = MockTimeline("MV_Edit_v07_color", items)
    prj = MockProject("데모 프로젝트", tl, {
        "colorScienceMode": "davinciYRGBColorManagedv2", "colorSpaceTimeline": "DaVinci WG/Intermediate",
        "timelineFrameRate": "24"})
    return MockResolve(prj)


def demo_refs() -> dict[str, str]:
    """데모에서 미리 지정해 두는 그룹별 레퍼런스 (그룹 이름 → 클립 이름)."""
    return {g: shot.name for shot, g, isref, _ in DEMO if isref and g}
