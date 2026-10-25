"""A measure defined once and shown on other dashboards."""
import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from app import db
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    from app.main import app
    with TestClient(app) as c:
        yield c


def setup(client, codes=("I2D", "O2C", "OPS")):
    """A workspace with On-Time Delivery defined on the first dashboard. Returns (api, cells by (code, slot))."""
    r = client.post("/api/clients", json={"name": "Acme", "dashboards": [{"code": c} for c in codes]})
    api = f"/api/w/{r.json()['slug']}"
    st = client.get(f"{api}/state").json()
    codes_by_id = {d["id"]: d["code"] for d in st["dashboards"]}
    cells = {(codes_by_id[r["dashboard_id"]], r["slot"]): r["id"] for r in st["requirements"]}
    client.patch(f"{api}/requirements/{cells['I2D', 1]}", json={"name": "On-Time Delivery", "description": "Share on time"})
    return api, cells


def cell(client, api, rid):
    return next(r for r in client.get(f"{api}/state").json()["requirements"] if r["id"] == rid)


def test_an_empty_cell_can_show_a_measure_from_another_dashboard(client):
    api, cells = setup(client)
    otd, ops = cells["I2D", 1], cells["OPS", 1]
    client.patch(f"{api}/requirements/{ops}", json={"assignee_id": None, "followup": True})
    r = client.patch(f"{api}/requirements/{ops}", json={"ref_id": otd})
    assert r.status_code == 200 and r.json()["ref_id"] == otd and not r.json()["followup"]
    # Counted once.
    from app import db
    with db.transaction() as conn:
        cid = conn.execute("SELECT id FROM clients WHERE slug = ?", (api.rsplit("/", 1)[1],)).fetchone()[0]
        assert db.client_progress(conn, cid)["defined"] == 1
    st = client.get(f"{api}/planning").json()
    assert [m["name"] for m in st["measures"]] == ["On-Time Delivery"]
    assert {(p["dashboard_id"], p["measure_id"], p["shared"]) for p in st["placements"]} == {
        (cell(client, api, otd)["dashboard_id"], otd, False), (cell(client, api, ops)["dashboard_id"], otd, True)}
    # The measure's history says where it's shown.
    log = client.get(f"{api}/requirements/{otd}/activity").json()
    assert log[0]["detail"] == "Added to OPS"

    client.patch(f"{api}/requirements/{ops}", json={"ref_id": None})
    assert cell(client, api, ops)["ref_id"] is None
    assert client.get(f"{api}/requirements/{otd}/activity").json()[0]["detail"] == "Removed from OPS"


def test_shared_measure_rules(client):
    api, cells = setup(client)
    otd = cells["I2D", 1]
    def ref(rid, target):
        return client.patch(f"{api}/requirements/{rid}", json={"ref_id": target})
    assert ref(cells["I2D", 2], otd).status_code == 422          # already on this dashboard
    assert ref(cells["OPS", 1], cells["O2C", 1]).status_code == 422  # nothing defined there
    assert ref(cells["OPS", 1], 999999).status_code == 422
    client.patch(f"{api}/requirements/{cells['OPS', 2]}", json={"name": "Fill Rate"})
    assert ref(cells["OPS", 2], otd).status_code == 422          # not empty
    assert client.patch(f"{api}/requirements/{cells['OPS', 1]}", json={"ref_id": otd, "name": "x"}).status_code == 422

    assert ref(cells["OPS", 1], otd).status_code == 200
    assert ref(cells["OPS", 3], otd).status_code == 422          # once per dashboard
    assert ref(cells["O2C", 1], cells["OPS", 1]).status_code == 422  # not a cell that only shows one
    assert ref(otd, cells["OPS", 2]).status_code == 422          # shown elsewhere, so it stays a definition
    # A shared cell is edited where the measure is defined.
    assert client.patch(f"{api}/requirements/{cells['OPS', 1]}", json={"name": "Mine"}).status_code == 422
    assert client.post(f"{api}/requirements/{cells['OPS', 1]}/status", json={"status": "complete"}).status_code == 422

    other = client.post("/api/clients", json={"name": "Other"}).json()["slug"]
    theirs = client.get(f"/api/w/{other}/state").json()["requirements"][0]["id"]
    assert client.patch(f"/api/w/{other}/requirements/{theirs}", json={"ref_id": otd}).status_code == 422


def test_tiles_can_show_a_shared_measure(client):
    api, cells = setup(client)
    otd, ops = cells["I2D", 1], cells["OPS", 1]
    ops_dash = cell(client, api, ops)["dashboard_id"]
    assert client.post(f"{api}/dashboards/{ops_dash}/tiles", json={"kind": "kpi", "requirement_id": otd}).status_code == 422
    client.patch(f"{api}/requirements/{ops}", json={"ref_id": otd})
    t = client.post(f"{api}/dashboards/{ops_dash}/tiles", json={"kind": "kpi", "requirement_id": otd})
    assert t.status_code == 201
    assert client.post(f"{api}/dashboards/{ops_dash}/tiles", json={"kind": "kpi", "requirement_id": ops}).status_code == 422
    client.patch(f"{api}/requirements/{ops}", json={"ref_id": None})
    assert client.get(f"{api}/planning").json()["tiles"][0]["requirement_id"] is None


def test_deleting_the_definition_clears_it_elsewhere_and_restoring_brings_it_back(client):
    api, cells = setup(client)
    otd, ops = cells["I2D", 1], cells["OPS", 1]
    client.patch(f"{api}/requirements/{ops}", json={"ref_id": otd})
    ops_dash = cell(client, api, ops)["dashboard_id"]
    tile = client.post(f"{api}/dashboards/{ops_dash}/tiles", json={"kind": "kpi", "requirement_id": otd}).json()
    i2d = cell(client, api, otd)["dashboard_id"]

    aid = client.delete(f"{api}/dashboards/{i2d}").json()["archive_id"]
    assert cell(client, api, ops)["ref_id"] is None
    client.post(f"{api}/archive/{aid}/restore")
    st = client.get(f"{api}/planning").json()
    new_otd = next(m["id"] for m in st["measures"] if m["name"] == "On-Time Delivery")
    assert cell(client, api, ops)["ref_id"] == new_otd
    assert next(t for t in st["tiles"] if t["id"] == tile["id"])["requirement_id"] == new_otd


def test_column_archive_keeps_shares_within_the_column(client):
    api, cells = setup(client)
    otd, ops = cells["I2D", 1], cells["OPS", 1]
    client.patch(f"{api}/requirements/{ops}", json={"ref_id": otd})
    aid = client.delete(f"{api}/slots/1").json()["archive_id"]
    client.post(f"{api}/archive/{aid}/restore")
    st = client.get(f"{api}/state").json()
    by = {(r["dashboard_id"], r["slot"]): r for r in st["requirements"]}
    codes = {d["code"]: d["id"] for d in st["dashboards"]}
    assert by[codes["OPS"], 1]["ref_id"] == by[codes["I2D"], 1]["id"]
    assert by[codes["I2D"], 1]["name"] == "On-Time Delivery"


def test_archived_dashboard_that_showed_a_measure_shows_it_again(client):
    api, cells = setup(client)
    otd, ops = cells["I2D", 1], cells["OPS", 1]
    client.patch(f"{api}/requirements/{ops}", json={"ref_id": otd})
    ops_dash = cell(client, api, ops)["dashboard_id"]
    client.post(f"{api}/dashboards/{ops_dash}/tiles", json={"kind": "line", "requirement_id": otd})
    aid = client.delete(f"{api}/dashboards/{ops_dash}").json()["archive_id"]
    client.post(f"{api}/archive/{aid}/restore")
    st = client.get(f"{api}/planning").json()
    new_ops = next(d["id"] for d in st["dashboards"] if d["code"] == "OPS")
    assert {"dashboard_id": new_ops, "slot": 1, "measure_id": otd, "shared": True} in st["placements"]
    assert st["tiles"][0]["dashboard_id"] == new_ops and st["tiles"][0]["requirement_id"] == otd

    # Once its measure is gone, a restored cell comes back empty.
    aid = client.delete(f"{api}/dashboards/{new_ops}").json()["archive_id"]
    client.delete(f"{api}/dashboards/{cell(client, api, otd)['dashboard_id']}")
    client.post(f"{api}/archive/{aid}/restore")
    st = client.get(f"{api}/planning").json()
    assert not any(p["shared"] for p in st["placements"]) and st["tiles"][0]["requirement_id"] is None


def test_exports_show_where_a_measure_is_shared(client):
    api, cells = setup(client)
    client.patch(f"{api}/requirements/{cells['OPS', 1]}", json={"ref_id": cells["I2D", 1]})
    slug = api.rsplit("/", 1)[1]
    csv = client.get(f"/w/{slug}/export.csv").text.splitlines()
    assert len(csv) == 2 and csv[1].endswith(",OPS")
    md = client.get(f"/w/{slug}/export.md").text
    assert "| Also on | OPS |" in md and "## OPS\n\nAlso shows On-Time Delivery (defined on I2D)." in md
    assert md.count("### On-Time Delivery") == 1


def test_demo_has_a_department_dashboard(client):
    st = client.get("/api/w/demo/planning").json()
    ops = next(d for d in st["dashboards"] if d["code"] == "OPS")
    names = {m["id"]: m for m in st["measures"]}
    on_ops = [p for p in st["placements"] if p["dashboard_id"] == ops["id"]]
    assert [names[p["measure_id"]]["name"] for p in on_ops] == ["On-Time Delivery", "Cost Per Order", "Supplier On-Time Rate"]
    assert [p["shared"] for p in on_ops] == [True, True, False]
    procs = {p["id"]: p["name"] for p in st["processes"]}
    assert {procs[names[p["measure_id"]]["process_id"]] for p in on_ops} == {"Inventory to Delivery", "Order to Cash", "Procure to Pay"}
    handbook = client.get("/w/demo/handbook.md").text
    assert "| Filters for the whole dashboard | Date, Product, Site |" in handbook


def test_existing_demo_gets_the_department_dashboard_once(client):
    from app import db
    from app.demo import seed_demo_department
    with db.transaction() as conn:
        cid = conn.execute("SELECT id FROM clients WHERE slug = 'demo'").fetchone()[0]
        conn.execute("DELETE FROM dashboards WHERE client_id = ? AND code = 'OPS'", (cid,))
        seed_demo_department(conn, cid)
        seed_demo_department(conn, cid)
        assert conn.execute("SELECT COUNT(*) FROM dashboards WHERE client_id = ? AND code = 'OPS'", (cid,)).fetchone()[0] == 1
