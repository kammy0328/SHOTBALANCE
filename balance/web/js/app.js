import { createApi } from "./api.js";
import { Thumbs } from "./thumbs.js";
import * as F from "./format.js";

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const h = (tag, cls, html) => { const e = document.createElement(tag); if (cls) e.className = cls; if (html != null) e.innerHTML = html; return e; };
const icon = (id) => `<svg><use href="#${id}"/></svg>`;
const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
const flash = (el, cls, ms = 700) => { el.classList.remove(cls); void el.offsetWidth; el.classList.add(cls); setTimeout(() => el.classList.remove(cls), ms); };

const ui = {
  st: null, sel: new Set(), anchor: null, filter: "all", scope: "cut", compare: false, size: "m",
  cards: new Map(), chips: new Map(), byId: new Map(), groups: new Map(),
  entered: false, hadJob: false, railSig: "", lastValue: null, lastTarget: "", pop: null,
};
let api;
const thumbs = new Thumbs();
const app = $("#app");

/* ────────────────────────────────────────────────────── 상태 */
async function boot() {
  buildTape();
  wire();
  api = await createApi();
  api.onEvent(onEvent);
  apply(await api.state());
  requestAnimationFrame(() => app.classList.remove("is-booting"));
  if (api.demo) setTimeout(() => toast("합성 영상으로 도는 미리보기입니다. 자동 매칭을 눌러 보세요"), 1400);
}

let refreshTimer = 0;
function onEvent(ev) {
  if (ev.type === "state") {
    clearTimeout(refreshTimer);
    refreshTimer = setTimeout(async () => apply(await api.state()), 40);
  } else if (ev.type === "toast") {
    toast(ev.text, ev.level);
  } else if (ev.type === "push") {
    apply(ev.state);
  }
}

async function act(name, body) {
  try {
    const r = await api.post(name, body);
    if (r && r.state) apply(r.state);
    if (r && r.ok === false && r.error) toast(r.error, "error");
    return r;
  } catch {
    toast("앱과 연결이 끊겼습니다. 다시 실행해 주세요", "error");
    return null;
  }
}

function apply(st) {
  if (!st) return;
  if (ui.st && st.version < ui.st.version && st.conn.project_id === ui.st.conn.project_id) return;
  ui.st = st;
  ui.byId = new Map(st.cuts.map((c) => [c.id, c]));
  ui.groups = new Map(st.groups.map((g) => [g.id, g]));
  for (const id of [...ui.sel]) if (!ui.byId.has(id)) ui.sel.delete(id);
  if (ui.filter !== "all" && ui.filter !== "none" && !ui.groups.has(ui.filter)) ui.filter = "all";
  if (ui.filter === "none" && !st.cuts.some((c) => !c.group)) ui.filter = "all";
  render();
}

const gainOf = (t) => { const e = Math.pow(2, t.exp); return [e * t.wb[0], e * t.wb[1], e * t.wb[2]]; };
const inFilter = (c) => ui.filter === "all" || (ui.filter === "none" ? !c.group : c.group === ui.filter);
const visibleCuts = () => ui.st.cuts.filter(inFilter);
const selCuts = () => ui.st.cuts.filter((c) => ui.sel.has(c.id));
const selKeys = () => [...new Set(selCuts().map((c) => c.key))];
const hasAuto = (c) => !!c.auto && !["none", "excluded"].includes(c.auto.method);
const isSuggestion = (c) => hasAuto(c) && !c.total.use_exp && !c.excluded && !c.locked && !["ref", "median"].includes(c.auto.method);

function activeGroup() {
  if (ui.groups.has(ui.filter)) return ui.groups.get(ui.filter);
  const gs = new Set(selCuts().map((c) => c.group));
  if (gs.size === 1) { const g = [...gs][0]; return g ? ui.groups.get(g) : null; }
  return null;
}

function mode() {
  const n = ui.sel.size, g = activeGroup();
  if (!n) return g && ui.groups.has(ui.filter) ? "group" : "none";
  return ui.scope === "group" && g ? "group" : "cut";
}

/* ────────────────────────────────────────────────────── 그리기 */
function render() {
  renderBar();
  renderRail();
  renderEmpty();
  renderGrid();
  renderWedge();
  renderMeter();
  if (!ui.entered && ui.st.cuts.length) {
    ui.entered = true;
    app.classList.add("is-entering");
    setTimeout(() => app.classList.remove("is-entering"), 1500);
  }
}

function renderBar() {
  const st = ui.st, c = st.conn;
  const status = c.status === "demo" ? "connected" : c.status;
  $("#conn").dataset.status = status;
  $("#conn").title = { connected: "Resolve 에 연결됨", connecting: "연결하는 중", error: "연결 실패", disconnected: "연결 안 됨" }[status] || "";
  $("#where-project").textContent = c.project || (status === "connecting" ? "Resolve 에 연결하는 중" : "연결 안 됨");
  $("#where-timeline").textContent = c.timeline || "";
  $(".where-sep").hidden = !c.timeline;
  $("#demo-tag").hidden = !(c.demo || api.demo);
  $("#btn-undo").disabled = !st.can_undo || !!st.job;
  $("#btn-redo").disabled = !st.can_redo || !!st.job;
  $("#btn-rescan").disabled = !c.project;

  const job = st.job, btn = $("#btn-match");
  app.classList.toggle("is-busy", !!job || status === "connecting");
  btn.classList.toggle("is-running", !!job);
  btn.disabled = !c.project;
  if (job) {
    const p = job.total ? job.done / job.total : 0.08;
    btn.style.setProperty("--p", p);
    $("#progress").style.setProperty("--p", p);
    $("#match-label").textContent = job.cancel ? "멈추는 중" : job.kind === "models" ? "모델 받는 중" : `분석 중 ${job.done}/${job.total}`;
    btn.title = "누르면 멈춥니다";
  } else {
    $("#match-label").textContent = "자동 매칭";
    btn.title = "분석하고 그룹별로 노출 · 화이트밸런스를 맞춥니다 (A)";
    $("#progress").style.setProperty("--p", 0);
    if (ui.hadJob) flash(btn, "is-done", 750);
  }
  ui.hadJob = !!job;

  const pending = st.cuts.filter((x) => x.pending).length;
  const ab = $("#btn-apply");
  ab.hidden = !(st.settings.auto_apply === false && pending > 0);
  ab.textContent = `Resolve 에 적용 ${pending}`;
}

function renderRail() {
  const st = ui.st;
  const loose = st.cuts.filter((c) => !c.group).length;
  const rows = [{ id: "all", name: "전체", count: st.cuts.length, kind: "all" }];
  for (const g of st.groups) rows.push({ ...g, kind: "group" });
  if (loose && st.groups.length) rows.push({ id: "none", name: "미지정", count: loose, kind: "none" });
  const sig = JSON.stringify([rows.map((r) => [r.id, r.name, r.count, r.color, r.ref, r.mode, r.flagged, r.busy > 0]), ui.filter]);
  $("#btn-new-group").disabled = !ui.sel.size;
  if (sig === ui.railSig) return;
  ui.railSig = sig;
  const list = $("#rail-list");
  list.innerHTML = "";
  for (const r of rows) {
    if (r.kind === "none") list.append(h("div", "rail-sep"));
    const b = h("button", "g-row" + (ui.filter === r.id ? " is-on" : ""));
    b.dataset.gid = r.id;
    let sub = "";
    if (r.kind === "group") {
      const ref = r.ref ? st.cuts.find((c) => c.key === r.ref) : null;
      const parts = [];
      if (r.busy) parts.push(`<i class="g-busy"></i>`);
      if (r.flagged) parts.push(`<span class="g-flag" title="확인이 필요한 컷">${icon("i-ring")}${r.flagged}</span>`);
      parts.push(`<span>${ref ? "기준 " + F.pad2(ref.index) : r.mode === "median" ? "중앙값 기준" : "기준 컷 없음"}</span>`);
      sub = `<span class="g-sub">${parts.join("")}</span>`;
    }
    b.innerHTML = `<i class="g-chip ${r.kind === "all" ? "is-all" : r.kind === "none" ? "is-none" : ""}" style="--gc:${r.color || ""}"></i>` +
      `<span class="g-name">${F.esc(r.name)}</span><span class="g-count">${r.count}</span>${sub}`;
    list.append(b);
    if (r.kind === "all" && st.groups.length) list.append(h("div", "rail-sep"));
  }
}

function renderEmpty() {
  const st = ui.st, c = st.conn, box = $("#empty"), grid = $("#grid");
  let html = "";
  if (c.status === "connecting") {
    html = `<svg class="boot-mark" viewBox="0 0 24 24"><circle cx="12" cy="12" r="9.25" fill="none" stroke="currentColor" stroke-width="1.2"/><path d="M12 2.75a9.25 9.25 0 0 1 0 18.5z" fill="currentColor"/></svg><p>Resolve 에 연결하는 중입니다</p>`;
  } else if (c.status === "error" || c.status === "disconnected") {
    html = `<h2>Resolve 에 연결하지 못했습니다</h2><p>${F.esc(c.message || "Resolve Studio 가 실행 중인지 확인해 주세요.")}</p>
      <ol><li><b>DaVinci Resolve Studio</b> 를 실행하고 프로젝트와 타임라인을 엽니다.</li>
      <li>환경설정 &gt; 시스템 &gt; 일반에서 <b>외부 스크립트 사용</b>을 <b>로컬</b>로 바꿉니다.</li>
      <li>아래에서 다시 연결합니다.</li></ol>
      <div class="empty-actions"><button class="btn is-solid" data-do="connect">다시 연결</button><button class="btn" data-do="demo">데모로 둘러보기</button></div>`;
  } else if (!st.cuts.length) {
    html = `<h2>읽을 수 있는 컷이 없습니다</h2><p>현재 타임라인의 비디오 트랙에 미디어 클립이 있는지 확인해 주세요.</p>
      <div class="empty-actions"><button class="btn is-solid" data-do="rescan">타임라인 다시 읽기</button></div>`;
  }
  box.hidden = !html;
  grid.hidden = !!html;
  $(".sheet-head").hidden = !!html;
  if (html && box.dataset.sig !== html) { box.innerHTML = html; box.dataset.sig = html; }

  const rows = [];
  if (!html) {
    if (!st.models.detector || !st.models.recognizer) {
      rows.push(`${icon("i-target")}<span><b>얼굴 인식 모델</b>이 없습니다. 지금은 스킨 대신 밝기 분포로만 맞춥니다.</span><button data-do="models">모델 내려받기</button>`);
    }
    if (c.dwg === false && !c.aces && st.settings.apply_mode === "linear_gain") {
      rows.push(`${icon("i-warn")}<span>타임라인 색공간이 <b>${F.esc(c.timeline_space || "알 수 없음")}</b> 입니다. <b>DaVinci WG/Intermediate</b> 가 아니면 Gamma=Linear 노드의 Gain 이 실제 노출과 달라집니다. 프로젝트 설정 &gt; 컬러 매니지먼트에서 바꿔 주세요.</span>`);
    }
    const nonode = st.cuts.filter((x) => !x.node).length;
    if (nonode > 0) {
      rows.push(`${icon("i-unlink")}<span><b>${F.esc(st.settings.node_label)}</b> 노드가 없는 컷이 ${nonode}개 있습니다. 값은 계산하지만 Resolve 에는 쓸 수 없습니다.</span><button data-do="node-guide">노드 만드는 방법</button>`);
    }
  }
  const n = $("#notice"), nsig = rows.join("|");
  n.hidden = !rows.length;
  if (n.dataset.sig !== nsig) { n.dataset.sig = nsig; n.innerHTML = rows.map((r) => `<div class="notice-row">${r}</div>`).join(""); }
}

function makeCard(c) {
  const el = h("article", "cut");
  el.dataset.id = c.id;
  el.draggable = true;
  el.innerHTML = `<div class="frame"><canvas width="320" height="180"></canvas><div class="scan"></div><div class="hatch"></div>
    <span class="ref-tag">기준</span><div class="tags"></div></div>
    <div class="cap"><span class="cap-no"></span><span class="cap-name"></span><span class="cap-val"></span></div>
    <div class="mini"><i></i></div>`;
  const card = { el, canvas: $("canvas", el), tags: $(".tags", el), no: $(".cap-no", el), name: $(".cap-name", el),
                 val: $(".cap-val", el), needle: $(".mini i", el), prev: {}, ready: false, tagSig: "" };
  thumbs.attach(c.id, card.canvas, () => { el.classList.add("has-thumb"); card.ready = true; });
  return card;
}

function renderGrid() {
  const st = ui.st, grid = $("#grid");
  grid.dataset.size = ui.size;
  const seen = new Set();
  let prevEl = null, vis = 0;
  for (const c of st.cuts) {
    let card = ui.cards.get(c.id);
    if (!card) { card = makeCard(c); ui.cards.set(c.id, card); }
    const el = card.el, p = card.prev;
    if (el.parentNode !== grid || el.previousElementSibling !== prevEl) grid.insertBefore(el, prevEl ? prevEl.nextSibling : grid.firstChild);
    prevEl = el;
    seen.add(c.id);
    const shown = inFilter(c);
    el.hidden = !shown;
    if (shown) el.style.setProperty("--i", Math.min(vis++, 44));

    const g = c.group ? ui.groups.get(c.group) : null;
    el.style.setProperty("--gc", g ? g.color : "transparent");
    const sugg = isSuggestion(c);
    const cls = {
      "is-selected": ui.sel.has(c.id), "is-busy": c.busy, "is-ref": c.is_ref, "is-excluded": c.excluded,
      "no-media": !c.media_ok, "has-value": c.analyzed || Math.abs(c.total.exp) > 0.005 || c.locked,
    };
    for (const k in cls) el.classList.toggle(k, !!cls[k]);

    if (p.no !== c.index) card.no.textContent = F.pad2(c.index);
    if (p.name !== c.name) { card.name.textContent = c.name; card.name.title = c.name; }

    // 값 표시: 적용 안 된 제안은 흐리게 (노란 고리)
    const ghost = sugg && Math.abs(c.total.exp) < 0.005;
    const shownVal = ghost ? c.auto.exp : c.total.exp;
    const vsig = `${shownVal.toFixed(3)}|${ghost}|${cls["has-value"]}`;
    if (p.vsig !== vsig) {
      card.val.textContent = cls["has-value"] || ghost ? F.dec(shownVal) : "";
      card.val.classList.toggle("is-zero", Math.abs(shownVal) < 0.005);
      card.val.classList.toggle("is-ghost", ghost);
      card.val.title = ghost ? "제안값입니다. 아직 적용하지 않았습니다" : "";
      card.needle.style.setProperty("--x", `${50 + clamp(shownVal, -2, 2) * 24}%`);
      card.needle.classList.toggle("is-ghost", ghost);
      if (p.vsig !== undefined && card.ready) flash(card.val, "is-pop", 420);
    }

    // 썸네일
    thumbs.setSource(c.id, c.thumb);
    thumbs.setGain(c.id, ui.compare ? [1, 1, 1] : gainOf(c.total), card.ready);

    // 표식
    const tags = [];
    if (c.locked) tags.push(["i-lock", "", "잠금"]);
    if (c.kind && F.KIND_ICON[c.kind]) tags.push([F.KIND_ICON[c.kind], "", F.KIND_LABEL[c.kind]]);
    if (c.space_warn) tags.push(["i-warn", "t-warn", "입력 색공간 확인"]);
    if (c.stale) tags.push(["i-refresh", "t-warn", "다시 분석 필요"]);
    if (sugg) tags.push(["i-ring", "t-warn", "신뢰도가 낮아 적용하지 않은 제안값"]);
    if (!c.node) tags.push(["i-unlink", "", "EXPOSURE 노드 없음"]);
    if (!c.media_ok) tags.push(["i-file", "t-bad", c.media_note || "읽을 수 없음"]);
    if (c.write_failed) tags.push(["i-warn", "t-bad", "Resolve 가 값을 받지 않음"]);
    const tsig = tags.map((t) => t[0] + t[1]).join(",");
    if (tsig !== card.tagSig) {
      card.tagSig = tsig;
      card.tags.innerHTML = tags.map(([i, k, t]) => `<span class="tag ${k}" title="${F.esc(t)}">${icon(i)}</span>`).join("");
    }

    if (p.busy && !c.busy && c.analyzed) flash(el, "just-done", 650);
    if (p.group !== undefined && p.group !== c.group && c.group) flash(el, "just-grouped", 400);
    if (p.is_ref === false && c.is_ref) flash(el, "just-ref", 400);
    card.prev = { no: c.index, name: c.name, vsig, busy: c.busy, group: c.group, is_ref: c.is_ref };
  }
  for (const [id, card] of ui.cards) if (!seen.has(id)) { card.el.remove(); thumbs.detach(id); ui.cards.delete(id); }

  const g = ui.groups.get(ui.filter);
  $("#sheet-title").textContent = g ? g.name : ui.filter === "none" ? "미지정" : "전체";
  $("#sheet-count").textContent = vis ? `${vis}컷` : "";
}

function renderWedge() {
  const st = ui.st, track = $("#wedge-track");
  const seen = new Set();
  let prevEl = null, i = 0;
  for (const c of st.cuts) {
    let chip = ui.chips.get(c.id);
    if (!chip) {
      chip = h("button", "w-chip", `<i></i><i></i><i class="w-g"></i>`);
      chip.dataset.id = c.id;
      ui.chips.set(c.id, chip);
    }
    if (chip.parentNode !== track || chip.previousElementSibling !== prevEl) track.insertBefore(chip, prevEl ? prevEl.nextSibling : track.firstChild);
    prevEl = chip;
    seen.add(c.id);
    chip.style.setProperty("--d", c.dur);
    chip.style.setProperty("--i", Math.min(i++, 60));
    const g = c.group ? ui.groups.get(c.group) : null;
    chip.style.setProperty("--gc", g ? g.color : "transparent");
    const [a, b] = chip.children;
    const dev = hasAuto(c) ? -c.auto.exp : null;
    const known = dev !== null;
    const lum = (v) => `${clamp(46 + v * 15, 8, 88).toFixed(1)}%`;
    a.classList.toggle("is-unknown", !known);
    b.classList.toggle("is-unknown", !known);
    if (known) { a.style.setProperty("--l", lum(dev)); b.style.setProperty("--l", lum(dev + c.total.exp)); }
    chip.classList.toggle("is-selected", ui.sel.has(c.id));
    chip.classList.toggle("is-dim", !inFilter(c));
    chip.classList.toggle("is-busy", c.busy);
    chip.title = `${F.pad2(c.index)}  ${c.name}`;
  }
  for (const [id, chip] of ui.chips) if (!seen.has(id)) { chip.remove(); ui.chips.delete(id); }
}

/* ────────────────────────────────────────────────────── 노출 패널 */
function buildTape() {
  const strip = $("#tape-strip");
  let html = `<i class="tape-auto is-off" id="tape-auto"></i>`;
  for (let k = -27; k <= 27; k++) {
    const s = k / 3, major = k % 3 === 0;
    html += `<i class="tick${major ? " is-major" : ""}" style="--s:${s.toFixed(4)}"></i>`;
    if (major) html += `<span class="tick-label${k === 0 ? " is-zero" : ""}" style="--s:${s}">${k === 0 ? "0" : (k > 0 ? "+" : "−") + Math.abs(k / 3)}</span>`;
  }
  strip.innerHTML = html;
}

function setReadout(target, value, html, mixed) {
  const el = $("#readout-val");
  const changed = ui.lastTarget === target && ui.lastValue !== null && Math.abs(ui.lastValue - value) > 1e-4;
  const dir = value > (ui.lastValue ?? 0) ? 1 : -1;
  el.classList.toggle("is-mixed", !!mixed);
  if (el.dataset.html !== html) { el.innerHTML = html; el.dataset.html = html; }
  if (changed && el.animate) {
    el.animate([{ transform: `translateY(${dir * 46}%)`, opacity: 0 }, { transform: "none", opacity: 1 }],
      { duration: 240, easing: "cubic-bezier(.3,1.4,.45,1)" });
  }
  ui.lastTarget = target;
  ui.lastValue = value;
}

function setLayer(id, value, { off = false, text = null } = {}) {
  const row = $("#layer-" + id), v = $("#lv-" + id);
  row.classList.toggle("is-off", off);
  $(".layer-bar i", row).style.setProperty("--w", clamp(value || 0, -2, 2) * 25);
  v.innerHTML = text ?? F.stops(value);
  v.classList.toggle("is-zero", !text && Math.abs(value) < 0.005);
}

function wbCoords(wb) {
  const k = ui.st.steps.wb, l = wb.map((x) => Math.log2(x));
  return [(l[0] - l[2]) / (2 * k), ((l[0] + l[2]) / 2 - l[1]) / (1.5 * k)];
}

function padPos(el, temp, tint) {
  const r = 24, s = 2.6;
  let x = temp * s, y = tint * s;
  const m = Math.hypot(x, y);
  if (m > r) { x *= r / m; y *= r / m; }
  el.style.setProperty("--x", `${x}px`);
  el.style.setProperty("--y", `${y}px`);
}

function renderMeter() {
  const st = ui.st, m = mode(), body = $("#meter-body"), meter = $("#meter");
  $("#meter-none").hidden = m !== "none";
  body.hidden = m === "none";
  if (m !== "none" && !meter.classList.contains("is-open") && matchMedia("(max-width: 900px)").matches) meter.classList.add("is-min");
  meter.classList.toggle("is-open", m !== "none");
  if (m === "none") { ui.lastTarget = ""; return; }

  const cuts = selCuts(), g = activeGroup(), one = cuts.length === 1 ? cuts[0] : null;
  const groupMode = m === "group";
  $$("#seg-scope button").forEach((b) => {
    b.classList.toggle("is-on", b.dataset.v === m);
    b.disabled = b.dataset.v === "group" ? !g : !cuts.length;
  });

  // 제목
  if (groupMode) {
    $("#m-no").innerHTML = `<i class="g-chip" style="--gc:${g.color};display:inline-block"></i>`;
    $("#m-name").textContent = g.name;
    $("#m-count").textContent = `${g.count}컷`;
  } else if (one) {
    $("#m-no").textContent = F.pad2(one.index);
    $("#m-name").textContent = one.name;
    $("#m-count").textContent = "";
  } else {
    $("#m-no").textContent = "";
    $("#m-name").textContent = `${cuts.length}컷 선택`;
    $("#m-count").textContent = "";
  }
  $("#m-name").title = $("#m-name").textContent;

  // 값
  let value, html, mixed = false, sub = "";
  const target = groupMode ? "g:" + g.id : "c:" + cuts.map((c) => c.id).join(",");
  if (groupMode) {
    value = g.intent.exp;
    html = F.stops(value);
    sub = "그룹 전체에 더하는 값";
  } else {
    const vals = cuts.map((c) => c.total.exp), lo = Math.min(...vals), hi = Math.max(...vals);
    value = (lo + hi) / 2;
    if (hi - lo > 0.005) { mixed = true; html = `${F.stops(lo)} … ${F.stops(hi)}`; sub = "컷마다 값이 다릅니다. 함께 움직입니다"; }
    else { html = F.stops(lo); sub = one && one.locked ? "잠근 값" : html.includes("frac") ? F.dec(lo) : ""; }
  }
  setReadout(target, value, html, mixed);
  $("#readout-sub").innerHTML = sub;
  $("#tape-strip").style.setProperty("--v", value.toFixed(4));
  const ta = $("#tape-auto");
  const showAuto = !groupMode && one && hasAuto(one) && !one.excluded && !["ref", "median"].includes(one.auto.method);
  ta.classList.toggle("is-off", !showAuto);
  if (showAuto) { ta.style.setProperty("--a", one.auto.exp.toFixed(4)); ta.title = "자동 매칭값"; }

  const allLocked = !groupMode && cuts.every((c) => c.locked);
  $("#step-down").disabled = $("#step-up").disabled = allLocked;

  // 세 층
  $("#m-layers").hidden = false;
  $("#layer-auto").hidden = groupMode || !one;
  $("#layer-group").hidden = !groupMode && !one;
  $("#layer-cut").hidden = groupMode;
  if (groupMode) {
    setLayer("group", g.intent.exp);
  } else if (one) {
    const a = one.auto, has = hasAuto(one);
    setLayer("auto", has ? a.exp : 0, { off: !one.total.use_exp, text: has ? null : "–" });
    const sw = $("#sw-auto");
    sw.setAttribute("aria-checked", String(!!one.total.use_exp));
    sw.disabled = !has || one.excluded || one.locked || ["ref", "median"].includes(a.method);
    let note = "";
    if (one.locked) note = "잠겨 있습니다. 풀면 자동값과 의도 값이 다시 적용됩니다";
    else if (one.excluded) note = "매칭에서 제외한 컷입니다";
    else if (one.busy) note = "분석하는 중";
    else if (one.stale) note = "입력 색공간이 바뀌었습니다. 자동 매칭을 다시 눌러 주세요";
    else if (!one.group) note = "그룹에 넣으면 자동으로 맞춥니다";
    else if (!has) note = one.analyzed ? "" : "아직 분석하지 않았습니다";
    else note = `<i class="conf" data-c="${a.exp_conf}" title="${F.CONF[a.exp_conf]}"></i><span>${F.METHOD[a.method] || ""}${one.total.use_exp ? "" : " (적용 안 함)"}</span>`;
    $("#auto-note").innerHTML = note;
    setLayer("group", one.total.group_exp);
    setLayer("cut", one.total.cut_exp);
  } else {
    const ce = cuts.map((c) => c.total.cut_exp);
    setLayer("cut", ce[0], { text: Math.max(...ce) - Math.min(...ce) > 0.005 ? "여러 값" : null });
  }
  const it = groupMode ? [g.intent] : cuts.map((c) => c.intent);
  const dirty = it.some((x) => Math.abs(x.exp) > 0.001 || Math.abs(x.temp) > 0.001 || Math.abs(x.tint) > 0.001);
  $("#btn-reset-intent").disabled = !dirty || groupMode;

  // 화이트밸런스
  const gi = !groupMode && one && one.group ? ui.groups.get(one.group)?.intent : null;
  const temp = groupMode ? g.intent.temp : (one ? one.intent.temp + (gi ? gi.temp : 0) : cuts[0].intent.temp);
  const tint = groupMode ? g.intent.tint : (one ? one.intent.tint + (gi ? gi.tint : 0) : cuts[0].intent.tint);
  padPos($("#wb-dot"), temp, tint);
  const wa = $("#wb-auto"), sw = $("#sw-wb");
  const hasWb = !groupMode && one && hasAuto(one) && !["none", "ref", "median"].includes(one.auto.wb_method);
  wa.classList.toggle("is-off", !hasWb);
  if (hasWb) { const [x, y] = wbCoords(one.auto.wb); padPos(wa, x, y); }
  sw.hidden = groupMode || !one;
  if (one) {
    sw.setAttribute("aria-checked", String(!!one.total.use_wb));
    sw.disabled = !hasWb || one.excluded || one.locked || !st.settings.wb_enabled;
  }
  $$(".wb-btn").forEach((b) => (b.disabled = allLocked));
  let wn = "";
  if (hasWb) wn = `<i class="conf" data-c="${one.auto.wb_conf}"></i><span>자동: ${F.WB_METHOD[one.auto.wb_method]}${one.total.use_wb ? "" : " (적용 안 함)"}</span>`;
  else if (one && hasAuto(one) && one.auto.wb_method === "none" && !["ref", "median"].includes(one.auto.method)) wn = "<span>자동 WB 는 맞추지 않았습니다</span>";
  if (Math.abs(temp) > 0.01 || Math.abs(tint) > 0.01) {
    const parts = [];
    if (Math.abs(temp) > 0.01) parts.push(`${temp > 0 ? "따뜻하게" : "차갑게"} ${Math.abs(temp)}`);
    if (Math.abs(tint) > 0.01) parts.push(`${tint > 0 ? "마젠타" : "그린"} ${Math.abs(tint)}`);
    wn += `<span>${wn ? "/ " : ""}의도 ${parts.join(", ")}</span>`;
  }
  $("#wb-note").innerHTML = wn;

  // 컷 동작
  $("#m-actions").hidden = groupMode;
  $("#m-info").hidden = groupMode;
  $("#m-group").hidden = !groupMode;
  if (!groupMode) {
    const ar = $("#act-ref");
    ar.classList.toggle("is-on", !!one && one.is_ref);
    ar.disabled = !one || !one.group;
    $("#act-lock").classList.toggle("is-on", allLocked);
    $("#act-exclude").classList.toggle("is-on", cuts.every((c) => c.excluded));
    $("#act-exclude").disabled = cuts.every((c) => !c.group);

    const sel = $("#sel-space");
    if (!sel.options.length) {
      const kinds = { log: "카메라 로그", display: "디스플레이", linear: "선형" };
      for (const k of Object.keys(kinds)) {
        const og = document.createElement("optgroup");
        og.label = kinds[k];
        for (const s of st.spaces.filter((x) => x.kind === k)) og.append(new Option(s.label, s.id));
        sel.append(og);
      }
    }
    const spaces = new Set(cuts.map((c) => c.space));
    sel.value = spaces.size === 1 ? [...spaces][0] : "";
    const sn = $("#space-note");
    const ref = one || cuts[0];
    if (one && one.space_warn) { sn.textContent = one.space_warn; sn.classList.add("is-warn"); }
    else { sn.classList.remove("is-warn"); sn.textContent = spaces.size === 1 ? F.SPACE_SOURCE[ref.space_source] || "" : "컷마다 다릅니다"; }

    const notes = [];
    if (one) {
      if (!one.media_ok) notes.push([one.media_note || "원본을 읽을 수 없습니다", true, "i-file"]);
      else if (one.error) notes.push([one.error, true, "i-warn"]);
      if (!one.node) notes.push([`${st.settings.node_label} 노드가 없어 Resolve 에 쓸 수 없습니다`, true, "i-unlink"]);
      if (one.write_failed) notes.push(["Resolve 가 값을 받지 않았습니다. 노드 구성을 확인하세요", true, "i-warn"]);
      if (one.retimed) notes.push(["속도가 바뀐 컷입니다", false, "i-ramp"]);
      if (one.analyzed) {
        notes.push([one.faces ? `얼굴 ${one.faces}명을 찾았습니다` : "얼굴이 없는 컷입니다", false, "i-target"]);
        for (const n of (one.auto?.notes || [])) if (F.NOTE[n]) notes.push([F.NOTE[n][0], F.NOTE[n][1], F.KIND_ICON[n] || (F.NOTE[n][1] ? "i-ring" : "i-ring")]);
      }
    }
    $("#notes").innerHTML = notes.map(([t, w, i]) => `<li class="${w ? "is-warn" : ""}">${icon(i)}<span>${F.esc(t)}</span></li>`).join("");
  } else {
    const gn = $("#g-name");
    if (document.activeElement !== gn) gn.value = g.name;
    $$("#seg-mode button").forEach((b) => b.classList.toggle("is-on", b.dataset.v === (g.ref ? "reference" : g.mode === "median" ? "median" : "reference")));
    const ref = g.ref ? st.cuts.find((c) => c.key === g.ref) : null;
    const lines = [];
    if (g.mode === "median") lines.push("그룹의 가운데 값에 모두 맞춥니다. 실수로 튄 컷 몇 개에 흔들리지 않습니다.");
    else if (ref) lines.push(`${F.pad2(ref.index)}번 컷에 나머지를 맞춥니다.`);
    else lines.push("기준 컷이 없습니다. 컷을 고르고 '기준 컷'을 누르세요. 지금은 그룹 중앙값에 맞춥니다.");
    if (g.identities) lines.push(`인물 ${g.identities}명을 구분했습니다.`);
    if (g.bridge) lines.push("기준 컷에 얼굴이 없어 밝기 분포로 이었습니다.");
    $("#g-note").innerHTML = lines.map(F.esc).join("<br>");
    $("#g-note").style.marginLeft = "0";
  }
}

/* ────────────────────────────────────────────────────── 동작 */
function stepSize(ev) {
  const s = ui.st.steps;
  return ev && (ev.ctrlKey || ev.metaKey) ? s.exp_coarse : ev && ev.shiftKey ? s.exp_fine : s.exp;
}

function intent(delta) {
  const m = mode();
  if (m === "none") return;
  if (m === "group") return act("intent", { scope: "group", group: activeGroup().id, ...delta });
  return act("intent", { scope: "cut", keys: selKeys(), ...delta });
}

function nudge(dir, ev) {
  const b = $(dir > 0 ? "#step-up" : "#step-down");
  if (b.disabled || mode() === "none") return;
  flash(b, "is-down", 130);
  intent({ exp: dir * stepSize(ev) });
}

function select(id, ev) {
  const list = visibleCuts().map((c) => c.id);
  if (ev && ev.shiftKey && ui.anchor && list.includes(ui.anchor)) {
    const a = list.indexOf(ui.anchor), b = list.indexOf(id);
    ui.sel = new Set(list.slice(Math.min(a, b), Math.max(a, b) + 1));
  } else if (ev && (ev.ctrlKey || ev.metaKey)) {
    ui.sel.has(id) ? ui.sel.delete(id) : ui.sel.add(id);
    ui.anchor = id;
  } else {
    ui.sel = new Set([id]);
    ui.anchor = id;
  }
  afterSelect();
}

let gotoTimer = 0;
function afterSelect() {
  if (!ui.sel.size) ui.scope = "cut";
  render();
  clearTimeout(gotoTimer);
  if (ui.sel.size === 1 && !api.demo) {
    const id = [...ui.sel][0];
    gotoTimer = setTimeout(() => api.post("goto", { id }).catch(() => {}), 160);
  }
}

function move(dir, ev) {
  const list = visibleCuts().map((c) => c.id);
  if (!list.length) return;
  const cur = ui.anchor && list.includes(ui.anchor) ? list.indexOf(ui.anchor) : dir > 0 ? -1 : list.length;
  const next = list[clamp(cur + dir, 0, list.length - 1)];
  if (ev.shiftKey && ui.sel.size) { ui.sel.add(next); ui.anchor = next; afterSelect(); }
  else { ui.sel = new Set([next]); ui.anchor = next; afterSelect(); }
  ui.cards.get(next)?.el.scrollIntoView({ block: "nearest", behavior: "smooth" });
}

function setFilter(f) {
  ui.filter = f;
  ui.sel.clear();
  ui.anchor = null;
  ui.scope = "cut";
  ui.railSig = "";
  render();
  $("#grid").scrollTop = 0;
}

function toggleFlag(name) {
  const cuts = selCuts();
  if (!cuts.length || mode() !== "cut") return;
  if (name === "ref") {
    const c = cuts[0];
    if (cuts.length !== 1 || !c.group) return;
    return act("group/update", { id: c.group, ref: c.is_ref ? null : c.key });
  }
  const on = !cuts.every((c) => c[name]);
  return act("cut", { keys: selKeys(), [name]: on });
}

async function newGroup() {
  const keys = selKeys();
  if (!keys.length) return toast("먼저 그룹으로 묶을 컷을 고르세요");
  const r = await act("group/create", { keys });
  if (r && r.result) { ui.filter = r.result; ui.sel.clear(); ui.railSig = ""; render(); }
}

function toast(text, level = "info") {
  const t = h("div", "toast", `${level === "error" || level === "warn" ? icon("i-warn") : ""}<span>${F.esc(text)}</span>`);
  t.dataset.level = level;
  $("#toasts").append(t);
  setTimeout(() => { t.classList.add("is-out"); setTimeout(() => t.remove(), 240); }, level === "error" ? 5200 : 3200);
}

/* ────────────────────────────────────────────────────── 팝오버 */
function closePop() {
  $("#pop-layer").classList.remove("is-open");
  $("#pop-layer").innerHTML = "";
  ui.pop = null;
}

function openPop(anchor, node, name) {
  if (ui.pop === name) return closePop();
  closePop();
  const layer = $("#pop-layer");
  layer.classList.add("is-open");
  layer.append(node);
  const r = anchor.getBoundingClientRect(), w = node.offsetWidth, hh = node.offsetHeight;
  let left = clamp(r.right - w, 8, innerWidth - w - 8);
  let top = r.bottom + 8;
  if (top + hh > innerHeight - 8) top = Math.max(8, innerHeight - hh - 8);
  node.style.left = left + "px";
  node.style.top = top + "px";
  node.style.setProperty("--ox", `${clamp(((r.left + r.width / 2 - left) / w) * 100, 0, 100)}%`);
  ui.pop = name;
}

function settingsPop() {
  const st = ui.st, s = st.settings;
  const logs = st.spaces.filter((x) => x.kind === "log");
  const sw = (key, on) => `<button class="switch" role="switch" aria-checked="${!!on}" data-set="${key}"><i></i></button>`;
  const p = h("div", "pop", `
    <h3>설정</h3>
    <label class="pop-row"><span>값을 쓸 노드 라벨<small>노드 트리에 이 이름의 노드를 만들어 둡니다</small></span>
      <input type="text" data-set="node_label" value="${F.esc(s.node_label)}" maxlength="40" spellcheck="false" style="width:110px"></label>
    <label class="pop-row"><span>쓰는 방식<small>${s.apply_mode === "linear_gain" ? "노드 Gamma 를 Linear 로 둔 Gain. 수학적으로 정확합니다" : "DI 타임라인의 Offset. 섀도에서 조금 달라집니다"}</small></span>
      <select data-set="apply_mode"><option value="linear_gain">Linear Gain</option><option value="di_offset">Offset</option></select></label>
    <div class="pop-row"><span>값이 바뀌면 바로 Resolve 에 쓰기</span>${sw("auto_apply", s.auto_apply)}</div>
    <div class="pop-row"><span>화이트밸런스도 맞추기</span>${sw("wb_enabled", s.wb_enabled)}</div>
    <div class="pop-row"><span>신뢰도 낮은 값도 적용<small>끄면 제안만 하고 사람이 고릅니다</small></span>${sw("apply_low", s.apply_low)}</div>
    <div class="pop-row"><span>컷을 고르면 Resolve 재생헤드 이동</span>${sw("follow_selection", s.follow_selection)}</div>
    <label class="pop-row"><span>로그 소스 기본 입력<small>Resolve 에 입력 색공간이 지정돼 있지 않을 때</small></span>
      <select data-set="default_log">${logs.map((x) => `<option value="${x.id}">${F.esc(x.label)}</option>`).join("")}</select></label>
    <div class="pop-sep"></div>
    <button class="pop-item" data-do="import-groups">${icon("i-plus")}Resolve 의 그룹 가져오기</button>
    <button class="pop-item" data-do="rewrite">${icon("i-refresh")}모든 값을 Resolve 에 다시 쓰기</button>
    ${st.models.detector && st.models.recognizer ? "" : `<button class="pop-item" data-do="models">${icon("i-target")}얼굴 인식 모델 내려받기</button>`}
    <div class="pop-foot">${st.app.name} ${st.app.version}${st.conn.resolve_version ? ", Resolve " + F.esc(st.conn.resolve_version) : ""}</div>`);
  $('[data-set="apply_mode"]', p).value = s.apply_mode;
  $('[data-set="default_log"]', p).value = s.default_log;
  p.addEventListener("click", (e) => {
    const b = e.target.closest(".switch[data-set]");
    if (b) {
      const on = b.getAttribute("aria-checked") !== "true";
      b.setAttribute("aria-checked", String(on));
      act("settings", { [b.dataset.set]: on });
    }
  });
  p.addEventListener("change", (e) => {
    const f = e.target.closest("select[data-set], input[data-set]");
    if (f) act("settings", { [f.dataset.set]: f.value });
  });
  return p;
}

function helpPop() {
  const row = (keys, text) => `<span>${keys.map((k) => `<kbd>${k}</kbd>`).join("")}</span><span>${text}</span>`;
  return h("div", "pop is-wide", `<h3>단축키</h3><div class="keys">
    ${row(["←", "→"], "앞뒤 컷으로 이동. Shift 를 누르면 이어서 선택")}
    ${row(["↑", "↓"], "노출 올리기와 내리기 (3분의 1 스톱)")}
    ${row(["Shift", "↑"], "10분의 1 스톱씩")}
    ${row(["Ctrl", "↑"], "1 스톱씩")}
    ${row(["Tab"], "컷 ↔ 그룹 전체")}
    ${row(["R"], "기준 컷으로 지정")}
    ${row(["L"], "잠금")}
    ${row(["X"], "매칭에서 제외")}
    ${row(["G"], "선택한 컷으로 그룹 만들기")}
    ${row(["0"], "의도 값 지우기")}
    ${row(["A"], "자동 매칭")}
    ${row(["\\"], "누르고 있는 동안 촬영본 보기")}
    ${row(["Ctrl", "Z"], "되돌리기. Shift 를 더하면 다시 실행")}
  </div>`);
}

function nodeGuidePop() {
  const lab = F.esc(ui.st.settings.node_label);
  return h("div", "pop is-wide", `<h3>${lab} 노드 만들기</h3><ol>
    <li>컬러 페이지에서 입력 변환(CST 또는 컬러 매니지먼트) <b>바로 다음</b>에 노드를 하나 만듭니다.</li>
    <li>노드 라벨을 <b>${lab}</b> 로 바꿉니다.</li>
    <li>노드를 우클릭해 <b>Gamma 를 Linear</b> 로 바꿉니다. (설정에서 Offset 방식을 쓰면 건너뜁니다)</li>
    <li>프라이머리 팔레트에서 이 노드의 <b>Lum Mix 를 0</b> 으로 둡니다. 0 이 아니면 채널별 게인이 계산한 값 그대로 들어가지 않습니다.</li>
    <li>이 노드 구성을 나머지 컷에 복사하고, 위의 <b>타임라인 다시 읽기</b>를 누릅니다.</li>
  </ol><div class="pop-foot" style="padding:10px 0 0">프로젝트의 타임라인 색공간은 DaVinci WG/Intermediate 여야 합니다. 이 노드는 ShotBalance 전용으로 두세요. 값을 쓸 때마다 노드의 프라이머리를 덮어씁니다.</div>`);
}

/* ────────────────────────────────────────────────────── 이벤트 연결 */
function hold(btn, fn) {
  let t1 = 0, t2 = 0, last = null;
  const stop = () => { clearTimeout(t1); clearInterval(t2); };
  btn.addEventListener("pointerdown", (e) => {
    if (e.button !== 0 || btn.disabled) return;
    last = e;
    fn(e);
    t1 = setTimeout(() => { t2 = setInterval(() => fn(last), 120); }, 380);
  });
  for (const n of ["pointerup", "pointerleave", "pointercancel", "blur"]) btn.addEventListener(n, stop);
  btn.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); fn(e); } });
}

function wire() {
  // 컷 시트
  const grid = $("#grid");
  grid.addEventListener("click", (e) => {
    const el = e.target.closest(".cut");
    if (el) select(el.dataset.id, e);
    else if (ui.sel.size) { ui.sel.clear(); afterSelect(); }
  });
  grid.addEventListener("dragstart", (e) => {
    const el = e.target.closest(".cut");
    if (!el) return;
    if (!ui.sel.has(el.dataset.id)) { ui.sel = new Set([el.dataset.id]); ui.anchor = el.dataset.id; afterSelect(); }
    const n = ui.sel.size;
    e.dataTransfer.effectAllowed = "move";
    e.dataTransfer.setData("text/plain", "balance-cuts");
    const ghost = h("div", "drag-ghost", `${n}컷`);
    document.body.append(ghost);
    e.dataTransfer.setDragImage(ghost, -8, -8);
    setTimeout(() => ghost.remove(), 0);
    for (const id of ui.sel) ui.cards.get(id)?.el.classList.add("is-dragging");
  });
  grid.addEventListener("dragend", () => $$(".cut.is-dragging").forEach((x) => x.classList.remove("is-dragging")));

  // 그룹 레일
  const rail = $("#rail-list");
  rail.addEventListener("click", (e) => { const r = e.target.closest(".g-row"); if (r) setFilter(r.dataset.gid); });
  rail.addEventListener("dragover", (e) => {
    const r = e.target.closest(".g-row");
    $$(".g-row.is-drop").forEach((x) => x !== r && x.classList.remove("is-drop"));
    if (!r || r.dataset.gid === "all") return;
    e.preventDefault();
    r.classList.add("is-drop");
  });
  rail.addEventListener("dragleave", (e) => e.target.closest(".g-row")?.classList.remove("is-drop"));
  rail.addEventListener("drop", (e) => {
    const r = e.target.closest(".g-row");
    $$(".g-row.is-drop").forEach((x) => x.classList.remove("is-drop"));
    if (!r || r.dataset.gid === "all") return;
    e.preventDefault();
    act("group/assign", { keys: selKeys(), group: r.dataset.gid === "none" ? null : r.dataset.gid });
  });
  $("#btn-new-group").addEventListener("click", newGroup);

  // 상단
  $("#btn-match").addEventListener("click", () => (ui.st.job ? act("cancel") : act("analyze", {})));
  $("#btn-apply").addEventListener("click", () => act("apply", {}));
  $("#btn-undo").addEventListener("click", () => act("undo"));
  $("#btn-redo").addEventListener("click", () => act("redo"));
  $("#btn-rescan").addEventListener("click", () => { act("rescan"); toast("타임라인을 다시 읽습니다"); });
  $("#btn-settings").addEventListener("click", (e) => openPop(e.currentTarget, settingsPop(), "settings"));
  $("#btn-help").addEventListener("click", (e) => openPop(e.currentTarget, helpPop(), "help"));
  $("#pop-layer").addEventListener("pointerdown", (e) => { if (e.target === e.currentTarget) closePop(); });
  document.addEventListener("click", (e) => {
    const b = e.target.closest("[data-do]");
    if (!b) return;
    const d = b.dataset.do;
    if (d === "connect") act("connect", {});
    else if (d === "demo") act("connect", { demo: true });
    else if (d === "rescan") act("rescan");
    else if (d === "node-guide") openPop(b, nodeGuidePop(), "node");
    else if (d === "import-groups") { closePop(); act("group/import", { source: "resolve" }); }
    else if (d === "rewrite") { closePop(); act("apply", { force: true }); }
    else if (d === "models") { closePop(); act("models"); toast("얼굴 인식 모델을 내려받습니다 (약 39MB)"); }
  });

  // 보기 옵션
  $("#seg-compare").addEventListener("click", (e) => {
    const b = e.target.closest("button"); if (!b) return;
    ui.compare = b.dataset.v === "before";
    $$("#seg-compare button").forEach((x) => x.classList.toggle("is-on", x === b));
    renderGrid();
  });
  $("#seg-size").addEventListener("click", (e) => {
    const b = e.target.closest("button"); if (!b) return;
    ui.size = b.dataset.v;
    $$("#seg-size button").forEach((x) => x.classList.toggle("is-on", x === b));
    renderGrid();
  });

  // 노출 패널
  hold($("#step-up"), (e) => nudge(1, e));
  hold($("#step-down"), (e) => nudge(-1, e));
  $("#seg-scope").addEventListener("click", (e) => {
    const b = e.target.closest("button"); if (!b || b.disabled) return;
    ui.scope = b.dataset.v;
    renderMeter();
  });
  $$(".wb-btn").forEach((b) => hold(b, () => intent(b.dataset.temp ? { temp: +b.dataset.temp } : { tint: +b.dataset.tint })));
  $("#sw-auto").addEventListener("click", () => { const c = selCuts()[0]; if (c) act("cut", { keys: [c.key], use_exp: !c.total.use_exp }); });
  $("#sw-wb").addEventListener("click", () => { const c = selCuts()[0]; if (c) act("cut", { keys: [c.key], use_wb: !c.total.use_wb }); });
  $("#btn-reset-intent").addEventListener("click", () => intent({ reset: true }));
  $("#act-ref").addEventListener("click", () => toggleFlag("ref"));
  $("#act-lock").addEventListener("click", () => toggleFlag("locked"));
  $("#act-exclude").addEventListener("click", () => toggleFlag("excluded"));
  $("#sel-space").addEventListener("change", (e) => { if (e.target.value) act("cut", { keys: selKeys(), space: e.target.value }); });
  const gn = $("#g-name");
  const rename = () => { const g = activeGroup(); if (g && gn.value.trim() && gn.value.trim() !== g.name) act("group/update", { id: g.id, name: gn.value.trim() }); };
  gn.addEventListener("change", rename);
  gn.addEventListener("keydown", (e) => { if (e.key === "Enter") gn.blur(); });
  $("#seg-mode").addEventListener("click", (e) => {
    const b = e.target.closest("button"), g = activeGroup(); if (!b || !g) return;
    if (b.dataset.v === "median") act("group/update", { id: g.id, mode: "median", ref: null });
    else if (!g.ref) { act("group/update", { id: g.id, mode: "reference" }); toast("기준으로 삼을 컷을 고르고 '기준 컷'을 누르세요"); }
  });
  $("#btn-del-group").addEventListener("click", () => { const g = activeGroup(); if (g) { act("group/delete", { id: g.id }); setFilter("all"); } });
  $("#meter-handle").addEventListener("click", () => $("#meter").classList.toggle("is-min"));

  // 노출 띠
  $("#wedge-track").addEventListener("click", (e) => {
    const chip = e.target.closest(".w-chip"); if (!chip) return;
    const c = ui.byId.get(chip.dataset.id);
    if (c && !inFilter(c)) { ui.filter = "all"; ui.railSig = ""; }
    select(chip.dataset.id, e);
    ui.cards.get(chip.dataset.id)?.el.scrollIntoView({ block: "center", behavior: "smooth" });
  });

  // 키보드
  document.addEventListener("keydown", (e) => {
    if (!ui.st) return;
    const tag = (e.target.tagName || "").toLowerCase();
    if (e.key === "Escape") { if (ui.pop) return closePop(); if (tag === "input") return e.target.blur(); if (ui.sel.size) { ui.sel.clear(); afterSelect(); } return; }
    if (tag === "input" || tag === "select" || tag === "textarea") return;
    const mod = e.ctrlKey || e.metaKey;
    if (mod && e.key.toLowerCase() === "z") { e.preventDefault(); return act(e.shiftKey ? "redo" : "undo"); }
    if (mod && e.key.toLowerCase() === "y") { e.preventDefault(); return act("redo"); }
    if (mod && e.key.toLowerCase() === "a") { e.preventDefault(); ui.sel = new Set(visibleCuts().map((c) => c.id)); return afterSelect(); }
    switch (e.key) {
      case "ArrowRight": e.preventDefault(); return move(1, e);
      case "ArrowLeft": e.preventDefault(); return move(-1, e);
      case "ArrowUp": case "+": case "=": e.preventDefault(); return nudge(1, e);
      case "ArrowDown": case "-": case "_": e.preventDefault(); return nudge(-1, e);
      case "Tab": if (mode() !== "none" && activeGroup() && ui.sel.size) { e.preventDefault(); ui.scope = ui.scope === "cut" ? "group" : "cut"; renderMeter(); } return;
      case "\\": if (!ui.compare) { ui.compare = true; $$("#seg-compare button").forEach((x) => x.classList.toggle("is-on", x.dataset.v === "before")); renderGrid(); } return;
      case "?": return openPop($("#btn-help"), helpPop(), "help");
    }
    if (mod || e.altKey) return;
    switch (e.key.toLowerCase()) {
      case "r": return toggleFlag("ref");
      case "l": return toggleFlag("locked");
      case "x": return toggleFlag("excluded");
      case "g": return newGroup();
      case "0": return intent({ reset: true });
      case "a": return ui.st.job ? null : act("analyze", {});
    }
  });
  document.addEventListener("keyup", (e) => {
    if (e.key === "\\" && ui.compare) {
      ui.compare = false;
      $$("#seg-compare button").forEach((x) => x.classList.toggle("is-on", x.dataset.v === "after"));
      renderGrid();
    }
  });
}

boot();
