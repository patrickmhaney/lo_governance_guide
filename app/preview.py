"""Build preview: a workspace's requirements as numbers, in a labelled preview.

    POST /api/w/{slug}/preview      compile, rebuild what changed, read the KPIs (about a minute)
    GET  /api/w/{slug}/preview      the job: its log, what changed, the preview numbers

Never one per keystroke: someone presses Build preview. It compiles the
requirements spec into the workspace's target pack (contract C8, compiler.py),
writing to the target checkout's working tree - one branch for every
workspace, so each preview is an ordinary diff there - then asks the target
to rebuild what changed (`python -m ctl --project <pack> preview`) and to
report each KPI (`python -m ctl --project <pack> kpis`). Each pack has its own
data directory in the target, so workspaces never share numbers.

Numbers appear only here, labelled as a preview of synthetic data, and in the
target's Studio; the measure grid (review mode) never shows them.
"""
from __future__ import annotations

import json
import os
import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import APIRouter, HTTPException

from . import compiler, db, spec
from .scope import client_for

router = APIRouter()
STUDIO_URL = os.environ.get("STUDIO_URL", "http://localhost:8801")
LOG_LINES = 400


@dataclass
class Job:
    pack: str
    status: str = "running"          # running | done | failed
    step: str = "compile"
    log: list[str] = field(default_factory=list)
    changed: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    kpis: list[dict] = field(default_factory=list)
    error: str | None = None
    started_at: str = field(default_factory=db.now)
    finished_at: str | None = None

    def say(self, line: str) -> None:
        self.log.append(line)
        del self.log[:-LOG_LINES]

    def public(self) -> dict:
        return {**{k: v for k, v in self.__dict__.items()},
                "studio_url": f"{STUDIO_URL}/?project={self.pack}&view=dash"}


JOBS: dict[str, Job] = {}
_LOCK = threading.Lock()


def _target() -> tuple[Path, Path]:
    repo = spec.target_repo()
    if repo is None or not (repo / "projects").is_dir():
        raise HTTPException(409, "No target repo: set TARGET_REPO to the target checkout.")
    python = repo / ".venv" / "bin" / "python"
    if not python.exists():
        raise HTTPException(409, f"The target has no virtualenv at {python}: run `make setup` there.")
    return repo, python


def _ctl(job: Job, repo: Path, python: Path, *args: str) -> str:
    """Run one target step, streaming its output into the job's log. Returns its last line."""
    env = {**os.environ, "CT_PROJECT": job.pack, "DBT_USE_COLORS": "false", "PYTHONUNBUFFERED": "1"}
    env.pop("VIRTUAL_ENV", None)
    proc = subprocess.Popen([str(python), "-m", "ctl", "--project", job.pack, *args], cwd=repo, env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    last = ""
    assert proc.stdout
    for line in proc.stdout:
        line = line.rstrip()
        if line:
            last = line
            job.say(line)
    if proc.wait():
        raise RuntimeError(f"`python -m ctl {' '.join(args)}` failed: {last}")
    return last


def _run(job: Job, s: dict) -> None:
    try:
        repo, python = _target()
        pack = repo / "projects" / job.pack
        job.say(f"compiling the requirements into projects/{job.pack}/")
        files, res = compiler.compile_spec(s, pack, compiler.registry_of(pack))
        compiler.write(files)
        job.changed, job.notes = res.changed, res.notes
        for n in res.notes:
            job.say(f"  {n}")
        job.say(f"{len(files)} file(s) changed" if files else "no requirement changes; rebuilding the preview anyway")
        job.step = "build"
        _ctl(job, repo, python, "check-pack")
        _ctl(job, repo, python, "preview", "--changed", *job.changed)
        job.step = "numbers"
        job.kpis = json.loads(_ctl(job, repo, python, "kpis"))
        job.status = "done"
    except Exception as exc:  # noqa: BLE001 - shown to the person who pressed the button
        job.status, job.error = "failed", str(exc).splitlines()[0]
        job.say(f"error: {job.error}")
    finally:
        job.finished_at = db.now()


@router.post("/api/w/{slug}/preview", status_code=202)
def start_preview(slug: str):
    with db.transaction() as conn:
        c = client_for(conn, slug)
        if not c.get("target_slug"):
            raise HTTPException(409, "This workspace isn't bound to a target pack yet.")
        s = spec.build_spec(conn, c["id"])
    problems = spec.validate(s)
    if problems:
        raise HTTPException(422, {"problems": problems[:20]})
    problems = compiler.check_references(s)
    if problems:
        raise HTTPException(422, {"problems": problems[:20]})
    _target()
    with _LOCK:
        if slug in JOBS and JOBS[slug].status == "running":
            raise HTTPException(409, "A preview is already building.")
        if any(j.status == "running" and j.pack == c["target_slug"] for j in JOBS.values()):
            raise HTTPException(409, "Another workspace is building this pack.")
        job = JOBS[slug] = Job(pack=c["target_slug"])
    threading.Thread(target=_run, args=(job, s), daemon=True).start()
    return job.public()


@router.get("/api/w/{slug}/preview")
def get_preview(slug: str):
    with db.transaction() as conn:
        c = client_for(conn, slug)
    job = JOBS.get(slug)
    return {"bound": bool(c.get("target_slug")), "pack": c.get("target_slug") or None,
            "job": job.public() if job else None}
