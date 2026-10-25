import sqlite3

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from app import db
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    from app.main import app
    with TestClient(app) as c:
        yield c


def workspace(client, name="Acme", dashboards=({"code": "SLS"},)):
    r = client.post("/api/clients", json={"name": name, "dashboards": list(dashboards), "sources": ["ERP", "CRM"]})
    assert r.status_code == 201, r.text
    return r.json()["slug"]


def planning(client, slug):
    return client.get(f"/api/w/{slug}/planning").json()


def full_process(client, slug, name="Order to Cash"):
    """A process with every question answered."""
    api = f"/api/w/{slug}"
    p = client.post(f"{api}/processes", json={"name": name}).json()
    client.post(f"{api}/processes/{p['id']}/steps", json={"name": "Take the order"})
    return client.patch(f"{api}/processes/{p['id']}", json={
        "answers": {"purpose": "p", "boundaries": "b", "event": "One order line", "systems": ["ERP"],
                    "volume": "v", "decisions": "d", "pain_points_none": True, "priority": "now"},
        "owner_note": "Pat, Sales VP"}).json()


def test_planning_pages_render(client):
    slug = workspace(client)
    for path in ("/overview", "/processes", "/bus-matrix", "/prototypes", "/ownership", ""):
        page = client.get(f"/w/{slug}{path}")
        assert page.status_code == 200 and f'data-slug="{slug}"' in page.text, path
    assert client.get("/w/nope/processes").status_code == 404
    assert 'aria-current="page">Bus matrix' in client.get(f"/w/{slug}/bus-matrix").text


def test_process_completes_only_when_every_question_is_answered(client):
    slug = workspace(client)
    api = f"/api/w/{slug}"
    p = client.post(f"{api}/processes", json={"name": "Order to Cash"}).json()
    assert p["status"] == "not_started" and p["answered"]["name"] and not p["answered"]["steps"]
    assert client.post(f"{api}/processes/{p['id']}/status", json={"status": "complete"}).status_code == 422

    p = full_process(client, slug, "Procure to Pay")
    assert p["status"] == "in_progress" and all(p["answered"].values())
    done = client.post(f"{api}/processes/{p['id']}/status", json={"status": "complete"}).json()
    assert done["status"] == "complete" and done["completed_at"]

    # Removing the only step reopens it, as clearing an answer does.
    client.delete(f"{api}/steps/{p['steps'][0]['id']}")
    again = next(x for x in planning(client, slug)["processes"] if x["id"] == p["id"])
    assert again["status"] == "in_progress" and not again["answered"]["steps"]
    log = [a["detail"] for a in client.get(f"{api}/processes/{p['id']}/activity").json()]
    assert "Reopened because the steps were cleared" in log and "Marked complete" in log


def test_item_answers_are_checked_and_merged(client):
    slug = workspace(client)
    api = f"/api/w/{slug}"
    d = client.post(f"{api}/dimensions", json={"name": "Customer"}).json()
    base = f"{api}/dimensions/{d['id']}"
    assert client.patch(base, json={"answers": {"bogus": "x"}}).status_code == 422
    assert client.patch(base, json={"answers": {"history": "sometimes"}}).status_code == 422
    assert client.patch(base, json={"answers": {"quality_none": "yes"}}).status_code == 422
    assert client.patch(base, json={"answers": {"priority": "now"}}).status_code == 422  # a process question
    assert client.patch(base, json={"unknown": {"steps": ""}}).status_code == 422

    client.patch(base, json={"answers": {"definition": "A buyer", "systems": [" ERP ", "ERP", ""]}})
    r = client.patch(base, json={"answers": {"history": "as_was", "quality_none": True}}).json()
    # Answers patch merges into what's there.
    assert r["answers"] == {"definition": "A buyer", "systems": ["ERP"], "history": "as_was", "quality_none": True}
    assert r["answered"]["quality"] and r["answered"]["history"] and not r["answered"]["hierarchy"]


def test_dont_know_on_an_item_flags_it(client):
    slug = workspace(client)
    api = f"/api/w/{slug}"
    p = client.post(f"{api}/processes", json={"name": "Hire to Retire"}).json()
    r = client.patch(f"{api}/processes/{p['id']}", json={"unknown": {"volume": " HR lead "}}).json()
    assert r["followup"] and r["unknown"] == {"volume": "HR lead"} and r["answered"]["volume"]
    log = [a["detail"] for a in client.get(f"{api}/processes/{p['id']}/activity").json()]
    assert "Flagged for follow-up: “I don't know” on volume" in log


def test_bus_matrix_and_measure_links(client):
    slug = workspace(client)
    api = f"/api/w/{slug}"
    p = client.post(f"{api}/processes", json={"name": "O2C"}).json()
    cust = client.post(f"{api}/dimensions", json={"name": "Customer"}).json()
    assert client.put(f"{api}/bus", json={"process_id": p["id"], "dimension_id": cust["id"], "on": True}).status_code == 200
    assert planning(client, slug)["bus"] == [[p["id"], cust["id"]]]
    client.put(f"{api}/bus", json={"process_id": p["id"], "dimension_id": cust["id"], "on": False})
    assert planning(client, slug)["bus"] == []

    rid = client.get(f"{api}/state").json()["requirements"][0]["id"]
    r = client.patch(f"{api}/requirements/{rid}", json={"name": "Revenue", "process_id": p["id"],
                                                         "dimension_ids": [cust["id"], cust["id"]]}).json()
    assert r["process_id"] == p["id"] and r["dimension_ids"] == [cust["id"]]
    # Dimension chips answer the breakdowns question on their own.
    assert r["answered"]["breakdowns"]
    log = [a["detail"] for a in client.get(f"{api}/requirements/{rid}/activity").json()]
    assert "Linked to O2C" in log

    # Another workspace's process or dimension can't be linked.
    other = workspace(client, "Other")
    op = client.post(f"/api/w/{other}/processes", json={"name": "X"}).json()
    od = client.post(f"/api/w/{other}/dimensions", json={"name": "Y"}).json()
    assert client.patch(f"{api}/requirements/{rid}", json={"process_id": op["id"]}).status_code == 422
    assert client.patch(f"{api}/requirements/{rid}", json={"dimension_ids": [od["id"]]}).status_code == 422
    assert client.put(f"{api}/bus", json={"process_id": op["id"], "dimension_id": cust["id"], "on": True}).status_code == 404


def test_delete_and_restore_process_keeps_its_links(client):
    slug = workspace(client)
    api = f"/api/w/{slug}"
    p = full_process(client, slug)
    cust = client.post(f"{api}/dimensions", json={"name": "Customer"}).json()
    client.put(f"{api}/bus", json={"process_id": p["id"], "dimension_id": cust["id"], "on": True})
    rid = client.get(f"{api}/state").json()["requirements"][0]["id"]
    client.patch(f"{api}/requirements/{rid}", json={"name": "Revenue", "process_id": p["id"]})

    aid = client.delete(f"{api}/processes/{p['id']}").json()["archive_id"]
    st = planning(client, slug)
    assert st["processes"] == [] and st["bus"] == [] and st["measures"][0]["process_id"] is None
    [item] = client.get(f"{api}/archive").json()
    assert item["kind"] == "process" and item["label"] == "Order to Cash" and item["measures"] == []

    client.post(f"{api}/archive/{aid}/restore")
    st = planning(client, slug)
    [back] = st["processes"]
    assert back["name"] == "Order to Cash" and back["owner_note"] == "Pat, Sales VP"
    assert [s["name"] for s in back["steps"]] == ["Take the order"]
    assert st["bus"] == [[back["id"], cust["id"]]] and st["measures"][0]["process_id"] == back["id"]
    log = [a["detail"] for a in client.get(f"{api}/processes/{back['id']}/activity").json()]
    assert "Restored from the archive" in log and "Added a step" in log


def test_delete_and_restore_dimension_keeps_its_links(client):
    slug = workspace(client)
    api = f"/api/w/{slug}"
    p = client.post(f"{api}/processes", json={"name": "O2C"}).json()
    cust = client.post(f"{api}/dimensions", json={"name": "Customer"}).json()
    client.put(f"{api}/bus", json={"process_id": p["id"], "dimension_id": cust["id"], "on": True})
    st = client.get(f"{api}/state").json()
    rid, did = st["requirements"][0]["id"], st["dashboards"][0]["id"]
    client.patch(f"{api}/requirements/{rid}", json={"name": "Revenue", "dimension_ids": [cust["id"]]})
    tile = client.post(f"{api}/dashboards/{did}/tiles", json={"kind": "bar", "requirement_id": rid, "dimension_id": cust["id"]}).json()

    aid = client.delete(f"{api}/dimensions/{cust['id']}").json()["archive_id"]
    st = planning(client, slug)
    assert st["measures"][0]["dimension_ids"] == [] and st["tiles"][0]["dimension_id"] is None and st["bus"] == []

    client.post(f"{api}/archive/{aid}/restore")
    st = planning(client, slug)
    [back] = st["dimensions"]
    assert st["measures"][0]["dimension_ids"] == [back["id"]]
    assert st["tiles"][0]["id"] == tile["id"] and st["tiles"][0]["dimension_id"] == back["id"]
    assert st["bus"] == [[p["id"], back["id"]]]


def test_tiles_are_checked(client):
    slug = workspace(client, dashboards=[{"code": "A"}, {"code": "B"}])
    api = f"/api/w/{slug}"
    st = client.get(f"{api}/state").json()
    a, b = st["dashboards"]
    b_rid = next(r["id"] for r in st["requirements"] if r["dashboard_id"] == b["id"])
    assert client.post(f"{api}/dashboards/{a['id']}/tiles", json={"kind": "kpi", "x": 10, "w": 3}).status_code == 422
    assert client.post(f"{api}/dashboards/{a['id']}/tiles", json={"kind": "pie"}).status_code == 422
    assert client.post(f"{api}/dashboards/{a['id']}/tiles", json={"kind": "kpi", "requirement_id": b_rid}).status_code == 422
    t = client.post(f"{api}/dashboards/{a['id']}/tiles", json={"kind": "kpi"}).json()
    assert client.patch(f"{api}/tiles/{t['id']}", json={"x": 9, "w": 4}).status_code == 422
    assert client.patch(f"{api}/tiles/{t['id']}", json={"x": 8, "w": 4, "title": "Big"}).json()["title"] == "Big"
    assert client.patch(f"{api}/dashboards/{a['id']}", json={"audience": "Ops"}).json()["audience"] == "Ops"
    other = workspace(client, "Other")
    assert client.patch(f"/api/w/{other}/tiles/{t['id']}", json={"title": "x"}).status_code == 404
    assert client.delete(f"/api/w/{other}/tiles/{t['id']}").status_code == 404
    assert client.delete(f"{api}/tiles/{t['id']}").status_code == 204


def test_dashboard_archive_keeps_its_sketch(client):
    slug = workspace(client, dashboards=[{"code": "A"}])
    api = f"/api/w/{slug}"
    st = client.get(f"{api}/state").json()
    d = st["dashboards"][0]
    rid = next(r["id"] for r in st["requirements"] if r["slot"] == 2)
    client.patch(f"{api}/requirements/{rid}", json={"name": "Fill Rate"})
    client.patch(f"{api}/dashboards/{d['id']}", json={"audience": "Ops", "access": "Not finance"})
    client.post(f"{api}/dashboards/{d['id']}/tiles", json={"kind": "line", "w": 6, "h": 3, "requirement_id": rid, "note": "weekly"})

    aid = client.delete(f"{api}/dashboards/{d['id']}").json()["archive_id"]
    assert planning(client, slug)["tiles"] == []
    client.post(f"{api}/archive/{aid}/restore")
    st = planning(client, slug)
    [dash] = st["dashboards"]
    [tile] = st["tiles"]
    new_rid = next(m["id"] for m in st["measures"] if m["name"] == "Fill Rate")
    assert dash["audience"] == "Ops" and dash["access"] == "Not finance"
    assert tile["dashboard_id"] == dash["id"] and tile["requirement_id"] == new_rid and tile["note"] == "weekly"


def test_column_archive_relinks_tiles(client):
    slug = workspace(client, dashboards=[{"code": "A"}])
    api = f"/api/w/{slug}"
    st = client.get(f"{api}/state").json()
    d = st["dashboards"][0]
    rid = next(r["id"] for r in st["requirements"] if r["slot"] == 2)
    client.patch(f"{api}/requirements/{rid}", json={"name": "Fill Rate"})
    t = client.post(f"{api}/dashboards/{d['id']}/tiles", json={"kind": "kpi", "requirement_id": rid}).json()
    aid = client.delete(f"{api}/slots/2").json()["archive_id"]
    assert planning(client, slug)["tiles"][0]["requirement_id"] is None
    client.post(f"{api}/archive/{aid}/restore")
    st = planning(client, slug)
    assert st["tiles"][0]["id"] == t["id"] and st["tiles"][0]["requirement_id"] == st["measures"][0]["id"]


def test_departments_and_people(client):
    slug = workspace(client)
    api = f"/api/w/{slug}"
    fin = client.post(f"{api}/departments", json={"name": "Finance"}).json()
    assert client.post(f"{api}/departments", json={"name": "finance"}).status_code == 422
    pat = client.post(f"{api}/people", json={"name": "Pat", "department_id": fin["id"]}).json()
    assert pat["department_id"] == fin["id"]
    sam = client.post(f"{api}/people", json={"name": "Sam"}).json()
    assert client.patch(f"{api}/people/{sam['id']}", json={"department_id": fin["id"]}).json()["department_id"] == fin["id"]
    p = client.post(f"{api}/processes", json={"name": "R2R"}).json()
    client.patch(f"{api}/processes/{p['id']}", json={"owner_dept_id": fin["id"], "assignee_id": pat["id"]})
    step = client.post(f"{api}/processes/{p['id']}/steps", json={"name": "Close"}).json()
    client.patch(f"{api}/steps/{step['id']}", json={"dept_id": fin["id"]})

    st = planning(client, slug)
    assert next(x for x in st["people"] if x["id"] == pat["id"])["assigned"] == 1
    assert client.patch(f"{api}/departments/{fin['id']}", json={"name": "Finance & Accounting"}).json()["name"] == "Finance & Accounting"
    client.delete(f"{api}/departments/{fin['id']}")
    st = planning(client, slug)
    assert st["departments"] == [] and all(x["department_id"] is None for x in st["people"])
    assert st["processes"][0]["owner_dept_id"] is None and st["processes"][0]["steps"][0]["dept_id"] is None

    other = workspace(client, "Other")
    od = client.post(f"/api/w/{other}/departments", json={"name": "Ops"}).json()
    assert client.patch(f"{api}/people/{pat['id']}", json={"department_id": od["id"]}).status_code == 422
    assert client.patch(f"{api}/processes/{p['id']}", json={"owner_dept_id": od["id"]}).status_code == 422
    assert client.delete(f"/api/w/{other}/steps/{step['id']}").status_code == 404


def test_items_are_isolated(client):
    a, b = workspace(client, "A"), workspace(client, "B")
    p = client.post(f"/api/w/{a}/processes", json={"name": "Only in A"}).json()
    assert client.patch(f"/api/w/{b}/processes/{p['id']}", json={"name": "x"}).status_code == 404
    assert client.get(f"/api/w/{b}/processes/{p['id']}/activity").status_code == 404
    assert client.delete(f"/api/w/{b}/processes/{p['id']}").status_code == 404
    assert client.post(f"/api/w/{b}/processes/{p['id']}/steps", json={}).status_code == 404
    assert planning(client, b)["processes"] == []
    assert client.post(f"/api/w/{a}/widgets", json={}).status_code == 422  # not a kind


def test_handbook_and_bus_csv(client):
    slug = workspace(client)
    api = f"/api/w/{slug}"
    p = full_process(client, slug)
    cust = client.post(f"{api}/dimensions", json={"name": "Customer"}).json()
    client.patch(f"{api}/dimensions/{cust['id']}", json={"answers": {"history": "as_was"}})
    client.post(f"{api}/dimensions", json={"name": "Product"})
    client.put(f"{api}/bus", json={"process_id": p["id"], "dimension_id": cust["id"], "on": True})
    rid = client.get(f"{api}/state").json()["requirements"][0]["id"]
    client.patch(f"{api}/requirements/{rid}", json={"name": "Revenue", "process_id": p["id"], "dimension_ids": [cust["id"]]})

    md = client.get(f"/w/{slug}/handbook.md").text
    assert "### Order to Cash" in md and "| 1. Take the order |" in md
    assert "| Process | Customer | Product |" in md and "| Order to Cash | ✓ |  |" in md
    assert "As it was at the time: keep history (type 2)" in md
    assert "#### Revenue" in md and "| Business process | Order to Cash |" in md and "| Dimensions | Customer |" in md
    assert "| No department | Order to Cash |" in md  # owned by a typed-in name, no department
    csv_text = client.get(f"/w/{slug}/bus-matrix.csv").text.splitlines()
    assert csv_text[0] == "business_process,records,Customer,Product" and csv_text[1] == "Order to Cash,One order line,x,"
    assert client.get(f"/w/{slug}/export.csv").text.splitlines()[1].endswith(",Order to Cash,Customer,")


def test_demo_planning_is_seeded_and_valid(client):
    from app import items
    st = planning(client, "demo")
    assert len(st["processes"]) == 5 and len(st["dimensions"]) == 7 and st["bus"] and st["tiles"]
    for kind, key in (("process", "processes"), ("dimension", "dimensions")):
        complete = [x for x in st[key] if x["status"] == "complete"]
        assert complete and all(items.is_complete_ready(kind, x) for x in complete)
    assert all(m["process_id"] for m in st["measures"])


def test_existing_demo_gets_planning_content_once(client):
    from app import db
    from app.demo import seed_demo_planning
    with db.transaction() as conn:
        cid = conn.execute("SELECT id FROM clients WHERE slug = 'demo'").fetchone()[0]
        for t in ("processes", "dimensions", "departments"):
            conn.execute(f"DELETE FROM {t} WHERE client_id = ?", (cid,))
        conn.execute("DELETE FROM tiles")
        seed_demo_planning(conn, cid)
        seed_demo_planning(conn, cid)  # a second run changes nothing
    st = planning(client, "demo")
    assert len(st["processes"]) == 5 and len(st["departments"]) == 5 and len(st["tiles"]) == 10


def test_older_database_gets_planning_tables(tmp_path, monkeypatch):
    from app import db
    path = tmp_path / "old.db"
    monkeypatch.setattr(db, "DB_PATH", path)
    old = db.SCHEMA.replace("    department_id INTEGER REFERENCES departments(id) ON DELETE SET NULL,\n", "")
    for k in db.DASHBOARD_USE:
        old = "\n".join(l for l in old.splitlines() if not l.strip().startswith(f"{k} "))
    old = "\n".join(l for l in old.splitlines() if l.split()[:1] not in (["process_id"], ["dimension_ids"], ["ref_id"]))
    conn = sqlite3.connect(path)
    conn.executescript(old)
    conn.commit(); conn.close()
    db.init_db()
    conn = sqlite3.connect(path)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"departments", "processes", "dimensions", "process_steps", "bus_cells", "tiles", "item_activity"} <= tables
    assert "department_id" in {r[1] for r in conn.execute("PRAGMA table_info(people)")}
    assert set(db.DASHBOARD_USE) <= {r[1] for r in conn.execute("PRAGMA table_info(dashboards)")}
    assert "ref_id" in {r[1] for r in conn.execute("PRAGMA table_info(requirements)")}
