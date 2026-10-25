import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

DASHBOARDS = [{"code": c} for c in ["P2C", "O2C", "S2P", "F2P", "I2D", "R2P", "H2R", "I2S", "M2O"]]


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from app import db
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    from app.main import app
    with TestClient(app) as c:
        yield c


def new_workspace(client, name="Acme", **kw):
    body = {"name": name, "dashboards": DASHBOARDS, "sources": ["Sage X3", "HubSpot"], **kw}
    r = client.post("/api/clients", json=body)
    assert r.status_code == 201, r.text
    return r.json()["slug"]


def first_req(client, slug):
    return client.get(f"/api/w/{slug}/state").json()["requirements"][0]


def test_pages_render(client):
    slug = new_workspace(client)
    index = client.get("/").text
    assert "Requirements to Governance" in index and "Acme" in index
    moved = client.get("/admin", follow_redirects=False)
    assert moved.status_code == 301 and moved.headers["location"] == "/"
    page = client.get(f"/w/{slug}")
    assert page.status_code == 200 and f'data-slug="{slug}"' in page.text
    assert client.get("/w/demo").status_code == 200
    assert client.get("/w/nope").status_code == 404


def test_demo_is_seeded_and_complete_cells_are_valid(client):
    from app import db
    st = client.get("/api/w/demo/state").json()
    assert st["client"]["is_demo"]
    complete = [r for r in st["requirements"] if r["status"] == "complete"]
    assert complete and all(db.is_complete_ready(r) for r in complete)


def test_new_workspace_has_full_grid(client):
    slug = new_workspace(client, slots=4)
    st = client.get(f"/api/w/{slug}/state").json()
    assert len(st["dashboards"]) == 9
    assert len(st["requirements"]) == 9 * 4
    assert st["sources"] == ["Sage X3", "HubSpot"]


def test_name_only_workspace_starts_with_one_dashboard(client):
    r = client.post("/api/clients", json={"name": "Blank"})
    assert r.status_code == 201
    st = client.get(f"/api/w/{r.json()['slug']}/state").json()
    assert [d["code"] for d in st["dashboards"]] == ["D1"]
    assert st["slots"] == 4 and len(st["requirements"]) == 4


def test_add_measure_column_and_auto_dashboard_code(client):
    slug = client.post("/api/clients", json={"name": "Blank"}).json()["slug"]
    d2 = client.post(f"/api/w/{slug}/dashboards", json={}).json()
    assert d2["code"] == "D2"
    assert client.post(f"/api/w/{slug}/slots").json() == {"slots": 5}
    st = client.get(f"/api/w/{slug}/state").json()
    assert len(st["requirements"]) == 2 * 5
    assert sorted(r["slot"] for r in st["requirements"] if r["dashboard_id"] == d2["id"]) == [1, 2, 3, 4, 5]
    # A dashboard added afterwards gets every column too.
    d3 = client.post(f"/api/w/{slug}/dashboards", json={}).json()
    assert len([r for r in client.get(f"/api/w/{slug}/state").json()["requirements"] if r["dashboard_id"] == d3["id"]]) == 5
    from app import db
    for _ in range(db.MAX_SLOTS - 5):
        client.post(f"/api/w/{slug}/slots")
    assert client.post(f"/api/w/{slug}/slots").status_code == 422


def test_edit_dashboard_code(client):
    slug = client.post("/api/clients", json={"name": "Blank"}).json()["slug"]
    d1 = client.get(f"/api/w/{slug}/state").json()["dashboards"][0]
    d2 = client.post(f"/api/w/{slug}/dashboards", json={}).json()
    base = f"/api/w/{slug}/dashboards"
    assert client.patch(f"{base}/{d1['id']}", json={"code": "sls"}).json()["code"] == "SLS"
    assert client.patch(f"{base}/{d2['id']}", json={"code": "SLS"}).status_code == 422
    assert client.patch(f"{base}/{d1['id']}", json={"code": "sls", "name": "Sales"}).json() == {**d1, "code": "SLS", "name": "Sales"}


def test_add_dashboard(client):
    slug = new_workspace(client, dashboards=[{"code": "SLS", "name": "Sales"}], slots=4)
    r = client.post(f"/api/w/{slug}/dashboards", json={"code": "fin", "name": "Finance"})
    assert r.status_code == 201 and r.json()["code"] == "FIN" and r.json()["position"] == 1
    st = client.get(f"/api/w/{slug}/state").json()
    assert [d["code"] for d in st["dashboards"]] == ["SLS", "FIN"]
    assert len(st["requirements"]) == 2 * 4
    assert client.post(f"/api/w/{slug}/dashboards", json={"code": "Fin"}).status_code == 422
    assert client.post("/api/w/nope/dashboards", json={"code": "X"}).status_code == 404


def test_duplicate_dashboard_codes_rejected(client):
    r = client.post("/api/clients", json={"name": "X", "dashboards": [{"code": "O2C"}, {"code": "o2c"}]})
    assert r.status_code == 422


def test_edit_starts_and_complete_requires_every_answer(client):
    slug = new_workspace(client)
    base = f"/api/w/{slug}/requirements/{first_req(client, slug)['id']}"
    r = client.patch(base, json={"name": "On-Time Delivery"}).json()
    assert r["status"] == "in_progress"
    assert client.post(base + "/status", json={"status": "complete"}).status_code == 422

    owner = client.post(f"/api/w/{slug}/people", json={"name": "Pat"}).json()
    client.patch(base, json={
        "description": "Share delivered on time", "sources": ["Sage X3"],
        "unknown": {"formula": ""}, "target_none": True, "report_none": True,
        "time_basis": "Ship date, fiscal months, MTD", "refresh": "daily",
        "restatement_none": True, "owner_id": owner["id"], "sme_note": "Sam, logistics analyst",
    })
    # Breakdowns is still unanswered.
    r = client.post(base + "/status", json={"status": "complete"})
    assert r.status_code == 422 and r.json()["detail"].endswith(": breakdowns")
    client.patch(base, json={"breakdowns_same": True})
    r = client.post(base + "/status", json={"status": "complete"}).json()
    assert r["status"] == "complete" and r["completed_at"]

    # Clearing an answer reopens it.
    assert client.patch(base, json={"refresh": ""}).json()["status"] == "in_progress"


def test_assign_flag_and_activity(client):
    slug = new_workspace(client)
    rid = first_req(client, slug)["id"]
    p = client.post(f"/api/w/{slug}/people", json={"name": "Jordan", "role": "Logistics"}).json()
    client.patch(f"/api/w/{slug}/requirements/{rid}", json={"assignee_id": p["id"], "followup": True},
                 headers={"X-Actor": str(p["id"])})
    log = client.get(f"/api/w/{slug}/requirements/{rid}/activity").json()
    assert {"Assigned to Jordan", "Flagged for follow-up"} <= {a["detail"] for a in log}
    assert all(a["actor"] == "Jordan" for a in log)
    assert first_req(client, slug)["status"] == "not_started"


def test_workspaces_are_isolated(client):
    a, b = new_workspace(client, "A"), new_workspace(client, "B")
    a_req = first_req(client, a)["id"]
    a_person = client.post(f"/api/w/{a}/people", json={"name": "Only in A"}).json()["id"]
    b_req = first_req(client, b)["id"]

    # A's requirement can't be reached through B's slug.
    assert client.patch(f"/api/w/{b}/requirements/{a_req}", json={"name": "x"}).status_code == 404
    assert client.get(f"/api/w/{b}/requirements/{a_req}/activity").status_code == 404
    # A's people can't be assigned in B, and don't appear there.
    assert client.patch(f"/api/w/{b}/requirements/{b_req}", json={"assignee_id": a_person}).status_code == 422
    assert client.get(f"/api/w/{b}/state").json()["people"] == []
    # Deleting through the wrong workspace does nothing.
    client.delete(f"/api/w/{b}/people/{a_person}")
    assert len(client.get(f"/api/w/{a}/state").json()["people"]) == 1


def test_exports(client):
    slug = new_workspace(client)
    rid = first_req(client, slug)["id"]
    client.patch(f"/api/w/{slug}/requirements/{rid}",
                 json={"name": "Cost Per Order", "description": "Fulfilment cost per order", "sources": ["HubSpot"],
                       "target": "Under $12", "restatement_none": True,
                       "followup": True, "followup_note": "Which cost pool?"})
    lines = client.get(f"/w/{slug}/export.csv").text.strip().splitlines()
    assert len(lines) == 2 and "Cost Per Order" in lines[1] and "Under $12" in lines[1]
    md = client.get(f"/w/{slug}/export.md").text
    assert "### Cost Per Order" in md and "| Source systems | HubSpot |" in md and "Which cost pool?" in md
    assert "| Benchmark or target | Under $12 |" in md and "| Changes to past numbers | Past numbers don't change |" in md
    # Other workspaces' measures never leak into an export.
    assert "On-Time Delivery" not in md


def test_older_database_gets_new_questions(tmp_path, monkeypatch):
    from app import db
    path = tmp_path / "old.db"
    monkeypatch.setattr(db, "DB_PATH", path)
    # The schema as it was before the added columns, with one measure marked complete.
    old = "\n".join(l for l in db.SCHEMA.splitlines() if not (l.split() and l.split()[0] in db.ADDED_COLUMNS))
    conn = sqlite3.connect(path)
    conn.executescript(old)
    conn.execute("INSERT INTO clients (slug, name, created_at) VALUES ('acme-1', 'Acme', '')")
    conn.execute("INSERT INTO dashboards (client_id, code, position) VALUES (1, 'O2C', 0)")
    conn.execute("INSERT INTO requirements (dashboard_id, slot, name, status) VALUES (1, 1, 'DSO', 'complete')")
    conn.commit(); conn.close()

    db.init_db()
    conn = sqlite3.connect(path)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(requirements)")}
    assert set(db.ADDED_COLUMNS) <= cols
    assert conn.execute("SELECT status FROM requirements WHERE name = 'DSO'").fetchone() == ("in_progress",)
    assert conn.execute("SELECT detail FROM activity").fetchall() == [("Reopened because new questions were added",)]
    db.init_db()  # a second start changes nothing
    assert conn.execute("SELECT COUNT(*) FROM activity").fetchone() == (1,)


def test_single_client_database_is_set_aside(tmp_path, monkeypatch):
    from app import db
    path = tmp_path / "old.db"
    monkeypatch.setattr(db, "DB_PATH", path)
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE dashboards (id INTEGER PRIMARY KEY, code TEXT)")
    conn.commit(); conn.close()
    db.init_db()
    assert path.with_suffix(".v1-backup.db").exists()
    conn = sqlite3.connect(path)
    assert conn.execute("SELECT slug FROM clients").fetchall() == [("demo",)]


def cell(st, dashboard_id, slot):
    return next(r for r in st["requirements"] if r["dashboard_id"] == dashboard_id and r["slot"] == slot)


def test_delete_and_restore_dashboard(client):
    slug = new_workspace(client, dashboards=[{"code": "A"}, {"code": "B"}, {"code": "C"}])
    api = f"/api/w/{slug}"
    st = client.get(f"{api}/state").json()
    b = st["dashboards"][1]
    pat = client.post(f"{api}/people", json={"name": "Pat"}).json()
    rid = cell(st, b["id"], 2)["id"]
    client.patch(f"{api}/requirements/{rid}", json={"name": "Fill Rate", "assignee_id": pat["id"]})

    aid = client.delete(f"{api}/dashboards/{b['id']}", headers={"X-Actor": str(pat["id"])}).json()["archive_id"]
    st = client.get(f"{api}/state").json()
    assert [d["code"] for d in st["dashboards"]] == ["A", "C"] and st["archived"] == 1
    assert len(st["requirements"]) == 2 * 4
    [item] = client.get(f"{api}/archive").json()
    assert item["kind"] == "dashboard" and item["label"] == "B" and item["measures"] == ["Fill Rate"]
    assert item["archived_by"] == "Pat"
    assert "Fill Rate" not in client.get(f"/w/{slug}/export.csv").text

    assert client.post(f"{api}/archive/{aid}/restore").json() == {"note": None}
    st = client.get(f"{api}/state").json()
    assert [d["code"] for d in st["dashboards"]] == ["A", "B", "C"] and st["archived"] == 0
    restored = cell(st, st["dashboards"][1]["id"], 2)
    assert restored["name"] == "Fill Rate" and restored["assignee_id"] == pat["id"]
    log = client.get(f"{api}/requirements/{restored['id']}/activity").json()
    assert {"Assigned to Pat", "Restored from the archive"} <= {a["detail"] for a in log}


def test_restored_dashboard_gets_new_code_if_taken_and_drops_removed_people(client):
    slug = new_workspace(client, dashboards=[{"code": "A"}])
    api = f"/api/w/{slug}"
    st = client.get(f"{api}/state").json()
    a = st["dashboards"][0]
    pat = client.post(f"{api}/people", json={"name": "Pat"}).json()
    client.patch(f"{api}/requirements/{cell(st, a['id'], 1)['id']}", json={"name": "X", "owner_id": pat["id"]})
    aid = client.delete(f"{api}/dashboards/{a['id']}").json()["archive_id"]
    client.post(f"{api}/dashboards", json={"code": "A"})
    client.delete(f"{api}/people/{pat['id']}")

    note = client.post(f"{api}/archive/{aid}/restore").json()["note"]
    assert "came back as D1" in note
    st = client.get(f"{api}/state").json()
    back = next(d for d in st["dashboards"] if d["code"] == "D1")
    assert cell(st, back["id"], 1)["name"] == "X" and cell(st, back["id"], 1)["owner_id"] is None


def test_delete_and_restore_column(client):
    slug = new_workspace(client, dashboards=[{"code": "A"}, {"code": "B"}], slots=4)
    api = f"/api/w/{slug}"
    st = client.get(f"{api}/state").json()
    a, b = st["dashboards"]
    for d in (a, b):
        for s in (2, 3):
            client.patch(f"{api}/requirements/{cell(st, d['id'], s)['id']}", json={"name": f"{d['code']}{s}"})

    aid = client.delete(f"{api}/slots/2").json()["archive_id"]
    st = client.get(f"{api}/state").json()
    assert st["slots"] == 3 and len(st["requirements"]) == 2 * 3
    assert cell(st, a["id"], 2)["name"] == "A3"  # column 3 shifted left
    [item] = client.get(f"{api}/archive").json()
    assert item["kind"] == "column" and item["label"] == "Measure 2" and item["measures"] == ["A2", "B2"]

    # A dashboard added while the column was archived gets an empty cell when it comes back.
    c = client.post(f"{api}/dashboards", json={"code": "C"}).json()
    client.post(f"{api}/archive/{aid}/restore")
    st = client.get(f"{api}/state").json()
    assert st["slots"] == 4
    assert [cell(st, a["id"], s)["name"] for s in (1, 2, 3, 4)] == ["", "A2", "A3", ""]
    assert cell(st, b["id"], 2)["name"] == "B2" and cell(st, c["id"], 2)["name"] == ""


def test_cannot_delete_last_column_or_another_workspaces_rows(client):
    one = new_workspace(client, dashboards=[{"code": "A"}], slots=1)
    other = new_workspace(client, dashboards=[{"code": "B"}])
    assert client.delete(f"/api/w/{one}/slots/1").status_code == 422
    assert client.delete(f"/api/w/{one}/slots/5").status_code == 404
    b = client.get(f"/api/w/{other}/state").json()["dashboards"][0]
    assert client.delete(f"/api/w/{one}/dashboards/{b['id']}").status_code == 404
    aid = client.delete(f"/api/w/{other}/dashboards/{b['id']}").json()["archive_id"]
    assert client.post(f"/api/w/{one}/archive/{aid}/restore").status_code == 404
    assert client.get(f"/api/w/{one}/archive").json() == []


def test_dont_know_counts_and_is_exported_for_follow_up(client):
    slug = new_workspace(client)
    base = f"/api/w/{slug}/requirements/{first_req(client, slug)['id']}"
    r = client.patch(base, json={"name": "Fill Rate", "unknown": {"formula": " Jordan in finance ", "sme": ""}}).json()
    assert r["unknown"] == {"formula": "Jordan in finance", "sme": ""}
    assert r["answered"]["formula"] and r["answered"]["sme"] and not r["answered"]["sources"]
    # The measure name can't be "I don't know", and neither can anything that isn't a question.
    assert client.patch(base, json={"unknown": {"name": ""}}).status_code == 422
    assert client.patch(base, json={"unknown": {"bogus": ""}}).status_code == 422

    csv_text = client.get(f"/w/{slug}/export.csv").text
    assert "Calculation (ask Jordan in finance); Subject matter expert" in csv_text
    md = client.get(f"/w/{slug}/export.md").text
    assert "| Don't know yet | Calculation (ask Jordan in finance); Subject matter expert |" in md
    assert client.get("/").text.count("Fill Rate") == 0  # the index shows counts, not names
    log = client.get(f"{base}/activity").json()
    assert any("“I don't know” answers" in a["detail"] for a in log)

    # Unticking removes it again.
    r = client.patch(base, json={"unknown": {"sme": ""}}).json()
    assert r["unknown"] == {"sme": ""} and not r["answered"]["formula"]


def test_old_not_sure_answers_become_dont_know(tmp_path, monkeypatch):
    from app import db
    path = tmp_path / "old.db"
    monkeypatch.setattr(db, "DB_PATH", path)
    # The schema with the old per-question flags instead of the unknown column.
    old = "\n".join(l for l in db.SCHEMA.splitlines() if not l.strip().startswith("unknown "))
    old = old.replace("    formula          TEXT NOT NULL DEFAULT '',",
                      "    formula          TEXT NOT NULL DEFAULT '',\n"
                      "    formula_unknown  INTEGER NOT NULL DEFAULT 0,\n"
                      "    source_unsure    INTEGER NOT NULL DEFAULT 0,")
    conn = sqlite3.connect(path)
    conn.executescript(old)
    conn.execute("INSERT INTO clients (slug, name, created_at) VALUES ('acme-1', 'Acme', '')")
    conn.execute("INSERT INTO dashboards (client_id, code, position) VALUES (1, 'O2C', 0)")
    conn.execute("""INSERT INTO requirements (dashboard_id, slot, name, description, formula_unknown, source_unsure,
                    target_none, report_none, breakdowns_same, time_basis, refresh, restatement_none, owner_note,
                    sme_note, status) VALUES (1, 1, 'DSO', 'd', 1, 1, 1, 1, 1, 't', 'unsure', 1, 'o', 's', 'complete')""")
    conn.commit(); conn.close()

    db.init_db()
    conn = sqlite3.connect(path)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(requirements)")}
    assert "unknown" in cols and not cols & set(db.LEGACY_UNKNOWNS)
    refresh, unknown, status = conn.execute("SELECT refresh, unknown, status FROM requirements").fetchone()
    assert refresh == "" and json.loads(unknown) == {"formula": "", "sources": "", "refresh": ""}
    assert status == "complete"  # still every question answered, so not reopened


def test_restoring_an_old_archive_converts_not_sure(client):
    from app import db
    slug = new_workspace(client, dashboards=[{"code": "A"}])
    st = client.get(f"/api/w/{slug}/state").json()
    old_cell = {"slot": 1, "name": "X", "formula_unknown": 1, "refresh": "unsure", "activity": []}
    with db.transaction() as conn:
        cid = conn.execute("SELECT id FROM clients WHERE slug = ?", (slug,)).fetchone()[0]
        aid = conn.execute("INSERT INTO archive (client_id, kind, label, payload, archived_at) VALUES (?, 'dashboard', 'B', ?, '')",
                           (cid, json.dumps({"code": "B", "name": "", "position": 1, "cells": [old_cell]}))).lastrowid
    client.post(f"/api/w/{slug}/archive/{aid}/restore")
    st = client.get(f"/api/w/{slug}/state").json()
    b = next(d for d in st["dashboards"] if d["code"] == "B")
    x = cell(st, b["id"], 1)
    assert x["name"] == "X" and x["refresh"] == "" and x["unknown"] == {"formula": "", "refresh": ""}


def test_dont_know_flags_for_follow_up(client):
    slug = new_workspace(client)
    base = f"/api/w/{slug}/requirements/{first_req(client, slug)['id']}"
    client.patch(base, json={"name": "Fill Rate"})
    r = client.patch(base, json={"unknown": {"formula": "Jordan"}}).json()
    assert r["followup"] is True
    log = [a["detail"] for a in client.get(f"{base}/activity").json()]
    assert "Flagged for follow-up: “I don't know” on calculation" in log

    # Unticking leaves the flag for whoever triages it.
    assert client.patch(base, json={"unknown": {}}).json()["followup"] is True
    # Once cleared by hand, editing who might know on an existing tick doesn't re-flag...
    client.patch(base, json={"unknown": {"formula": "Jordan"}})
    client.patch(base, json={"followup": False})
    assert client.patch(base, json={"unknown": {"formula": "Jordan Lee"}}).json()["followup"] is False
    # ...but ticking another question does.
    assert client.patch(base, json={"unknown": {"formula": "Jordan Lee", "sme": ""}}).json()["followup"] is True
