"""The sample workspace prospects see at /w/demo.

A fictional distributor, partly filled in, so the grid shows every state a real
engagement goes through: complete, in progress, flagged, unassigned and empty.
The measure content is adapted from a real proof of concept; the company and
the people are not real. Delete the row in `clients` with slug 'demo' and
restart to reseed it.

The planning content (departments, processes, dimensions, the bus matrix and
one dashboard sketch) is seeded by seed_demo_planning, and a department
dashboard that shares measures with the process dashboards by
seed_demo_department. Both also fill in a sample created before they existed.
"""
from __future__ import annotations

import json

from . import db, items

DASHBOARDS = [
    ("O2C", "Order to Cash"),
    ("P2P", "Procure to Pay"),
    ("I2D", "Inventory to Delivery"),
    ("R2R", "Record to Report"),
    ("H2R", "Hire to Retire"),
]
SOURCES = ["ERP", "CRM", "Payroll", "Inventory planning", "Freight platform"]

# Roles rather than names: this is a sample company.
PEOPLE = [
    ("Logistics Lead", "Operations"),
    ("Finance Controller", "Finance"),
    ("Sales Operations Lead", "Sales"),
    ("Purchasing Manager", "Procurement"),
]

# (dashboard code, slot, fields, status, assignee index, owner index, subject matter expert index)
MEASURES = [
    ("I2D", 1, {
        "name": "On-Time Delivery",
        "description": "The share of delivered shipments that arrived by the date we promised.",
        "sources": ["Freight platform", "ERP"],
        "formula": "Shipments delivered on or before the promised date, divided by all delivered shipments. "
                   "Measured against the customer's requested date, not the carrier's estimate.",
        "target": "95% on time, set by the Logistics Lead each year. Kept in the scorecard header.",
        "report": "Yes, reviewed every Monday. Trusted: weekly logistics scorecard (Excel, Operations shared drive).",
        "breakdowns": "By customer, carrier, ship-from site and week. Customers roll up to account tier. "
                      "Lowest level: individual shipment.",
        "time_basis": "Delivery date. Fiscal weeks and months. Week to date, MTD and prior year.",
        "refresh": "daily", "refresh_why": "Late shipments are chased the next morning.",
        "restatement": "Yes. Carriers confirm deliveries up to five days late, so the last two weeks are recalculated daily.",
    }, "complete", 0, 0, 0),
    ("I2D", 2, {
        "name": "Cost Per Shipment",
        "description": "The average freight cost of moving one shipment.",
        "sources": ["Freight platform"],
        "formula": "Total carrier charges (freight, fuel surcharge, accessorials) divided by shipments.",
        "target_none": True,
        "report_none": True,
        "breakdowns_same": False,
        "breakdowns": "By carrier, lane and month. Lowest level: individual shipment.",
        "time_basis": "Ship date. Calendar months. Month and prior month.",
        "refresh": "weekly",
        "restatement": "Yes. Carrier invoices can arrive a month late; the month is restated when they do.",
    }, "complete", 0, 0, 0),
    ("I2D", 3, {
        "name": "Inventory Accuracy",
        "description": "How closely counted stock matches what the system says we have.",
        "sources": ["ERP", "Inventory planning"],
        "unknown": {"formula": "Finance Controller"},
        "notes": "Not sure whether this is by item or by location. Cycle counts happen monthly.",
    }, "in_progress", 0, None, 0),
    ("O2C", 1, {
        "name": "Days Sales Outstanding",
        "description": "How long, on average, customers take to pay us.",
        "sources": ["ERP"],
        "formula": "Accounts receivable divided by credit sales, times days in the period.",
        "target": "Under 45 days, set by the CFO in the annual budget.",
        "report": "Yes, in the monthly finance review. Trusted: month-end close pack, AR tab.",
        "breakdowns_same": True,
        "time_basis": "Month-end balance date. Fiscal months. Month, YTD and prior year.",
        "refresh": "monthly",
        "restatement_none": True,
    }, "complete", 1, 1, 1),
    ("O2C", 2, {
        "name": "Cost Per Order",
        "description": "What it costs us to fulfil one customer order.",
        "sources": ["Freight platform", "ERP"],
        "formula": "Fulfilment cost divided by orders shipped.",
    }, "in_progress", 2, None, None),
    ("O2C", 3, {
        "name": "Return Rate",
        "description": "The share of sales that come back as returns.",
        "unknown": {"sources": "Sales Operations Lead"},
    }, "in_progress", None, None, None),
    ("P2P", 1, {
        "name": "Three-Way Match Rate",
        "description": "The share of supplier invoices that match the purchase order and the goods receipt.",
        "sources": ["ERP"],
        "formula": "Invoices matching PO and receipt within tolerance, divided by all invoices.",
        "report": "AP exceptions report.",
        "breakdowns": "By supplier and buyer, monthly.",
        "refresh": "weekly",
    }, "in_progress", 3, 3, 3),
]

FOLLOWUP = {
    ("O2C", 2): "Which costs count? Freight only, or warehouse labour too. Ask the Finance Controller.",
}


def seed_demo(conn) -> None:
    client_id = db.create_client(conn, "Sample Project", DASHBOARDS,
                                 sources=SOURCES, slug="demo", is_demo=True)
    people = []
    for name, role in PEOPLE:
        people.append(conn.execute(
            "INSERT INTO people (client_id, name, role, created_at) VALUES (?, ?, ?, ?)",
            (client_id, name, role, db.now()),
        ).lastrowid)
    for code, slot, fields, status, assignee, owner, sme in MEASURES:
        rid = conn.execute(
            """SELECT r.id FROM requirements r JOIN dashboards d ON d.id = r.dashboard_id
               WHERE d.client_id = ? AND d.code = ? AND r.slot = ?""",
            (client_id, code, slot),
        ).fetchone()[0]
        row = {**fields, "status": status, "updated_at": db.now(),
               "assignee_id": people[assignee] if assignee is not None else None,
               "owner_id": people[owner] if owner is not None else None,
               "sme_id": people[sme] if sme is not None else None}
        if status == "complete":
            row["completed_at"] = db.now()
        note = FOLLOWUP.get((code, slot))
        if note:
            row.update(followup=1, followup_note=note)
        if row.get("unknown"):
            row["followup"] = 1  # as ticking "I don't know" does in the app
        for key in ("sources", "unknown"):
            if key in row:
                row[key] = json.dumps(row[key])
        cols = ", ".join(f"{k} = ?" for k in row)
        conn.execute(f"UPDATE requirements SET {cols} WHERE id = ?", (*row.values(), rid))
    seed_demo_planning(conn, client_id)
    seed_demo_department(conn, client_id)


# ---------------------------------------------------------------------------
# Planning content
# ---------------------------------------------------------------------------

DEPARTMENTS = ["Sales", "Customer Service", "Operations", "Finance", "Procurement"]
# Each sample person's department, by the role they're named for.
PEOPLE_DEPARTMENTS = {"Logistics Lead": "Operations", "Finance Controller": "Finance",
                      "Sales Operations Lead": "Sales", "Purchasing Manager": "Procurement"}

# (name, answers, steps as (name, department, system), owner as ("person"|"dept", name), unknown, status)
PROCESSES = [
    ("Order to Cash", {
        "purpose": "Turns customer orders into cash. Every sale and every receivable starts here.",
        "boundaries": "Starts when a customer places an order. Ends when their payment is applied to the invoice.",
        "event": "One line on a customer order, and later one line on its invoice.",
        "systems": ["CRM", "ERP"],
        "volume": "About 1,800 order lines a day. Month-end is twice as busy.",
        "decisions": "Which customers to chase for payment. Whether to extend credit. Which late orders to expedite.",
        "pain_points": "Order changes are agreed by email, so the ERP is often a day behind.",
        "priority": "now",
    }, [("Take the order", "Sales", "CRM"), ("Check credit", "Finance", "ERP"),
        ("Pick, pack and ship", "Operations", "ERP"), ("Invoice", "Finance", "ERP"),
        ("Collect payment", "Finance", "ERP")], ("person", "Finance Controller"), {}, "complete"),
    ("Procure to Pay", {
        "purpose": "Buys stock and services and pays suppliers on time.",
        "boundaries": "Starts with a purchase requisition. Ends when the supplier is paid.",
        "event": "One line on a purchase order.",
        "systems": ["ERP"],
        "decisions": "Which suppliers to keep. When to pay to take early-payment discounts.",
        "pain_points": "Invoices that don't match the order are fixed by hand in AP.",
        "priority": "next",
    }, [("Raise purchase order", "Procurement", "ERP"), ("Receive goods", "Operations", "ERP"),
        ("Match invoice", "Finance", "ERP"), ("Pay supplier", "Finance", "ERP")],
     ("person", "Purchasing Manager"), {"volume": "Purchasing Manager"}, "in_progress"),
    ("Inventory to Delivery", {
        "purpose": "Keeps the right stock in each warehouse and gets orders to customers when promised.",
        "boundaries": "Starts when stock is planned for a site. Ends when the customer confirms delivery.",
        "event": "One shipment, and each delivery confirmation from the carrier.",
        "systems": ["Inventory planning", "ERP", "Freight platform"],
        "volume": "About 600 shipments a day from three sites.",
        "decisions": "Which carriers to review. Where to hold safety stock. Which customers need a call about a late order.",
        "pain_points_none": True,
        "priority": "now",
    }, [("Plan stock", "Operations", "Inventory planning"), ("Pick and pack", "Operations", "ERP"),
        ("Ship", "Operations", "Freight platform"), ("Confirm delivery", "Customer Service", "Freight platform")],
     ("person", "Logistics Lead"), {}, "complete"),
    ("Record to Report", {
        "purpose": "Closes the books each month and produces the financial statements.",
        "systems": ["ERP"],
    }, [], ("dept", "Finance"), {}, "in_progress"),
    ("Hire to Retire", {}, [], None, {}, "not_started"),
]

# (name, answers, owner, unknown, status)
DIMENSIONS = [
    ("Date", {
        "definition": "The calendar and our fiscal calendar: days, fiscal weeks, months, quarters and years.",
        "identifier": "The date itself.",
        "attributes": "Fiscal week, fiscal month, quarter, working day, holiday.",
        "hierarchy": "Day → fiscal week → fiscal month → quarter → fiscal year.",
        "systems": ["ERP"],
        "history": "as_is",
        "quality_none": True,
    }, ("dept", "Finance"), {}, "complete"),
    ("Customer", {
        "definition": "Any company that has placed an order with us in the last five years.",
        "identifier": "Customer number in the ERP. The CRM uses its own account ID, matched by hand.",
        "attributes": "Name, segment, account manager, payment terms, account tier, region.",
        "hierarchy": "Customer → account tier → region.",
        "systems": ["ERP"],
        "history": "as_was",
        "history_note": "Region and account manager. Name changes can overwrite.",
        "quality": "Some customers exist twice under slightly different names.",
    }, ("person", "Sales Operations Lead"), {}, "complete"),
    ("Product", {
        "definition": "Anything we sell or stock, down to the individual SKU.",
        "identifier": "Item number in the ERP.",
        "attributes": "Description, product family, brand, unit of measure, weight.",
        "systems": ["ERP"],
        "history": "as_was",
    }, ("dept", "Operations"), {"hierarchy": "Purchasing Manager"}, "in_progress"),
    ("Site", {
        "definition": "A warehouse or branch that holds stock or ships orders.",
        "identifier": "Site code, e.g. DAL1.",
        "hierarchy": "Site → region.",
        "systems": ["ERP"],
    }, ("person", "Logistics Lead"), {}, "in_progress"),
    ("Supplier", {"definition": "A company we buy goods or services from."}, None, {}, "in_progress"),
    ("Carrier", {
        "definition": "A freight company that moves our shipments.",
        "systems": ["Freight platform"],
    }, ("person", "Logistics Lead"), {}, "in_progress"),
    ("Employee", {}, None, {}, "not_started"),
]

BUS = {
    "Order to Cash": ["Date", "Customer", "Product", "Site"],
    "Procure to Pay": ["Date", "Product", "Site", "Supplier"],
    "Inventory to Delivery": ["Date", "Customer", "Product", "Site", "Carrier"],
    "Record to Report": ["Date"],
}

# Measure name: (process, dimensions). Cost Per Order and Three-Way Match Rate
# break down by dimensions their process doesn't tick yet, so the bus matrix
# shows a suggestion for each.
MEASURE_LINKS = {
    "On-Time Delivery": ("Inventory to Delivery", ["Date", "Customer", "Carrier", "Site"]),
    "Cost Per Shipment": ("Inventory to Delivery", ["Date", "Carrier"]),
    "Inventory Accuracy": ("Inventory to Delivery", ["Product", "Site"]),
    "Days Sales Outstanding": ("Order to Cash", ["Date", "Customer"]),
    "Cost Per Order": ("Order to Cash", ["Customer", "Carrier"]),
    "Return Rate": ("Order to Cash", []),
    "Three-Way Match Rate": ("Procure to Pay", ["Supplier", "Employee", "Date"]),
}

SKETCH_DASHBOARD = "I2D"
SKETCH_USE = {
    "audience": "The logistics team and the operations director.",
    "decisions": "Which carriers to review, and which customers need a call about a late order.",
    "cadence": "The Monday operations meeting, and daily for late shipments.",
    "access": "Carrier rates are confidential: customer service shouldn't see the cost tiles.",
}
# (kind, x, y, w, h, measure, dimension, title, note)
SKETCH = [
    ("filter", 0, 0, 3, 1, None, "Date", "", ""),
    ("filter", 3, 0, 3, 1, None, "Customer", "", ""),
    ("filter", 6, 0, 3, 1, None, "Carrier", "", ""),
    ("kpi", 0, 1, 3, 2, "On-Time Delivery", None, "", ""),
    ("kpi", 3, 1, 3, 2, "Cost Per Shipment", None, "", ""),
    ("kpi", 6, 1, 3, 2, "Inventory Accuracy", None, "", ""),
    ("text", 9, 0, 3, 3, None, None, "Late shipments",
     "Each late shipment links to its record in the freight platform, so it can be chased from here."),
    ("line", 0, 3, 6, 3, "On-Time Delivery", None, "On-time delivery by week", ""),
    ("bar", 6, 3, 6, 3, "On-Time Delivery", "Carrier", "", ""),
    ("table", 0, 6, 12, 3, "On-Time Delivery", "Customer", "Late shipments by customer", ""),
]


def seed_demo_planning(conn, client_id: int) -> None:
    if any(conn.execute(f"SELECT 1 FROM {t} WHERE client_id = ?", (client_id,)).fetchone()
           for t in ("departments", "processes", "dimensions")):
        return
    depts = {name: conn.execute("INSERT INTO departments (client_id, name) VALUES (?, ?)", (client_id, name)).lastrowid
             for name in DEPARTMENTS}
    people = {r["name"]: r["id"] for r in conn.execute("SELECT id, name FROM people WHERE client_id = ?", (client_id,))}
    for person, dept in PEOPLE_DEPARTMENTS.items():
        conn.execute("UPDATE people SET department_id = ? WHERE id = ?", (depts[dept], people.get(person)))

    def owner_cols(owner):
        if owner is None:
            return {}
        kind, name = owner
        return {"owner_id": people.get(name)} if kind == "person" else {"owner_dept_id": depts[name]}

    def seed_item(kind, name, answers, owner, unknown, status, assignee=None):
        iid = items.create_item(conn, client_id, kind, name, answers)
        row = {**owner_cols(owner), "unknown": json.dumps(unknown), "status": status,
               "followup": int(bool(unknown)), "updated_at": db.now(), "assignee_id": assignee,
               "completed_at": db.now() if status == "complete" else None}
        conn.execute(f"UPDATE {items.KINDS[kind]} SET {', '.join(f'{k} = ?' for k in row)} WHERE id = ?",
                     (*row.values(), iid))
        return iid

    procs = {}
    for name, answers, steps, owner, unknown, status in PROCESSES:
        owner_person = people.get(owner[1]) if owner and owner[0] == "person" else None
        procs[name] = pid = seed_item("process", name, answers, owner, unknown, status, owner_person)
        for pos, (step, dept, system) in enumerate(steps):
            conn.execute("INSERT INTO process_steps (process_id, position, name, dept_id, system) VALUES (?, ?, ?, ?, ?)",
                         (pid, pos, step, depts[dept], system))
    dims = {name: seed_item("dimension", name, answers, owner, unknown, status)
            for name, answers, owner, unknown, status in DIMENSIONS}
    for proc, names in BUS.items():
        for d in names:
            conn.execute("INSERT INTO bus_cells (process_id, dimension_id) VALUES (?, ?)", (procs[proc], dims[d]))

    measures = {r["name"]: r["id"] for r in conn.execute(
        """SELECT r.id, r.name FROM requirements r JOIN dashboards d ON d.id = r.dashboard_id
           WHERE d.client_id = ? AND r.name <> ''""", (client_id,))}
    for measure, (proc, dim_names) in MEASURE_LINKS.items():
        if measure in measures:
            conn.execute("UPDATE requirements SET process_id = ?, dimension_ids = ? WHERE id = ?",
                         (procs[proc], json.dumps([dims[d] for d in dim_names]), measures[measure]))

    dash = conn.execute("SELECT id FROM dashboards WHERE client_id = ? AND code = ?", (client_id, SKETCH_DASHBOARD)).fetchone()
    if dash:
        conn.execute(f"UPDATE dashboards SET {', '.join(f'{k} = ?' for k in SKETCH_USE)} WHERE id = ?",
                     (*SKETCH_USE.values(), dash["id"]))
        for kind, x, y, w, h, measure, dim, title, note in SKETCH:
            conn.execute(
                """INSERT INTO tiles (dashboard_id, kind, x, y, w, h, title, requirement_id, dimension_id, note)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (dash["id"], kind, x, y, w, h, title, measures.get(measure), dims.get(dim), note))


# ---------------------------------------------------------------------------
# A department dashboard
# ---------------------------------------------------------------------------

# Operations reads three processes. Two of its measures are defined on the
# process dashboards and shown here; Supplier On-Time Rate is its own.
DEPARTMENT = ("OPS", "Operations")
DEPARTMENT_SHOWS = ["On-Time Delivery", "Cost Per Order"]
DEPARTMENT_MEASURE = {
    "name": "Supplier On-Time Rate",
    "description": "The share of purchase order lines that arrive by the date the supplier promised.",
    "sources": ["ERP"],
    "formula": "PO lines received on or before the promised date, divided by all PO lines received.",
    "breakdowns": "By supplier, site and month.",
}
DEPARTMENT_LINK = ("Procure to Pay", ["Supplier", "Site", "Date"])
DEPARTMENT_USE = {
    "audience": "The operations director and the site managers.",
    "decisions": "Where to add staff or stock. Which carriers and suppliers to review.",
    "cadence": "The monthly operations review.",
}


def seed_demo_department(conn, client_id: int) -> None:
    code, name = DEPARTMENT
    mine = "SELECT r.* FROM requirements r JOIN dashboards d ON d.id = r.dashboard_id WHERE d.client_id = ?"
    if (conn.execute("SELECT 1 FROM dashboards WHERE client_id = ? AND code = ?", (client_id, code)).fetchone()
            or conn.execute("SELECT 1 FROM archive WHERE client_id = ? AND kind = 'dashboard' AND label LIKE ?",
                            (client_id, code + "%")).fetchone()
            or conn.execute(f"{mine} AND r.ref_id IS NOT NULL", (client_id,)).fetchone()):
        return
    measures = {r["name"]: r["id"] for r in conn.execute(f"{mine} AND r.name <> '' AND r.ref_id IS NULL", (client_id,))}
    slots = conn.execute("SELECT slots FROM clients WHERE id = ?", (client_id,)).fetchone()[0]
    did = db.add_dashboard(conn, client_id, code, name, slots)
    conn.execute(f"UPDATE dashboards SET {', '.join(f'{k} = ?' for k in DEPARTMENT_USE)} WHERE id = ?",
                 (*DEPARTMENT_USE.values(), did))
    cells = iter(r[0] for r in conn.execute("SELECT id FROM requirements WHERE dashboard_id = ? ORDER BY slot", (did,)))
    for shown in DEPARTMENT_SHOWS:
        if shown in measures:
            conn.execute("UPDATE requirements SET ref_id = ? WHERE id = ?", (measures[shown], next(cells)))
    rid = next(cells, None)
    if rid is None:
        return
    proc, dim_names = DEPARTMENT_LINK
    pid = conn.execute("SELECT id FROM processes WHERE client_id = ? AND name = ?", (client_id, proc)).fetchone()
    dims = {r["name"]: r["id"] for r in conn.execute("SELECT id, name FROM dimensions WHERE client_id = ?", (client_id,))}
    lead = conn.execute("SELECT id FROM people WHERE client_id = ? AND name = 'Logistics Lead'", (client_id,)).fetchone()
    row = {**DEPARTMENT_MEASURE, "sources": json.dumps(DEPARTMENT_MEASURE["sources"]), "status": "in_progress",
           "updated_at": db.now(), "assignee_id": lead[0] if lead else None, "process_id": pid[0] if pid else None,
           "dimension_ids": json.dumps([dims[d] for d in dim_names if d in dims])}
    conn.execute(f"UPDATE requirements SET {', '.join(f'{k} = ?' for k in row)} WHERE id = ?", (*row.values(), rid))
