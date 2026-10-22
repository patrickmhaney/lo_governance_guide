"""Governance Guide: measure requirement workspaces and their API.

Run locally:  uvicorn app.main:app --reload

Pages
    /              every workspace, and creating new ones
    /w/{slug}      one requirements workspace (/w/demo is the sample)

There is no login yet. Anyone with a workspace link can open it, and the
workspace list is open. The random suffix on workspace slugs keeps links unguessable, which is
fine for local development and nothing more. Every API route is scoped by the
slug, so access checks plug in at `_client()` later.

The "You are" picker sends the chosen person's id as `X-Actor`. That is
attribution for the change history, not authentication.
"""
from __future__ import annotations

import csv
import io
import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field, field_validator

from . import db

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


app = FastAPI(title="Governance Guide", lifespan=lifespan)


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


class DashboardPatch(BaseModel):
    code: str | None = Field(None, min_length=1, max_length=12)
    name: str | None = Field(None, max_length=120)


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
}

REFRESH_LABELS = {"monthly": "Monthly", "weekly": "Weekly", "daily": "Daily",
                  "intraday": "During the day"}


# ---------------------------------------------------------------------------
# Scoping helpers: every lookup goes through the client
# ---------------------------------------------------------------------------

def _client(conn, slug: str) -> dict:
    row = conn.execute("SELECT * FROM clients WHERE slug = ?", (slug,)).fetchone()
    if not row:
        raise HTTPException(404, "No such workspace")
    c = dict(row)
    c["sources"] = json.loads(c["sources"])
    c["is_demo"] = bool(c["is_demo"])
    return c


def _actor(conn, client_id: int, actor: str | None) -> int | None:
    if not actor or not actor.isdigit():
        return None
    row = conn.execute("SELECT id FROM people WHERE id = ? AND client_id = ?", (int(actor), client_id)).fetchone()
    return row["id"] if row else None


def _person_name(conn, pid: int | None) -> str:
    if pid is None:
        return "nobody"
    row = conn.execute("SELECT name FROM people WHERE id = ?", (pid,)).fetchone()
    return row["name"] if row else "a removed person"


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


def _defined_rows(conn, client_id: int):
    return conn.execute(
        """SELECT d.code, d.name AS dashboard, r.*, a.name AS assignee, o.name AS owner, s.name AS sme
           FROM requirements r
           JOIN dashboards d ON d.id = r.dashboard_id
           LEFT JOIN people a ON a.id = r.assignee_id
           LEFT JOIN people o ON o.id = r.owner_id
           LEFT JOIN people s ON s.id = r.sme_id
           WHERE d.client_id = ? AND r.name <> ''
           ORDER BY d.position, r.slot""",
        (client_id,),
    ).fetchall()


def _sources_text(r) -> str:
    sources = json.loads(r["sources"] or "[]")
    if r["source_other"]:
        sources.append(r["source_other"])
    return "; ".join(sources)


def _unknown_text(r) -> str:
    """The questions answered "I don't know", with who might know, for follow-up."""
    unknown = json.loads(r["unknown"] or "{}")
    return "; ".join(db.QUESTION_LABELS[k] + (f" (ask {who})" if who else "")
                     for k, who in sorted(unknown.items(), key=lambda kv: list(db.QUESTION_LABELS).index(kv[0])))


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------

@app.get("/w/{slug}", response_class=HTMLResponse)
def workspace(request: Request, slug: str):
    with db.transaction() as conn:
        client = _client(conn, slug)
    return templates.TemplateResponse(request, "workspace.html", {"client": client})


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
            "client": {k: c[k] for k in ("slug", "name", "is_demo")},
            "dashboards": [dict(r) for r in conn.execute(
                "SELECT id, code, name, position FROM dashboards WHERE client_id = ? ORDER BY position", (c["id"],))],
            "people": [dict(r) for r in conn.execute(
                "SELECT id, name, role, email FROM people WHERE client_id = ? ORDER BY name COLLATE NOCASE", (c["id"],))],
            "requirements": [db.requirement_dict(r) for r in conn.execute(
                """SELECT r.* FROM requirements r JOIN dashboards d ON d.id = r.dashboard_id
                   WHERE d.client_id = ?""", (c["id"],))],
            "sources": c["sources"],
            "slots": c["slots"],
            "max_slots": db.MAX_SLOTS,
            "archived": conn.execute("SELECT COUNT(*) FROM archive WHERE client_id = ?", (c["id"],)).fetchone()[0],
        }


@app.patch("/api/w/{slug}/requirements/{rid}")
def update_requirement(slug: str, rid: int, patch: RequirementPatch, x_actor: str | None = Header(None)):
    changes = patch.model_dump(exclude_unset=True)
    with db.transaction() as conn:
        c = _client(conn, slug)
        actor_id = _actor(conn, c["id"], x_actor)
        current = _get_requirement(conn, c["id"], rid)
        for key in ("owner_id", "sme_id", "assignee_id"):
            if changes.get(key) is not None and not conn.execute(
                "SELECT 1 FROM people WHERE id = ? AND client_id = ?", (changes[key], c["id"])
            ).fetchone():
                raise HTTPException(422, f"{key} does not match a person in this workspace")
        changes = {k: v for k, v in changes.items() if current.get(k) != v}
        if not changes:
            return current

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
        content_change = any(k not in ("assignee_id", "followup", "followup_note") for k in changes)
        if status == "not_started" and content_change:
            changes["status"] = "in_progress"
        elif status == "complete" and not db.is_complete_ready(merged):
            changes["status"] = "in_progress"
            changes["completed_at"] = None

        stored = dict(changes)
        for key in ("sources", "unknown"):
            if key in stored:
                stored[key] = json.dumps(stored[key])
        stored["updated_at"] = db.now()
        stored["updated_by"] = actor_id
        cols = ", ".join(f"{k} = ?" for k in stored)
        conn.execute(f"UPDATE requirements SET {cols} WHERE id = ?", (*stored.values(), rid))

        if "assignee_id" in changes:
            _log(conn, rid, actor_id, "assigned", f"Assigned to {_person_name(conn, changes['assignee_id'])}")
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
        cur = conn.execute(
            "INSERT INTO people (client_id, name, role, email, created_at) VALUES (?, ?, ?, ?, ?)",
            (c["id"], body.name.strip(), body.role.strip(), body.email.strip(), db.now()),
        )
        return dict(conn.execute("SELECT id, name, role, email FROM people WHERE id = ?", (cur.lastrowid,)).fetchone())


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
        return dict(conn.execute("SELECT id, code, name, position FROM dashboards WHERE id = ?", (did,)).fetchone())


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
            cells = json.loads(a["payload"])["cells"]
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
# Exports: the content handed over at the end, in whatever home the client wants
# ---------------------------------------------------------------------------

@app.get("/w/{slug}/export.csv")
def export_csv(slug: str):
    with db.transaction() as conn:
        c = _client(conn, slug)
        rows = _defined_rows(conn, c["id"])
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["dashboard", "slot", "measure", "definition", "calculation", "source_systems",
                "benchmark_target", "trusted_report", "breakdowns", "date_and_periods", "refresh", "refresh_why",
                "changes_to_past_numbers", "definition_owner", "subject_matter_expert", "dont_know", "assignee",
                "status", "follow_up", "follow_up_note", "notes", "updated_at"])
    for r in rows:
        w.writerow([
            r["code"] + (f" {r['dashboard']}" if r["dashboard"] else ""), f"M{r['slot']}", r["name"], r["description"],
            r["formula"], _sources_text(r),
            "(none)" if r["target_none"] and not r["target"] else r["target"],
            "(none today)" if r["report_none"] and not r["report"] else r["report"],
            "(same as current report)" if r["breakdowns_same"] and not r["breakdowns"] else r["breakdowns"],
            r["time_basis"], r["refresh"], r["refresh_why"],
            "(past numbers don't change)" if r["restatement_none"] and not r["restatement"] else r["restatement"],
            r["owner"] or r["owner_note"], r["sme"] or r["sme_note"], _unknown_text(r), r["assignee"] or "",
            r["status"], "yes" if r["followup"] else "", r["followup_note"], r["notes"], r["updated_at"] or "",
        ])
    return Response(buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{slug}-requirements.csv"'})


@app.get("/w/{slug}/export.md", response_class=PlainTextResponse)
def export_markdown(slug: str):
    """A measure catalogue in Markdown: pastes into Confluence, SharePoint, Notion or a repo wiki."""
    with db.transaction() as conn:
        c = _client(conn, slug)
        rows = _defined_rows(conn, c["id"])
    out = [f"# {c['name']}: measure catalogue", "",
           f"{len(rows)} measures. Generated {db.now()[:10]}.", ""]
    current = None
    for r in rows:
        if r["code"] != current:
            current = r["code"]
            out += [f"## {r['code']}" + (f": {r['dashboard']}" if r["dashboard"] else ""), ""]
        out += [f"### {r['name']}", ""]
        if r["description"]:
            out += [r["description"], ""]
        status = {"complete": "Agreed", "in_progress": "Draft", "not_started": "Not started"}[r["status"]]
        fields = [
            ("Status", status + (" (needs follow-up)" if r["followup"] else "")),
            ("Definition owner", r["owner"] or r["owner_note"] or "Not yet named"),
            ("Subject matter expert", r["sme"] or r["sme_note"] or "Not yet named"),
            ("Calculation", r["formula"]),
            ("Source systems", _sources_text(r)),
            ("Benchmark or target", r["target"] or ("None" if r["target_none"] else "")),
            ("Breakdowns", r["breakdowns"] or ("Same as the current report" if r["breakdowns_same"] else "")),
            ("Date and periods", r["time_basis"]),
            ("Refresh", REFRESH_LABELS.get(r["refresh"], "") + (f" ({r['refresh_why']})" if r["refresh_why"] else "")),
            ("Changes to past numbers", r["restatement"] or ("Past numbers don't change" if r["restatement_none"] else "")),
            ("Trusted report", r["report"] or ("None today" if r["report_none"] else "")),
            ("Don't know yet", _unknown_text(r)),
        ]
        out += ["| | |", "|---|---|"]
        out += [f"| {k} | {v.replace('|', '/').replace(chr(10), ' ')} |" for k, v in fields if v]
        if r["followup"] and r["followup_note"]:
            out += ["", f"> Open question: {r['followup_note']}"]
        out.append("")
    return PlainTextResponse("\n".join(out), media_type="text/markdown",
                             headers={"Content-Disposition": f'inline; filename="{slug}-catalogue.md"'})


app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
