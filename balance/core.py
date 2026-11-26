"""앱의 상태와 동작을 한곳에서 관리한다 (UI · 서버와 분리된 엔진)."""
from __future__ import annotations

import hashlib
import os
import queue
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable

import numpy as np

from . import APP_NAME, __version__, config
from .analysis import encoding
from .analysis.clip import ANALYSIS_VERSION, Cancelled, analyze_clip
from .analysis.faces import MODEL_URLS, SFACE, YUNET, FaceEngine
from .analysis.match import ShotIn, match_group
from .color import curves, preview, spaces
from .color.gamuts import EXPOSURE_W
from .media.decode import RAW_EXT, DecodeError, VideoSource
from .media.synthetic import SyntheticSource
from .resolve import mock
from .resolve.api import ResolveError, ResolveSession, load_resolve
from .store import ProjectStore, new_cut, new_intent

UNDO_DEPTH = 60


class Worker(threading.Thread):
    """작업을 순서대로 처리하는 단일 스레드. Resolve 호출은 전부 여기서 한다."""

    def __init__(self, name: str):
        super().__init__(name=name, daemon=True)
        self.q: queue.Queue = queue.Queue()
        self.start()

    def submit(self, fn: Callable, *args) -> None:
        self.q.put((fn, args))

    def run(self) -> None:
        while True:
            fn, args = self.q.get()
            if fn is None:
                return
            try:
                fn(*args)
            except Exception as e:  # noqa: BLE001
                print(f"[{self.name}] {type(e).__name__}: {e}")

    def stop(self) -> None:
        self.q.put((None, ()))


def wb_from_intent(temp: float, tint: float) -> np.ndarray:
    """의도 버튼(따뜻하게/차갑게, 그린/마젠타) → 중성 회색의 노출을 보존하는 채널 게인."""
    k = config.WB_STEP
    wb = np.array([2.0 ** (k * temp + 0.5 * k * tint), 2.0 ** (-k * tint), 2.0 ** (-k * temp + 0.5 * k * tint)])
    return wb / float(wb @ EXPOSURE_W.astype(np.float64))


def cdl_values(total: dict, mode: str) -> tuple[tuple, tuple]:
    """(Slope, Offset). linear_gain 은 Gamma=Linear 노드의 Gain 과 Offset, di_offset 은 DI 노드의 Offset."""
    if mode == "di_offset":
        gain = (2.0 ** total["exp"]) * np.asarray(total["wb"], np.float64)
        off = curves.DI_C * np.log2(np.maximum(gain, 1e-6))
        return (1.0, 1.0, 1.0), tuple(round(float(v), 6) for v in off)
    return tuple(round(float(v), 6) for v in total["slope"]), tuple(round(float(v), 6) for v in total["offset"])


class Engine:
    def __init__(self, notify: Callable[[dict], None] | None = None, data_root: Path | None = None,
                 workers: int | None = None):
        self._notify = notify or (lambda e: None)
        self.lock = threading.RLock()
        self.root = Path(data_root) if data_root else config.data_dir()
        self.cache = (Path(data_root) / "cache") if data_root else config.cache_dir()
        (self.cache / "thumbs").mkdir(parents=True, exist_ok=True)
        self.session: ResolveSession | None = None
        self.store: ProjectStore | None = None
        self.cuts: list[dict] = []
        self.by_key: dict[str, list[dict]] = {}
        self.conn: dict = {"status": "disconnected", "message": "", "demo": False}
        self.job: dict | None = None
        self.version = 0
        self.undo_stack: list[dict] = []
        self.redo_stack: list[dict] = []
        self.quick: dict[str, str] = {}
        self.failed_writes: set[str] = set()
        self.timelines: list[dict] = []
        self.faces = FaceEngine(config.model_dirs())
        self.rworker = Worker("resolve")
        self.n_workers = workers or int(np.clip((os.cpu_count() or 4) // 2, 1, 4))
        self._flush_timer: threading.Timer | None = None
        self._apply_pending = False

    # ============================================================ 이벤트/저장
    def _bump(self, save: bool = True) -> None:
        with self.lock:
            self.version += 1
            v = self.version
            if save and self.store is not None:
                self.store.touch()
                if self._flush_timer is not None:
                    self._flush_timer.cancel()
                self._flush_timer = threading.Timer(0.6, self._flush)
                self._flush_timer.daemon = True
                self._flush_timer.start()
        self._notify({"type": "state", "version": v})

    def _flush(self) -> None:
        with self.lock:
            if self.store is not None:
                self.store.flush()

    def _toast(self, text: str, level: str = "info") -> None:
        self._notify({"type": "toast", "level": level, "text": text})

    def _push_undo(self) -> None:
        if self.store is None:
            return
        self.undo_stack.append(self.store.snapshot())
        del self.undo_stack[:-UNDO_DEPTH]
        self.redo_stack.clear()

    # ================================================================ 연결
    def connect(self, demo: bool = False) -> None:
        with self.lock:
            self.conn = {"status": "connecting", "message": "", "demo": demo}
        self._bump(save=False)
        self.rworker.submit(self._connect, demo)

    def _connect(self, demo: bool) -> None:
        try:
            resolve = mock.demo_resolve() if demo else load_resolve()
            session = ResolveSession(resolve)
            info = session.describe()
            store = ProjectStore((self.root / "demo") if demo else (self.root / "projects"), info["project"], info["project_id"])
            cuts = session.scan(store.settings["node_label"])
            timelines = session.timelines()
        except ResolveError as e:
            with self.lock:
                self.conn = {"status": "error", "message": str(e), "demo": demo}
            self._bump(save=False)
            return
        except Exception as e:  # noqa: BLE001
            with self.lock:
                self.conn = {"status": "error", "message": f"연결 중 오류: {type(e).__name__}: {e}", "demo": demo}
            self._bump(save=False)
            return
        with self.lock:
            self._flush()
            self.session, self.store = session, store
            self.timelines = timelines
            self.conn = {"status": "demo" if demo else "connected", "message": "", "demo": demo, **info}
            self.undo_stack.clear()
            self.redo_stack.clear()
            self.quick.clear()
            self.failed_writes.clear()
            self._set_cuts(cuts)
            if demo and not store.groups:
                self._import_groups("resolve")
                refs = mock.demo_refs()
                for g in store.groups.values():
                    name = refs.get(g["name"])
                    g["ref"] = next((c["key"] for c in self.cuts if c["name"] == name), None)
                dark = next((c["key"] for c in self.cuts if c["name"].startswith("A003C022")), None)
                if dark:                                  # 의도된 실루엣 컷은 미리 제외해 둔다
                    store.cut(dark)["excluded"] = True
        self._bump()
        self._start_quick_thumbs()
        self._schedule_apply()

    def rescan(self) -> None:
        self.rworker.submit(self._rescan)

    def _rescan(self) -> None:
        if self.session is None or self.store is None:
            return
        try:
            info = self.session.describe()
            if info["project_id"] != self.conn.get("project_id"):
                self._connect(bool(self.conn.get("demo")))      # 다른 프로젝트가 열렸다: 처음부터 다시
                return
            cuts = self.session.scan(self.store.settings["node_label"])
            timelines = self.session.timelines()
        except ResolveError as e:
            self._toast(str(e), "error")
            return
        with self.lock:
            self.conn.update(info)
            self.timelines = timelines
            self._set_cuts(cuts)
        self._bump()
        self._start_quick_thumbs()
        self._schedule_apply()

    def _set_cuts(self, cuts: list[dict]) -> None:
        for c in cuts:
            path = c["src_path"]
            if path.startswith(mock.SCHEME):
                c["media_ok"], c["media_note"] = True, None
                continue
            ext = os.path.splitext(path)[1].lower()
            if "[" in os.path.basename(path) or "%" in os.path.basename(path):
                c["media_ok"], c["media_note"] = False, "이미지 시퀀스는 아직 직접 읽지 못합니다"
            elif os.path.isfile(path) and ext not in RAW_EXT:
                c["media_ok"], c["media_note"] = True, None
            elif c.get("proxy_path") and os.path.isfile(c["proxy_path"]):
                c["media_ok"], c["media_note"] = True, "프록시로 분석"
                c["src_path"] = c["proxy_path"]
            elif ext in RAW_EXT:
                c["media_ok"], c["media_note"] = False, f"{RAW_EXT[ext]}: 프록시를 연결하면 분석됩니다"
            else:
                c["media_ok"], c["media_note"] = False, "원본 파일을 찾을 수 없습니다"
        self.cuts = cuts
        self.by_key = {}
        for c in cuts:
            self.by_key.setdefault(c["key"], []).append(c)
            self.store.cut(c["key"])
        # 그룹의 레퍼런스가 이 타임라인에 없으면 그대로 두되, 매칭 때는 중앙값으로 처리된다

    # ============================================================ 색공간
    def _space_for(self, cut: dict, guess: dict | None = None) -> tuple[str, str]:
        st = self.store.settings
        user = self.store.cuts.get(cut["key"], {}).get("space")
        if user in spaces.SPACES:
            return user, "user"
        sid = spaces.from_resolve_name(cut.get("resolve_space"))
        if sid is None and cut.get("resolve_space") and self.conn.get("rcm"):
            sid = spaces.from_resolve_name(self.conn.get("input_space"))    # '프로젝트 설정 따름' 인 클립
        if sid:
            return sid, "resolve"
        if guess and guess.get("space") and guess.get("confidence", 0) >= 0.75:
            return guess["space"], "guess"
        return st["default_log"], "default"

    def _analysis(self, key: str) -> dict | None:
        e = self.store.analysis.get(key)
        return e.get("a") if e else None

    def _is_stale(self, key: str) -> bool:
        a = self._analysis(key)
        if a is None:
            return False
        cut = self.by_key.get(key, [None])[0]
        if cut is None:
            return False
        return a.get("version") != ANALYSIS_VERSION or a.get("space") != self._space_for(cut, a.get("guess"))[0]

    # ============================================================ 소스
    def _open(self, cut: dict):
        shot = mock.shot_for(cut["src_path"])
        if shot is not None:
            return SyntheticSource(shot)
        return VideoSource(cut["src_path"], cut.get("data_level"))

    def _thumb_name(self, key: str, kind: str = "") -> str:
        """kind: "" 대표 프레임, "q" 분석 전 임시, "h" 컷의 첫 프레임, "t" 컷의 끝 프레임."""
        return hashlib.sha1(key.encode("utf-8")).hexdigest()[:18] + (f"_{kind}" if kind else "") + ".jpg"

    def _start_quick_thumbs(self) -> None:
        with self.lock:
            keys = [k for k, cs in self.by_key.items()
                    if cs[0]["media_ok"] and k not in self.quick and not (self.store.analysis.get(k, {}).get("thumb"))]
        if keys:
            threading.Thread(target=self._quick_thumbs, args=(keys,), daemon=True, name="thumbs").start()

    def _quick_thumbs(self, keys: list[str]) -> None:
        def one(key):
            cut = self.by_key.get(key, [None])[0]
            if cut is None:
                return
            try:
                with self._open(cut) as src:
                    _, code = src.read_fast((cut["src_in"] + cut["src_out"]) // 2, cut["src_in"], 480)
                lin = spaces.to_dwg_linear(code, self._space_for(cut)[0])
                name = self._thumb_name(key, "q")
                (self.cache / "thumbs" / name).write_bytes(preview.thumb_jpeg(lin))
                with self.lock:
                    self.quick[key] = name
            except Exception:  # noqa: BLE001
                return
        last = time.time()
        with ThreadPoolExecutor(max_workers=min(2, self.n_workers), thread_name_prefix="thumb") as ex:
            for _ in ex.map(one, keys):
                if time.time() - last > 0.25:
                    last = time.time()
                    self._bump(save=False)
        self._bump(save=False)

    # ============================================================== 합계
    def _use_flags(self, c: dict) -> tuple[bool, bool, bool]:
        auto, st = c.get("auto"), self.store.settings
        if not auto or c.get("excluded") or auto.get("method") in ("none", "excluded"):
            return False, False, False
        use_exp = c["use_exp"] if c.get("use_exp") is not None else (auto["exp_apply"] or st["apply_low"])
        has_wb = auto.get("wb_method") not in ("none", None)
        use_wb = c["use_wb"] if c.get("use_wb") is not None else (auto["wb_apply"] or (st["apply_low"] and has_wb))
        has_black = auto.get("black_method") in ("ref", "floor")
        use_black = c["use_black"] if c.get("use_black") is not None else (auto.get("black_apply") or st["apply_low"])
        # 블랙은 노출을 맞춘 상태를 전제로 한 값이고, 선형 Offset 으로만 정확하게 쓸 수 있다
        use_black = bool(use_black and has_black and use_exp and st["black_enabled"] and st["apply_mode"] == "linear_gain")
        return bool(use_exp), bool(use_wb and st["wb_enabled"] and has_wb), use_black

    def total(self, key: str) -> dict:
        """컷에 최종으로 들어가는 값.  L' = slope · L + offset  (선형).
        slope = 의도 게인 · 자동 게인,  offset = 의도 게인 · 블랙 오프셋."""
        c = self.store.cuts.get(key) or new_cut()
        if c.get("locked") and c.get("frozen"):
            f = c["frozen"]
            slope = f.get("slope") or [round(float(v) * 2.0 ** f["exp"], 6) for v in f["wb"]]
            return dict(exp=f["exp"], wb=list(f["wb"]), slope=list(slope), offset=list(f.get("offset") or [0.0, 0.0, 0.0]),
                        black=f.get("black", 0.0), auto_exp=0.0, group_exp=0.0, cut_exp=0.0,
                        use_exp=False, use_wb=False, use_black=False, locked=True)
        g = self.store.groups.get(c.get("group") or "")
        gi = g["intent"] if g else new_intent()
        ci = c["intent"]
        use_exp, use_wb, use_black = self._use_flags(c)
        auto = c.get("auto") or {}
        a_exp = float(auto.get("exp", 0.0)) if use_exp else 0.0
        a_wb = np.asarray(auto.get("wb", [1, 1, 1]), np.float64) if use_wb else np.ones(3)
        wb = a_wb * wb_from_intent(gi["temp"] + ci["temp"], gi["tint"] + ci["tint"])
        exp = a_exp + gi["exp"] + ci["exp"]
        slope = (2.0 ** exp) * wb
        offset = np.zeros(3)
        if use_black:
            slope = slope * np.asarray(auto.get("black_k", [1, 1, 1]), np.float64)
            offset = (2.0 ** (gi["exp"] + ci["exp"])) * np.asarray(auto.get("black_o", [0, 0, 0]), np.float64)
        return dict(exp=round(exp, 4), wb=[round(float(v), 5) for v in wb],
                    slope=[round(float(v), 6) for v in slope], offset=[round(float(v), 6) for v in offset],
                    black=round(float(auto.get("black", 0.0)), 5) if use_black else 0.0,
                    auto_exp=round(a_exp, 4), group_exp=round(gi["exp"], 4), cut_exp=round(ci["exp"], 4),
                    use_exp=use_exp, use_wb=use_wb, use_black=use_black, locked=False)

    def _cdl(self, key: str) -> tuple:
        return cdl_values(self.total(key), self.store.settings["apply_mode"])

    # ====================================================== Resolve 에 쓰기
    def _written(self) -> dict:
        w = self.store.data.setdefault("written", {})
        return w.setdefault(self.conn.get("timeline_id", "?"), {})

    def _pending(self, cut: dict) -> bool:
        if cut.get("node_index") is None:
            return False
        want = [list(v) for v in self._cdl(cut["key"])]
        have = self._written().get(cut["id"])
        if have is None:
            return want != [[1.0, 1.0, 1.0], [0.0, 0.0, 0.0]]
        return have != want

    def _schedule_apply(self, force: bool = False) -> None:
        with self.lock:
            if self.store is None or self.session is None:
                return
            if not (self.store.settings["auto_apply"] or force):
                return
            if self._apply_pending and not force:
                return
            self._apply_pending = True
        self.rworker.submit(self._apply, force)

    def apply(self, force: bool = False) -> None:
        """Resolve 에 값 쓰기. force 면 이미 쓴 값도 전부 다시 쓴다."""
        with self.lock:
            self._apply_pending = True
        self.rworker.submit(self._apply, True, force)

    def _apply(self, manual: bool = False, rewrite: bool = False) -> None:
        with self.lock:
            self._apply_pending = False
            if self.store is None or self.session is None:
                return
            todo = []
            for cut in self.cuts:
                if cut.get("node_index") is None:
                    continue
                if rewrite or self._pending(cut):
                    todo.append((cut, self._cdl(cut["key"])))
        ok = fail = 0
        for cut, (slope, offset) in todo:
            done = self.session.write_cdl(cut["id"], self.store.settings["node_label"], slope, offset)
            with self.lock:
                if done:
                    self._written()[cut["id"]] = [list(slope), list(offset)]
                    self.failed_writes.discard(cut["id"])
                    ok += 1
                else:
                    self.failed_writes.add(cut["id"])
                    fail += 1
        if todo:
            self._bump()
        if fail:
            self._toast(f"{fail}컷은 Resolve 가 값을 받지 않았습니다. 노드 구성을 확인하세요.", "error")
        elif manual and ok:
            self._toast(f"{ok}컷에 적용했습니다")
        elif manual and not todo:
            self._toast("이미 모두 적용돼 있습니다")

    def goto(self, cut_id: str, at_start: bool = False) -> None:
        """재생헤드를 컷 가운데로, at_start 면 컷이 시작하는 프레임(경계)으로."""
        if self.session is not None and self.store is not None and self.store.settings["follow_selection"]:
            self.rworker.submit(self.session.goto, cut_id, at_start)

    # ======================================================== 다른 타임라인
    def sync_timelines(self, ids: list[str]) -> None:
        """같은 소스를 쓰는 다른 타임라인(티저, 다른 버전)에 지금 값을 그대로 쓴다.
        값은 새로 계산하지 않는다: 이 타임라인에서 정한 값을 소스 구간이 겹치는 컷에 복사한다."""
        with self.lock:
            ids = [t for t in ids if t != self.conn.get("timeline_id") and any(x["id"] == t for x in self.timelines)]
            if self.store is None or self.session is None or self.job is not None or not ids:
                return
            self.job = {"kind": "sync", "total": len(ids), "done": 0, "active": [], "cancel": False}
        self._bump(save=False)
        self.rworker.submit(self._sync_timelines, ids)

    def _sync_timelines(self, ids: list[str]) -> None:
        job, label = self.job, self.store.settings["node_label"]
        report: list[tuple[str, dict | None]] = []
        try:
            for tid in ids:
                if job["cancel"]:
                    break
                name = next((t["name"] for t in self.timelines if t["id"] == tid), tid)
                got = self.session.open_timeline(tid, label)
                if got is None:
                    report.append((name, None))
                    continue
                cuts, items = got
                n = dict(ok=0, miss=0, nonode=0, fail=0)
                with self.lock:
                    written = self.store.data.setdefault("written", {}).setdefault(tid, {})
                for cut in cuts:
                    with self.lock:
                        key = self.store.find(cut["key"])
                        cdl = self._cdl(key) if key is not None else None
                    if cdl is None:
                        n["miss"] += 1
                    elif cut["node_index"] is None:
                        n["nonode"] += 1
                    elif self.session.write_cdl(cut["id"], label, cdl[0], cdl[1], item=items[cut["id"]]):
                        written[cut["id"]] = [list(cdl[0]), list(cdl[1])]
                        n["ok"] += 1
                    else:
                        n["fail"] += 1
                report.append((name, n))
                with self.lock:
                    job["done"] += 1
                self._bump(save=False)
        finally:
            try:
                self.session.restore_timeline()
            except Exception:  # noqa: BLE001
                pass
            with self.lock:
                self.job = None
            self._bump()
        for name, n in report:
            if n is None:
                self._toast(f"{name}: 타임라인을 열지 못했습니다", "error")
                continue
            msg = f"{name}: {n['ok']}컷에 적용"
            if n["miss"]:
                msg += f", 같은 소스가 없는 컷 {n['miss']}개"
            if n["nonode"]:
                msg += f", 노드가 없는 컷 {n['nonode']}개"
            if n["fail"]:
                msg += f", Resolve 가 받지 않은 컷 {n['fail']}개"
            self._toast(msg, "warn" if (n["fail"] or n["nonode"]) else "ok")

    # ================================================================ 그룹
    def _order(self, key: str) -> int:
        cs = self.by_key.get(key)
        return cs[0]["index"] if cs else 10 ** 9

    def _group_keys(self, gid: str) -> list[str]:
        ks = [k for k, c in self.store.cuts.items() if c.get("group") == gid and k in self.by_key]
        return sorted(ks, key=self._order)

    def _new_group(self, name: str | None = None) -> str:
        d = self.store.data
        n = d["next_group"]
        d["next_group"] = n + 1
        gid = f"g{n}"
        self.store.groups[gid] = {"id": gid, "name": name or f"그룹 {n}",
                                  "color": config.GROUP_COLORS[(n - 1) % len(config.GROUP_COLORS)],
                                  "mode": "reference", "ref": None, "intent": new_intent()}
        d["group_order"].append(gid)
        return gid

    def create_group(self, keys: list[str], name: str | None = None) -> str | None:
        with self.lock:
            if self.store is None:
                return None
            self._push_undo()
            gid = self._new_group(name)
            touched = self._move(keys, gid)
            self._rematch(touched | {gid})
        self._bump()
        self._schedule_apply()
        return gid

    def _move(self, keys: list[str], gid: str | None) -> set[str]:
        touched: set[str] = set()
        for k in keys:
            if k not in self.by_key:
                continue
            c = self.store.cut(k)
            old = c.get("group")
            if old == gid:
                continue
            if old and old in self.store.groups:
                touched.add(old)
                if self.store.groups[old].get("ref") == k:
                    self.store.groups[old]["ref"] = None
            c["group"] = gid
            c["auto"] = None
            if gid:
                touched.add(gid)
        return touched

    def assign(self, keys: list[str], gid: str | None) -> None:
        with self.lock:
            if self.store is None or (gid and gid not in self.store.groups):
                return
            self._push_undo()
            self._rematch(self._move(keys, gid))
        self._bump()
        self._schedule_apply()

    def update_group(self, gid: str, name=None, mode=None, ref=..., color=None) -> None:
        with self.lock:
            g = self.store.groups.get(gid) if self.store else None
            if g is None:
                return
            self._push_undo()
            rematch = False
            if name is not None and str(name).strip():
                g["name"] = str(name).strip()[:40]
            if color is not None:
                g["color"] = str(color)
            if mode in ("reference", "median") and mode != g["mode"]:
                g["mode"], rematch = mode, True
            if ref is not ... and ref != g.get("ref"):
                if ref is None or self.store.cuts.get(ref, {}).get("group") == gid:
                    g["ref"], rematch = ref, True
                    if ref is not None:
                        g["mode"] = "reference"
            if rematch:
                self._rematch({gid})
        self._bump()
        self._schedule_apply()

    def delete_group(self, gid: str) -> None:
        with self.lock:
            if self.store is None or gid not in self.store.groups:
                return
            self._push_undo()
            for c in self.store.cuts.values():
                if c.get("group") == gid:
                    c["group"], c["auto"] = None, None
            del self.store.groups[gid]
            self.store.data["group_order"] = [g for g in self.store.data["group_order"] if g != gid]
        self._bump()
        self._schedule_apply()

    def import_groups(self, source: str = "resolve") -> None:
        with self.lock:
            if self.store is None:
                return
            self._push_undo()
            n = self._import_groups(source)
        self._bump()
        self._schedule_apply()
        self._toast(f"Resolve 에서 그룹 {n}개를 가져왔습니다" if n else "가져올 그룹이 없습니다")

    def _import_groups(self, source: str) -> int:
        field = "resolve_group" if source == "resolve" else "clip_color"
        names: dict[str, list[str]] = {}
        for c in self.cuts:
            if c.get(field):
                names.setdefault(c[field], []).append(c["key"])
        by_name = {g["name"]: gid for gid, g in self.store.groups.items()}
        touched: set[str] = set()
        for name, keys in names.items():
            gid = by_name.get(name) or self._new_group(name)
            touched |= self._move(keys, gid)
        self._rematch(touched)
        return len(names)

    # ================================================================ 매칭
    def _rematch(self, gids) -> None:
        for gid in gids:
            g = self.store.groups.get(gid)
            if g is None:
                continue
            keys = self._group_keys(gid)
            shots = []
            for k in keys:
                a = self._analysis(k)
                if a is not None and self._is_stale(k):
                    a = None
                shots.append(ShotIn(k, self._order(k), a, excluded=bool(self.store.cuts[k].get("excluded"))))
            if not any(s.analysis is not None for s in shots):
                continue
            res, info = match_group(shots, g.get("ref"), g.get("mode", "reference"))
            for k in keys:
                self.store.cuts[k]["auto"] = res[k] if (self._analysis(k) is not None and not self._is_stale(k)) else None
            g["info"] = info

    def analyze(self, gids: list[str] | None = None, force: bool = False) -> None:
        with self.lock:
            if self.store is None or self.job is not None:
                return
            groups = [g for g in (gids or list(self.store.groups)) if g in self.store.groups]
            targets, seen = [], set()
            for gid in groups:
                for k in self._group_keys(gid):
                    cut = self.by_key[k][0]
                    if k in seen or not cut["media_ok"]:
                        continue                      # 제외한 컷도 측정은 해 둔다 (다시 포함하면 바로 매칭)
                    seen.add(k)
                    entry = self.store.analysis.get(k)
                    if force or entry is None or entry.get("a") is None or self._is_stale(k):
                        targets.append(k)
            if not groups:
                self._toast("먼저 컷을 그룹으로 묶어 주세요")
                return
            self._push_undo()
            self.job = {"kind": "analyze", "total": len(targets), "done": 0, "active": [], "cancel": False,
                        "groups": groups, "started": time.time()}
        self._bump(save=False)
        threading.Thread(target=self._run_analysis, args=(targets, groups), daemon=True, name="analysis").start()

    def cancel(self) -> None:
        with self.lock:
            if self.job is not None:
                self.job["cancel"] = True
        self._bump(save=False)

    def _analyze_key(self, key: str) -> None:
        job = self.job
        cut = self.by_key.get(key, [None])[0]
        if cut is None or job is None or job["cancel"]:
            return
        with self.lock:
            job["active"].append(key)
        self._bump(save=False)
        try:
            a, thumbs = analyze_clip(lambda: self._open(cut), cut["src_in"], cut["src_out"],
                                     lambda g: self._space_for(cut, g)[0], self.faces, name=cut["src_name"],
                                     cancel=lambda: job["cancel"])
            entry = {"a": a, "t": int(time.time())}
            for kind, data in thumbs.items():
                name = self._thumb_name(key, kind)
                (self.cache / "thumbs" / name).write_bytes(data)
                entry["thumb" + ("_" + kind if kind else "")] = name
        except Cancelled:
            entry = None
        except DecodeError as e:
            entry = {"a": None, "error": str(e)}
        except Exception as e:  # noqa: BLE001
            entry = {"a": None, "error": f"분석 오류: {type(e).__name__}: {str(e)[:120]}"}
        with self.lock:
            if entry is not None:
                self.store.analysis[key] = entry
                self.store.touch(analysis=True)
            job["active"].remove(key)
            job["done"] += 1
        self._bump(save=False)

    def _run_analysis(self, targets: list[str], groups: list[str]) -> None:
        job = self.job
        if targets:
            with ThreadPoolExecutor(max_workers=self.n_workers, thread_name_prefix="analyze") as ex:
                for f in as_completed([ex.submit(self._analyze_key, k) for k in targets]):
                    f.result()
        with self.lock:
            cancelled = job["cancel"]
            errors = n_flag = 0
            try:
                self._rematch(groups)
                errors = sum(1 for k in targets if self.store.analysis.get(k, {}).get("error"))
                n_flag = sum(1 for g in groups for k in self._group_keys(g)
                             if (self.store.cuts[k].get("auto") or {}).get("exp_conf") == "low")
            except Exception as e:  # noqa: BLE001  매칭이 실패해도 작업 표시는 반드시 내린다
                self.job = None
                self._bump()
                self._toast(f"매칭 중 오류가 났습니다: {type(e).__name__}: {str(e)[:120]}", "error")
                raise
            self.job = None
        self._bump()
        self._schedule_apply()
        if cancelled:
            self._toast("분석을 멈췄습니다. 끝난 컷까지만 반영했습니다")
        else:
            msg = "매칭을 마쳤습니다"
            if n_flag:
                msg += f". 확인이 필요한 컷이 {n_flag}개 있습니다"
            if errors:
                msg += f". 읽지 못한 컷이 {errors}개 있습니다"
            self._toast(msg, "warn" if (n_flag or errors) else "ok")

    # ================================================================ 의도
    def intent(self, scope: str, keys: list[str] | None = None, gid: str | None = None,
               exp: float = 0.0, temp: float = 0.0, tint: float = 0.0, reset: bool = False) -> None:
        with self.lock:
            if self.store is None:
                return
            targets = []
            if scope == "group":
                g = self.store.groups.get(gid or "")
                if g is not None:
                    targets.append(g["intent"])
            else:
                for k in keys or []:
                    c = self.store.cuts.get(k)
                    if c is not None and k in self.by_key and not c.get("locked"):
                        targets.append(c["intent"])
            if not targets:
                return
            self._push_undo()
            for it in targets:
                if reset:
                    it.update(new_intent())
                else:
                    it["exp"] = round(float(np.clip(it["exp"] + exp, -6, 6)), 4)
                    it["temp"] = round(float(np.clip(it["temp"] + temp, -40, 40)), 3)
                    it["tint"] = round(float(np.clip(it["tint"] + tint, -40, 40)), 3)
        self._bump()
        self._schedule_apply()

    def set_cut(self, keys: list[str], locked=None, excluded=None, use_exp=..., use_wb=..., use_black=...,
                space=...) -> None:
        with self.lock:
            if self.store is None:
                return
            self._push_undo()
            regroup: set[str] = set()
            for k in keys:
                if k not in self.by_key:
                    continue
                c = self.store.cut(k)
                if locked is not None and bool(locked) != bool(c.get("locked")):
                    if locked:
                        t = self.total(k)
                        c["frozen"] = {"exp": t["exp"], "wb": t["wb"], "slope": t["slope"], "offset": t["offset"],
                                       "black": t["black"]}
                    else:
                        c["frozen"] = None
                    c["locked"] = bool(locked)
                if excluded is not None and bool(excluded) != bool(c.get("excluded")):
                    c["excluded"] = bool(excluded)
                    if c.get("group"):
                        regroup.add(c["group"])
                if use_exp is not ...:
                    c["use_exp"] = None if use_exp is None else bool(use_exp)
                if use_wb is not ...:
                    c["use_wb"] = None if use_wb is None else bool(use_wb)
                if use_black is not ...:
                    c["use_black"] = None if use_black is None else bool(use_black)
                if space is not ...:
                    c["space"] = space if space in spaces.SPACES else None
                    if c.get("group"):
                        regroup.add(c["group"])
                    self.quick.pop(k, None)
            self._rematch(regroup)
        self._bump()
        self._start_quick_thumbs()
        self._schedule_apply()

    def set_settings(self, **kw) -> None:
        with self.lock:
            if self.store is None:
                return
            st = self.store.settings
            rescan = False
            for k, v in kw.items():
                if k not in config.DEFAULT_SETTINGS:
                    continue
                if k == "node_label":
                    v = str(v).strip()[:40] or config.DEFAULT_SETTINGS[k]
                    rescan = rescan or v != st[k]
                elif k == "apply_mode":
                    if v not in ("linear_gain", "di_offset"):
                        continue
                elif k in ("default_log", "default_display"):
                    if v not in spaces.SPACES:
                        continue
                else:
                    v = bool(v)
                st[k] = v
            if "default_log" in kw:
                self._rematch(list(self.store.groups))
        self._bump()
        if rescan:
            self.rescan()
        else:
            self._schedule_apply()

    # ========================================================== 되돌리기
    def undo(self) -> None:
        self._history(self.undo_stack, self.redo_stack)

    def redo(self) -> None:
        self._history(self.redo_stack, self.undo_stack)

    def _history(self, src: list, dst: list) -> None:
        with self.lock:
            if self.store is None or not src or self.job is not None:
                return
            dst.append(self.store.snapshot())
            self.store.restore(src.pop())
            for c in self.cuts:
                self.store.cut(c["key"])
        self._bump()
        self._schedule_apply()

    # ============================================================== 모델
    def download_models(self) -> None:
        with self.lock:
            if self.job is not None:
                return
            self.job = {"kind": "models", "total": 2, "done": 0, "active": [], "cancel": False}
        self._bump(save=False)
        threading.Thread(target=self._download_models, daemon=True, name="models").start()

    def _download_models(self) -> None:
        dest = config.data_dir() / "models"
        dest.mkdir(parents=True, exist_ok=True)
        err = None
        for name in (YUNET, SFACE):
            url, sha = MODEL_URLS[name]
            try:
                if not (dest / name).is_file():
                    data = urllib.request.urlopen(url, timeout=120).read()
                    if hashlib.sha256(data).hexdigest() != sha:
                        raise ValueError("체크섬이 맞지 않습니다")
                    (dest / name).write_bytes(data)
            except Exception as e:  # noqa: BLE001
                err = f"{name}: {type(e).__name__}"
                break
            with self.lock:
                self.job["done"] += 1
            self._bump(save=False)
        with self.lock:
            self.job = None
            self.faces = FaceEngine(config.model_dirs())
        self._bump(save=False)
        self._toast("얼굴 모델을 받지 못했습니다 (" + err + ")" if err else "얼굴 모델을 준비했습니다", "error" if err else "ok")

    # ================================================================ 상태
    def thumb_path(self, name: str) -> Path | None:
        p = self.cache / "thumbs" / os.path.basename(name)
        return p if p.is_file() else None

    def state(self) -> dict:
        with self.lock:
            base = {"version": self.version, "app": {"name": APP_NAME, "version": __version__},
                    "conn": dict(self.conn), "job": None, "groups": [], "cuts": [], "settings": {},
                    "timelines": [dict(t) for t in self.timelines],
                    "spaces": spaces.options(), "can_undo": False, "can_redo": False,
                    "models": {"detector": self.faces.detector_ready, "recognizer": self.faces.recognizer_ready},
                    "steps": {"exp": config.EXP_STEP, "exp_fine": config.EXP_STEP_FINE,
                              "exp_coarse": config.EXP_STEP_COARSE, "wb": config.WB_STEP}}
            if self.job is not None:
                j = self.job
                base["job"] = {"kind": j["kind"], "total": j["total"], "done": j["done"],
                               "active": list(j["active"]), "cancel": j["cancel"]}
            if self.store is None:
                return base
            st = self.store
            base["settings"] = dict(st.settings)
            base["can_undo"], base["can_redo"] = bool(self.undo_stack), bool(self.redo_stack)
            active = set(self.job["active"]) if self.job else set()
            counts: dict[str, dict] = {}
            for cut in self.cuts:
                k = cut["key"]
                c = st.cuts.get(k) or new_cut()
                entry = st.analysis.get(k) or {}
                a = entry.get("a")
                stale = self._is_stale(k) if a is not None else False
                sid, ssrc = self._space_for(cut, a.get("guess") if a else None)
                t = self.total(k)
                thumb = entry.get("thumb") or self.quick.get(k)
                gid = c.get("group") if c.get("group") in st.groups else None
                g = st.groups.get(gid) if gid else None
                warn = encoding.check_assignment(a["space"], a["guess"], a.get("signal")) if (a and not stale) else None
                auto = c.get("auto")
                item = {
                    "id": cut["id"], "key": k, "index": cut["index"], "name": cut["name"], "track": cut["track"],
                    "start": cut["start"], "dur": cut["dur"], "fps": cut["fps"], "group": gid,
                    "thumb": f"thumb/{thumb}?v={entry.get('t', 0) if entry.get('thumb') else 'q'}" if thumb else None,
                    "thumb_head": f"thumb/{entry['thumb_h']}?v={entry.get('t', 0)}" if entry.get("thumb_h") else None,
                    "thumb_tail": f"thumb/{entry['thumb_t']}?v={entry.get('t', 0)}" if entry.get("thumb_t") else None,
                    "edge": (a.get("edges") if a and not stale else None),
                    "media_ok": cut["media_ok"], "media_note": cut.get("media_note") or entry.get("error"),
                    "node": cut.get("node_index") is not None, "retimed": bool(cut.get("retimed")),
                    "space": sid, "space_label": spaces.SPACES[sid].label, "space_source": ssrc, "space_warn": warn,
                    "analyzed": a is not None and not stale, "stale": stale, "busy": k in active,
                    "error": entry.get("error"),
                    "kind": a["temporal"]["kind"] if a else None, "faces": len(a["faces"]) if a else 0,
                    "detector": a.get("detector") if a else None,
                    "auto": auto, "intent": dict(c["intent"]), "locked": bool(c.get("locked")),
                    "excluded": bool(c.get("excluded")), "use_exp": c.get("use_exp"), "use_wb": c.get("use_wb"),
                    "use_black": c.get("use_black"),
                    "space_user": c.get("space"), "total": t, "is_ref": bool(g and g.get("ref") == k),
                    "pending": self._pending(cut), "write_failed": cut["id"] in self.failed_writes,
                }
                base["cuts"].append(item)
                if gid:
                    n = counts.setdefault(gid, {"count": 0, "analyzed": 0, "flagged": 0, "busy": 0})
                    n["count"] += 1
                    n["analyzed"] += int(item["analyzed"] or item["excluded"])
                    n["busy"] += int(item["busy"])
                    n["flagged"] += int(bool(auto and auto.get("exp_conf") == "low" and not item["excluded"]) or bool(warn))
            for gid in st.data["group_order"]:
                g = st.groups.get(gid)
                if g is None:
                    continue
                n = counts.get(gid, {"count": 0, "analyzed": 0, "flagged": 0, "busy": 0})
                info = g.get("info") or {}
                base["groups"].append({"id": gid, "name": g["name"], "color": g["color"], "mode": g["mode"],
                                       "ref": g.get("ref"), "intent": dict(g["intent"]), **n,
                                       "identities": info.get("identities", 0), "ref_used": info.get("ref"),
                               "black": info.get("black"),
                                       "bridge": info.get("bridge")})
            return base

    def shutdown(self) -> None:
        with self.lock:
            if self.job is not None:
                self.job["cancel"] = True
            if self._flush_timer is not None:
                self._flush_timer.cancel()
            self._flush()
        self.rworker.stop()
