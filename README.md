# Requirements to Governance

Workspaces where business stakeholders plan a data warehouse: their business
processes and who owns them, the bus matrix, measure requirements, and sketches
of the dashboards.

- **`/`** lists every workspace with its progress and exports. **New
  workspace** asks for a name and opens a workspace with one dashboard row (D1)
  and four measure slots. (`/admin` redirects here.)
- **`/w/{slug}`** is one workspace's measure requirements: a grid of dashboards
  by measure slots. Clicking a cell opens the twelve phase 1 questions. The
  other tabs sit beside it (see [Planning tabs](#planning-tabs)).
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

## Planning tabs

Every workspace has six tabs, in the order the work is done. Measures keep the
workspace's own link, which clients already have.

| Tab | Path | What it's for |
|---|---|---|
| Overview | `/w/{slug}/overview` | The four steps with progress, what needs attention, and the handoff exports |
| Processes | `/w/{slug}/processes` | Business processes, each with eleven questions and its steps; a swimlane per process, one lane per department |
| Bus matrix | `/w/{slug}/bus-matrix` | Processes by dimensions. Tick a box to say "analyze this process by this". Dimensions have nine questions of their own. Pick a dashboard to lay it over the matrix |
| Measures | `/w/{slug}` | The measure grid below, unchanged, plus a business process and dimension chips on each measure |
| Prototypes | `/w/{slug}/prototypes` | A sketch per dashboard row: tiles on a 12-column board, and who uses the dashboard and for what |
| Ownership | `/w/{slug}/ownership` | What has no owner, what each department owns and does, and the departments themselves |

Each planning page opens with a two- or three-line guide, which a viewer can
hide (remembered in their browser) and bring back from the toolbar.

**Processes and dimensions work like measures.** The same drawer, saved as you
type: "I don't know" with "Who might know?" on every question but the name,
which flags it for follow-up; assignee; complete only when every question is
answered; change history; deleting moves it to the archive. A process's steps
are one of its questions, so removing the last step reopens a completed process.

**How the pieces link.** A measure names the business process that produces it
and the dimensions it breaks down by (the chips also answer its breakdowns
question). When a measure breaks down by a dimension its process doesn't tick,
the bus matrix shows a dashed suggestion and lists it under Checks. A sketch
tile shows one measure from its own dashboard, optionally by one dimension, and
the dimensions from that measure's breakdowns are offered first.

**Process and department dashboards.** A measure is defined once, on one
dashboard row, and other rows can show it: an empty cell's drawer offers
"Already defined on another dashboard?", and typing a name that's already
defined elsewhere offers the existing definition. A department dashboard is
usually a few measures shown from process dashboards plus one or two of its
own. Picking a dashboard on the bus matrix highlights the processes its
measures come from and sorts the dimensions into filters for the whole
dashboard (ticked for every one of those processes) and filters for some
measures only. The prototype panel and a filter tile's settings say the same.
The sample's OPS row shows this.

**Sketches show shapes, never numbers**, so a review stays on layout and
content. Tiles: headline number, trend, comparison, table, filter, note. Drag to
move, drag the corner to resize; with a tile focused, arrow keys move it and
Shift + arrows resize it. A tile's shape is seeded by its id, so it doesn't
change between visits.

**Ownership.** An owner is a department, a person (whose department counts), or
a typed-in name. People get a department under People. Removing a department
leaves its people, steps and owned items without one.

### Exports

| Export | Path |
|---|---|
| Handbook: processes and steps, bus matrix, dimensions, measures, dashboard sketches, ownership | `/w/{slug}/handbook.md` |
| Measure catalogue (now with business process, dimensions and the other dashboards that show each measure) | `/w/{slug}/export.md` |
| Measures CSV (three columns added at the end: `business_process`, `dimensions`, `also_on`) | `/w/{slug}/export.csv` |
| Bus matrix CSV | `/w/{slug}/bus-matrix.csv` |

The handbook gives each dimension's history answer in business terms with the
warehouse term beside it, e.g. "As it was at the time: keep history (type 2)".

## A client workspace's measures

| Feature | Where |
|---|---|
| Grid of dashboards × measure slots, with status, assignee, follow-up flag and an answered-of-12 meter per cell | Home of the workspace |
| Highlight filters: Mine, Needs follow-up, Unassigned, Don't know | Toolbar |
| Edit a dashboard's code or name | Click it in the row header |
| Add a dashboard row | + under the last row |
| Add a measure column (up to 12) | + after the last column header |
| Delete a dashboard row or a measure column (it goes to the archive) | × on the row or column header, shown on hover |
| The twelve questions, saved as you type | Cell drawer |
| Reassign, flag for follow-up with a note, mark complete or reopen | Cell drawer, top panel |
| Change history per measure | Cell drawer, bottom |
| Roster with department, and assigned and completed counts across measures, processes and dimensions | People (on every tab) |
| Restore anything deleted: dashboard rows, measure columns, processes, dimensions | Archive (on every tab) |
| Link a measure to its business process | Cell drawer, top panel |
| Show a measure from another dashboard, or remove it from one | Empty cell's drawer; "On n dashboards" in the drawer |
| Dimensions it breaks down by | Cell drawer, breakdowns question |
| Source-system choices for this client, also offered on processes, steps and dimensions | Source systems |
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
- Every workspace route is scoped by slug. A requirement, person, dashboard,
  process, dimension, step, department or tile from one client can't be read,
  edited, linked or assigned through another client's link (see
  `test_workspaces_are_isolated` and `test_items_are_isolated`).
- A deleted process or dimension is archived with its steps, bus matrix ticks,
  and the measures and tiles that pointed at it; restoring puts the links back
  unless they've been pointed elsewhere since. Archiving a dashboard row takes
  its sketch and "data use" answers with it.
- Databases from before the planning tabs get the new tables and columns on
  start, and an existing sample workspace gets the sample planning content once.
- A cell that shows another dashboard's measure (`ref_id`) holds nothing else:
  it's edited, completed and counted where the measure is defined. Only an
  empty cell can show one, a dashboard shows a measure once, and a measure shown
  elsewhere can't itself become a reference. Deleting the definition empties the
  cells that showed it; restoring it, or the row that showed it, puts them back.
  A sketch tile can show a measure that's on its dashboard either way.

## From requirements to the target platform

A workspace can build a working instance of the target platform
(`bgr_control_tower`, one *pack* per project under `projects/<slug>/`). The
product knows one thing about the target: contract C8, the **requirements
spec**, checked against `semantic/requirements_spec.schema.json` in the target
repo (`docs/requirements_contract.md` there has the field-by-field mapping).
Set `TARGET_REPO` to the target checkout (default: `../bgr_control_tower_v3`).

| What | How |
|---|---|
| The spec | `GET /w/{slug}/requirements_spec.json` (422 if it fails the schema) |
| Compile it into a pack's "what" files | `python -m app.compiler --workspace <slug> --pack $TARGET_REPO/projects/<pack> [--check]` |
| Build a workspace from a pack (the reverse) | `python -m app.target_import --target $TARGET_REPO --rev <commit> --slug <slug> --name "<name>" --pack <pack>` |
| Preview it with numbers | **Build preview** on the Overview tab (see below) |

The compiler writes only what requirements own - a KPI's label, description
and governance meta, the backlog, the cubes, the process and department seeds
and `project.yml`'s name and systems - and leaves an unchanged entry byte for
byte, so an edit lands as a small reviewable diff. A measure with no MetricFlow
definition yet compiles to a `placeholder` in `backlog.yml`. Target bindings
(a measure's metric name, format, direction and backlog reason; process,
dimension and department codes; the cubes; which departments own or
contribute to a measure) are engineer-set and stored beside the answers.

**Build preview** (Overview tab, workspaces bound to a pack) compiles the
workspace into the pack in the target checkout's working tree, then runs the
target's `python -m ctl --project <pack> preview` (rebuild what changed) and
`ctl kpis`, and shows each KPI's latest month in a panel labelled as synthetic
data - never on the measure grid. It takes about a minute; one build runs at a
time per pack. "Open the dashboards in the Studio" opens the target's Studio
(`STUDIO_URL`, default `http://localhost:8801`) on that pack.

**Sample Project** (`/w/demo`) is bound to the target's `sample` pack
(`demo.seed_demo_bindings`). **Sample Project v2** (`/w/sample-v2`) is the target as it was at `269ad97`,
imported with `app.target_import`. Compiling it reproduces the target pack
exactly (`tests/test_target_round_trip.py`).

## Layout

```
app/
  main.py            pages and tabs, workspace creation, the measure API, export routes
  planning.py        the planning API: processes, dimensions, steps, bus matrix, departments, tiles
  items.py           processes and dimensions: question sets, the completeness rule, archive
  scope.py           slug scoping, "You are", people and shared workspace state
  exports.py         CSVs, the measure catalogue and the handbook
  db.py              SQLite schema and migrations, measure cells, the measure archive
  demo.py            the sample workspace, planning content included
  spec.py            the requirements spec (contract C8) and its schema check
  compiler.py        spec -> a target pack's "what" files
  target_import.py   a target pack (at a pinned commit) -> a workspace
  preview.py         Build preview: compile, rebuild in the target, the preview numbers
  templates/         base, index, _workspace (tabs and shared dialogs), one per tab
  static/            site.css (shared), styles.css (grid and drawer), planning.css (tabs and planning pages)
                     common.js (every tab), app.js (measures), planning.js (planning state and the
                     process/dimension drawer), overview.js, processes.js, bus.js, prototypes.js, ownership.js
tests/test_api.py, tests/test_planning.py, tests/test_target_round_trip.py, tests/test_preview.py
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
