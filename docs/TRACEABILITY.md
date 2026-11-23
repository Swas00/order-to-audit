# Requirement traceability and completion report

Every requirement from the problem statement, where it is implemented, and **how it was verified**.
Evidence is either an automated test (name the file), a command you can run, or a manual check I performed (stated as such).
Nothing is marked done without one of these. Status words: **Done** (implemented and verified as described), **Done, partly verified** (see note), **Not done**.

Test files are under `apps/<app>/`. Run everything with `.venv/bin/python -m pytest -q`.

## A. Benchmark results (section 5): 30% of the score

All eleven are asserted in `apps/core/tests.py::test_section_5_results_table` and printed by `manage.py run_benchmark`.

| ID | Check | Expected | This system | Status |
|---|---|---|---|---|
| B-01 | Flour closing | 120 kg | 120 | Done |
| B-02 | Oil closing | 19.5 L | 19.5 | Done |
| B-03 | Pouches closing | 306 | 306 | Done |
| B-04 | Finished goods after run | 238 | 238 (measured right after the run, before dispatch) | Done |
| B-05 | Finished goods after Retailer A dispatch | 88 | 88 | Done |
| B-06 | Yield variance | −6%, flagged | −6.00%, flagged | Done |
| B-07 | Oil variance | +5%, flagged | +5.00%, flagged | Done |
| B-08 | Retailer A invoice | ₹7,500 + 187.50 + 187.50 = ₹7,875 | identical | Done |
| B-09 | Retailer A outstanding | ₹3,875 | ₹3,875.00 | Done |
| B-10 | Distributor B | shortage of 32 flagged; IGST if invoiced | 32 short; IGST at 5% | Done |
| B-11 | Cost per packet | own figure, method stated | ₹31.14 (weighted average; overhead rule in README) | Done |

## B. Roles (section 3)

| ID | Requirement | Where | Verified by | Status |
|---|---|---|---|---|
| R-01 | Login with at least 3 roles | 5 roles, `apps/accounts` | `accounts/tests.py` (login for every role, bad password) | Done |
| R-02 | Each role sees only what it needs | per-view `roles_required` | `core/test_security.py`: pages and write actions each role must not reach return 403; every route needs login | Done |
| R-03 | Role-based access is part of the requirement | server-side on every view | same; plus per-feature role tests in each app | Done |

## C. Functional requirements (section 4)

| ID | Requirement | Where | Verified by | Status |
|---|---|---|---|---|
| R-04 | Mobile order entry: pick customer, add products and quantities | `/orders/new/` | `sales/tests.py`; viewed on a 375 px browser window | Done |
| R-05 | Price from price list; customer-specific price optional | `apps/sales/services.py::price_for`, `CustomerPrice` | `sales/tests.py::test_customer_specific_price_overrides_list` | Done |
| R-06 | Order goes straight to the factory | order is created in the same system | `sales/tests.py` | Done |
| R-07 | Offline capture and sync (strongly preferred) | `sw.js`, `offline.js`, `sales/api.py` | `sales/test_offline.py` (27 tests), `sales/jstests/offline.test.js` (10 tests); **manual, in real Chrome:** page loads with server down, order queues, survives reload, syncs by itself | Done, partly verified (desktop Chrome only; no iOS; sync only while the app is open) |
| R-08 | Salesman sees order status and customer-wise sales | `/orders/`, `/sales/customers/` | `sales/tests.py` (scoping to own orders) | Done |
| R-09 | Admin edits quantities or prices, approves or rejects | `/orders/<id>/` | `sales/tests.py` (edits audited, reject needs reason, final) | Done |
| R-10 | Stock coverage shown before approval | coverage table | `sales/tests.py::test_benchmark_distributor_b_shortage_of_32` and others | Done |
| R-11 | GST invoice: GSTIN, HSN, taxable value, CGST/SGST or IGST, FY numbering | `apps/billing` | `billing/tests.py` (tax maths, FY boundary, sequence restart, snapshot, DB constraint) | Done |
| R-12 | PDF, shareable by WhatsApp link or email | PDF + WhatsApp/email buttons + unlisted token link | `billing/tests.py::test_invoice_pdf_is_a_valid_pdf`, `test_shared_pdf_link_needs_the_token_not_a_login`; PDF text read back and checked; **PDF layout not eyeballed** | Done, partly verified |
| R-13 | Dispatch reduces finished-goods stock; partial dispatch and returns (optional) | `dispatch_invoice` (lots), `issue_credit_note` (partial returns) | `billing/tests.py` (benchmark 238 → 88, refuses if short, atomic); `billing/test_partial.py` (lots, FEFO per lot, over-dispatch, partial and final credit notes add up exactly, restock to original batches, reports, Tally, screens, roles, upgrade of old data) | Done |
| R-14 | Payments against invoices; outstanding per customer | `/payments/`, `/receivables/` | `billing/tests.py` (benchmark ₹3,875, overpay refused, oldest-first, advance) | Done |

## D. BOM and production (section 4.3), the most heavily weighted module

| ID | Requirement | Where | Verified by | Status |
|---|---|---|---|---|
| R-15 | BOM: standard quantities per batch plus expected yield | `Bom`, `BomLine` | `masters/tests.py` (scaling, unit conversion, one active BOM per SKU) | Done |
| R-16 | Run records product, batch size/issue, expected, actual, wastage | `ProductionRun`, `RunLine` | `production/tests.py` | Done |
| R-17 | Saving a run updates raw and finished-goods stock automatically | `complete_run` | `production/tests.py` (benchmark steps 2-3; atomic if pouches run out) | Done |
| R-18 | Planned vs actual consumption and yield; flag beyond tolerance | `variance.py` | `production/tests.py` (rule edge cases, benchmark variances) | Done |
| R-19 | Unit conversion: kg, g, litre, pieces, packets, cartons | `masters/units.py` | `masters/tests.py`; purchase and issue in other units in `inventory/tests.py` | Done |

## E. Inventory and costing (4.4, 4.5)

| ID | Requirement | Where | Verified by | Status |
|---|---|---|---|---|
| R-20 | Separate balances: raw, packaging, finished goods (WIP optional) | stock summary by group | `inventory/tests.py`, `reports/tests.py`. WIP **not modelled** (optional) | Done |
| R-21 | Opening + receipts − issues ± adjustments = closing | `stock_summary` | `inventory/tests.py::test_summary_equation_holds`, `reports/tests.py::test_stock_reports_after_benchmark` (every row) | Done |
| R-22 | Stock ledger with every movement and its source | `/stock/ledger/` | `inventory/tests.py` (every movement has a source; immutability) | Done |
| R-23 | Batch numbers and expiry (strong plus) | `Batch`, FEFO | `inventory/test_batches.py` (23 tests) | Done |
| R-24 | Material and packaging cost per run, method chosen and justified | weighted average | `costing/tests.py`; justification in README and DESIGN.md | Done |
| R-25 | Overheads allocated by a stated rule | per run in full; monthly pool by good packets | `costing/tests.py` (exact allocation, voided, unallocated) | Done |
| R-26 | Cost per packet and margin vs selling price | `/costing/` | `costing/tests.py` (benchmark ₹31.1436, 37.71%) | Done |

## F. Reports (4.6)

Each report has a date range and exports to CSV, Excel and PDF. `reports/tests.py::test_every_report_renders_and_exports_in_every_format` renders and exports all of them.

| ID | Report | Verified by | Status |
|---|---|---|---|
| R-27 | Sales register; customer-wise and product-wise sales | `reports/tests.py` (benchmark values, credit-note netting) | Done |
| R-28 | GST summary mapping to GSTR-1 / GSTR-3B | `reports/tests.py::test_gst_summary_*` (3B block, B2B, B2C, credit notes, HSN). Field layout follows the published GSTR-1 tables as I understand them; **not validated by the GST portal** | Done, partly verified |
| R-29 | Purchase register | `reports/tests.py` | Done |
| R-30 | Stock summary and stock ledger | `reports/tests.py`, `inventory/tests.py` | Done |
| R-31 | Production report: planned vs actual, yield, wastage | `reports/tests.py::test_production_report` | Done |
| R-32 | Collections; outstanding by customer with ageing | `reports/tests.py`, `billing/tests.py::test_ageing_buckets` | Done |
| R-33 | Filterable by date range, exportable to Excel/CSV and PDF | as above; `reports/tests.py::test_date_range_filter_excludes_other_periods` | Done |

## G. Should-have and stretch (section 6)

| ID | Item | Status | Verified by |
|---|---|---|---|
| R-34 | Purchases and supplier records | Done | `inventory/tests.py`; suppliers via `/admin/` |
| R-35 | Owner dashboard | Done | `reports/tests.py::test_owner_dashboard_*` |
| R-36 | Reorder levels and low-stock alerts | Done | `production/test_stretch.py` |
| R-37 | Production planning from pending orders | Done | `production/test_stretch.py` |
| R-38 | Salesman visit log with GPS check-in | Done, partly verified | `sales/test_visits.py`; **real-device GPS not tried** |
| R-39 | Tally-compatible export | Done, partly verified | `billing/test_exports.py` (every voucher balances; parses back). **Not imported into real Tally** |
| R-40 | e-Invoice / e-Way Bill data (mock) | Done, partly verified | `billing/test_exports.py` (real IRN hash; structure). Mock only; **not validated against the government schema validator** |
| R-41 | Tamil interface for floor staff | Done, partly verified | `core/test_tamil.py`; two screens viewed in a browser. **Text is machine-drafted and needs native review**; floor-staff screens only |

## H. Technical expectations (section 7)

| ID | Expectation | Status | Verified by |
|---|---|---|---|
| T-01 | Real database; stock derived from a ledger, never an overwritten number | Done | `StockMovement` append-only; `inventory/tests.py::test_movements_are_immutable` (instance save/delete and bulk update/delete all refused) |
| T-02 | Decimal or integer arithmetic for money and quantities | Done | `billing/tests.py::test_rounding_*`, `test_money_sums_do_not_drift` |
| T-03 | Every movement links to its source | Done | DB requires a source; `inventory/tests.py::test_every_movement_has_a_source_document`; `production/tests.py::test_every_run_movement_links_to_the_run` |
| T-04 | Audit trail; invoices cancelled by credit note, never deleted | Done | `production/tests.py` (void history), `sales/tests.py` (edits before/after), `billing/tests.py::test_cancel_*` (invoice kept) |
| T-05 | Seed script for the section 5 master data | Done | `seed_users`, `seed_masters`, `seed_inventory`; `core/tests.py::test_seed_commands_are_rerunnable` |
| T-06 | Hosted demo or a single command to run locally | **Partly** | Local run commands documented and used. **No hosted demo. Docker Compose is written but has never been run** |
| T-07 | Tally not required for daily work | Done | Tally is an optional export only |
| T-08 | Stack justification in the README | Done | README section 2 |
| T-09 | Tests for stock and tax maths | Done | see sections C-E; 306 tests in total |
| T-10 | Only dummy and invented data | Done | all GSTINs, names, addresses and coordinates are invented and marked so in the README |

Security and robustness (not a numbered requirement, but you asked for it): route-by-route login and role tests, CSRF, escaping, hostile and oversized input, and `pip-audit` (clean when run). `core/test_crawl.py` logs in as every role on a populated database, follows every link and fails on any that errors for that role (this found the production role being shown links to pages it may not open), and opens every Django admin page. Defects found and fixed this way include server errors on non-numeric ids and database overflow on huge numbers (`core/test_security.py`).

## I. Deliverables (section 8)

| ID | Deliverable | Status | Notes |
|---|---|---|---|
| D-1 | Public Git repository with a clear commit history | **Not yet done by me** | The history exists locally (mostly one commit per milestone). **You must push it to a public GitHub or GitLab repository.** |
| D-2 | README: setup, stack and why, tiers, assumptions, gaps, benchmark numbers | Done | `README.md` |
| D-3 | Design note, max 2 pages: ER diagram, stock and BOM computation, costing and valuation | Done | `docs/DESIGN.md`. Mermaid diagram renders on GitHub/GitLab. **Check the length when you render it; export to PDF if you prefer** |
| D-4 | Demo video, max 8 minutes, section 5 in order, mobile and admin views | **Not done: needs you** | Script ready: `docs/DEMO_SCRIPT.md` (targets 7:55). Every step was dry-run through the real screens |
| D-5 | Live link or run command, with test logins for each role | Partly | Run command and test logins in the README; no live link |
| D-6 | Work alone | n/a | Individual task |

## J. Evaluation weights (section 9)

| Criterion | Weight | Where to look |
|---|---|---|
| Benchmark correctness | 30% | Section A; `manage.py run_benchmark` |
| Production and inventory design | 20% | `docs/DESIGN.md` sections 2-3; `apps/inventory`, `apps/production`, `apps/masters/bom.py` |
| Sales-to-invoice flow | 15% | `apps/sales`, `apps/billing` |
| Data model and code quality | 15% | `docs/DESIGN.md` section 2; services-layer structure; tests |
| Usability | 10% | `/orders/new/` on a phone: pick customer, type quantity, submit (three actions). Not timed with a stopwatch |
| Reports and documentation | 10% | Section F; README; this folder |
| Stretch goals | up to +10% | Section G: all eight stretch goals built |

## K. Open items before you submit

1. **Push the repository** to a public GitHub or GitLab repository (D-1).
2. **Record the demo video** with `docs/DEMO_SCRIPT.md` and upload it unlisted (D-4).
3. **Run Docker Compose** on a machine with Docker, or remove the Docker claims (T-06).
4. Put your own words in the README (the one-line AI-assistance note is mine) and be ready to explain every part: `docs/INTERVIEW_GUIDE.md`.
5. Have the Tamil strings reviewed by a native speaker, or say so plainly as the README does.
6. Fill in the dates, contact and submission form: section 10 of the brief has placeholders only, so I do not know your deadline.
