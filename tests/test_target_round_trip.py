"""Sample Project v2 round trip: target at 269ad97 -> workspace -> spec -> compiler -> the pack.

Needs the target repo (TARGET_REPO, or bgr_control_tower_v3 beside this repo);
skipped without it.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from app import compiler, db, spec, target_import

REV = "269ad97"
REPO = spec.target_repo()
PACK = REPO / "projects" / "sample_v2" if REPO else None

pytestmark = pytest.mark.skipif(
    PACK is None or not PACK.exists()
    or subprocess.run(["git", "-C", str(REPO), "cat-file", "-e", REV], capture_output=True).returncode != 0,
    reason="needs the target repo with the pinned checkpoint")


@pytest.fixture()
def workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    db.init_db()
    with db.transaction() as conn:
        cid = target_import.build(conn, target_import.Target(REPO, REV, "sample_v2"), REPO,
                                  "sample-v2", "Sample Project v2", "sample_v2")
    return cid


@pytest.fixture()
def pack(tmp_path):
    """A scratch copy of the pack, inside a copy of just enough of the repo."""
    root = tmp_path / "repo"
    shutil.copytree(PACK, root / "projects" / "sample_v2", ignore=shutil.ignore_patterns("target", "dbt_packages", "logs"))
    for rel in ("sources/systems.yml", str(spec.SCHEMA)):
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(REPO / rel, root / rel)
    return root / "projects" / "sample_v2"


def _spec(cid):
    with db.transaction() as conn:
        return spec.build_spec(conn, cid)


def _compile(s, pack):
    files, res = compiler.compile_spec(s, pack, compiler.registry_of(pack))
    compiler.write(files)
    return files, res


def test_every_target_object_has_its_counterpart(workspace):
    s = _spec(workspace)
    assert spec.validate(s, REPO) == []
    assert len(s["measures"]) == 19                       # 15 KPIs and 4 backlog metrics; components stay out
    assert [d["process"] for d in s["dashboards"]] == ["P2C", "O2C", "S2P", "F2P", "I2D", "R2P", "H2R", "I2S", "M2O"]
    assert sum(len(d["slots"]) for d in s["dashboards"]) == 15
    assert len(s["department_metrics"]) == 16 and len(s["departments"]) == 7
    assert len(s["cubes"]) == 10 and len(s["dimensions"]) == 7
    ticks = {(b["process"], b["dimension"]) for b in s["bus_matrix"]}
    assert ("S2P", "supplier") in ticks and ("F2P", "employee") not in ticks
    otd = next(m for m in s["measures"] if m["name"] == "on_time_delivery_rate")
    assert otd["owner"] == {"unknown": True, "who_might_know": "VP Operations"}
    assert otd["trusted_report"] == {"unknown": True}


def test_round_trip_is_exact(workspace, pack):
    files, res = _compile(_spec(workspace), pack)
    assert files == {}, res.notes


def test_an_edited_definition_is_the_only_change(workspace, pack):
    with db.transaction() as conn:
        conn.execute("UPDATE requirements SET description = ? WHERE metric_name = 'return_rate'",
                     ("Credit value of customer returns as a share of invoiced value.",))
    before = (pack / "models" / "semantic" / "metrics.yml").read_text().splitlines()
    files, _ = _compile(_spec(workspace), pack)
    assert [p.name for p in files] == ["metrics.yml"]
    after = (pack / "models" / "semantic" / "metrics.yml").read_text().splitlines()
    changed = [ln for ln in after if ln not in before] + [ln for ln in before if ln not in after]
    assert changed == ["    description: Credit value of customer returns as a share of invoiced value.",
                       "    description: Credit value of customer returns as a share of invoiced value, dated on the order."]


def test_a_new_measure_lands_in_the_backlog_until_it_has_a_definition(workspace, pack):
    with db.transaction() as conn:
        db.add_slot(conn, workspace)
        rid = conn.execute("""SELECT r.id FROM requirements r JOIN dashboards d ON d.id = r.dashboard_id
                              WHERE d.client_id = ? AND d.code = 'OTHER' AND r.name = '' ORDER BY r.slot""",
                           (workspace,)).fetchone()[0]
        conn.execute("UPDATE requirements SET name = 'Backorder Rate', description = 'Order lines waiting on stock.', "
                     "status = 'in_progress' WHERE id = ?", (rid,))
    files, res = _compile(_spec(workspace), pack)
    assert [p.name for p in files] == ["backlog.yml"]
    text = (pack / "backlog.yml").read_text()
    assert "- name: backorder_rate" in text and compiler.NO_DEFINITION in text


def test_a_moved_slot_rewrites_only_the_seed(workspace, pack):
    with db.transaction() as conn:
        o2c = conn.execute("SELECT id FROM dashboards WHERE client_id = ? AND code = 'O2C'", (workspace,)).fetchone()[0]
        conn.execute("UPDATE requirements SET slot = 99 WHERE dashboard_id = ? AND slot = 4", (o2c,))
        conn.execute("UPDATE requirements SET slot = 4 WHERE dashboard_id = ? AND slot = 3", (o2c,))
        conn.execute("UPDATE requirements SET slot = 3 WHERE dashboard_id = ? AND slot = 99", (o2c,))
    s = _spec(workspace)
    files, _ = _compile(s, pack)
    assert sorted(p.name for p in files) == ["metrics.yml", "process_metric.csv"]   # order_fill_rate is O2C-M3 now
    assert "O2C,order_fill_rate,M3,3" in (pack / "seeds" / "process_metric.csv").read_text()
    json.dumps(s)
