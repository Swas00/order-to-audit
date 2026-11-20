# Interview guide

For the 30-minute interview: a live walkthrough, questions on design choices, and **a small change made on the spot**.
Read this once, then open the code beside it. Everything here points at code that exists.

## 1. The system in one minute

Two loops meet at finished-goods stock.

- **Sales loop:** salesman order (`apps/sales`) → admin approval with stock check → GST invoice (`apps/billing`) → dispatch (stock goes down) → payment and outstanding.
- **Production loop:** purchase (`apps/purchasing`) → issue raw material against a BOM → complete the run (stock goes up, in a batch) → variance and cost per packet (`apps/production`, `apps/costing`).
- **One idea underneath:** stock is the sum of an **append-only ledger** (`apps/inventory`). Nothing overwrites a balance; every movement points to its source document.

If you can explain those three lines and draw the ER diagram in [DESIGN.md](DESIGN.md), you have covered most questions.

## 2. Where things live

| I want to look at... | File : function |
|---|---|
| The ledger model, immutability, batch rule | `apps/inventory/models.py` : `StockMovement`, `Batch` |
| Receive / issue / adjust / reverse stock | `apps/inventory/services.py` : `receive`, `issue`, `issue_fefo`, `adjust`, `reverse_source` |
| Weighted-average cost | `apps/inventory/services.py` : `average_cost` |
| FEFO allocation, expired stock | `apps/inventory/services.py` : `batches_with_stock`, `_allocate`, `saleable_balance` |
| Unit conversion | `apps/masters/units.py` : `convert` |
| BOM scaling | `apps/masters/bom.py` : `planned_consumption` |
| Variance rule (flag / note / ok) | `apps/production/variance.py` : `variance` |
| Production run lifecycle | `apps/production/services.py` : `start_run`, `issue_material`, `complete_run`, `void_run` |
| Production planning | `apps/production/planning.py` : `plan` |
| Order approval and stock check | `apps/sales/services.py` : `coverage`, `approve_order` |
| Offline submit endpoint | `apps/sales/api.py` : `submit_order`; browser side `apps/sales/static/sales/offline.js` |
| GST maths | `apps/billing/tax.py` : `line_tax`, `round_to_rupee`, `financial_year` |
| Invoice, dispatch, cancel, payment | `apps/billing/services.py` : `raise_invoice`, `dispatch_invoice`, `cancel_invoice`, `record_payment` |
| Cost per packet | `apps/costing/services.py` : `run_cost`, `allocate`, `sku_costs` |
| Reports | `apps/reports/builders.py` (one function per report), `apps/reports/views.py` (registry) |
| Who may do what | `apps/accounts/permissions.py` : `roles_required` (used on every view) |
| The benchmark | `apps/core/benchmark.py`, `apps/core/tests.py` |

## 3. Likely live changes, and how to do them

### "Add a second pack size of 500 g sharing the same BOM"  (the example in the brief)

**This needs no code.** It is master data, done in `/admin/` as the `admin` user. It is proven by
`apps/production/test_new_pack_size.py`, which does exactly this and then runs it through production, sales, invoice, dispatch and costing.

1. **Items > Add:** the new packaging, e.g. `500 g pouch` (kind *Packaging*, unit *piece*) and, if the carton size differs, `Carton (holds 10 packets)`.
2. **SKUs > Add:** code `MUR500`, product `Murukku`, pack size `500 g`, same HSN and GST rate, its own selling price, `packets per carton`, and **pouch item** = the new 500 g pouch.
3. **BOMs > Add:** SKU `MUR500`, **batch yield** = packets one batch makes (the same 40 kg dough makes 80 × 500 g, so `80`). Add the lines: the *same* flour, oil and spice quantities as the 200 g recipe, plus the new pouch (80) and carton (8).

Be ready to say **why it is not literally "the same BOM object"**: a BOM belongs to one SKU because packaging and yield differ by pack size, and variance is judged per SKU. If asked for a shared recipe, the honest answer is the BOM would be split into a *recipe* (dough materials) and a *packing spec* per SKU. That is a larger refactor, not an on-the-spot change.
The admin action *"Copy selected BOM(s) to a new inactive version"* in BOM admin copies a recipe quickly (for the same SKU).

### Small code or setting changes the interviewer might ask for

| They ask | Do this |
|---|---|
| "Change the variance tolerance to 5%" | `/admin/` > Company profile > *Global variance tolerance %* (no code). Per product: SKU > *Variance tolerance %* |
| "Flag favourable variances too" | `apps/production/variance.py`, in `variance()`: change `status = FLAG if adverse else NOTE` to `status = FLAG`. The dashboard alert and tests that expect `NOTE` for cartons will need updating |
| "Use `>=` for the tolerance" | Same function: `if abs(pct) <= tol` becomes `if abs(pct) < tol` |
| "Change GST to 12% for this product" | `/admin/` > SKU > *GST rate*. Old invoices keep their own rate (copied at posting) |
| "Add a unit (e.g. 500 g sachet)" | `/admin/` > Units: code, name, dimension *count*, factor 1 |
| "Dispatch oldest-made first instead of earliest expiry" | `apps/inventory/services.py`, `batches_with_stock()`: change `.order_by("expiry_date", "mfg_date", "id")` to `.order_by("mfg_date", "expiry_date", "id")` |
| "Change the shelf life" | `/admin/` > SKU > *Shelf life days* (applies to new batches) |
| "Warn 30 days before expiry, not 14" | `apps/inventory/services.py`: `NEAR_EXPIRY_DAYS = 14` |
| "Change the 300 m GPS distance" | `apps/sales/geo.py`: `FAR_METERS` |
| "Last-purchase-price costing instead of weighted average" | `apps/inventory/services.py`, `average_cost()`: return the unit cost of the most recent positive movement instead of the blended value. Say what you would lose (stability) |
| "Add a new report" | Write a function in `apps/reports/builders.py` returning a list of `Table(title, headers, rows)`, add one line to `REPORTS` in `apps/reports/views.py`. It automatically gets the date filter and CSV, Excel and PDF export |
| "Add a field to the invoice" | Model field in `apps/billing/models.py` + `makemigrations` + `migrate`; set it in `raise_invoice`; show it in the template and in `apps/billing/pdf.py` |
| "Change the invoice number format" | `apps/billing/services.py`, `next_number()` |

After any change run: `.venv/bin/python -m pytest -q apps/production apps/billing` (about a minute), then the benchmark: `.venv/bin/python manage.py run_benchmark` on a fresh database.

## 4. Questions to expect, and honest answers

**Why a ledger instead of a stock column?** A column can be overwritten and cannot explain itself. With a ledger every balance is a sum, every row names its source (purchase, run, dispatch, credit note, adjustment), and errors are corrected by a counter-entry, so an auditor can reproduce any number.

**How do you stop float errors?** `Decimal` everywhere. Invoice lines are rounded half-up to the paisa individually. Stock quantities are 4-place decimals in the item's own unit. There are tests with awkward values (₹0.10 + ₹0.20 + ₹33.33...) that check the strings, not just equality.

**Why weighted average?** Prices move (₹42 then ₹45); weighted average is stable, needs no layers, and can be recomputed from the ledger. Cost: it blurs old and new prices, so a run's cost depends on the blend at that moment. FIFO would give ₹30.90 for the benchmark, last price ₹31.54, mine ₹31.14.

**How is the variance decided?** (actual − planned) / planned, to 2 places. Flagged only if adverse (more material, less yield) *and strictly greater* than tolerance. Favourable beyond tolerance is a note. That is why cartons at −10% are a note while yield at −6% is flagged.

**What stops two admins approving the same stock?** Approval counts packets already promised to other approved orders, and stock is checked again at dispatch (dispatch refuses if it is short and posts nothing). Row locks serialise changes on PostgreSQL. **I have not run PostgreSQL or a concurrency load test**, and SQLite serialises writes anyway.

**What happens if a run was entered wrongly?** The admin voids it: exact opposite movements are posted into the same batches and the run keeps its history. If the batch has already been sold, the void is refused rather than leaving the ledger inconsistent.

**What happens when an invoice is wrong?** It is never edited. Cancel it with a credit note; the order returns to *approved* and a corrected invoice can be raised. Goods that came back return to their original batches at the cost they left at.

**How does offline ordering avoid duplicates?** The page generates a unique id per order; the server treats it as an idempotency key, so a retry returns the same order. The queue is written to the phone *before* sending, so nothing is lost if the connection drops.

**What are the weak spots?** Be upfront (they are listed in the README): Docker/PostgreSQL never run, no login throttling, master data edited in Django admin, FG stock valued at material cost only, Tamil text needs native review, offline only for order entry and only while the app is open.

**What would you build next?** Split BOM into recipe + packing spec (true shared recipes); costing layers (FIFO) and COGS that include overheads; partial dispatch and partial credit notes; a PostgreSQL concurrency test; per-user language; Tamil names for master data.

## 5. Running the walkthrough

1. Fresh database, seeded (see README). Log in as `production`, `admin`, and `salesman_a` (phone-sized window).
2. Follow [DEMO_SCRIPT.md](DEMO_SCRIPT.md). It is the same scenario they will check.
3. Have `manage.py run_benchmark` ready in a terminal as a fallback: it prints PASS/FAIL for all 11 checks.
4. Know where the tests are: `apps/core/tests.py` (the benchmark), `apps/billing/tests.py` (tax), `apps/inventory/tests.py` and `apps/inventory/test_batches.py` (stock), `apps/production/tests.py` (variance and runs).
