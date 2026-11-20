# Order-to-Audit System: Decisions and Plan (Phase 2-3)

Source: "Problem Statement - Winter Internship Application - 2026" (7 pages). Status of every item below: NOT STARTED.

## 1. Decisions (delegated by the candidate; each goes into the README assumptions)

| # | Topic | Decision | Why |
|---|---|---|---|
| Q1 | Stack | Python 3 + Django 5, PostgreSQL 16, server-rendered templates with HTMX and plain CSS, Docker Compose | One language end to end, built-in auth/ORM/migrations, fewest moving parts, easiest to explain and modify live |
| Q2 | Offline orders | Attempt after all must/should items pass: PWA service worker, IndexedDB queue, idempotent submit via client UUID | Brief lists it as stretch but "strongly preferred" |
| Q3 | Stretch order | 1) offline, 2) reorder alerts, 3) batch/expiry + FEFO, 4) production planning. Skip Tally, e-invoice, Tamil, GPS unless time remains | Ordered by value per effort |
| Q4 | Costing | Weighted-average cost, recalculated on each purchase; cost per packet = run cost / good packets | Stable and simple to defend; damaged pouches fall into run cost |
| Q5 | Stock | Stock check shown at approval; no reservation; deduct at dispatch. Shortage: admin may approve a partial quantity or reject; invoice only for what is approved | Avoids phantom reservations; matches B-10 |
| Q6 | Consumption | Two stages: raw materials consumed at issue to the run; packaging and damage recorded at completion; FG held in packets; cartons are a consumable | Matches benchmark steps 2-3 and the 306 pouch check |
| Q7 | Tolerance | Global 3% default, per-product override | Matches brief example |
| Q7b | Variance flags (confirmed by candidate after M4) | Only adverse variances beyond tolerance are FLAGGED (more material than planned, less yield than planned); favourable ones beyond tolerance are shown as notes; exactly at tolerance is OK | Benchmark lists only yield and oil as flagged; cartons (-10%) must not be an alert |
| Q8 | Reports | CSV/XLSX for all reports; PDF for invoice, GST summary, stock summary, production report | Meets must-haves without PDF bloat |
| Q9 | Hosting | `docker compose up` is the primary run path; hosted demo only if time remains | Meets Deployability bullet |
| Q10 | Deadline | UNKNOWN: candidate must confirm | Brief placeholders unfilled |
| Q11 | Tests | pytest: heavy on stock ledger, unit conversion, tax and costing maths, plus one end-to-end benchmark test; lighter on views | Evaluation scores stock and tax tests |
| Q12 | Scope | Must-haves, then should-haves, then stretch. No stretch until the benchmark test passes | Brief's own rule |

Detail decisions: money as Decimal, invoice lines rounded half-up to paise; quantities as Decimal(14,4) in the item's base unit with a conversion table; invoice number `TN/2026-27/0001` per financial year (April-March); GSTIN/HSN values are invented but format-valid; round-off line on invoices; credit note reverses accounts and, if goods return, stock; salesman sees only own orders; admin cannot edit a posted invoice, only cancel by credit note; all invented data.

## 2. Architecture (summary)

- Apps: `accounts` (users/roles), `masters` (items, units, products, customers, price list, BOM), `inventory` (append-only `StockMovement` ledger; balance = sum), `production`, `sales` (orders, invoices, credit notes, dispatch, payments), `purchasing`, `costing`, `reports`, `audit` (change log).
- Every `StockMovement` has item, signed base-unit quantity, unit cost, timestamp, and a generic reference to its source document (purchase, production run, invoice/dispatch, adjustment). Balances are never edited; a reconciliation command checks derived vs expected.
- Variance computed from BOM scaled to batch size: consumption variance % = (actual - planned)/planned; yield variance % likewise; flag if |x| > tolerance.
- RBAC: role-based permission decorators/mixins server-side; auditor and owner read-only.
- Audit: model-change log (user, time, before/after) on invoices and runs.
- Error handling: transactional services (`atomic`), validation in service layer, friendly form errors.
- Security: Django CSRF/auth, password hashing, per-role queryset scoping, no secrets in repo (`.env.example`).

## 3. Milestones

1. Scaffold, Docker Compose, Postgres, roles/login, seed script (section 5 master data)
2. Masters + units + BOM
3. Inventory ledger + purchases
4. Production runs + variance
5. Orders (mobile) + admin approval + stock check
6. GST invoice + PDF + dispatch + payments + credit notes
7. Costing
8. Reports + CSV/XLSX/PDF export + owner dashboard
9. Benchmark end-to-end test; fix defects
10. Stretch: offline, reorder alerts, etc.
11. README, design note, demo script

## 4. Traceability matrix (requirement -> milestone -> verification)

| Req | Milestone | Verification |
|---|---|---|
| R-01..03 roles/RBAC | 1 | permission tests per role |
| R-04..08 order entry/status | 5 | view tests + manual mobile check |
| R-09..13 approval/invoice/dispatch | 5-6 | tax maths tests, B-08, B-05 |
| R-14 payments | 6 | B-09 |
| R-15..19 BOM/production/units | 2,4 | unit conversion + variance tests, B-06, B-07 |
| R-20..22 inventory/ledger | 3 | ledger tests, B-01..B-04 |
| R-24..26 costing | 7 | costing tests, B-11 |
| R-27..33 reports/export | 8 | export tests |
| R-34, R-35 purchases, dashboard | 3, 8 | tests/manual |
| B-10 shortage + IGST | 5-6 | tax test with Karnataka |
| T-01..T-10 | 1-9 | per item |
| D-1..D-5 | 1, 11 | checklist |
| R-07, R-23, R-36..R-41 stretch | 10 | per item |
