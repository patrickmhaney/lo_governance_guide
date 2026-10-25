"""Business processes and dimensions: the rows and columns of the bus matrix.

Both are "items" with the same workflow as a measure: an assignee, a follow-up
flag, "I don't know" on any question but the name, and "complete" only when
every question is answered. Answers live in one JSON column so a question set
can grow without a schema change. Owners and people are real columns, so
removing a person or a department clears them.

A process also has ordered steps, each done by a department or a person in
one system. Those draw its swimlane.
"""
from __future__ import annotations

import json
import sqlite3

from .db import now

KINDS = {"process": "processes", "dimension": "dimensions"}

# Question keys and their labels in exports and the change history, in order.
# The name is asked first on both and can't be "I don't know".
QUESTIONS = {
    "process": {
        "purpose": "Purpose", "boundaries": "Start and end", "steps": "Steps", "event": "What it records",
        "systems": "Systems", "volume": "Volume", "decisions": "Decisions it supports",
        "pain_points": "Pain points", "owner": "Owner", "priority": "Priority",
    },
    "dimension": {
        "definition": "Definition", "identifier": "How one is identified", "attributes": "Described by",
        "hierarchy": "Roll-ups", "systems": "Master system", "history": "When it changes",
        "quality": "Known data problems", "owner": "Owner",
    },
}
# Questions a single box can answer, e.g. "No known problems". Stored as "<key>_none".
NONE_BOXES = {"process": ("pain_points",), "dimension": ("hierarchy", "quality")}
CHOICES = {
    "priority": {"now": "Now", "next": "Next", "later": "Later"},
    "history": {"as_was": "As it was at the time", "as_is": "As it is now", "both": "Both, chosen per report"},
}
# Free text alongside the systems chips or a choice. Never needed for an answer.
EXTRA = {"process": ("systems_other",), "dimension": ("systems_other", "history_note")}

TEXT_LIMIT = 4000


def answer_keys(kind: str) -> set[str]:
    return ((set(QUESTIONS[kind]) - {"steps", "owner"})
            | {f"{k}_none" for k in NONE_BOXES[kind]} | set(EXTRA[kind]))


def clean_answers(kind: str, answers: dict) -> dict:
    """Check a partial answers patch. Raises ValueError with a readable message."""
    bad = set(answers) - answer_keys(kind)
    if bad:
        raise ValueError("unknown answer: " + ", ".join(sorted(bad)))
    out = {}
    for k, v in answers.items():
        if k.endswith("_none"):
            if not isinstance(v, bool):
                raise ValueError(f"{k} must be true or false")
        elif k == "systems":
            if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
                raise ValueError("systems must be a list of names")
            v = list(dict.fromkeys(x.strip() for x in v if x.strip()))
        elif not isinstance(v, str) or len(v) > TEXT_LIMIT:
            raise ValueError(f"{k} must be text of at most {TEXT_LIMIT} characters")
        elif k in CHOICES and v not in ("", *CHOICES[k]):
            raise ValueError(f"{k} must be one of: " + ", ".join(CHOICES[k]))
        out[k] = v
    return out


def answered(kind: str, item: dict) -> dict[str, bool]:
    """Which questions have an answer, in order. "I don't know" counts, as on a measure."""
    a, unknown = item["answers"], item["unknown"]

    def has(k: str) -> bool:
        v = a.get(k)
        return bool(v.strip()) if isinstance(v, str) else bool(v)

    out = {"name": bool(item["name"].strip())}
    for k in QUESTIONS[kind]:
        if k == "owner":
            ok = item["owner_id"] is not None or item["owner_dept_id"] is not None or bool(item["owner_note"].strip())
        elif k == "steps":
            ok = any(s["name"].strip() for s in item.get("steps", []))
        elif k == "systems":
            ok = has("systems") or has("systems_other")
        else:
            ok = has(k) or bool(a.get(f"{k}_none"))
        out[k] = ok or k in unknown
    return out


def is_complete_ready(kind: str, item: dict) -> bool:
    return all(answered(kind, item).values())


def steps_of(conn: sqlite3.Connection, process_id: int) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT id, position, name, dept_id, person_id, system FROM process_steps WHERE process_id = ? ORDER BY position",
        (process_id,))]


def item_dict(conn: sqlite3.Connection, kind: str, row: sqlite3.Row) -> dict:
    d = dict(row)
    d["kind"] = kind
    d["answers"] = json.loads(d["answers"] or "{}")
    d["unknown"] = json.loads(d["unknown"] or "{}")
    d["followup"] = bool(d["followup"])
    if kind == "process":
        d["steps"] = steps_of(conn, d["id"])
    d["answered"] = answered(kind, d)
    return d


def get_item(conn: sqlite3.Connection, client_id: int, kind: str, iid: int) -> dict | None:
    row = conn.execute(f"SELECT * FROM {KINDS[kind]} WHERE id = ? AND client_id = ?", (iid, client_id)).fetchone()
    return item_dict(conn, kind, row) if row else None


def list_items(conn: sqlite3.Connection, client_id: int, kind: str) -> list[dict]:
    return [item_dict(conn, kind, r) for r in conn.execute(
        f"SELECT * FROM {KINDS[kind]} WHERE client_id = ? ORDER BY position, id", (client_id,))]


def create_item(conn: sqlite3.Connection, client_id: int, kind: str, name: str = "", answers: dict | None = None) -> int:
    table = KINDS[kind]
    pos = conn.execute(f"SELECT COALESCE(MAX(position) + 1, 0) FROM {table} WHERE client_id = ?", (client_id,)).fetchone()[0]
    return conn.execute(f"INSERT INTO {table} (client_id, position, name, answers) VALUES (?, ?, ?, ?)",
                        (client_id, pos, name, json.dumps(answers or {}))).lastrowid


def log(conn: sqlite3.Connection, client_id: int, kind: str, iid: int, actor_id: int | None,
        action: str, detail: str = "") -> None:
    conn.execute(
        "INSERT INTO item_activity (client_id, kind, item_id, at, actor_id, action, detail) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (client_id, kind, iid, now(), actor_id, action, detail))


def move(conn: sqlite3.Connection, table: str, scope_col: str, scope_id: int, row_id: int, to: int) -> None:
    """Put one row at index `to` in its list and renumber the rest."""
    ids = [r[0] for r in conn.execute(f"SELECT id FROM {table} WHERE {scope_col} = ? ORDER BY position, id", (scope_id,))]
    ids.remove(row_id)
    ids.insert(max(0, min(to, len(ids))), row_id)
    for pos, i in enumerate(ids):
        conn.execute(f"UPDATE {table} SET position = ? WHERE id = ?", (pos, i))


# ---------------------------------------------------------------------------
# Archive: a deleted process or dimension is stored whole, with what pointed at
# it (bus matrix ticks, measures, sketch tiles), and comes back where it was.
# ---------------------------------------------------------------------------

def archive_item(conn: sqlite3.Connection, client_id: int, kind: str, iid: int, actor_id: int | None) -> int:
    table = KINDS[kind]
    row = dict(conn.execute(f"SELECT * FROM {table} WHERE id = ?", (iid,)).fetchone())
    payload = {
        "row": row,
        "activity": [dict(a) for a in conn.execute(
            "SELECT at, actor_id, action, detail FROM item_activity WHERE kind = ? AND item_id = ? ORDER BY id", (kind, iid))],
    }
    mine = "SELECT r.id FROM requirements r JOIN dashboards d ON d.id = r.dashboard_id WHERE d.client_id = ?"
    if kind == "process":
        payload["steps"] = steps_of(conn, iid)
        payload["bus"] = [r[0] for r in conn.execute("SELECT dimension_id FROM bus_cells WHERE process_id = ?", (iid,))]
        payload["measures"] = [r[0] for r in conn.execute(
            f"SELECT id FROM requirements WHERE process_id = ? AND id IN ({mine})", (iid, client_id))]
    else:
        payload["bus"] = [r[0] for r in conn.execute("SELECT process_id FROM bus_cells WHERE dimension_id = ?", (iid,))]
        payload["tiles"] = [r[0] for r in conn.execute("SELECT id FROM tiles WHERE dimension_id = ?", (iid,))]
        payload["measures"] = []
        for r in conn.execute(f"SELECT id, dimension_ids FROM requirements WHERE id IN ({mine})", (client_id,)).fetchall():
            dims = json.loads(r["dimension_ids"] or "[]")
            if iid in dims:
                payload["measures"].append(r["id"])
                conn.execute("UPDATE requirements SET dimension_ids = ? WHERE id = ?",
                             (json.dumps([d for d in dims if d != iid]), r["id"]))
    label = row["name"] or ("Unnamed process" if kind == "process" else "Unnamed dimension")
    aid = conn.execute(
        "INSERT INTO archive (client_id, kind, label, payload, archived_at, archived_by) VALUES (?, ?, ?, ?, ?, ?)",
        (client_id, kind, label, json.dumps(payload), now(), actor_id)).lastrowid
    conn.execute("DELETE FROM item_activity WHERE kind = ? AND item_id = ?", (kind, iid))
    conn.execute(f"DELETE FROM {table} WHERE id = ?", (iid,))  # cascades to steps and bus ticks
    return aid


def restore_item(conn: sqlite3.Connection, client_id: int, kind: str, payload: dict, actor_id: int | None) -> int:
    table = KINDS[kind]
    people = {r[0] for r in conn.execute("SELECT id FROM people WHERE client_id = ?", (client_id,))}
    depts = {r[0] for r in conn.execute("SELECT id FROM departments WHERE client_id = ?", (client_id,))}
    columns = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")} - {"id"}
    row = {k: v for k, v in payload["row"].items() if k in columns}
    for k in ("owner_id", "assignee_id", "updated_by"):
        if row.get(k) not in people:
            row[k] = None  # that person was removed while this was archived
    if row.get("owner_dept_id") not in depts:
        row["owner_dept_id"] = None
    row["client_id"] = client_id
    conn.execute(f"UPDATE {table} SET position = position + 1 WHERE client_id = ? AND position >= ?",
                 (client_id, row["position"]))
    iid = conn.execute(f"INSERT INTO {table} ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})",
                       tuple(row.values())).lastrowid

    for a in payload.get("activity", []):
        conn.execute(
            "INSERT INTO item_activity (client_id, kind, item_id, at, actor_id, action, detail) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (client_id, kind, iid, a["at"], a["actor_id"] if a["actor_id"] in people else None, a["action"], a["detail"]))
    log(conn, client_id, kind, iid, actor_id, "restored", "Restored from the archive")

    mine = "SELECT r.id FROM requirements r JOIN dashboards d ON d.id = r.dashboard_id WHERE d.client_id = ?"
    if kind == "process":
        for s in payload.get("steps", []):
            conn.execute(
                "INSERT INTO process_steps (process_id, position, name, dept_id, person_id, system) VALUES (?, ?, ?, ?, ?, ?)",
                (iid, s["position"], s["name"], s["dept_id"] if s["dept_id"] in depts else None,
                 s["person_id"] if s["person_id"] in people else None, s["system"]))
        for did in payload.get("bus", []):
            if conn.execute("SELECT 1 FROM dimensions WHERE id = ? AND client_id = ?", (did, client_id)).fetchone():
                conn.execute("INSERT OR IGNORE INTO bus_cells (process_id, dimension_id) VALUES (?, ?)", (iid, did))
        for rid in payload.get("measures", []):
            # Only measures that still exist and haven't been linked to another process since.
            conn.execute(f"UPDATE requirements SET process_id = ? WHERE id = ? AND process_id IS NULL AND id IN ({mine})",
                         (iid, rid, client_id))
    else:
        for pid in payload.get("bus", []):
            if conn.execute("SELECT 1 FROM processes WHERE id = ? AND client_id = ?", (pid, client_id)).fetchone():
                conn.execute("INSERT OR IGNORE INTO bus_cells (process_id, dimension_id) VALUES (?, ?)", (pid, iid))
        for rid in payload.get("measures", []):
            r = conn.execute(f"SELECT dimension_ids FROM requirements WHERE id = ? AND id IN ({mine})", (rid, client_id)).fetchone()
            if r:
                conn.execute("UPDATE requirements SET dimension_ids = ? WHERE id = ?",
                             (json.dumps(json.loads(r["dimension_ids"] or "[]") + [iid]), rid))
        for tid in payload.get("tiles", []):
            conn.execute(
                """UPDATE tiles SET dimension_id = ? WHERE id = ? AND dimension_id IS NULL
                   AND dashboard_id IN (SELECT id FROM dashboards WHERE client_id = ?)""", (iid, tid, client_id))
    return iid
