# Governance Guide

Workspaces where business stakeholders fill out measure requirements.

- **`/`** lists every workspace with its progress and exports. **New
  workspace** asks for a name and opens a workspace with one dashboard row (D1)
  and four measure slots. (`/admin` redirects here.)
- **`/w/{slug}`** is one requirements workspace: a grid of dashboards by
  measure slots. Clicking a cell opens the twelve phase 1 questions.
  **`/w/demo`** is a sample workspace for a fictional distributor.

## Run it

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
uvicorn app.main:app --reload
```

Open http://localhost:8000. The database is created on first start at
`data/requirements.db` (override with `REQUIREMENTS_DB=/path/to/file.db`), and the
sample workspace is seeded into it. To reset the sample, delete the `demo` row
in `clients` and restart.

```bash
pytest -q        # each test uses a throwaway database
```

## A client workspace

| Feature | Where |
|---|---|
| Grid of dashboards × measure slots, with status, assignee, follow-up flag and an answered-of-12 meter per cell | Home of the workspace |
| Highlight filters: Mine, Needs follow-up, Unassigned, Don't know | Toolbar |
| Edit a dashboard's code or name | Click it in the row header |
| Add a dashboard row | + under the last row |
| Add a measure column (up to 12) | + after the last column header |
| Delete a dashboard row or a measure column (it goes to the archive) | × on the row or column header, shown on hover |
| Restore something deleted, back where it was | Archive |
| The twelve questions, saved as you type | Cell drawer |
| Reassign, flag for follow-up with a note, mark complete or reopen | Cell drawer, top panel |
| Change history per measure | Cell drawer, bottom |
| Roster with assigned and completed counts | People |
| Source-system choices for this client | Source systems |
| **Handoff content**: a Markdown measure catalogue, and a CSV | Catalogue (Markdown), Export CSV |

The Markdown catalogue is the deliverable that goes into the client's own
wiki, intranet or BI tool at the end. It groups measures by dashboard and shows
each one's status, owner, calculation, sources, breakdowns, refresh, reference
report and any open question.

### Rules the server enforces

- A measure can only be marked complete when all twelve questions have an
  answer. Every question but the measure name has an **I don't know** box,
  with an optional "Who might know?" name; ticking it counts as an answer.
  "No report today", "no target", "past numbers don't change" and "same as
  the current report" count too.
- Ticking "I don't know" on any question flags the measure for follow-up.
  Unticking leaves the flag; whoever triages clears it.
- "I don't know" answers are for follow-up by hand: the **Don't know** filter
  highlights those measures, the workspaces page counts them, and both exports
  list them with who might know (CSV `dont_know`, Markdown "Don't know yet").
- Databases from before the twelve questions get the new columns on start, and
  any measure marked complete under the old seven is reopened.
- Editing an untouched measure moves it to In progress. Assigning or flagging
  alone does not.
- Clearing an answer on a completed measure reopens it.
- Deleting never loses answers. A deleted dashboard row or measure column is
  stored whole in the `archive` table, history included, and restoring it puts
  it back in place: a column shifts later columns right again, and a row whose
  code has been reused since comes back under the next free code (D1, D2...).
  A workspace always keeps at least one measure column.
- Every workspace route is scoped by slug. A requirement, person or dashboard
  from one client can't be read, edited or assigned through another client's
  link (see `test_workspaces_are_isolated`).

## Layout

```
app/
  main.py            pages, workspace creation, workspace API (all scoped by slug), exports
  db.py              SQLite schema, client creation, the completeness rule
  demo.py            the sample workspace
  templates/         base, index (workspace list), workspace, _brand (dot mark)
  static/            site.css (shared), styles.css (workspace), app.js, mark.svg
tests/test_api.py
```

## Before hosting it

- **There is no login.** Anyone with a workspace link can open and edit it, and
  the workspace list at `/` is open to anyone. Workspace slugs end in a random suffix
  (`bgr-bf23c4`), so links can't be guessed, but that is not access control.
  Add login before deploying anywhere public. Every workspace route already
  looks up the client in `_client()` in `main.py`, which is where the access
  check goes: admins see everything, and a client's users see only their own
  workspace.
- **"You are" is attribution only.** It's a name picker stored in the browser
  and sent as `X-Actor` for the change history.
- **The sample workspace can be edited by visitors.** Reset it as described
  above, or make it read-only when login arrives.
- **Edits are last-write-wins**, and other people's changes appear within 15
  seconds.
- **SQLite suits one server.** Postgres is the intended production database;
  the SQL is plain and ports directly.
