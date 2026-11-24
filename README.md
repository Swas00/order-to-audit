# Order-to-Audit System: small food manufacturer

A working prototype that takes a snack factory from a **salesman's order** to the **auditor's monthly report**.
Every transaction is entered once, at its source, and flows into stock, production, costing and accounts.
Built for the CDI NIT Tiruchirappalli internship problem statement ("Order-to-Audit System for a Small Food Manufacturer").

The part that makes the problem interesting is the production loop: **recipe (BOM) based production with
planned-vs-actual consumption and yield**. That is where most of the design effort went.

Built with an AI coding assistant, as section 7 of the problem statement permits.

**Live demo:** https://order-to-audit-207c.onrender.com (Render free plan: the first load after idle takes about a minute, and the demo data resets on every restart). The login page lists the test logins, or see the table below.

| Document | What it is |
|---|---|
| [docs/DESIGN.md](docs/DESIGN.md) | Design note (2 pages): ER diagram, how stock and BOM consumption are computed, costing and valuation |
| [docs/DEMO_SCRIPT.md](docs/DEMO_SCRIPT.md) | Timed script for the 8-minute demo video (section 5 scenario) |
| [docs/TRACEABILITY.md](docs/TRACEABILITY.md) | Every requirement and benchmark check, with how it was verified |
| [docs/INTERVIEW_GUIDE.md](docs/INTERVIEW_GUIDE.md) | Where things live, how to make likely live changes, and answers to likely questions |

---

## 1. Run it

### Option A: locally, no Docker (what I developed and tested with)

Needs a recent Python 3. Developed and tested on Python 3.15.2 / macOS; the Dockerfile uses Python 3.12, which I have not run.

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python manage.py migrate
.venv/bin/python manage.py seed_users
.venv/bin/python manage.py seed_masters
.venv/bin/python manage.py seed_inventory
.venv/bin/python manage.py runserver
```

Open http://localhost:8000. The default database is SQLite (`db.sqlite3`), so nothing else has to be installed.

### Option B: Docker Compose (PostgreSQL)

```bash
docker compose up --build
```

> **Not verified.** The `Dockerfile` and `docker-compose.yml` are written, but Docker is not installed on the machine
> this was built on, so I have never run them. The application itself is tested only against SQLite. If Compose fails,
> use Option A.

### Test logins (all passwords: `demo12345`)

| Username | Role | Device it is designed for | Can do |
|---|---|---|---|
| `salesman_a` | Salesman | Mobile | Take orders for **Retailer A**, see own orders, customer-wise sales, visit check-in |
| `salesman_b` | Salesman | Mobile | Same, for **Distributor B** |
| `admin` | Factory admin | Desktop | Approve/reject orders, GST invoices, dispatch, payments, credit notes, masters, all reports |
| `production` | Production supervisor | Desktop / tablet | Purchases, production runs, stock adjustments, overheads, production plan, stock reports |
| `accountant` | Accountant / auditor | Desktop, **read-only** | All financial reports and exports (CSV / Excel / PDF / Tally XML) |
| `owner` | Owner | Any | Dashboard, reports (read-only) |

These are dummy accounts created by `seed_users`. They must be deleted before any real use.

Accounts are created by an administrator (Django admin), not by self-registration, because every account carries a role that decides what it can reach. On a public demo deployment, set the environment variable `DEMO_LOGINS=1` and the login page lists these usernames and the shared password, with click-to-fill. It is off by default, so a normal install never shows credentials.

### Replay the benchmark yourself

On a **fresh** database (delete `db.sqlite3`, then migrate and seed as above):

```bash
.venv/bin/python manage.py run_benchmark
```

It performs section 5 steps 1-8 through the same service layer the screens use, then checks every row of the
"Results we will check" table and prints PASS/FAIL. (The demo video does the same steps by hand on screen.)

### Run the tests

```bash
.venv/bin/python -m pytest -q
```

308 tests, about 3 minutes. One of them runs 10 JavaScript unit tests through Node (skipped if Node is not installed).

---

## 2. Stack, and why

**Django 5 + a server-rendered UI (plain HTML/CSS and a little JavaScript), SQLite for development and PostgreSQL in Docker.**
One language and one framework carry the whole system: Django gives authentication, an ORM with migrations and a
ready-made admin for master data, so the effort could go into the stock, BOM and GST logic rather than plumbing.
The salesman screen is a plain mobile-first page with a service worker for offline use, not a separate single-page app,
which keeps the system small enough to explain and change live.

---

## 3. Benchmark results (section 5)

Master data is loaded by `seed_users`, `seed_masters`, `seed_inventory`. The scenario is replayed by `run_benchmark`
and asserted in `apps/core/tests.py::test_section_5_results_table`. **No number differs from the expected table.**

| Check | Expected | This system | Match |
|---|---|---|---|
| Flour closing balance | 120 kg (100 + 60 − 40) | 120 kg | yes |
| Oil closing balance | 19.5 L | 19.5 L | yes |
| Pouches closing balance | 306 (188 used + 6 damaged) | 306 | yes |
| Finished goods after run | 238 packets | 238 | yes |
| Finished goods after Retailer A dispatch | 88 packets | 88 | yes |
| Yield variance | 188 vs 200, −6%, flagged | 188 vs 200, −6.00%, **flagged** | yes |
| Oil consumption variance | +5%, flagged | +5.00%, **flagged** | yes |
| Retailer A invoice | ₹7,500 + ₹187.50 CGST + ₹187.50 SGST = ₹7,875 | identical | yes |
| Retailer A outstanding | ₹3,875 | ₹3,875.00 | yes |
| Distributor B order | shortage of 32 flagged; IGST if invoiced | 32 packets short, flagged; IGST at 5% (no CGST/SGST) when invoiced | yes |
| **Cost per packet** | my figure, with method and overhead rule stated | **₹31.14** (₹31.1436) | n/a |

Other figures the system also produces: spice closing 3.8 kg, cartons closing 21, run material cost ₹4,055, margin
₹18.86 per packet (37.71%) against the ₹50 price.

### How the cost per packet is derived (₹31.14)

- **Valuation method: weighted average**, recalculated on each purchase. Flour: (100 kg × ₹42 + 60 kg × ₹45) / 160 kg = ₹43.125/kg.
- **Materials for the run (₹4,055):** flour 40 × 43.125 = 1,725; oil 10.5 × 140 = 1,470; spice 1.2 × 200 = 240;
  pouches 194 × 2.50 = 485 (188 used + 6 damaged); cartons 9 × 15 = 135.
- **Overheads (₹1,800):** labour ₹1,200 + gas/power ₹600, charged to this run in full (they were entered against the run).
- **Cost per packet** = run cost ₹5,855 / **188 good packets** = ₹31.1436. Damaged pouches are part of the run cost, so waste raises the cost of the good packets.
- **Why weighted average:** it is stable when purchase prices move (₹42 then ₹45), needs no layer tracking, and is easy to defend and recompute from the ledger.
  For comparison only, calculated by hand and **not** implemented: FIFO would give ₹30.90 (flour 40 × 42 = 1,680) and last purchase price ₹31.54 (flour 40 × 45 = 1,800).

### Things in the benchmark that are open to interpretation, and what I chose

- **The 6 "unaccounted" packets.** Expected 200, good 188, damaged pouches 6. That leaves 6 packets neither good nor in a damaged pouch.
  The system reports the full shortfall of 12 (6.00%) and separately shows the 6 damaged pouches; the other 6 are visible as the gap.
- **Pouch consumption is 194, not 200** (−3.00%). That is exactly at the tolerance, which counts as within it (flag only when *strictly* greater).
- **Cartons used 9 against 10 planned (−10%)** is shown as a favourable *note*, not an alert, because the brief lists only yield and oil as flagged.
  Rule: only *adverse* variances beyond tolerance are flagged (more material than planned, less yield than planned).
- **Distributor B's shortage of 32** is the same whether or not Retailer A's order has been dispatched yet:
  approving Retailer A commits 150 of the 238 packets, leaving 88 available. This is why the coverage check subtracts packets
  already promised to other approved orders (stock itself still drops only at dispatch).
- **Dispatch takes opening stock first.** Finished goods are tracked per batch and dispatch is first-expiry-first-out, so Retailer A's 150
  packets are 50 from the opening batch plus 100 from the new batch. The totals above are unaffected.

---

## 4. What was built, against the section 6 tiers

Everything in all three tiers is implemented. "Where" gives the URL after login.

### Must have

| Requirement | Where / how |
|---|---|
| Login, at least three roles | 5 roles (salesman, admin, production, accountant, owner). Access enforced on the server for every view; tested route by route |
| Product, customer, raw-material and BOM masters | `/admin/` (admin: full edit; production: view only). Units with exact conversions, SKUs, BOM with batch yield, customers with GSTIN and state |
| Mobile-friendly order entry and admin approval | `/orders/new/` (phone), `/orders/<id>/` (admin approves, edits quantity/price, partial approval) |
| GST invoice as PDF | `/invoices/<id>/pdf/` |
| Production run updates raw and finished-goods stock automatically | `/production/` (issue materials, then complete the run) |
| Planned vs actual consumption and yield with variance | `/production/<id>/` |
| Stock summary and stock ledger | `/stock/`, `/stock/ledger/` |
| Sales register and stock report exportable to Excel/CSV | `/reports/sales-register/`, `/stock/` (CSV, Excel and PDF buttons) |

### Should have

| Requirement | Where |
|---|---|
| Purchases and supplier records | `/purchases/`; suppliers in `/admin/` |
| Payments, outstanding and ageing | `/payments/`, `/receivables/`, `/reports/outstanding/` |
| Cost per packet with overheads | `/costing/` |
| GST summary report (GSTR-1 / GSTR-3B shapes) | `/reports/gst-summary/` |
| Variance alerts beyond tolerance | Flagged on the run page; listed on the owner dashboard |
| Owner dashboard | `/dashboard/` (the owner lands here) |

### Stretch (all built)

| Stretch goal | Notes |
|---|---|
| Offline order capture with sync | Service worker + local queue + idempotent submit. Verified in real Chrome (page loads with the server down, order queues, survives reload, syncs by itself). See limitations |
| Batch and expiry tracking, first-expiry-first-out dispatch | Batch per production run; expired stock cannot be sold; expiry alerts; recall trace page `/batches/` |
| Reorder levels and low-stock alerts | Per-item level in master data; report `/reports/low-stock/`, stock page banner, dashboard |
| Production planning from pending orders | `/production/plan/`: "make N batches", with a materials shortage list |
| Salesman visit log with GPS check-in | `/visits/new/`; flags check-ins more than 300 m from the shop's recorded location |
| Tally-compatible export | `/reports/tally.xml`: Sales, Credit Note and Receipt vouchers, each balancing to zero |
| e-Invoice / e-Way Bill data format (mocked) | JSON buttons on each invoice. The IRN is the real SHA-256 rule; acknowledgement and QR are placeholders. Nothing is sent anywhere |
| Tamil interface for floor staff | English / தமிழ் switch in the header. Floor-staff screens only; **machine-drafted, needs native-speaker review** |

### Technical expectations (section 7)

| Expectation | How it is met |
|---|---|
| Real database, stock derived from a ledger | Stock balance is the sum of an **append-only** `StockMovement` table. Movements cannot be edited or deleted (enforced in code); mistakes are fixed by a reversal movement |
| No floats for money or quantities | `Decimal` everywhere; rounding is half-up to the paisa per invoice line. Tests include awkward amounts (e.g. ₹0.10 + ₹0.20 + ₹33.33…) |
| Traceability | Every movement points to its source document (purchase, production run, dispatch, credit note or adjustment) |
| Audit trail | Starts, issues, completions, voids, approvals (with before/after of edited quantities and prices), invoices, dispatches, cancellations and payments are logged with user and time. Posted invoices are **cancelled by credit note, never deleted or edited** |
| Seed data | `seed_users`, `seed_masters`, `seed_inventory` (all re-runnable) |
| Deployability | Local run command above; Docker Compose written but unverified |

---

## 5. Assumptions

Where the brief was silent I chose, and wrote it down. Invented values are marked *(invented)*.

**Production and inventory**
- **Two-stage consumption.** Raw materials are consumed when issued to the run; packaging and damage are recorded when the run is completed.
  The pouch is derived: one per good packet plus the damaged ones as wastage. Finished goods are counted in packets; cartons are a consumable.
- **Variance:** tolerance is 3% globally (per-SKU override possible). A line is flagged only if the variance is adverse and *strictly* greater than the tolerance; favourable variances beyond tolerance are notes.
- **Planned quantities are snapshotted** when a run starts, so editing a BOM later never rewrites history.
- A BOM belongs to one SKU (pack size). A second pack size is a new SKU with its own BOM; see the interview guide.
- Opening finished goods are loaded at cost ₹0 *(invented: the brief gives none)* as one batch assumed to expire 60 days after loading *(invented)*.
  New batches get a 90-day shelf life *(invented, per-product setting)*.
- Reorder levels flour 50 kg, oil 20 L, spice 2 kg, pouches 200, cartons 25 *(invented; chosen so the benchmark trips two visible alerts)*.

**Sales and billing**
- Approving an order does not move stock; **dispatch** does. Coverage at approval counts only packets not already promised to other approved orders. An order cannot be approved above available stock; the admin can approve the available quantity (partial approval) or reject.
- A salesman can only order for customers assigned to them.
- Invoice numbers are `INV/<financial year>/<4 digits>` (financial year April-March), gap-free per year. Credit notes use `CN/...`. Totals are rounded to the nearest rupee with a round-off line.
- An invoice can be **dispatched in several lots** (partial dispatch; the order shows *invoiced* until every packet has gone, then *dispatched*) and **credited in part** (a partial return): a credit note names the packets, restocks them if they physically come back (into the batches they left from, at the cost they left at) and reduces what the customer owes. Cancelling an invoice credits whatever has not yet been credited; when every packet is credited the invoice becomes *cancelled* and its order returns to *approved*. The credit notes of one invoice always add up to the invoice to the paisa. A payment made before cancellation stays on the customer's account as credit.
- Payments can be applied to one invoice (never above what is owed) or, if no invoice is chosen, clear the oldest invoices first with any remainder kept as an advance.
- Ageing runs from the **invoice date** (no credit terms are given). "Customer-wise sales" means approved orders, before GST.
- GSTINs, the HSN code `19059030`, pincodes, phone numbers and shop coordinates are **invented** dummy values in the right format. The 5% GST rate is the brief's. The HSN code is a placeholder and has not been checked against the official schedule.

**Costing**
- Weighted-average valuation; cost per packet = run cost / good packets.
- Overheads entered against a run are charged to it in full. Overheads entered as a monthly pool are shared by the runs completed that month in proportion to good packets.
- Finished-goods stock is valued at **material cost only** (overheads arrive after the run, and the ledger is append-only). The full cost per packet is a costing-report figure.

**Other**
- Time zone is Asia/Kolkata. Report date filters default to the current month.
- The language choice (English / Tamil) is stored per device in a cookie, not per user.

---

## 6. Known gaps and limitations

Stated plainly, so nothing here is a surprise.

**Not verified**
- **Docker Compose / PostgreSQL has never been run.** The app is tested on SQLite only. Row locks that protect against two people acting on the same stock at once only take effect on PostgreSQL; I did not run a concurrency load test.
- No hosted demo link (run command only).
- I have not viewed the admin approval, invoice and report pages on a desktop browser visually (their content is covered by tests). The PDF text was checked, not its visual layout.
- The browser location prompt for GPS check-in was not tried on a real phone. iOS Safari was not tried at all.

**Missing or simplified**
- Login is throttled (5 wrong passwords per username and address lock it for 15 minutes), but the counter lives in the process cache, so with several worker processes the limit applies per process. The demo users share one public password (users can change theirs at `/password/`).
- Returns are credit-led: a credit note can restock packets, but there is no replacement-goods flow, and a credit note reduces quantity only (no price-difference credit note).
- Only weighted-average costing is implemented (FIFO and last-price are discussed above, not built).
- Batches and expiry apply to finished goods only, not raw materials. Dispatch cost uses the SKU's average cost, not the specific batch's cost.
- Master data is edited in Django's admin site, not custom screens.
- Offline mode covers **order entry only**; the order list and sales views need a connection. Queued orders are sent only while the app is open (no background sync), and live in the browser's local storage (lost if site data is cleared). The service worker was verified in desktop Chrome only.
- The Tamil text is my draft and **needs review by a native speaker**. Only the floor-staff screens, the shared header and their messages are translated; master-data names (e.g. "Cooking oil"), PDFs and exports stay English.
- The Tally XML, e-Invoice JSON and e-Way Bill JSON follow the published field names as best I know them but have **not** been validated by Tally or the government portals.
- Passwords in the seed are for demonstration only; there is no password-reset flow.
- A database created before batch tracking was added cannot be migrated in place (there is no production data; use a fresh database and the seed).
- Accessibility was not audited (form fields have labels, but no screen-reader or keyboard testing was done).

**Measured**
- With 20,000 ledger movements for one item: balance in about 2 ms, weighted-average cost in about 26 ms, issuing stock in about 29 ms. Producing the all-items stock ledger report at that size took about 3 seconds when I measured it.

---

## 7. How it is tested

308 automated tests, all executed (results are what you get when you run the command above):

- **Stock and tax maths:** unit conversion, ledger balances, weighted-average cost, FEFO allocation, GST per line, round-off, financial-year boundaries, amount in words.
- **The benchmark:** every row of the section 5 results table, plus a test that adding a 500 g pack size does not disturb it.
- **Negative and security tests:** every route requires login (checked for all routes); a table of pages and write actions each role must *not* reach; CSRF enforcement; escaping of hostile input; garbage and oversized inputs; unauthorised access to other salesmen's data.
- **Integration:** the full order-to-invoice-to-dispatch-to-payment flow, voids and reversals, credit notes, exports.
- **Offline logic:** 10 JavaScript tests of the queue and sync logic (run through Node) plus the server-side API tests.
- **Translation integrity:** every string translated, placeholders intact, compiled file current.
- A `pip-audit` of the pinned dependencies reported no known vulnerabilities (run once, during development).

Not covered by automated tests: browser rendering and layout (checked by hand in a browser for the salesman order page, the offline flow and two Tamil pages), real-device GPS, and Docker.

---

## 8. Repository layout

```
config/            Django settings and URL routes
apps/accounts/     Users and the five roles; server-side access decorators
apps/masters/      Units, items, SKUs, BOM, customers, company profile; unit conversion and BOM scaling
apps/inventory/    Append-only stock ledger, batches, FEFO, adjustments, stock reports
apps/purchasing/   Suppliers and purchases
apps/production/   Production runs, variance rules, planning
apps/sales/        Orders, approval, stock coverage, offline JSON API, visit log
apps/billing/      GST invoices, PDF, dispatch, payments, credit notes, Tally, e-Invoice
apps/costing/      Overheads, cost per packet and margin
apps/reports/      All reports (CSV/Excel/PDF) and the owner dashboard
apps/audit/        Audit log
apps/core/         Exports, PDF tables, service worker, benchmark module, shared tests
templates/         HTML templates
locale/ta/         Tamil translation
docs/              Design note, demo script, traceability, interview guide
```

Commit history is mostly one commit per milestone (plus a few follow-up fixes), so it can be read as a story.
