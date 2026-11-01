"""SQLite schema, product catalogue and demo data."""
import os
import math
import random
import sqlite3
from datetime import datetime, timedelta
from werkzeug.security import generate_password_hash

import services as S

BASE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("SOLAR_DB", os.path.join(BASE, "instance", "solar.db"))

SCHEMA = """
create table users(id integer primary key, name text, email text unique, password_hash text, role text,
                   hourly_rate real default 0, active integer default 1);
create table products(id integer primary key, sku text unique, name text, category text, unit text default 'pcs',
                      unit_cost real, sale_price real, tracked integer default 0, is_service integer default 0,
                      warranty_months integer default 0, stock_qty integer default 0, reorder_level integer default 0);
create table packages(id integer primary key, name text, ptype text, kw integer);
create table package_lines(id integer primary key, package_id integer, product_id integer, qty real);
create table leads(id integer primary key, name text, phone text, email text, address text, kw integer,
                   system_type text, subsidy integer default 0, source text, status text, assigned_to integer,
                   shadow_area real, monthly_bill real, grid_ok integer, call_notes text, followup_date text,
                   message text, created_at text);
create table surveys(id integer primary key, lead_id integer, technician_id integer, scheduled_date text,
                     status text, roof_direction text, tilt real, cable_distance real, shadow_area real,
                     notes text, done_at text);
create table quotes(id integer primary key, number text, lead_id integer, package_id integer, discount real default 0,
                    subsidy_amount real default 0, status text, created_at text, created_by integer);
create table quote_lines(id integer primary key, quote_id integer, product_id integer, qty real, unit_price real);
create table orders(id integer primary key, number text, quote_id integer, lead_id integer, total real, status text,
                    installer_id integer, install_date text, dispatched_at text, delivered_at text,
                    subsidy_status text, subsidy_amount real default 0, created_at text);
create table order_lines(id integer primary key, order_id integer, product_id integer, qty real, unit_price real, unit_cost real);
create table serials(id integer primary key, serial text unique, product_id integer, status text, order_id integer,
                     purchase_cost real, received_at text, dispatched_at text, delivered_at text,
                     warranty_start text, warranty_end text);
create table invoices(id integer primary key, number text, order_id integer, ticket_id integer, kind text,
                      amount real, date text, status text);
create table payments(id integer primary key, invoice_id integer, order_id integer, amount real, method text,
                      ref text, date text);
create table expenses(id integer primary key, order_id integer, category text, description text, amount real, date text);
create table installs(id integer primary key, order_id integer, technician_id integer, start_time text, end_time text,
                      system_kw real, power_test_kw real, notes text, signer text, signature text, completed_at text);
create table install_photos(id integer primary key, install_id integer, filename text);
create table tickets(id integer primary key, number text, order_id integer, serial_id integer, customer_name text,
                     phone text, issue text, fault_code text, status text, assigned_to integer, in_warranty integer,
                     labour_charge real default 0, charge real default 0, resolution text, created_at text,
                     resolved_at text, closed_at text, created_by integer);
create table ticket_parts(id integer primary key, ticket_id integer, product_id integer, qty integer, unit_price real, new_serial text);
create table activity(id integer primary key, ts text, message text, target text);
create table shares(token text primary key, created_by integer, created_at text, expires_at text);
create index ix_serial_order on serials(order_id);
create index ix_lead_status on leads(status);
"""

ROLES = {
    "admin": "Admin", "sales": "Sales executive", "survey": "Survey technician",
    "warehouse": "Warehouse manager", "installer": "Installation technician",
    "service": "Service manager", "accountant": "Accountant", "director": "Director",
}

DEMO_USERS = [
    ("Asha Admin", "admin@sunpower.test", "admin", 0),
    ("Rohit Sales", "sales@sunpower.test", "sales", 0),
    ("Priya Sales", "sales2@sunpower.test", "sales", 0),
    ("Sanjay Survey", "survey@sunpower.test", "survey", 200),
    ("Meena Survey", "survey2@sunpower.test", "survey", 200),
    ("Wasim Warehouse", "warehouse@sunpower.test", "warehouse", 0),
    ("Imran Installer", "installer@sunpower.test", "installer", 220),
    ("Kavya Installer", "installer2@sunpower.test", "installer", 220),
    ("Naveen Installer", "installer3@sunpower.test", "installer", 240),
    ("Sunita Service", "service@sunpower.test", "service", 0),
    ("Arjun Accounts", "accountant@sunpower.test", "accountant", 0),
    ("Divya Director", "director@sunpower.test", "director", 0),
]
DEMO_PASSWORD = "demo123"


DEMO_ROOF = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 320 200"><rect width="320" height="200" fill="#cfe3f2"/>'
             '<polygon points="20,170 160,60 300,170" fill="#8a5a44"/>'
             + "".join(f'<rect x="{80 + c * 40}" y="{110 + r * 22}" width="34" height="18" fill="#1d4e89" stroke="#f2a900"/>'
                       for r in range(2) for c in range(4)) + '</svg>')


def connect():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


# ------------------------------------------------------------ catalogue
def _r50(x):
    return int(round(x / 50.0) * 50)


def seed_catalogue(db):
    P = lambda *a: db.execute("""insert into products(sku,name,category,unit,unit_cost,sale_price,tracked,is_service,
                                 warranty_months,reorder_level) values(?,?,?,?,?,?,?,?,?,?)""", a)
    P("PAN540", "Mono PERC Solar Panel 540 W", "panel", "pcs", 9000, 11500, 1, 0, 240, 12)
    for kw, cost in zip(range(1, 6), (9000, 15000, 21000, 27500, 34000)):
        P(f"INV{kw}K", f"Solar Inverter {kw} kW", "inverter", "pcs", cost, _r50(cost * 1.25), 1, 0, 24, 2)
    P("BAT128", "Lithium Battery 12.8 V 100 Ah", "battery", "pcs", 24000, 29500, 1, 0, 24, 6)
    P("MPPT60", "MPPT Charge Controller 60 A", "controller", "pcs", 5500, 7200, 1, 0, 24, 3)
    P("DCDB", "DC Distribution Box", "wiring", "pcs", 2200, 3000, 0, 0, 0, 5)
    P("ACDB", "AC Distribution Box", "wiring", "pcs", 2000, 2800, 0, 0, 0, 5)
    P("STR-GI", "Mounting Structure per kW (GI)", "structure", "set", 5500, 7500, 0, 0, 0, 5)
    P("DCCAB", "Solar DC Cable 4 sq mm", "wiring", "m", 55, 75, 0, 0, 0, 100)
    P("ACCAB", "AC Cable 4 core", "wiring", "m", 90, 120, 0, 0, 0, 50)
    P("MC4", "MC4 Connector Pair", "wiring", "pair", 120, 180, 0, 0, 0, 20)
    P("EARTH", "Earthing Kit", "safety", "set", 2500, 3500, 0, 0, 0, 5)
    P("LA", "Lightning Arrestor", "safety", "pcs", 1800, 2600, 0, 0, 0, 5)
    P("MCB", "MCB / Isolator Switch Set", "safety", "set", 1500, 2200, 0, 0, 0, 5)
    P("INSTALL", "Installation & Commissioning (per kW)", "service", "kW", 0, 4000, 0, 1, 0, 0)

    pid = {r["sku"]: r["id"] for r in db.execute("select id, sku from products")}
    for ptype in ("On-Grid", "Off-Grid", "Panel Set"):
        for kw in range(1, 6):
            n = math.ceil(kw * 1000 / 540)
            pk = db.execute("insert into packages(name,ptype,kw) values(?,?,?)", (f"{ptype} {kw} kW", ptype, kw)).lastrowid
            lines = [("PAN540", n), ("MC4", n), ("DCCAB", 10 * kw if ptype == "Panel Set" else 15 * kw)]
            if ptype != "Panel Set":
                lines += [(f"INV{kw}K", 1), ("MPPT60", 1), ("DCDB", 1), ("ACDB", 1), ("STR-GI", kw),
                          ("ACCAB", 10), ("EARTH", 1), ("LA", 1), ("MCB", 1), ("INSTALL", kw)]
            if ptype == "Off-Grid":
                lines.append(("BAT128", kw))
            for sku, q in lines:
                db.execute("insert into package_lines(package_id,product_id,qty) values(?,?,?)", (pk, pid[sku], q))


def seed_users(db):
    h = generate_password_hash(DEMO_PASSWORD)
    for n, e, r, rate in DEMO_USERS:
        db.execute("insert into users(name,email,password_hash,role,hourly_rate) values(?,?,?,?,?)", (n, e, h, r, rate))


# ------------------------------------------------------------ demo data
def _ids(db, role):
    return [r["id"] for r in db.execute("select id from users where role=? order by id", (role,))]


def _stock_up(db):
    S.CLOCK = datetime.now() - timedelta(days=1000)
    plan = {"PAN540": 260, "INV1K": 12, "INV2K": 12, "INV3K": 14, "INV4K": 10, "INV5K": 10,
            "BAT128": 40, "MPPT60": 30}
    for sku, n in plan.items():
        p = db.execute("select * from products where sku=?", (sku,)).fetchone()
        S.receive_stock(db, p["id"], n, p["unit_cost"], "SolarTech Distributors", [])
    for sku, n in {"DCDB": 40, "ACDB": 40, "STR-GI": 120, "DCCAB": 3000, "ACCAB": 800, "MC4": 400,
                   "EARTH": 40, "LA": 40, "MCB": 40}.items():
        p = db.execute("select * from products where sku=?", (sku,)).fetchone()
        S.receive_stock(db, p["id"], n, p["unit_cost"], "Bharat Electricals", [])


def _flow(db, ids, name, phone, kw, stype, stage, age, discount=0, hours=6.0, pay_balance=False,
          subsidy=1, extra_expense=0, address="", sig="data:image/png;base64," + "A" * 600):
    """Run a customer through the real workflow, ending at `stage` `age` days ago."""
    steps = {"confirmed": 3, "dispatched": 4, "delivered": 5, "installed": 6}[stage]
    S.CLOCK = (datetime.now() - timedelta(days=age + steps)).replace(hour=10, minute=0, second=0, microsecond=0)
    adv = lambda: S.CLOCK + timedelta(days=1)
    tick = lambda d=1: setattr(S, "CLOCK", S.CLOCK + timedelta(days=d))
    lid = S.create_lead(db, name, phone, address or "Plot 14, Madhapur, Hyderabad", kw, stype, subsidy,
                        email="", source=random.choice(["Web form", "WhatsApp", "Phone call"]))
    sales = db.execute("select assigned_to a from leads where id=?", (lid,)).fetchone()["a"]
    tick(); S.qualify(db, lid, kw * 9, 2000 + kw * 900, 1, "Roof is free, 3-phase connection available", "Qualified")
    sv = S.schedule_survey(db, lid, random.choice(ids["survey"]), S.today())
    tick(); S.submit_survey(db, sv, "South", 15, 12 + kw * 2, kw * 9, "Good roof, no shade after 9 AM", True)
    tick(0); pk = db.execute("select id from packages where ptype=? and kw=?", (stype, kw)).fetchone()["id"]
    qid = S.create_quote(db, lid, pk, discount, S.subsidy_for(kw) if subsidy else 0, sales)
    total, _ = S.quote_total(db, qid)
    tick(); inst = ids["installer"][db.execute("select count(*) c from orders").fetchone()["c"] % len(ids["installer"])]
    oid = S.confirm_order(db, qid, round(total * 0.5), "UPI", "UTR" + str(random.randint(10**8, 10**9)),
                          inst, (S.now() + timedelta(days=3)).strftime("%Y-%m-%d"), sales)
    if stage == "confirmed":
        return oid
    tick()
    codes = []
    for l in db.execute("""select ol.product_id, ol.qty from order_lines ol join products p on p.id=ol.product_id
                           where ol.order_id=? and p.tracked=1""", (oid,)).fetchall():
        codes += [r["serial"] for r in db.execute(
            "select serial from serials where product_id=? and status='in_stock' order by id limit ?",
            (l["product_id"], int(l["qty"])))]
    S.dispatch(db, oid, " ".join(codes))
    if stage == "dispatched":
        return oid
    tick(); S.deliver(db, oid)
    if stage == "delivered":
        return oid
    tick()
    start = S.now().replace(hour=9, minute=0)
    S.complete_install(db, oid, inst, start.strftime("%Y-%m-%dT%H:%M"),
                       (start + timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M"), kw, kw * 0.92,
                       "Tested OK. Earthing resistance 2 ohm.", name, sig, ["demo_roof.svg"])
    if extra_expense:
        db.execute("insert into expenses(order_id,category,description,amount,date) values(?,?,?,?,?)",
                   (oid, "Transport", "Extra crane and transport", extra_expense, S.today()))
    inv = db.execute("select id from invoices where order_id=? and kind='order'", (oid,)).fetchone()["id"]
    if pay_balance:
        due = total - S.invoice_paid(db, inv)
        S.record_payment(db, inv, due, "Bank transfer", "NEFT")
    return oid


def seed_demo(db):
    random.seed(7)
    ids = {r: _ids(db, r) for r in ("sales", "survey", "installer", "service", "warehouse")}
    _stock_up(db)

    # history: older installed projects for trends and profit/loss
    _flow(db, ids, "Ramesh Kumar", "9848011101", 3, "Off-Grid", "installed", 900, pay_balance=True, subsidy=0,
          address="12-3, Kukatpally, Hyderabad")                                  # inverter warranty expired
    _flow(db, ids, "Lakshmi Textiles", "9848011102", 5, "On-Grid", "installed", 130, pay_balance=True, hours=9)
    _flow(db, ids, "Suresh Reddy", "9848011103", 2, "On-Grid", "installed", 75, pay_balance=True, hours=5)
    _flow(db, ids, "Anita Rao", "9848011104", 3, "On-Grid", "installed", 62, pay_balance=True)
    _flow(db, ids, "Vikram Singh", "9848011105", 4, "Off-Grid", "installed", 48, pay_balance=True, hours=8)
    _flow(db, ids, "Green Valley Foods", "9848011106", 5, "Off-Grid", "installed", 33, discount=85000, hours=12,
          extra_expense=22000, pay_balance=False)                                  # loss-making job
    _flow(db, ids, "Pooja Sharma", "9848011107", 1, "On-Grid", "installed", 20, pay_balance=True, hours=4)
    _flow(db, ids, "Farhan Ali", "9848011108", 3, "On-Grid", "installed", 9, pay_balance=False)
    # in-flight projects
    _flow(db, ids, "Deepak Joshi", "9848011109", 3, "On-Grid", "delivered", 2)
    _flow(db, ids, "Sridevi Nair", "9848011110", 2, "Off-Grid", "dispatched", 1)
    _flow(db, ids, "Karthik Menon", "9848011111", 4, "On-Grid", "confirmed", 0)

    # service tickets
    inv_old = db.execute("""select s.id from serials s join products p on p.id=s.product_id
                            where p.category='inverter' and s.order_id=1""").fetchone()["id"]
    inv_new = db.execute("""select s.id from serials s join products p on p.id=s.product_id
                            where p.category='inverter' and s.order_id=3""").fetchone()["id"]
    S.CLOCK = datetime.now() - timedelta(days=2)
    S.create_ticket(db, inv_old, "Inverter shows fault E04 and there is no power", "E04", ids["service"][0])
    S.CLOCK = datetime.now() - timedelta(hours=5)
    t2 = S.create_ticket(db, inv_new, "Inverter tripped during rain, zero output", "E12", ids["service"][0])
    S.assign_ticket(db, t2, ids["installer"][0])

    # public enquiries still to be worked
    S.CLOCK = datetime.now() - timedelta(hours=3)
    S.create_lead(db, "Geeta Patel", "9000011111", "Flat 302, Gachibowli, Hyderabad", 3, "On-Grid", 1,
                  "geeta@example.com", "Web form", "Looking for 3 kW with subsidy")
    S.CLOCK = datetime.now() - timedelta(hours=1)
    S.create_lead(db, "Mohan Das", "9000022222", "Plot 8, Uppal, Hyderabad", 2, "Off-Grid", 0, "", "WhatsApp",
                  "Frequent power cuts, wants battery backup")
    S.CLOCK = datetime.now() - timedelta(days=1)
    l3 = S.create_lead(db, "Nisha Verma", "9000033333", "H-22, Banjara Hills, Hyderabad", 5, "On-Grid", 0, "", "Phone call")
    S.qualify(db, l3, 55, 8200, 1, "Wants a quote this week", "Qualified")
    S.CLOCK = datetime.now() - timedelta(days=3)
    l4 = S.create_lead(db, "Rajesh Gupta", "9000044444", "Sainikpuri, Hyderabad", 3, "On-Grid", 1, "", "Web form")
    S.qualify(db, l4, 30, 3900, 1, "Roof facing east, asks about shading", "Qualified")
    S.schedule_survey(db, l4, ids["survey"][0], (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d"))
    S.CLOCK = datetime.now() - timedelta(days=4)
    l5 = S.create_lead(db, "Sunil Chopra", "9000055555", "Miyapur, Hyderabad", 4, "Off-Grid", 1, "", "WhatsApp")
    S.qualify(db, l5, 40, 5200, 1, "Ready to proceed", "Qualified")
    sv = S.schedule_survey(db, l5, ids["survey"][1], S.today())
    S.CLOCK = datetime.now() - timedelta(days=2)
    S.submit_survey(db, sv, "South-West", 12, 18, 38, "Approved. Water tank shadow in the evening only", True)
    S.CLOCK = None


def init_db(reset=False, demo=True):
    if reset and os.path.exists(DB_PATH):
        os.remove(DB_PATH)
    if os.path.exists(DB_PATH):
        return False
    db = connect()
    db.executescript(SCHEMA)
    seed_users(db)
    seed_catalogue(db)
    if demo:
        seed_demo(db)
    db.commit()
    db.close()
    os.makedirs(os.path.join(BASE, "instance", "uploads"), exist_ok=True)
    with open(os.path.join(BASE, "instance", "uploads", "demo_roof.svg"), "w") as f:
        f.write(DEMO_ROOF)
    return True
