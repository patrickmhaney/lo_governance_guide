"use strict";
/* Dashboard sketches: tiles on a 12-column board, one board per dashboard
   row on the Measures tab. Each tile shows one of that dashboard's measures,
   optionally by a dimension, drawn as a sketch: shapes, never numbers, so
   nobody stops to ask why a number is wrong. */

const COLS = 12, ROW_H = 56, GAP = 10;
const TILE_KINDS = [
  { kind: "kpi", label: "Headline number", w: 3, h: 2 },
  { kind: "line", label: "Trend", w: 6, h: 3 },
  { kind: "bar", label: "Comparison", w: 6, h: 3 },
  { kind: "table", label: "Table", w: 6, h: 3 },
  { kind: "filter", label: "Filter", w: 3, h: 1 },
  { kind: "text", label: "Note", w: 3, h: 2 },
];
const KIND = Object.fromEntries(TILE_KINDS.map(k => [k.kind, k]));
// What the "by" dimension means on each kind of tile; kinds without one don't ask.
const BY_LABEL = { line: "Split into lines by (optional)", bar: "Compare by", table: "One row per", filter: "Filter by" };
const USE = [
  ["audience", "Who uses it?", "e.g. The logistics team and the operations director."],
  ["decisions", "What do they decide with it?", "e.g. Which carriers to review. Which customers to call about late orders."],
  ["cadence", "When do they look at it?", "e.g. The Monday operations meeting, and daily for exceptions."],
  ["access", "Should anyone not see some of it?", "e.g. Carrier rates are confidential: hide cost tiles from customer service."],
];

// Other pages link to a dashboard's sketch as #d<id>.
const linkedDash = (m => m ? Number(m[1]) : null)(/^#d(\d+)$/.exec(location.hash));
Object.assign(S, { dash: linkedDash || Number(store.get("proto:dash")) || null, tile: null, dragging: false });

const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
const dashTiles = () => S.tiles.filter(t => t.dashboard_id === S.dash);
const dashMeasures = () => measuresOn(S.dash);
const measureOf = id => S.measures.find(m => m.id === id) || null;
const dimOf = id => S.dimensions.find(d => d.id === id) || null;
const tileOf = id => S.tiles.find(t => t.id === id) || null;
const hasTarget = m => !!(m && (m.target || "").trim());

function defaultTitle(t) {
  const m = measureOf(t.requirement_id), d = dimOf(t.dimension_id);
  const dn = d ? itemName("dimension", d) : "";
  switch (t.kind) {
    case "kpi": return m?.name || "Headline number";
    case "line": return m ? `${m.name} over time` : "Trend";
    case "bar": return m ? m.name + (dn ? ` by ${dn}` : "") : "Comparison";
    case "table": return m ? m.name + (dn ? ` by ${dn}` : "") : "Table";
    case "filter": return dn || "Filter";
    default: return "Note";
  }
}

/* ---------- sketches ---------- */
// A small seeded generator, so a tile keeps its shape from one render to the next.
function rng(seed) {
  let a = (seed * 2654435761) >>> 0;
  return () => {
    a = (a + 0x6D2B79F5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}
function walk(r, n, lo = 12, hi = 50) {
  let y = lo + (hi - lo) * (0.35 + 0.3 * r());
  return Array.from({ length: n }, (_, i) => {
    y = clamp(y + (r() - 0.48) * 12, lo, hi);
    return `${(i * 100 / (n - 1)).toFixed(1)},${y.toFixed(1)}`;
  }).join(" ");
}
const chart = (...kids) => svg("svg", { class: "sk-svg", viewBox: "0 0 100 60", preserveAspectRatio: "none", "aria-hidden": "true" }, ...kids);
const axes = () => [
  ...[15, 30, 45].map(y => svg("line", { class: "sk-grid", x1: 0, x2: 100, y1: y, y2: y })),
  svg("line", { class: "sk-axis", x1: 0, x2: 100, y1: 59.5, y2: 59.5 }),
];
const ph = (cls, width) => h("span", { class: "ph " + cls, style: width ? `width:${width}%` : null });

function sketch(t) {
  const r = rng(t.id), m = measureOf(t.requirement_id), d = dimOf(t.dimension_id);
  const needsMeasure = ["kpi", "line", "bar", "table"].includes(t.kind) && !m;
  if (needsMeasure) return h("div", { class: "sk-empty", text: "Choose a measure" });
  switch (t.kind) {
    case "kpi": return h("div", { class: "sk-kpi" },
      ph("ph-big"),
      h("span", { class: "sk-cmp" }, ph("ph-sm"), hasTarget(m) ? " vs target" : " vs prior period"),
      t.h >= 2 ? chart(svg("polyline", { class: "sk-line", points: walk(r, 10, 8, 52) })) : null);
    case "line": {
      const series = d ? 3 : 1;
      return h("div", { class: "sk-chart" }, chart(...axes(),
        hasTarget(m) ? svg("line", { class: "sk-target", x1: 0, x2: 100, y1: 18, y2: 18 }) : null,
        ...Array.from({ length: series }, (_, i) => svg("polyline", { class: `sk-line s${i}`, points: walk(r, 14) }))),
        d ? h("div", { class: "sk-legend" }, [0, 1, 2].map(i => h("span", { class: `s${i}` }, h("i"), ph("ph-xs")))) : null);
    }
    case "bar": {
      const n = clamp(t.w + 1, 4, 9);
      const heights = Array.from({ length: n }, () => 12 + r() * 44).sort((a, b) => b - a);
      const bw = 100 / n;
      return h("div", { class: "sk-chart" }, chart(...axes(),
        ...heights.map((v, i) => svg("rect", { class: "sk-bar", x: i * bw + bw * 0.18, width: bw * 0.64, y: 60 - v, height: v }))),
        h("div", { class: "sk-ticks" }, heights.map(() => ph("ph-xs"))));
    }
    case "table": {
      const rows = Math.max(1, t.h * 2 - 2);
      const head = [d ? itemName("dimension", d) : "Item", m.name, hasTarget(m) ? "vs target" : "vs prior"];
      return h("div", { class: "sk-table" },
        h("div", { class: "sk-tr sk-th" }, head.map(x => h("span", { text: x }))),
        Array.from({ length: rows }, () => h("div", { class: "sk-tr" }, ph("ph-sm", 40 + r() * 50), ph("ph-sm", 30 + r() * 40), ph("ph-sm", 20 + r() * 30))));
    }
    case "filter": return h("div", { class: "sk-filter" }, h("span", { class: "sk-select" }, d ? "All" : "Choose a dimension", h("i", { text: "▾" })));
    default: return h("div", { class: "sk-note", text: t.note || "Add a note in the panel." });
  }
}

/* ---------- saving: geometry at once, text after a pause ---------- */
const pendingT = new Map(), timersT = new Map();
function updateTile(id, patch, delay = 0) {
  const t = tileOf(id); if (!t) return;
  Object.assign(t, patch);
  pendingT.set(id, { ...(pendingT.get(id) || {}), ...patch });
  clearTimeout(timersT.get(id));
  timersT.set(id, setTimeout(() => flushTile(id), delay));
  renderBoard(); syncSide();
}
async function flushTile(id) {
  clearTimeout(timersT.get(id));
  const p = pendingT.get(id); if (!p) return;
  pendingT.delete(id);
  try {
    const out = await api("PATCH", `${API}/tiles/${id}`, p);
    const t = tileOf(id); if (t && !pendingT.has(id)) Object.assign(t, out);
  } catch (e) { toast("Couldn't save the tile: " + e.message, true); await page.load(); }
}
window.addEventListener("beforeunload", () => { for (const id of [...pendingT.keys()]) flushTile(id); });

function findSpot(w, hgt) {
  const taken = new Set();
  for (const t of dashTiles()) for (let x = t.x; x < t.x + t.w; x++) for (let y = t.y; y < t.y + t.h; y++) taken.add(x + ":" + y);
  for (let y = 0; ; y++) for (let x = 0; x <= COLS - w; x++) {
    let free = true;
    for (let dx = 0; dx < w && free; dx++) for (let dy = 0; dy < hgt && free; dy++) if (taken.has((x + dx) + ":" + (y + dy))) free = false;
    if (free) return { x, y };
  }
}
async function addTile(kind, measureId = null) {
  const k = KIND[kind], spot = findSpot(k.w, k.h);
  // With only one measure on the dashboard, a chart can only mean that one.
  const only = dashMeasures().length === 1 ? dashMeasures()[0].id : null;
  try {
    const t = await api("POST", `${API}/dashboards/${S.dash}/tiles`, { kind, ...spot, w: k.w, h: k.h,
      requirement_id: measureId ?? (["kpi", "line", "bar", "table"].includes(kind) ? only : null) });
    S.tiles.push(t); S.tile = t.id; renderProto();
    $(`.tile[data-id="${t.id}"]`)?.scrollIntoView({ block: "nearest" });
  } catch (e) { toast("Couldn't add the tile: " + e.message, true); }
}
async function removeTile(id) {
  try {
    await api("DELETE", `${API}/tiles/${id}`);
    S.tiles = S.tiles.filter(t => t.id !== id);
    if (S.tile === id) S.tile = null;
    renderProto(); toast("Tile removed");
  } catch (e) { toast(e.message, true); }
}

/* ---------- board ---------- */
function place(el, g) { el.style.gridColumn = `${g.x + 1} / span ${g.w}`; el.style.gridRow = `${g.y + 1} / span ${g.h}`; }
function boardRows(extra) { return Math.max(6, ...dashTiles().map(t => t.y + t.h + 1), extra || 0); }

function renderBoard() {
  if (S.dragging) return;
  const board = $("#board"), focused = document.activeElement?.closest?.(".tile")?.dataset.id;
  const rows = boardRows();
  board.style.setProperty("--rows", rows);
  const cells = [];
  for (let y = 0; y < rows; y++) for (let x = 0; x < COLS; x++) cells.push(h("div", { class: "board-cell", style: `grid-column:${x + 1};grid-row:${y + 1}` }));
  const tiles = dashTiles().map(t => {
    const m = measureOf(t.requirement_id);
    const sub = t.kind === "line" && m?.time_basis ? m.time_basis.split(/[.;]/)[0] : t.kind === "kpi" && m && hasTarget(m) ? "Against target" : "";
    const el = h("div", { class: `tile k-${t.kind}` + (S.tile === t.id ? " selected" : ""), tabindex: "0", dataset: { id: t.id },
      role: "button", "aria-label": `${KIND[t.kind].label}: ${t.title || defaultTitle(t)}` },
      h("div", { class: "tile-h" }, h("span", { class: "tile-title", text: t.title || defaultTitle(t) }), sub ? h("span", { class: "tile-sub", text: sub }) : null),
      h("div", { class: "tile-body" }, sketch(t)),
      h("span", { class: "tile-resize", title: "Drag to resize" }));
    place(el, t);
    el.addEventListener("pointerdown", e => startDrag(e, t, "move"));
    $(".tile-resize", el).addEventListener("pointerdown", e => { e.stopPropagation(); startDrag(e, t, "resize"); });
    el.addEventListener("keydown", e => tileKey(e, t));
    el.addEventListener("focus", () => { if (S.tile !== t.id) { S.tile = t.id; renderSide(); $$(".tile").forEach(x => x.classList.toggle("selected", x === el)); } });
    return el;
  });
  board.replaceChildren(...cells, ...tiles);
  if (!dashTiles().length) board.append(h("div", { class: "board-empty", text: "Empty board. Add a tile from the bar above." }));
  // Keep keyboard focus on the selected tile only; focusing another would select it.
  if (focused && Number(focused) === S.tile) $(`.tile[data-id="${focused}"]`)?.focus({ preventScroll: true });
}

function startDrag(e, t, mode) {
  if (e.button !== 0) return;
  const el = e.currentTarget.closest(".tile"), board = $("#board").getBoundingClientRect();
  const colW = (board.width - GAP * (COLS - 1)) / COLS + GAP, rowH = ROW_H + GAP;
  const sx = e.clientX, sy = e.clientY, orig = { x: t.x, y: t.y, w: t.w, h: t.h };
  let cur = orig, moved = false;
  e.preventDefault();
  el.focus({ preventScroll: true });
  el.setPointerCapture(e.pointerId);
  S.dragging = true;
  el.classList.add("dragging");
  const onMove = ev => {
    if (Math.abs(ev.clientX - sx) + Math.abs(ev.clientY - sy) > 4) moved = true;
    const dx = Math.round((ev.clientX - sx) / colW), dy = Math.round((ev.clientY - sy) / rowH);
    cur = mode === "move"
      ? { ...orig, x: clamp(orig.x + dx, 0, COLS - orig.w), y: Math.max(0, orig.y + dy) }
      : { ...orig, w: clamp(orig.w + dx, 1, COLS - orig.x), h: clamp(orig.h + dy, 1, 12) };
    place(el, cur);
    $("#board").style.setProperty("--rows", boardRows(cur.y + cur.h + 1));
  };
  const onUp = () => {
    el.removeEventListener("pointermove", onMove); el.removeEventListener("pointerup", onUp); el.removeEventListener("pointercancel", onUp);
    S.dragging = false;
    S.tile = t.id;
    const changed = Object.fromEntries(Object.entries(cur).filter(([k, v]) => v !== orig[k]));
    if (moved && Object.keys(changed).length) updateTile(t.id, changed);
    else { renderBoard(); renderSide(); }
  };
  el.addEventListener("pointermove", onMove); el.addEventListener("pointerup", onUp); el.addEventListener("pointercancel", onUp);
}
// Arrows move the selected tile; Shift + arrows resize it.
function tileKey(e, t) {
  const step = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] }[e.key];
  if (step) {
    e.preventDefault();
    const [dx, dy] = step;
    updateTile(t.id, e.shiftKey ? { w: clamp(t.w + dx, 1, COLS - t.x), h: clamp(t.h + dy, 1, 12) }
      : { x: clamp(t.x + dx, 0, COLS - t.w), y: Math.max(0, t.y + dy) }, 400);
  } else if (e.key === "Delete" || e.key === "Backspace") { e.preventDefault(); removeTile(t.id); }
  else if (e.key === "Escape") { S.tile = null; e.target.blur(); renderBoard(); renderSide(); }
}

/* ---------- side panel: the selected tile, or the dashboard itself ---------- */
function field(label, input) { return h("label", { class: "side-field" }, h("span", { class: "lbl", text: label }), input); }
function renderSide() {
  const side = $("#proto-side"), t = tileOf(S.tile);
  const key = t ? `t${t.id}:${t.kind}:${t.requirement_id}` : `d${S.dash}`;
  side.dataset.key = key;
  if (!S.dash) { side.replaceChildren(); return; }
  if (t) {
    const kind = h("div", { class: "kind-pick", role: "radiogroup", "aria-label": "Tile type" }, TILE_KINDS.map(k =>
      h("button", { class: "btn sm", "aria-pressed": String(k.kind === t.kind), text: k.label, onclick: () => { updateTile(t.id, { kind: k.kind }); renderSide(); } })));
    const measures = dashMeasures();
    const msel = h("select", { class: "select", dataset: { tf: "requirement_id" } },
      h("option", { value: "", text: measures.length ? "Choose a measure" : "No measures on this dashboard yet" }),
      ...measures.map(m => h("option", { value: m.id, text: m.name })));
    msel.value = t.requirement_id ?? "";
    msel.addEventListener("change", () => { updateTile(t.id, { requirement_id: msel.value ? Number(msel.value) : null }); renderSide(); });
    const m = measureOf(t.requirement_id), linked = new Set(m?.dimension_ids || []);
    const own = S.dimensions.filter(d => linked.has(d.id)), rest = S.dimensions.filter(d => !linked.has(d.id));
    const dsel = h("select", { class: "select", dataset: { tf: "dimension_id" } }, h("option", { value: "", text: S.dimensions.length ? "None" : "No dimensions yet (add them on the Bus matrix)" }),
      own.length ? h("optgroup", { label: "This measure's breakdowns" }, own.map(d => h("option", { value: d.id, text: itemName("dimension", d) }))) : null,
      rest.length ? h("optgroup", { label: own.length ? "Other dimensions" : "Dimensions" }, rest.map(d => h("option", { value: d.id, text: itemName("dimension", d) }))) : null);
    dsel.value = t.dimension_id ?? "";
    const fnote = h("p", { class: "small muted filter-note" });
    const setNote = () => { fnote.textContent = t.kind === "filter" ? filterNote(t.dimension_id) : ""; fnote.hidden = !fnote.textContent; };
    setNote();
    dsel.addEventListener("change", () => { updateTile(t.id, { dimension_id: dsel.value ? Number(dsel.value) : null }); setNote(); });
    const title = h("input", { class: "input", value: t.title, placeholder: defaultTitle(t), dataset: { tf: "title" } });
    title.addEventListener("input", () => updateTile(t.id, { title: title.value }, 600));
    const note = h("textarea", { class: "input", rows: "3", placeholder: t.kind === "text" ? "What should this note say?" : "Anything to know: drill-throughs, thresholds, colours.", dataset: { tf: "note" } });
    note.value = t.note;
    note.addEventListener("input", () => updateTile(t.id, { note: note.value }, 600));
    side.replaceChildren(
      h("div", { class: "side-h" }, h("h3", { text: "Tile" }), h("button", { class: "btn ghost sm", text: "Done", onclick: () => { S.tile = null; renderBoard(); renderSide(); } })),
      kind,
      t.kind !== "filter" && t.kind !== "text" ? field("Shows", msel) : null,
      BY_LABEL[t.kind] ? field(BY_LABEL[t.kind], dsel) : null,
      fnote,
      field("Title", title),
      field(t.kind === "text" ? "Note" : "Notes for whoever builds it", note),
      h("button", { class: "btn ghost danger", text: "Remove tile", onclick: () => removeTile(t.id) }));
    return;
  }
  const d = S.dashboards.find(x => x.id === S.dash);
  const placed = new Set(dashTiles().map(x => x.requirement_id));
  const measures = dashMeasures(), v = dashboardView(S.dash), dimNames = ds => ds.map(x => itemName("dimension", x)).join(", ");
  side.replaceChildren(
    h("div", { class: "side-h" }, h("h3", { text: "About this dashboard" })),
    ...USE.map(([k, label, placeholder]) => {
      const el = h("textarea", { class: "input", rows: "2", placeholder, dataset: { use: k } });
      el.value = d[k] || "";
      el.addEventListener("input", () => saveUse(d, k, el.value));
      return field(label, el);
    }),
    h("div", { class: "side-h" }, h("h3", { text: "Measures" })),
    measures.length ? h("ul", { class: "side-measures" }, measures.map(m => h("li", {},
      h("span", {}, h("a", { href: `/w/${SLUG}#m${m.id}`, text: m.name }),
        sharedOn(S.dash, m.id) ? h("small", { class: "muted", text: ` · from ${S.dashboards.find(x => x.id === m.dashboard_id)?.code}` }) : null),
      placed.has(m.id) ? h("span", { class: "muted", text: "In the sketch" }) : h("button", { class: "btn sm", text: "Add", onclick: () => addTile("kpi", m.id) }))))
      : h("p", { class: "muted small" }, "No measures on this dashboard yet. Define them on the ", h("a", { href: `/w/${SLUG}`, text: "Measures" }), " tab."),
    measures.length ? h("div", { class: "side-h" }, h("h3", { text: "Filters" })) : null,
    measures.length ? h("p", { class: "small side-filters" },
      !v.procs.length ? "Link its measures to business processes to see which filters work across it."
        : v.all.length ? `Work on every tile: ${dimNames(v.all)}.` : "No dimension works on every tile yet.",
      v.some.length ? h("span", { class: "muted", text: ` Only some tiles: ${dimNames(v.some.map(x => x.d))}.` }) : null,
      " ", h("a", { href: `/w/${SLUG}/bus-matrix#d${S.dash}`, text: "See it on the bus matrix" })) : null);
}
// For a filter tile: whether it filters every measure on the dashboard.
function filterNote(did) {
  if (did == null) return "";
  const v = dashboardView(S.dash), d = itemOf("dimension", did);
  if (!v.procs.length || v.all.includes(d)) return "";
  const part = v.some.find(x => x.d === d);
  return part ? `Only filters ${part.measures.map(m => m.name).join(", ")}.` : "None of this dashboard's processes are ticked for it on the bus matrix.";
}
// Keep fields in step with the data without rebuilding the one being typed in.
function syncSide() {
  const t = tileOf(S.tile), side = $("#proto-side"), active = document.activeElement;
  const key = t ? `t${t.id}:${t.kind}:${t.requirement_id}` : `d${S.dash}`;
  if (side.dataset.key !== key) return renderSide();
  if (t) $$("[data-tf]", side).forEach(el => { if (el !== active) el.value = t[el.dataset.tf] ?? ""; });
  else {
    const d = S.dashboards.find(x => x.id === S.dash);
    $$("[data-use]", side).forEach(el => { if (el !== active && !useTimers.has(d.id + el.dataset.use)) el.value = d[el.dataset.use] || ""; });
  }
}
const useTimers = new Map();
function saveUse(d, k, value) {
  d[k] = value;
  const key = d.id + k;
  clearTimeout(useTimers.get(key));
  useTimers.set(key, setTimeout(async () => {
    useTimers.delete(key);
    try { await api("PATCH", `${API}/dashboards/${d.id}`, { [k]: value }); } catch (e) { toast("Couldn't save: " + e.message, true); }
  }, 600));
}

/* ---------- page ---------- */
function renderProto() {
  if (!S.dashboards.some(d => d.id === S.dash)) {
    S.dash = (S.dashboards.find(d => S.tiles.some(t => t.dashboard_id === d.id)) || S.dashboards[0])?.id ?? null;
  }
  if (S.tile && !tileOf(S.tile)) S.tile = null;
  $("#proto-nav").replaceChildren(h("div", { class: "proto-nav-h", text: "Dashboards" }), ...S.dashboards.map(d => {
    const n = S.tiles.filter(t => t.dashboard_id === d.id).length;
    return h("button", { class: "proto-dash" + (d.id === S.dash ? " active" : ""), "aria-current": d.id === S.dash ? "true" : null,
      onclick: () => { S.dash = d.id; S.tile = null; store.set("proto:dash", d.id); history.replaceState(null, "", location.pathname); renderProto(); } },
      h("b", { text: d.code }), h("span", { text: d.name || "" }), h("small", { class: "muted", text: n ? plural(n, "tile") : "Not sketched" }));
  }));
  const d = S.dashboards.find(x => x.id === S.dash);
  $("#proto-title").textContent = d ? `${d.code}${d.name ? " · " + d.name : ""}` : "No dashboards yet";
  $("#proto-add").replaceChildren(...(d ? TILE_KINDS.map(k => h("button", { class: "btn", text: "+ " + k.label, onclick: () => addTile(k.kind) })) : []));
  $(".board-wrap").hidden = !d;
  if (d) renderBoard();
  syncSide();
}

$("#board").addEventListener("pointerdown", e => {
  if (e.target === e.currentTarget || e.target.classList.contains("board-cell") || e.target.classList.contains("board-empty")) {
    if (S.tile != null) { S.tile = null; renderBoard(); renderSide(); }
  }
});
page.load = async () => {
  await loadPlanning();
  // Keep unsaved local edits over whatever the server just sent.
  for (const [id, p] of pendingT) Object.assign(tileOf(id) || {}, p);
  for (const d of S.dashboards) for (const k of USE.map(u => u[0])) if (useTimers.has(d.id + k)) d[k] = $(`[data-use="${k}"]`)?.value ?? d[k];
  renderProto();
};
page.render = () => { if (!S.dragging) renderProto(); };
start();
