"""Build preview: only for a workspace bound to a target pack, one build at a time, numbers labelled."""
import pytest
from fastapi.testclient import TestClient

from app import db, preview
from app.main import app


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    with TestClient(app) as c:
        yield c


def test_an_unbound_workspace_has_no_preview(client):
    slug = client.post("/api/clients", json={"name": "Acme test"}).json()["slug"]
    assert client.get(f"/api/w/{slug}/preview").json() == {"bound": False, "pack": None, "job": None}
    assert client.post(f"/api/w/{slug}/preview").status_code == 409


def test_the_sample_is_bound_to_the_sample_pack(client):
    body = client.get("/api/w/demo/preview").json()
    assert body["bound"] and body["pack"] == "sample"


def test_no_target_repo_is_a_clear_refusal(client, monkeypatch, tmp_path):
    monkeypatch.setenv("TARGET_REPO", str(tmp_path / "nowhere"))
    r = client.post("/api/w/demo/preview")
    assert r.status_code in (409, 422)


def test_one_build_at_a_time(client, monkeypatch):
    monkeypatch.setitem(preview.JOBS, "demo", preview.Job(pack="sample"))
    monkeypatch.setattr(preview, "_target", lambda: (None, None))
    monkeypatch.setattr(preview.spec, "validate", lambda s, repo=None: [])
    assert client.post("/api/w/demo/preview").status_code == 409
    job = client.get("/api/w/demo/preview").json()["job"]
    assert job["status"] == "running" and job["studio_url"].endswith("?project=sample&view=dash")
