"""The compiler: a requirements spec (contract C8) -> a target pack's "what" files.

    python -m app.compiler --workspace sample-v2 --pack ../bgr_control_tower_v3/projects/sample_v2
    python -m app.compiler --spec spec.json --pack ...  [--check]

It writes only files the target already understands, and only what the
requirements own:

    metrics.yml          a KPI's label, description and governance meta
                         (status, owner, provisional_reason, caveats, format,
                         direction, requirement); never its type or inputs
    backlog.yml          measures with no number yet, and measures defined in the
                         product that have no MetricFlow definition (placeholder)
    saved_queries.yml    the cubes
    seeds/process.csv, process_metric.csv, department_metric.csv, department.csv
    project.yml          the project's name and systems

Everything else in those files is the "how" and stays as the engineers wrote
it. An entry whose requirements did not change is left byte for byte, so a
requirement edited in the product lands as a small reviewable diff, gated by
the target's own checks (check-pack, semantic.lint, the golden answers).
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml as pyyaml
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq
from ruamel.yaml.scalarstring import DoubleQuotedScalarString, FoldedScalarString

#: The order governance keys appear in, in a metric's meta and a backlog entry.
META_ORDER = ("role", "status", "owner", "owner_proposed", "provisional_reason", "caveats", "format",
              "direction", "is_data_quality", "requirement")
METRIC_ORDER = ("name", "label", "description", "type", "type_params", "filter", "config")
BACKLOG_ORDER = ("name", "label", "status", "owner", "owner_proposed", "description", "blocked_reason",
                 "placeholder_reason", "unblock_requires")
NO_DEFINITION = "Defined in the requirements tool; it has no MetricFlow definition yet."
IN_PROGRESS = "Still in progress in the requirements tool."


class CompileError(ValueError):
    pass


@dataclass
class Result:
    changed: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Round-trip YAML, one list entry at a time
# ---------------------------------------------------------------------------

def _yaml() -> YAML:
    y = YAML()
    y.preserve_quotes = True
    y.width = 4096          # keep every folded string's own line breaks; new text gets _folded()
    y.indent(mapping=2, sequence=4, offset=2)
    return y


def _folded(text: str, width: int = 76) -> FoldedScalarString:
    """New long text as a `>` block, folded at spaces near `width`."""
    s = FoldedScalarString(text if text.endswith("\n") else text + "\n")
    folds, line_start = [], 0
    for i, ch in enumerate(s):
        if ch == " " and i - line_start > width:
            prev = s.rfind(" ", line_start, i)
            at = prev if prev > line_start else i
            folds.append(at)
            line_start = at + 1
    s.fold_pos = folds
    return s


def _indented(dumped: str, indent: int) -> str:
    """A dumped one-item sequence, moved so its dash sits at column `indent`."""
    lines = dumped.splitlines(keepends=True)
    have = len(lines[0]) - len(lines[0].lstrip())
    return "".join(" " * indent + ln[have:] if ln.strip() else ln for ln in lines)


def _plain(node):
    return json.loads(json.dumps(node, default=str))


class YamlList:
    """A YAML file with one top-level list of named entries (metrics, saved_queries).

    An entry is edited by loading its own lines, changing them and dumping them
    back. Entries that end up equal to what was there keep their original text.
    """

    def __init__(self, path: Path, key: str):
        self.path, self.key = path, key
        self.text = path.read_text()
        self.lines = self.text.splitlines(keepends=True)
        doc = _yaml().load(self.text)
        self.seq = doc[key]
        self.edits: dict[int, str] = {}       # index -> replacement text ("" removes)
        self.appended: list[str] = []

    def names(self) -> list[str]:
        return [item["name"] for item in self.seq]

    def index(self, name: str) -> int | None:
        return next((i for i, item in enumerate(self.seq) if item["name"] == name), None)

    def _span(self, i: int) -> tuple[int, int, int]:
        start, col = self.seq.lc.item(i)
        end = self.seq.lc.item(i + 1)[0] if i + 1 < len(self.seq) else len(self.lines)
        while end - 1 > start and (not self.lines[end - 1].strip() or self.lines[end - 1].lstrip().startswith("#")):
            end -= 1
        return start, end, col - 2

    def edit(self, name: str, change) -> bool:
        """Apply change(entry) to a round-trip copy of one entry. True when it changed."""
        i = self.index(name)
        start, end, indent = self._span(i)
        before = _plain(self.seq[i])
        y = _yaml()
        block = "".join(ln[indent:] if ln.strip() else ln for ln in self.lines[start:end])
        item = y.load(block)[0]
        change(item)
        if _plain(item) == before:
            return False
        out = io.StringIO()
        y.dump(CommentedSeq([item]), out)
        self.edits[i] = _indented(out.getvalue(), indent)
        return True

    def remove(self, name: str) -> None:
        self.edits[self.index(name)] = ""

    def append(self, item: CommentedMap) -> None:
        indent = self._span(len(self.seq) - 1)[2] if len(self.seq) else 2
        out = io.StringIO()
        _yaml().dump(CommentedSeq([item]), out)
        self.appended.append("\n" + _indented(out.getvalue(), indent))

    def render(self) -> str:
        lines = list(self.lines)
        for i in sorted(self.edits, reverse=True):
            start, end, _ = self._span(i)
            if self.edits[i] == "":
                # Take the blank line that separated it, so the spacing stays even.
                while end < len(lines) and not lines[end].strip():
                    end += 1
            lines[start:end] = [self.edits[i]]
        text = "".join(lines)
        if self.appended:
            if not len(self.seq):
                text = text.replace(f"{self.key}: []", f"{self.key}:", 1)
            text = text.rstrip("\n") + "\n" + "".join(self.appended)
        return text


def _ordered(mapping: CommentedMap, order: tuple[str, ...], key: str, value) -> None:
    """Set mapping[key], inserting a new key where `order` puts it."""
    if key in mapping:
        old = mapping[key]
        if old == value:
            return
        if isinstance(value, str) and (isinstance(old, FoldedScalarString) or len(value) > 80):
            value = _folded(value)
        mapping[key] = value
        return
    if isinstance(value, str) and len(value) > 80:
        value = _folded(value)
    rank = order.index(key) if key in order else len(order)
    pos = len(mapping)
    for j, k in enumerate(mapping):
        if k in order and order.index(k) > rank:
            pos = j
            break
    mapping.insert(pos, key, value)


def _set(mapping: CommentedMap, order: tuple[str, ...], key: str, value) -> None:
    if value in (None, "", []):
        mapping.pop(key, None)
    else:
        _ordered(mapping, order, key, value)


def _flow(values: list) -> CommentedSeq:
    s = CommentedSeq(values)
    s.fa.set_flow_style()
    return s


# ---------------------------------------------------------------------------
# The spec, checked and read
# ---------------------------------------------------------------------------

def _answer(value) -> str | None:
    return value if isinstance(value, str) else None


def _unknown_role(value) -> str | None:
    if isinstance(value, dict) and value.get("unknown"):
        return value.get("who_might_know")
    return None


def check_references(spec: dict) -> list[str]:
    """What the schema can't check (docs/requirements_contract.md)."""
    problems = []
    names = [m["name"] for m in spec["measures"]]
    labels = [m["label"].strip().lower() for m in spec["measures"]]
    for n in {n for n in names if names.count(n) > 1}:
        problems.append(f"measure {n!r} is defined twice")
    for lab in {x for x in labels if labels.count(x) > 1}:
        problems.append(f"label {lab!r} is used by two measures")
    known = set(names)
    procs = {p["code"] for p in spec["processes"]}
    depts = {d["code"] for d in spec["departments"]}
    dims = {d["code"] for d in spec["dimensions"]}
    for d in spec["dashboards"]:
        if d["process"] not in procs:
            problems.append(f"dashboard {d['process']!r} is not a process")
        for s in d["slots"]:
            if s["measure"] not in known:
                problems.append(f"{d['process']}-{s['slot']}: {s['measure']!r} is not a measure")
        for t in d.get("tiles", []):
            if t.get("measure") and t["measure"] not in known:
                problems.append(f"{d['process']} tile: {t['measure']!r} is not a measure")
            if t.get("dimension") and t["dimension"] not in dims:
                problems.append(f"{d['process']} tile: {t['dimension']!r} is not a dimension")
    for r in spec.get("department_metrics", []):
        if r["department"] not in depts or r["process"] not in procs or r["measure"] not in known:
            problems.append(f"department row {r}: an unknown department, process or measure")
    for b in spec["bus_matrix"]:
        if b["process"] not in procs or b["dimension"] not in dims:
            problems.append(f"bus matrix {b}: an unknown process or dimension")
    for c in spec["cubes"]:
        for m in c["measures"]:
            if m not in known:
                problems.append(f"cube {c['name']}: {m!r} is not a measure")
    return problems


def requirement_keys(spec: dict) -> dict[str, str]:
    """Each measure's first slot: <process>-<slot>, in process order then slot order."""
    order = {p["code"]: p["display_order"] for p in spec["processes"]}
    out: dict[str, str] = {}
    for d in sorted(spec["dashboards"], key=lambda d: order.get(d["process"], 10 ** 6)):
        for s in sorted(d["slots"], key=lambda s: int(s["slot"][1:])):
            out.setdefault(s["measure"], f"{d['process']}-{s['slot']}")
    return out


def governance(m: dict, requirement: str | None) -> dict:
    """The meta the requirements own, for one measure."""
    owner = m.get("owner") or {"unknown": True}
    note = (m.get("followup") or {}).get("note")
    provisional = m["status"] != "complete"
    if provisional and not (note or "").strip():
        note = IN_PROGRESS
    return {"status": "provisional" if provisional else "active",
            "owner": owner.get("role") or "TBD",
            "owner_proposed": _unknown_role(owner),
            "provisional_reason": note if provisional else None,
            "caveats": m.get("caveats") or [],
            "format": m.get("format"), "direction": m.get("direction"),
            "requirement": requirement}


def systems_slugs(spec: dict, registry: dict) -> tuple[list[str], list[str]]:
    """The architecture's slugs for the project's systems, by name or type; and the ones it lacks."""
    by = {}
    for slug, s in registry.items():
        for label in (slug, s.get("name", ""), s.get("type", "")):
            by.setdefault(label.strip().lower(), slug)
    out, missing = [], []
    for name in spec["project"]["systems"]:
        slug = by.get(name.strip().lower())
        if slug is None:
            missing.append(name)
        elif slug not in out:
            out.append(slug)
    return out, missing


# ---------------------------------------------------------------------------
# Writing the pack
# ---------------------------------------------------------------------------

def _csv_text(header: list[str], rows: list[list]) -> str:
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\n")
    w.writerow(header)
    w.writerows(rows)
    return out.getvalue()


def compile_spec(spec: dict, pack: Path, registry: dict) -> tuple[dict[Path, str], Result]:
    """The pack's what files as they should be: {path: text} for the files that change."""
    problems = check_references(spec)
    if problems:
        raise CompileError("; ".join(problems))
    res = Result()
    files: dict[Path, str] = {}
    req = requirement_keys(spec)
    measures = [m for m in spec["measures"] if m["status"] != "not_started" or m.get("backlog")]
    skipped = [m["name"] for m in spec["measures"] if m not in measures]
    if skipped:
        res.notes.append("not started, so not compiled: " + ", ".join(skipped))

    # metrics.yml: KPIs, governance only.
    mpath = pack / "models" / "semantic" / "metrics.yml"
    metrics = YamlList(mpath, "metrics")
    defined = set(metrics.names())
    roles = {item["name"]: ((item.get("config") or {}).get("meta") or {}).get("role") for item in metrics.seq}
    no_definition = []
    for m in measures:
        if m.get("backlog"):
            continue
        if m["name"] not in defined:
            no_definition.append(m)
            continue
        wanted = governance(m, req.get(m["name"]))

        def change(item, m=m, wanted=wanted):
            item["label"] = m["label"]
            desc = _answer(m.get("definition"))
            if desc is not None:
                _ordered(item, METRIC_ORDER, "description", desc)
            meta = item.setdefault("config", CommentedMap()).setdefault("meta", CommentedMap())
            for k, v in wanted.items():
                _set(meta, META_ORDER, k, v)
            # A changed one-line {meta} would no longer fit a line: write it as a block.
            item["config"].fa.set_block_style()
            meta.fa.set_block_style()

        if metrics.edit(m["name"], change):
            res.notes.append(f"metrics.yml: {m['name']}")
    in_spec = {m["name"] for m in spec["measures"]}
    for name, role in roles.items():
        if role == "kpi" and name not in in_spec:
            res.notes.append(f"metrics.yml: KPI {name} is not in the requirements - left as it is, for review")
    new = metrics.render()
    if new != metrics.text:
        files[mpath] = new

    # backlog.yml: measures with no number, plus requirements with no definition yet.
    bpath = pack / "backlog.yml"
    backlog = YamlList(bpath, "metrics")
    wanted_backlog = {}
    for m in measures:
        if m.get("backlog"):
            wanted_backlog[m["name"]] = (m, m["backlog"])
        elif m in no_definition:
            wanted_backlog[m["name"]] = (m, {"status": "placeholder", "reason": NO_DEFINITION})
            res.notes.append(f"backlog.yml: {m['name']} has no MetricFlow definition yet (placeholder)")
    for name in backlog.names():
        if name not in wanted_backlog:
            backlog.remove(name)
            res.notes.append(f"backlog.yml: removed {name}")
    for name, (m, b) in wanted_backlog.items():
        owner = m.get("owner") or {"unknown": True}
        fields = {"label": m["label"], "status": b["status"], "owner": owner.get("role") or "TBD",
                  "owner_proposed": _unknown_role(owner), "description": _answer(m.get("definition")),
                  "blocked_reason": b["reason"] if b["status"] == "blocked" else None,
                  "placeholder_reason": b["reason"] if b["status"] == "placeholder" else None,
                  "unblock_requires": b.get("unblock_requires") or []}

        def change(item, fields=fields):
            for k, v in fields.items():
                _set(item, BACKLOG_ORDER, k, v)

        if name in backlog.names():
            if backlog.edit(name, change):
                res.notes.append(f"backlog.yml: {name}")
        else:
            item = CommentedMap(name=name)
            change(item)
            backlog.append(item)
            res.notes.append(f"backlog.yml: added {name}")
    new = backlog.render()
    if new != backlog.text:
        files[bpath] = new

    # saved_queries.yml: the cubes.
    spath = pack / "models" / "semantic" / "saved_queries.yml"
    sq = YamlList(spath, "saved_queries")
    cubes = {c["name"]: c for c in spec["cubes"]}
    for name in sq.names():
        if name not in cubes:
            sq.remove(name)
            res.notes.append(f"saved_queries.yml: removed {name}")
    for name, c in cubes.items():
        def change(item, c=c):
            item["description"] = c["description"]
            qp = item.setdefault("query_params", CommentedMap())
            for k, v in (("metrics", c["measures"]), ("group_by", c["group_by"])):
                if list(qp.get(k) or []) != v:
                    qp[k] = _flow([DoubleQuotedScalarString(x) for x in v] if k == "group_by" else v)

        if name in sq.names():
            if sq.edit(name, change):
                res.notes.append(f"saved_queries.yml: {name}")
        else:
            item = CommentedMap(name=name)
            change(item)
            sq.append(item)
            res.notes.append(f"saved_queries.yml: added {name}")
    new = sq.render()
    if new != sq.text:
        files[spath] = new

    # Seeds.
    seeds = pack / "seeds"
    order = {p["code"]: p["display_order"] for p in spec["processes"]}
    procs = sorted(spec["processes"], key=lambda p: p["display_order"])
    seed_text = {
        "process.csv": _csv_text(["process_code", "display_order", "is_active"],
                                 [[p["code"], p["display_order"], int(p["active"])] for p in procs]),
        "process_metric.csv": _csv_text(
            ["process_code", "metric_name", "slot", "display_order"],
            [[d["process"], s["measure"], s["slot"], s.get("display_order", int(s["slot"][1:]))]
             for d in sorted(spec["dashboards"], key=lambda d: order[d["process"]])
             for s in sorted(d["slots"], key=lambda s: int(s["slot"][1:]))]),
        "department_metric.csv": _csv_text(
            ["department_code", "process_code", "metric_name", "influence"],
            [[r["department"], r["process"], r["measure"], r["influence"]] for r in spec.get("department_metrics", [])]),
        "department.csv": _csv_text(
            ["department_code", "department_name", "display_order"],
            [[d["code"], d["name"], d["display_order"]]
             for d in sorted(spec["departments"], key=lambda d: d["display_order"])]),
    }
    for name, text in seed_text.items():
        path = seeds / name
        if not path.exists() or path.read_text() != text:
            files[path] = text
            res.notes.append(f"seeds/{name}")

    # project.yml: name and systems.
    ppath = pack / "project.yml"
    y = YAML()
    y.preserve_quotes = True
    y.indent(mapping=2, sequence=2, offset=0)
    old = ppath.read_text()
    doc = y.load(old)
    doc["name"] = spec["project"]["name"]
    slugs, missing = systems_slugs(spec, registry)
    for name in missing:
        res.notes.append(f"project.yml: {name!r} has no simulator in the architecture yet "
                         "(sources/systems.yml); measures that need it stay in the backlog")
    if list(doc.get("systems") or []) != slugs:
        doc["systems"][:] = slugs
    out = io.StringIO()
    y.dump(doc, out)
    if out.getvalue() != old:
        files[ppath] = out.getvalue()
        res.notes.append("project.yml")
    res.changed = [str(p.relative_to(pack)) for p in files]
    return files, res


def write(files: dict[Path, str]) -> None:
    for path, text in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)


def registry_of(pack: Path) -> dict:
    """The architecture's systems, from the target repo the pack lives in."""
    for parent in pack.resolve().parents:
        path = parent / "sources" / "systems.yml"
        if path.exists():
            return pyyaml.safe_load(path.read_text())
    return {}


def main() -> None:
    p = argparse.ArgumentParser(prog="python -m app.compiler", description=__doc__.split("\n")[0])
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--workspace", help="a workspace slug in the product's database")
    src.add_argument("--spec", type=Path, help="a requirements spec JSON file")
    p.add_argument("--pack", required=True, type=Path, help="the target pack folder (projects/<slug>/)")
    p.add_argument("--check", action="store_true", help="exit 1 if anything would change; write nothing")
    a = p.parse_args()
    from . import spec as spec_mod

    if a.workspace:
        from . import db

        with db.transaction() as conn:
            row = conn.execute("SELECT id FROM clients WHERE slug = ?", (a.workspace,)).fetchone()
            if not row:
                raise SystemExit(f"no workspace {a.workspace!r}")
            spec = spec_mod.build_spec(conn, row["id"])
    else:
        spec = json.loads(a.spec.read_text())
    repo = next((q for q in a.pack.resolve().parents if (q / spec_mod.SCHEMA).exists()), None)
    errors = spec_mod.validate(spec, repo)
    if errors:
        raise SystemExit("spec does not match the schema:\n  " + "\n  ".join(errors[:20]))
    try:
        files, res = compile_spec(spec, a.pack, registry_of(a.pack))
    except CompileError as exc:
        raise SystemExit(f"compile: {exc}") from None
    for n in res.notes:
        print(f"  {n}")
    if a.check:
        print("no changes" if not files else f"{len(files)} file(s) would change: {', '.join(res.changed)}")
        sys.exit(1 if files else 0)
    write(files)
    print("no changes" if not files else f"wrote {', '.join(res.changed)}")


if __name__ == "__main__":
    main()
