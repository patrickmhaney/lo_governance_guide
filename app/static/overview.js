"use strict";
/* The overview: the four steps in the order the work is done, how far along
   each is, and the gaps worth closing next. */

const w = path => `/w/${SLUG}${path}`;

function stepCard(n, title, what, href, done, total, line) {
  return h("a", { class: "ov-step card", href },
    h("span", { class: "ov-n", text: String(n) }),
    h("span", { class: "ov-title", text: title }),
    h("span", { class: "ov-what", text: what }),
    h("span", { class: "mini-bar" }, h("span", { class: "b-ok", style: `width:${total ? done / total * 100 : 0}%` })),
    h("span", { class: "ov-line", text: line }));
}

function renderOverview() {
  const procs = S.processes.filter(p => (p.name || "").trim()), dims = S.dimensions.filter(d => (d.name || "").trim());
  const ticks = S.processes.reduce((n, p) => n + S.dimensions.filter(d => S.bus.has(busKey(p.id, d.id))).length, 0);
  const complete = list => list.filter(x => x.status === "complete").length;
  const sketched = S.dashboards.filter(d => S.tiles.some(t => t.dashboard_id === d.id));
  const linked = S.measures.filter(m => m.process_id != null);
  const sugg = new Set();
  for (const m of S.measures) if (m.process_id != null) for (const d of m.dimension_ids || []) if (!S.bus.has(busKey(m.process_id, d))) sugg.add(busKey(m.process_id, d));

  $("#ov-steps").replaceChildren(
    stepCard(1, "Processes", "What the business does, step by step, and who does each step.", w("/processes"),
      complete(procs), procs.length, procs.length ? `${complete(procs)} of ${plural(procs.length, "process", "processes")} complete` : "None yet"),
    stepCard(2, "Bus matrix", "What each process is analyzed by. Shared dimensions are defined once.", w("/bus-matrix"),
      complete(dims), dims.length, dims.length ? `${plural(dims.length, "dimension")} · ${plural(ticks, "tick")}` : "No dimensions yet"),
    stepCard(3, "Measures", "The numbers on each dashboard, defined once, with an owner.", w(""),
      complete(S.measures), S.measures.length, S.measures.length ? `${complete(S.measures)} of ${plural(S.measures.length, "measure")} complete` : "None defined yet"),
    stepCard(4, "Prototypes", "A sketch of each dashboard: what goes where, and what it shows.", w("/prototypes"),
      sketched.length, S.dashboards.length, `${sketched.length} of ${plural(S.dashboards.length, "dashboard")} sketched`),
  );

  const todo = [];
  const add = (text, href, label) => todo.push(h("li", {}, h("span", { text }), h("a", { class: "btn sm", href, text: label })));
  if (!procs.length) add("List the business processes the warehouse should cover.", w("/processes"), "Add processes");
  const noOwner = [...procs, ...dims].filter(x => !ownerText(x)).length + S.measures.filter(m => m.owner_id == null && !(m.owner_note || "").trim()).length;
  if (noOwner) add(`${plural(noOwner, "item")} ${noOwner === 1 ? "has" : "have"} no owner.`, w("/ownership"), "See which");
  const noSteps = procs.filter(p => !(p.steps || []).some(s => (s.name || "").trim())).length;
  if (noSteps) add(`${plural(noSteps, "process", "processes")} ${noSteps === 1 ? "has" : "have"} no steps mapped.`, w("/processes"), "Map steps");
  if (procs.length && !dims.length) add("Add the dimensions the business analyzes by, then tick them on the bus matrix.", w("/bus-matrix"), "Add dimensions");
  if (sugg.size) add(`The bus matrix has ${plural(sugg.size, "suggested tick")} from measure breakdowns.`, w("/bus-matrix"), "Review");
  const unlinked = S.measures.length - linked.length;
  if (unlinked && procs.length) add(`${plural(unlinked, "measure")} ${unlinked === 1 ? "isn't" : "aren't"} linked to a business process.`, w(""), "Link them");
  const follow = [...S.processes, ...S.dimensions, ...S.measures].filter(x => x.followup).length;
  if (follow) add(`${plural(follow, "item")} ${follow === 1 ? "needs" : "need"} follow-up.`, w(""), "Measures");
  const unsketched = S.dashboards.filter(d => !sketched.includes(d) && S.measures.some(m => m.dashboard_id === d.id));
  if (unsketched.length) add(`${unsketched.map(d => d.code).join(", ")} ${unsketched.length === 1 ? "has" : "have"} measures but no sketch.`, w("/prototypes"), "Sketch");
  $("#todo").replaceChildren(...(todo.length ? todo : [h("li", { class: "muted" }, h("span", { text: "Nothing outstanding." }))]));
}

page.render = renderOverview;
start();

/* Build preview: compile, rebuild what changed, show the numbers - labelled, never on the measures. */
const fmtKpi = (v, f) => v == null ? "–" : f === "percent" ? (v * 100).toFixed(1) + "%"
  : f === "currency_usd" ? "$" + v.toLocaleString("en-US", { maximumFractionDigits: 0 })
  : f === "days" ? v.toFixed(1) + " days" : v.toLocaleString("en-US", { maximumFractionDigits: 2 });

function renderPreview(p) {
  const box = $("#preview");
  box.hidden = !p.bound;
  if (!p.bound) return;
  $("#preview-pack").textContent = p.pack;
  const job = p.job, btn = $("#preview-btn");
  btn.disabled = job?.status === "running";
  btn.textContent = job?.status === "running" ? "Building…" : "Build preview";
  const when = job?.finished_at ? new Date(job.finished_at).toLocaleString() : "";
  $("#preview-status").replaceChildren(!job ? h("span", { class: "muted", text: "No preview built in this session yet." })
    : job.status === "running" ? h("span", { text: `Building: ${job.step}…` })
    : job.status === "failed" ? h("span", { class: "bad", text: `The preview failed: ${job.error}` })
    : h("span", {}, `Built ${when} from ${job.changed.length ? plural(job.changed.length, "changed file") : "unchanged requirements"}. `,
        h("a", { href: job.studio_url, target: "_blank", rel: "noopener", text: "Open the dashboards in the Studio" })));
  $("#preview-kpis").replaceChildren(job?.status === "done" && job.kpis.length ? h("table", { class: "preview-kpis" },
    h("thead", {}, h("tr", {}, ["Measure", "Status", "Month", "Preview value", "Month before"].map(t => h("th", { text: t })))),
    h("tbody", {}, job.kpis.map(k => h("tr", {}, h("td", { text: k.label }), h("td", {}, h("span", { class: "tag", text: k.status || "" })),
      h("td", { text: k.month ? new Date(k.month + "T00:00:00").toLocaleDateString("en-US", { month: "short", year: "numeric" }) : "–" }),
      h("td", { class: "num", text: fmtKpi(k.value, k.format) }), h("td", { class: "num muted", text: fmtKpi(k.previous, k.format) }))))) : "");
  $("#preview-log-wrap").hidden = !job;
  if (job) $("#preview-log").textContent = job.log.join("\n");
  if (job?.status === "running") setTimeout(loadPreview, 1500);
}

async function loadPreview() {
  try { renderPreview(await api("GET", `${API}/preview`)); } catch { /* no target configured */ }
}

$("#preview-btn").addEventListener("click", async () => {
  try { await api("POST", `${API}/preview`); } catch (e) { toast(e.message, true); }
  loadPreview();
});
loadPreview();
