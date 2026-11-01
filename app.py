"""Solar Installation Management System: web application (Flask + SQLite)."""
import os
import io
import csv
import re
import secrets
from datetime import date, datetime, timedelta
from functools import wraps

import segno
from flask import (Flask, Response, abort, flash, g, redirect, render_template, request,
                   send_from_directory, session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

import db as dbm
import services as S

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-only-change-me")
app.config["MAX_CONTENT_LENGTH"] = 40 * 1024 * 1024
UPLOAD_DIR = os.path.join(dbm.BASE, "instance", "uploads")
ROLES = dbm.ROLES

HOME = {"admin": "dashboard", "director": "dashboard", "sales": "leads", "survey": "surveys",
        "warehouse": "warehouse", "installer": "jobs", "service": "tickets", "accountant": "accounts"}
NAV = {
    "admin": [("Dashboard", "dashboard"), ("Leads", "leads"), ("Surveys", "surveys"), ("Orders", "orders"),
              ("Warehouse", "warehouse"), ("Jobs", "jobs"), ("Service", "tickets"), ("Accounts", "accounts"),
              ("Admin", "admin_users")],
    "director": [("Dashboard", "dashboard"), ("Orders", "orders"), ("Service", "tickets"), ("Accounts", "accounts")],
    "sales": [("Leads", "leads"), ("Orders", "orders")],
    "survey": [("My surveys", "surveys")],
    "warehouse": [("Warehouse", "warehouse"), ("Orders", "orders")],
    "installer": [("My jobs", "jobs")],
    "service": [("Tickets", "tickets"), ("Orders", "orders")],
    "accountant": [("Accounts", "accounts"), ("Orders", "orders")],
}
FINANCE = ("admin", "director", "accountant")
PUBLIC = {"login", "enquiry", "enquiry_thanks", "warranty_lookup", "share_report", "static", "landing", "home"}


# ------------------------------------------------------------------ plumbing
def get_db():
    if "db" not in g:
        g.db = dbm.connect()
    return g.db


@app.teardown_appcontext
def close_db(_):
    d = g.pop("db", None)
    if d:
        d.close()


def q(sql, *a):
    return get_db().execute(sql, a).fetchall()


def q1(sql, *a):
    return get_db().execute(sql, a).fetchone()


def act(fn, *a, ok=None, **k):
    """Run a service call; commit on success, flash the rule message on failure."""
    try:
        r = fn(get_db(), *a, **k)
        get_db().commit()
        if ok:
            flash(ok, "ok")
        return r if r is not None else True
    except ValueError as e:
        get_db().rollback()
        flash(str(e), "err")
        return None


def roles(*allowed):
    def deco(f):
        @wraps(f)
        def w(*a, **k):
            if session.get("role") not in allowed + ("admin",):
                abort(403)
            return f(*a, **k)
        return w
    return deco


@app.before_request
def guard():
    if request.endpoint in PUBLIC or request.endpoint is None:
        return
    if "uid" not in session:
        return redirect(url_for("login", next=request.path))


@app.errorhandler(403)
def forbidden(_):
    return render_template("error.html", code=403, msg="Your role does not have access to this page."), 403


@app.errorhandler(404)
def notfound(_):
    return render_template("error.html", code=404, msg="That page or record does not exist."), 404


def inr(v):
    v = int(round(float(v or 0)))
    neg, s = v < 0, str(abs(v))
    if len(s) > 3:
        head, tail, parts = s[:-3], s[-3:], []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        s = ",".join(parts + [tail])
    return ("-" if neg else "") + "₹" + s


app.jinja_env.filters["inr"] = inr
app.jinja_env.filters["d"] = lambda s: (s or "")[:10]
app.jinja_env.filters["dt"] = lambda s: (s or "")[:16].replace("T", " ")


def notifs(limit=30, hours=None):
    role, uid = session.get("role"), session.get("uid")
    if role == "admin":
        rows = q("select * from activity order by id desc limit ?", limit)
    else:
        rows = q("select * from activity where target like ? or target like ? order by id desc limit ?",
                 f"%,{role},%", f"%,u:{uid},%", limit)
    if hours:
        cut = (datetime.now() - timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")
        rows = [r for r in rows if r["ts"] >= cut]
    return rows


@app.context_processor
def inject():
    r = session.get("role")
    return dict(nav_items=NAV.get(r, []), role_label=ROLES.get(r, ""), ROLES=ROLES, today=S.today(),
                unread=len(notifs(50, 24)) if r else 0, FINANCE=FINANCE, S=S)


def products_in_stock():
    return q("""select p.*, case when p.tracked=1 then (select count(*) from serials s where s.product_id=p.id and s.status='in_stock')
                else p.stock_qty end as onhand,
                coalesce((select sum(ol.qty) from order_lines ol join orders o on o.id=ol.order_id
                          where ol.product_id=p.id and o.status='confirmed'),0) as reserved
                from products p where is_service=0 order by category, name""")


# ------------------------------------------------------------------ auth + public
@app.route("/")
def home():
    if "uid" in session and request.args.get("view") != "landing":
        return redirect(url_for(HOME[session["role"]]))
    return landing()


@app.route("/landing")
def landing():
    db = get_db()
    pkgs = q("select * from packages where ptype in ('On-Grid', 'Off-Grid') order by ptype, kw")
    packages = []
    for p in pkgs:
        packages.append(dict(p, price=S.package_price(db, p["id"])))
    return render_template("landing.html", packages=packages, demo=dbm.DEMO_USERS, pw=dbm.DEMO_PASSWORD)


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        u = q1("select * from users where email=? and active=1", request.form["email"].strip().lower())
        if u and check_password_hash(u["password_hash"], request.form["password"]):
            session.clear()
            session.update(uid=u["id"], name=u["name"], role=u["role"])
            nxt = request.args.get("next", "")
            return redirect(nxt if nxt.startswith("/") and not nxt.startswith("//") else url_for("home"))
        flash("Email or password is incorrect.", "err")
    return render_template("login.html", demo=dbm.DEMO_USERS, pw=dbm.DEMO_PASSWORD)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/enquiry", methods=["GET", "POST"])
def enquiry():
    """Public web form. The enquiry lands in the sales queue immediately."""
    if request.method == "POST":
        f = request.form
        r = act(S.create_lead, f.get("name"), f.get("phone"), f.get("address"), f.get("kw"), f.get("system_type"),
                f.get("subsidy"), email=f.get("email", ""), source="Web form", notes=f.get("message", ""))
        if r:
            return redirect(url_for("enquiry_thanks"))
    return render_template("enquiry.html", f=request.values)


@app.route("/enquiry/thanks")
def enquiry_thanks():
    return render_template("thanks.html")


@app.route("/warranty", methods=["GET", "POST"])
def warranty_lookup():
    s = None
    code = (request.values.get("serial") or "").strip().upper()
    if code:
        s = q1("""select s.*, p.name pname from serials s join products p on p.id=s.product_id where s.serial=?""", code)
        if not s:
            flash("That serial number is not registered.", "err")
    return render_template("warranty.html", s=s, code=code)


@app.route("/qr/<serial>.svg")
def qr(serial):
    buf = io.BytesIO()
    segno.make(serial, error="m").save(buf, kind="svg", scale=4, border=1, xmldecl=False)
    return Response(buf.getvalue(), mimetype="image/svg+xml")


@app.route("/uploads/<path:fn>")
def uploads(fn):
    return send_from_directory(UPLOAD_DIR, fn)


@app.route("/activity")
def activity():
    return render_template("activity.html", rows=notifs(100))


# ------------------------------------------------------------------ sales: leads
@app.route("/leads")
@roles("sales")
def leads():
    status, s = request.args.get("status", ""), request.args.get("q", "").strip()
    sql = "select l.*, u.name owner from leads l left join users u on u.id=l.assigned_to where 1=1"
    args = []
    if status:
        sql += " and l.status=?"
        args.append(status)
    if s:
        sql += " and (l.name like ? or l.phone like ? or l.address like ?)"
        args += [f"%{s}%"] * 3
    rows = get_db().execute(sql + " order by l.id desc", args).fetchall()
    counts = {r["status"]: r["c"] for r in q("select status, count(*) c from leads group by status")}
    return render_template("leads.html", rows=rows, status=status, s=s, counts=counts)


@app.route("/leads/new", methods=["POST"])
@roles("sales")
def lead_new():
    f = request.form
    lid = act(S.create_lead, f.get("name"), f.get("phone"), f.get("address"), f.get("kw"), f.get("system_type"),
              f.get("subsidy"), email=f.get("email", ""), source=f.get("source", "Phone call"),
              notes=f.get("message", ""), ok="Enquiry added and assigned.")
    return redirect(url_for("lead", lid=lid) if lid else url_for("leads"))


@app.route("/leads/<int:lid>")
@roles("sales", "director")
def lead(lid):
    l = q1("select l.*, u.name owner from leads l left join users u on u.id=l.assigned_to where l.id=?", lid)
    if not l:
        abort(404)
    return render_template(
        "lead.html", l=l,
        survey=q1("select s.*, u.name tech from surveys s join users u on u.id=s.technician_id where s.lead_id=? and s.status!='Cancelled' order by s.id desc", lid),
        quotes=q("select * from quotes where lead_id=? order by id desc", lid),
        order=q1("select * from orders where lead_id=?", lid),
        techs=q("select * from users where role='survey' and active=1"),
        packages=[dict(p, price=S.package_price(get_db(), p["id"])) for p in q("select * from packages order by ptype, kw")])


@app.route("/leads/<int:lid>/qualify", methods=["POST"])
@roles("sales")
def lead_qualify(lid):
    f = request.form
    act(S.qualify, lid, float(f.get("shadow_area") or 0), float(f.get("monthly_bill") or 0), f.get("grid_ok"),
        f.get("call_notes", ""), f.get("outcome"), f.get("followup_date"), ok="Call recorded.")
    return redirect(url_for("lead", lid=lid))


@app.route("/leads/<int:lid>/survey", methods=["POST"])
@roles("sales")
def lead_survey(lid):
    act(S.schedule_survey, lid, request.form.get("tech_id"), request.form.get("when"), ok="Survey booked. The technician sees it now.")
    return redirect(url_for("lead", lid=lid))


@app.route("/leads/<int:lid>/quote", methods=["POST"])
@roles("sales")
def lead_quote(lid):
    f = request.form
    qid = act(S.create_quote, lid, f.get("package_id"), f.get("discount"), f.get("subsidy_amount"), session["uid"],
              ok="Quotation created from the template. The parts list follows automatically.")
    return redirect(url_for("quote", qid=qid) if qid else url_for("lead", lid=lid))


@app.route("/quotes/<int:qid>")
@roles("sales", "director", "accountant")
def quote(qid):
    qt = q1("""select q.*, l.name, l.phone, l.address, l.kw, l.subsidy, l.system_type, p.name pkg
               from quotes q join leads l on l.id=q.lead_id join packages p on p.id=q.package_id where q.id=?""", qid)
    if not qt:
        abort(404)
    lines = q("""select ql.*, p.name, p.unit, p.category from quote_lines ql join products p on p.id=ql.product_id
                 where ql.quote_id=? order by p.category='service', p.category, p.name""", qid)
    total, sub = S.quote_total(get_db(), qid)
    return render_template("quote.html", qt=qt, lines=lines, total=total, sub=sub,
                           installers=q("select * from users where role='installer' and active=1"),
                           order=q1("select * from orders where quote_id=?", qid))


@app.route("/quotes/<int:qid>/status", methods=["POST"])
@roles("sales")
def quote_status(qid):
    st = request.form.get("status")
    if st in ("Sent", "Rejected"):
        get_db().execute("update quotes set status=? where id=? and status!='Accepted'", (st, qid))
        get_db().commit()
        flash(f"Quotation marked {st.lower()}.", "ok")
    return redirect(url_for("quote", qid=qid))


@app.route("/quotes/<int:qid>/confirm", methods=["POST"])
@roles("sales")
def quote_confirm(qid):
    f = request.form
    oid = act(S.confirm_order, qid, f.get("advance"), f.get("method"), f.get("ref"), f.get("installer_id"),
              f.get("install_date"), session["uid"],
              ok="Order confirmed. Warehouse and installer have been notified.")
    return redirect(url_for("order", oid=oid) if oid else url_for("quote", qid=qid))


# ------------------------------------------------------------------ site surveys
@app.route("/surveys")
@roles("survey", "sales", "director")
def surveys():
    sql = """select s.*, l.name, l.phone, l.address, l.kw, l.system_type, u.name tech from surveys s
             join leads l on l.id=s.lead_id join users u on u.id=s.technician_id where s.status!='Cancelled'"""
    args = []
    if session["role"] == "survey":
        sql += " and s.technician_id=?"
        args.append(session["uid"])
    rows = get_db().execute(sql + " order by s.status='Scheduled' desc, s.scheduled_date", args).fetchall()
    return render_template("surveys.html", rows=rows)


@app.route("/surveys/<int:sid>", methods=["GET", "POST"])
@roles("survey", "sales", "director")
def survey(sid):
    s = q1("""select s.*, l.name, l.phone, l.address, l.kw, l.system_type, l.shadow_area claimed_shadow,
              l.monthly_bill, l.grid_ok, l.call_notes from surveys s join leads l on l.id=s.lead_id where s.id=?""", sid)
    if not s:
        abort(404)
    mine = session["role"] in ("survey", "admin") and (session["role"] == "admin" or s["technician_id"] == session["uid"])
    if request.method == "POST":
        if not mine:
            abort(403)
        f = request.form
        if act(S.submit_survey, sid, f.get("direction"), f.get("tilt"), f.get("cable"), f.get("shadow"),
               f.get("notes", ""), f.get("decision") == "approve", ok="Survey submitted. Sales can now prepare the quote."):
            return redirect(url_for("surveys"))
        return redirect(url_for("survey", sid=sid))
    return render_template("survey.html", s=s, mine=mine)


# ------------------------------------------------------------------ orders
@app.route("/orders")
@roles("sales", "warehouse", "service", "accountant", "director")
def orders():
    status, s = request.args.get("status", ""), request.args.get("q", "").strip()
    sql = """select o.*, l.name, l.phone, l.kw, l.system_type, u.name installer from orders o
             join leads l on l.id=o.lead_id left join users u on u.id=o.installer_id where 1=1"""
    args = []
    if status:
        sql += " and o.status=?"
        args.append(status)
    if s:
        sql += " and (o.number like ? or l.name like ? or l.phone like ?)"
        args += [f"%{s}%"] * 3
    rows = get_db().execute(sql + " order by o.id desc", args).fetchall()
    due = {r["id"]: S.financials(get_db(), r["id"])["due"] for r in rows}
    return render_template("orders.html", rows=rows, status=status, s=s, due=due)


@app.route("/orders/<int:oid>")
@roles("sales", "warehouse", "service", "accountant", "director", "installer")
def order(oid):
    o = q1("""select o.*, l.name, l.phone, l.address, l.kw, l.system_type, u.name installer from orders o
              join leads l on l.id=o.lead_id left join users u on u.id=o.installer_id where o.id=?""", oid)
    if not o:
        abort(404)
    if session["role"] == "installer" and o["installer_id"] != session["uid"]:
        abort(403)
    lines = q("""select ol.*, p.name, p.unit, p.tracked, p.is_service, p.category from order_lines ol
                 join products p on p.id=ol.product_id where ol.order_id=? order by p.is_service, p.category""", oid)
    suggest = {}
    if o["status"] == "confirmed":
        for l in lines:
            if l["tracked"]:
                suggest[l["product_id"]] = [r["serial"] for r in q(
                    "select serial from serials where product_id=? and status='in_stock' order by id limit ?",
                    l["product_id"], int(l["qty"]))]
    inst = q1("select i.*, u.name tech from installs i join users u on u.id=i.technician_id where i.order_id=?", oid)
    return render_template(
        "order.html", o=o, lines=lines, suggest=suggest, inst=inst,
        photos=q("select * from install_photos where install_id=?", inst["id"]) if inst else [],
        serials=q("""select s.*, p.name pname from serials s join products p on p.id=s.product_id
                     where s.order_id=? order by p.category, s.serial""", oid),
        invoices=[dict(i, paid=S.invoice_paid(get_db(), i["id"])) for i in q("select * from invoices where order_id=?", oid)],
        payments=q("select * from payments where order_id=? order by id", oid),
        tickets=q("select * from tickets where order_id=? order by id desc", oid),
        fin=S.financials(get_db(), oid), hours=S.install_hours(inst) if inst else 0)


@app.route("/orders/<int:oid>/dispatch", methods=["POST"])
@roles("warehouse")
def order_dispatch(oid):
    act(S.dispatch, oid, request.form.get("serials", ""), ok="Dispatched. Every serial number is now linked to this customer.")
    return redirect(url_for("order", oid=oid))


@app.route("/orders/<int:oid>/deliver", methods=["POST"])
@roles("warehouse", "installer")
def order_deliver(oid):
    act(S.deliver, oid, ok="Delivery recorded. The warranty clock has started for every serial number.")
    return redirect(url_for("order", oid=oid))


@app.route("/orders/<int:oid>/subsidy", methods=["POST"])
@roles("accountant", "sales")
def order_subsidy(oid):
    st = request.form.get("status")
    if st in S.SUBSIDY_STATUSES:
        get_db().execute("update orders set subsidy_status=?, subsidy_amount=? where id=?",
                         (st, float(request.form.get("amount") or 0), oid))
        get_db().commit()
        flash("Subsidy status updated.", "ok")
    return redirect(request.referrer or url_for("order", oid=oid))


# ------------------------------------------------------------------ warehouse
@app.route("/warehouse")
@roles("warehouse", "accountant", "director")
def warehouse():
    return render_template(
        "warehouse.html", stock=products_in_stock(),
        pending=q("select o.*, l.name, l.kw, l.system_type from orders o join leads l on l.id=o.lead_id where o.status='confirmed' order by o.install_date"),
        transit=q("select o.*, l.name, l.address from orders o join leads l on l.id=o.lead_id where o.status='dispatched' order by o.id"))


@app.route("/warehouse/receive", methods=["POST"])
@roles("warehouse")
def warehouse_receive():
    f = request.form
    codes = [c for c in re.split(r"[\s,;]+", f.get("serials", "").strip()) if c]
    n = act(S.receive_stock, f.get("product_id"), f.get("qty"), f.get("unit_cost"), f.get("supplier"), codes)
    if n:
        flash(f"{n} unit(s) received into stock and the purchase was logged for accounts.", "ok")
    return redirect(url_for("warehouse"))


@app.route("/warehouse/serials")
@roles("warehouse", "service", "accountant", "director")
def serials():
    s, status = request.args.get("q", "").strip(), request.args.get("status", "")
    sql = """select s.*, p.name pname, p.category, o.number onum, l.name cname from serials s
             join products p on p.id=s.product_id left join orders o on o.id=s.order_id left join leads l on l.id=o.lead_id where 1=1"""
    args = []
    if s:
        sql += " and (s.serial like ? or l.name like ? or o.number like ? or p.name like ?)"
        args += [f"%{s}%"] * 4
    if status:
        sql += " and s.status=?"
        args.append(status)
    rows = get_db().execute(sql + " order by s.id desc limit 300", args).fetchall()
    return render_template("serials.html", rows=rows, s=s, status=status)


@app.route("/warehouse/labels")
@roles("warehouse")
def labels():
    pid = request.args.get("product_id")
    rows = q("""select s.serial, p.name from serials s join products p on p.id=s.product_id
                where s.status='in_stock' and (?='' or s.product_id=?) order by s.id desc limit 120""", pid or "", pid or "")
    return render_template("labels.html", rows=rows)


# ------------------------------------------------------------------ installation
@app.route("/jobs")
@roles("installer")
def jobs():
    sql = """select o.*, l.name, l.phone, l.address, l.kw, l.system_type, u.name installer from orders o
             join leads l on l.id=o.lead_id left join users u on u.id=o.installer_id where 1=1"""
    args = []
    if session["role"] == "installer":
        sql += " and o.installer_id=?"
        args.append(session["uid"])
    rows = get_db().execute(sql + " order by o.status='installed', o.install_date", args).fetchall()
    mine = q("""select t.*, l.address from tickets t join orders o on o.id=t.order_id join leads l on l.id=o.lead_id
                where t.status not in ('Closed','Resolved') and (?='admin' or t.assigned_to=?) order by t.id desc""",
             session["role"], session["uid"])
    return render_template("jobs.html", rows=rows, tickets=mine)


@app.route("/jobs/<int:oid>", methods=["GET", "POST"])
@roles("installer")
def job(oid):
    o = q1("select o.*, l.name, l.phone, l.address, l.kw, l.system_type from orders o join leads l on l.id=o.lead_id where o.id=?", oid)
    if not o:
        abort(404)
    if session["role"] == "installer" and o["installer_id"] != session["uid"]:
        abort(403)
    if request.method == "POST":
        f = request.form
        names = []
        for file in request.files.getlist("photos"):
            ext = os.path.splitext(file.filename or "")[1].lower()
            if file and ext in (".jpg", ".jpeg", ".png", ".webp", ".heic"):
                fn = f"{oid}_{secrets.token_hex(4)}{ext}"
                os.makedirs(UPLOAD_DIR, exist_ok=True)
                file.save(os.path.join(UPLOAD_DIR, fn))
                names.append(fn)
        if act(S.complete_install, oid, session["uid"], f.get("start"), f.get("end"), f.get("system_kw"),
               f.get("power_kw"), f.get("notes", ""), f.get("signer", ""), f.get("signature", ""), names,
               ok="Job completed. Proof saved and the customer's sign-off recorded."):
            return redirect(url_for("jobs"))
        for n in names:
            try:
                os.remove(os.path.join(UPLOAD_DIR, n))
            except OSError:
                pass
    return render_template("job.html", o=o, f=request.form,
                           serials=q("select s.serial, p.name from serials s join products p on p.id=s.product_id where s.order_id=?", oid))


# ------------------------------------------------------------------ service
@app.route("/tickets")
@roles("service", "installer", "director")
def tickets():
    status = request.args.get("status", "")
    sql = """select t.*, u.name tech, p.name pname, s.serial from tickets t left join users u on u.id=t.assigned_to
             join serials s on s.id=t.serial_id join products p on p.id=s.product_id where 1=1"""
    args = []
    if session["role"] == "installer":
        sql += " and t.assigned_to=?"
        args.append(session["uid"])
    if status:
        sql += " and t.status=?"
        args.append(status)
    rows = get_db().execute(sql + " order by t.status='Closed', t.id desc", args).fetchall()
    return render_template("tickets.html", rows=rows, status=status)


@app.route("/tickets/new", methods=["GET", "POST"])
@roles("service")
def ticket_new():
    if request.method == "POST":
        f = request.form
        tid = act(S.create_ticket, f.get("serial_id"), f.get("issue"), f.get("fault_code", ""), session["uid"],
                  ok="Ticket created.")
        if tid:
            return redirect(url_for("ticket", tid=tid))
    s = request.values.get("q", "").strip()
    found = []
    if s:
        found = q("""select s.*, p.name pname, o.number onum, l.name cname, l.phone, l.address from serials s
                     join products p on p.id=s.product_id join orders o on o.id=s.order_id join leads l on l.id=o.lead_id
                     where s.order_id is not null and (s.serial like ? or l.name like ? or l.phone like ? or o.number like ?)
                     order by p.tracked desc, p.category, s.id limit 40""", *[f"%{s}%"] * 4)
    return render_template("ticket_new.html", found=found, s=s, f=request.form)


@app.route("/tickets/<int:tid>")
@roles("service", "installer", "director")
def ticket(tid):
    t = q1("""select t.*, s.serial, s.warranty_start, s.warranty_end, s.status sstatus, p.name pname, l.address,
              o.number onum, u.name tech from tickets t join serials s on s.id=t.serial_id
              join products p on p.id=s.product_id join orders o on o.id=t.order_id join leads l on l.id=o.lead_id
              left join users u on u.id=t.assigned_to where t.id=?""", tid)
    if not t:
        abort(404)
    if session["role"] == "installer" and t["assigned_to"] != session["uid"]:
        abort(403)
    return render_template(
        "ticket.html", t=t,
        parts=q("select tp.*, p.name from ticket_parts tp join products p on p.id=tp.product_id where tp.ticket_id=?", tid),
        techs=q("select * from users where role='installer' and active=1"),
        catalogue=q("select * from products where is_service=0 order by category, name"),
        invoice=q1("select * from invoices where ticket_id=?", tid))


@app.route("/tickets/<int:tid>/<action>", methods=["POST"])
@roles("service", "installer")
def ticket_action(tid, action):
    f = request.form
    if action == "assign" and session["role"] in ("service", "admin"):
        act(S.assign_ticket, tid, f.get("tech_id"), ok="Technician assigned.")
    elif action == "start":
        act(S.start_ticket, tid, ok="Work started.")
    elif action == "resolve":
        parts = [(pid, f.getlist("qty")[i], f.getlist("new_serial")[i])
                 for i, pid in enumerate(f.getlist("part")) if pid]
        act(S.resolve_ticket, tid, f.get("resolution"), f.get("labour"), parts, ok="Ticket resolved. Waiting for the customer to confirm.")
    elif action == "close" and session["role"] in ("service", "admin"):
        act(S.close_ticket, tid, ok="Ticket closed.")
    else:
        abort(403)
    return redirect(url_for("ticket", tid=tid))


# ------------------------------------------------------------------ accounts
@app.route("/accounts")
@roles("accountant", "director")
def accounts():
    tab = request.args.get("tab", "invoices")
    month = request.args.get("month") or S.today()[:7]
    ctx = dict(tab=tab, month=month)
    db = get_db()
    if tab == "invoices":
        ctx["invoices"] = [dict(i, paid=S.invoice_paid(db, i["id"])) for i in q(
            "select i.*, l.name from invoices i join orders o on o.id=i.order_id join leads l on l.id=o.lead_id order by i.id desc")]
    elif tab == "payments":
        ctx["payments"] = q("""select p.*, i.number inv, l.name from payments p join invoices i on i.id=p.invoice_id
                               join orders o on o.id=p.order_id join leads l on l.id=o.lead_id order by p.id desc""")
    elif tab == "expenses":
        ctx["expenses"] = q("select e.*, o.number onum from expenses e left join orders o on o.id=e.order_id order by e.id desc limit 200")
        ctx["orders"] = q("select o.id, o.number, l.name from orders o join leads l on l.id=o.lead_id order by o.id desc")
    elif tab == "subsidy":
        ctx["subs"] = q("""select o.*, l.name, l.kw from orders o join leads l on l.id=o.lead_id
                           where o.subsidy_status!='Not applicable' order by o.id desc""")
    elif tab == "techpay":
        agg = {}
        for i in q("select i.*, u.name, u.hourly_rate from installs i join users u on u.id=i.technician_id where substr(i.end_time,1,7)=?", month):
            a = agg.setdefault(i["technician_id"], dict(name=i["name"], rate=i["hourly_rate"], jobs=0, hours=0.0))
            a["jobs"] += 1
            a["hours"] += S.install_hours(i)
        ctx["pay"] = [dict(a, amount=a["hours"] * a["rate"]) for a in agg.values()]
    elif tab == "profit":
        ctx["projects"] = sorted(S.project_rows(db), key=lambda r: r["profit"])
    totals = dict(due=sum(max(0, S.financials(db, o["id"])["due"]) for o in q("select id from orders")))
    ctx["totals"] = totals
    return render_template("accounts.html", **ctx)


@app.route("/accounts/payment", methods=["POST"])
@roles("accountant")
def add_payment():
    f = request.form
    act(S.record_payment, f.get("invoice_id"), f.get("amount"), f.get("method", "UPI"), f.get("ref", ""), ok="Payment recorded.")
    return redirect(request.referrer or url_for("accounts"))


@app.route("/accounts/expense", methods=["POST"])
@roles("accountant")
def add_expense():
    f = request.form
    try:
        amt = float(f.get("amount") or 0)
        if amt <= 0 or not f.get("description", "").strip():
            raise ValueError
    except ValueError:
        flash("Enter a description and an amount above zero.", "err")
        return redirect(url_for("accounts", tab="expenses"))
    get_db().execute("insert into expenses(order_id,category,description,amount,date) values(?,?,?,?,?)",
                     (f.get("order_id") or None, f.get("category"), f.get("description").strip(), amt, S.today()))
    get_db().commit()
    flash("Expense recorded.", "ok")
    return redirect(url_for("accounts", tab="expenses"))


@app.route("/invoices/<int:iid>")
@roles("accountant", "sales", "director", "service")
def invoice(iid):
    i = q1("""select i.*, l.name, l.phone, l.address, o.number onum from invoices i join orders o on o.id=i.order_id
              join leads l on l.id=o.lead_id where i.id=?""", iid)
    if not i:
        abort(404)
    if i["kind"] == "order":
        lines = [(r["name"], r["qty"], r["unit_price"]) for r in q(
            "select p.name, ol.qty, ol.unit_price from order_lines ol join products p on p.id=ol.product_id where ol.order_id=?", i["order_id"])]
        disc = S.val(get_db(), "select discount from quotes q join orders o on o.quote_id=q.id where o.id=?", i["order_id"])
    else:
        lines = [(r["name"], r["qty"], r["unit_price"]) for r in q(
            "select p.name, tp.qty, tp.unit_price from ticket_parts tp join products p on p.id=tp.product_id where tp.ticket_id=?", i["ticket_id"])]
        lab = S.val(get_db(), "select labour_charge from tickets where id=?", i["ticket_id"])
        if lab:
            lines.append(("Service labour", 1, lab))
        disc = 0
    return render_template("invoice.html", i=i, lines=lines, disc=disc, paid=S.invoice_paid(get_db(), iid),
                           pays=q("select * from payments where invoice_id=?", iid))


# ------------------------------------------------------------------ director dashboard
PERIODS = {"today": "Today", "week": "This week", "month": "This month"}


def period_start(p):
    t = date.fromisoformat(S.today())
    return {"today": t, "week": t - timedelta(days=t.weekday()), "month": t.replace(day=1)}.get(p, t.replace(day=1)).isoformat()


@app.route("/dashboard")
@roles("director")
def dashboard():
    p = request.args.get("p", "month")
    d = S.dashboard(get_db(), period_start(p))
    shares = q("select * from shares where expires_at>=? order by created_at desc", S.today())
    return render_template("dashboard.html", d=d, p=p, periods=PERIODS, shares=shares, shared=False)


@app.route("/dashboard/share", methods=["POST"])
@roles("director")
def dashboard_share():
    tok = secrets.token_urlsafe(16)
    get_db().execute("insert into shares values(?,?,?,?)",
                     (tok, session["uid"], S.ts(), (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d")))
    get_db().commit()
    flash("Read-only link created. It expires in 7 days.", "ok")
    return redirect(url_for("dashboard"))


@app.route("/dashboard/unshare/<tok>", methods=["POST"])
@roles("director")
def dashboard_unshare(tok):
    get_db().execute("delete from shares where token=?", (tok,))
    get_db().commit()
    return redirect(url_for("dashboard"))


@app.route("/share/<tok>")
def share_report(tok):
    s = q1("select * from shares where token=? and expires_at>=?", tok, S.today())
    if not s:
        abort(404)
    p = request.args.get("p", "month")
    return render_template("dashboard.html", d=S.dashboard(get_db(), period_start(p)), p=p, periods=PERIODS,
                           shares=[], shared=True, tok=tok)


@app.route("/export/projects.csv")
@roles("accountant", "director")
def export_projects():
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["Order", "Customer", "kW", "Type", "Status", "Revenue", "Received", "Due", "Material", "Labour", "Other", "Profit", "Margin %"])
    for r in S.project_rows(get_db()):
        w.writerow([r["number"], r["name"], r["kw"], r["system_type"], r["status"], round(r["revenue"]), round(r["received"]),
                    round(r["due"]), round(r["material"]), round(r["labour"]), round(r["other"]), round(r["profit"]), round(r["margin"], 1)])
    return Response(out.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=project_profitability.csv"})


# ------------------------------------------------------------------ admin
@app.route("/admin/users", methods=["GET", "POST"])
@roles()
def admin_users():
    if request.method == "POST":
        f = request.form
        try:
            if f["role"] not in ROLES or len(f["password"]) < 6:
                raise ValueError("Choose a role and a password of at least 6 characters.")
            get_db().execute("insert into users(name,email,password_hash,role,hourly_rate) values(?,?,?,?,?)",
                             (f["name"].strip(), f["email"].strip().lower(), generate_password_hash(f["password"]),
                              f["role"], float(f.get("rate") or 0)))
            get_db().commit()
            flash("User added.", "ok")
        except Exception as e:  # duplicate email, bad value
            get_db().rollback()
            flash(str(e) if isinstance(e, ValueError) else "That email is already in use.", "err")
        return redirect(url_for("admin_users"))
    return render_template("admin_users.html", users=q("select * from users order by role, name"))


@app.route("/admin/users/<int:uid>/toggle", methods=["POST"])
@roles()
def admin_toggle(uid):
    if uid != session["uid"]:
        get_db().execute("update users set active=1-active where id=?", (uid,))
        get_db().commit()
    return redirect(url_for("admin_users"))


@app.route("/admin/products", methods=["GET", "POST"])
@roles()
def admin_products():
    if request.method == "POST":
        f = request.form
        get_db().execute("update products set sale_price=?, unit_cost=?, warranty_months=?, reorder_level=? where id=?",
                         (f["sale_price"], f["unit_cost"], f["warranty_months"], f["reorder_level"], f["id"]))
        get_db().commit()
        flash("Product updated. New quotations use the new price.", "ok")
        return redirect(url_for("admin_products"))
    return render_template("admin_products.html", rows=q("select * from products order by category, name"))


@app.route("/admin/packages")
@roles()
def admin_packages():
    pk = [dict(p, price=S.package_price(get_db(), p["id"]),
               lines=q("""select pl.id, pl.qty, p.name, p.unit from package_lines pl join products p on p.id=pl.product_id
                          where pl.package_id=? order by p.category""", p["id"])) for p in q("select * from packages order by ptype, kw")]
    return render_template("admin_packages.html", packages=pk, products=q("select * from products order by name"))


@app.route("/admin/packages/<int:pid>/line", methods=["POST"])
@roles()
def admin_package_line(pid):
    f = request.form
    if f.get("delete"):
        get_db().execute("delete from package_lines where id=? and package_id=?", (f["delete"], pid))
    elif f.get("product_id") and float(f.get("qty") or 0) > 0:
        get_db().execute("insert into package_lines(package_id,product_id,qty) values(?,?,?)", (pid, f["product_id"], f["qty"]))
    get_db().commit()
    return redirect(url_for("admin_packages") + f"#p{pid}")


with app.app_context():
    dbm.init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=bool(os.environ.get("DEBUG")))
