"""Requirements to Governance: data warehouse requirement workspaces and their API.

Run locally:  uvicorn app.main:app --reload

Pages
    /                         every workspace, and creating new ones
    /w/{slug}                 measure requirements (/w/demo is the sample)
    /w/{slug}/overview        how the pieces fit, and what needs attention
    /w/{slug}/processes       business processes, their steps and owners
    /w/{slug}/bus-matrix      processes by dimensions
    /w/{slug}/prototypes      dashboard sketches
    /w/{slug}/ownership       departments, and who owns what

The measure API is here; the planning API (processes, dimensions, the bus
matrix, departments, sketches) is in planning.py.

There is no login yet. Anyone with a workspace link can open it, and the
workspace list is open. The random suffix on workspace slugs keeps links unguessable, which is
fine for local development and nothing more. Every API route is scoped by the
slug, so access checks plug in at `_client()` later.

The "You are" picker sends the chosen person's id as `X-Actor`. That is
attribution for the change history, not authentication.
"""
from __future__ import annotations

import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field, field_validator

from . import db, exports, items, planning, preview, spec
from .scope import (actor_id as _actor, check_department, check_person, client_for as _client,
                    person_name as _person_name, shared)

ROOT = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=ROOT / "templates")


def asset(path: str) -> str:
    """A static URL that changes whenever the file does, so browsers never run a stale copy."""
    f = ROOT / "static" / path
    return f"/static/{path}?v={int(f.stat().st_mtime)}" if f.exists() else f"/static/{path}"


templates.env.globals["asset"] = asset


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.init_db()
    yield


app = FastAPI(title="Requirements to Governance", lifespan=lifespan)


# ---------------------------------------------------------------------------
# Request bodies
# ---------------------------------------------------------------------------

class RequirementPatch(BaseModel):
    """Every editable field is optional; only the ones sent are changed."""
    name: str | None = Field(None, max_length=200)
    description: str | None = Field(None, max_length=4000)
    sources: list[str] | None = None
    source_other: str | None = Field(None, max_length=500)
    formula: str | None = Field(None, max_length=4000)
    report: str | None = Field(None, max_length=4000)
    report_none: bool | None = None
    breakdowns: str | None = Field(None, max_length=2000)
    breakdowns_same: bool | None = None
    target: str | None = Field(None, max_length=2000)
    target_none: bool | None = None
    time_basis: str | None = Field(None, max_length=2000)
    refresh: Literal["", "monthly", "weekly", "daily", "intraday"] | None = None
    refresh_why: str | None = Field(None, max_length=1000)
    restatement: str | None = Field(None, max_length=2000)
    restatement_none: bool | None = None
    owner_id: int | None = None
    owner_note: str | None = Field(None, max_length=300)
    sme_id: int | None = None
    sme_note: str | None = Field(None, max_length=300)
    assignee_id: int | None = None
    followup: bool | None = None
    followup_note: str | None = Field(None, max_length=2000)
    notes: str | None = Field(None, max_length=4000)
    process_id: int | None = None
    dimension_ids: list[int] | None = Field(None, max_length=50)
    # Show the measure defined in another cell instead (sent on its own; None stops showing it).
    ref_id: int | None = None
    # Questions answered "I don't know": {question key: who might know, or ""}.
    unknown: dict[str, str] | None = None

    @field_validator("unknown")
    @classmethod
    def _known_questions(cls, v):
        if v is not None:
            if set(v) - set(db.QUESTION_LABELS):
                raise ValueError("unknown question: " + ", ".join(sorted(set(v) - set(db.QUESTION_LABELS))))
            if any(len(who) > 200 for who in v.values()):
                raise ValueError("'who might know' is limited to 200 characters")
            v = {k: who.strip() for k, who in v.items()}
        return v


class StatusChange(BaseModel):
    status: Literal["not_started", "in_progress", "complete"]


class PersonIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    role: str = Field("", max_length=120)
    email: str = Field("", max_length=200)
    department_id: int | None = None


class PersonPatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=120)
    role: str | None = Field(None, max_length=120)
    email: str | None = Field(None, max_length=200)
    department_id: int | None = None


class DashboardPatch(BaseModel):
    code: str | None = Field(None, min_length=1, max_length=12)
    name: str | None = Field(None, max_length=120)
    audience: str | None = Field(None, max_length=1000)
    decisions: str | None = Field(None, max_length=2000)
    cadence: str | None = Field(None, max_length=1000)
    access: str | None = Field(None, max_length=1000)


class SourcesIn(BaseModel):
    sources: list[str]


class DashboardIn(BaseModel):
    code: str = Field("", max_length=12)     # blank: the next free D1, D2, ...
    name: str = Field("", max_length=120)


class ClientIn(BaseModel):
    """Only the name is needed; a new workspace starts with one dashboard row."""
    name: str = Field(min_length=1, max_length=120)
    dashboards: list[DashboardIn] = Field(default_factory=list, max_length=30)
    slots: int = Field(db.DEFAULT_SLOTS, ge=1, le=db.MAX_SLOTS)
    sources: list[str] = []


# Human labels for the activity log.
FIELD_LABELS = {
    "name": "measure name", "description": "description", "sources": "source systems",
    "source_other": "source systems",
    "formula": "formula", "report": "trusted report",
    "report_none": "trusted report", "breakdowns": "breakdowns", "breakdowns_same": "breakdowns",
    "target": "benchmark or target", "target_none": "benchmark or target",
    "time_basis": "date and periods", "refresh": "refresh timing", "refresh_why": "refresh timing",
    "restatement": "changes to past numbers", "restatement_none": "changes to past numbers",
    "owner_id": "definition owner", "owner_note": "definition owner",
    "sme_id": "subject matter expert", "sme_note": "subject matter expert", "notes": "notes",
    "followup_note": "follow-up note", "unknown": "“I don't know” answers",
    "dimension_ids": "breakdowns",
}


# ---------------------------------------------------------------------------
# Measure helpers
# ---------------------------------------------------------------------------

def _get_requirement(conn, client_id: int, rid: int) -> dict:
    row = conn.execute(
        """SELECT r.* FROM requirements r JOIN dashboards d ON d.id = r.dashboard_id
           WHERE r.id = ? AND d.client_id = ?""",
        (rid, client_id),
    ).fetchone()
    if not row:
        raise HTTPException(404, "No such requirement")
    return db.requirement_dict(row)


def _log(conn, rid: int, actor_id: int | None, action: str, detail: str = "") -> None:
    conn.execute(
        "INSERT INTO activity (requirement_id, at, actor_id, action, detail) VALUES (?, ?, ?, ?, ?)",
        (rid, db.now(), actor_id, action, detail),
    )


def _set_ref(conn, client_id: int, cell: dict, target: int | None, actor_id: int | None) -> dict:
    """Make an empty cell show a measure from another dashboard, or stop showing it.
    The history is kept on the measure itself, where people look for it."""
    rid, old = cell["id"], cell["ref_id"]
    code = conn.execute("SELECT code FROM dashboards WHERE id = ?", (cell["dashboard_id"],)).fetchone()[0]
    if target is not None:
        if old is None and (any(cell["answered"].values()) or cell["notes"].strip()):
            raise HTTPException(422, "Only an empty cell can show a measure from another dashboard")
        if conn.execute("SELECT 1 FROM requirements WHERE ref_id = ?", (rid,)).fetchone():
            raise HTTPException(422, "Other dashboards show the measure in this cell")
        problem = db.ref_problem(conn, client_id, cell["dashboard_id"], target, rid)
        if problem:
            raise HTTPException(422, problem)
    if old is not None:
        # Sketch tiles here that showed it no longer can.
        conn.execute("UPDATE tiles SET requirement_id = NULL WHERE dashboard_id = ? AND requirement_id = ?",
                     (cell["dashboard_id"], old))
        _log(conn, old, actor_id, "shared", f"Removed from {code}")
    conn.execute(
        """UPDATE requirements SET ref_id = ?, assignee_id = NULL, followup = 0, followup_note = '', process_id = NULL,
                  dimension_ids = '[]', status = 'not_started', completed_at = NULL, updated_at = ?, updated_by = ?
           WHERE id = ?""", (target, db.now(), actor_id, rid))
    if target is not None:
        _log(conn, target, actor_id, "shared", f"Added to {code}")
    return _get_requirement(conn, client_id, rid)


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------

# (tab key, label, path under /w/{slug}, template), in the order the work is done.
# Measures stay at the workspace's own link, which clients already have.
TABS = [
    ("overview", "Overview", "/overview", "overview.html"),
    ("processes", "Processes", "/processes", "processes.html"),
    ("bus", "Bus matrix", "/bus-matrix", "bus.html"),
    ("measures", "Measures", "", "workspace.html"),
    ("prototypes", "Prototypes", "/prototypes", "prototypes.html"),
    ("ownership", "Ownership", "/ownership", "ownership.html"),
]
templates.env.globals["TABS"] = TABS


def _page(request: Request, slug: str, tab: str):
    with db.transaction() as conn:
        client = _client(conn, slug)
    _, label, _, template = next(t for t in TABS if t[0] == tab)
    return templates.TemplateResponse(request, template, {"client": client, "tab": tab, "tab_label": label})


@app.get("/w/{slug}", response_class=HTMLResponse)
def workspace(request: Request, slug: str):
    return _page(request, slug, "measures")


def _add_page(tab: str, path: str) -> None:
    def page(request: Request, slug: str):
        return _page(request, slug, tab)
    app.add_api_route(f"/w/{{slug}}{path}", page, response_class=HTMLResponse, name=f"page_{tab}")


for _tab, _, _path, _ in TABS:
    if _path:
        _add_page(_tab, _path)


@app.get("/admin")
def admin_moved():
    return RedirectResponse("/", status_code=301)


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    with db.transaction() as conn:
        clients = []
        for row in conn.execute("SELECT * FROM clients ORDER BY is_demo, created_at DESC"):
            c = dict(row)
            c["progress"] = db.client_progress(conn, c["id"])
            c["dashboards"] = conn.execute("SELECT COUNT(*) FROM dashboards WHERE client_id = ?", (c["id"],)).fetchone()[0]
            c["processes"] = conn.execute("SELECT COUNT(*) FROM processes WHERE client_id = ? AND name <> ''", (c["id"],)).fetchone()[0]
            clients.append(c)
    return templates.TemplateResponse(request, "index.html", {"clients": clients})


# ---------------------------------------------------------------------------
# Workspace creation
# ---------------------------------------------------------------------------

@app.post("/api/clients", status_code=201)
def create_client(body: ClientIn):
    dashboards = body.dashboards or [DashboardIn()]
    codes = [d.code.strip().upper() or f"D{i}" for i, d in enumerate(dashboards, 1)]
    if len(set(codes)) != len(codes):
        raise HTTPException(422, "Dashboard codes must be unique")
    sources = list(dict.fromkeys(s.strip() for s in body.sources if s.strip()))
    with db.transaction() as conn:
        cid = db.create_client(conn, body.name.strip(),
                               [(c, d.name.strip()) for c, d in zip(codes, dashboards)],
                               slots=body.slots, sources=sources)
        return dict(conn.execute("SELECT id, slug, name FROM clients WHERE id = ?", (cid,)).fetchone())


# ---------------------------------------------------------------------------
# Workspace API, scoped by slug
# ---------------------------------------------------------------------------

@app.get("/api/w/{slug}/state")
def state(slug: str):
    """Everything the grid needs in one call."""
    with db.transaction() as conn:
        c = _client(conn, slug)
        return {
            **shared(conn, c),
            "dashboards": [dict(r) for r in conn.execute(
                "SELECT id, code, name, position FROM dashboards WHERE client_id = ? ORDER BY position", (c["id"],))],
            "requirements": [db.requirement_dict(r) for r in conn.execute(
                """SELECT r.* FROM requirements r JOIN dashboards d ON d.id = r.dashboard_id
                   WHERE d.client_id = ?""", (c["id"],))],
            "processes": [dict(r) for r in conn.execute(
                "SELECT id, name FROM processes WHERE client_id = ? ORDER BY position, id", (c["id"],))],
            "dimensions": [dict(r) for r in conn.execute(
                "SELECT id, name FROM dimensions WHERE client_id = ? ORDER BY position, id", (c["id"],))],
            "slots": c["slots"],
            "max_slots": db.MAX_SLOTS,
        }


@app.patch("/api/w/{slug}/requirements/{rid}")
def update_requirement(slug: str, rid: int, patch: RequirementPatch, x_actor: str | None = Header(None)):
    changes = patch.model_dump(exclude_unset=True)
    with db.transaction() as conn:
        c = _client(conn, slug)
        actor_id = _actor(conn, c["id"], x_actor)
        current = _get_requirement(conn, c["id"], rid)
        if "ref_id" in changes:
            if len(changes) > 1:
                raise HTTPException(422, "Send ref_id on its own")
            return current if changes["ref_id"] == current["ref_id"] else _set_ref(conn, c["id"], current, changes["ref_id"], actor_id)
        for key in ("owner_id", "sme_id", "assignee_id"):
            check_person(conn, c["id"], changes.get(key), key)
        if changes.get("process_id") is not None and not items.get_item(conn, c["id"], "process", changes["process_id"]):
            raise HTTPException(422, "process_id does not match a process in this workspace")
        if "dimension_ids" in changes:
            changes["dimension_ids"] = list(dict.fromkeys(changes["dimension_ids"] or []))
            known = {r[0] for r in conn.execute("SELECT id FROM dimensions WHERE client_id = ?", (c["id"],))}
            if set(changes["dimension_ids"]) - known:
                raise HTTPException(422, "dimension_ids includes a dimension that isn't in this workspace")
        changes = {k: v for k, v in changes.items() if current.get(k) != v}
        if not changes:
            return current
        if current["ref_id"] is not None:
            raise HTTPException(422, "This cell shows a measure from another dashboard. Edit it where it's defined.")

        # Ticking "I don't know" on any question flags the measure for
        # follow-up. Unticking leaves the flag; it's cleared by hand after triage.
        newly_unknown = [k for k in db.QUESTION_LABELS if k in changes.get("unknown", {}) and k not in current["unknown"]]
        auto_flag = bool(newly_unknown) and not current["followup"] and changes.get("followup") is not False
        if auto_flag:
            changes["followup"] = True

        merged = {**current, **changes}
        # Editing an untouched cell starts it; clearing an answer on a
        # completed one reopens it, so "complete" always means every question
        # is answered.
        status = current["status"]
        content_change = any(k not in ("assignee_id", "followup", "followup_note", "process_id") for k in changes)
        if status == "not_started" and content_change:
            changes["status"] = "in_progress"
        elif status == "complete" and not db.is_complete_ready(merged):
            changes["status"] = "in_progress"
            changes["completed_at"] = None

        stored = dict(changes)
        for key in ("sources", "unknown", "dimension_ids"):
            if key in stored:
                stored[key] = json.dumps(stored[key])
        stored["updated_at"] = db.now()
        stored["updated_by"] = actor_id
        cols = ", ".join(f"{k} = ?" for k in stored)
        conn.execute(f"UPDATE requirements SET {cols} WHERE id = ?", (*stored.values(), rid))

        if "assignee_id" in changes:
            _log(conn, rid, actor_id, "assigned", f"Assigned to {_person_name(conn, changes['assignee_id'])}")
        if "process_id" in changes:
            proc = items.get_item(conn, c["id"], "process", changes["process_id"]) if changes["process_id"] else None
            _log(conn, rid, actor_id, "linked", f"Linked to {proc['name'] or 'a process'}" if proc else "Unlinked from its process")
            del changes["process_id"]
        if auto_flag:
            _log(conn, rid, actor_id, "followup", "Flagged for follow-up: “I don't know” on "
                 + ", ".join(db.QUESTION_LABELS[k].lower() for k in newly_unknown))
        elif "followup" in changes:
            _log(conn, rid, actor_id, "followup",
                 "Flagged for follow-up" if changes["followup"] else "Follow-up cleared")
        edited = sorted({FIELD_LABELS[k] for k in changes if k in FIELD_LABELS})
        if edited:
            _log(conn, rid, actor_id, "edited", "Updated " + ", ".join(edited))
        if changes.get("status") == "in_progress" and status == "complete":
            _log(conn, rid, actor_id, "reopened", "Reopened because an answer was cleared")
        return _get_requirement(conn, c["id"], rid)


@app.post("/api/w/{slug}/requirements/{rid}/status")
def change_status(slug: str, rid: int, body: StatusChange, x_actor: str | None = Header(None)):
    with db.transaction() as conn:
        c = _client(conn, slug)
        actor_id = _actor(conn, c["id"], x_actor)
        current = _get_requirement(conn, c["id"], rid)
        if current["ref_id"] is not None:
            raise HTTPException(422, "This cell shows a measure from another dashboard. Change its status where it's defined.")
        if body.status == "complete" and not db.is_complete_ready(current):
            missing = [k for k, ok in current["answered"].items() if not ok]
            raise HTTPException(422, "Answer every question first: " + ", ".join(missing))
        if body.status == current["status"]:
            return current
        conn.execute(
            "UPDATE requirements SET status = ?, completed_at = ?, updated_at = ?, updated_by = ? WHERE id = ?",
            (body.status, db.now() if body.status == "complete" else None, db.now(), actor_id, rid),
        )
        label = {"complete": "Marked complete", "in_progress": "Reopened", "not_started": "Reset"}[body.status]
        _log(conn, rid, actor_id, body.status, label)
        return _get_requirement(conn, c["id"], rid)


@app.get("/api/w/{slug}/requirements/{rid}/activity")
def activity(slug: str, rid: int):
    with db.transaction() as conn:
        c = _client(conn, slug)
        _get_requirement(conn, c["id"], rid)
        rows = conn.execute(
            """SELECT a.at, a.action, a.detail, p.name AS actor
               FROM activity a LEFT JOIN people p ON p.id = a.actor_id
               WHERE a.requirement_id = ? ORDER BY a.at DESC, a.id DESC LIMIT 50""",
            (rid,),
        ).fetchall()
        return [dict(r) for r in rows]


@app.post("/api/w/{slug}/people", status_code=201)
def add_person(slug: str, body: PersonIn):
    with db.transaction() as conn:
        c = _client(conn, slug)
        check_department(conn, c["id"], body.department_id, "department_id")
        cur = conn.execute(
            "INSERT INTO people (client_id, name, role, email, department_id, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (c["id"], body.name.strip(), body.role.strip(), body.email.strip(), body.department_id, db.now()),
        )
        return dict(conn.execute("SELECT id, name, role, email, department_id FROM people WHERE id = ?",
                                 (cur.lastrowid,)).fetchone())


@app.patch("/api/w/{slug}/people/{pid}")
def update_person(slug: str, pid: int, body: PersonPatch):
    changes = body.model_dump(exclude_unset=True)
    with db.transaction() as conn:
        c = _client(conn, slug)
        check_person(conn, c["id"], pid, "person")
        check_department(conn, c["id"], changes.get("department_id"), "department_id")
        changes = {k: v.strip() if isinstance(v, str) else v for k, v in changes.items()}
        if changes:
            conn.execute(f"UPDATE people SET {', '.join(f'{k} = ?' for k in changes)} WHERE id = ?", (*changes.values(), pid))
        return dict(conn.execute("SELECT id, name, role, email, department_id FROM people WHERE id = ?", (pid,)).fetchone())


@app.delete("/api/w/{slug}/people/{pid}", status_code=204)
def remove_person(slug: str, pid: int):
    with db.transaction() as conn:
        c = _client(conn, slug)
        conn.execute("DELETE FROM people WHERE id = ? AND client_id = ?", (pid, c["id"]))


def _code_taken(conn, client_id: int, code: str, other_than: int | None = None) -> bool:
    return conn.execute("SELECT 1 FROM dashboards WHERE client_id = ? AND code = ? AND id IS NOT ?",
                        (client_id, code, other_than)).fetchone() is not None


@app.post("/api/w/{slug}/dashboards", status_code=201)
def add_dashboard(slug: str, body: DashboardIn):
    code = body.code.strip().upper()
    with db.transaction() as conn:
        c = _client(conn, slug)
        if not code:
            code = db.next_code(conn, c["id"])
        elif _code_taken(conn, c["id"], code):
            raise HTTPException(422, f"There is already a {code} dashboard")
        did = db.add_dashboard(conn, c["id"], code, body.name.strip(), c["slots"])
        return dict(conn.execute("SELECT id, code, name, position FROM dashboards WHERE id = ?", (did,)).fetchone())


@app.patch("/api/w/{slug}/dashboards/{did}")
def update_dashboard(slug: str, did: int, body: DashboardPatch):
    with db.transaction() as conn:
        c = _client(conn, slug)
        if not conn.execute("SELECT 1 FROM dashboards WHERE id = ? AND client_id = ?", (did, c["id"])).fetchone():
            raise HTTPException(404, "No such dashboard")
        if body.code is not None:
            code = body.code.strip().upper()
            if not code:
                raise HTTPException(422, "A dashboard needs a code")
            if _code_taken(conn, c["id"], code, other_than=did):
                raise HTTPException(422, f"There is already a {code} dashboard")
            conn.execute("UPDATE dashboards SET code = ? WHERE id = ?", (code, did))
        if body.name is not None:
            conn.execute("UPDATE dashboards SET name = ? WHERE id = ?", (body.name.strip(), did))
        for k in db.DASHBOARD_USE:
            if getattr(body, k) is not None:
                conn.execute(f"UPDATE dashboards SET {k} = ? WHERE id = ?", (getattr(body, k), did))
        # The grid's fields, plus any "data use" answers that were just saved.
        fields = ["id", "code", "name", "position", *(k for k in db.DASHBOARD_USE if k in body.model_fields_set)]
        return dict(conn.execute(f"SELECT {', '.join(fields)} FROM dashboards WHERE id = ?", (did,)).fetchone())


@app.post("/api/w/{slug}/slots")
def add_slot(slug: str):
    """Add one measure column: an empty cell at the new slot on every dashboard."""
    with db.transaction() as conn:
        c = _client(conn, slug)
        if c["slots"] >= db.MAX_SLOTS:
            raise HTTPException(422, f"A workspace can have at most {db.MAX_SLOTS} measures per dashboard")
        db.add_slot(conn, c["id"])
        return {"slots": c["slots"] + 1}


@app.delete("/api/w/{slug}/dashboards/{did}")
def delete_dashboard(slug: str, did: int, x_actor: str | None = Header(None)):
    """Moves the row, with every measure on it, to the archive."""
    with db.transaction() as conn:
        c = _client(conn, slug)
        if not conn.execute("SELECT 1 FROM dashboards WHERE id = ? AND client_id = ?", (did, c["id"])).fetchone():
            raise HTTPException(404, "No such dashboard")
        return {"archive_id": db.archive_dashboard(conn, c["id"], did, _actor(conn, c["id"], x_actor))}


@app.delete("/api/w/{slug}/slots/{slot}")
def delete_slot(slug: str, slot: int, x_actor: str | None = Header(None)):
    """Moves one measure column to the archive; the columns after it shift left."""
    with db.transaction() as conn:
        c = _client(conn, slug)
        if not 1 <= slot <= c["slots"]:
            raise HTTPException(404, "No such measure column")
        if c["slots"] == 1:
            raise HTTPException(422, "A workspace needs at least one measure column")
        return {"archive_id": db.archive_column(conn, c["id"], slot, _actor(conn, c["id"], x_actor))}


@app.get("/api/w/{slug}/archive")
def list_archive(slug: str):
    with db.transaction() as conn:
        c = _client(conn, slug)
        out = []
        for a in conn.execute(
            """SELECT a.*, p.name AS archived_by_name FROM archive a LEFT JOIN people p ON p.id = a.archived_by
               WHERE a.client_id = ? ORDER BY a.archived_at DESC, a.id DESC""", (c["id"],)):
            cells = json.loads(a["payload"]).get("cells", [])  # processes and dimensions have none
            out.append({"id": a["id"], "kind": a["kind"], "label": a["label"], "archived_at": a["archived_at"],
                        "archived_by": a["archived_by_name"],
                        "measures": [x["name"] for x in cells if x["name"]]})
        return out


@app.post("/api/w/{slug}/archive/{aid}/restore")
def restore_archived(slug: str, aid: int, x_actor: str | None = Header(None)):
    with db.transaction() as conn:
        c = _client(conn, slug)
        if not conn.execute("SELECT 1 FROM archive WHERE id = ? AND client_id = ?", (aid, c["id"])).fetchone():
            raise HTTPException(404, "Nothing in the archive with that id")
        return {"note": db.restore(conn, c["id"], aid, _actor(conn, c["id"], x_actor))}


@app.put("/api/w/{slug}/sources")
def set_sources(slug: str, body: SourcesIn):
    clean = list(dict.fromkeys(s.strip() for s in body.sources if s.strip()))
    with db.transaction() as conn:
        c = _client(conn, slug)
        conn.execute("UPDATE clients SET sources = ? WHERE id = ?", (json.dumps(clean), c["id"]))
    return {"sources": clean}


# ---------------------------------------------------------------------------
# Exports: the content handed over at the end, built in exports.py
# ---------------------------------------------------------------------------

def _export(slug: str, build, media_type: str, filename: str, disposition: str = "attachment") -> Response:
    with db.transaction() as conn:
        body = build(conn, _client(conn, slug))
    return Response(body, media_type=media_type,
                    headers={"Content-Disposition": f'{disposition}; filename="{slug}-{filename}"'})


@app.get("/w/{slug}/export.csv")
def export_csv(slug: str):
    return _export(slug, exports.measures_csv, "text/csv", "requirements.csv")


@app.get("/w/{slug}/export.md", response_class=PlainTextResponse)
def export_markdown(slug: str):
    """A measure catalogue in Markdown: pastes into Confluence, SharePoint, Notion or a repo wiki."""
    return _export(slug, exports.catalogue_md, "text/markdown", "catalogue.md", "inline")


@app.get("/w/{slug}/handbook.md", response_class=PlainTextResponse)
def export_handbook(slug: str):
    """Processes, bus matrix, dimensions, measures, dashboards and ownership in one document."""
    return _export(slug, exports.handbook_md, "text/markdown", "handbook.md", "inline")


@app.get("/w/{slug}/bus-matrix.csv")
def export_bus(slug: str):
    return _export(slug, exports.bus_csv, "text/csv", "bus-matrix.csv")


@app.get("/w/{slug}/requirements_spec.json")
def export_spec(slug: str):
    """The requirements spec (contract C8): what the target platform compiles. 422 if it fails the schema."""
    with db.transaction() as conn:
        s = spec.build_spec(conn, _client(conn, slug)["id"])
    problems = spec.validate(s)
    if problems and spec.target_repo() is not None:
        raise HTTPException(422, {"problems": problems[:50]})
    return Response(json.dumps(s, indent=2), media_type="application/json",
                    headers={"Content-Disposition": f'inline; filename="{slug}-requirements_spec.json"'})


# Before planning: its /api/w/{slug}/{kind} would read "preview" as a kind.
app.include_router(preview.router)
app.include_router(planning.router)
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
