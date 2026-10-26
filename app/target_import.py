"""Build a workspace from a target pack: the compiler (compiler.py) run backwards.

    python -m app.target_import --target ../bgr_control_tower_v3 --rev 269ad97 \\
        --slug sample-v2 --name "Sample Project v2" --pack sample_v2

Reads the target's files as they were at a pinned commit (`git show`), never
its working tree, and creates one workspace with contract C8's mapping
(docs/requirements_contract.md in the target):

- dashboards and slots from process_metric.csv; a measure on two process
  dashboards is defined on the first, in process order, and shown on the other
- measures from metrics.yml (KPIs only - component metrics are implementation)
  and backlog.yml; measures on no process dashboard sit on one extra row
- which departments own or contribute to each measure from department_metric.csv
- departments, processes and their codes from department.csv and process.csv
- dimensions from the gold dim_* models, their known problems from docs/sources.md
- the bus matrix from the semantic models' entities: a process ticks a
  dimension when a measure on its dashboard can be grouped by it
- cubes from saved_queries.yml, and a sketch per process dashboard from its cube

Questions the target can't answer - targets, trusted reports, subject-matter
experts, process steps and volumes - are "I don't know"; an owner of TBD with a
proposed owner is "I don't know, who might know: <role>". People stay roles.
Back up data/requirements.db before running it against the real database.
"""
from __future__ import annotations

import argparse
import ast
import csv
import io
import json
import re
import subprocess
from pathlib import Path

import yaml

from . import db, items

#: Where each file lives: before the target had packs (269ad97), and in a pack.
LEGACY = {"metrics": "transform/models/semantic/metrics.yml",
          "saved_queries": "transform/models/semantic/saved_queries.yml",
          "semantic_models": "transform/models/semantic/semantic_models.yml",
          "gold": "transform/models/gold/_gold__models.yml",
          "backlog": "semantic/backlog.yml", "seeds": "transform/seeds", "models": "transform/models",
          "sources_doc": "docs/sources.md", "specs": "ingestion/specs", "studio": "apps/studio/data.py"}
PACK = {"metrics": "models/semantic/metrics.yml", "saved_queries": "models/semantic/saved_queries.yml",
        "semantic_models": "models/semantic/semantic_models.yml", "gold": "models/gold/_gold__models.yml",
        "backlog": "backlog.yml", "seeds": "seeds", "models": "models", "sources_doc": "docs/sources.md",
        "specs": "ingestion/specs", "studio": None}

#: The row for measures that no process dashboard shows yet.
OTHER = ("OTHER", "Not on a process dashboard")
#: Dimension codes that are an attribute of another entity in the semantic layer.
VIA = {"department": "employee"}
#: Words that tie a landmine in docs/sources.md to a dimension.
QUALITY_WORDS = {"customer": ("customer",), "employee": ("employee", "user", "owner"),
                 "item": ("item",), "site": ("site", "location"), "supplier": ("supplier",)}


class Target:
    """The target's files at one commit."""

    def __init__(self, repo: Path, rev: str, pack: str):
        self.repo, self.rev = Path(repo), rev
        listing = self._git("ls-tree", "-r", "--name-only", rev).splitlines()
        self.files = set(listing)
        prefix = f"projects/{pack}/"
        if any(f.startswith(prefix) for f in self.files):
            self.paths = {k: v and prefix + v for k, v in PACK.items()}
        else:
            self.paths = LEGACY

    def _git(self, *args: str) -> str:
        return subprocess.run(["git", "-C", str(self.repo), *args], check=True, capture_output=True, text=True).stdout

    def text(self, key: str, name: str = "") -> str:
        path = self.paths[key] + (f"/{name}" if name else "")
        return self._git("show", f"{self.rev}:{path}")

    def yaml(self, key: str, name: str = ""):
        return yaml.safe_load(self.text(key, name))

    def csv(self, name: str) -> list[dict]:
        return list(csv.DictReader(io.StringIO(self.text("seeds", name))))

    def under(self, key: str) -> list[str]:
        base = self.paths[key] + "/"
        return sorted(f for f in self.files if f.startswith(base))


# ---------------------------------------------------------------------------
# Reading the target
# ---------------------------------------------------------------------------

def systems_registry(repo: Path) -> dict[str, dict]:
    """The architecture's system registry (sources/systems.yml), from the target's working tree."""
    path = Path(repo) / "sources" / "systems.yml"
    return yaml.safe_load(path.read_text()) if path.exists() else {}


def model_systems(t: Target) -> dict[str, set[str]]:
    """The source systems behind every dbt model, by following ref() down to staging."""
    refs: dict[str, set[str]] = {}
    for path in t.under("models"):
        if path.endswith(".sql"):
            sql = t._git("show", f"{t.rev}:{path}")
            refs[Path(path).stem] = set(re.findall(r"ref\(\s*'([a-z0-9_]+)'\s*\)", sql))
    memo: dict[str, set[str]] = {}

    def walk(name: str, seen=()) -> set[str]:
        if name in memo:
            return memo[name]
        m = re.match(r"^(?:stg|bronze)_([a-z0-9]+(?:_[a-z0-9]+)*?)__", name)
        out = {m.group(1)} if m else set()
        for r in refs.get(name, ()):
            if r not in seen:
                out |= walk(r, (*seen, name))
        memo[name] = out
        return out

    # A staging or bronze model's name gives its system, so this works for a pack too,
    # whose staging adapters live in the platform package rather than in its own files.
    return {name: walk(name) for name in refs}


def semantic_index(t: Target):
    """{metric: semantic models it reads}, {model: entities}, {model: table}, {model: agg time dimension}."""
    sms = t.yaml("semantic_models")["semantic_models"]
    metrics = {m["name"]: m for m in t.yaml("metrics")["metrics"]}
    model_of_measure, entities, table, time_dim = {}, {}, {}, {}
    for sm in sms:
        table[sm["name"]] = re.search(r"ref\('([a-z0-9_]+)'\)", sm["model"]).group(1)
        entities[sm["name"]] = [e["name"] for e in sm.get("entities", [])]
        time_dim[sm["name"]] = (sm.get("defaults") or {}).get("agg_time_dimension")
        for ms in sm.get("measures", []):
            model_of_measure[ms["name"]] = sm["name"]

    def models(name: str) -> set[str]:
        m = metrics.get(name)
        if not m:
            return set()
        tp = m.get("type_params") or {}
        if m["type"] in ("simple", "cumulative"):
            measure = tp.get("measure")
            measure = measure["name"] if isinstance(measure, dict) else measure
            return {model_of_measure[measure]} if measure in model_of_measure else set()
        names = [tp.get("numerator"), tp.get("denominator")] if m["type"] == "ratio" else \
            [x["name"] if isinstance(x, dict) else x for x in tp.get("metrics", [])]
        out = set()
        for n in names:
            out |= models(n["name"] if isinstance(n, dict) else n)
        return out

    return {name: models(name) for name in metrics}, entities, table, time_dim


def _process_names(t: Target) -> dict[str, str]:
    """Process names as the Studio spells them (process.csv deliberately has none)."""
    if not t.paths.get("studio"):
        return {}
    src = t.text("studio")
    m = re.search(r"^TITLES = (\{.*?\})\n", src, re.S | re.M)
    titles = ast.literal_eval(m.group(1)) if m else {}
    return {k.removeprefix("mart_").upper(): v for k, v in titles.items() if re.fullmatch(r"mart_[a-z]2[a-z]", k)}


def _landmines(t: Target) -> list[str]:
    try:
        doc = t.text("sources_doc")
    except subprocess.CalledProcessError:
        return []
    section = doc.split("## Landmines", 1)[-1].split("\n## ", 1)[0]
    return [" ".join(x.split()) for x in re.findall(r"^\d+\.\s+(.*?)(?=^\d+\.|\Z)", section, re.M | re.S)]


def _strip(text: str | None) -> str:
    return text or ""


# ---------------------------------------------------------------------------
# Building the workspace
# ---------------------------------------------------------------------------

def build(conn, t: Target, repo: Path, slug: str, name: str, pack: str) -> int:
    metrics = t.yaml("metrics")["metrics"]
    kpis = [m for m in metrics if ((m.get("config") or {}).get("meta") or {}).get("role") == "kpi"]
    labels = {m["name"]: m.get("label") or m["name"] for m in metrics}
    backlog = t.yaml("backlog")["metrics"]
    cubes = t.yaml("saved_queries")["saved_queries"]
    processes = t.csv("process.csv")
    process_metric = t.csv("process_metric.csv")
    department_metric = t.csv("department_metric.csv")
    departments = t.csv("department.csv")
    gold = {m["name"]: m for m in t.yaml("gold")["models"]}
    reads, entities, tables, time_dims = semantic_index(t)
    lineage = model_systems(t)
    registry = systems_registry(repo)
    display = {s: (registry.get(s) or {}).get("name", s) for s in registry}
    with_spec = {yaml.safe_load(t._git("show", f"{t.rev}:{path}"))["source"] for path in t.under("specs")}
    # In the registry's order, which is the order the pipeline extracts them in.
    spec_systems = [s for s in registry if s in with_spec] + sorted(with_spec - set(registry))
    names = _process_names(t)

    def systems_of_metric(metric: str) -> list[str]:
        found = set()
        for sm in reads.get(metric, ()):
            found |= lineage.get(tables[sm], set())
        return [display.get(s, s) for s in spec_systems if s in found]

    # The workspace, one dashboard row per process (in process order), then the rest.
    order = sorted(processes, key=lambda p: int(p["display_order"]))
    shown = {(r["process_code"], r["metric_name"]) for r in process_metric}
    on_dash = {r["metric_name"] for r in process_metric}
    measures = [*kpis, *({"name": b["name"], "backlog": b} for b in backlog)]
    other = [m["name"] for m in measures if m["name"] not in on_dash]
    slots_needed = max([int(r["slot"][1:]) for r in process_metric] + [len(other), db.DEFAULT_SLOTS])
    if slots_needed > db.MAX_SLOTS:
        raise SystemExit(f"{slots_needed} slots needed; the product allows {db.MAX_SLOTS}")
    rows = [(p["process_code"], names.get(p["process_code"], p["process_code"])) for p in order]
    if other:
        rows.append(OTHER)
    cid = db.create_client(conn, name, rows, slots=slots_needed,
                           sources=[display.get(s, s) for s in spec_systems], slug=slug)
    conn.execute("UPDATE clients SET target_slug = ? WHERE id = ?", (pack, cid))
    dash = {r["code"]: r["id"] for r in conn.execute("SELECT id, code FROM dashboards WHERE client_id = ?", (cid,))}

    def cell(code: str, slot: int) -> int:
        return conn.execute("SELECT id FROM requirements WHERE dashboard_id = ? AND slot = ?",
                            (dash[code], slot)).fetchone()[0]

    # Departments, with their codes and display order.
    dept = {}
    for d in departments:
        dept[d["department_code"]] = conn.execute(
            "INSERT INTO departments (client_id, name, code, position) VALUES (?, ?, ?, ?)",
            (cid, d["department_name"], d["department_code"], int(d["display_order"]))).lastrowid

    # Dimensions: the gold dim_* models, coded by the entity the semantic layer joins on.
    landmines = _landmines(t)
    dims = {}
    for model_name, m in gold.items():
        if not model_name.startswith("dim_"):
            continue
        code = model_name.removeprefix("dim_")
        cols = [c["name"] for c in m.get("columns", [])]
        key = next((c for c in cols if c.endswith(("_key", "_code", "_day"))), cols[0] if cols else "")
        found = lineage.get(model_name, set())
        quality = [x for x in landmines if any(w in x.lower() for w in QUALITY_WORDS.get(code, ()))]
        answers = {"definition": " ".join(_strip(m.get("description")).split()),
                   "identifier": f"{key} in gold.{model_name}" if key else "",
                   "attributes": ", ".join(c for c in cols if c != key),
                   "systems": [display.get(s, s) for s in spec_systems if s in found]}
        unknown = {"hierarchy": "", "history": "", "owner": ""}
        if quality:
            answers["quality"] = "\n".join(quality)
        else:
            unknown["quality"] = ""
        if not answers["systems"]:
            answers.pop("systems")
            unknown["systems"] = ""
        iid = items.create_item(conn, cid, "dimension", code.replace("_", " ").title(), answers)
        conn.execute("UPDATE dimensions SET code = ?, unknown = ?, status = 'in_progress', updated_at = ? WHERE id = ?",
                     (code, json.dumps(unknown), db.now(), iid))
        dims[code] = iid

    # Processes: code, order, active. The target can't answer any of the questions but systems.
    procs = {}
    for p in order:
        code = p["process_code"]
        metric_systems = []
        for r in process_metric:
            if r["process_code"] == code:
                metric_systems += systems_of_metric(r["metric_name"])
        systems = list(dict.fromkeys(metric_systems))
        unknown = {k: "" for k in items.QUESTIONS["process"] if k not in ("systems",)}
        if not systems:
            unknown["systems"] = ""
        iid = items.create_item(conn, cid, "process", names.get(code, code), {"systems": systems} if systems else {})
        conn.execute("UPDATE processes SET code = ?, active = ?, unknown = ?, status = 'in_progress', updated_at = ? "
                     "WHERE id = ?", (code, int(p["is_active"]), json.dumps(unknown), db.now(), iid))
        procs[code] = iid

    # The bus matrix: a measure on the process's dashboard can be grouped by the dimension.
    def groupable(metric: str) -> set[str]:
        out = {"date"} if reads.get(metric) else set()
        for sm in reads.get(metric, ()):
            out |= set(entities[sm])
        out |= {code for code, via in VIA.items() if via in out}
        return out & set(dims)

    for code in procs:
        ticks = set()
        for r in process_metric:
            if r["process_code"] == code:
                ticks |= groupable(r["metric_name"])
        for d in sorted(ticks):
            conn.execute("INSERT INTO bus_cells (process_id, dimension_id) VALUES (?, ?)", (procs[code], dims[d]))

    # Cubes, and each measure's breakdowns from the cubes that carry it.
    cube_dims: dict[str, set[str]] = {}
    for pos, c in enumerate(cubes):
        qp = c["query_params"]
        conn.execute("INSERT INTO cubes (client_id, position, name, description, measures, group_by) "
                     "VALUES (?, ?, ?, ?, ?, ?)",
                     (cid, pos, c["name"], c.get("description", ""), json.dumps(qp["metrics"]),
                      json.dumps(qp.get("group_by") or [])))
        grain = set()
        for g in qp.get("group_by") or []:
            if g.startswith("TimeDimension"):
                grain.add("date")
            else:
                ent, attr = re.search(r"Dimension\('([a-z0-9_]+)__([a-z0-9_]+)'\)", g).groups()
                grain.add("department" if ent == "employee" and attr.startswith("department") else ent)
        for m in qp["metrics"]:
            cube_dims.setdefault(m, set()).update(grain & set(dims))

    # Measures: where each is defined (the first process slot that shows it, else the extra row).
    defined_at: dict[str, int] = {}
    for p in order:
        for r in sorted((r for r in process_metric if r["process_code"] == p["process_code"]),
                        key=lambda r: int(r["slot"][1:])):
            if r["metric_name"] not in defined_at:
                defined_at[r["metric_name"]] = cell(r["process_code"], int(r["slot"][1:]))
    for i, metric in enumerate(other, 1):
        defined_at[metric] = cell(OTHER[0], i)
    first_process = {}
    for p in order:
        for r in process_metric:
            if r["process_code"] == p["process_code"]:
                first_process.setdefault(r["metric_name"], procs[p["process_code"]])

    for m in measures:
        metric = m["name"]
        b = m.get("backlog")
        meta = {} if b else (m.get("config") or {}).get("meta") or {}
        src = b or meta
        owner, proposed = src.get("owner"), src.get("owner_proposed")
        unknown = {"target": proposed or "", "report": "", "sme": proposed or "", "restatement": ""}
        row = {"name": (b or m).get("label") or metric, "metric_name": metric,
               "description": _strip((b or m).get("description")), "updated_at": db.now(),
               "process_id": first_process.get(metric),
               "dimension_ids": json.dumps([dims[d] for d in dims if d in cube_dims.get(metric, set())])}
        if owner in (None, "", "TBD"):
            unknown["owner"] = proposed or ""
        else:
            row["owner_note"] = owner
        systems = systems_of_metric(metric)
        if systems:
            row["sources"] = json.dumps(systems)
        else:
            unknown["sources"] = ""
        if b:
            unknown["formula"] = proposed or ""
            unknown["time"] = proposed or ""
        else:
            tp = m.get("type_params") or {}
            if m["type"] == "ratio":
                num, den = (tp[k]["name"] if isinstance(tp[k], dict) else tp[k] for k in ("numerator", "denominator"))
                row["formula"] = f"{labels.get(num, num)} divided by {labels.get(den, den)}."
            else:
                unknown["formula"] = ""
            sm = sorted(reads.get(metric, ()))
            basis = next((time_dims[s] for s in sm if time_dims.get(s)), None)
            if basis:
                row["time_basis"] = f"{basis.replace('_', ' ').capitalize()}. Calendar months."
            else:
                unknown["time"] = ""
        if not cube_dims.get(metric):
            unknown["breakdowns"] = ""
        row["refresh"] = "daily"
        row["refresh_why"] = "The pipeline runs every morning."
        caveats = src.get("caveats") or []
        row["notes"] = "\n".join(caveats)
        binding = {k: meta[k] for k in ("format", "direction") if meta.get(k)}
        if b:
            reason = b.get("blocked_reason") if b["status"] == "blocked" else b.get("placeholder_reason")
            binding["backlog"] = {"status": b["status"], "reason": _strip(reason)}
            if b.get("unblock_requires"):
                binding["backlog"]["unblock_requires"] = b["unblock_requires"]
        row["binding"] = json.dumps(binding)
        status = "complete" if meta.get("status") == "active" else "in_progress"
        row["status"] = status
        if status == "complete":
            row["completed_at"] = db.now()
        reason = _strip(meta.get("provisional_reason"))
        if reason:
            row.update(followup=1, followup_note=reason)
        row["unknown"] = json.dumps(unknown)
        conn.execute(f"UPDATE requirements SET {', '.join(f'{k} = ?' for k in row)} WHERE id = ?",
                     (*row.values(), defined_at[metric]))

    # A measure shown on a second process dashboard points at the cell that defines it.
    for r in process_metric:
        rid = cell(r["process_code"], int(r["slot"][1:]))
        if rid != defined_at[r["metric_name"]]:
            conn.execute("UPDATE requirements SET ref_id = ? WHERE id = ?", (defined_at[r["metric_name"]], rid))

    # Which departments own or contribute to each measure, in which process.
    for pos, r in enumerate(department_metric):
        conn.execute("INSERT INTO measure_departments (requirement_id, department_id, process_id, influence, position) "
                     "VALUES (?, ?, ?, ?, ?)", (defined_at[r["metric_name"]], dept[r["department_code"]],
                                                procs.get(r["process_code"]), r["influence"], pos))

    # A sketch per process dashboard from its cube: slicers, headline numbers, a trend and a comparison.
    for code in procs:
        cube = next((c for c in cubes if c["name"] == f"mart_{code.lower()}"), None)
        mine = [r["metric_name"] for r in sorted((r for r in process_metric if r["process_code"] == code),
                                                 key=lambda r: int(r["slot"][1:]))]
        mine = [m for m in mine if m in defined_at and cube and m in cube["query_params"]["metrics"]]
        if not cube or not mine:
            continue
        grain = [g for g in cube["query_params"].get("group_by") or [] if not g.startswith("TimeDimension")]
        filters = ["date"] + [re.search(r"Dimension\('([a-z0-9_]+)__", g).group(1) for g in grain]
        filters = [d for d in dict.fromkeys(filters) if d in dims]
        tiles = [("filter", 3 * i, 0, 3, 1, None, d, "") for i, d in enumerate(filters[:4])]
        tiles += [("kpi", 3 * i, 1, 3, 2, m, None, "") for i, m in enumerate(mine[:4])]
        tiles.append(("line", 0, 3, 6, 3, mine[0], "date", f"{labels.get(mine[0], mine[0])} by month"))
        if len(filters) > 1:
            tiles.append(("bar", 6, 3, 6, 3, mine[0], filters[1], ""))
        for kind, x, y, w, h, metric, dim, title in tiles:
            conn.execute("INSERT INTO tiles (dashboard_id, kind, x, y, w, h, title, requirement_id, dimension_id, note) "
                         "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, '')",
                         (dash[code], kind, x, y, w, h, title, defined_at.get(metric), dims.get(dim)))
    return cid


def main() -> None:
    p = argparse.ArgumentParser(prog="python -m app.target_import", description=__doc__.split("\n")[0])
    p.add_argument("--target", required=True, type=Path, help="a checkout of the target repo")
    p.add_argument("--rev", required=True, help="the commit to read, e.g. 269ad97")
    p.add_argument("--slug", required=True, help="the new workspace's slug")
    p.add_argument("--name", required=True, help="its display name - never a client's name")
    p.add_argument("--pack", required=True, help="the target pack it compiles to (projects/<pack>/)")
    p.add_argument("--replace", action="store_true", help="delete a workspace with this slug first")
    a = p.parse_args()
    db.init_db()
    t = Target(a.target, a.rev, a.pack)
    with db.transaction() as conn:
        if a.replace:
            conn.execute("DELETE FROM clients WHERE slug = ?", (a.slug,))
        elif conn.execute("SELECT 1 FROM clients WHERE slug = ?", (a.slug,)).fetchone():
            raise SystemExit(f"workspace {a.slug!r} exists; pass --replace to rebuild it")
        build(conn, t, a.target, a.slug, a.name, a.pack)
    print(f"Imported {a.target}@{a.rev} as /w/{a.slug} ({a.name})")


if __name__ == "__main__":
    main()
