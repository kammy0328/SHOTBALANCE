// 서버 없이 브라우저 안에서만 도는 미리보기.
// 데이터(demo/demo.json)는 실제 엔진이 합성 타임라인을 분석한 결과를 그대로 떠 온 것이다.
// 원래 구성 그대로일 때는 그 결과를 쓰고, 그룹이나 기준 컷을 바꾸면 합성 영상의 정답값으로 다시 계산한다.

const clone = (o) => JSON.parse(JSON.stringify(o));
const W = [0.25, 0.6, 0.15];
const COLORS = ["#d98b4a", "#5fa8a0", "#b9a44c", "#8f7fc4", "#c96f7e", "#6f9fd1", "#8fb267", "#c58fb8"];
const zero = () => ({ exp: 0, temp: 0, tint: 0 });
const r4 = (v) => Math.round(v * 1e4) / 1e4;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

export class DemoApi {
  constructor() { this.demo = true; this.cb = () => {}; this.undoStack = []; this.redoStack = []; }

  async load() {
    this.d = await (await fetch("demo/demo.json", { cache: "no-cache" })).json();
    this.reset();
  }

  reset() {
    const d = this.d;
    this.s = clone(d.before);
    this.s.conn.status = "demo";
    this.s.conn.demo = true;
    this.s.version = 1;
    this.after = new Map(d.after.cuts.map((c) => [c.key, c]));
    this.orig = new Map(d.after.groups.map((g) => [g.id, {
      ref: g.ref, mode: g.mode, keys: new Set(d.after.cuts.filter((c) => c.group === g.id).map((c) => c.key)) }]));
    this.next = this.s.groups.length + 1;
    this.undoStack = []; this.redoStack = [];
    this.retotal();
  }

  onEvent(cb) { this.cb = cb; }
  async state() { return this.out(); }
  out() { this.s.can_undo = this.undoStack.length > 0; this.s.can_redo = this.redoStack.length > 0; return clone(this.s); }
  push() { this.s.version++; this.cb({ type: "push", state: this.out() }); }
  toast(text, level = "info") { this.cb({ type: "toast", text, level }); }

  // ───────────────────────────── 값 계산 (balance/core.py 의 total 과 같은 식)
  wbIntent(temp, tint) {
    const k = this.s.steps.wb;
    const w = [2 ** (k * temp + 0.5 * k * tint), 2 ** (-k * tint), 2 ** (-k * temp + 0.5 * k * tint)];
    const e = w[0] * W[0] + w[1] * W[1] + w[2] * W[2];
    return w.map((v) => v / e);
  }

  total(c) {
    if (c.locked && c.frozen) return { ...c.frozen, auto_exp: 0, group_exp: 0, cut_exp: 0, use_exp: false, use_wb: false, use_black: false, locked: true };
    const g = this.s.groups.find((x) => x.id === c.group), gi = g ? g.intent : zero(), ci = c.intent, st = this.s.settings, a = c.auto;
    const valid = !!a && !c.excluded && !["none", "excluded"].includes(a.method);
    const useExp = valid && (c.use_exp ?? (a.exp_apply || st.apply_low));
    const hasWb = valid && a.wb_method !== "none";
    const useWb = hasWb && st.wb_enabled && (c.use_wb ?? (a.wb_apply || st.apply_low));
    const hasBlack = valid && ["ref", "floor"].includes(a.black_method);
    const useBlack = !!(hasBlack && useExp && st.black_enabled && st.apply_mode === "linear_gain" && (c.use_black ?? (a.black_apply || st.apply_low)));
    const aExp = useExp ? a.exp : 0, aWb = useWb ? a.wb : [1, 1, 1];
    const wi = this.wbIntent(gi.temp + ci.temp, gi.tint + ci.tint);
    const exp = aExp + gi.exp + ci.exp, wb = aWb.map((v, i) => v * wi[i]);
    const k = useBlack ? a.black_k : [1, 1, 1], ig = 2 ** (gi.exp + ci.exp);
    return { exp: r4(exp), wb, slope: wb.map((v, i) => 2 ** exp * v * k[i]), offset: (useBlack ? a.black_o : [0, 0, 0]).map((v) => v * ig),
             black: useBlack ? a.black : 0, auto_exp: r4(aExp), group_exp: gi.exp, cut_exp: ci.exp,
             use_exp: !!useExp, use_wb: !!useWb, use_black: useBlack, locked: false };
  }

  retotal() {
    const s = this.s;
    for (const g of s.groups) Object.assign(g, { count: 0, analyzed: 0, flagged: 0, busy: 0 });
    for (const c of s.cuts) {
      const g = s.groups.find((x) => x.id === c.group);
      if (!g) c.group = null;
      c.is_ref = !!g && g.ref === c.key;
      c.total = this.total(c);
      c.pending = false;
      if (g) {
        g.count++;
        g.analyzed += c.analyzed || c.excluded ? 1 : 0;
        g.busy += c.busy ? 1 : 0;
        g.flagged += (c.auto && c.auto.exp_conf === "low" && !c.excluded) || c.space_warn ? 1 : 0;
      }
    }
  }

  // ───────────────────────────── 매칭
  need(c) {
    const t = this.d.truth[c.key];
    return [0, 1, 2].map((i) => -(t.exp + Math.log2(t.wb[i])));
  }

  rematch(gid) {
    const s = this.s, g = s.groups.find((x) => x.id === gid);
    if (!g) return;
    const members = s.cuts.filter((c) => c.group === gid);
    const live = members.filter((c) => c.analyzed && !c.stale && !c.excluded);
    const o = this.orig.get(gid);
    const same = o && o.ref === g.ref && o.mode === g.mode && o.keys.size === members.length && members.every((c) => o.keys.has(c.key));
    const ref = live.find((c) => c.key === g.ref);
    let base = [0, 0, 0];
    if (ref) base = this.need(ref);
    else if (live.length) {
      const ns = live.map((c) => this.need(c));
      base = [0, 1, 2].map((i) => { const v = ns.map((n) => n[i]).sort((a, b) => a - b); return v[Math.floor(v.length / 2)]; });
    }
    g.identities = new Set(live.flatMap((c) => this.d.truth[c.key].idents)).size;
    for (const c of members) {
      const a0 = this.after.get(c.key), t = this.d.truth[c.key];
      if (!c.analyzed || c.stale) { c.auto = null; continue; }
      const noBlack = { black: 0, black_k: [1, 1, 1], black_o: [0, 0, 0], black_conf: "low", black_method: "none", black_apply: false };
      if (c.excluded) { c.auto = { exp: 0, wb: [1, 1, 1], exp_conf: "low", wb_conf: "low", method: "excluded", wb_method: "none", notes: ["excluded"], exp_apply: false, wb_apply: false, ...noBlack }; continue; }
      const wrongSpace = t.space !== c.space;
      if ((same && a0 && a0.auto && a0.space === c.space && a0.auto.method !== "excluded") || (wrongSpace && a0 && a0.auto && a0.auto.method !== "excluded")) {
        c.auto = clone(a0.auto);
        continue;
      }
      const n = this.need(c), gl = n.map((v, i) => v - base[i]);
      const exp = gl[0] * W[0] + gl[1] * W[1] + gl[2] * W[2];
      const isRef = !!ref && ref.key === c.key;
      const faces = t.idents.length > 0;
      const notes = [];
      if (c.kind && c.kind !== "steady") notes.push(c.kind);
      let conf = faces ? "high" : "medium";
      if (["flicker", "strobe", "ramp"].includes(c.kind)) conf = "medium";
      if (c.space.startsWith("rec709") || c.space === "srgb") { notes.push("display_source"); conf = "medium"; }
      // 블랙: 기준 컷이 있으면 합성 영상의 플레어 값으로 계산한다 (엔진과 같은 식: 기준 노출 단위의 오프셋)
      let black = noBlack;
      if (ref) {
        const tr = this.d.truth[ref.key], gr = tr.wb.map((w) => 2 ** tr.exp * w);
        const o = [0, 1, 2].map((i) => gr[i] * (tr.flare[i] - t.flare[i]));
        if (isRef) black = { ...noBlack, black_conf: "high", black_method: "ref", black_apply: true };
        else {
          if (!faces) notes.push("black_no_skin");
          black = { black: o[0] * W[0] + o[1] * W[1] + o[2] * W[2], black_k: [1, 1, 1], black_o: o, black_conf: faces ? conf : "low",
                    black_method: "floor", black_apply: faces };
        }
      }
      c.auto = isRef
        ? { exp: 0, wb: [1, 1, 1], exp_conf: "high", wb_conf: "high", method: "ref", wb_method: "ref", notes, exp_apply: true, wb_apply: true, ...black }
        : { exp: r4(exp), wb: gl.map((v) => 2 ** (v - exp)), exp_conf: conf, wb_conf: conf, method: faces ? "skin" : "anchors",
            wb_method: faces ? "skin" : "neutral", notes, exp_apply: true, wb_apply: true, ...black };
    }
  }

  async runAnalysis() {
    const s = this.s;
    const targets = s.cuts.filter((c) => c.group && c.media_ok && (!c.analyzed || c.stale));
    const job = (s.job = { kind: "analyze", total: targets.length, done: 0, active: [], cancel: false });
    this.push();
    const queue = targets.slice();
    const worker = async () => {
      while (queue.length && !job.cancel) {
        const c = queue.shift();
        c.busy = true; job.active.push(c.key);
        this.retotal(); this.push();
        await sleep(230 + Math.random() * 240);
        const a0 = this.after.get(c.key) || {}, m = this.d.meta[c.key] || {};
        Object.assign(c, { busy: false, analyzed: true, stale: false, kind: a0.kind || m.kind || "steady",
                           faces: a0.faces ?? m.faces ?? 0, detector: "hint", thumb: m.thumb || c.thumb,
                           thumb_head: m.thumb_head || null, thumb_tail: m.thumb_tail || null, edge: m.edge || null });
        if (this.d.truth[c.key].space !== c.space && a0.space_warn) c.space_warn = a0.space_warn;
        job.active.splice(job.active.indexOf(c.key), 1);
        job.done++;
        this.retotal(); this.push();
      }
    };
    if (targets.length) await Promise.all([worker(), worker(), worker()]);
    else await sleep(260);
    for (const g of s.groups) this.rematch(g.id);
    const cancelled = job.cancel;
    s.job = null;
    this.retotal(); this.push();
    const flagged = s.cuts.filter((c) => c.group && c.auto && c.auto.exp_conf === "low" && !c.excluded).length;
    if (cancelled) this.toast("분석을 멈췄습니다. 끝난 컷까지만 반영했습니다");
    else this.toast(flagged ? `매칭을 마쳤습니다. 확인이 필요한 컷이 ${flagged}개 있습니다` : "매칭을 마쳤습니다", flagged ? "warn" : "ok");
  }

  // ───────────────────────────── 되돌리기
  snap() { return clone({ groups: this.s.groups, cuts: this.s.cuts.map((c) => ({ key: c.key, group: c.group, intent: c.intent, auto: c.auto, locked: c.locked, frozen: c.frozen || null, excluded: c.excluded, use_exp: c.use_exp, use_wb: c.use_wb, use_black: c.use_black })) }); }
  remember() { this.undoStack.push(this.snap()); if (this.undoStack.length > 60) this.undoStack.shift(); this.redoStack = []; }
  restore(sn) {
    this.s.groups = sn.groups;
    const m = new Map(sn.cuts.map((c) => [c.key, c]));
    for (const c of this.s.cuts) Object.assign(c, m.get(c.key));
  }

  move(keys, gid) {
    const touched = new Set();
    for (const c of this.s.cuts) {
      if (!keys.includes(c.key) || c.group === gid) continue;
      const old = this.s.groups.find((g) => g.id === c.group);
      if (old) { touched.add(old.id); if (old.ref === c.key) old.ref = null; }
      c.group = gid; c.auto = null;
      if (gid) touched.add(gid);
    }
    for (const g of touched) this.rematch(g);
  }

  // ───────────────────────────── 동작
  async post(name, b = {}) {
    const s = this.s;
    let result;
    const cutsOf = (keys) => s.cuts.filter((c) => (keys || []).includes(c.key));
    switch (name) {
      case "connect": this.reset(); break;
      case "analyze":
        if (s.job) break;
        if (!s.groups.length) { this.toast("먼저 컷을 그룹으로 묶어 주세요"); break; }
        this.remember();
        this.runAnalysis();
        break;
      case "cancel": if (s.job) s.job.cancel = true; break;
      case "intent": {
        const targets = b.scope === "group" ? s.groups.filter((g) => g.id === b.group).map((g) => g.intent)
          : cutsOf(b.keys).filter((c) => !c.locked).map((c) => c.intent);
        if (!targets.length) break;
        this.remember();
        for (const it of targets) {
          if (b.reset) Object.assign(it, zero());
          else { it.exp = r4(Math.max(-6, Math.min(6, it.exp + (b.exp || 0)))); it.temp = r4(it.temp + (b.temp || 0)); it.tint = r4(it.tint + (b.tint || 0)); }
        }
        break;
      }
      case "cut": {
        this.remember();
        const regroup = new Set();
        for (const c of cutsOf(b.keys)) {
          if (b.locked != null && !!b.locked !== !!c.locked) { c.frozen = b.locked ? { exp: c.total.exp, wb: c.total.wb, slope: c.total.slope, offset: c.total.offset, black: c.total.black } : null; c.locked = !!b.locked; }
          if (b.excluded != null && !!b.excluded !== !!c.excluded) { c.excluded = !!b.excluded; if (c.group) regroup.add(c.group); }
          if ("use_exp" in b) c.use_exp = b.use_exp;
          if ("use_wb" in b) c.use_wb = b.use_wb;
          if ("use_black" in b) c.use_black = b.use_black;
          if ("space" in b && b.space && b.space !== c.space) {
            const sp = s.spaces.find((x) => x.id === b.space);
            Object.assign(c, { space: b.space, space_user: b.space, space_label: sp ? sp.label : b.space, space_source: "user",
                               stale: !!c.analyzed, analyzed: false, auto: null,
                               space_warn: this.d.truth[c.key].space === b.space ? null : c.space_warn });
            if (c.group) regroup.add(c.group);
          }
        }
        for (const g of regroup) this.rematch(g);
        break;
      }
      case "group/create": {
        this.remember();
        const n = this.next++;
        const g = { id: "d" + n, name: `그룹 ${n}`, color: COLORS[(n - 1) % COLORS.length], mode: "reference", ref: null, intent: zero(),
                    count: 0, analyzed: 0, flagged: 0, busy: 0, identities: 0 };
        s.groups.push(g);
        this.move(b.keys || [], g.id);
        result = g.id;
        break;
      }
      case "group/assign": this.remember(); this.move(b.keys || [], b.group || null); break;
      case "group/update": {
        const g = s.groups.find((x) => x.id === b.id);
        if (!g) break;
        this.remember();
        if (b.name) g.name = String(b.name).slice(0, 40);
        if (b.mode) g.mode = b.mode;
        if ("ref" in b) { g.ref = b.ref; if (b.ref) g.mode = "reference"; }
        this.rematch(g.id);
        break;
      }
      case "group/delete":
        this.remember();
        for (const c of s.cuts) if (c.group === b.id) { c.group = null; c.auto = null; }
        s.groups = s.groups.filter((g) => g.id !== b.id);
        break;
      case "group/import": this.toast("데모에서는 이미 Resolve 그룹을 가져온 상태입니다"); break;
      case "settings":
        for (const k of Object.keys(b)) if (k in s.settings) s.settings[k] = b[k];
        break;
      case "undo": case "redo": {
        const [src, dst] = name === "undo" ? [this.undoStack, this.redoStack] : [this.redoStack, this.undoStack];
        if (!src.length || s.job) break;
        dst.push(this.snap());
        this.restore(src.pop());
        break;
      }
      case "apply": this.toast("미리보기에서는 Resolve 에 쓰지 않습니다"); break;
      case "models": case "rescan": case "goto": break;
      default: return { ok: false, error: "unknown action" };
    }
    this.retotal();
    this.s.version++;
    return { ok: true, state: this.out(), result };
  }
}
