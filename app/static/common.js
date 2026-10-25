"use strict";
/* Shared by every workspace page: the API, toasts, "You are", People,
   Archive and the short guides. Each page sets page.load (fetch its state and
   call setShared) and page.render, then calls start(). */

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
// The same for SVG, which needs its own namespace.
function svg(tag, attrs, ...kids) {
  const el = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [k, v] of Object.entries(attrs || {})) if (v != null && v !== false) el.setAttribute(k, v);
  for (const c of kids.flat()) { if (c == null || c === false) continue; el.append(c.nodeType ? c : document.createTextNode(String(c))); }
  return el;
}
const store = {
  get(k) { try { return localStorage.getItem(`mreq:${SLUG}:${k}`); } catch { return null; } },
  set(k, v) { try { v == null ? localStorage.removeItem(`mreq:${SLUG}:${k}`) : localStorage.setItem(`mreq:${SLUG}:${k}`, v); } catch {} },
};

const STATUS = { not_started: "Not started", in_progress: "In progress", complete: "Complete" };

const S = {
  people: [], departments: [], sources: [], archived: 0,
  me: store.get("me") ? Number(store.get("me")) : null,
};
const page = { load: async () => {}, render: () => {} };

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

/* ---------- helpers ---------- */
const person = id => S.people.find(p => p.id === id) || null;
const department = id => S.departments.find(d => d.id === id) || null;
const plural = (n, word, many = word + "s") => `${n} ${n === 1 ? word : many}`;
function rel(iso) {
  if (!iso) return "";
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 60) return "just now";
  if (s < 3600) return Math.floor(s / 60) + " min ago";
  if (s < 86400) return Math.floor(s / 3600) + " h ago";
  if (s < 604800) return Math.floor(s / 86400) + " d ago";
  return new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}
// Departments and people in one <select>, as "d:<id>" and "p:<id>".
function whoOptions(emptyLabel, extra) {
  return [
    h("option", { value: "", text: emptyLabel }),
    S.departments.length ? h("optgroup", { label: "Departments" }, S.departments.map(d => h("option", { value: "d:" + d.id, text: d.name }))) : null,
    S.people.length ? h("optgroup", { label: "People" }, S.people.map(p => h("option", { value: "p:" + p.id, text: p.name + (p.role ? ` · ${p.role}` : "") }))) : null,
    ...(extra || []),
  ];
}
// The department a step or an owner belongs to: its own, or its person's.
const deptOf = (deptId, personId) => deptId ?? person(personId)?.department_id ?? null;

function setShared(st) {
  S.people = st.people; S.departments = st.departments; S.sources = st.sources; S.archived = st.archived;
  if (S.me && !S.people.some(p => p.id === S.me)) { S.me = null; store.set("me", null); }
}
function renderShared() {
  $("#btn-archive").textContent = S.archived ? `Archive (${S.archived})` : "Archive";
  const me = $("#me");
  me.replaceChildren(h("option", { value: "", text: "Choose your name" }), ...S.people.map(p => h("option", { value: p.id, text: p.name })));
  me.value = S.me ?? "";
  if ($("#dlg-people").open) renderPeople();
}
function renderAll() { renderShared(); page.render(); }

/* ---------- people ---------- */
function deptSelect(value, onChange) {
  const sel = h("select", { class: "select", "aria-label": "Department" },
    h("option", { value: "", text: "No department" }),
    ...S.departments.map(d => h("option", { value: d.id, text: d.name })),
    h("option", { value: "new", text: "New department…" }));
  sel.value = value ?? "";
  sel.addEventListener("change", async () => {
    if (sel.value !== "new") return onChange(sel.value ? Number(sel.value) : null);
    const name = (prompt("Name the new department") || "").trim();
    if (!name) { sel.value = value ?? ""; return; }
    try { const d = await api("POST", `${API}/departments`, { name }); S.departments.push(d); await onChange(d.id); }
    catch (e) { sel.value = value ?? ""; toast(e.message, true); }
  });
  return sel;
}
function renderPeople() {
  const formDept = $("#people-form [name=department_id]");
  const keep = formDept.value;
  formDept.replaceChildren(h("option", { value: "", text: "Department" }), ...S.departments.map(d => h("option", { value: d.id, text: d.name })));
  formDept.value = keep;
  $("#people-rows").replaceChildren(...(S.people.length ? S.people.map(p => {
    const rm = h("button", { class: "btn ghost danger", text: "Remove" });
    rm.addEventListener("click", async () => {
      if (!rm.classList.contains("armed")) { rm.classList.add("armed"); rm.textContent = "Confirm"; setTimeout(() => { rm.classList.remove("armed"); rm.textContent = "Remove"; }, 4000); return; }
      try { await api("DELETE", `${API}/people/${p.id}`); await page.load(); toast(`Removed ${p.name}`); } catch (e) { toast(e.message, true); }
    });
    const dept = deptSelect(p.department_id, async id => {
      try { await api("PATCH", `${API}/people/${p.id}`, { department_id: id }); await page.load(); }
      catch (e) { toast(e.message, true); }
    });
    return h("tr", {}, h("td", {}, h("b", { text: p.name }), p.email ? h("div", { class: "muted", text: p.email }) : null), h("td", { text: p.role }),
      h("td", {}, dept), h("td", { class: "num", text: String(p.assigned) }), h("td", { class: "num", text: p.assigned ? `${p.complete}/${p.assigned}` : "–" }), h("td", { class: "num" }, rm));
  }) : [h("tr", {}, h("td", { colspan: "6", class: "muted", text: "No one yet. Add the people who will fill this in." }))]));
}
$("#people-form").addEventListener("submit", async e => {
  e.preventDefault();
  const f = new FormData(e.target);
  try {
    const p = await api("POST", `${API}/people`, { name: f.get("name"), role: f.get("role") || "", email: f.get("email") || "",
      department_id: f.get("department_id") ? Number(f.get("department_id")) : null });
    e.target.reset(); await page.load(); toast(`Added ${p.name}`);
    if (S.me == null) { S.me = p.id; store.set("me", p.id); renderAll(); }
  } catch (err) { toast(err.message, true); }
});
$("#btn-people").addEventListener("click", () => { renderPeople(); $("#dlg-people").showModal(); });

/* ---------- archive ---------- */
const ARCHIVE_KINDS = { dashboard: "Dashboard row", column: "Measure column", process: "Business process", dimension: "Dimension" };
async function renderArchive() {
  const rows = await api("GET", `${API}/archive`);
  $("#archive-rows").replaceChildren(...(rows.length ? rows.map(a => {
    const btn = h("button", { class: "btn", text: "Restore" });
    btn.addEventListener("click", async () => {
      btn.disabled = true;
      try {
        const out = await api("POST", `${API}/archive/${a.id}/restore`);
        await page.load(); await renderArchive();
        toast(out.note || `Restored ${a.label}`);
      } catch (e) { btn.disabled = false; toast("Couldn't restore: " + e.message, true); }
    });
    const measured = a.kind === "dashboard" || a.kind === "column";
    return h("tr", {},
      h("td", {}, h("b", { text: a.label }), h("div", { class: "muted", text: ARCHIVE_KINDS[a.kind] || a.kind })),
      h("td", { class: measured ? "" : "muted", text: !measured ? "–" : a.measures.length ? a.measures.join(", ") : "No measures filled in" }),
      h("td", { class: "muted" }, rel(a.archived_at), a.archived_by ? h("div", { text: "by " + a.archived_by }) : null),
      h("td", { class: "num" }, btn));
  }) : [h("tr", {}, h("td", { colspan: "4", class: "muted", text: "Nothing has been deleted." }))]));
}
$("#btn-archive").addEventListener("click", () => { renderArchive().catch(e => toast(e.message, true)); $("#dlg-archive").showModal(); });
$$("[data-close]").forEach(b => b.addEventListener("click", () => b.closest("dialog").close()));

/* ---------- guides: short intros a viewer can hide, and bring back ---------- */
$$("[data-guide]").forEach(g => {
  const key = "guide:" + g.dataset.guide;
  const show = $(`[data-guide-show="${g.dataset.guide}"]`);
  const set = hidden => { g.hidden = hidden; if (show) show.hidden = !hidden; store.set(key, hidden ? "hidden" : null); };
  g.append(h("button", { class: "guide-x", "aria-label": "Hide this guide", text: "Hide", onclick: () => set(true) }));
  show?.addEventListener("click", () => set(false));
  set(store.get(key) === "hidden");
});

/* ---------- wiring ---------- */
$("#me").addEventListener("change", e => { S.me = e.target.value ? Number(e.target.value) : null; store.set("me", S.me); renderAll(); });

function start() {
  // Pick up other people's changes. Fields being edited here are left alone.
  setInterval(() => { if (!document.hidden && !document.querySelector("dialog[open]")) page.load().catch(() => {}); }, 15000);
  page.load().catch(e => toast("Couldn't load: " + e.message, true));
}
