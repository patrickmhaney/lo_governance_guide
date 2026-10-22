"use strict";

const SLUG = document.body.dataset.slug;
const API = `/api/w/${SLUG}`;

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
function h(tag, props, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (v == null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "text") el.textContent = v;
    else if (k === "value") el.value = v;
    else if (k === "checked") el.checked = !!v;
    else if (k === "dataset") Object.assign(el.dataset, v);
    else if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const c of kids.flat()) { if (c == null || c === false) continue; el.append(c.nodeType ? c : document.createTextNode(String(c))); }
  return el;
}
const store = {
  get(k) { try { return localStorage.getItem(`mreq:${SLUG}:${k}`); } catch { return null; } },
  set(k, v) { try { v == null ? localStorage.removeItem(`mreq:${SLUG}:${k}`) : localStorage.setItem(`mreq:${SLUG}:${k}`, v); } catch {} },
};

const STATUS = { not_started: "Not started", in_progress: "In progress", complete: "Complete" };
const REFRESH = [
  ["monthly", "Monthly", "After month-end close"],
  ["weekly", "Weekly", "Once a week"],
  ["daily", "Daily", "Fresh each morning"],
  ["intraday", "Intraday", "Several times a day"],
];
const Q = [
  { key: "name", title: "Measure name", why: "The name people in the business know it by." },
  { key: "definition", title: "What does this measure tell you about the business and how is it defined?", why: "In plain words. This becomes its glossary entry." },
  { key: "formula", title: "How is this measure calculated?", why: "A formula, or describe it in words. Two reasonable definitions can give very different numbers." },
  { key: "sources", title: "Which source application(s) are the system of record for each input to this calculation?", why: "Pick every one you think it touches. Guessing is fine; we confirm it on our side." },
  { key: "target", title: "Does it have a benchmark, target, or threshold? If so, who sets it, and where is it currently kept?", why: "So the dashboard can show the number against the goal it's measured by." },
  { key: "report", title: "Is this measure currently used by the business and is it a trusted source for validation testing? If available, please share the trusted report or spreadsheet.", why: "We test the new numbers against it, so you can trust them from day one." },
  { key: "breakdowns", title: "Which dimensional rollups do you need to see it by? What's the lowest level of detail you need?", why: "For example by product, customer, region, rolling up to product family or territory. This tells us what the data model has to support.", note: "This doesn't need to be 100% complete. We can build on the initial answer as needed." },
  { key: "time", title: "Which date drives it, and do you use fiscal or calendar periods? Which time rollups or comparisons do you need (MTD, YTD, Prior Year, etc.)?", why: "For example order date or ship date, fiscal months, with MTD and prior year.", note: "This doesn't need to be 100% complete. We can build on the initial answer as needed." },
  { key: "refresh", title: "How often should this number refresh (Daily, Weekly, Monthly, etc.)?", why: "Match it to how often the number is checked or acted on. We size the pipeline to fit." },
  { key: "restatement", title: "Can past numbers change after the fact? If so, how should that be handled?", why: "For example late invoices, returns or reclassifications. Decide whether history is restated or kept as first reported." },
  { key: "owner", title: "Who owns the definition and approves changes to it?", why: "When a definition question comes up, this is who decides. It doesn't have to be you." },
  { key: "sme", title: "Who is the subject matter expert to contact for questions?", why: "The person who knows how the number works day to day. It can be the owner." },
];

const S = {
  dashboards: [], people: [], reqs: new Map(), sources: [], slots: 4, maxSlots: 12, archived: 0,
  me: store.get("me") ? Number(store.get("me")) : null,
  filter: "all", open: null,
};

/* ---------- api ---------- */
async function api(method, path, body) {
  const res = await fetch(path, {
    method,
    headers: { "Content-Type": "application/json", ...(S.me ? { "X-Actor": String(S.me) } : {}) },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok) {
    let msg = `${res.status}`;
    try { const j = await res.json(); msg = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail); } catch {}
    throw new Error(msg);
  }
  return res.status === 204 ? null : res.json();
}
let toastT;
function toast(msg, err) {
  const t = $("#toast"); t.textContent = msg; t.classList.toggle("err", !!err); t.classList.add("on");
  clearTimeout(toastT); toastT = setTimeout(() => t.classList.remove("on"), 3500);
}

async function load() {
  const st = await api("GET", `${API}/state`);
  S.dashboards = st.dashboards; S.people = st.people; S.sources = st.sources; S.slots = st.slots; S.maxSlots = st.max_slots; S.archived = st.archived;
  // Rebuilt each time, so cells in rows or columns deleted elsewhere drop out.
  S.reqs = new Map(st.requirements.map(r => [r.id, pending.has(r.id) ? { ...r, ...pending.get(r.id) } : r]));
  if (S.open && !S.reqs.has(S.open)) { pending.delete(S.open); closeDrawer(); }
  if (S.me && !S.people.some(p => p.id === S.me)) { S.me = null; store.set("me", null); }
  renderAll();
}

/* ---------- saving: one debounced PATCH per requirement ---------- */
const pending = new Map(), timers = new Map(), chains = new Map();
function save(id, patch, delay = 600) {
  pending.set(id, { ...(pending.get(id) || {}), ...patch });
  Object.assign(S.reqs.get(id), patch);
  clearTimeout(timers.get(id));
  timers.set(id, setTimeout(() => flush(id), delay));
  renderGrid(); syncState();
}
function flush(id) {
  clearTimeout(timers.get(id)); timers.delete(id);
  const p = pending.get(id); if (!p) return chains.get(id) || Promise.resolve();
  pending.delete(id);
  const c = (chains.get(id) || Promise.resolve())
    .then(() => api("PATCH", `${API}/requirements/${id}`, p))
    .then(r => {
      const local = pending.get(id);
      S.reqs.set(id, local ? { ...r, ...local } : r);
      renderGrid(); renderSummary();
      if (S.open === id) { syncDrawer(); loadActivity(); }
    })
    .catch(e => toast("Couldn't save: " + e.message, true));
  chains.set(id, c);
  return c;
}
window.addEventListener("beforeunload", () => { for (const id of [...pending.keys()]) flush(id); });

/* ---------- helpers ---------- */
const person = id => S.people.find(p => p.id === id) || null;
// "I don't know" counts as answered; those get followed up by hand.
const unknownOf = r => r.unknown || {};
function answered(r) {
  const has = k => !!(r[k] || "").trim();
  const ans = {
    name: has("name"),
    definition: has("description"),
    formula: has("formula"),
    sources: (r.sources || []).length > 0 || has("source_other"),
    target: has("target") || !!r.target_none,
    report: has("report") || !!r.report_none,
    breakdowns: has("breakdowns") || !!r.breakdowns_same,
    time: has("time_basis"),
    refresh: has("refresh"),
    restatement: has("restatement") || !!r.restatement_none,
    owner: r.owner_id != null || has("owner_note"),
    sme: r.sme_id != null || has("sme_note"),
  };
  for (const k in unknownOf(r)) ans[k] = true;
  return ans;
}
const doneCount = r => Object.values(answered(r)).filter(Boolean).length;
function rel(iso) {
  if (!iso) return "";
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 60) return "just now";
  if (s < 3600) return Math.floor(s / 60) + " min ago";
  if (s < 86400) return Math.floor(s / 3600) + " h ago";
  if (s < 604800) return Math.floor(s / 86400) + " d ago";
  return new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}
function reqAt(dashId, slot) { for (const r of S.reqs.values()) if (r.dashboard_id === dashId && r.slot === slot) return r; return null; }
function matches(r) {
  if (S.filter === "mine") return S.me != null && r.assignee_id === S.me;
  if (S.filter === "followup") return r.followup;
  if (S.filter === "unassigned") return !!(r.name || "").trim() && !person(r.assignee_id);
  if (S.filter === "unknown") return Object.keys(unknownOf(r)).length > 0;
  return true;
}

/* ---------- header ---------- */
function renderSummary() {
  $("#btn-archive").textContent = S.archived ? `Archive (${S.archived})` : "Archive";
  const defined = [...S.reqs.values()].filter(r => (r.name || "").trim());
  const c = { not_started: 0, in_progress: 0, complete: 0 };
  defined.forEach(r => c[r.status]++);
  const n = defined.length, follow = defined.filter(r => r.followup).length;
  $("#prog-big").replaceChildren(String(c.complete), " ", h("small", { text: `of ${n} defined measure${n === 1 ? "" : "s"} complete` }));
  $("#bar-ok").style.width = n ? `${c.complete / n * 100}%` : "0";
  $("#bar-warn").style.width = n ? `${c.in_progress / n * 100}%` : "0";
  $("#legend").replaceChildren(
    h("span", {}, h("i", { style: "background:var(--ok)" }), `${c.complete} complete`),
    h("span", {}, h("i", { style: "background:var(--warn)" }), `${c.in_progress} in progress`),
    h("span", {}, h("i", { style: "background:var(--line-2)" }), `${c.not_started} not started`),
    h("span", {}, h("i", { style: "background:var(--flag)" }), `${follow} need follow-up`),
    h("span", { class: "muted", text: `${S.dashboards.length * S.slots - n} empty slots` }),
  );
  const me = $("#me");
  me.replaceChildren(h("option", { value: "", text: "Choose your name" }), ...S.people.map(p => h("option", { value: p.id, text: p.name })));
  me.value = S.me ?? "";
}

/* ---------- grid ---------- */
function renderGrid() {
  const g = $("#grid");
  g.style.setProperty("--slots", S.slots);
  const focused = document.activeElement?.dataset?.dash ? { ...document.activeElement.dataset } : null;
  const kids = [h("div", { class: "col-h", text: "Dashboard" })];
  for (let s = 1; s <= S.slots; s++) kids.push(h("div", { class: "col-h" }, h("span", { text: `Measure ${s}` }),
    S.slots > 1 ? h("button", { class: "del", title: `Delete Measure ${s}`, "aria-label": `Delete measure column ${s}`, onclick: () => deleteSlot(s), text: "×" }) : null));
  kids.push(S.slots < S.maxSlots ? h("button", { class: "add-col", title: "Add a measure", "aria-label": "Add a measure column", onclick: addSlot, text: "+" }) : h("div"));
  for (const d of S.dashboards) {
    const rowReqs = [...S.reqs.values()].filter(r => r.dashboard_id === d.id && (r.name || "").trim());
    kids.push(h("div", { class: "row-h" },
      h("button", { class: "del", title: `Delete ${d.code}`, "aria-label": `Delete dashboard ${d.code}`, onclick: () => deleteDashboard(d), text: "×" }),
      dashInput(d, "code", { class: "dcode", maxlength: "12", "aria-label": "Dashboard code" }),
      dashInput(d, "name", { class: "dname", placeholder: "Add a name", "aria-label": `Name for ${d.code}` }),
      h("span", { class: "rcount", text: `${rowReqs.filter(r => r.status === "complete").length} of ${rowReqs.length} complete` })));
    for (let s = 1; s <= S.slots; s++) {
      const r = reqAt(d.id, s); if (!r) { kids.push(h("div")); continue; }
      const empty = !(r.name || "").trim();
      const who = person(r.assignee_id);
      const ans = answered(r);
      const cls = ["cell", "s-" + r.status, empty && "empty", S.open === r.id && "active", S.filter !== "all" && !matches(r) && "dim"].filter(Boolean).join(" ");
      kids.push(h("button", { class: cls, role: "gridcell", onclick: () => openDrawer(r.id), "aria-label": `${d.code} measure ${s}: ${empty ? "empty" : r.name}` },
        h("span", { class: "c-name", text: empty ? "+ Define a measure" : r.name }),
        empty && !who ? null : h("span", { class: "c-who", text: who ? (who.id === S.me ? "You" : who.name) : "Unassigned" }),
        empty ? null : h("span", { class: "c-foot" },
          h("span", { class: "pill s-" + r.status, text: STATUS[r.status] }),
          r.followup ? h("span", { class: "flag", text: "Follow-up" }) : h("span", { class: "meter", title: `${doneCount(r)} of ${Q.length} answered` }, Q.map(q => h("i", { class: q.key in unknownOf(r) ? "unk" : ans[q.key] ? "on" : "" })))),
      ));
    }
    kids.push(h("div"));  // under the add-measure column
  }
  kids.push(h("button", { class: "add-row", title: "Add a dashboard", "aria-label": "Add a dashboard", onclick: addDashboard, text: "+" }));
  g.replaceChildren(...kids);
  if (focused) $(`[data-dash="${focused.dash}"][data-part="${focused.part}"]`)?.focus();
}
// The code and name of a dashboard row, edited in place.
function dashInput(d, part, props) {
  const el = h("input", { ...props, value: d[part], dataset: { dash: d.id, part } });
  el.addEventListener("change", async () => {
    try { const r = await api("PATCH", `${API}/dashboards/${d.id}`, { [part]: el.value }); d[part] = el.value = r[part]; toast(part === "code" ? "Code updated" : "Dashboard renamed"); }
    catch (e) { el.value = d[part]; toast("Couldn't save: " + e.message, true); }
  });
  el.addEventListener("keydown", e => { if (e.key === "Enter") el.blur(); if (e.key === "Escape") { el.value = d[part]; el.blur(); } });
  return el;
}
async function addDashboard() {
  try {
    const d = await api("POST", `${API}/dashboards`, {});
    await load();
    const code = $(`[data-dash="${d.id}"][data-part="code"]`);
    code?.focus(); code?.select();
  } catch (e) { toast("Couldn't add a dashboard: " + e.message, true); }
}
// Deleting moves the row or column to the archive, so a confirm is enough.
const measuresIn = pred => [...S.reqs.values()].filter(r => pred(r) && (r.name || "").trim()).length;
const nMeasures = n => `${n} measure${n === 1 ? "" : "s"}`;
async function deleteDashboard(d) {
  const n = measuresIn(r => r.dashboard_id === d.id);
  if (!confirm(`Delete ${d.code}${n ? ` and its ${nMeasures(n)}` : ""}? It goes to the archive, where you can restore it.`)) return;
  const open = S.open && S.reqs.get(S.open);
  if (open && open.dashboard_id === d.id) closeDrawer();
  await Promise.all([...S.reqs.values()].filter(r => r.dashboard_id === d.id).map(r => flush(r.id)));
  try { await api("DELETE", `${API}/dashboards/${d.id}`); await load(); toast(`Deleted ${d.code}. Restore it from Archive.`); }
  catch (e) { toast("Couldn't delete: " + e.message, true); }
}
async function deleteSlot(slot) {
  const n = measuresIn(r => r.slot === slot);
  if (!confirm(`Delete Measure ${slot}${n ? `, with the ${nMeasures(n)} in that column` : ""}? It goes to the archive, where you can restore it.`)) return;
  if (S.open) closeDrawer();
  await Promise.all([...pending.keys()].map(flush));
  try { await api("DELETE", `${API}/slots/${slot}`); await load(); toast(`Deleted Measure ${slot}. Restore it from Archive.`); }
  catch (e) { toast("Couldn't delete: " + e.message, true); }
}
async function addSlot() {
  try {
    await api("POST", `${API}/slots`);
    await load();
    const wrap = $(".grid-wrap"); wrap.scrollLeft = wrap.scrollWidth;
  } catch (e) { toast("Couldn't add a measure: " + e.message, true); }
}

/* ---------- drawer ---------- */
function peopleOptions(sel, emptyLabel, extra) {
  const cur = sel.value;
  sel.replaceChildren(h("option", { value: "", text: emptyLabel }), ...S.people.map(p => h("option", { value: p.id, text: p.name + (p.role ? ` · ${p.role}` : "") })), ...(extra || []));
  sel.value = cur;
}
function bindText(el, id, field) {
  el.dataset.field = field;
  el.addEventListener("input", () => save(id, { [field]: el.value }));
  el.addEventListener("blur", () => { if (pending.has(id)) flush(id); });
  return el;
}
function bindCheck(el, id, field) {
  el.dataset.field = field; el.dataset.kind = "check";
  el.addEventListener("change", () => save(id, { [field]: el.checked }, 100));
  return el;
}
// A person from the roster, or "Someone not listed…" with a typed name.
// `key` is "owner" or "sme", backed by `${key}_id` and `${key}_note`.
// "I don't know", with an optional "who might know" once it's ticked.
function dontKnow(id, key) {
  const box = h("input", { type: "checkbox", id: `d-unk-${key}`, dataset: { unknown: key } });
  const who = h("input", { class: "input", id: `d-who-${key}`, placeholder: "Who might know? (optional)", "aria-label": "Who might know" });
  const whoWrap = h("div", { class: "who", hidden: true }, who);
  const set = value => {
    const u = { ...unknownOf(S.reqs.get(id)) };
    if (value === null) delete u[key]; else u[key] = value;
    return u;
  };
  box.addEventListener("change", () => {
    whoWrap.hidden = !box.checked;
    save(id, { unknown: set(box.checked ? who.value : null) }, 100);
    if (box.checked) who.focus();
  });
  who.addEventListener("input", () => save(id, { unknown: set(who.value) }));
  who.addEventListener("blur", () => { if (pending.has(id)) flush(id); });
  return h("div", { class: "dont-know" }, h("label", { class: "check" }, box, "I don't know"), whoWrap);
}
function personPicker(id, key, notePlaceholder) {
  const sel = h("select", { class: "select", id: `d-${key}`, dataset: { field: `${key}_id`, kind: "person-note", key } });
  const note = bindText(h("input", { class: "input", id: `d-${key}-note`, placeholder: notePlaceholder }), id, `${key}_note`);
  const wrap = h("div", { id: `d-${key}-note-wrap`, hidden: true }, note);
  sel.addEventListener("change", () => {
    if (sel.value === "other") { wrap.hidden = false; save(id, { [`${key}_id`]: null }, 0); note.focus(); }
    else save(id, { [`${key}_id`]: sel.value ? Number(sel.value) : null, [`${key}_note`]: "" }, 0);
  });
  return [sel, wrap];
}
function openDrawer(id) {
  if (S.open && S.open !== id) flush(S.open);
  S.open = id;
  const r = S.reqs.get(id), d = S.dashboards.find(x => x.id === r.dashboard_id);
  const root = $("#drawer-inner");

  const title = bindText(h("input", { class: "input", id: "d-title", placeholder: "e.g. On-Time Delivery", "aria-label": "Measure name" }), id, "name");
  const assign = h("select", { class: "select", id: "d-assignee", dataset: { field: "assignee_id", kind: "person" } });
  assign.addEventListener("change", () => {
    const v = assign.value ? Number(assign.value) : null;
    save(id, { assignee_id: v }, 0);
    toast(v ? `Assigned to ${person(v).name}` : "Unassigned");
  });
  const flag = h("input", { type: "checkbox", id: "d-followup" }); bindCheck(flag, id, "followup");
  const flagNote = bindText(h("input", { class: "input", id: "d-followup-note", placeholder: "What needs following up, and with whom?" }), id, "followup_note");
  const completeBtn = h("button", { class: "btn ok", id: "d-complete", onclick: () => toggleComplete(id) });

  const srcWrap = h("div", { class: "chips", id: "d-sources", role: "group", "aria-label": "Source systems" });

  const bodies = {
    name: [title],
    definition: [bindText(h("textarea", { class: "input", id: "d-description", rows: "2", placeholder: "e.g. The share of shipments that arrive by the date we promised the customer." }), id, "description")],
    formula: [bindText(h("textarea", { class: "input", id: "d-formula", rows: "3", placeholder: "e.g. Shipments delivered on or before the promised date ÷ all delivered shipments." }), id, "formula")],
    sources: [srcWrap,
      bindText(h("input", { class: "input", id: "d-src-other", placeholder: "Another system, a spreadsheet, a vendor portal… Note which input comes from where." }), id, "source_other")],
    target: [bindText(h("textarea", { class: "input", id: "d-target", rows: "2", placeholder: "e.g. 95% on time, set by the VP of Operations each year, kept in the budget workbook." }), id, "target"),
      h("label", { class: "check" }, bindCheck(h("input", { type: "checkbox", id: "d-t-none" }), id, "target_none"), "No benchmark, target or threshold")],
    report: [bindText(h("textarea", { class: "input", id: "d-report", rows: "2", placeholder: "How it's used today, and a link to the trusted report (SharePoint, Power BI, Excel) or where to find it." }), id, "report"),
      h("label", { class: "check" }, bindCheck(h("input", { type: "checkbox", id: "d-r-none" }), id, "report_none"), "Not used today, or no trusted report to test against")],
    breakdowns: [bindText(h("textarea", { class: "input", id: "d-breakdowns", rows: "2", placeholder: "e.g. By product and customer, rolling up to product family and region. Lowest level: individual order line." }), id, "breakdowns"),
      h("label", { class: "check" }, bindCheck(h("input", { type: "checkbox", id: "d-b-same" }), id, "breakdowns_same"), "Same breakdowns as the current report")],
    time: [bindText(h("textarea", { class: "input", id: "d-time", rows: "2", placeholder: "e.g. Ship date. Fiscal periods. Week, MTD, YTD and prior year." }), id, "time_basis")],
    refresh: [h("div", { class: "radios", role: "radiogroup", "aria-label": "Refresh timing" }, REFRESH.map(([v, t, s]) => {
        const inp = h("input", { type: "radio", name: "d-refresh", value: v, id: "d-refresh-" + v, dataset: { field: "refresh", kind: "radio" } });
        inp.addEventListener("change", () => { if (inp.checked) save(id, { refresh: v }, 100); });
        return h("label", { class: "radio", for: "d-refresh-" + v }, inp, h("span", {}, h("b", { text: t }), h("small", { text: s })));
      })),
      bindText(h("input", { class: "input", id: "d-refresh-why", placeholder: "Optional: when is this number usually reviewed? e.g. Monday ops meeting" }), id, "refresh_why")],
    restatement: [bindText(h("textarea", { class: "input", id: "d-restatement", rows: "2", placeholder: "e.g. Yes, late carrier invoices. Restate the last two months; earlier months stay as reported." }), id, "restatement"),
      h("label", { class: "check" }, bindCheck(h("input", { type: "checkbox", id: "d-rs-none" }), id, "restatement_none"), "No, past numbers don't change")],
    owner: personPicker(id, "owner", "Name and role, e.g. Jordan Lee, Director of Logistics"),
    sme: personPicker(id, "sme", "Name and role, e.g. Sam Patel, Transportation Analyst"),
  };

  root.replaceChildren(
    h("div", { class: "d-top" }, h("span", { class: "d-crumb", text: `${d.code}${d.name ? " · " + d.name : ""} · Measure ${r.slot}` }), h("button", { class: "x", "aria-label": "Close", onclick: closeDrawer, text: "×" })),
    h("div", { class: "d-meta" }, h("span", { class: "pill", id: "d-pill" }), h("span", { id: "d-updated" })),
    h("div", { class: "panel" },
      h("div", {}, h("label", { class: "lbl", for: "d-assignee", text: "Assigned to" }), assign),
      h("div", { class: "complete-row" }, completeBtn, h("span", { class: "hint", id: "d-hint" })),
      h("div", { class: "full" }, h("label", { class: "check flagbox" }, flag, "Flag measure for follow-up"),
        h("p", { class: "flag-hint", text: "Questions with \u201cI don't know\u201d checked will also flag this measure for follow-up." })),
      h("div", { class: "full", id: "d-followup-wrap" }, flagNote)),
    ...Q.map((q, i) => h("section", { class: "q" },
      h("div", { class: "q-num", id: "q-" + q.key, text: String(i + 1).padStart(2, "0") }),
      h("div", {}, h("h3", { text: q.title }), h("p", { class: "why", text: q.why }), q.note ? h("p", { class: "q-note", text: q.note }) : null),
      h("div", { class: "q-body" }, bodies[q.key], q.key === "name" ? null : dontKnow(id, q.key)))),
    h("div", { class: "section-h", text: "Notes for the project team" }),
    bindText(h("textarea", { class: "input", id: "d-notes", rows: "2", placeholder: "Anything you're unsure about, or context we should know." }), id, "notes"),
    h("div", { class: "section-h", text: "Activity" }),
    h("ul", { class: "log", id: "d-log" }),
  );
  syncDrawer();
  loadActivity();
  $("#drawer").classList.add("open"); $("#drawer").setAttribute("aria-hidden", "false");
  $("#scrim").classList.add("open");
  renderGrid();
  if (!(r.name || "").trim()) title.focus();
}
function closeDrawer() {
  if (S.open) flush(S.open);
  S.open = null;
  $("#drawer").classList.remove("open"); $("#drawer").setAttribute("aria-hidden", "true");
  $("#scrim").classList.remove("open");
  renderGrid();
}
function syncDrawer() {
  const id = S.open; if (!id) return;
  const r = S.reqs.get(id), p = pending.get(id) || {}, active = document.activeElement, root = $("#drawer-inner");
  // source chips
  const wrap = $("#d-sources", root);
  if (!("sources" in p)) {
    const chosen = r.sources || [];
    const all = [...S.sources, ...chosen.filter(s => !S.sources.includes(s))];
    const key = all.join("\u0001") + "|" + chosen.join("\u0001");
    if (wrap.dataset.key !== key) {
      wrap.dataset.key = key;
      wrap.replaceChildren(...all.map((s, i) => {
        const inp = h("input", { type: "checkbox", id: "d-src-" + i, value: s, checked: chosen.includes(s) });
        inp.addEventListener("change", () => save(id, { sources: $$("#d-sources input").filter(x => x.checked).map(x => x.value) }, 250));
        return h("label", { class: "chip", for: "d-src-" + i }, inp, h("span", { text: s }));
      }));
    }
  }
  peopleOptions($("#d-assignee"), "Unassigned");
  for (const k of ["owner", "sme"]) peopleOptions($(`#d-${k}`), "Choose a person", [h("option", { value: "other", text: "Someone not listed…" })]);
  if (!("unknown" in p)) $$("[data-unknown]", root).forEach(box => {
    const k = box.dataset.unknown, on = k in unknownOf(r), who = $(`#d-who-${k}`, root);
    box.checked = on; who.parentElement.hidden = !on;
    if (who !== active) who.value = on ? unknownOf(r)[k] : "";
  });
  $$("[data-field]", root).forEach(el => {
    const f = el.dataset.field, kind = el.dataset.kind;
    if (el === active || f in p) return;
    if (kind === "person") el.value = r[f] ?? "";
    else if (kind === "person-note") {
      const k = el.dataset.key;
      const v = r[f] != null ? String(r[f]) : ((r[`${k}_note`] || "").trim() ? "other" : "");
      el.value = v; $(`#d-${k}-note-wrap`).hidden = v !== "other";
    }
    else if (kind === "radio") el.checked = el.value === r[f];
    else if (kind === "check") el.checked = !!r[f];
    else if (el.value !== (r[f] || "")) el.value = r[f] || "";
  });
  const upd = $("#d-updated");
  upd.textContent = r.updated_at ? `Last edited ${rel(r.updated_at)}${person(r.updated_by) ? " by " + person(r.updated_by).name : ""}` : "Not started yet";
  syncState();
}
function syncState() {
  const id = S.open; if (!id) return;
  const r = S.reqs.get(id), ans = answered(r), n = doneCount(r);
  const pill = $("#d-pill"); pill.className = "pill s-" + r.status; pill.textContent = STATUS[r.status];
  Q.forEach((q, i) => {
    const el = $("#q-" + q.key), unk = q.key in unknownOf(r);
    el.classList.toggle("done", ans[q.key] && !unk); el.classList.toggle("unk", unk);
    el.textContent = unk ? "?" : ans[q.key] ? "✓" : String(i + 1).padStart(2, "0");
  });
  $("#d-followup-wrap").hidden = !r.followup;
  const btn = $("#d-complete"), hint = $("#d-hint");
  if (r.status === "complete") { btn.textContent = "Reopen"; btn.className = "btn"; btn.disabled = false; hint.textContent = "Complete" + (r.completed_at ? " " + rel(r.completed_at) : ""); }
  else {
    btn.textContent = "Mark complete"; btn.className = "btn ok"; btn.disabled = n < Q.length;
    hint.textContent = n < Q.length ? `${Q.length - n} left.` : "Every question answered.";
  }
}
async function toggleComplete(id) {
  await flush(id);
  const r = S.reqs.get(id);
  const status = r.status === "complete" ? "in_progress" : "complete";
  try {
    const out = await api("POST", `${API}/requirements/${id}/status`, { status });
    S.reqs.set(id, out); renderGrid(); renderSummary(); syncDrawer(); loadActivity();
    toast(status === "complete" ? "Marked complete. Thank you!" : "Reopened");
  } catch (e) { toast(e.message, true); }
}
async function loadActivity() {
  const id = S.open; if (!id) return;
  try {
    const rows = await api("GET", `${API}/requirements/${id}/activity`);
    if (S.open !== id) return;
    $("#d-log").replaceChildren(...(rows.length ? rows.map(a => h("li", {}, h("span", { text: `${a.detail}${a.actor ? " · " + a.actor : ""}` }), h("span", { text: rel(a.at) })))
      : [h("li", {}, h("span", { class: "muted", text: "No changes yet." }))]));
  } catch {}
}

/* ---------- people & sources dialogs ---------- */
function renderPeople() {
  const counts = new Map();
  for (const r of S.reqs.values()) if (r.assignee_id != null) {
    const c = counts.get(r.assignee_id) || { a: 0, d: 0 }; c.a++; if (r.status === "complete") c.d++; counts.set(r.assignee_id, c);
  }
  $("#people-rows").replaceChildren(...(S.people.length ? S.people.map(p => {
    const c = counts.get(p.id) || { a: 0, d: 0 };
    const rm = h("button", { class: "btn ghost danger", text: "Remove" });
    rm.addEventListener("click", async () => {
      if (!rm.classList.contains("armed")) { rm.classList.add("armed"); rm.textContent = "Confirm"; setTimeout(() => { rm.classList.remove("armed"); rm.textContent = "Remove"; }, 4000); return; }
      try { await api("DELETE", `${API}/people/${p.id}`); await load(); toast(`Removed ${p.name}`); } catch (e) { toast(e.message, true); }
    });
    return h("tr", {}, h("td", {}, h("b", { text: p.name }), p.email ? h("div", { class: "muted", text: p.email }) : null), h("td", { text: p.role }),
      h("td", { class: "num", text: String(c.a) }), h("td", { class: "num", text: c.a ? `${c.d}/${c.a}` : "–" }), h("td", { class: "num" }, rm));
  }) : [h("tr", {}, h("td", { colspan: "5", class: "muted", text: "No one yet. Add the people who will fill in measures." }))]));
}
$("#people-form").addEventListener("submit", async e => {
  e.preventDefault();
  const f = new FormData(e.target);
  try {
    const p = await api("POST", `${API}/people`, { name: f.get("name"), role: f.get("role") || "", email: f.get("email") || "" });
    e.target.reset(); await load(); toast(`Added ${p.name}`);
    if (S.me == null) { S.me = p.id; store.set("me", p.id); renderAll(); }
  } catch (err) { toast(err.message, true); }
});
$("#btn-people").addEventListener("click", () => { renderPeople(); $("#dlg-people").showModal(); });
async function renderArchive() {
  const rows = await api("GET", `${API}/archive`);
  $("#archive-rows").replaceChildren(...(rows.length ? rows.map(a => {
    const btn = h("button", { class: "btn", text: "Restore" });
    btn.addEventListener("click", async () => {
      btn.disabled = true;
      try {
        const out = await api("POST", `${API}/archive/${a.id}/restore`);
        await load(); await renderArchive();
        toast(out.note || `Restored ${a.label}`);
      } catch (e) { btn.disabled = false; toast("Couldn't restore: " + e.message, true); }
    });
    return h("tr", {},
      h("td", {}, h("b", { text: a.label }), h("div", { class: "muted", text: a.kind === "dashboard" ? "Dashboard row" : "Measure column" })),
      h("td", { text: a.measures.length ? a.measures.join(", ") : "No measures filled in" }),
      h("td", { class: "muted" }, rel(a.archived_at), a.archived_by ? h("div", { text: "by " + a.archived_by }) : null),
      h("td", { class: "num" }, btn));
  }) : [h("tr", {}, h("td", { colspan: "4", class: "muted", text: "Nothing has been deleted." }))]));
}
$("#btn-archive").addEventListener("click", () => { renderArchive().catch(e => toast(e.message, true)); $("#dlg-archive").showModal(); });
$("#btn-sources").addEventListener("click", () => { $("#sources-text").value = S.sources.join("\n"); $("#dlg-sources").showModal(); });
$("#sources-save").addEventListener("click", async () => {
  try {
    const r = await api("PUT", `${API}/sources`, { sources: $("#sources-text").value.split("\n") });
    S.sources = r.sources; $("#dlg-sources").close(); syncDrawer(); toast("Source systems saved");
  } catch (e) { toast(e.message, true); }
});
$$("[data-close]").forEach(b => b.addEventListener("click", () => b.closest("dialog").close()));

/* ---------- wiring ---------- */
function renderAll() { renderSummary(); renderGrid(); if (S.open) syncDrawer(); if ($("#dlg-people").open) renderPeople(); }
$("#me").addEventListener("change", e => { S.me = e.target.value ? Number(e.target.value) : null; store.set("me", S.me); renderAll(); });
$$(".seg button").forEach(b => b.addEventListener("click", () => {
  S.filter = b.dataset.filter;
  $$(".seg button").forEach(x => x.setAttribute("aria-pressed", String(x === b)));
  if (S.filter === "mine" && S.me == null) toast("Choose your name at the top right first");
  renderGrid();
}));
$("#scrim").addEventListener("click", closeDrawer);
document.addEventListener("keydown", e => { if (e.key === "Escape" && S.open && !document.querySelector("dialog[open]")) closeDrawer(); });

// Pick up other people's changes. Fields being edited here are left alone.
setInterval(() => { if (!document.hidden) load().catch(() => {}); }, 15000);
load().catch(e => toast("Couldn't load: " + e.message, true));
