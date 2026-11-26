"""그룹 안의 컷들을 레퍼런스에 맞추는 노출 · WB 게인을 구한다.

원리
  - 같은 인물의 스킨끼리 비교한다. 선형 공간에서 채널별 게인 = 레퍼런스 스킨 / 컷 스킨.
    이 게인을 노출(스칼라)과 WB(휘도 보존 채널 게인)로 나눠 저장한다.
  - 레퍼런스에 없는 인물만 나오는 컷은, 두 인물이 함께 나온 컷을 다리 삼아 이어 붙인다.
  - 얼굴이 없는 컷은 이미 맞춰진 컷들의 밝기 분포 · 중성색에 맞추고, 그것도 불확실하면
    타임라인상 앞뒤 컷의 값을 제안으로만 올린다.
  - 신뢰도가 낮은 값은 계산만 하고 적용하지 않는다 (apply=False).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ..color import spaces
from ..color.gamuts import EXPOSURE_W
from .faces import SAME_ID, similarity
from .measure import ANCHOR_KEYS

W64 = EXPOSURE_W.astype(np.float64)


@dataclass
class MatchOptions:
    max_exp: float = 3.0          # 자동 노출 한계 (스톱)
    wb_limit: float = 0.5         # 스킨 WB 가 이보다 크게 필요하면 조명색 차이로 보고 WB 제외
    wb_soft_limit: float = 0.35   # 중성색 기반 WB 한계
    neutral_trust: float = 0.7    # 중성색 기반 WB 반영 비율
    anchor_agree: float = 0.30    # 분포 앵커들이 이 안에서 일치해야 신뢰
    min_face_quality: float = 0.25
    black_dead: float = 0.0007    # 블랙 차이가 이보다 작으면 이미 맞은 것으로 본다 (선형, 맞춘 뒤의 노출 기준)
    black_max: float = 0.03       # 이보다 큰 차이는 헤이즈가 아니라 피사체 차이로 본다
    black_solid: float = 0.0015   # 플로어 바로 위 띠와의 차이가 이 안이면 블랙이 면으로 깔려 있다고 본다
    black_depth: float = 0.25     # 플로어가 중간 밝기의 이 비율보다 밝으면 블랙 후보가 아니다


@dataclass
class ShotIn:
    id: str
    order: int
    analysis: dict | None
    excluded: bool = False


@dataclass
class Auto:
    exp: float = 0.0
    wb: list = field(default_factory=lambda: [1.0, 1.0, 1.0])
    exp_conf: str = "low"
    wb_conf: str = "low"
    method: str = "none"
    wb_method: str = "none"
    notes: list = field(default_factory=list)
    exp_apply: bool = False
    wb_apply: bool = False
    # 블랙/헤이즈:  L' = (게인 · black_k) · L + black_o   (게인 = 2^exp · wb, 선형)
    black: float = 0.0                 # 표시용: 오프셋의 밝기 성분 (선형. 0.01 = 화이트의 1%)
    black_k: list = field(default_factory=lambda: [1.0, 1.0, 1.0])
    black_o: list = field(default_factory=lambda: [0.0, 0.0, 0.0])
    black_conf: str = "low"
    black_method: str = "none"         # ref | floor | none
    black_apply: bool = False

    def asdict(self) -> dict:
        return dict(exp=round(float(self.exp), 4), wb=[round(float(v), 5) for v in self.wb],
                    exp_conf=self.exp_conf, wb_conf=self.wb_conf, method=self.method, wb_method=self.wb_method,
                    notes=list(dict.fromkeys(self.notes)), exp_apply=bool(self.exp_apply), wb_apply=bool(self.wb_apply),
                    black=round(float(self.black), 5), black_k=[round(float(v), 5) for v in self.black_k],
                    black_o=[round(float(v), 6) for v in self.black_o], black_conf=self.black_conf,
                    black_method=self.black_method, black_apply=bool(self.black_apply))


_RANK = {"low": 0, "medium": 1, "high": 2}


def _cap(conf: str, cap: str) -> str:
    return conf if _RANK[conf] <= _RANK[cap] else cap


def _e(rgb) -> float:
    return float(np.dot(np.asarray(rgb, np.float64), W64))


def _wmean(vals, wts):
    v = np.asarray(vals, np.float64)
    w = np.asarray(wts, np.float64)
    return (v * w[:, None]).sum(axis=0) / w.sum() if v.ndim == 2 else float((v * w).sum() / w.sum())


def _wmedian(vals, wts):
    """가중 중앙값 (열별)."""
    v = np.asarray(vals, np.float64)
    w = np.asarray(wts, np.float64)
    if v.ndim == 1:
        v = v[:, None]
    out = []
    for j in range(v.shape[1]):
        o = np.argsort(v[:, j])
        cw = np.cumsum(w[o])
        out.append(v[o, j][int(np.searchsorted(cw, cw[-1] / 2.0))])
    return np.array(out)


def split_gain(g_log: np.ndarray, skin_rgb) -> tuple[float, np.ndarray]:
    """채널별 log2 게인을 (노출 스톱, 휘도 보존 WB 게인)으로 나눈다."""
    c = np.asarray(skin_rgb, np.float64)
    gain = 2.0 ** np.asarray(g_log, np.float64)
    exp = float(np.log2(max(_e(gain * c), 1e-9) / max(_e(c), 1e-9)))
    return exp, gain / (2.0 ** exp)


def _normalize_wb(wb: np.ndarray, color) -> np.ndarray:
    """WB 게인이 주어진 색의 노출 지표를 바꾸지 않도록 정규화."""
    c = np.asarray(color, np.float64)
    return wb * (_e(c) / max(_e(wb * c), 1e-9))


class _Identities:
    """그룹 전체에서 같은 인물끼리 묶는다."""

    def __init__(self):
        self.protos: list[dict] = []     # {emb | ident, n}

    def assign(self, faces: list[dict]) -> list[int | None]:
        cand = []
        for fi, f in enumerate(faces):
            for gi, p in enumerate(self.protos):
                s = similarity(f, p)
                if s >= SAME_ID:
                    cand.append((s, fi, gi))
        cand.sort(reverse=True)
        out: list[int | None] = [None] * len(faces)
        used: set[int] = set()
        for s, fi, gi in cand:
            if out[fi] is None and gi not in used:
                out[fi] = gi
                used.add(gi)
        for fi, f in enumerate(faces):
            if out[fi] is None:
                self.protos.append(dict(emb=f.get("emb"), ident=f.get("ident"), n=1))
                out[fi] = len(self.protos) - 1
            else:
                p = self.protos[out[fi]]
                if p.get("emb") is not None and f.get("emb") is not None:
                    e = np.asarray(p["emb"], np.float32) * p["n"] + np.asarray(f["emb"], np.float32)
                    p["emb"] = (e / max(float(np.linalg.norm(e)), 1e-9)).tolist()
                p["n"] += 1
        return out


def _usable_faces(a: dict, opts: MatchOptions) -> list[dict]:
    out = []
    for f in a.get("faces", []):
        rgb = np.asarray(f["rgb"], np.float64)
        if f["quality"] >= opts.min_face_quality and np.all(rgb > 0) and f.get("clip", 0) < 0.4:
            out.append(f)
    return out


def _anchor_delta(target: dict, st: dict) -> tuple[float, float]:
    d = np.array([target[k] - st[k] for k in ANCHOR_KEYS])
    return float(np.median(d)), float(np.ptp(d))


def match_group(shots: list[ShotIn], ref_id: str | None, mode: str = "reference",
                opts: MatchOptions | None = None) -> tuple[dict[str, dict], dict]:
    """{컷 id: Auto dict}, 그룹 진단 정보."""
    opts = opts or MatchOptions()
    res: dict[str, Auto] = {}
    usable = [s for s in shots if s.analysis is not None and not s.excluded]
    for s in shots:
        if s.analysis is None:
            res[s.id] = Auto(notes=["no_analysis"])
        elif s.excluded:
            res[s.id] = Auto(method="excluded", notes=["excluded"])
    info = dict(mode=mode, ref=ref_id, identities=0, solved=0)
    if not usable:
        return {k: v.asdict() for k, v in res.items()}, info
    by_id = {s.id: s for s in usable}
    faces = {s.id: _usable_faces(s.analysis, opts) for s in usable}
    stats = {s.id: s.analysis["stats"] for s in usable}

    # ---------------------------------------------------- 기준 컷 정하기
    pseudo = False
    if mode != "reference" or ref_id not in by_id:
        pseudo = True
        if mode == "reference":
            info["ref_missing"] = True
        with_faces = [s for s in usable if faces[s.id]]
        pool = with_faces or usable
        if with_faces:
            lv = {s.id: float(np.log2(_e(faces[s.id][0]["rgb"]))) for s in pool}
        else:
            lv = {s.id: stats[s.id]["p50"] for s in pool}
        med = float(np.median(list(lv.values())))
        ref_id = min(pool, key=lambda s: (abs(lv[s.id] - med), s.order)).id
    info["ref"] = ref_id

    # ---------------------------------------------------- 인물 묶기
    ids = _Identities()
    order = sorted(usable, key=lambda s: (s.id != ref_id, -len(faces[s.id]), s.order))
    gid_of: dict[str, list[int | None]] = {}
    for s in order:
        gid_of[s.id] = ids.assign(faces[s.id])
    info["identities"] = len(ids.protos)
    obs: dict[str, dict[int, tuple[np.ndarray, float]]] = {}
    for s in usable:
        obs[s.id] = {}
        for f, gid in zip(faces[s.id], gid_of[s.id]):
            if gid is not None:
                obs[s.id][gid] = (np.log2(np.asarray(f["rgb"], np.float64)), float(f["quality"]))

    # ---------------------------------------------------- 스킨 연쇄 풀이
    g: dict[str, np.ndarray] = {ref_id: np.zeros(3)}
    hop: dict[str, int] = {ref_id: 0}
    spread: dict[str, float] = {ref_id: 0.0}
    target: dict[int, np.ndarray] = {gid: m.copy() for gid, (m, _) in obs[ref_id].items()}
    fixed = set(target)
    ref_no_face = not obs[ref_id]

    if ref_no_face:
        # 레퍼런스에 얼굴이 없다: 분포로 레퍼런스와 가장 잘 이어지는 얼굴 컷을 다리로 쓴다
        best = None
        for s in usable:
            if s.id == ref_id or not obs[s.id]:
                continue
            est, agree = _anchor_delta(stats[ref_id], stats[s.id])
            if best is None or agree < best[1]:
                best = (s.id, agree, est)
        if best is not None:
            g[best[0]] = np.full(3, best[2])
            hop[best[0]] = 1
            spread[best[0]] = best[1]
            for gid, (m, _) in obs[best[0]].items():
                target[gid] = m + g[best[0]]
            fixed = set(target)
            info["bridge"] = best[0]

    for _ in range(len(usable) + 1):
        newly = []
        for s in usable:
            if s.id in g:
                continue
            links = [(target[gid] - m, q) for gid, (m, q) in obs[s.id].items() if gid in target]
            if not links:
                continue
            vals = np.array([v for v, _ in links])
            wts = np.array([q for _, q in links])
            g[s.id] = _wmedian(vals, wts) if len(links) >= 3 else _wmean(vals, wts)
            spread[s.id] = float(np.ptp(vals.mean(axis=1))) if len(links) > 1 else 0.0
            newly.append(s.id)
        if not newly:
            break
        h = max(hop.values()) + 1
        for sid in newly:
            hop[sid] = h
        gids = {gid for sid in g for gid in obs[sid]} - fixed
        for gid in gids:
            pts = [(obs[sid][gid][0] + g[sid], obs[sid][gid][1]) for sid in g if gid in obs[sid]]
            target[gid] = _wmean([p for p, _ in pts], [q for _, q in pts])

    anchor: dict[str, np.ndarray] = {}        # 컷마다 노출을 맞춘 지점의 색 (촬영본 단위). 블랙을 맞출 때 이 점은 고정한다
    for sid, gl in g.items():
        a = by_id[sid].analysis
        if sid == ref_id:
            res[sid] = Auto(exp=0.0, exp_conf="high", wb_conf="high", method="ref", wb_method="ref",
                            exp_apply=True, wb_apply=True)
            if faces[sid]:
                anchor[sid] = _wmean([f["rgb"] for f in faces[sid]], [f["quality"] for f in faces[sid]])
            continue
        linked = [(f, gid) for f, gid in zip(faces[sid], gid_of[sid]) if gid in target]
        skin = _wmean([f["rgb"] for f, _ in linked], [f["quality"] for f, _ in linked])
        anchor[sid] = np.asarray(skin, np.float64)
        exp, wb = split_gain(gl, skin)
        q = float(np.mean([f["quality"] for f, _ in linked]))
        conf = "high" if (q >= 0.45 and hop[sid] <= 2 and spread[sid] <= 0.25) else "medium"
        if q < 0.3 or spread[sid] > 0.6:
            conf = "low"
        r = Auto(exp=exp, wb=list(wb), exp_conf=conf, wb_conf=conf, method="skin", wb_method="skin")
        if ref_no_face:
            r.exp_conf = _cap(r.exp_conf, "medium")
            r.wb_conf = "low"
            r.wb = [1.0, 1.0, 1.0]
            r.wb_method = "none"
            r.notes.append("ref_no_face")
        else:
            dev = float(np.max(np.abs(np.log2(wb))))
            weak = float(np.min(skin) / max(np.max(skin), 1e-9)) < 0.02
            if dev > opts.wb_limit:
                r.wb, r.wb_conf, r.wb_method = [1.0, 1.0, 1.0], "low", "none"
                r.notes.append("light_color")
            elif weak:
                r.wb, r.wb_conf, r.wb_method = [1.0, 1.0, 1.0], "low", "none"
                r.notes.append("mono_light")
        if any(f.get("clip", 0) > 0.1 for f, _ in linked):
            r.notes.append("skin_clipped")
            r.exp_conf = _cap(r.exp_conf, "medium")
        res[sid] = r
    info["solved"] = len(g)

    # ---------------------------------------------------- 기준 분포 (맞춰진 컷들의 보정 후 값)
    def solved_ids():
        return [sid for sid, r in res.items() if r.method in ("ref", "skin") and sid in by_id]

    base = solved_ids()
    wts = [3.0 if sid == ref_id else 1.0 for sid in base]
    tgt_anchor = {k: float(_wmedian([stats[sid][k] + res[sid].exp for sid in base], wts)[0]) for k in ANCHOR_KEYS}

    def chroma_after(sid, key):
        wb = np.log2(np.asarray(res[sid].wb, np.float64))
        c = stats[sid][key]
        return [c[0] + wb[0] - wb[1], c[1] + wb[2] - wb[1]]

    tgt_mid = _wmedian([chroma_after(sid, "chroma_mid") for sid in base], wts)
    tgt_neu = _wmedian([chroma_after(sid, "chroma_neutral") for sid in base], wts)
    tgt_skin_e = None
    if target:
        tgt_skin_e = float(np.mean([np.log2(_e(2.0 ** t)) for t in target.values()]))

    def neighbours(s: ShotIn, pred):
        prev = [o for o in usable if o.order < s.order and pred(o.id)]
        nxt = [o for o in usable if o.order > s.order and pred(o.id)]
        out = []
        if prev:
            out.append(max(prev, key=lambda o: o.order).id)
        if nxt:
            out.append(min(nxt, key=lambda o: o.order).id)
        return out

    # ---------------------------------------------------- 스킨으로 못 이은 컷
    pending = [s for s in usable if s.id not in res]
    for s in pending:
        st = stats[s.id]
        r = Auto()
        est, agree = _anchor_delta(tgt_anchor, st)
        near = neighbours(s, lambda i: i in res and res[i].method in ("ref", "skin"))
        # 앞뒤 컷이 둘 다 있고 서로 비슷한 보정을 받았을 때만 '같은 세팅으로 찍힌 구간'으로 본다
        e_near = None
        if len(near) == 2 and abs(res[near[0]].exp - res[near[1]].exp) <= 0.3:
            e_near = float(np.mean([res[i].exp for i in near]))
        if faces[s.id] and tgt_skin_e is not None:
            # 레퍼런스와 이어지지 않는 다른 인물: 그룹의 평균 스킨 레벨에 맞춘다
            f = faces[s.id][0]
            r.exp = tgt_skin_e - float(np.log2(_e(f["rgb"])))
            r.method, r.exp_conf = "skin_other", "medium"
            r.notes.append("other_person")
        elif agree <= opts.anchor_agree and (e_near is None or abs(est - e_near) <= 0.6):
            r.exp, r.method, r.exp_conf = est, "anchors", "medium"
        elif e_near is not None:
            r.exp, r.method, r.exp_conf = e_near, "inherit", "low"
            r.notes.append("inherit")
        else:
            r.exp, r.method, r.exp_conf = est, "anchors", "low"
            r.notes.append("uncertain")
        res[s.id] = r

    # WB: 중성색 기준, 불확실하면 앞뒤 컷 상속
    for s in pending:
        st, r = stats[s.id], res[s.id]
        d_mid = tgt_mid - np.asarray(st["chroma_mid"])
        d_neu = tgt_neu - np.asarray(st["chroma_neutral"])
        agree = float(np.max(np.abs(d_mid - d_neu)))
        d = d_neu if st.get("sat_neutral", 1.0) < 0.25 else 0.5 * (d_mid + d_neu)
        size = float(np.max(np.abs(d)))
        if agree < 0.08 and size <= opts.wb_soft_limit:
            d = d * opts.neutral_trust
            wb = np.array([2.0 ** d[0], 1.0, 2.0 ** d[1]])
            mid = st["chroma_mid"]
            wb = _normalize_wb(wb, [2.0 ** mid[0], 1.0, 2.0 ** mid[1]])
            r.wb, r.wb_method, r.wb_conf = list(wb), "neutral", "medium"
        else:
            near = neighbours(s, lambda i: i in res and res[i].wb_method in ("ref", "skin"))
            if len(near) == 2 and float(np.max(np.abs(np.log2(np.asarray(res[near[0]].wb) / np.asarray(res[near[1]].wb))))) <= 0.08:
                lw = np.mean([np.log2(np.asarray(res[i].wb, np.float64)) for i in near], axis=0)
                r.wb, r.wb_method, r.wb_conf = list(2.0 ** lw), "inherit", "low"
            else:
                r.wb, r.wb_method, r.wb_conf = [1.0, 1.0, 1.0], "none", "low"

    # ---------------------------------------------------- 공통 후처리
    for s in usable:
        r, a = res[s.id], s.analysis
        kind = a["temporal"]["kind"]
        if kind != "steady":
            r.notes.append(kind)
            if kind in ("flicker", "strobe", "ramp") and s.id != ref_id:
                r.exp_conf = _cap(r.exp_conf, "medium")
        sp = spaces.SPACES.get(a.get("space", ""))
        if sp is not None and sp.kind == "display" and s.id != ref_id:
            r.notes.append("display_source")
            r.exp_conf = _cap(r.exp_conf, "medium")
            r.wb_conf = _cap(r.wb_conf, "medium")
        if abs(r.exp) > opts.max_exp:
            r.exp = float(np.clip(r.exp, -opts.max_exp, opts.max_exp))
            r.exp_conf = "low"
            r.notes.append("clamped")
        r.exp_apply = r.exp_conf != "low"
        r.wb_apply = r.wb_conf != "low" and r.wb_method != "none"

    # 중앙값 모드: 기준 컷도 그룹 중앙값으로 옮긴다
    if pseudo:
        ok = [sid for sid in by_id if res[sid].exp_apply]
        if len(ok) >= 2:
            shift = float(np.median([res[sid].exp for sid in ok]))
            lw = np.median([np.log2(np.asarray(res[sid].wb, np.float64)) for sid in ok if res[sid].wb_apply] or [np.zeros(3)], axis=0)
            for sid in by_id:
                r = res[sid]
                r.exp -= shift
                if r.wb_apply or sid == ref_id:
                    r.wb = list(2.0 ** (np.log2(np.asarray(r.wb, np.float64)) - lw))
                if r.method == "ref":
                    r.method, r.wb_method = "median", "median"

    _match_black(usable, res, stats, anchor, ref_id, opts, info)
    return {k: v.asdict() for k, v in res.items()}, info


def _match_black(usable, res, stats, anchor, ref_id, opts: MatchOptions, info: dict) -> None:
    """블랙/헤이즈. 노출 · WB 를 맞춘 뒤에도 남는 블랙 플로어 차이를 선형 오프셋으로 맞춘다.

    플레어 · 헤이즈는 선형 공간에서 더해지는 빛이라 게인만으로는 못 맞춘다. 그래서 컷마다
    L' = a·L + o 의 두 값을 구한다: 플로어는 기준 플로어로 가고, 노출을 맞춘 지점(스킨)은 그대로 남는다.

    플로어가 블랙인지, 블랙이 화면에 없어서 어두운 피사체가 잡힌 것인지는 한 컷만 봐서는 알 수 없다.
    그래서 양쪽 모두 블랙이 면으로 깔려 있고, 스킨으로 이어진 컷일 때만 적용하고 나머지는 제안으로 둔다.
    """
    def gain(r: Auto) -> np.ndarray:
        return (2.0 ** r.exp) * np.asarray(r.wb, np.float64)

    def floor(sid: str) -> np.ndarray | None:
        k = stats[sid].get("black")
        return None if k is None or "black_spread" not in stats[sid] else np.asarray(k, np.float64)

    def solid(sid: str) -> bool:
        k = floor(sid)
        if k is None:
            return False
        sc, ke = 2.0 ** res[sid].exp, _e(k)
        return (stats[sid]["black_spread"] * sc <= max(opts.black_solid, 0.12 * ke * sc)
                and ke <= opts.black_depth * 2.0 ** stats[sid]["p50"])

    by_id = {s.id: s for s in usable}
    ref = res.get(ref_id)
    target, source = None, None
    if ref is not None and ref_id in by_id and solid(ref_id):
        target, source = gain(ref) * floor(ref_id), "ref"
    else:
        cand = [sid for sid, r in res.items() if sid in by_id and r.method in ("ref", "median", "skin")
                and r.exp_apply and solid(sid)]
        if len(cand) >= 2:
            target, source = _wmedian([gain(res[sid]) * floor(sid) for sid in cand], [1.0] * len(cand)), "consensus"
    info["black"] = source
    if target is None:
        return
    for s in usable:
        r, k = res[s.id], floor(s.id)
        if k is None or r.method in ("none", "excluded"):
            continue
        a0 = gain(r)
        o = target - a0 * k
        a = a0.copy()
        an = anchor.get(s.id)
        if an is None:
            an = np.full(3, 2.0 ** stats[s.id]["p75"])
        ok = (an - k) > 0.5 * an                      # 고정할 지점이 플로어보다 1스톱 이상 밝을 때만
        a = np.where(ok, (a0 * an - target) / np.where(ok, an - k, 1.0), a0)
        if np.any(a / a0 < 0.8) or np.any(a / a0 > 1.25):
            a = a0.copy()
        o = target - a * k
        r.black, r.black_k, r.black_o = _e(o), list(a / a0), list(o)
        if s.id == ref_id and source == "ref":
            r.black_method, r.black_conf, r.black_apply = "ref", "high", True
            continue
        r.black_method = "floor"
        linked = r.method in ("ref", "median", "skin")
        conf = "low"
        if solid(s.id) and linked and r.exp_conf != "low":
            conf = "high" if (source == "ref" and r.exp_conf == "high") else "medium"
        elif not solid(s.id):
            r.notes.append("black_unsure")
        elif not linked:
            r.notes.append("black_no_skin")
        if float(np.max(np.abs(o))) > opts.black_max:
            conf = "low"
            r.notes.append("black_large")
        kind = by_id[s.id].analysis["temporal"]["kind"]
        if kind in ("flicker", "strobe", "ramp"):
            conf = _cap(conf, "medium")
        if abs(r.black) < opts.black_dead and float(np.max(np.abs(o))) < 2 * opts.black_dead:
            r.black, r.black_k, r.black_o = 0.0, [1.0, 1.0, 1.0], [0.0, 0.0, 0.0]     # 이미 맞아 있다
        r.black_conf, r.black_apply = conf, conf != "low"
