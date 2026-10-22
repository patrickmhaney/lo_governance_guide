"""The sample workspace prospects see at /w/demo.

A fictional distributor, partly filled in, so the grid shows every state a real
engagement goes through: complete, in progress, flagged, unassigned and empty.
The measure content is adapted from a real proof of concept; the company and
the people are not real. Delete the row in `clients` with slug 'demo' and
restart to reseed it.
"""
from __future__ import annotations

import json

from . import db

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
