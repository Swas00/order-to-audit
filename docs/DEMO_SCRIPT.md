# Demo video script (max 8 minutes)

The brief requires the section 5 scenario **in order**, showing **both the mobile salesman view and the admin view**, with the
exact data from the problem statement. This script targets **about 7:30**, leaving a margin.

## Before you record

**Reset to a clean, seeded database** (do not run `run_benchmark`: you perform the steps live):

```bash
rm -f db.sqlite3
.venv/bin/python manage.py migrate
.venv/bin/python manage.py seed_users && .venv/bin/python manage.py seed_masters && .venv/bin/python manage.py seed_inventory
.venv/bin/python manage.py runserver
```

**Set up two windows side by side on one screen:**

| Window | What | Logged in as |
|---|---|---|
| **Phone** (left, narrow) | A browser window at phone size (Chrome DevTools device toolbar, about 390 × 844), or a real phone on the same Wi-Fi: start the server with `ALLOWED_HOSTS='*' .venv/bin/python manage.py runserver 0.0.0.0:8000` and open `http://<your-computer-ip>:8000` | `salesman_a` (switch to `salesman_b` for step 6) |
| **Desktop** (right, wide) | Normal browser window | `production` first, then `admin` |

Use a separate browser profile or a private window for the second login so the two sessions do not clash.
Password for every account: `demo12345`.

**Pre-flight checks**
- The report date filters default to the **current month**, so record on the same day you reset the database.
- Close other tabs and notifications. Zoom the browser to 110-125% so text is legible in the video.
- Do one full dry run first; the data you create in the dry run is wiped by the reset above.
- Have this script open on a second monitor or printed.

## The script

Times are cumulative targets. Say the numbers out loud: the evaluator is checking them against the table.

| Time | Step | On screen | What to say / show |
|---|---|---|---|
| 0:00 | **Intro** (20 s) | Desktop, login page | "Order-to-Audit: one system from a salesman's order to the auditor's report. Stock is a ledger of movements, never an overwritten number. Section 5 scenario, in order." Log in as `production`. |
| 0:20 | **1. Purchase 60 kg flour at ₹45** (35 s) | Desktop: **Purchases → Record purchase** | Supplier *Demo Flour Mills*, bill no `F-001`, line: *Flour*, qty `60`, unit `kg`, rate `45`, GST `0`. Save. Open **Stock summary**: flour receipts 60. "Weighted-average cost is now ₹43.125." |
| 0:55 | **2. Start run, issue materials** (50 s) | Desktop: **Production runs** | Product *Murukku 200 g*, batches `1`, **Start run**. Issue: Flour `40` kg; Cooking oil `10.5` L; Salt and spice mix `1.2` kg. "Oil is 0.5 L over the standard 10." |
| 1:45 | **3. Complete the run** (45 s) | Desktop: same run page, "Complete run" | Good packets `188`, damaged pouches `6`, Carton used: change the pre-filled `10` to `9`. Remark: "fryer temperature". **Complete.** Point at: **yield 188 vs 200, −6.00%, FLAGGED**; **oil +5.00%, FLAGGED**; cartons −10% is only a *note*; the new batch number and expiry. |
| 2:30 | **Stock after the run** (20 s) | Desktop: **Stock summary** | Flour **120**, oil **19.5**, pouches **306**, finished goods **238**. "Cooking oil and cartons are below their reorder level, so the low-stock banner appears." |
| 2:50 | **4. Salesman A orders 150 packets** (40 s) | **Phone**: log in as `salesman_a`, **New order** | Customer *Retailer A*, Murukku 200 g, `150`. "Total ₹7,500 before GST." Submit. "It is already at the factory: no phone call, no WhatsApp." |
| 3:30 | **5. Approve, invoice, dispatch** (70 s) | Desktop as `admin`: **All orders → ORD-0001** | Show the stock-coverage table (238 in stock, covered). **Approve as entered.** **Raise GST invoice.** On the invoice: taxable ₹7,500, CGST ₹187.50, SGST ₹187.50, **total ₹7,875**. Click **Download PDF** (show it briefly). **Record dispatch** (leave "Everything still to go" selected). It shows *dispatched from batches* (opening batch 50, new batch 100). Back to Stock summary: finished goods **88**. On the phone: refresh **My orders**, status now *Dispatched*. |
| 4:40 | **6. Salesman B, Distributor B, 120 packets** (50 s) | **Phone**: log out, log in as `salesman_b`; order *Distributor B* `120`. **Desktop**: open the order | Coverage shows **in stock 88, short by 32**. Try **Approve as entered**: refused with "Only 88 packets… available". "The admin can approve what stock covers or reject." Open **Production plan**: "make 1 batch" (net need 32), materials cover it. Click **Approve only what stock covers**, then **Raise GST invoice**: **IGST** (Karnataka), no CGST/SGST. *Do not dispatch.* |
| 5:30 | **7. Payment ₹4,000 from Retailer A** (25 s) | Desktop: **Payments → Record payment** | Customer *Retailer A*, invoice the ₹7,875 one, amount `4000`, mode UPI. Open **Outstanding and ageing**: Retailer A **₹3,875** (Distributor B also shows ₹4,620: invoiced in step 6, not yet paid). |
| 5:55 | **8. Labour ₹1,200, gas/power ₹600, cost per packet** (40 s) | Desktop: **Cost per packet** | Add *Labour* `1200` charged to *RUN-0001*; add *Gas / power* `600` charged to *RUN-0001*. Show **₹31.14 per packet**, margin ₹18.86 (37.7%). Say the method: "Weighted-average materials ₹4,055 plus overheads ₹1,800 equals ₹5,855, divided by 188 good packets." |
| 6:35 | **9. Month's reports** (60 s) | Desktop: **Reports** | Open in turn, a few seconds each: **Sales register**, **GST summary** (GSTR-3B block, B2B table, HSN summary), **Purchase register**, **Stock summary**, **Production report** (planned vs actual), **Outstanding and ageing**. Click **CSV**, **Excel** or **PDF** once. If you have time, mention the Tally XML download. |
| 7:35 | **Wrap-up** (20 s) | Desktop | "All 11 benchmark checks match, with `manage.py run_benchmark` to replay them, plus automated tests for the stock and tax maths. Details in the README." |
| **7:55** | End | | |

## If you run over, cut in this order
1. The PDF preview in step 5 (just say it downloads).
2. The production-plan page in step 6.
3. Show only three reports in step 9 (sales register, GST summary, production report).

## Do not forget (the evaluators check these)
- **Both views on screen:** the phone window (order entry, status) and the admin window (approval, invoice).
- **Say the key numbers:** 120 kg, 19.5 L, 306, 238, 88, −6%, +5%, ₹7,875, ₹3,875, shortage 32, IGST, cost per packet.
- **Show the shortage handling** for Distributor B, not only the number.
- Upload as **unlisted YouTube or Drive**, and test the link in a private window.

## Optional extras (outside the 8 minutes; record separately if you want)
- **Offline ordering:** open `/orders/new/` on the phone, stop the server, place an order (it queues), restart the server (it sends by itself).
- **Tamil:** switch to தமிழ் in the header and open a production run.
- **Batch recall trace:** `/batches/`, open a batch to see which customers received it.
