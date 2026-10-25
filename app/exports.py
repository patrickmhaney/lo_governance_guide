"""The content handed over at the end, in whatever home the client wants.

- measures CSV and the Markdown measure catalogue (the original handoff)
- the bus matrix as CSV
- the handbook: one Markdown document with processes, the bus matrix,
  dimensions, measures, dashboard sketches and ownership
"""
from __future__ import annotations

import csv
import io
import json

from . import db, items

REFRESH_LABELS = {"monthly": "Monthly", "weekly": "Weekly", "daily": "Daily",
                  "intraday": "During the day"}
TILE_LABELS = {"kpi": "Headline number", "line": "Trend", "bar": "Comparison", "table": "Table",
               "filter": "Filter", "text": "Note"}
# The warehouse term next to the business answer, for whoever builds it.
HISTORY_TECH = {"as_was": "keep history (type 2)", "as_is": "overwrite (type 1)", "both": "keep history, show either"}


def defined_rows(conn, client_id: int):
    return conn.execute(
        """SELECT d.code, d.name AS dashboard, r.*, a.name AS assignee, o.name AS owner, s.name AS sme,
                  p.name AS process
           FROM requirements r
           JOIN dashboards d ON d.id = r.dashboard_id
           LEFT JOIN people a ON a.id = r.assignee_id
           LEFT JOIN people o ON o.id = r.owner_id
           LEFT JOIN people s ON s.id = r.sme_id
           LEFT JOIN processes p ON p.id = r.process_id
           WHERE d.client_id = ? AND r.name <> ''
           ORDER BY d.position, r.slot""",
        (client_id,),
    ).fetchall()


def _refs(conn, client_id: int):
    """(dashboard id, dashboard code, measure id) for every cell showing a measure defined elsewhere."""
    return conn.execute(
        """SELECT d.id, d.code, r.ref_id FROM requirements r JOIN dashboards d ON d.id = r.dashboard_id
           WHERE d.client_id = ? AND r.ref_id IS NOT NULL ORDER BY d.position, r.slot""", (client_id,)).fetchall()


def _also_on(conn, client_id: int) -> dict[int, str]:
    """Measure id -> the other dashboards that show it."""
    out: dict[int, list[str]] = {}
    for _, code, mid in _refs(conn, client_id):
        out.setdefault(mid, []).append(code)
    return {k: ", ".join(v) for k, v in out.items()}


def _dimension_names(conn, client_id: int) -> dict[int, str]:
    return {r["id"]: r["name"] or "Unnamed dimension" for r in conn.execute(
        "SELECT id, name FROM dimensions WHERE client_id = ?", (client_id,))}


def _dims_text(r, names: dict[int, str]) -> str:
    return ", ".join(names[d] for d in json.loads(r["dimension_ids"] or "[]") if d in names)


def _sources_text(r) -> str:
    sources = json.loads(r["sources"] or "[]")
    if r["source_other"]:
        sources.append(r["source_other"])
    return "; ".join(sources)


def _unknown_text(unknown: dict, labels: dict[str, str]) -> str:
    """The questions answered "I don't know", with who might know, for follow-up."""
    return "; ".join(labels[k] + (f" (ask {who})" if who else "")
                     for k, who in sorted(unknown.items(), key=lambda kv: list(labels).index(kv[0])))


def _cell(v: str) -> str:
    return v.replace("|", "/").replace("\n", " ")


def _table(fields) -> list[str]:
    return ["| | |", "|---|---|", *[f"| {k} | {_cell(v)} |" for k, v in fields if v]]


def measures_csv(conn, c: dict) -> str:
    rows = defined_rows(conn, c["id"])
    names = _dimension_names(conn, c["id"])
    also = _also_on(conn, c["id"])
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["dashboard", "slot", "measure", "definition", "calculation", "source_systems",
                "benchmark_target", "trusted_report", "breakdowns", "date_and_periods", "refresh", "refresh_why",
                "changes_to_past_numbers", "definition_owner", "subject_matter_expert", "dont_know", "assignee",
                "status", "follow_up", "follow_up_note", "notes", "updated_at", "business_process", "dimensions",
                "also_on"])
    for r in rows:
        w.writerow([
            r["code"] + (f" {r['dashboard']}" if r["dashboard"] else ""), f"M{r['slot']}", r["name"], r["description"],
            r["formula"], _sources_text(r),
            "(none)" if r["target_none"] and not r["target"] else r["target"],
            "(none today)" if r["report_none"] and not r["report"] else r["report"],
            "(same as current report)" if r["breakdowns_same"] and not r["breakdowns"] else r["breakdowns"],
            r["time_basis"], r["refresh"], r["refresh_why"],
            "(past numbers don't change)" if r["restatement_none"] and not r["restatement"] else r["restatement"],
            r["owner"] or r["owner_note"], r["sme"] or r["sme_note"],
            _unknown_text(json.loads(r["unknown"] or "{}"), db.QUESTION_LABELS), r["assignee"] or "",
            r["status"], "yes" if r["followup"] else "", r["followup_note"], r["notes"], r["updated_at"] or "",
            r["process"] or "", _dims_text(r, names), also.get(r["id"], ""),
        ])
    return buf.getvalue()


def _measure_sections(conn, c: dict, depth: int) -> list[str]:
    """Measures grouped by dashboard: dashboards at `depth`, measures one level below.
    A measure is written out where it's defined; other dashboards list it by name."""
    rows = defined_rows(conn, c["id"])
    names = _dimension_names(conn, c["id"])
    also = _also_on(conn, c["id"])
    by_id = {r["id"]: r for r in rows}
    own: dict[int, list] = {}
    for r in rows:
        own.setdefault(r["dashboard_id"], []).append(r)
    shown: dict[int, list] = {}
    for did, _, mid in _refs(conn, c["id"]):
        if mid in by_id:
            shown.setdefault(did, []).append(by_id[mid])
    out = []
    for d in conn.execute("SELECT id, code, name FROM dashboards WHERE client_id = ? ORDER BY position", (c["id"],)):
        if d["id"] in own or d["id"] in shown:
            out += ["#" * depth + f" {d['code']}" + (f": {d['name']}" if d["name"] else ""), ""]
        for r in own.get(d["id"], []):
            out += _measure_section(r, depth + 1, names, also.get(r["id"], ""))
        if d["id"] in shown:
            out += ["Also shows " + ", ".join(f"{r['name']} (defined on {r['code']})" for r in shown[d["id"]]) + ".", ""]
    return out


def _measure_section(r, depth: int, names: dict[int, str], also_on: str) -> list[str]:
    out = ["#" * depth + f" {r['name']}", ""]
    if r["description"]:
        out += [r["description"], ""]
    status = {"complete": "Agreed", "in_progress": "Draft", "not_started": "Not started"}[r["status"]]
    out += _table([
        ("Status", status + (" (needs follow-up)" if r["followup"] else "")),
        ("Definition owner", r["owner"] or r["owner_note"] or "Not yet named"),
        ("Subject matter expert", r["sme"] or r["sme_note"] or "Not yet named"),
        ("Business process", r["process"] or ""),
        ("Also on", also_on),
        ("Calculation", r["formula"]),
        ("Source systems", _sources_text(r)),
        ("Benchmark or target", r["target"] or ("None" if r["target_none"] else "")),
        ("Dimensions", _dims_text(r, names)),
        ("Breakdowns", r["breakdowns"] or ("Same as the current report" if r["breakdowns_same"] else "")),
        ("Date and periods", r["time_basis"]),
        ("Refresh", REFRESH_LABELS.get(r["refresh"], "") + (f" ({r['refresh_why']})" if r["refresh_why"] else "")),
        ("Changes to past numbers", r["restatement"] or ("Past numbers don't change" if r["restatement_none"] else "")),
        ("Trusted report", r["report"] or ("None today" if r["report_none"] else "")),
        ("Don't know yet", _unknown_text(json.loads(r["unknown"] or "{}"), db.QUESTION_LABELS)),
    ])
    if r["followup"] and r["followup_note"]:
        out += ["", f"> Open question: {r['followup_note']}"]
    out.append("")
    return out


def catalogue_md(conn, c: dict) -> str:
    """A measure catalogue in Markdown: pastes into Confluence, SharePoint, Notion or a repo wiki."""
    n = len(defined_rows(conn, c["id"]))
    return "\n".join([f"# {c['name']}: measure catalogue", "", f"{n} measures. Generated {db.now()[:10]}.", "",
                      *_measure_sections(conn, c, 2)])


def bus_csv(conn, c: dict) -> str:
    procs = items.list_items(conn, c["id"], "process")
    dims = items.list_items(conn, c["id"], "dimension")
    ticked = {(r[0], r[1]) for r in conn.execute(
        "SELECT b.process_id, b.dimension_id FROM bus_cells b JOIN processes p ON p.id = b.process_id WHERE p.client_id = ?",
        (c["id"],))}
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["business_process", "records", *[d["name"] for d in dims]])
    for p in procs:
        w.writerow([p["name"], p["answers"].get("event", ""), *["x" if (p["id"], d["id"]) in ticked else "" for d in dims]])
    return buf.getvalue()


def _owner_text(conn, item: dict) -> str:
    parts = []
    if item["owner_id"] is not None:
        parts.append(conn.execute("SELECT name FROM people WHERE id = ?", (item["owner_id"],)).fetchone()["name"])
    if item["owner_dept_id"] is not None:
        parts.append(conn.execute("SELECT name FROM departments WHERE id = ?", (item["owner_dept_id"],)).fetchone()["name"])
    return ", ".join(parts) or item["owner_note"]


def _item_status(item: dict) -> str:
    return {"complete": "Agreed", "in_progress": "Draft", "not_started": "Not started"}[item["status"]] \
        + (" (needs follow-up)" if item["followup"] else "")


def _systems_text(a: dict) -> str:
    return "; ".join(a.get("systems", []) + ([a["systems_other"]] if a.get("systems_other") else []))


def _none_or(a: dict, key: str, none_text: str) -> str:
    return a.get(key, "") or (none_text if a.get(f"{key}_none") else "")


def handbook_md(conn, c: dict) -> str:
    """Everything in one Markdown document, in the order the work is done."""
    cid = c["id"]
    procs = items.list_items(conn, cid, "process")
    dims = items.list_items(conn, cid, "dimension")
    people = {r["id"]: r["name"] for r in conn.execute("SELECT id, name FROM people WHERE client_id = ?", (cid,))}
    depts = {r["id"]: r["name"] for r in conn.execute("SELECT id, name FROM departments WHERE client_id = ?", (cid,))}
    ticked = {(r[0], r[1]) for r in conn.execute(
        "SELECT b.process_id, b.dimension_id FROM bus_cells b JOIN processes p ON p.id = b.process_id WHERE p.client_id = ?",
        (cid,))}
    out = [f"# {c['name']}: data warehouse requirements", "",
           f"{len(procs)} business processes, {len(dims)} dimensions, "
           f"{len(defined_rows(conn, cid))} measures. Generated {db.now()[:10]}.", ""]

    out += ["## Business processes", ""]
    for p in procs:
        a = p["answers"]
        out += [f"### {p['name'] or 'Unnamed process'}", ""]
        if a.get("purpose"):
            out += [a["purpose"], ""]
        out += _table([
            ("Status", _item_status(p)),
            ("Owner", _owner_text(conn, p) or "Not yet named"),
            ("Priority", items.CHOICES["priority"].get(a.get("priority", ""), "")),
            ("Start and end", a.get("boundaries", "")),
            ("What it records", a.get("event", "")),
            ("Systems", _systems_text(a)),
            ("Volume", a.get("volume", "")),
            ("Decisions it supports", a.get("decisions", "")),
            ("Pain points", _none_or(a, "pain_points", "Nothing major")),
            ("Don't know yet", _unknown_text(p["unknown"], items.QUESTIONS["process"])),
        ])
        steps = [s for s in p["steps"] if s["name"].strip()]
        if steps:
            out += ["", "| Step | Who | System |", "|---|---|---|"]
            out += [f"| {i}. {_cell(s['name'])} | {_cell(depts.get(s['dept_id']) or people.get(s['person_id']) or '')} "
                    f"| {_cell(s['system'])} |" for i, s in enumerate(steps, 1)]
        out.append("")

    out += ["## Bus matrix", "", "Business processes down the side, the dimensions they're analyzed by across the top.", ""]
    if procs and dims:
        out += ["| Process | " + " | ".join(_cell(d["name"]) for d in dims) + " |",
                "|---|" + "---|" * len(dims)]
        out += [f"| {_cell(p['name'])} | " + " | ".join("✓" if (p["id"], d["id"]) in ticked else "" for d in dims) + " |"
                for p in procs]
        out.append("")

    out += ["## Dimensions", ""]
    for d in dims:
        a = d["answers"]
        history = a.get("history", "")
        history_text = (f"{items.CHOICES['history'][history]}: {HISTORY_TECH[history]}" if history else "") \
            + (f". {a['history_note']}" if a.get("history_note") else "")
        out += [f"### {d['name'] or 'Unnamed dimension'}", ""]
        if a.get("definition"):
            out += [a["definition"], ""]
        out += _table([
            ("Status", _item_status(d)),
            ("Owner", _owner_text(conn, d) or "Not yet named"),
            ("Used by", ", ".join(p["name"] for p in procs if (p["id"], d["id"]) in ticked)),
            ("How one is identified", a.get("identifier", "")),
            ("Described by", a.get("attributes", "")),
            ("Roll-ups", _none_or(a, "hierarchy", "None")),
            ("Master system", _systems_text(a)),
            ("When it changes", history_text),
            ("Known data problems", _none_or(a, "quality", "None known")),
            ("Don't know yet", _unknown_text(d["unknown"], items.QUESTIONS["dimension"])),
        ])
        out.append("")

    out += ["## Measures", "", *_measure_sections(conn, c, 3)]

    out += ["## Dashboards", "", "The processes each dashboard's measures come from. A filter on a dimension "
            "every one of those processes is analyzed by works across the whole dashboard.", ""]
    defined = defined_rows(conn, cid)
    measure_names = {r["id"]: r["name"] for r in defined}
    process_of = {r["id"]: r["process_id"] for r in defined}
    proc_names = {p["id"]: p["name"] or "Unnamed process" for p in procs}
    dim_names = {d["id"]: d["name"] for d in dims}
    on_dash: dict[int, list[int]] = {}
    for r in conn.execute(
            """SELECT r.dashboard_id, COALESCE(r.ref_id, r.id) FROM requirements r JOIN dashboards d ON d.id = r.dashboard_id
               WHERE d.client_id = ? AND (r.name <> '' OR r.ref_id IS NOT NULL) ORDER BY r.slot""", (cid,)):
        on_dash.setdefault(r[0], []).append(r[1])
    for dash in conn.execute("SELECT * FROM dashboards WHERE client_id = ? ORDER BY position", (cid,)).fetchall():
        tiles = conn.execute("SELECT * FROM tiles WHERE dashboard_id = ? ORDER BY y, x", (dash["id"],)).fetchall()
        mids = [m for m in on_dash.get(dash["id"], []) if m in measure_names]
        if not tiles and not mids and not any(dash[k] for k in db.DASHBOARD_USE):
            continue
        pids = list(dict.fromkeys(process_of[m] for m in mids if process_of[m] in proc_names))
        n_ticked = {d["id"]: sum((p, d["id"]) in ticked for p in pids) for d in dims}
        out += [f"### {dash['code']}" + (f": {dash['name']}" if dash["name"] else ""), ""]
        out += _table([("Who uses it", dash["audience"]), ("Decisions", dash["decisions"]),
                       ("When it's used", dash["cadence"]), ("Access", dash["access"]),
                       ("Measures", ", ".join(measure_names[m] for m in mids)),
                       ("Processes", ", ".join(proc_names[p] for p in pids)),
                       ("Filters for the whole dashboard", ", ".join(dim_names[d] for d, n in n_ticked.items() if pids and n == len(pids))),
                       ("Filters for some measures only", ", ".join(dim_names[d] for d, n in n_ticked.items() if 0 < n < len(pids)))])
        if tiles:
            out += ["", "| Tile | Shows | By | Note |", "|---|---|---|---|"]
            out += [f"| {TILE_LABELS.get(t['kind'], t['kind'])}{': ' + _cell(t['title']) if t['title'] else ''} "
                    f"| {_cell(measure_names.get(t['requirement_id'], ''))} | {_cell(dim_names.get(t['dimension_id'], ''))} "
                    f"| {_cell(t['note'])} |" for t in tiles]
        out.append("")

    out += ["## Ownership", "", "| Department | Processes owned | Dimensions owned | Measures owned |", "|---|---|---|---|"]
    person_dept = {r["id"]: r["department_id"] for r in conn.execute(
        "SELECT id, department_id FROM people WHERE client_id = ?", (cid,))}

    def dept_of(item):
        """The owning department, "none" for an owner without one, or None when nobody owns it."""
        if item["owner_dept_id"] is not None:
            return item["owner_dept_id"]
        if item["owner_id"] is not None:
            return person_dept.get(item["owner_id"]) or "none"
        return "none" if item["owner_note"].strip() else None

    measures = [{"name": r["name"], "owner_id": r["owner_id"], "owner_note": r["owner_note"], "owner_dept_id": None}
                for r in conn.execute(
                    """SELECT r.name, r.owner_id, r.owner_note FROM requirements r JOIN dashboards d ON d.id = r.dashboard_id
                       WHERE d.client_id = ? AND r.name <> ''""", (cid,))]
    rows = [*sorted(depts.items(), key=lambda kv: kv[1].lower()), ("none", "No department"), (None, "Not owned yet")]
    for did, dname in rows:
        row = [", ".join(x["name"] or "Unnamed" for x in group if dept_of(x) == did) for group in (procs, dims, measures)]
        if any(row):
            out.append(f"| {_cell(dname)} | " + " | ".join(_cell(x) for x in row) + " |")
    out.append("")
    return "\n".join(out)
