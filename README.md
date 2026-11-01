# Solar Installation Management System

One platform for a rooftop-solar company, from the first web enquiry to the 20-year panel warranty.
Built for the Odoo Hackathon final round, problem statement "Solar Installation Management System" (SunPower Solutions).

## Run it

```bash
pip install -r requirements.txt
python app.py               # http://localhost:5000  (database and demo data are created on first start)
python tests/test_flow.py   # end-to-end test of the whole business flow and role walls
python manage.py reset      # rebuild demo data any time
```

Docker: `docker build -t solar-erp . && docker run -p 5000:5000 solar-erp`

Demo logins (password `demo123`, also listed on the sign-in page):

| Role | Email | Lands on |
|---|---|---|
| Admin | admin@sunpower.test | Everything, plus users, prices, package templates |
| Sales executive | sales@sunpower.test | Leads, quotations, order confirmation |
| Survey technician | survey@sunpower.test | My surveys |
| Warehouse manager | warehouse@sunpower.test | Stock, scan and dispatch |
| Installation technician | installer@sunpower.test | My jobs, completion form |
| Service manager | service@sunpower.test | Tickets, warranty lookup |
| Accountant | accountant@sunpower.test | Invoices, payments, expenses, subsidy, technician pay |
| Director | director@sunpower.test | Dashboard, shareable report |

Public pages (no login): `/enquiry` (customer web form) and `/warranty` (serial number warranty check).

## How each scene in the problem statement is handled

| Scene | What the system does |
|---|---|
| The rooftop inquiry | Web form, phone and WhatsApp enquiries are stored as leads and auto-assigned to the least-loaded sales executive. The call checklist (shadow-free area, bill, grid) is recorded. A survey can only be booked after the call qualifies the lead, and **a quotation is blocked until the survey is approved.** |
| Getting the kit right | The quotation is created from a package template (On-Grid / Off-Grid / Panel Set, 1 to 5 kW). The full bill of materials (panels, inverter, charge controller, boxes, stands, cables, earthing, arrestor, switches, installation) is copied automatically. Confirming the order needs an advance payment, creates the invoice, and the warehouse pick list and the installer's schedule update by themselves. |
| Dispatch day | Every panel, inverter, battery and charge controller has a serial number (QR labels are printable). Dispatch rejects wrong, duplicate, unavailable or missing serials, so each unit is tied to exactly one customer. Marking delivery starts the warranty on every serial: 24 months for inverter, battery and controller, 240 months for panels (configurable per product). |
| On the roof | Technician form for phone or tablet: start and finish time, system size, power test reading, photos, on-screen customer signature. The job cannot be completed without a photo and a signature. |
| Rainy-season breakdown | Search by phone, name, order or serial. The ticket shows the serial and whether it is in or out of warranty. In warranty means a free repair or replacement (parts are booked as a warranty cost on the job); out of warranty means parts plus labour are invoiced automatically. Replacement serials get a fresh warranty and the old unit is marked faulty. The ticket stays open until the customer confirms. |
| Director's month-end | Dashboard for today, this week and this month: enquiries, orders, cash, profit, open repairs, unfinished jobs, dues, subsidy in progress, low stock, profit per job (losses in red) and a six-month trend. Read-only share link (7 days, revocable) and CSV export. The accountant gets invoices, payments, expenses, a subsidy tracker, and technician pay from recorded hours. |

## Architecture

```
app.py        Flask routes, role permissions, page assembly
services.py   All business rules (qualification gate, BOM copy, serial dispatch, warranty, billing, profit)
db.py         SQLite schema, product catalogue, demo data (built through the real services)
templates/    Jinja pages        static/   CSS and small JS (signature pad, camera QR scan)
tests/        End-to-end test of the full flow and role walls
```

Rules live in `services.py` and raise readable errors, so routes stay thin and the same logic is used by the web app, the demo data and the tests.

Core data flow: `leads -> surveys -> quotes(+lines) -> orders(+lines) -> serials -> installs(+photos) -> tickets(+parts)`, with `invoices`, `payments`, `expenses`, `activity` (role alerts) and `shares`.

Profit per job = invoice value (plus any service invoices) minus parts cost at order time, technician hours x hourly rate, and job-linked expenses (transport, warranty replacements). Company-level stock purchases are recorded as expenses but are not double-counted against jobs.

## Mapping to Odoo

| This app | Odoo equivalent |
|---|---|
| Leads | CRM `crm.lead` |
| Quotation, order, invoice, payments | Sales `sale.order`, Accounting `account.move`, `account.payment` |
| Packages and BOM | Product kits / MRP `mrp.bom` |
| Stock, serials, dispatch | Inventory `stock.lot` (lots and serials), `stock.picking` |
| Installation job, survey | Field Service `project.task` with worksheet and signature |
| Warranty and tickets | Helpdesk `helpdesk.ticket` with serial and warranty fields |
| Director dashboard | Odoo dashboards / spreadsheet reporting |

This is a standalone working build, so the whole flow can be demonstrated end to end. The data model and rules map directly onto Odoo models.

## Production notes

Set `SECRET_KEY`. Serve with gunicorn (see Dockerfile) behind HTTPS. Add CSRF tokens (for example Flask-WTF) and move from SQLite to PostgreSQL before real use. Uploaded photos live in `instance/uploads`.
