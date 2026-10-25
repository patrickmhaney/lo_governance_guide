"""The planning API: departments, business processes and their steps,
dimensions, the bus matrix, and dashboard sketches. Every route is scoped by
the workspace slug, as in main.py.

Processes and dimensions share one set of routes under /api/w/{slug}/{kind},
with the same rules as a measure (see items.py). Routes with a fixed first
segment are declared before those, so "steps" or "tiles" is never read as a kind.
"""
from __future__ import annotations

import json
from enum import Enum
from typing import Any, Literal

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from . import db, items
from .scope import actor_id as _actor, check_department, check_person, client_for as _client, person_name, shared

router = APIRouter()

GRID_COLUMNS = 12


class Kind(str, Enum):
    processes = "processes"
    dimensions = "dimensions"


SINGULAR = {"processes": "process", "dimensions": "dimension"}


# ---------------------------------------------------------------------------
# Request bodies
# ---------------------------------------------------------------------------

class DepartmentIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class ItemIn(BaseModel):
    name: str = Field("", max_length=200)


class ItemPatch(BaseModel):
    """Every field is optional; only the ones sent change. `answers` merges into what's there."""
    name: str | None = Field(None, max_length=200)
    answers: dict[str, Any] | None = None
    unknown: dict[str, str] | None = None
    owner_id: int | None = None
    owner_dept_id: int | None = None
    owner_note: str | None = Field(None, max_length=300)
    assignee_id: int | None = None
    followup: bool | None = None
    followup_note: str | None = Field(None, max_length=2000)
    notes: str | None = Field(None, max_length=4000)


class StatusChange(BaseModel):
    status: Literal["not_started", "in_progress", "complete"]


class MoveIn(BaseModel):
    position: int = Field(ge=0)


class StepIn(BaseModel):
    name: str = Field("", max_length=200)


class StepPatch(BaseModel):
    name: str | None = Field(None, max_length=200)
    dept_id: int | None = None
    person_id: int | None = None
    system: str | None = Field(None, max_length=200)


class BusIn(BaseModel):
    process_id: int
    dimension_id: int
    on: bool


class TileIn(BaseModel):
    kind: Literal["kpi", "line", "bar", "table", "filter", "text"]
    x: int = Field(0, ge=0, lt=GRID_COLUMNS)
    y: int = Field(0, ge=0, le=500)
    w: int = Field(3, ge=1, le=GRID_COLUMNS)
    h: int = Field(2, ge=1, le=12)
    title: str = Field("", max_length=200)
    requirement_id: int | None = None
    dimension_id: int | None = None
    note: str = Field("", max_length=2000)


class TilePatch(BaseModel):
    kind: Literal["kpi", "line", "bar", "table", "filter", "text"] | None = None
    x: int | None = Field(None, ge=0, lt=GRID_COLUMNS)
    y: int | None = Field(None, ge=0, le=500)
    w: int | None = Field(None, ge=1, le=GRID_COLUMNS)
    h: int | None = Field(None, ge=1, le=12)
    title: str | None = Field(None, max_length=200)
    requirement_id: int | None = None
    dimension_id: int | None = None
    note: str | None = Field(None, max_length=2000)


# ---------------------------------------------------------------------------
# Everything the planning pages need in one call
# ---------------------------------------------------------------------------

@router.get("/api/w/{slug}/planning")
def planning_state(slug: str):
    with db.transaction() as conn:
        c = _client(conn, slug)
        cid = c["id"]
        measures = []
        for r in conn.execute(
            """SELECT r.* FROM requirements r JOIN dashboards d ON d.id = r.dashboard_id
               WHERE d.client_id = ? AND r.name <> '' ORDER BY d.position, r.slot""", (cid,)):
            m = db.requirement_dict(r)
            measures.append({k: m[k] for k in (
                "id", "dashboard_id", "slot", "name", "description", "status", "followup", "process_id",
                "dimension_ids", "owner_id", "owner_note", "target", "target_none", "time_basis", "breakdowns")})
        return {
            **shared(conn, c),
            "processes": items.list_items(conn, cid, "process"),
            "dimensions": items.list_items(conn, cid, "dimension"),
            "bus": [[r[0], r[1]] for r in conn.execute(
                """SELECT b.process_id, b.dimension_id FROM bus_cells b JOIN processes p ON p.id = b.process_id
                   WHERE p.client_id = ?""", (cid,))],
            "dashboards": [dict(r) for r in conn.execute(
                f"SELECT id, code, name, position, {', '.join(db.DASHBOARD_USE)} FROM dashboards WHERE client_id = ? ORDER BY position",
                (cid,))],
            "measures": measures,
            # Where each measure appears: the dashboard that defines it, and any that show it.
            "placements": [{**dict(r), "shared": bool(r["shared"])} for r in conn.execute(
                """SELECT r.dashboard_id, r.slot, COALESCE(r.ref_id, r.id) AS measure_id, r.ref_id IS NOT NULL AS shared
                   FROM requirements r JOIN dashboards d ON d.id = r.dashboard_id
                   WHERE d.client_id = ? AND (r.name <> '' OR r.ref_id IS NOT NULL) ORDER BY d.position, r.slot""", (cid,))],
            "tiles": [dict(r) for r in conn.execute(
                "SELECT t.* FROM tiles t JOIN dashboards d ON d.id = t.dashboard_id WHERE d.client_id = ? ORDER BY t.id", (cid,))],
        }


# ---------------------------------------------------------------------------
# Departments
# ---------------------------------------------------------------------------

def _department(conn, client_id: int, did: int) -> dict:
    row = conn.execute("SELECT id, name FROM departments WHERE id = ? AND client_id = ?", (did, client_id)).fetchone()
    if not row:
        raise HTTPException(404, "No such department")
    return dict(row)


def _department_name_taken(conn, client_id: int, name: str, other_than: int | None = None) -> bool:
    return conn.execute("SELECT 1 FROM departments WHERE client_id = ? AND name = ? COLLATE NOCASE AND id IS NOT ?",
                        (client_id, name, other_than)).fetchone() is not None


@router.post("/api/w/{slug}/departments", status_code=201)
def add_department(slug: str, body: DepartmentIn):
    name = body.name.strip()
    with db.transaction() as conn:
        c = _client(conn, slug)
        if _department_name_taken(conn, c["id"], name):
            raise HTTPException(422, f"There is already a {name} department")
        did = conn.execute("INSERT INTO departments (client_id, name) VALUES (?, ?)", (c["id"], name)).lastrowid
        return _department(conn, c["id"], did)


@router.patch("/api/w/{slug}/departments/{did}")
def rename_department(slug: str, did: int, body: DepartmentIn):
    name = body.name.strip()
    with db.transaction() as conn:
        c = _client(conn, slug)
        _department(conn, c["id"], did)
        if _department_name_taken(conn, c["id"], name, other_than=did):
            raise HTTPException(422, f"There is already a {name} department")
        conn.execute("UPDATE departments SET name = ? WHERE id = ?", (name, did))
        return _department(conn, c["id"], did)


@router.delete("/api/w/{slug}/departments/{did}", status_code=204)
def remove_department(slug: str, did: int):
    """People, steps and owners in it are left without a department."""
    with db.transaction() as conn:
        c = _client(conn, slug)
        _department(conn, c["id"], did)
        conn.execute("DELETE FROM departments WHERE id = ?", (did,))


# ---------------------------------------------------------------------------
# Process steps
# ---------------------------------------------------------------------------

def _process(conn, client_id: int, pid: int) -> dict:
    item = items.get_item(conn, client_id, "process", pid)
    if not item:
        raise HTTPException(404, "No such process")
    return item


def _step(conn, client_id: int, sid: int) -> dict:
    row = conn.execute(
        """SELECT s.* FROM process_steps s JOIN processes p ON p.id = s.process_id
           WHERE s.id = ? AND p.client_id = ?""", (sid, client_id)).fetchone()
    if not row:
        raise HTTPException(404, "No such step")
    return dict(row)


def _steps_changed(conn, client_id: int, pid: int, actor_id: int | None, detail: str) -> dict:
    """Steps answer a question, so a change starts the process or reopens it, as an answer does."""
    item = _process(conn, client_id, pid)
    changes = {"updated_at": db.now(), "updated_by": actor_id}
    if item["status"] == "not_started":
        changes["status"] = "in_progress"
    elif item["status"] == "complete" and not items.is_complete_ready("process", item):
        changes.update(status="in_progress", completed_at=None)
    conn.execute(f"UPDATE processes SET {', '.join(f'{k} = ?' for k in changes)} WHERE id = ?", (*changes.values(), pid))
    items.log(conn, client_id, "process", pid, actor_id, "edited", detail)
    if item["status"] == "complete" and changes.get("status") == "in_progress":
        items.log(conn, client_id, "process", pid, actor_id, "reopened", "Reopened because the steps were cleared")
    return _process(conn, client_id, pid)


@router.post("/api/w/{slug}/processes/{pid}/steps", status_code=201)
def add_step(slug: str, pid: int, body: StepIn, x_actor: str | None = Header(None)):
    with db.transaction() as conn:
        c = _client(conn, slug)
        _process(conn, c["id"], pid)
        pos = conn.execute("SELECT COALESCE(MAX(position) + 1, 0) FROM process_steps WHERE process_id = ?", (pid,)).fetchone()[0]
        sid = conn.execute("INSERT INTO process_steps (process_id, position, name) VALUES (?, ?, ?)",
                           (pid, pos, body.name.strip())).lastrowid
        _steps_changed(conn, c["id"], pid, _actor(conn, c["id"], x_actor), "Added a step")
        return _step(conn, c["id"], sid)


@router.patch("/api/w/{slug}/steps/{sid}")
def update_step(slug: str, sid: int, body: StepPatch, x_actor: str | None = Header(None)):
    changes = body.model_dump(exclude_unset=True)
    with db.transaction() as conn:
        c = _client(conn, slug)
        step = _step(conn, c["id"], sid)
        check_person(conn, c["id"], changes.get("person_id"), "person_id")
        check_department(conn, c["id"], changes.get("dept_id"), "dept_id")
        changes = {k: v.strip() if isinstance(v, str) else v for k, v in changes.items()}
        changes = {k: v for k, v in changes.items() if step[k] != v}
        if changes:
            conn.execute(f"UPDATE process_steps SET {', '.join(f'{k} = ?' for k in changes)} WHERE id = ?",
                         (*changes.values(), sid))
            name = changes.get("name", step["name"]) or "a step"
            _steps_changed(conn, c["id"], step["process_id"], _actor(conn, c["id"], x_actor), f"Updated step: {name}")
        return _step(conn, c["id"], sid)


@router.post("/api/w/{slug}/steps/{sid}/move")
def move_step(slug: str, sid: int, body: MoveIn):
    with db.transaction() as conn:
        c = _client(conn, slug)
        step = _step(conn, c["id"], sid)
        items.move(conn, "process_steps", "process_id", step["process_id"], sid, body.position)
        return items.steps_of(conn, step["process_id"])


@router.delete("/api/w/{slug}/steps/{sid}", status_code=204)
def remove_step(slug: str, sid: int, x_actor: str | None = Header(None)):
    with db.transaction() as conn:
        c = _client(conn, slug)
        step = _step(conn, c["id"], sid)
        conn.execute("DELETE FROM process_steps WHERE id = ?", (sid,))
        _steps_changed(conn, c["id"], step["process_id"], _actor(conn, c["id"], x_actor),
                       f"Removed step: {step['name'] or 'unnamed'}")


# ---------------------------------------------------------------------------
# The bus matrix
# ---------------------------------------------------------------------------

@router.put("/api/w/{slug}/bus")
def set_bus_cell(slug: str, body: BusIn, x_actor: str | None = Header(None)):
    with db.transaction() as conn:
        c = _client(conn, slug)
        proc = _process(conn, c["id"], body.process_id)
        dim = items.get_item(conn, c["id"], "dimension", body.dimension_id)
        if not dim:
            raise HTTPException(404, "No such dimension")
        ticked = conn.execute("SELECT 1 FROM bus_cells WHERE process_id = ? AND dimension_id = ?",
                              (proc["id"], dim["id"])).fetchone() is not None
        if ticked != body.on:
            if body.on:
                conn.execute("INSERT INTO bus_cells (process_id, dimension_id) VALUES (?, ?)", (proc["id"], dim["id"]))
            else:
                conn.execute("DELETE FROM bus_cells WHERE process_id = ? AND dimension_id = ?", (proc["id"], dim["id"]))
            items.log(conn, c["id"], "process", proc["id"], _actor(conn, c["id"], x_actor), "bus",
                      f"{'Ticked' if body.on else 'Unticked'} {dim['name'] or 'a dimension'} in the bus matrix")
        return {"process_id": proc["id"], "dimension_id": dim["id"], "on": body.on}


# ---------------------------------------------------------------------------
# Dashboard sketches
# ---------------------------------------------------------------------------

def _tile(conn, client_id: int, tid: int) -> dict:
    row = conn.execute(
        "SELECT t.* FROM tiles t JOIN dashboards d ON d.id = t.dashboard_id WHERE t.id = ? AND d.client_id = ?",
        (tid, client_id)).fetchone()
    if not row:
        raise HTTPException(404, "No such tile")
    return dict(row)


def _check_tile(conn, client_id: int, t: dict) -> None:
    if t["x"] + t["w"] > GRID_COLUMNS:
        raise HTTPException(422, f"A tile must fit in {GRID_COLUMNS} columns")
    if t["requirement_id"] is not None and not conn.execute(
            """SELECT 1 FROM requirements WHERE dashboard_id = ?
               AND ((id = ? AND ref_id IS NULL) OR ref_id = ?)""",
            (t["dashboard_id"], t["requirement_id"], t["requirement_id"])).fetchone():
        raise HTTPException(422, "A tile can only show a measure on its own dashboard")
    if t["dimension_id"] is not None and not items.get_item(conn, client_id, "dimension", t["dimension_id"]):
        raise HTTPException(422, "dimension_id does not match a dimension in this workspace")


@router.post("/api/w/{slug}/dashboards/{did}/tiles", status_code=201)
def add_tile(slug: str, did: int, body: TileIn):
    with db.transaction() as conn:
        c = _client(conn, slug)
        if not conn.execute("SELECT 1 FROM dashboards WHERE id = ? AND client_id = ?", (did, c["id"])).fetchone():
            raise HTTPException(404, "No such dashboard")
        t = {**body.model_dump(), "dashboard_id": did}
        _check_tile(conn, c["id"], t)
        tid = conn.execute(f"INSERT INTO tiles ({', '.join(t)}) VALUES ({', '.join('?' * len(t))})", tuple(t.values())).lastrowid
        return _tile(conn, c["id"], tid)


@router.patch("/api/w/{slug}/tiles/{tid}")
def update_tile(slug: str, tid: int, body: TilePatch):
    changes = body.model_dump(exclude_unset=True)
    with db.transaction() as conn:
        c = _client(conn, slug)
        merged = {**_tile(conn, c["id"], tid), **changes}
        for k in ("kind", "x", "y", "w", "h", "title", "note"):
            if merged[k] is None:
                raise HTTPException(422, f"{k} can't be empty")
        _check_tile(conn, c["id"], merged)
        if changes:
            conn.execute(f"UPDATE tiles SET {', '.join(f'{k} = ?' for k in changes)} WHERE id = ?", (*changes.values(), tid))
        return _tile(conn, c["id"], tid)


@router.delete("/api/w/{slug}/tiles/{tid}", status_code=204)
def remove_tile(slug: str, tid: int):
    with db.transaction() as conn:
        c = _client(conn, slug)
        _tile(conn, c["id"], tid)
        conn.execute("DELETE FROM tiles WHERE id = ?", (tid,))


# ---------------------------------------------------------------------------
# Processes and dimensions: /api/w/{slug}/processes/... and /dimensions/...
# ---------------------------------------------------------------------------

def _item(conn, client_id: int, kind: str, iid: int) -> dict:
    item = items.get_item(conn, client_id, kind, iid)
    if not item:
        raise HTTPException(404, f"No such {kind}")
    return item


def _edited_labels(kind: str, changes: dict, current: dict) -> list[str]:
    labels = items.QUESTIONS[kind]
    out = set()
    for k in changes:
        if k == "answers":
            for a in set(changes["answers"]) | set(current["answers"]):
                if changes["answers"].get(a) != current["answers"].get(a):
                    base = {"systems_other": "systems", "history_note": "history"}.get(a, a.removesuffix("_none"))
                    out.add(labels[base].lower())
        elif k in ("owner_id", "owner_dept_id", "owner_note"):
            out.add("owner")
        elif k == "unknown":
            out.add("“I don't know” answers")
        elif k in ("name", "notes"):
            out.add(k)
    return sorted(out)


@router.post("/api/w/{slug}/{kind}", status_code=201)
def add_item(slug: str, kind: Kind, body: ItemIn):
    k = SINGULAR[kind.value]
    with db.transaction() as conn:
        c = _client(conn, slug)
        iid = items.create_item(conn, c["id"], k, body.name.strip())
        return _item(conn, c["id"], k, iid)


@router.patch("/api/w/{slug}/{kind}/{iid}")
def update_item(slug: str, kind: Kind, iid: int, patch: ItemPatch, x_actor: str | None = Header(None)):
    k = SINGULAR[kind.value]
    table = items.KINDS[k]
    changes = patch.model_dump(exclude_unset=True)
    with db.transaction() as conn:
        c = _client(conn, slug)
        actor_id = _actor(conn, c["id"], x_actor)
        current = _item(conn, c["id"], k, iid)
        for key in ("owner_id", "assignee_id"):
            check_person(conn, c["id"], changes.get(key), key)
        check_department(conn, c["id"], changes.get("owner_dept_id"), "owner_dept_id")
        if "answers" in changes:
            try:
                changes["answers"] = {**current["answers"], **items.clean_answers(k, changes["answers"] or {})}
            except ValueError as e:
                raise HTTPException(422, str(e))
        if "unknown" in changes:
            unknown = changes["unknown"] or {}
            if set(unknown) - set(items.QUESTIONS[k]):
                raise HTTPException(422, "unknown question: " + ", ".join(sorted(set(unknown) - set(items.QUESTIONS[k]))))
            if any(len(who) > 200 for who in unknown.values()):
                raise HTTPException(422, "'who might know' is limited to 200 characters")
            changes["unknown"] = {q: who.strip() for q, who in unknown.items()}
        for key in ("name", "owner_note", "followup_note", "notes"):
            if key in changes and changes[key] is None:
                changes[key] = ""
        changes = {key: v for key, v in changes.items() if current.get(key) != v}
        if not changes:
            return current

        # The same rules as a measure: "I don't know" flags for follow-up,
        # editing starts it, and clearing an answer reopens it.
        newly_unknown = [q for q in items.QUESTIONS[k] if q in changes.get("unknown", {}) and q not in current["unknown"]]
        auto_flag = bool(newly_unknown) and not current["followup"] and changes.get("followup") is not False
        if auto_flag:
            changes["followup"] = True
        merged = {**current, **changes}
        status = current["status"]
        content_change = any(key not in ("assignee_id", "followup", "followup_note") for key in changes)
        if status == "not_started" and content_change:
            changes["status"] = "in_progress"
        elif status == "complete" and not items.is_complete_ready(k, merged):
            changes["status"] = "in_progress"
            changes["completed_at"] = None

        stored = dict(changes)
        for key in ("answers", "unknown"):
            if key in stored:
                stored[key] = json.dumps(stored[key])
        stored["updated_at"] = db.now()
        stored["updated_by"] = actor_id
        conn.execute(f"UPDATE {table} SET {', '.join(f'{key} = ?' for key in stored)} WHERE id = ?", (*stored.values(), iid))

        if "assignee_id" in changes:
            items.log(conn, c["id"], k, iid, actor_id, "assigned", f"Assigned to {person_name(conn, changes['assignee_id'])}")
        if auto_flag:
            items.log(conn, c["id"], k, iid, actor_id, "followup", "Flagged for follow-up: “I don't know” on "
                      + ", ".join(items.QUESTIONS[k][q].lower() for q in newly_unknown))
        elif "followup" in changes:
            items.log(conn, c["id"], k, iid, actor_id, "followup",
                      "Flagged for follow-up" if changes["followup"] else "Follow-up cleared")
        edited = _edited_labels(k, changes, current)
        if edited:
            items.log(conn, c["id"], k, iid, actor_id, "edited", "Updated " + ", ".join(edited))
        if changes.get("status") == "in_progress" and status == "complete":
            items.log(conn, c["id"], k, iid, actor_id, "reopened", "Reopened because an answer was cleared")
        return _item(conn, c["id"], k, iid)


@router.post("/api/w/{slug}/{kind}/{iid}/status")
def change_item_status(slug: str, kind: Kind, iid: int, body: StatusChange, x_actor: str | None = Header(None)):
    k = SINGULAR[kind.value]
    with db.transaction() as conn:
        c = _client(conn, slug)
        actor_id = _actor(conn, c["id"], x_actor)
        current = _item(conn, c["id"], k, iid)
        if body.status == "complete" and not items.is_complete_ready(k, current):
            missing = [q for q, ok in current["answered"].items() if not ok]
            raise HTTPException(422, "Answer every question first: " + ", ".join(missing))
        if body.status == current["status"]:
            return current
        conn.execute(
            f"UPDATE {items.KINDS[k]} SET status = ?, completed_at = ?, updated_at = ?, updated_by = ? WHERE id = ?",
            (body.status, db.now() if body.status == "complete" else None, db.now(), actor_id, iid))
        label = {"complete": "Marked complete", "in_progress": "Reopened", "not_started": "Reset"}[body.status]
        items.log(conn, c["id"], k, iid, actor_id, body.status, label)
        return _item(conn, c["id"], k, iid)


@router.post("/api/w/{slug}/{kind}/{iid}/move")
def move_item(slug: str, kind: Kind, iid: int, body: MoveIn):
    k = SINGULAR[kind.value]
    with db.transaction() as conn:
        c = _client(conn, slug)
        _item(conn, c["id"], k, iid)
        items.move(conn, items.KINDS[k], "client_id", c["id"], iid, body.position)
        return [{"id": r[0], "position": r[1]} for r in conn.execute(
            f"SELECT id, position FROM {items.KINDS[k]} WHERE client_id = ? ORDER BY position", (c["id"],))]


@router.get("/api/w/{slug}/{kind}/{iid}/activity")
def item_activity(slug: str, kind: Kind, iid: int):
    k = SINGULAR[kind.value]
    with db.transaction() as conn:
        c = _client(conn, slug)
        _item(conn, c["id"], k, iid)
        rows = conn.execute(
            """SELECT a.at, a.action, a.detail, p.name AS actor
               FROM item_activity a LEFT JOIN people p ON p.id = a.actor_id
               WHERE a.kind = ? AND a.item_id = ? ORDER BY a.at DESC, a.id DESC LIMIT 50""", (k, iid)).fetchall()
        return [dict(r) for r in rows]


@router.delete("/api/w/{slug}/{kind}/{iid}")
def delete_item(slug: str, kind: Kind, iid: int, x_actor: str | None = Header(None)):
    """Moves it to the archive, with its steps, bus matrix ticks and links to measures and tiles."""
    k = SINGULAR[kind.value]
    with db.transaction() as conn:
        c = _client(conn, slug)
        _item(conn, c["id"], k, iid)
        return {"archive_id": items.archive_item(conn, c["id"], k, iid, _actor(conn, c["id"], x_actor))}
