"use strict";
/* The bus matrix: processes down the side, dimensions across the top. A tick
   says "we want to see this process by this dimension". Measures linked to a
   process suggest ticks from the dimensions they break down by.

   Picking a dashboard lays it over the matrix: the processes its measures come
   from, and the dimensions ticked for all of them. Those are the filters that
   work across the whole dashboard. The pick is kept in the link as #d<id>. */

// "pid:did" -> the measures that break down by a dimension their process doesn't tick.
function suggestions() {
  const out = new Map();
  for (const m of S.measures) {
    if (m.process_id == null || !itemOf("process", m.process_id)) continue;
    for (const d of m.dimension_ids || []) {
      const key = busKey(m.process_id, d);
      if (S.bus.has(key) || !itemOf("dimension", d)) continue;
      out.set(key, [...(out.get(key) || []), m]);
    }
  }
  return out;
}
const usedBy = d => S.processes.filter(p => S.bus.has(busKey(p.id, d.id)));
const names = ms => ms.map(m => m.name).join(", ");

async function setTick(p, d, on) {
  const key = busKey(p.id, d.id);
  on ? S.bus.add(key) : S.bus.delete(key);
  renderBus();
  try { await api("PUT", `${API}/bus`, { process_id: p.id, dimension_id: d.id, on }); }
  catch (e) { on ? S.bus.delete(key) : S.bus.add(key); renderBus(); toast("Couldn't save: " + e.message, true); }
}

S.view = (m => m ? Number(m[1]) : null)(/^#d(\d+)$/.exec(location.hash));
function setView(id) {
  S.view = id;
  history.replaceState(null, "", id ? `#d${id}` : location.pathname);
  renderBus();
}
function renderViewPick() {
  const sel = $("#bus-view"), key = S.dashboards.map(d => d.id + d.code + d.name).join("|") + "|" + S.view;
  if (sel.dataset.key === key) return;
  sel.dataset.key = key;
  sel.replaceChildren(h("option", { value: "", text: "All processes" }),
    ...S.dashboards.map(d => h("option", { value: d.id, text: d.code + (d.name ? " · " + d.name : "") })));
  sel.value = S.view ?? "";
}

function renderBus() {
  if (S.view != null && !S.dashboards.some(d => d.id === S.view)) S.view = null;
  renderViewPick();
  const g = $("#bus"), sugg = suggestions(), open = S.open || {};
  const v = S.view != null ? dashboardView(S.view) : null;
  g.style.setProperty("--dims", S.dimensions.length);
  g.classList.toggle("viewing", !!v);
  if (!S.dimensions.length) {
    g.replaceChildren(h("div", { class: "empty card bus-empty" },
      S.processes.length
        ? h("p", {}, h("b", { text: "No dimensions yet." }), ` Add the things you analyze ${plural(S.processes.length, "process", "processes")} by.`)
        : h("p", {}, h("b", { text: "Nothing here yet." }), " Add business processes on the Processes tab, or here, then the dimensions you analyze them by."),
      h("div", { class: "starters" }, h("span", { class: "muted", text: "Common dimensions:" }),
        ["Date", "Customer", "Product", "Site", "Supplier", "Employee"].map(n => h("button", { class: "btn", text: n, onclick: () => addItem("dimension", n) })))));
    renderSide(sugg, v);
    return;
  }
  const kids = [h("div", { class: "bus-corner", text: "Process  ·  Dimension" })];
  for (const d of S.dimensions) {
    const n = usedBy(d).length;
    let fit = "", use = n > 1 ? `Shared · ${n}` : n === 1 ? "1 process" : "Not used";
    if (v) [fit, use] = v.all.includes(d) ? [" v-all", "Whole dashboard"] : v.some.some(x => x.d === d) ? [" v-some", "Some measures"] : [" v-out", "Not used here"];
    kids.push(h("button", { class: `bus-col s-${d.status}${fit}` + (open.kind === "dimension" && open.id === d.id ? " active" : ""), onclick: () => openItem("dimension", d.id),
      title: `Open ${itemName("dimension", d)}` },
      h("span", { class: "bus-col-name" + ((d.name || "").trim() ? "" : " muted"), text: itemName("dimension", d) }),
      h("span", { class: "bus-col-use", text: use })));
  }
  kids.push(h("button", { class: "add-col", title: "Add a dimension", "aria-label": "Add a dimension", onclick: () => addItem("dimension"), text: "+" }));
  for (const p of S.processes) {
    const owner = ownerText(p), inView = v?.procs.find(e => e.p === p);
    const sub = v ? (inView ? names(inView.measures) : "Not on this dashboard")
      : [owner, (p.answers || {}).event ? "Records: " + p.answers.event : ""].filter(Boolean).join(" · ") || "No owner yet";
    kids.push(h("button", { class: `bus-row s-${p.status}` + (v ? (inView ? " v-in" : " v-out") : "") + (open.kind === "process" && open.id === p.id ? " active" : ""),
      onclick: () => openItem("process", p.id) },
      h("span", { class: "bus-row-name" + ((p.name || "").trim() ? "" : " muted"), text: itemName("process", p) }),
      h("span", { class: "bus-row-sub", text: sub })));
    for (const d of S.dimensions) {
      const key = busKey(p.id, d.id), on = S.bus.has(key), s = sugg.get(key);
      const label = `${itemName("process", p)} by ${itemName("dimension", d)}`;
      const fit = !v ? "" : !inView ? " v-out" : v.all.includes(d) ? " v-all" : "";
      kids.push(h("button", { class: "bus-cell" + (on ? " on" : s ? " sugg" : "") + fit, role: "gridcell", "aria-pressed": String(on),
        title: on ? label : s ? `Suggested: ${names(s)} ${s.length === 1 ? "breaks" : "break"} down by ${itemName("dimension", d)}` : label,
        "aria-label": label, onclick: () => setTick(p, d, !on) }, h("i")));
    }
    kids.push(h("div", { class: "bus-count", text: String(S.dimensions.filter(d => S.bus.has(busKey(p.id, d.id))).length) }));
  }
  kids.push(h("button", { class: "add-row", title: "Add a process", "aria-label": "Add a process", onclick: () => addItem("process"), text: "+" }));
  g.replaceChildren(...kids);
  renderSide(sugg, v);
}

function suggestionItem(key, ms) {
  const [pid, did] = key.split(":").map(Number), p = itemOf("process", pid), d = itemOf("dimension", did);
  return h("li", {}, h("span", {}, h("b", { text: `${itemName("process", p)} by ${itemName("dimension", d)}?` }),
    ` ${names(ms)} ${ms.length === 1 ? "breaks" : "break"} down by it.`),
    h("button", { class: "btn sm", text: "Tick", onclick: () => setTick(p, d, true) }));
}

function renderSide(sugg, v) {
  $("#checks").hidden = !!v;
  $("#view").hidden = !v;
  if (v) return renderView(sugg, v);
  $("#checks-h").textContent = "Checks";
  renderChecks(sugg);
}

// Short, actionable gaps. Each one does something when clicked.
function renderChecks(sugg) {
  const items = [...sugg].map(([key, ms]) => suggestionItem(key, ms));
  for (const p of S.processes) {
    if (!S.dimensions.some(d => S.bus.has(busKey(p.id, d.id))) && S.dimensions.length)
      items.push(h("li", {}, h("span", { text: `${itemName("process", p)} has no dimensions ticked.` })));
    if (!(p.answers || {}).event && !("event" in (p.unknown || {})))
      items.push(h("li", {}, h("span", { text: `Say what ${itemName("process", p)} records, at its finest detail.` }),
        h("button", { class: "btn sm", text: "Answer", onclick: () => openItem("process", p.id, "event") })));
  }
  for (const d of S.dimensions) {
    const n = usedBy(d).length;
    if (!n) items.push(h("li", {}, h("span", { text: `${itemName("dimension", d)} isn't ticked for any process.` })));
    else if (n > 1 && !ownerText(d))
      items.push(h("li", {}, h("span", { text: `${itemName("dimension", d)} is shared by ${n} processes but has no owner.` }),
        h("button", { class: "btn sm", text: "Name one", onclick: () => openItem("dimension", d.id, "owner") })));
  }
  $("#checks").replaceChildren(...(items.length ? items : [h("li", { class: "muted" }, h("span", { text: "Nothing to check." }))]));
}

// One dashboard against the matrix.
function renderView(sugg, v) {
  const d = S.dashboards.find(x => x.id === S.view);
  $("#checks-h").textContent = d.code + (d.name ? " · " + d.name : "");
  const sec = (title, ...body) => h("section", { class: "view-sec" }, h("h3", { text: title }), ...body);
  const dimName = x => itemName("dimension", x);
  const kids = [];
  if (!v.measures.length) kids.push(h("p", { class: "muted", text: "No measures on this dashboard yet." }));
  else {
    kids.push(sec("Measures come from", v.procs.length
      ? h("ul", {}, v.procs.map(e => h("li", {}, h("b", { text: itemName("process", e.p) }), h("span", { class: "muted", text: names(e.measures) }))))
      : h("p", { class: "muted", text: "None of its measures are linked to a process yet." })));
    if (v.procs.length) {
      kids.push(sec("Filters for the whole dashboard", h("p", { text: v.all.length ? v.all.map(dimName).join(", ")
        : `None yet. No dimension is ticked for ${v.procs.length === 1 ? "its process" : `all ${v.procs.length} of its processes`}.` })));
      if (v.some.length) kids.push(sec("Filters for some measures only",
        h("ul", {}, v.some.map(x => h("li", {}, h("b", { text: dimName(x.d) }), h("span", { class: "muted", text: names(x.measures) }))))));
    }
    if (v.unlinked.length) kids.push(sec("Not linked to a process",
      h("ul", {}, v.unlinked.map(m => h("li", { class: "row" }, h("span", { text: m.name }), h("a", { class: "btn sm", href: `/w/${SLUG}#m${m.id}`, text: "Link" }))))));
    const mine = new Set(v.measures);
    const ticks = [...sugg].map(([key, ms]) => [key, ms.filter(m => mine.has(m))]).filter(([, ms]) => ms.length);
    if (ticks.length) kids.push(sec("Suggested ticks", h("ul", { class: "ticks" }, ticks.map(([key, ms]) => suggestionItem(key, ms)))));
  }
  kids.push(h("p", { class: "view-foot" }, h("a", { href: `/w/${SLUG}/prototypes#d${d.id}`, text: "Sketch this dashboard" })));
  $("#view").replaceChildren(...kids);
}

$("#bus-view").addEventListener("change", e => setView(e.target.value ? Number(e.target.value) : null));
$("#add-process").addEventListener("click", () => addItem("process"));
$("#add-dimension").addEventListener("click", () => addItem("dimension"));
page.render = renderBus;
start();
