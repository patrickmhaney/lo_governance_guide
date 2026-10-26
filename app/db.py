"""SQLite storage: schema, client workspaces and the completeness rule.

Every row belongs to one client workspace. Dashboards and people carry a
client_id; requirements and activity reach their client through their
dashboard. Every API route is scoped by the workspace slug, which is where
login-based access will plug in later.

Each grid cell (dashboard x slot) exists as a requirement row from the moment
the workspace is created, so the API only ever edits cells, never creates them.

A measure is defined once. Another dashboard can show it: its cell is left
empty and its ref_id points at the cell where the measure is defined, so every
dashboard that shows it shows the same definition and the same status.

The planning tables (departments, processes, dimensions, the bus matrix and
dashboard sketches) are in PLANNING_SCHEMA; their rules live in items.py.

Target bindings link the business content to the target platform (contract C8,
see spec.py): a measure's stable metric name and its engineer-set binding
(format, direction, backlog), process, dimension and department codes, which
departments own or contribute to a measure, and the cubes (saved queries).
"""
from __future__ import annotations

import json
import os
import re
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(os.environ.get("REQUIREMENTS_DB", Path(__file__).resolve().parent.parent / "data" / "requirements.db"))

DEFAULT_SLOTS = 4
MAX_SLOTS = 12
DEFAULT_SOURCES: list[str] = []

STATUSES = ("not_started", "in_progress", "complete")
REFRESH = ("monthly", "weekly", "daily", "intraday")

# Every question but the measure name can be answered "I don't know", with an
# optional note of who might know. Keys match answered().
QUESTION_LABELS = {
    "definition": "Definition", "formula": "Calculation", "sources": "Source systems",
    "target": "Benchmark or target", "report": "Trusted report", "breakdowns": "Breakdowns",
    "time": "Date and periods", "refresh": "Refresh", "restatement": "Changes to past numbers",
    "owner": "Definition owner", "sme": "Subject matter expert",
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS clients (
    id          INTEGER PRIMARY KEY,
    slug        TEXT NOT NULL UNIQUE,
    name        TEXT NOT NULL,
    slots       INTEGER NOT NULL DEFAULT 4,
    sources     TEXT NOT NULL DEFAULT '[]',     -- JSON array offered on every measure
    is_demo     INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS dashboards (
    id         INTEGER PRIMARY KEY,
    client_id  INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    code       TEXT NOT NULL,
    name       TEXT NOT NULL DEFAULT '',
    position   INTEGER NOT NULL,
    audience   TEXT NOT NULL DEFAULT '',   -- who uses it
    decisions  TEXT NOT NULL DEFAULT '',   -- what they decide with it
    cadence    TEXT NOT NULL DEFAULT '',   -- when they look at it
    access     TEXT NOT NULL DEFAULT '',   -- who must not see it, or what they must not see
    UNIQUE (client_id, code)
);
CREATE TABLE IF NOT EXISTS people (
    id         INTEGER PRIMARY KEY,
    client_id  INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    name       TEXT NOT NULL,
    role       TEXT NOT NULL DEFAULT '',
    email      TEXT NOT NULL DEFAULT '',
    department_id INTEGER REFERENCES departments(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS requirements (
    id               INTEGER PRIMARY KEY,
    dashboard_id     INTEGER NOT NULL REFERENCES dashboards(id) ON DELETE CASCADE,
    slot             INTEGER NOT NULL,
    name             TEXT NOT NULL DEFAULT '',
    description      TEXT NOT NULL DEFAULT '',
    sources          TEXT NOT NULL DEFAULT '[]',   -- JSON array of system names
    source_other     TEXT NOT NULL DEFAULT '',
    formula          TEXT NOT NULL DEFAULT '',
    report           TEXT NOT NULL DEFAULT '',
    report_none      INTEGER NOT NULL DEFAULT 0,
    breakdowns       TEXT NOT NULL DEFAULT '',
    breakdowns_same  INTEGER NOT NULL DEFAULT 0,  -- "same as the current report"
    target           TEXT NOT NULL DEFAULT '',    -- benchmark, target or threshold
    target_none      INTEGER NOT NULL DEFAULT 0,
    time_basis       TEXT NOT NULL DEFAULT '',    -- driving date, fiscal or calendar, comparisons
    refresh          TEXT NOT NULL DEFAULT '',
    refresh_why      TEXT NOT NULL DEFAULT '',
    restatement      TEXT NOT NULL DEFAULT '',    -- how changes to past numbers are handled
    restatement_none INTEGER NOT NULL DEFAULT 0,  -- "past numbers don't change"
    owner_id         INTEGER REFERENCES people(id) ON DELETE SET NULL,
    owner_note       TEXT NOT NULL DEFAULT '',
    sme_id           INTEGER REFERENCES people(id) ON DELETE SET NULL,
    sme_note         TEXT NOT NULL DEFAULT '',
    unknown          TEXT NOT NULL DEFAULT '{}',  -- JSON: {question key: who might know}
    assignee_id      INTEGER REFERENCES people(id) ON DELETE SET NULL,
    status           TEXT NOT NULL DEFAULT 'not_started',
    followup         INTEGER NOT NULL DEFAULT 0,
    followup_note    TEXT NOT NULL DEFAULT '',
    notes            TEXT NOT NULL DEFAULT '',
    updated_at       TEXT,
    updated_by       INTEGER REFERENCES people(id) ON DELETE SET NULL,
    completed_at     TEXT,
    process_id       INTEGER REFERENCES processes(id) ON DELETE SET NULL,
    dimension_ids    TEXT NOT NULL DEFAULT '[]',  -- JSON array: the dimensions it breaks down by
    ref_id           INTEGER REFERENCES requirements(id) ON DELETE SET NULL,  -- shows the measure defined there
    UNIQUE (dashboard_id, slot)
);
CREATE TABLE IF NOT EXISTS activity (
    id              INTEGER PRIMARY KEY,
    requirement_id  INTEGER NOT NULL REFERENCES requirements(id) ON DELETE CASCADE,
    at              TEXT NOT NULL,
    actor_id        INTEGER REFERENCES people(id) ON DELETE SET NULL,
    action          TEXT NOT NULL,
    detail          TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS activity_by_requirement ON activity (requirement_id, at);
CREATE TABLE IF NOT EXISTS archive (
    id           INTEGER PRIMARY KEY,
    client_id    INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    kind         TEXT NOT NULL,                  -- 'dashboard' or 'column'
    label        TEXT NOT NULL,
    payload      TEXT NOT NULL,                  -- JSON: everything needed to put it back
    archived_at  TEXT NOT NULL,
    archived_by  INTEGER REFERENCES people(id) ON DELETE SET NULL
);
"""

# Processes and dimensions share one shape: a name, JSON answers to their
# question set, an owner, and the same workflow columns as a measure.
_ITEM_COLUMNS = """
    id             INTEGER PRIMARY KEY,
    client_id      INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    position       INTEGER NOT NULL,
    name           TEXT NOT NULL DEFAULT '',
    answers        TEXT NOT NULL DEFAULT '{}',   -- JSON: {question key: answer}, see items.QUESTIONS
    unknown        TEXT NOT NULL DEFAULT '{}',   -- JSON: {question key: who might know}
    owner_id       INTEGER REFERENCES people(id) ON DELETE SET NULL,
    owner_dept_id  INTEGER REFERENCES departments(id) ON DELETE SET NULL,
    owner_note     TEXT NOT NULL DEFAULT '',
    assignee_id    INTEGER REFERENCES people(id) ON DELETE SET NULL,
    status         TEXT NOT NULL DEFAULT 'not_started',
    followup       INTEGER NOT NULL DEFAULT 0,
    followup_note  TEXT NOT NULL DEFAULT '',
    notes          TEXT NOT NULL DEFAULT '',
    updated_at     TEXT,
    updated_by     INTEGER REFERENCES people(id) ON DELETE SET NULL,
    completed_at   TEXT
"""

PLANNING_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS departments (
    id         INTEGER PRIMARY KEY,
    client_id  INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    name       TEXT NOT NULL,
    UNIQUE (client_id, name)
);
CREATE TABLE IF NOT EXISTS processes ({_ITEM_COLUMNS});
CREATE TABLE IF NOT EXISTS dimensions ({_ITEM_COLUMNS});
CREATE TABLE IF NOT EXISTS process_steps (
    id          INTEGER PRIMARY KEY,
    process_id  INTEGER NOT NULL REFERENCES processes(id) ON DELETE CASCADE,
    position    INTEGER NOT NULL,
    name        TEXT NOT NULL DEFAULT '',
    dept_id     INTEGER REFERENCES departments(id) ON DELETE SET NULL,
    person_id   INTEGER REFERENCES people(id) ON DELETE SET NULL,
    system      TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS bus_cells (                -- a row means the box is ticked
    process_id    INTEGER NOT NULL REFERENCES processes(id) ON DELETE CASCADE,
    dimension_id  INTEGER NOT NULL REFERENCES dimensions(id) ON DELETE CASCADE,
    PRIMARY KEY (process_id, dimension_id)
);
CREATE TABLE IF NOT EXISTS tiles (                    -- a dashboard sketch, on a 12-column grid
    id              INTEGER PRIMARY KEY,
    dashboard_id    INTEGER NOT NULL REFERENCES dashboards(id) ON DELETE CASCADE,
    kind            TEXT NOT NULL,
    x               INTEGER NOT NULL,
    y               INTEGER NOT NULL,
    w               INTEGER NOT NULL,
    h               INTEGER NOT NULL,
    title           TEXT NOT NULL DEFAULT '',
    requirement_id  INTEGER REFERENCES requirements(id) ON DELETE SET NULL,
    dimension_id    INTEGER REFERENCES dimensions(id) ON DELETE SET NULL,
    note            TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS item_activity (            -- change history for processes and dimensions
    id         INTEGER PRIMARY KEY,
    client_id  INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    kind       TEXT NOT NULL,
    item_id    INTEGER NOT NULL,
    at         TEXT NOT NULL,
    actor_id   INTEGER REFERENCES people(id) ON DELETE SET NULL,
    action     TEXT NOT NULL,
    detail     TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS item_activity_by_item ON item_activity (kind, item_id, at);
CREATE TABLE IF NOT EXISTS measure_departments (     -- which departments own or contribute to a measure
    id              INTEGER PRIMARY KEY,
    requirement_id  INTEGER NOT NULL REFERENCES requirements(id) ON DELETE CASCADE,
    department_id   INTEGER NOT NULL REFERENCES departments(id) ON DELETE CASCADE,
    process_id      INTEGER REFERENCES processes(id) ON DELETE SET NULL,
    influence       TEXT NOT NULL CHECK (influence IN ('owns', 'contributes')),
    position        INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS cubes (                    -- target binding: a saved query, a fixed set of measures at a fixed grain
    id           INTEGER PRIMARY KEY,
    client_id    INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    position     INTEGER NOT NULL,
    name         TEXT NOT NULL,
    description  TEXT NOT NULL DEFAULT '',
    measures     TEXT NOT NULL DEFAULT '[]',          -- JSON: metric names
    group_by     TEXT NOT NULL DEFAULT '[]',          -- JSON: MetricFlow group-by references
    UNIQUE (client_id, name)
);
"""

# Requirement columns added after the first release. Databases created before
# then get them on start; see _add_missing_columns.
ADDED_COLUMNS = {
    "target": "TEXT NOT NULL DEFAULT ''",
    "target_none": "INTEGER NOT NULL DEFAULT 0",
    "time_basis": "TEXT NOT NULL DEFAULT ''",
    "restatement": "TEXT NOT NULL DEFAULT ''",
    "restatement_none": "INTEGER NOT NULL DEFAULT 0",
    "sme_id": "INTEGER REFERENCES people(id) ON DELETE SET NULL",
    "sme_note": "TEXT NOT NULL DEFAULT ''",
    "unknown": "TEXT NOT NULL DEFAULT '{}'",
    "process_id": "INTEGER REFERENCES processes(id) ON DELETE SET NULL",
    "dimension_ids": "TEXT NOT NULL DEFAULT '[]'",
    "ref_id": "INTEGER REFERENCES requirements(id) ON DELETE SET NULL",
}

# A dashboard's "data use" answers, asked on its sketch page.
DASHBOARD_USE = ("audience", "decisions", "cadence", "access")

# Columns other tables gained with the planning pages.
ADDED_TABLE_COLUMNS = {
    "people": {"department_id": "INTEGER REFERENCES departments(id) ON DELETE SET NULL"},
    "dashboards": {k: "TEXT NOT NULL DEFAULT ''" for k in DASHBOARD_USE},
    # Target bindings (contract C8). None of them is a question, so adding them
    # never reopens a completed measure.
    "clients": {"target_slug": "TEXT NOT NULL DEFAULT ''"},
    "requirements": {"metric_name": "TEXT NOT NULL DEFAULT ''", "binding": "TEXT NOT NULL DEFAULT '{}'"},
    "processes": {"code": "TEXT NOT NULL DEFAULT ''", "active": "INTEGER NOT NULL DEFAULT 1"},
    "dimensions": {"code": "TEXT NOT NULL DEFAULT ''"},
    "departments": {"code": "TEXT NOT NULL DEFAULT ''", "position": "INTEGER"},
}

# Per-question "not sure" answers from before "I don't know" covered every
# question. _fold_legacy_unknowns turns them into entries in `unknown`.
LEGACY_UNKNOWNS = {"formula_unknown": "formula", "source_unsure": "sources"}

FLAGS = ("report_none", "breakdowns_same", "target_none", "restatement_none", "followup")


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def transaction():
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    _retire_single_client_db()
    with transaction() as conn:
        conn.executescript(SCHEMA)
        conn.executescript(PLANNING_SCHEMA)
        for table, columns in ADDED_TABLE_COLUMNS.items():
            have = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
            for col, ddl in columns.items():
                if col not in have:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")
        _add_missing_columns(conn)
        from .demo import seed_demo, seed_demo_bindings, seed_demo_department, seed_demo_planning
        demo = conn.execute("SELECT id FROM clients WHERE slug = 'demo'").fetchone()
        if not demo:
            seed_demo(conn)
        else:
            # Each does nothing once the sample has that content.
            seed_demo_planning(conn, demo["id"])
            seed_demo_department(conn, demo["id"])
            seed_demo_bindings(conn, demo["id"])


def _retire_single_client_db() -> None:
    """The first version had no clients table. Set such a file aside, untouched."""
    if not DB_PATH.exists():
        return
    conn = sqlite3.connect(DB_PATH)
    try:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    finally:
        conn.close()
    if "dashboards" in tables and "clients" not in tables:
        DB_PATH.rename(DB_PATH.with_suffix(".v1-backup.db"))


def _add_missing_columns(conn: sqlite3.Connection) -> None:
    """Bring an older requirements table up to the current question set.

    New questions start unanswered, so a measure marked complete under the old
    set is reopened: "complete" always means every question is answered.
    """
    have = {r["name"] for r in conn.execute("PRAGMA table_info(requirements)")}
    missing = [c for c in ADDED_COLUMNS if c not in have]
    for col in missing:
        conn.execute(f"ALTER TABLE requirements ADD COLUMN {col} {ADDED_COLUMNS[col]}")
    _fold_legacy_unknowns(conn, have)
    if not missing:
        return
    for row in conn.execute("SELECT * FROM requirements WHERE status = 'complete'").fetchall():
        if not is_complete_ready(requirement_dict(row)):
            conn.execute("UPDATE requirements SET status = 'in_progress', completed_at = NULL WHERE id = ?", (row["id"],))
            conn.execute(
                "INSERT INTO activity (requirement_id, at, action, detail) VALUES (?, ?, 'reopened', ?)",
                (row["id"], now(), "Reopened because new questions were added"),
            )


def fold_legacy(row: dict) -> dict:
    """Move old "not sure" flags and the "unsure" refresh choice into `unknown`."""
    unknown = json.loads(row.get("unknown") or "{}")
    for col, key in LEGACY_UNKNOWNS.items():
        if row.pop(col, 0):
            unknown.setdefault(key, "")
    if row.get("refresh") == "unsure":
        row["refresh"] = ""
        unknown.setdefault("refresh", "")
    row["unknown"] = json.dumps(unknown)
    return row


def _fold_legacy_unknowns(conn: sqlite3.Connection, have: set[str]) -> None:
    legacy = [c for c in LEGACY_UNKNOWNS if c in have]
    flagged = " OR ".join([f"{c} = 1" for c in legacy] + ["refresh = 'unsure'"])
    for row in conn.execute(f"SELECT * FROM requirements WHERE {flagged}").fetchall():
        r = fold_legacy(dict(row))
        conn.execute("UPDATE requirements SET refresh = ?, unknown = ? WHERE id = ?", (r["refresh"], r["unknown"], r["id"]))
    for col in legacy:
        conn.execute(f"ALTER TABLE requirements DROP COLUMN {col}")


def slugify(name: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40] or "client"
    # The random suffix keeps a workspace link from being guessable until
    # login-based access exists. It is not a substitute for it.
    return f"{base}-{secrets.token_hex(3)}"


def create_client(conn: sqlite3.Connection, name: str, dashboards: list[tuple[str, str]],
                  slots: int = DEFAULT_SLOTS, sources: list[str] | None = None,
                  slug: str | None = None, is_demo: bool = False) -> int:
    cur = conn.execute(
        "INSERT INTO clients (slug, name, slots, sources, is_demo, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (slug or slugify(name), name, slots, json.dumps(sources or DEFAULT_SOURCES), int(is_demo), now()),
    )
    client_id = cur.lastrowid
    for code, dname in dashboards:
        add_dashboard(conn, client_id, code, dname, slots)
    return client_id


def add_dashboard(conn: sqlite3.Connection, client_id: int, code: str, name: str, slots: int) -> int:
    """Append a dashboard row with its empty measure cells."""
    pos = conn.execute("SELECT COALESCE(MAX(position) + 1, 0) FROM dashboards WHERE client_id = ?",
                       (client_id,)).fetchone()[0]
    d = conn.execute(
        "INSERT INTO dashboards (client_id, code, name, position) VALUES (?, ?, ?, ?)",
        (client_id, code, name, pos),
    ).lastrowid
    for slot in range(1, slots + 1):
        conn.execute("INSERT INTO requirements (dashboard_id, slot) VALUES (?, ?)", (d, slot))
    return d


def add_slot(conn: sqlite3.Connection, client_id: int) -> None:
    """Add a measure column: one more slot, with an empty cell on every dashboard."""
    conn.execute("UPDATE clients SET slots = slots + 1 WHERE id = ?", (client_id,))
    conn.execute(
        """INSERT INTO requirements (dashboard_id, slot)
           SELECT d.id, c.slots FROM dashboards d JOIN clients c ON c.id = d.client_id WHERE c.id = ?""",
        (client_id,),
    )


def next_code(conn: sqlite3.Connection, client_id: int) -> str:
    """The first of D1, D2, ... not already used by a dashboard in this workspace."""
    used = {r[0] for r in conn.execute("SELECT code FROM dashboards WHERE client_id = ?", (client_id,))}
    return next(f"D{n}" for n in range(1, len(used) + 2) if f"D{n}" not in used)


# ---------------------------------------------------------------------------
# Archive: deleting a dashboard row or a measure column moves its cells, with
# their answers and history, into one archive row that can be restored.
# ---------------------------------------------------------------------------

def _cells_payload(conn: sqlite3.Connection, rows, dashboard_id: int | None = None) -> list[dict]:
    """Each cell with its history, and what points at it from outside what's being
    archived: cells on other dashboards that show it, and sketch tiles (other than
    dashboard_id's own, which are archived with it)."""
    out = [dict(r) for r in rows]
    ids = {d["id"] for d in out}
    for d in out:
        d["activity"] = [dict(a) for a in conn.execute(
            "SELECT at, actor_id, action, detail FROM activity WHERE requirement_id = ? ORDER BY id", (d["id"],))]
        d["refs"] = [r[0] for r in conn.execute("SELECT id FROM requirements WHERE ref_id = ?", (d["id"],)) if r[0] not in ids]
        d["tiles"] = [t[0] for t in conn.execute(
            "SELECT id FROM tiles WHERE requirement_id = ? AND dashboard_id IS NOT ?", (d["id"], dashboard_id))]
    return out


def _shift_slots(conn: sqlite3.Connection, client_id: int, from_slot: int, delta: int) -> None:
    """Move every cell at from_slot or later by delta columns (two steps, so UNIQUE never trips)."""
    mine = "SELECT r.id FROM requirements r JOIN dashboards d ON d.id = r.dashboard_id WHERE d.client_id = ?"
    conn.execute(f"UPDATE requirements SET slot = -(slot + ?) WHERE slot >= ? AND id IN ({mine})",
                 (delta, from_slot, client_id))
    conn.execute(f"UPDATE requirements SET slot = -slot WHERE slot < 0 AND id IN ({mine})", (client_id,))


def _restore_cell(conn: sqlite3.Connection, client_id: int, dashboard_id: int, slot: int, cell: dict | None,
                  actor_id: int | None) -> int:
    if cell is None:
        return conn.execute("INSERT INTO requirements (dashboard_id, slot) VALUES (?, ?)", (dashboard_id, slot)).lastrowid
    columns = {r["name"] for r in conn.execute("PRAGMA table_info(requirements)")} - {"id"}
    people = {r[0] for r in conn.execute("SELECT id FROM people WHERE client_id = ?", (client_id,))}
    row = {k: v for k, v in fold_legacy(dict(cell)).items() if k in columns}
    for k in ("assignee_id", "owner_id", "sme_id", "updated_by"):
        if row.get(k) not in people:
            row[k] = None  # that person was removed while this was archived
    # The same for a process or dimension deleted while this was archived.
    if not conn.execute("SELECT 1 FROM processes WHERE id = ? AND client_id = ?", (row.get("process_id"), client_id)).fetchone():
        row["process_id"] = None
    dims = {r[0] for r in conn.execute("SELECT id FROM dimensions WHERE client_id = ?", (client_id,))}
    row["dimension_ids"] = json.dumps([d for d in json.loads(row.get("dimension_ids") or "[]") if d in dims])
    row.update(dashboard_id=dashboard_id, slot=slot, ref_id=None)  # set by _restore_refs once every cell is back
    rid = conn.execute(f"INSERT INTO requirements ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})",
                       tuple(row.values())).lastrowid
    for a in cell.get("activity", []):
        conn.execute("INSERT INTO activity (requirement_id, at, actor_id, action, detail) VALUES (?, ?, ?, ?, ?)",
                     (rid, a["at"], a["actor_id"] if a["actor_id"] in people else None, a["action"], a["detail"]))
    if row.get("name") or cell.get("activity"):
        conn.execute("INSERT INTO activity (requirement_id, at, actor_id, action, detail) VALUES (?, ?, ?, 'restored', ?)",
                     (rid, now(), actor_id, "Restored from the archive"))
    # Cells on other dashboards that showed this measure, and sketch tiles that
    # did, point at it again if nothing else has taken their place.
    mine = "SELECT id FROM dashboards WHERE client_id = ?"
    for ref in cell.get("refs", []):
        conn.execute(f"UPDATE requirements SET ref_id = ? WHERE id = ? AND ref_id IS NULL AND name = '' AND dashboard_id IN ({mine})",
                     (rid, ref, client_id))
    for tid in cell.get("tiles", []):
        conn.execute(f"UPDATE tiles SET requirement_id = ? WHERE id = ? AND requirement_id IS NULL AND dashboard_id IN ({mine})",
                     (rid, tid, client_id))
    return rid


def ref_problem(conn: sqlite3.Connection, client_id: int, dashboard_id: int, target_id: int,
                cell_id: int | None = None) -> str | None:
    """Why a cell on dashboard_id can't show the measure defined at target_id, or None if it can."""
    t = conn.execute(
        """SELECT r.name, r.dashboard_id FROM requirements r JOIN dashboards d ON d.id = r.dashboard_id
           WHERE r.id = ? AND d.client_id = ? AND r.ref_id IS NULL""", (target_id, client_id)).fetchone()
    if not t or not t["name"].strip():
        return "ref_id does not match a defined measure in this workspace"
    if t["dashboard_id"] == dashboard_id or conn.execute(
            "SELECT 1 FROM requirements WHERE dashboard_id = ? AND ref_id = ? AND id IS NOT ?",
            (dashboard_id, target_id, cell_id)).fetchone():
        return "That measure is already on this dashboard"
    return None


def _restore_refs(conn: sqlite3.Connection, client_id: int, restored: list[tuple[dict, int]]) -> None:
    """Point restored cells back at the measures they showed. A measure restored
    in the same go has a new id; one deleted since leaves the cell empty."""
    new_ids = {cell["id"]: rid for cell, rid in restored if cell and "id" in cell}
    for cell, rid in restored:
        if cell and cell.get("ref_id") is not None:
            target = new_ids.get(cell["ref_id"], cell["ref_id"])
            dash = conn.execute("SELECT dashboard_id FROM requirements WHERE id = ?", (rid,)).fetchone()[0]
            if not ref_problem(conn, client_id, dash, target, rid):
                conn.execute("UPDATE requirements SET ref_id = ? WHERE id = ?", (target, rid))


def _drop_stray_tiles(conn: sqlite3.Connection, client_id: int) -> None:
    """A tile only shows a measure that's on its own dashboard, defined there or shown there."""
    conn.execute(
        """UPDATE tiles SET requirement_id = NULL
           WHERE requirement_id IS NOT NULL AND dashboard_id IN (SELECT id FROM dashboards WHERE client_id = ?)
             AND NOT EXISTS (SELECT 1 FROM requirements r WHERE r.dashboard_id = tiles.dashboard_id
                             AND ((r.id = tiles.requirement_id AND r.ref_id IS NULL) OR r.ref_id = tiles.requirement_id))""",
        (client_id,))


def _archive(conn: sqlite3.Connection, client_id: int, kind: str, label: str, payload: dict,
             actor_id: int | None) -> int:
    return conn.execute(
        "INSERT INTO archive (client_id, kind, label, payload, archived_at, archived_by) VALUES (?, ?, ?, ?, ?, ?)",
        (client_id, kind, label, json.dumps(payload), now(), actor_id),
    ).lastrowid


def archive_dashboard(conn: sqlite3.Connection, client_id: int, dashboard_id: int, actor_id: int | None) -> int:
    d = conn.execute("SELECT * FROM dashboards WHERE id = ? AND client_id = ?", (dashboard_id, client_id)).fetchone()
    cells = _cells_payload(conn, conn.execute(
        "SELECT * FROM requirements WHERE dashboard_id = ? ORDER BY slot", (dashboard_id,)), dashboard_id)
    label = d["code"] + (f" · {d['name']}" if d["name"] else "")
    # Sketch tiles go with the row; each remembers its measure by the slot that
    # defines or shows it.
    tiles = [dict(t) for t in conn.execute(
        """SELECT t.kind, t.x, t.y, t.w, t.h, t.title, t.dimension_id, t.note,
                  (SELECT r.slot FROM requirements r WHERE r.dashboard_id = t.dashboard_id
                     AND (r.id = t.requirement_id OR r.ref_id = t.requirement_id)) AS slot
           FROM tiles t WHERE t.dashboard_id = ? ORDER BY t.id""", (dashboard_id,))]
    aid = _archive(conn, client_id, "dashboard", label,
                   {"code": d["code"], "name": d["name"], "position": d["position"], "cells": cells, "tiles": tiles,
                    **{k: d[k] for k in DASHBOARD_USE}}, actor_id)
    conn.execute("DELETE FROM dashboards WHERE id = ?", (dashboard_id,))  # cascades to cells and history
    return aid


def archive_column(conn: sqlite3.Connection, client_id: int, slot: int, actor_id: int | None) -> int:
    cells = _cells_payload(conn, conn.execute(
        """SELECT r.*, d.code AS dashboard_code FROM requirements r JOIN dashboards d ON d.id = r.dashboard_id
           WHERE d.client_id = ? AND r.slot = ? ORDER BY d.position""", (client_id, slot)))
    aid = _archive(conn, client_id, "column", f"Measure {slot}", {"slot": slot, "cells": cells}, actor_id)
    conn.execute("DELETE FROM requirements WHERE slot = ? AND dashboard_id IN (SELECT id FROM dashboards WHERE client_id = ?)",
                 (slot, client_id))
    _shift_slots(conn, client_id, slot + 1, -1)
    conn.execute("UPDATE clients SET slots = slots - 1 WHERE id = ?", (client_id,))
    return aid


def restore(conn: sqlite3.Connection, client_id: int, archive_id: int, actor_id: int | None) -> str | None:
    """Put an archived dashboard or column back where it was. Returns a note when it couldn't be exact."""
    a = conn.execute("SELECT * FROM archive WHERE id = ? AND client_id = ?", (archive_id, client_id)).fetchone()
    payload, note = json.loads(a["payload"]), None
    slots = conn.execute("SELECT slots FROM clients WHERE id = ?", (client_id,)).fetchone()[0]

    if a["kind"] in ("process", "dimension"):
        from .items import restore_item
        restore_item(conn, client_id, a["kind"], payload, actor_id)
    elif a["kind"] == "dashboard":
        code = payload["code"]
        if conn.execute("SELECT 1 FROM dashboards WHERE client_id = ? AND code = ?", (client_id, code)).fetchone():
            code = next_code(conn, client_id)
            note = f"{payload['code']} is in use now, so it came back as {code}"
        needed = max([c["slot"] for c in payload["cells"]] + [slots])
        for _ in range(needed - slots):
            add_slot(conn, client_id)
        conn.execute("UPDATE dashboards SET position = position + 1 WHERE client_id = ? AND position >= ?",
                     (client_id, payload["position"]))
        use = {k: payload.get(k, "") for k in DASHBOARD_USE}
        did = conn.execute(
            f"INSERT INTO dashboards (client_id, code, name, position, {', '.join(use)}) VALUES (?, ?, ?, ?{', ?' * len(use)})",
            (client_id, code, payload["name"], payload["position"], *use.values())).lastrowid
        by_slot = {c["slot"]: c for c in payload["cells"]}
        rids = {slot: _restore_cell(conn, client_id, did, slot, by_slot.get(slot), actor_id) for slot in range(1, needed + 1)}
        _restore_refs(conn, client_id, [(by_slot.get(slot), rid) for slot, rid in rids.items()])
        # A tile on a cell that shows another dashboard's measure points at that
        # measure, or at nothing once the measure is gone.
        shown = {}
        for slot, rid in rids.items():
            ref = conn.execute("SELECT ref_id FROM requirements WHERE id = ?", (rid,)).fetchone()[0]
            shown[slot] = ref or (None if (by_slot.get(slot) or {}).get("ref_id") else rid)
        dims = {r[0] for r in conn.execute("SELECT id FROM dimensions WHERE client_id = ?", (client_id,))}
        for t in payload.get("tiles", []):
            conn.execute(
                """INSERT INTO tiles (dashboard_id, kind, x, y, w, h, title, requirement_id, dimension_id, note)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (did, t["kind"], t["x"], t["y"], t["w"], t["h"], t["title"], shown.get(t["slot"]),
                 t["dimension_id"] if t["dimension_id"] in dims else None, t["note"]))
    else:
        slot = min(payload["slot"], slots + 1)
        _shift_slots(conn, client_id, slot, 1)
        conn.execute("UPDATE clients SET slots = slots + 1 WHERE id = ?", (client_id,))
        by_id = {c["dashboard_id"]: c for c in payload["cells"]}
        by_code = {c["dashboard_code"]: c for c in payload["cells"]}
        lost, restored = set(by_code), []
        for d in conn.execute("SELECT id, code FROM dashboards WHERE client_id = ?", (client_id,)).fetchall():
            # Same dashboard id first (its code may have been edited since), then the same code.
            cell = by_id.get(d["id"])
            if cell is None or cell["dashboard_code"] not in lost:
                cell = by_code.get(d["code"])
            if cell is not None and cell["dashboard_code"] not in lost:
                cell = None  # already put back on another dashboard
            if cell:
                lost.discard(cell["dashboard_code"])
            restored.append((cell, _restore_cell(conn, client_id, d["id"], slot, cell, actor_id)))
        _restore_refs(conn, client_id, restored)
        lost_named = sorted(c for c in lost if by_code[c]["name"])
        if lost_named:
            note = "Measures for dashboards that no longer exist weren't restored: " + ", ".join(lost_named)
    _drop_stray_tiles(conn, client_id)
    conn.execute("DELETE FROM archive WHERE id = ?", (archive_id,))
    return note


def client_progress(conn: sqlite3.Connection, client_id: int) -> dict:
    row = conn.execute(
        """SELECT COUNT(*) AS cells,
                  SUM(r.name <> '') AS defined,
                  SUM(r.name <> '' AND r.status = 'complete') AS complete,
                  SUM(r.name <> '' AND r.status = 'in_progress') AS in_progress,
                  SUM(r.name <> '' AND r.followup = 1) AS followup,
                  SUM(r.name <> '' AND r.unknown <> '{}') AS unknown
           FROM requirements r JOIN dashboards d ON d.id = r.dashboard_id
           WHERE d.client_id = ?""",
        (client_id,),
    ).fetchone()
    return {k: row[k] or 0 for k in row.keys()}


def requirement_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["sources"] = json.loads(d["sources"] or "[]")
    for flag in FLAGS:
        d[flag] = bool(d[flag])
    d["unknown"] = json.loads(d.get("unknown") or "{}")
    d["dimension_ids"] = json.loads(d.get("dimension_ids") or "[]")
    d["binding"] = json.loads(d.get("binding") or "{}")
    d["answered"] = answered(d)
    return d


def answered(r: dict) -> dict[str, bool]:
    """Which of the phase 1 questions have an answer, in question order.

    "I don't know" counts as an answer: the point of the template is to find
    the gaps, not to block on them. Those are followed up by hand.
    """
    def has(key: str) -> bool:
        return bool((r.get(key) or "").strip())

    unknown = r.get("unknown") or {}
    if isinstance(unknown, str):
        unknown = json.loads(unknown)
    dims = r.get("dimension_ids") or []
    if isinstance(dims, str):
        dims = json.loads(dims)
    answers = {
        "name": has("name"),
        "definition": has("description"),
        "formula": has("formula"),
        "sources": bool(r.get("sources")) or has("source_other"),
        "target": has("target") or bool(r.get("target_none")),
        "report": has("report") or bool(r.get("report_none")),
        "breakdowns": has("breakdowns") or bool(r.get("breakdowns_same")) or bool(dims),
        "time": has("time_basis"),
        "refresh": has("refresh"),
        "restatement": has("restatement") or bool(r.get("restatement_none")),
        "owner": r.get("owner_id") is not None or has("owner_note"),
        "sme": r.get("sme_id") is not None or has("sme_note"),
    }
    return {k: ok or k in unknown for k, ok in answers.items()}


def is_complete_ready(r: dict) -> bool:
    return all(answered(r).values())
