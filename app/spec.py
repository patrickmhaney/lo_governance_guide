"""The requirements spec: one workspace as contract C8, the one thing the product
tells the target (docs/requirements_contract.md and
semantic/requirements_spec.schema.json in the target repo).

    GET /w/{slug}/requirements_spec.json

People are reduced to roles and a blank answer is left out. "I don't know" is
{"unknown": true, "who_might_know": <role>}; a single-box answer is {"none": true}.
The target bindings (metric names, codes, cubes, format, direction, backlog)
are engineer-set; everything else is what the business said.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from . import db, items

SCHEMA = Path("semantic") / "requirements_spec.schema.json"


def target_repo() -> Path | None:
    """The target checkout: TARGET_REPO, else a bgr_control_tower_v3 folder next to this repo."""
    env = os.environ.get("TARGET_REPO")
    if env:
        return Path(env).expanduser()
    sibling = Path(__file__).resolve().parents[2] / "bgr_control_tower_v3"
    return sibling if sibling.exists() else None


def snake(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_") or "x"


def code_of(text: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "_", text.upper()).strip("_") or "X"


def _unknown(who: str | None) -> dict:
    return {"unknown": True, **({"who_might_know": who} if (who or "").strip() else {})}


def _text(value: str, key: str, unknown: dict, none: bool = False):
    if key in unknown:
        return _unknown(unknown[key])
    if none:
        return {"none": True}
    return value if (value or "").strip() else None


def _clean(d: dict) -> dict:
    return {k: v for k, v in d.items() if v not in (None, "", [])}


def build_spec(conn, client_id: int) -> dict:
    c = dict(conn.execute("SELECT * FROM clients WHERE id = ?", (client_id,)).fetchone())
    people = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM people WHERE client_id = ?", (client_id,))}
    depts = [dict(r) for r in conn.execute(
        "SELECT * FROM departments WHERE client_id = ? ORDER BY COALESCE(position, 1000000), id", (client_id,))]
    dept_code = {d["id"]: d["code"] or code_of(d["name"]) for d in depts}

    def role(pid: int | None) -> str | None:
        p = people.get(pid)
        return (p["role"] or p["name"]) if p else None

    def owner(row: dict, unknown: dict):
        if "owner" in unknown:
            return _unknown(unknown["owner"])
        who = role(row.get("owner_id")) or (row.get("owner_note") or "").strip()
        return {"role": who} if who else None

    # Unnamed processes and dimensions are drafts: nothing can refer to them yet.
    procs = [items.item_dict(conn, "process", r) for r in conn.execute(
        "SELECT * FROM processes WHERE client_id = ? AND name <> '' ORDER BY position, id", (client_id,))]
    proc_code = {p["id"]: p["code"] or code_of(p["name"]) for p in procs}
    dims = [items.item_dict(conn, "dimension", r) for r in conn.execute(
        "SELECT * FROM dimensions WHERE client_id = ? AND name <> '' ORDER BY position, id", (client_id,))]
    dim_code = {d["id"]: d["code"] or snake(d["name"]) for d in dims}

    spec: dict = {
        "spec_version": 1,
        "project": {"slug": c["target_slug"] or snake(c["slug"]), "name": c["name"],
                    "systems": json.loads(c["sources"] or "[]")},
        "departments": [{"code": dept_code[d["id"]], "name": d["name"],
                         "display_order": d["position"] if d["position"] is not None else i}
                        for i, d in enumerate(depts, 1)],
    }

    out_procs = []
    for i, p in enumerate(procs, 1):
        a, unk = p["answers"], p["unknown"]
        entry = {"code": proc_code[p["id"]], "name": p["name"], "display_order": i,
                 "active": bool(p["active"]), "status": p["status"],
                 "followup": {"note": p["followup_note"]} if p["followup_note"] else None,
                 "owner": owner(p, unk), "owner_department": dept_code.get(p["owner_dept_id"])}
        for k in ("purpose", "boundaries", "event", "volume", "decisions"):
            entry[k] = _text(a.get(k, ""), k, unk)
        entry["pain_points"] = _text(a.get("pain_points", ""), "pain_points", unk, a.get("pain_points_none"))
        entry["systems"] = (_unknown(unk["systems"]) if "systems" in unk else
                            _clean({"systems": a.get("systems") or [], "other": a.get("systems_other", "")}) or None)
        entry["priority"] = _unknown(unk["priority"]) if "priority" in unk else (a.get("priority") or None)
        entry["steps"] = [_clean({"name": s["name"], "department": dept_code.get(s["dept_id"]),
                                  "role": role(s["person_id"]), "system": s["system"]})
                          for s in p["steps"] if s["name"].strip()]
        entry["notes"] = p["notes"]
        if entry["systems"] == {}:
            entry["systems"] = None
        out_procs.append(_clean(entry))
    spec["processes"] = out_procs

    out_dims = []
    for d in dims:
        a, unk = d["answers"], d["unknown"]
        entry = {"code": dim_code[d["id"]], "name": d["name"], "status": d["status"],
                 "followup": {"note": d["followup_note"]} if d["followup_note"] else None,
                 "owner": owner(d, unk), "owner_department": dept_code.get(d["owner_dept_id"])}
        for k in ("definition", "identifier", "attributes"):
            entry[k] = _text(a.get(k, ""), k, unk)
        entry["hierarchy"] = _text(a.get("hierarchy", ""), "hierarchy", unk, a.get("hierarchy_none"))
        entry["quality"] = _text(a.get("quality", ""), "quality", unk, a.get("quality_none"))
        entry["systems"] = (_unknown(unk["systems"]) if "systems" in unk else
                            _clean({"systems": a.get("systems") or [], "other": a.get("systems_other", "")}) or None)
        if "history" in unk:
            entry["history"] = _unknown(unk["history"])
        elif a.get("history"):
            entry["history"] = _clean({"choice": a["history"], "note": a.get("history_note", "")})
        entry["notes"] = d["notes"]
        out_dims.append(_clean(entry))
    spec["dimensions"] = out_dims
    spec["bus_matrix"] = [{"process": proc_code[r[0]], "dimension": dim_code[r[1]]} for r in conn.execute(
        """SELECT b.process_id, b.dimension_id FROM bus_cells b JOIN processes p ON p.id = b.process_id
           JOIN dimensions d ON d.id = b.dimension_id WHERE p.client_id = ? ORDER BY p.position, d.position""",
        (client_id,)) if r[0] in proc_code and r[1] in dim_code]

    # Measures: every cell where one is defined.
    cells = [db.requirement_dict(r) for r in conn.execute(
        """SELECT r.*, d.code AS dashboard_code FROM requirements r JOIN dashboards d ON d.id = r.dashboard_id
           WHERE d.client_id = ? ORDER BY d.position, r.slot""", (client_id,))]
    by_id = {r["id"]: r for r in cells}
    metric = {r["id"]: r["metric_name"] or snake(r["name"]) for r in cells if r["name"].strip() and r["ref_id"] is None}

    out_measures = []
    for r in cells:
        if r["id"] not in metric:
            continue
        unk, b = r["unknown"], r["binding"]
        entry = {"name": metric[r["id"]], "label": r["name"], "status": r["status"],
                 "followup": {"note": r["followup_note"]} if r["followup_note"] else None,
                 "definition": _text(r["description"], "definition", unk)}
        entry["calculation"] = _unknown(unk["formula"]) if "formula" in unk else (
            {"text": r["formula"]} if r["formula"].strip() else None)
        entry["sources"] = _unknown(unk["sources"]) if "sources" in unk else (
            _clean({"systems": r["sources"], "other": r["source_other"]}) or None)
        entry["target"] = _text(r["target"], "target", unk, r["target_none"])
        entry["trusted_report"] = _text(r["report"], "report", unk, r["report_none"])
        entry["breakdowns"] = _unknown(unk["breakdowns"]) if "breakdowns" in unk else (
            [dim_code[d] for d in r["dimension_ids"] if d in dim_code] or None)
        entry["time_basis"] = _text(r["time_basis"], "time", unk)
        entry["refresh"] = _unknown(unk["refresh"]) if "refresh" in unk else (
            _clean({"cadence": r["refresh"], "why": r["refresh_why"]}) if r["refresh"] else None)
        entry["restatement"] = _text(r["restatement"], "restatement", unk, r["restatement_none"])
        entry["owner"] = owner(r, unk)
        entry["sme"] = _unknown(unk["sme"]) if "sme" in unk else (
            {"role": role(r["sme_id"]) or r["sme_note"]} if (r["sme_id"] or r["sme_note"].strip()) else None)
        entry["caveats"] = [x.strip() for x in r["notes"].splitlines() if x.strip()]
        for k in ("format", "direction", "backlog"):
            entry[k] = b.get(k)
        out_measures.append(_clean(entry))
    spec["measures"] = out_measures

    # Process dashboards: rows whose code is a process code.
    codes = set(proc_code.values())
    out_dash = []
    for d in conn.execute("SELECT * FROM dashboards WHERE client_id = ? ORDER BY position", (client_id,)):
        if d["code"] not in codes:
            continue
        slots = []
        for r in cells:
            if r["dashboard_id"] != d["id"]:
                continue
            target = r["ref_id"] if r["ref_id"] is not None else r["id"]
            if target in metric:
                slots.append({"slot": f"M{r['slot']}", "measure": metric[target], "display_order": r["slot"]})
        tiles = [_clean({"kind": t["kind"], "x": t["x"], "y": t["y"], "w": t["w"], "h": t["h"], "title": t["title"],
                         "measure": metric.get(t["requirement_id"]), "dimension": dim_code.get(t["dimension_id"]),
                         "note": t["note"]})
                 for t in conn.execute("SELECT * FROM tiles WHERE dashboard_id = ? ORDER BY y, x, id", (d["id"],))]
        out_dash.append(_clean({"process": d["code"], **{k: d[k] for k in db.DASHBOARD_USE}, "slots": slots,
                                "tiles": tiles}) | {"slots": slots})
    spec["dashboards"] = out_dash

    spec["department_metrics"] = [
        {"department": dept_code[r["department_id"]], "process": proc_code[r["process_id"]],
         "measure": metric[r["requirement_id"]], "influence": r["influence"]}
        for r in conn.execute(
            """SELECT m.* FROM measure_departments m JOIN requirements r ON r.id = m.requirement_id
               JOIN dashboards d ON d.id = r.dashboard_id WHERE d.client_id = ? ORDER BY m.position, m.id""",
            (client_id,))
        if r["process_id"] in proc_code and r["requirement_id"] in metric]
    spec["cubes"] = [{"name": r["name"], "description": r["description"], "measures": json.loads(r["measures"]),
                      "group_by": json.loads(r["group_by"])}
                     for r in conn.execute("SELECT * FROM cubes WHERE client_id = ? ORDER BY position, id",
                                           (client_id,))]
    del by_id
    return spec


def validate(spec: dict, repo: Path | None = None) -> list[str]:
    """Check the spec against the target's JSON Schema. Empty when it passes."""
    import jsonschema

    repo = repo or target_repo()
    if repo is None or not (repo / SCHEMA).exists():
        return [f"no target repo with {SCHEMA}: set TARGET_REPO"]
    schema = json.loads((repo / SCHEMA).read_text())
    v = jsonschema.Draft202012Validator(schema)
    return [f"{'/'.join(map(str, e.absolute_path)) or '(top)'}: {e.message}" for e in v.iter_errors(spec)]
