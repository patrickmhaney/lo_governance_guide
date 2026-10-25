"use strict";
/* The processes page: one card per business process, with a swimlane drawn
   from its steps. Lanes are departments, in the order they first act. */

const STARTERS = ["Order to Cash", "Procure to Pay", "Inventory to Delivery", "Record to Report", "Hire to Retire", "Plan to Produce"];
const PRIORITY = { now: "Now", next: "Next", later: "Later" };

function renderProcesses() {
  const list = $("#proc-list");
  if (!S.processes.length) {
    list.replaceChildren(h("div", { class: "empty card" },
      h("p", {}, h("b", { text: "No processes yet." }), " Start with the three or four the business depends on most."),
      h("div", { class: "starters" }, h("span", { class: "muted", text: "Common ones:" }),
        STARTERS.map(name => h("button", { class: "btn", text: name, onclick: () => addItem("process", name) })))));
    return;
  }
  list.replaceChildren(...S.processes.map(p => {
    const a = p.answers || {};
    const measures = S.measures.filter(m => m.process_id === p.id).length;
    const dims = S.dimensions.filter(d => S.bus.has(busKey(p.id, d.id))).length;
    const active = S.open && S.open.kind === "process" && S.open.id === p.id;
    const owner = ownerText(p);
    return h("article", { class: "proc card" + (active ? " active" : "") },
      h("button", { class: "proc-h", onclick: () => openItem("process", p.id), "aria-label": `Open ${itemName("process", p)}` },
        h("span", { class: "proc-title" },
          h("span", { class: "proc-name" + ((p.name || "").trim() ? "" : " muted"), text: itemName("process", p) }),
          a.priority ? h("span", { class: "tag", text: PRIORITY[a.priority] }) : null),
        h("span", { class: "proc-meta" },
          h("span", { class: owner ? "" : "muted", text: owner ? `Owner: ${owner}` : "No owner yet" }),
          h("span", { class: "proc-state" }, p.followup ? h("span", { class: "flag", text: "Follow-up" }) : null, statusPill(p), meter("process", p)))),
      a.purpose ? h("p", { class: "proc-purpose", text: a.purpose }) : null,
      swimlane(p),
      h("div", { class: "proc-foot" },
        h("span", { text: plural(measures, "measure") }),
        h("a", { href: `/w/${SLUG}/bus-matrix`, text: plural(dims, "dimension") }),
        a.event ? h("span", { class: "muted", text: "Records: " + a.event }) : null));
  }));
  requestAnimationFrame(drawConnectors);
}

function swimlane(p) {
  const steps = (p.steps || []).filter(s => (s.name || "").trim());
  if (!steps.length) {
    return h("button", { class: "lanes-empty", text: "+ Add the main steps", onclick: () => openItem("process", p.id, "steps") });
  }
  const laneOf = s => { const d = deptOf(s.dept_id, s.person_id); return d == null ? "none" : String(d); };
  const lanes = [...new Set(steps.map(laneOf))].sort((x, y) => (x === "none") - (y === "none"));
  const grid = h("div", { class: "lanes", style: `--steps:${steps.length}` });
  lanes.forEach((lane, r) => {
    grid.append(h("div", { class: "lane-label", style: `grid-row:${r + 1}`, text: lane === "none" ? "Not assigned" : department(Number(lane))?.name || "" }));
    grid.append(h("div", { class: "lane-band", style: `grid-row:${r + 1}` }));
  });
  steps.forEach((s, i) => {
    const who = s.person_id != null ? person(s.person_id)?.name : null;
    grid.append(h("button", { class: "step", style: `grid-row:${lanes.indexOf(laneOf(s)) + 1};grid-column:${i + 2}`, dataset: { step: i },
      onclick: () => openItem("process", p.id, "steps"), title: s.name },
      h("span", { class: "step-name", text: `${i + 1}. ${s.name}` }),
      s.system || who ? h("span", { class: "step-sub", text: [who, s.system].filter(Boolean).join(" · ") }) : null));
  });
  return h("div", { class: "lanes-wrap" }, grid, svg("svg", { class: "lanes-svg", "aria-hidden": "true" }));
}

// Elbow arrows from each step to the next, drawn once the boxes are laid out.
function drawConnectors() {
  $$(".lanes-wrap").forEach((wrap, n) => {
    const out = $("svg", wrap), r = wrap.getBoundingClientRect();
    // Measured from the start of the scrolled content, so wide lanes line up when scrolled.
    const box = { left: r.left - wrap.scrollLeft, top: r.top };
    const steps = $$(".step", wrap).map(el => el.getBoundingClientRect());
    out.setAttribute("width", wrap.scrollWidth); out.setAttribute("height", r.height);
    const arrow = `arrow-${n}`;
    const paths = steps.slice(0, -1).map((a, i) => {
      const b = steps[i + 1];
      const x1 = a.right - box.left, y1 = a.top + a.height / 2 - box.top;
      const x2 = b.left - box.left - 2, y2 = b.top + b.height / 2 - box.top;
      const xm = (x1 + x2) / 2;
      return svg("path", { d: Math.abs(y1 - y2) < 1 ? `M${x1},${y1} H${x2}` : `M${x1},${y1} H${xm} V${y2} H${x2}`, "marker-end": `url(#${arrow})` });
    });
    out.replaceChildren(svg("defs", {}, svg("marker", { id: arrow, viewBox: "0 0 8 8", refX: 7, refY: 4, markerWidth: 7, markerHeight: 7, orient: "auto" },
      svg("path", { d: "M0,0 L8,4 L0,8 z" }))), ...paths);
  });
}
new ResizeObserver(() => drawConnectors()).observe($("#proc-list"));

$("#add-process").addEventListener("click", () => addItem("process"));
page.render = renderProcesses;
start();
