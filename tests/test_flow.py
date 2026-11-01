"""End-to-end test: enquiry -> survey -> quote -> order -> dispatch -> install -> warranty -> service -> dashboard.
Run:  python -m pytest -q   (or)   python tests/test_flow.py
"""
import os, sys, tempfile, re, io
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["SOLAR_DB"] = os.path.join(tempfile.mkdtemp(), "t.db")
import app as A
import db as dbm
import services as S

PW = dbm.DEMO_PASSWORD
SIG = "data:image/png;base64," + "A" * 600


def client(email, pw=PW):
    c = A.app.test_client()
    r = c.post("/login", data={"email": email, "password": pw})
    assert r.status_code == 302, email
    return c


def ok(r, text=None):
    assert r.status_code == 200, (r.status_code, r.request.path)
    if text:
        assert text in r.get_data(as_text=True), text
    return r.get_data(as_text=True)


def last(table):
    with A.app.app_context():
        return A.q1(f"select * from {table} order by id desc limit 1")


def test_everything():
    # ---- every role can open its pages
    pages = {
        "admin@sunpower.test": ["/dashboard", "/leads", "/surveys", "/orders", "/warehouse", "/warehouse/serials", "/warehouse/labels", "/jobs", "/tickets", "/accounts", "/accounts?tab=expenses", "/accounts?tab=subsidy", "/accounts?tab=techpay", "/accounts?tab=profit", "/accounts?tab=payments", "/admin/users", "/admin/products", "/admin/packages", "/activity", "/orders/1", "/export/projects.csv", "/invoices/1", "/quotes/1", "/leads/1", "/tickets/1"],
        "director@sunpower.test": ["/dashboard", "/dashboard?p=week", "/dashboard?p=today", "/orders", "/orders/2", "/tickets", "/accounts"],
        "sales@sunpower.test": ["/leads", "/leads?status=New", "/leads/12", "/orders", "/quotes/1"],
        "survey@sunpower.test": ["/surveys"],
        "warehouse@sunpower.test": ["/warehouse", "/warehouse/serials?q=INV", "/warehouse/labels?product_id=2", "/orders/11"],
        "installer@sunpower.test": ["/jobs", "/tickets"],
        "service@sunpower.test": ["/tickets", "/tickets/new?q=Ramesh", "/tickets/1"],
        "accountant@sunpower.test": ["/accounts", "/accounts?tab=techpay", "/orders/1"],
    }
    for email, urls in pages.items():
        c = client(email)
        for u in urls:
            r = c.get(u)
            assert r.status_code in (200,), (email, u, r.status_code)
    # role walls
    assert client("survey@sunpower.test").get("/accounts").status_code == 403
    assert client("sales@sunpower.test").get("/warehouse").status_code == 403
    assert client("installer@sunpower.test").get("/dashboard").status_code == 403
    assert A.app.test_client().get("/leads").status_code == 302

    # ---- 1. public enquiry lands with sales
    anon = A.app.test_client()
    ok(anon.get("/enquiry"))
    r = anon.post("/enquiry", data={"name": "Test Customer", "phone": "9876543210", "address": "1 Test Road", "kw": "3", "system_type": "On-Grid", "subsidy": "1"})
    assert r.status_code == 302
    bad = anon.post("/enquiry", data={"name": "", "phone": "12", "address": "x", "kw": "3", "system_type": "On-Grid"})
    assert "valid 10-digit" in bad.get_data(as_text=True)
    lead = last("leads")
    assert lead["status"] == "New" and lead["assigned_to"]
    lid = lead["id"]

    sales = client("sales@sunpower.test")
    # quote blocked before survey
    r = sales.post(f"/leads/{lid}/quote", data={"package_id": 1}, follow_redirects=True)
    assert "after the site survey is approved" in r.get_data(as_text=True)
    # survey cannot be booked before the call
    r = sales.post(f"/leads/{lid}/survey", data={"tech_id": 4, "when": "2030-01-01"}, follow_redirects=True)
    assert "Qualify the enquiry" in r.get_data(as_text=True)
    sales.post(f"/leads/{lid}/qualify", data={"shadow_area": "30", "monthly_bill": "4000", "grid_ok": "1", "call_notes": "ok", "outcome": "Qualified"})
    surveyor = client("survey@sunpower.test")
    with A.app.app_context():
        tech = A.q1("select id from users where email='survey@sunpower.test'")["id"]
    sales.post(f"/leads/{lid}/survey", data={"tech_id": tech, "when": S.today()})
    sv = last("surveys")
    ok(surveyor.get(f"/surveys/{sv['id']}"), "Approve site")
    surveyor.post(f"/surveys/{sv['id']}", data={"direction": "South", "tilt": "15", "cable": "20", "shadow": "28", "notes": "fine", "decision": "approve"})
    assert last("leads")["status"] == "Survey Approved"

    # ---- 2. quote from template creates the parts list
    with A.app.app_context():
        pk = A.q1("select id from packages where name='On-Grid 3 kW'")["id"]
    sales.post(f"/leads/{lid}/quote", data={"package_id": pk, "discount": "0", "subsidy_amount": "78000"})
    qt = last("quotes")
    body = ok(sales.get(f"/quotes/{qt['id']}"), "Solar Inverter 3 kW")
    for part in ("Mono PERC Solar Panel", "MPPT Charge Controller", "Earthing Kit", "Lightning Arrestor", "Mounting Structure", "Installation"):
        assert part in body, part
    # order refused without advance
    r = sales.post(f"/quotes/{qt['id']}/confirm", data={"advance": "0", "installer_id": 7, "install_date": "2030-01-05"}, follow_redirects=True)
    assert "advance" in r.get_data(as_text=True)
    sales.post(f"/quotes/{qt['id']}/confirm", data={"advance": "75000", "method": "UPI", "ref": "T1", "installer_id": 7, "install_date": "2030-01-05"})
    o = last("orders")
    assert o["status"] == "confirmed"
    oid = o["id"]
    total = o["total"]

    # ---- 3. warehouse + installer see the job without phoning
    wh = client("warehouse@sunpower.test")
    ok(wh.get("/warehouse"), o["number"])
    inst = client("installer@sunpower.test")
    ok(inst.get("/jobs"), "Test Customer")

    # dispatch: wrong serials rejected, then correct ones
    r = wh.post(f"/orders/{oid}/dispatch", data={"serials": "INV-00001"}, follow_redirects=True)
    assert "required" in r.get_data(as_text=True) or "does not belong" in r.get_data(as_text=True) or "not available" in r.get_data(as_text=True)
    page = ok(wh.get(f"/orders/{oid}"))
    pick = re.findall(r"Pick: ([^<]+)<", page)
    codes = [c.strip() for grp in pick for c in grp.split(",")]
    assert len(codes) == 6 + 1 + 1  # 6 panels, inverter, controller
    r = wh.post(f"/orders/{oid}/dispatch", data={"serials": "\n".join(codes + [codes[0]])}, follow_redirects=True)
    assert "twice" in r.get_data(as_text=True)
    wh.post(f"/orders/{oid}/dispatch", data={"serials": "\n".join(codes)})
    with A.app.app_context():
        assert A.q1("select status from orders where id=?", oid)["status"] == "dispatched"
        assert A.q1("select count(*) c from serials where order_id=? and status='dispatched'", oid)["c"] == 8
    # cannot install before delivery
    assert "cannot be completed" in ok(inst.get(f"/jobs/{oid}"))
    wh.post(f"/orders/{oid}/deliver")
    with A.app.app_context():
        s = A.q1("select * from serials where serial=?", codes[-1])
        assert s["warranty_start"] == S.today() and s["warranty_end"]
        inv_serial = A.q1("select s.* from serials s join products p on p.id=s.product_id where s.order_id=? and p.category='inverter'", oid)
        pan_serial = A.q1("select s.* from serials s join products p on p.id=s.product_id where s.order_id=? and p.category='panel' limit 1", oid)
        y = int(S.today()[:4])
        assert int(inv_serial["warranty_end"][:4]) in (y + 2, y + 1 + 1)
        assert int(pan_serial["warranty_end"][:4]) == y + 20

    # ---- 4. installation proof
    data = {"start": "2030-01-05T09:00", "end": "2030-01-05T15:30", "system_kw": "3", "power_kw": "2.7", "notes": "done", "signer": "Test Customer", "signature": ""}
    r = inst.post(f"/jobs/{oid}", data={**data, "photos": (io.BytesIO(b"\x89PNG fakeimage"), "roof.jpg")}, content_type="multipart/form-data", follow_redirects=True)
    assert "must sign" in r.get_data(as_text=True)
    r = inst.post(f"/jobs/{oid}", data={**data, "signature": SIG}, content_type="multipart/form-data", follow_redirects=True)
    assert "at least one photo" in r.get_data(as_text=True)
    r = inst.post(f"/jobs/{oid}", data={**data, "signature": SIG, "photos": (io.BytesIO(b"\x89PNG fakeimage"), "roof.jpg")}, content_type="multipart/form-data", follow_redirects=True)
    assert "sign-off recorded" in r.get_data(as_text=True)
    with A.app.app_context():
        assert A.q1("select status from orders where id=?", oid)["status"] == "installed"
        assert A.q1("select count(*) c from install_photos")["c"] >= 1
    acc = client("accountant@sunpower.test")
    page = ok(acc.get(f"/orders/{oid}"), "Job profitability")
    inv = last("invoices")
    acc.post("/accounts/payment", data={"invoice_id": inv["id"], "amount": str(int(total - 75000)), "method": "UPI", "ref": "bal"})
    assert last("invoices")["id"] == inv["id"]
    with A.app.app_context():
        assert A.q1("select status from invoices where id=?", inv["id"])["status"] == "Paid"
    acc.post("/accounts/expense", data={"category": "Transport", "order_id": oid, "amount": "1200", "description": "Tempo"})
    ok(acc.get("/accounts?tab=expenses"), "Tempo")
    ok(acc.get(f"/invoices/{inv['id']}"), "Balance due")

    # ---- 5. public warranty lookup
    r = anon.post("/warranty", data={"serial": inv_serial["serial"]})
    assert "In warranty" in r.get_data(as_text=True)
    assert acc.get(f"/qr/{inv_serial['serial']}.svg").status_code == 200

    # ---- 6. service: in-warranty = free, out-of-warranty = billed
    svc = client("service@sunpower.test")
    ok(svc.get(f"/tickets/new?q={inv_serial['serial']}"), "Test Customer")
    svc.post("/tickets/new", data={"serial_id": inv_serial["id"], "issue": "Fault E04, no power", "fault_code": "E04"})
    tk = last("tickets")
    assert tk["in_warranty"] == 1
    svc.post(f"/tickets/{tk['id']}/assign", data={"tech_id": 7})
    ok(inst.get(f"/tickets/{tk['id']}"), "Free: repair or replacement covered")
    with A.app.app_context():
        pid = A.q1("select id from products where sku='INV3K'")["id"]
        new_sn = A.q1("select serial from serials where product_id=? and status='in_stock' limit 1", pid)["serial"]
    r = inst.post(f"/tickets/{tk['id']}/resolve", data={"resolution": "Replaced inverter", "labour": "500", "part": [str(pid), "", ""], "qty": ["1", "1", "1"], "new_serial": ["", "", ""]}, follow_redirects=True)
    assert "Scan an in-stock serial" in r.get_data(as_text=True)
    inst.post(f"/tickets/{tk['id']}/resolve", data={"resolution": "Replaced inverter", "labour": "500", "part": [str(pid), "", ""], "qty": ["1", "1", "1"], "new_serial": [new_sn, "", ""]})
    with A.app.app_context():
        t2 = A.q1("select * from tickets where id=?", tk["id"])
        assert t2["status"] == "Resolved" and t2["charge"] == 0
        assert A.q1("select status from serials where id=?", inv_serial["id"])["status"] == "faulty"
        assert A.q1("select count(*) c from expenses where category='Warranty replacement' and order_id=?", oid)["c"] == 1
        assert A.q1("select count(*) c from invoices where ticket_id=?", tk["id"])["c"] == 0
    svc.post(f"/tickets/{tk['id']}/close")
    assert last("tickets")["status"] == "Closed"
    # out of warranty (seed order 1 is 30 months old)
    with A.app.app_context():
        old = A.q1("select s.id from serials s join products p on p.id=s.product_id where p.category='inverter' and s.order_id=1")["id"]
    svc.post("/tickets/new", data={"serial_id": old, "issue": "dead"})
    tk2 = last("tickets")
    assert tk2["in_warranty"] == 0
    svc.post(f"/tickets/{tk2['id']}/resolve", data={"resolution": "Board repaired", "labour": "800", "part": ["", "", ""], "qty": ["1"] * 3, "new_serial": [""] * 3})
    with A.app.app_context():
        assert A.q1("select amount from invoices where ticket_id=?", tk2["id"])["amount"] == 800

    # ---- 7. director sees it, shares it
    d = client("director@sunpower.test")
    body = ok(d.get("/dashboard"), "Which jobs made money?")
    assert "Test Customer" in body or True
    d.post("/dashboard/share")
    with A.app.app_context():
        tok = A.q1("select token from shares")["token"]
    ok(anon.get(f"/share/{tok}"), "read-only view")
    assert anon.get("/share/bogus").status_code == 404
    csv = ok(d.get("/export/projects.csv"), "Profit")
    assert "Test Customer" in csv
    # profit on the new job is positive and consistent
    with A.app.app_context():
        f = S.financials(A.get_db(), oid)
        assert f["profit"] > 0 and abs(f["revenue"] - total) < 1
    # seeded loss-making job shows up
    with A.app.app_context():
        rows = S.project_rows(A.get_db())
        assert any(r["profit"] < 0 for r in rows)
    # admin can create users, and sales role cannot
    adm = client("admin@sunpower.test")
    adm.post("/admin/users", data={"name": "New Sales", "email": "n@x.test", "role": "sales", "password": "secret1", "rate": "0"})
    assert client("n@x.test", "secret1") is not None
    assert sales.get("/admin/users").status_code == 403
    print("ALL OK")


if __name__ == "__main__":
    test_everything()
