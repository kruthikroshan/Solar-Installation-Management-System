"""Business rules for the Solar Installation Management System.

Every function takes an open sqlite connection as `db`, raises ValueError with a
human-readable message when a rule is broken, and never commits (the caller does).
"""
import re
import calendar
from datetime import datetime, date, timedelta

CLOCK = None  # tests / demo seeding can pin "now"

LEAD_OPEN = ("New", "Contacted", "Qualified", "Survey Scheduled", "Survey Approved", "Quoted")
LEAD_STATUSES = ["New", "Contacted", "Qualified", "Survey Scheduled", "Survey Approved",
                 "Quoted", "Won", "Lost", "Not Suitable", "Survey Rejected"]
SUBSIDY_STATUSES = ["Not applicable", "To apply", "Applied", "Approved", "Received", "Rejected"]
ORDER_STAGES = ["confirmed", "dispatched", "delivered", "installed"]
TICKET_STATUSES = ["New", "Assigned", "In Progress", "Resolved", "Closed"]
LABOUR_DEFAULT = 500
SERIAL_PREFIX = {"panel": "PNL", "inverter": "INV", "battery": "BAT", "controller": "MPPT"}


# ---------------------------------------------------------------- helpers
def now():
    return CLOCK or datetime.now()


def ts():
    return now().strftime("%Y-%m-%d %H:%M:%S")


def today():
    return now().strftime("%Y-%m-%d")


def add_months(d, m):
    y = d.year + (d.month - 1 + m) // 12
    mo = (d.month - 1 + m) % 12 + 1
    return date(y, mo, min(d.day, calendar.monthrange(y, mo)[1]))


def one(db, sql, *a):
    return db.execute(sql, a).fetchone()


def val(db, sql, *a):
    r = db.execute(sql, a).fetchone()
    return r[0] if r and r[0] is not None else 0


def log(db, msg, to=()):
    target = "," + ",".join(list(to) + ["admin"]) + ","
    db.execute("insert into activity(ts,message,target) values(?,?,?)", (ts(), msg, target))


def next_no(db, prefix, table):
    return f"{prefix}-{val(db, f'select max(id) from {table}') + 1:04d}"


def subsidy_for(kw):
    """PM Surya Ghar style central subsidy (indicative)."""
    return {1: 30000, 2: 60000}.get(int(kw), 78000)


def package_price(db, pid):
    return val(db, "select sum(pl.qty*p.sale_price) from package_lines pl join products p on p.id=pl.product_id where pl.package_id=?", pid)


# ---------------------------------------------------------------- leads
def create_lead(db, name, phone, address, kw, system_type, subsidy=0, email="",
                source="Web form", notes=""):
    name = (name or "").strip()
    phone = re.sub(r"[^\d+]", "", phone or "")
    if not name or len(re.sub(r"\D", "", phone)) < 10:
        raise ValueError("Enter the customer's name and a valid 10-digit phone number.")
    try:
        kw = int(kw)
    except (TypeError, ValueError):
        raise ValueError("Choose a capacity between 1 and 5 kW.")
    if not 1 <= kw <= 5:
        raise ValueError("Choose a capacity between 1 and 5 kW.")
    if system_type not in ("On-Grid", "Off-Grid"):
        raise ValueError("Choose On-Grid or Off-Grid.")
    owner = one(db, """select u.id from users u where u.role='sales' and u.active=1
        order by (select count(*) from leads l where l.assigned_to=u.id
                  and l.status in ('New','Contacted','Qualified')), u.id limit 1""")
    lid = db.execute(
        """insert into leads(name,phone,email,address,kw,system_type,subsidy,source,status,assigned_to,message,created_at)
           values(?,?,?,?,?,?,?,?,'New',?,?,?)""",
        (name, phone, email, address, kw, system_type, 1 if subsidy else 0, source,
         owner["id"] if owner else None, notes, ts())).lastrowid
    log(db, f"New enquiry from {name}: {kw} kW {system_type} ({source})",
        ["sales"] + ([f"u:{owner['id']}"] if owner else []))
    return lid


def qualify(db, lead_id, shadow_area, monthly_bill, grid_ok, notes, outcome, followup=None):
    lead = one(db, "select * from leads where id=?", lead_id)
    if not lead:
        raise ValueError("Lead not found.")
    if outcome not in ("Contacted", "Qualified", "Not Suitable"):
        raise ValueError("Unknown call outcome.")
    if outcome == "Qualified" and not (shadow_area and monthly_bill):
        raise ValueError("Record the shadow-free roof area and monthly bill before qualifying.")
    db.execute("""update leads set status=?, shadow_area=?, monthly_bill=?, grid_ok=?, call_notes=?, followup_date=?
                  where id=?""", (outcome, shadow_area or None, monthly_bill or None,
                                  1 if grid_ok else 0, notes, followup or None, lead_id))
    log(db, f"{lead['name']}: call result '{outcome}'", ["sales"])


def schedule_survey(db, lead_id, tech_id, when):
    lead = one(db, "select * from leads where id=?", lead_id)
    if lead["status"] not in ("Qualified", "Survey Scheduled", "Survey Rejected"):
        raise ValueError("Qualify the enquiry by phone before booking a site survey.")
    if not tech_id or not when:
        raise ValueError("Choose a survey technician and a date.")
    db.execute("update surveys set status='Cancelled' where lead_id=? and status='Scheduled'", (lead_id,))
    sid = db.execute("insert into surveys(lead_id,technician_id,scheduled_date,status) values(?,?,?,'Scheduled')",
                     (lead_id, tech_id, when)).lastrowid
    db.execute("update leads set status='Survey Scheduled' where id=?", (lead_id,))
    log(db, f"Site survey booked for {lead['name']} on {when}", [f"u:{tech_id}", "sales"])
    return sid


def submit_survey(db, survey_id, direction, tilt, cable, shadow, notes, approve):
    sv = one(db, "select * from surveys where id=?", survey_id)
    if not sv or sv["status"] != "Scheduled":
        raise ValueError("This survey is not open.")
    try:
        tilt, cable, shadow = float(tilt), float(cable), float(shadow)
    except (TypeError, ValueError):
        raise ValueError("Roof tilt, cable distance and shadow-free area must be numbers.")
    if not direction:
        raise ValueError("Select the roof direction.")
    st = "Approved" if approve else "Rejected"
    db.execute("""update surveys set status=?, roof_direction=?, tilt=?, cable_distance=?, shadow_area=?,
                  notes=?, done_at=? where id=?""", (st, direction, tilt, cable, shadow, notes, ts(), survey_id))
    lead = one(db, "select * from leads where id=?", sv["lead_id"])
    db.execute("update leads set status=? where id=?", ("Survey Approved" if approve else "Survey Rejected", lead["id"]))
    log(db, f"Survey {st.lower()} for {lead['name']} (roof {direction}, tilt {tilt:g} deg, cable {cable:g} m)",
        ["sales"] + ([f"u:{lead['assigned_to']}"] if lead["assigned_to"] else []))


# ---------------------------------------------------------------- quotes & orders
def quote_total(db, qid):
    sub = val(db, "select sum(qty*unit_price) from quote_lines where quote_id=?", qid)
    return sub - val(db, "select discount from quotes where id=?", qid), sub


def create_quote(db, lead_id, package_id, discount, subsidy_amount, uid):
    lead = one(db, "select * from leads where id=?", lead_id)
    if lead["status"] not in ("Survey Approved", "Quoted"):
        raise ValueError("A quotation can only be prepared after the site survey is approved.")
    pk = one(db, "select * from packages where id=?", package_id)
    if not pk:
        raise ValueError("Choose a package template.")
    discount = max(0.0, float(discount or 0))
    price = package_price(db, package_id)
    if discount > price:
        raise ValueError("Discount cannot exceed the package price.")
    qid = db.execute("""insert into quotes(number,lead_id,package_id,discount,subsidy_amount,status,created_at,created_by)
                        values(?,?,?,?,?,'Draft',?,?)""",
                     (next_no(db, "QT", "quotes"), lead_id, package_id, discount,
                      float(subsidy_amount or 0), ts(), uid)).lastrowid
    db.execute("""insert into quote_lines(quote_id,product_id,qty,unit_price)
                  select ?, pl.product_id, pl.qty, p.sale_price from package_lines pl
                  join products p on p.id=pl.product_id where pl.package_id=?""", (qid, package_id))
    db.execute("update leads set status='Quoted' where id=?", (lead_id,))
    log(db, f"Quotation prepared for {lead['name']} ({pk['name']})", ["sales"])
    return qid


def confirm_order(db, qid, advance, method, ref, installer_id, install_date, uid):
    qt = one(db, "select * from quotes where id=?", qid)
    if not qt or qt["status"] == "Accepted":
        raise ValueError("This quotation is already confirmed.")
    total, _ = quote_total(db, qid)
    advance = float(advance or 0)
    if advance <= 0:
        raise ValueError("Record the advance (or full) payment before confirming the order.")
    if advance > total:
        raise ValueError("Advance cannot be more than the order total.")
    if not installer_id or not install_date:
        raise ValueError("Choose an installation technician and a date.")
    lead = one(db, "select * from leads where id=?", qt["lead_id"])
    oid = db.execute("""insert into orders(number,quote_id,lead_id,total,status,installer_id,install_date,
                        subsidy_status,subsidy_amount,created_at) values(?,?,?,?,'confirmed',?,?,?,?,?)""",
                     (next_no(db, "SO", "orders"), qid, lead["id"], total, installer_id, install_date,
                      "To apply" if lead["subsidy"] else "Not applicable",
                      qt["subsidy_amount"] if lead["subsidy"] else 0, ts())).lastrowid
    db.execute("""insert into order_lines(order_id,product_id,qty,unit_price,unit_cost)
                  select ?, ql.product_id, ql.qty, ql.unit_price, p.unit_cost from quote_lines ql
                  join products p on p.id=ql.product_id where ql.quote_id=?""", (oid, qid))
    iid = db.execute("insert into invoices(number,order_id,kind,amount,date,status) values(?,?,?,?,?,'Unpaid')",
                     (next_no(db, "INV", "invoices"), oid, "order", total, today())).lastrowid
    record_payment(db, iid, advance, method or "UPI", ref or "")
    db.execute("update quotes set status='Accepted' where id=?", (qid,))
    db.execute("update leads set status='Won' where id=?", (lead["id"],))
    num = one(db, "select number from orders where id=?", oid)["number"]
    log(db, f"Order {num} confirmed for {lead['name']}: pack the kit, install on {install_date}",
        ["warehouse", f"u:{installer_id}", "accountant"])
    return oid


def invoice_paid(db, iid):
    return val(db, "select sum(amount) from payments where invoice_id=?", iid)


def record_payment(db, invoice_id, amount, method, ref):
    inv = one(db, "select * from invoices where id=?", invoice_id)
    amount = float(amount or 0)
    due = inv["amount"] - invoice_paid(db, invoice_id)
    if amount <= 0 or amount > due + 0.01:
        raise ValueError(f"Enter an amount between 1 and the balance due ({due:,.0f}).")
    db.execute("insert into payments(invoice_id,order_id,amount,method,ref,date) values(?,?,?,?,?,?)",
               (invoice_id, inv["order_id"], amount, method, ref, today()))
    left = due - amount
    db.execute("update invoices set status=? where id=?", ("Paid" if left < 0.5 else "Partial", invoice_id))
    log(db, f"Payment of {amount:,.0f} received on {inv['number']}", ["accountant"])


# ---------------------------------------------------------------- stock
def receive_stock(db, product_id, qty, unit_cost, supplier, codes):
    p = one(db, "select * from products where id=?", product_id)
    if not p or p["is_service"]:
        raise ValueError("Choose a stock item.")
    unit_cost = float(unit_cost or p["unit_cost"])
    if p["tracked"]:
        codes = [c.upper() for c in codes if c]
        if not codes:
            n = int(qty or 0)
            if n <= 0:
                raise ValueError("Enter a quantity or paste the serial numbers.")
            prefix = SERIAL_PREFIX.get(p["category"], "SN")
            k = val(db, "select count(*) from serials where serial like ?", prefix + "-%")
            codes = []
            while len(codes) < n:
                k += 1
                c = f"{prefix}-{k:05d}"
                if not one(db, "select 1 from serials where serial=?", c):
                    codes.append(c)
        if len(codes) != len(set(codes)):
            raise ValueError("The list contains duplicate serial numbers.")
        for c in codes:
            if one(db, "select 1 from serials where serial=?", c):
                raise ValueError(f"Serial {c} is already registered.")
        for c in codes:
            db.execute("insert into serials(serial,product_id,status,purchase_cost,received_at) values(?,?,'in_stock',?,?)",
                       (c, product_id, unit_cost, today()))
        n = len(codes)
    else:
        n = int(qty or 0)
        if n <= 0:
            raise ValueError("Enter a quantity greater than zero.")
        db.execute("update products set stock_qty=stock_qty+? where id=?", (n, product_id))
    db.execute("update products set unit_cost=? where id=?", (unit_cost, product_id))
    db.execute("insert into expenses(order_id,category,description,amount,date) values(NULL,'Stock purchase',?,?,?)",
               (f"{n} x {p['name']} from {supplier or 'supplier'}", unit_cost * n, today()))
    log(db, f"Stock received: {n} x {p['name']}", ["warehouse", "accountant"])
    return n


def dispatch(db, oid, text):
    o = one(db, "select o.*, l.name from orders o join leads l on l.id=o.lead_id where o.id=?", oid)
    if o["status"] != "confirmed":
        raise ValueError("This order is not waiting for dispatch.")
    lines = db.execute("""select ol.product_id, ol.qty, p.name, p.tracked, p.is_service, p.stock_qty
                          from order_lines ol join products p on p.id=ol.product_id where ol.order_id=?""", (oid,)).fetchall()
    need = {l["product_id"]: int(l["qty"]) for l in lines if l["tracked"]}
    names = {l["product_id"]: l["name"] for l in lines}
    codes = [c.upper() for c in re.split(r"[\s,;]+", (text or "").strip()) if c]
    if len(codes) != len(set(codes)):
        raise ValueError("A serial number was scanned twice.")
    got, rows = {}, []
    for c in codes:
        s = one(db, "select * from serials where serial=?", c)
        if not s:
            raise ValueError(f"Serial {c} is not registered in stock.")
        if s["status"] != "in_stock":
            raise ValueError(f"Serial {c} is not available (status: {s['status']}).")
        if s["product_id"] not in need:
            raise ValueError(f"Serial {c} does not belong to any tracked item in this order.")
        got[s["product_id"]] = got.get(s["product_id"], 0) + 1
        rows.append(s)
    for pid, n in need.items():
        if got.get(pid, 0) != n:
            raise ValueError(f"{names[pid]}: {n} serial number(s) required, {got.get(pid, 0)} scanned.")
    for l in lines:
        if not l["tracked"] and not l["is_service"] and l["stock_qty"] < l["qty"]:
            raise ValueError(f"Not enough stock of {l['name']} (have {l['stock_qty']}, need {l['qty']:g}).")
    for l in lines:
        if not l["tracked"] and not l["is_service"]:
            db.execute("update products set stock_qty=stock_qty-? where id=?", (l["qty"], l["product_id"]))
    for s in rows:
        db.execute("update serials set status='dispatched', order_id=?, dispatched_at=? where id=?", (oid, ts(), s["id"]))
    db.execute("update orders set status='dispatched', dispatched_at=? where id=?", (ts(), oid))
    log(db, f"{o['number']} dispatched to {o['name']} with {len(rows)} serial-tracked units",
        [f"u:{o['installer_id']}", "accountant"])


def deliver(db, oid):
    o = one(db, "select o.*, l.name from orders o join leads l on l.id=o.lead_id where o.id=?", oid)
    if o["status"] != "dispatched":
        raise ValueError("Only dispatched orders can be marked delivered.")
    d = now().date()
    for s in db.execute("""select s.id, p.warranty_months from serials s join products p on p.id=s.product_id
                           where s.order_id=? and s.status='dispatched'""", (oid,)).fetchall():
        end = add_months(d, s["warranty_months"]).isoformat() if s["warranty_months"] else None
        db.execute("update serials set delivered_at=?, warranty_start=?, warranty_end=? where id=?",
                   (today(), today(), end, s["id"]))
    db.execute("update orders set status='delivered', delivered_at=? where id=?", (ts(), oid))
    log(db, f"{o['number']} delivered. Warranty started for all serial numbers",
        [f"u:{o['installer_id']}", "service"])


# ---------------------------------------------------------------- installation
def parse_dt(s):
    try:
        return datetime.strptime(s, "%Y-%m-%dT%H:%M")
    except (TypeError, ValueError):
        raise ValueError("Enter valid start and finish times.")


def complete_install(db, oid, uid, start, end, system_kw, power_kw, notes, signer, signature, photos):
    o = one(db, "select o.*, l.name from orders o join leads l on l.id=o.lead_id where o.id=?", oid)
    if o["status"] != "delivered":
        raise ValueError("Materials must be delivered to the site before the job can be completed.")
    s, e = parse_dt(start), parse_dt(end)
    if e <= s:
        raise ValueError("Finish time must be after the start time.")
    try:
        system_kw, power_kw = float(system_kw), float(power_kw)
    except (TypeError, ValueError):
        raise ValueError("System size and power test reading must be numbers.")
    if power_kw <= 0:
        raise ValueError("Record a power-generation test reading above 0 kW.")
    if not photos:
        raise ValueError("Upload at least one photo of the finished roof.")
    if not (signature or "").startswith("data:image/png"):
        raise ValueError("The customer must sign on screen to accept the work.")
    iid = db.execute("""insert into installs(order_id,technician_id,start_time,end_time,system_kw,power_test_kw,notes,
                        signer,signature,completed_at) values(?,?,?,?,?,?,?,?,?,?)""",
                     (oid, uid, start, end, system_kw, power_kw, notes, signer, signature, ts())).lastrowid
    for f in photos:
        db.execute("insert into install_photos(install_id,filename) values(?,?)", (iid, f))
    db.execute("update serials set status='installed' where order_id=? and status='dispatched'", (oid,))
    db.execute("update orders set status='installed' where id=?", (oid,))
    log(db, f"{o['number']} installed ({system_kw:g} kW, {(e - s).total_seconds() / 3600:.1f} h). Customer signed off",
        ["accountant", "service", "sales"])


def install_hours(row):
    try:
        return (parse_dt(row["end_time"]) - parse_dt(row["start_time"])).total_seconds() / 3600
    except ValueError:
        return 0.0


# ---------------------------------------------------------------- service tickets
def create_ticket(db, serial_id, issue, fault, uid):
    s = one(db, """select s.*, p.name pname from serials s join products p on p.id=s.product_id where s.id=?""", serial_id)
    if not s or not s["order_id"]:
        raise ValueError("Choose a serial number that has been sold to a customer.")
    if not (issue or "").strip():
        raise ValueError("Describe the problem the customer reports.")
    o = one(db, "select o.*, l.name, l.phone from orders o join leads l on l.id=o.lead_id where o.id=?", s["order_id"])
    in_w = 1 if (s["warranty_end"] and s["warranty_end"] >= today()) else 0
    tid = db.execute("""insert into tickets(number,order_id,serial_id,customer_name,phone,issue,fault_code,status,
                        in_warranty,created_at,created_by) values(?,?,?,?,?,?,?,'New',?,?,?)""",
                     (next_no(db, "TK", "tickets"), o["id"], serial_id, o["name"], o["phone"], issue.strip(),
                      fault, in_w, ts(), uid)).lastrowid
    log(db, f"Service ticket for {o['name']}: {s['pname']} {s['serial']} ({'in warranty' if in_w else 'OUT of warranty'})",
        ["service"])
    return tid


def assign_ticket(db, tid, tech_id):
    t = one(db, "select * from tickets where id=?", tid)
    if t["status"] not in ("New", "Assigned"):
        raise ValueError("Only new tickets can be assigned.")
    if not tech_id:
        raise ValueError("Choose a technician.")
    db.execute("update tickets set assigned_to=?, status='Assigned' where id=?", (tech_id, tid))
    log(db, f"Ticket {t['number']} assigned to you: {t['customer_name']}", [f"u:{tech_id}"])


def start_ticket(db, tid):
    t = one(db, "select * from tickets where id=?", tid)
    if t["status"] not in ("Assigned", "New"):
        raise ValueError("This ticket cannot be started.")
    db.execute("update tickets set status='In Progress' where id=?", (tid,))


def resolve_ticket(db, tid, resolution, labour, parts):
    t = one(db, "select * from tickets where id=?", tid)
    if t["status"] not in ("New", "Assigned", "In Progress"):
        raise ValueError("This ticket is already resolved.")
    if not (resolution or "").strip():
        raise ValueError("Describe what was done to fix the problem.")
    labour = float(labour or 0)
    parts_total = parts_cost = 0.0
    for pid, qty, new_serial in parts:
        p = one(db, "select * from products where id=?", pid)
        qty = int(qty or 1)
        if p["tracked"]:
            new_serial = (new_serial or "").strip().upper()
            ns = one(db, "select * from serials where serial=?", new_serial)
            if not ns or ns["product_id"] != p["id"] or ns["status"] != "in_stock":
                raise ValueError(f"Scan an in-stock serial number for the replacement {p['name']}.")
            end = add_months(now().date(), p["warranty_months"]).isoformat() if p["warranty_months"] else None
            db.execute("""update serials set status='installed', order_id=?, dispatched_at=?, delivered_at=?,
                          warranty_start=?, warranty_end=? where id=?""",
                       (t["order_id"], ts(), today(), today(), end, ns["id"]))
            old = one(db, "select * from serials where id=?", t["serial_id"])
            if old and old["product_id"] == p["id"] and old["status"] != "faulty":
                db.execute("update serials set status='faulty' where id=?", (old["id"],))
            qty = 1
        else:
            if p["stock_qty"] < qty:
                raise ValueError(f"Not enough stock of {p['name']}.")
            db.execute("update products set stock_qty=stock_qty-? where id=?", (qty, pid))
        db.execute("insert into ticket_parts(ticket_id,product_id,qty,unit_price,new_serial) values(?,?,?,?,?)",
                   (tid, pid, qty, p["sale_price"], new_serial if p["tracked"] else None))
        parts_total += qty * p["sale_price"]
        parts_cost += qty * p["unit_cost"]
    if t["in_warranty"]:
        charge, labour = 0.0, 0.0
        if parts_cost:
            db.execute("insert into expenses(order_id,category,description,amount,date) values(?,?,?,?,?)",
                       (t["order_id"], "Warranty replacement", f"Parts for {t['number']}", parts_cost, today()))
    else:
        charge = parts_total + labour
        if charge > 0:
            db.execute("insert into invoices(number,order_id,ticket_id,kind,amount,date,status) values(?,?,?,?,?,?,'Unpaid')",
                       (next_no(db, "INV", "invoices"), t["order_id"], tid, "service", charge, today()))
    db.execute("update tickets set status='Resolved', resolution=?, labour_charge=?, charge=?, resolved_at=? where id=?",
               (resolution.strip(), labour, charge, ts(), tid))
    log(db, f"Ticket {t['number']} resolved ({'free, under warranty' if t['in_warranty'] else f'billed {charge:,.0f}'})",
        ["service", "accountant"])


def close_ticket(db, tid):
    t = one(db, "select * from tickets where id=?", tid)
    if t["status"] != "Resolved":
        raise ValueError("Resolve the ticket before the customer confirms.")
    db.execute("update tickets set status='Closed', closed_at=? where id=?", (ts(), tid))
    log(db, f"Ticket {t['number']} closed: customer confirmed everything works", ["service"])


# ---------------------------------------------------------------- finance
def financials(db, oid):
    o = one(db, "select * from orders where id=?", oid)
    material = val(db, "select sum(qty*unit_cost) from order_lines where order_id=?", oid)
    labour = 0.0
    for i in db.execute("select i.*, u.hourly_rate from installs i join users u on u.id=i.technician_id where i.order_id=?", (oid,)):
        labour += install_hours(i) * i["hourly_rate"]
    other = val(db, "select sum(amount) from expenses where order_id=?", oid)
    service = val(db, "select sum(amount) from invoices where order_id=? and kind='service'", oid)
    revenue = o["total"] + service
    received = val(db, "select sum(amount) from payments where order_id=?", oid)
    cost = material + labour + other
    profit = revenue - cost
    return dict(revenue=revenue, received=received, due=revenue - received, material=material,
                labour=labour, other=other, cost=cost, profit=profit,
                margin=(profit / revenue * 100) if revenue else 0)


def project_rows(db, start=None):
    sql = """select o.*, l.name, l.kw, l.system_type from orders o join leads l on l.id=o.lead_id"""
    args = ()
    if start:
        sql += " where date(o.created_at)>=?"
        args = (start,)
    rows = []
    for o in db.execute(sql + " order by o.id desc", args):
        f = financials(db, o["id"])
        rows.append({**dict(o), **f})
    return rows


def dashboard(db, start):
    d = {}
    d["enquiries"] = val(db, "select count(*) from leads where date(created_at)>=?", start)
    d["orders_n"] = val(db, "select count(*) from orders where date(created_at)>=?", start)
    d["orders_value"] = val(db, "select sum(total) from orders where date(created_at)>=?", start)
    d["cash_in"] = val(db, "select sum(amount) from payments where date>=?", start)
    d["expenses"] = val(db, "select sum(amount) from expenses where date>=?", start)
    d["installs"] = val(db, "select count(*) from installs where date(completed_at)>=?", start)
    d["tickets_new"] = val(db, "select count(*) from tickets where date(created_at)>=?", start)
    rows = project_rows(db, start)
    d["profit"] = sum(r["profit"] for r in rows)
    d["revenue"] = sum(r["revenue"] for r in rows)
    d["projects"] = project_rows(db)
    d["maxabs"] = max([abs(r["profit"]) for r in d["projects"]] or [1]) or 1
    d["stages"] = {s: val(db, "select count(*) from orders where status=?", s) for s in ORDER_STAGES}
    d["unfinished"] = [r for r in d["projects"] if r["status"] != "installed"]
    d["open_tickets"] = db.execute("""select t.*, u.name tech from tickets t left join users u on u.id=t.assigned_to
                                      where t.status!='Closed' order by t.in_warranty, t.id desc""").fetchall()
    d["leads"] = {s: val(db, "select count(*) from leads where status=?", s) for s in LEAD_STATUSES}
    d["dues"] = sum(max(0, r["due"]) for r in d["projects"])
    d["subsidy_pending"] = db.execute("""select count(*) c, coalesce(sum(subsidy_amount),0) s from orders
                                         where subsidy_status in ('To apply','Applied','Approved')""").fetchone()
    d["low_stock"] = db.execute("""select * from (select p.name, p.reorder_level,
        case when p.tracked=1 then (select count(*) from serials s where s.product_id=p.id and s.status='in_stock')
             else p.stock_qty end as onhand from products p where is_service=0) where onhand<=reorder_level""").fetchall()
    # six-month trend
    t = now().date().replace(day=1)
    months = []
    for k in range(5, -1, -1):
        m = add_months(t, -k)
        key = m.strftime("%Y-%m")
        cash = val(db, "select sum(amount) from payments where substr(date,1,7)=?", key)
        pr = sum(financials(db, r["id"])["profit"] for r in db.execute(
            "select id from orders where substr(created_at,1,7)=?", (key,)))
        months.append(dict(label=m.strftime("%b"), cash=cash, profit=pr))
    d["trend"] = months
    return d
