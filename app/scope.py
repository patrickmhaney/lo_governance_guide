"""Scoping helpers shared by every router: each lookup goes through the client.

Every workspace route finds its client by slug in client_for(), which is where
a login-based access check will go.
"""
from __future__ import annotations

import json

from fastapi import HTTPException


def client_for(conn, slug: str) -> dict:
    row = conn.execute("SELECT * FROM clients WHERE slug = ?", (slug,)).fetchone()
    if not row:
        raise HTTPException(404, "No such workspace")
    c = dict(row)
    c["sources"] = json.loads(c["sources"])
    c["is_demo"] = bool(c["is_demo"])
    return c


def actor_id(conn, client_id: int, actor: str | None) -> int | None:
    """The "You are" choice sent as X-Actor. Attribution only, not authentication."""
    if not actor or not actor.isdigit():
        return None
    row = conn.execute("SELECT id FROM people WHERE id = ? AND client_id = ?", (int(actor), client_id)).fetchone()
    return row["id"] if row else None


def person_name(conn, pid: int | None) -> str:
    if pid is None:
        return "nobody"
    row = conn.execute("SELECT name FROM people WHERE id = ?", (pid,)).fetchone()
    return row["name"] if row else "a removed person"


def check_person(conn, client_id: int, pid: int | None, what: str) -> None:
    if pid is not None and not conn.execute("SELECT 1 FROM people WHERE id = ? AND client_id = ?", (pid, client_id)).fetchone():
        raise HTTPException(422, f"{what} does not match a person in this workspace")


def check_department(conn, client_id: int, did: int | None, what: str) -> None:
    if did is not None and not conn.execute("SELECT 1 FROM departments WHERE id = ? AND client_id = ?", (did, client_id)).fetchone():
        raise HTTPException(422, f"{what} does not match a department in this workspace")


# Assigned and completed counts per person, across measures, processes and dimensions.
_WORK = """
    SELECT r.assignee_id AS pid, r.status FROM requirements r JOIN dashboards d ON d.id = r.dashboard_id
     WHERE d.client_id = :c AND r.name <> ''
    UNION ALL SELECT assignee_id, status FROM processes WHERE client_id = :c
    UNION ALL SELECT assignee_id, status FROM dimensions WHERE client_id = :c
"""


def shared(conn, c: dict) -> dict:
    """What every workspace page needs: people, departments, source systems, archive size."""
    counts = {r["pid"]: (r["assigned"], r["complete"]) for r in conn.execute(
        f"""SELECT pid, COUNT(*) AS assigned, SUM(status = 'complete') AS complete FROM ({_WORK})
            WHERE pid IS NOT NULL GROUP BY pid""", {"c": c["id"]})}
    people = []
    for r in conn.execute("SELECT id, name, role, email, department_id FROM people WHERE client_id = ? ORDER BY name COLLATE NOCASE",
                          (c["id"],)):
        p = dict(r)
        p["assigned"], p["complete"] = counts.get(p["id"], (0, 0))
        people.append(p)
    return {
        "client": {k: c[k] for k in ("slug", "name", "is_demo")},
        "people": people,
        "departments": [dict(r) for r in conn.execute(
            "SELECT id, name FROM departments WHERE client_id = ? ORDER BY name COLLATE NOCASE", (c["id"],))],
        "sources": c["sources"],
        "archived": conn.execute("SELECT COUNT(*) FROM archive WHERE client_id = ?", (c["id"],)).fetchone()[0],
    }
