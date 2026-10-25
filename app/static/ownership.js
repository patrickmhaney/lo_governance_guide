"use strict";
/* Ownership: the gaps first, then what each department owns and does, then
   the departments themselves. A person's department stands in for theirs. */

// The owning department of anything with an owner: its own, or its person's.
// "none" for an owner with no department, null for no owner at all.
function ownerDept(x) {
  if (x.owner_dept_id != null) return x.owner_dept_id;
  if (x.owner_id != null) return person(x.owner_id)?.department_id ?? "none";
  return (x.owner_note || "").trim() ? "none" : null;
}
const link = (text, onclick) => h("button", { class: "linkish", text, onclick });
const measureLink = m => h("a", { href: `/w/${SLUG}#m${m.id}`, text: m.name });
const itemLink = (kind, x) => link(itemName(kind, x), () => openItem(kind, x.id, "owner"));
const joined = els => els.length ? els.flatMap((el, i) => i ? [", ", el] : [el]) : [h("span", { class: "muted", text: "–" })];

function renderOwnership() {
  // Gaps
  const gaps = [
    ["Processes", S.processes.filter(p => ownerDept(p) == null).map(p => itemLink("process", p))],
    ["Dimensions", S.dimensions.filter(d => ownerDept(d) == null).map(d => itemLink("dimension", d))],
    ["Measures", S.measures.filter(m => ownerDept(m) == null).map(measureLink)],
  ];
  const open = gaps.filter(([, list]) => list.length);
  $("#gaps").replaceChildren(open.length
    ? h("dl", { class: "gaps" }, open.flatMap(([label, list]) => [h("dt", { text: `${label} · ${list.length}` }), h("dd", {}, joined(list))]))
    : h("p", { class: "muted", text: "Everything has an owner." }));

  // By department
  const depts = [...S.departments.map(d => [d.id, d.name]), ["none", "No department"]];
  const stepsBy = new Map();
  for (const p of S.processes) for (const s of p.steps || []) {
    const d = deptOf(s.dept_id, s.person_id) ?? "none";
    if ((s.name || "").trim()) stepsBy.set(d, [...(stepsBy.get(d) || []), `${s.name} (${itemName("process", p)})`]);
  }
  $("#by-dept").replaceChildren(...depts.map(([id, name]) => {
    const procs = S.processes.filter(p => ownerDept(p) === id), dims = S.dimensions.filter(d => ownerDept(d) === id);
    const meas = S.measures.filter(m => ownerDept(m) === id), steps = stepsBy.get(id) || [];
    if (id === "none" && !procs.length && !dims.length && !meas.length && !steps.length) return null;
    return h("tr", {}, h("td", {}, h("b", { text: name })),
      h("td", {}, joined(procs.map(p => itemLink("process", p)))),
      h("td", { class: "small" }, steps.length ? h("span", { title: steps.join("\n"), text: plural(steps.length, "step") }) : h("span", { class: "muted", text: "–" })),
      h("td", {}, joined(dims.map(d => itemLink("dimension", d)))),
      h("td", {}, joined(meas.map(measureLink))));
  }).filter(Boolean));
  if (!S.departments.length) $("#by-dept").prepend(h("tr", {}, h("td", { colspan: "5", class: "muted", text: "Add departments below, then give people a department under People." })));

  // Process x department
  const used = S.departments.filter(d => S.processes.some(p => ownerDept(p) === d.id || (p.steps || []).some(s => deptOf(s.dept_id, s.person_id) === d.id)));
  $("#matrix").replaceChildren(
    h("thead", {}, h("tr", {}, h("th", { text: "Process" }), used.map(d => h("th", { class: "c", text: d.name })))),
    h("tbody", {}, S.processes.length && used.length ? S.processes.map(p => h("tr", {},
      h("td", {}, itemLink("process", p)),
      used.map(d => {
        const owns = ownerDept(p) === d.id;
        const n = (p.steps || []).filter(s => (s.name || "").trim() && deptOf(s.dept_id, s.person_id) === d.id).length;
        return h("td", { class: "c" }, owns ? h("span", { class: "tag", text: "Owns" }) : null, n ? h("span", { class: "muted small", text: (owns ? " · " : "") + plural(n, "step") }) : null);
      })))
      : h("tr", {}, h("td", { class: "muted", colspan: String(used.length + 1), text: "Shows once processes have owners or steps with departments." }))));

  // Departments, unless one is being renamed
  if ($("#depts").contains(document.activeElement)) return;
  $("#depts").replaceChildren(...(S.departments.length ? S.departments.map(d => {
    const name = h("input", { class: "input inline", value: d.name, "aria-label": "Department name" });
    name.addEventListener("change", async () => {
      try { await api("PATCH", `${API}/departments/${d.id}`, { name: name.value }); await page.load(); toast("Department renamed"); }
      catch (e) { name.value = d.name; toast(e.message, true); }
    });
    name.addEventListener("keydown", e => { if (e.key === "Enter") name.blur(); });
    const rm = h("button", { class: "btn ghost danger", text: "Remove" });
    rm.addEventListener("click", async () => {
      if (!rm.classList.contains("armed")) { rm.classList.add("armed"); rm.textContent = "Confirm"; setTimeout(() => { rm.classList.remove("armed"); rm.textContent = "Remove"; }, 4000); return; }
      try { await api("DELETE", `${API}/departments/${d.id}`); await page.load(); toast(`Removed ${d.name}`); } catch (e) { toast(e.message, true); }
    });
    return h("tr", {}, h("td", {}, name), h("td", { class: "num", text: String(S.people.filter(p => p.department_id === d.id).length) }), h("td", { class: "num" }, rm));
  }) : [h("tr", {}, h("td", { colspan: "3", class: "muted", text: "No departments yet." }))]));
}

$("#dept-form").addEventListener("submit", async e => {
  e.preventDefault();
  const f = new FormData(e.target);
  try { const d = await api("POST", `${API}/departments`, { name: f.get("name") }); e.target.reset(); await page.load(); toast(`Added ${d.name}`); }
  catch (err) { toast(err.message, true); }
});
page.render = renderOwnership;
start();
