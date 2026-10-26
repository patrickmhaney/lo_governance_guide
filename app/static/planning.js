"use strict";
/* Shared by the planning pages: their state, and the drawer for a business
   process or a dimension. The drawer works like a measure's: questions saved
   as you type, "I don't know" on any of them, follow-up, complete when every
   question is answered. A process's steps are edited in the drawer too. */

const PLURAL = { process: "processes", dimension: "dimensions" };
const NOUN = { process: "process", dimension: "dimension" };
const SYSTEMS_WHY = "Pick every one that applies.";

const ITEM_Q = {
  process: [
    { key: "name", title: "Process name", why: "What the business calls it.", placeholder: "e.g. Order to Cash" },
    { key: "purpose", type: "textarea", title: "What does it do, and why does it matter?", placeholder: "e.g. Turns customer orders into cash. Every sale starts here." },
    { key: "boundaries", type: "textarea", title: "What starts it, and what ends it?", placeholder: "e.g. Starts when a customer places an order. Ends when their payment is applied." },
    { key: "steps", type: "steps", title: "What are the main steps, and who does each?", why: "Five to eight is plenty. The department doing each step becomes a lane in the swimlane." },
    { key: "event", type: "textarea", title: "What's the smallest thing it records?", why: "Reports can add up from this but never go below it. It sets the level of detail the warehouse keeps.", placeholder: "e.g. One line on a customer order." },
    { key: "systems", type: "systems", title: "Which systems does it run in?", why: SYSTEMS_WHY },
    { key: "volume", type: "text", title: "How often does it happen, and roughly how many?", why: "A rough number is fine.", placeholder: "e.g. About 2,000 order lines a day, busiest at month-end." },
    { key: "decisions", type: "textarea", title: "Which decisions depend on it?", why: "What people decide or act on using it. Dashboards should support these.", placeholder: "e.g. Which customers to chase for payment. Which late orders to expedite." },
    { key: "pain_points", type: "textarea", none: "Nothing major", title: "What's hard about it today?", placeholder: "e.g. Order changes are agreed by email, so the ERP is often behind." },
    { key: "owner", type: "owner", title: "Who owns it?", why: "Accountable for how it runs and for the data it creates." },
    { key: "priority", type: "choice", title: "When should the warehouse cover it?", options: [["now", "Now", "The first release"], ["next", "Next", "The release after"], ["later", "Later", "Not planned yet"]] },
  ],
  dimension: [
    { key: "name", title: "Name", why: "Something the business is measured by: customer, product, site, employee.", placeholder: "e.g. Customer" },
    { key: "definition", type: "textarea", title: "What is it, in the business's words?", placeholder: "e.g. Any company that has placed an order with us in the last five years." },
    { key: "identifier", type: "textarea", title: "How do you tell one from another?", why: "The number or code that identifies one, and whether it differs between systems.", placeholder: "e.g. Customer number in the ERP. The CRM has its own account ID." },
    { key: "attributes", type: "textarea", title: "What do you describe it by?", why: "The details you filter, group or label by.", placeholder: "e.g. Name, segment, account manager, payment terms, region." },
    { key: "hierarchy", type: "textarea", none: "It doesn't roll up", title: "How does it roll up?", placeholder: "e.g. Customer → account tier → region." },
    { key: "systems", type: "systems", title: "Which system holds the master copy?", why: "Where it's created and corrected." },
    { key: "history", type: "choice", title: "When something about it changes, what should past reports show?", why: "For example, a customer moves from the East region to the West.",
      options: [["as_was", "As it was", "Last year's sales stay in East"], ["as_is", "As it is now", "Last year's sales move to West"], ["both", "Both", "Chosen per report"]],
      extra: "history_note", extraPlaceholder: "Optional: which details this applies to" },
    { key: "quality", type: "textarea", none: "No known problems", title: "Any known data problems?", why: "Duplicates, gaps, the same thing typed different ways.", placeholder: "e.g. Some customers exist twice under slightly different names." },
    { key: "owner", type: "owner", title: "Who owns it?", why: "Approves changes to its definition and gets data errors fixed at the source." },
  ],
};

Object.assign(S, { processes: [], dimensions: [], bus: new Set(), dashboards: [], measures: [], placements: [], tiles: [], open: null });

/* ---------- state ---------- */
const busKey = (pid, did) => pid + ":" + did;
const itemsOf = kind => kind === "process" ? S.processes : S.dimensions;
const itemOf = (kind, id) => itemsOf(kind).find(x => x.id === id) || null;
const itemName = (kind, it) => (it.name || "").trim() || (kind === "process" ? "Unnamed process" : "Unnamed dimension");

/* ---------- a dashboard against the bus matrix ---------- */
// The measures on a dashboard: defined there, or shown from another dashboard.
const measuresOn = did => S.placements.filter(p => p.dashboard_id === did)
  .map(p => S.measures.find(m => m.id === p.measure_id)).filter(Boolean);
const sharedOn = (did, mid) => S.placements.some(p => p.dashboard_id === did && p.measure_id === mid && p.shared);
// The processes a dashboard's measures come from, and the dimensions ticked for
// all of them (filters that work on every measure) or only some.
function dashboardView(did) {
  const measures = measuresOn(did), procs = [];
  for (const m of measures) {
    const p = m.process_id != null ? itemOf("process", m.process_id) : null;
    if (!p) continue;
    let e = procs.find(x => x.p === p);
    if (!e) procs.push(e = { p, measures: [] });
    e.measures.push(m);
  }
  procs.sort((a, b) => S.processes.indexOf(a.p) - S.processes.indexOf(b.p));
  const all = [], some = [];
  for (const d of S.dimensions) {
    const on = procs.filter(e => S.bus.has(busKey(e.p.id, d.id)));
    if (on.length && on.length === procs.length) all.push(d);
    else if (on.length) some.push({ d, measures: on.flatMap(e => e.measures) });
  }
  return { measures, procs, all, some, unlinked: measures.filter(m => !procs.some(e => e.measures.includes(m))) };
}

async function loadPlanning() {
  const st = await api("GET", `${API}/planning`);
  setShared(st);
  const merge = kind => x => { const p = pendingI.get(kind + ":" + x.id); return p ? withPatch(kind, x, p) : x; };
  S.processes = st.processes.map(merge("process"));
  S.dimensions = st.dimensions.map(merge("dimension"));
  S.bus = new Set(st.bus.map(([p, d]) => busKey(p, d)));
  S.dashboards = st.dashboards; S.measures = st.measures; S.placements = st.placements; S.tiles = st.tiles;
  S.influence = st.influence || [];
  if (S.open && !itemOf(S.open.kind, S.open.id)) { pendingI.delete(S.open.kind + ":" + S.open.id); closeItem(); }
  renderAll();
  if (S.open) syncItem();
}

/* ---------- answers, as on the server (items.answered) ---------- */
function answeredItem(kind, it) {
  const a = it.answers || {}, unknown = it.unknown || {};
  const has = k => { const v = a[k]; return typeof v === "string" ? !!v.trim() : Array.isArray(v) ? v.length > 0 : !!v; };
  const out = { name: !!(it.name || "").trim() };
  for (const q of ITEM_Q[kind]) {
    if (q.key === "name") continue;
    let ok;
    if (q.key === "owner") ok = it.owner_id != null || it.owner_dept_id != null || !!(it.owner_note || "").trim();
    else if (q.key === "steps") ok = (it.steps || []).some(s => (s.name || "").trim());
    else if (q.key === "systems") ok = has("systems") || has("systems_other");
    else ok = has(q.key) || !!a[q.key + "_none"];
    out[q.key] = ok || q.key in unknown;
  }
  return out;
}
const itemDone = (kind, it) => Object.values(answeredItem(kind, it)).filter(Boolean).length;
// The owning department or person, for display.
function ownerText(it) {
  if (it.owner_dept_id != null) return department(it.owner_dept_id)?.name || "";
  if (it.owner_id != null) return person(it.owner_id)?.name || "";
  return (it.owner_note || "").trim();
}
function meter(kind, it) {
  const ans = answeredItem(kind, it);
  return h("span", { class: "meter", title: `${itemDone(kind, it)} of ${ITEM_Q[kind].length} answered` },
    ITEM_Q[kind].map(q => h("i", { class: q.key in (it.unknown || {}) ? "unk" : ans[q.key] ? "on" : "" })));
}
function statusPill(it) { return h("span", { class: "pill s-" + it.status, text: STATUS[it.status] }); }

/* ---------- saving: one debounced PATCH per item, as on the measures page ---------- */
const pendingI = new Map(), timersI = new Map(), chainsI = new Map();
function withPatch(kind, it, patch) {
  const out = { ...it, ...patch };
  if (patch.answers) out.answers = { ...(it.answers || {}), ...patch.answers };
  out.answered = answeredItem(kind, out);
  return out;
}
function saveItem(kind, id, patch, delay = 600) {
  const key = kind + ":" + id, prev = pendingI.get(key) || {};
  const next = { ...prev, ...patch };
  if (patch.answers) next.answers = { ...(prev.answers || {}), ...patch.answers };
  pendingI.set(key, next);
  const list = itemsOf(kind), i = list.findIndex(x => x.id === id);
  if (i >= 0) list[i] = withPatch(kind, list[i], patch);
  clearTimeout(timersI.get(key));
  timersI.set(key, setTimeout(() => flushItem(kind, id), delay));
  page.render(); syncItemState();
}
function flushItem(kind, id) {
  const key = kind + ":" + id;
  clearTimeout(timersI.get(key)); timersI.delete(key);
  const p = pendingI.get(key); if (!p) return chainsI.get(key) || Promise.resolve();
  pendingI.delete(key);
  const c = (chainsI.get(key) || Promise.resolve())
    .then(() => api("PATCH", `${API}/${PLURAL[kind]}/${id}`, p))
    .then(r => {
      const list = itemsOf(kind), i = list.findIndex(x => x.id === id), local = pendingI.get(key);
      if (i >= 0) list[i] = local ? withPatch(kind, r, local) : r;
      page.render();
      if (S.open && S.open.kind === kind && S.open.id === id) { syncItem(); loadItemActivity(); }
    })
    .catch(e => toast("Couldn't save: " + e.message, true));
  chainsI.set(key, c);
  return c;
}
const flushAllItems = () => Promise.all([...pendingI.keys()].map(k => { const [kind, id] = k.split(":"); return flushItem(kind, Number(id)); }));
window.addEventListener("beforeunload", () => { flushAllItems(); });

async function addItem(kind, name = "") {
  try {
    const it = await api("POST", `${API}/${PLURAL[kind]}`, { name });
    await page.load();
    openItem(kind, it.id);
    return it;
  } catch (e) { toast(`Couldn't add a ${NOUN[kind]}: ` + e.message, true); }
}

/* ---------- the drawer ---------- */
function openItem(kind, id, focusKey) {
  if (S.open && (S.open.kind !== kind || S.open.id !== id)) flushItem(S.open.kind, S.open.id);
  S.open = { kind, id };
  const it = itemOf(kind, id), cur = () => itemOf(kind, id);
  const root = $("#drawer-inner");
  const plain = (field, el) => {
    el.dataset.field = field;
    el.addEventListener("input", () => saveItem(kind, id, { [field]: el.value }));
    el.addEventListener("blur", () => flushItem(kind, id));
    return el;
  };
  const answer = (key, el) => {
    el.dataset.answer = key;
    el.addEventListener("input", () => saveItem(kind, id, { answers: { [key]: el.value } }));
    el.addEventListener("blur", () => flushItem(kind, id));
    return el;
  };
  const noneBox = (q) => {
    const box = h("input", { type: "checkbox", dataset: { answer: q.key + "_none", kind: "check" } });
    box.addEventListener("change", () => saveItem(kind, id, { answers: { [q.key + "_none"]: box.checked } }, 100));
    return h("label", { class: "check" }, box, q.none);
  };
  const dontKnow = (key) => {
    const box = h("input", { type: "checkbox", dataset: { unknown: key } });
    const who = h("input", { class: "input", id: `d-who-${key}`, placeholder: "Who might know? (optional)", "aria-label": "Who might know" });
    const whoWrap = h("div", { class: "who", hidden: true }, who);
    const set = value => { const u = { ...(cur().unknown || {}) }; if (value === null) delete u[key]; else u[key] = value; return u; };
    box.addEventListener("change", () => {
      whoWrap.hidden = !box.checked;
      saveItem(kind, id, { unknown: set(box.checked ? who.value : null) }, 100);
      if (box.checked) who.focus();
    });
    who.addEventListener("input", () => saveItem(kind, id, { unknown: set(who.value) }));
    who.addEventListener("blur", () => flushItem(kind, id));
    return h("div", { class: "dont-know" }, h("label", { class: "check" }, box, "I don't know"), whoWrap);
  };
  const bodyFor = q => {
    switch (q.type) {
      case undefined: return [plain("name", h("input", { class: "input", id: "d-title", placeholder: q.placeholder, "aria-label": q.title }))];
      case "text": return [answer(q.key, h("input", { class: "input", placeholder: q.placeholder }))];
      case "textarea": return [answer(q.key, h("textarea", { class: "input", rows: "2", placeholder: q.placeholder })), q.none ? noneBox(q) : null];
      case "systems": return [h("div", { class: "chips", id: "d-systems", role: "group", "aria-label": "Systems" }),
        answer("systems_other", h("input", { class: "input", placeholder: "Another system, a spreadsheet, a vendor portal…" }))];
      case "owner": {
        const sel = h("select", { class: "select", id: "d-owner", dataset: { kind: "owner" } });
        const note = plain("owner_note", h("input", { class: "input", placeholder: "Name and role, e.g. Jordan Lee, Director of Logistics" }));
        const wrap = h("div", { id: "d-owner-note-wrap", hidden: true }, note);
        sel.addEventListener("change", () => {
          const v = sel.value;
          if (v === "other") { wrap.hidden = false; saveItem(kind, id, { owner_id: null, owner_dept_id: null }, 0); note.focus(); return; }
          wrap.hidden = true;
          saveItem(kind, id, { owner_id: v.startsWith("p:") ? Number(v.slice(2)) : null, owner_dept_id: v.startsWith("d:") ? Number(v.slice(2)) : null, owner_note: "" }, 0);
        });
        return [sel, wrap];
      }
      case "choice": return [h("div", { class: "radios", role: "radiogroup", "aria-label": q.title }, q.options.map(([v, t, sub]) => {
          const inp = h("input", { type: "radio", name: "d-" + q.key, value: v, id: `d-${q.key}-${v}`, dataset: { answer: q.key, kind: "radio" } });
          inp.addEventListener("change", () => { if (inp.checked) saveItem(kind, id, { answers: { [q.key]: v } }, 100); });
          return h("label", { class: "radio", for: `d-${q.key}-${v}` }, inp, h("span", {}, h("b", { text: t }), h("small", { text: sub })));
        })), q.extra ? answer(q.extra, h("input", { class: "input", placeholder: q.extraPlaceholder })) : null];
      case "steps": return [h("div", { class: "steps-edit", id: "d-steps" })];
    }
  };
  const assign = h("select", { class: "select", id: "d-assignee", dataset: { field: "assignee_id", kind: "person" } });
  assign.addEventListener("change", () => {
    const v = assign.value ? Number(assign.value) : null;
    saveItem(kind, id, { assignee_id: v }, 0);
    toast(v ? `Assigned to ${person(v).name}` : "Unassigned");
  });
  const flag = h("input", { type: "checkbox", id: "d-followup", dataset: { field: "followup", kind: "check" } });
  flag.addEventListener("change", () => saveItem(kind, id, { followup: flag.checked }, 100));
  const list = itemsOf(kind), idx = list.findIndex(x => x.id === id);
  const moveBtn = (dir, label) => h("button", { class: "btn ghost sm", title: label, "aria-label": label, disabled: idx + dir < 0 || idx + dir >= list.length,
    onclick: () => moveItem(kind, id, idx + dir), text: dir < 0 ? "↑" : "↓" });

  root.replaceChildren(
    h("div", { class: "d-top" }, h("span", { class: "d-crumb", text: kind === "process" ? "Business process" : "Dimension" }),
      h("span", { class: "d-top-tools" }, moveBtn(-1, "Move earlier"), moveBtn(1, "Move later"),
        h("button", { class: "x", "aria-label": "Close", onclick: closeItem, text: "×" }))),
    h("div", { class: "d-meta" }, h("span", { class: "pill", id: "d-pill" }), h("span", { id: "d-updated" })),
    h("div", { class: "panel" },
      h("div", {}, h("label", { class: "lbl", for: "d-assignee", text: "Assigned to" }), assign),
      h("div", { class: "complete-row" }, h("button", { class: "btn ok", id: "d-complete", onclick: () => toggleItemComplete(kind, id) }), h("span", { class: "hint", id: "d-hint" })),
      h("div", { class: "full" }, h("label", { class: "check flagbox" }, flag, `Flag ${NOUN[kind]} for follow-up`),
        h("p", { class: "flag-hint", text: "Questions with “I don't know” checked will also flag it for follow-up." })),
      h("div", { class: "full", id: "d-followup-wrap" }, plain("followup_note", h("input", { class: "input", placeholder: "What needs following up, and with whom?" })))),
    ...ITEM_Q[kind].map((q, i) => h("section", { class: "q", id: "qs-" + q.key },
      h("div", { class: "q-num", id: "q-" + q.key, text: String(i + 1).padStart(2, "0") }),
      h("div", {}, h("h3", { text: q.title }), q.why ? h("p", { class: "why", text: q.why }) : null),
      h("div", { class: "q-body" }, bodyFor(q), q.key === "name" ? null : dontKnow(q.key)))),
    h("div", { class: "section-h", text: "Notes for the project team" }),
    plain("notes", h("textarea", { class: "input", rows: "2", placeholder: "Anything you're unsure about, or context we should know." })),
    h("div", { class: "d-danger" }, h("button", { class: "btn ghost danger", text: `Delete ${NOUN[kind]}`, onclick: () => deleteItem(kind, id) })),
    h("div", { class: "section-h", text: "Activity" }),
    h("ul", { class: "log", id: "d-log" }),
  );
  syncItem();
  loadItemActivity();
  $("#drawer").classList.add("open"); $("#drawer").setAttribute("aria-hidden", "false");
  $("#scrim").classList.add("open");
  page.render();
  if (focusKey) $("#qs-" + focusKey)?.scrollIntoView({ block: "start" });
  else if (!(it.name || "").trim()) $("#d-title").focus();
}
function closeItem() {
  if (!S.open) return;
  flushItem(S.open.kind, S.open.id);
  S.open = null;
  $("#drawer").classList.remove("open"); $("#drawer").setAttribute("aria-hidden", "true");
  $("#scrim").classList.remove("open");
  page.render();
}
function syncItem() {
  if (!S.open) return;
  const { kind, id } = S.open, it = itemOf(kind, id), p = pendingI.get(kind + ":" + id) || {}, pa = p.answers || {};
  const root = $("#drawer-inner"), active = document.activeElement, a = it.answers || {};
  // systems chips
  const wrap = $("#d-systems", root);
  if (wrap && !("systems" in pa)) {
    const chosen = a.systems || [];
    const all = [...S.sources, ...chosen.filter(s => !S.sources.includes(s))];
    const key = all.join("\u0001") + "|" + chosen.join("\u0001");
    if (wrap.dataset.key !== key) {
      wrap.dataset.key = key;
      wrap.replaceChildren(...(all.length ? all.map((s, i) => {
        const inp = h("input", { type: "checkbox", id: "d-sys-" + i, value: s, checked: chosen.includes(s) });
        inp.addEventListener("change", () => saveItem(kind, id, { answers: { systems: $$("#d-systems input").filter(x => x.checked).map(x => x.value) } }, 250));
        return h("label", { class: "chip", for: "d-sys-" + i }, inp, h("span", { text: s }));
      }) : [h("span", { class: "muted small", text: "No source systems listed yet. Add them under Source systems on the Measures tab, or type one below." })]));
    }
  }
  const assign = $("#d-assignee", root), av = assign.value;
  assign.replaceChildren(h("option", { value: "", text: "Unassigned" }), ...S.people.map(x => h("option", { value: x.id, text: x.name + (x.role ? ` · ${x.role}` : "") })));
  assign.value = av;
  const owner = $("#d-owner", root);
  if (owner && owner !== active) {
    owner.replaceChildren(...whoOptions("Choose a department or person", [h("option", { value: "other", text: "Someone not listed…" })]));
    const v = it.owner_dept_id != null ? "d:" + it.owner_dept_id : it.owner_id != null ? "p:" + it.owner_id : (it.owner_note || "").trim() ? "other" : "";
    if (!("owner_id" in p) && !("owner_dept_id" in p)) owner.value = v;
    $("#d-owner-note-wrap").hidden = owner.value !== "other";
  }
  if (!("unknown" in p)) $$("[data-unknown]", root).forEach(box => {
    const k = box.dataset.unknown, on = k in (it.unknown || {}), who = $(`#d-who-${k}`, root);
    box.checked = on; who.parentElement.hidden = !on;
    if (who !== active) who.value = on ? it.unknown[k] : "";
  });
  $$("[data-field]", root).forEach(el => {
    const f = el.dataset.field;
    if (el === active || f in p) return;
    if (el.dataset.kind === "check") el.checked = !!it[f];
    else if (el.dataset.kind === "person") el.value = it[f] ?? "";
    else if (el.value !== (it[f] || "")) el.value = it[f] || "";
  });
  $$("[data-answer]", root).forEach(el => {
    const k = el.dataset.answer;
    if (el === active || k in pa) return;
    if (el.dataset.kind === "check") el.checked = !!a[k];
    else if (el.dataset.kind === "radio") el.checked = el.value === a[k];
    else if (el.value !== (a[k] || "")) el.value = a[k] || "";
  });
  if (kind === "process") renderStepsEdit(it);
  $("#d-updated").textContent = it.updated_at ? `Last edited ${rel(it.updated_at)}${person(it.updated_by) ? " by " + person(it.updated_by).name : ""}` : "Not started yet";
  syncItemState();
}
function syncItemState() {
  if (!S.open) return;
  const { kind, id } = S.open, it = itemOf(kind, id), ans = answeredItem(kind, it), n = itemDone(kind, it), total = ITEM_Q[kind].length;
  const pill = $("#d-pill"); pill.className = "pill s-" + it.status; pill.textContent = STATUS[it.status];
  ITEM_Q[kind].forEach((q, i) => {
    const el = $("#q-" + q.key), unk = q.key in (it.unknown || {});
    el.classList.toggle("done", ans[q.key] && !unk); el.classList.toggle("unk", unk);
    el.textContent = unk ? "?" : ans[q.key] ? "✓" : String(i + 1).padStart(2, "0");
  });
  $("#d-followup-wrap").hidden = !it.followup;
  const btn = $("#d-complete"), hint = $("#d-hint");
  if (it.status === "complete") { btn.textContent = "Reopen"; btn.className = "btn"; btn.disabled = false; hint.textContent = "Complete" + (it.completed_at ? " " + rel(it.completed_at) : ""); }
  else { btn.textContent = "Mark complete"; btn.className = "btn ok"; btn.disabled = n < total; hint.textContent = n < total ? `${total - n} left.` : "Every question answered."; }
}
async function toggleItemComplete(kind, id) {
  await flushItem(kind, id);
  const it = itemOf(kind, id), status = it.status === "complete" ? "in_progress" : "complete";
  try {
    const out = await api("POST", `${API}/${PLURAL[kind]}/${id}/status`, { status });
    const list = itemsOf(kind), i = list.findIndex(x => x.id === id); if (i >= 0) list[i] = out;
    page.render(); syncItem(); loadItemActivity();
    toast(status === "complete" ? "Marked complete. Thank you!" : "Reopened");
  } catch (e) { toast(e.message, true); }
}
async function loadItemActivity() {
  if (!S.open) return;
  const { kind, id } = S.open;
  try {
    const rows = await api("GET", `${API}/${PLURAL[kind]}/${id}/activity`);
    if (!S.open || S.open.id !== id || S.open.kind !== kind) return;
    $("#d-log").replaceChildren(...(rows.length ? rows.map(a => h("li", {}, h("span", { text: `${a.detail}${a.actor ? " · " + a.actor : ""}` }), h("span", { text: rel(a.at) })))
      : [h("li", {}, h("span", { class: "muted", text: "No changes yet." }))]));
  } catch {}
}
async function deleteItem(kind, id) {
  const it = itemOf(kind, id);
  if (!confirm(`Delete ${itemName(kind, it)}? It goes to the archive, where you can restore it with everything linked to it.`)) return;
  await flushItem(kind, id);
  closeItem();
  try { await api("DELETE", `${API}/${PLURAL[kind]}/${id}`); await page.load(); toast(`Deleted ${itemName(kind, it)}. Restore it from Archive.`); }
  catch (e) { toast("Couldn't delete: " + e.message, true); }
}
async function moveItem(kind, id, position) {
  await flushItem(kind, id);
  try { await api("POST", `${API}/${PLURAL[kind]}/${id}/move`, { position }); await page.load(); openItem(kind, id); }
  catch (e) { toast("Couldn't move: " + e.message, true); }
}

/* ---------- process steps, edited in the drawer ---------- */
function renderStepsEdit(proc) {
  const wrap = $("#d-steps"); if (!wrap) return;
  const key = JSON.stringify([proc.steps, S.departments, S.people.map(p => [p.id, p.name]), S.sources]);
  if (wrap.dataset.key === key || wrap.contains(document.activeElement) && document.activeElement.tagName === "INPUT") return;
  wrap.dataset.key = key;
  const call = async (method, path, body) => {
    try { await api(method, path, body); await page.load(); } catch (e) { toast("Couldn't save the step: " + e.message, true); }
  };
  const steps = proc.steps || [];
  wrap.replaceChildren(
    ...steps.map((s, i) => {
      const name = h("input", { class: "input", value: s.name, placeholder: "e.g. Take the order", "aria-label": `Step ${i + 1}` });
      name.addEventListener("change", () => call("PATCH", `${API}/steps/${s.id}`, { name: name.value }));
      name.addEventListener("keydown", e => { if (e.key === "Enter") name.blur(); });
      const who = h("select", { class: "select", "aria-label": "Who does it" }, ...whoOptions("Who does it?"));
      who.value = s.dept_id != null ? "d:" + s.dept_id : s.person_id != null ? "p:" + s.person_id : "";
      who.addEventListener("change", () => call("PATCH", `${API}/steps/${s.id}`, {
        dept_id: who.value.startsWith("d:") ? Number(who.value.slice(2)) : null,
        person_id: who.value.startsWith("p:") ? Number(who.value.slice(2)) : null }));
      const systems = [...S.sources, ...(s.system && !S.sources.includes(s.system) ? [s.system] : [])];
      const sys = h("select", { class: "select", "aria-label": "System" }, h("option", { value: "", text: "System" }), ...systems.map(x => h("option", { value: x, text: x })));
      sys.value = s.system;
      sys.addEventListener("change", () => call("PATCH", `${API}/steps/${s.id}`, { system: sys.value }));
      return h("div", { class: "step-row" },
        h("span", { class: "step-n", text: String(i + 1) }), name, who, sys,
        h("span", { class: "step-tools" },
          h("button", { class: "btn ghost sm", title: "Move up", "aria-label": "Move step up", disabled: i === 0, text: "↑", onclick: () => call("POST", `${API}/steps/${s.id}/move`, { position: i - 1 }) }),
          h("button", { class: "btn ghost sm", title: "Move down", "aria-label": "Move step down", disabled: i === steps.length - 1, text: "↓", onclick: () => call("POST", `${API}/steps/${s.id}/move`, { position: i + 1 }) }),
          h("button", { class: "btn ghost sm danger", title: "Remove step", "aria-label": "Remove step", text: "×", onclick: () => call("DELETE", `${API}/steps/${s.id}`) })));
    }),
    h("button", { class: "btn add-step", text: "+ Add a step", onclick: async () => {
      try {
        await api("POST", `${API}/processes/${proc.id}/steps`, {});
        await page.load();
        const inputs = $$("#d-steps .step-row input"); inputs[inputs.length - 1]?.focus();
      } catch (e) { toast("Couldn't add a step: " + e.message, true); }
    } }),
  );
}

$("#scrim").addEventListener("click", closeItem);
document.addEventListener("keydown", e => { if (e.key === "Escape" && S.open && !document.querySelector("dialog[open]")) closeItem(); });
page.load = loadPlanning;
