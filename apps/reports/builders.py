"""Report builders. Each takes (date_from, date_to) and returns a list of Table sections.

Sales figures follow how GST is actually reported: every invoice counts on its own invoice date, and a
credit note counts negatively on the credit-note date. A cancelled invoice therefore stays in the
register, and its credit note offsets it in the month the cancellation happened.
"""

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal

from django.db.models import Sum

from apps.billing import services as billing
from apps.billing.models import CreditNote, Invoice, InvoiceStatus, Payment
from apps.core.exports import Table
from apps.inventory import services as stock
from apps.masters.models import Item, ItemKind, Sku
from apps.production import services as prod
from apps.production.models import ProductionRun, RunStatus
from apps.purchasing.models import Purchase

ZERO = Decimal("0.00")
GROUPS = (("Raw materials", lambda: Item.objects.filter(kind=ItemKind.RAW)),
          ("Packaging materials", lambda: Item.objects.filter(kind=ItemKind.PACKAGING)),
          ("Finished goods", lambda: Sku.objects.all()))


@dataclass
class DocLine:
    sku: Sku
    description: str
    hsn: str
    gst_rate: Decimal
    qty: int
    taxable: Decimal
    cgst: Decimal
    sgst: Decimal
    igst: Decimal


@dataclass
class SalesDoc:
    kind: str  # Invoice or Credit note
    sign: int
    date: object
    number: str
    ref: str
    customer: object
    gstin: str
    pos_code: str
    pos_name: str
    intra: bool
    taxable: Decimal
    cgst: Decimal
    sgst: Decimal
    igst: Decimal
    round_off: Decimal
    total: Decimal
    remark: str
    lines: list

    @property
    def registered(self):
        return bool(self.gstin)


def _lines(inv, sign):
    return [DocLine(l.sku, l.description, l.hsn_code, l.gst_rate, sign * l.qty, sign * l.taxable_value,
                    sign * l.cgst, sign * l.sgst, sign * l.igst) for l in inv.lines.all()]


def sales_docs(d_from, d_to) -> list[SalesDoc]:
    docs = []
    for inv in Invoice.objects.filter(invoice_date__range=(d_from, d_to)).select_related("customer", "credit_note") \
            .prefetch_related("lines__sku"):
        remark = f"Cancelled by {inv.credit_note.number}" if inv.status == InvoiceStatus.CANCELLED else ""
        docs.append(SalesDoc("Invoice", 1, inv.invoice_date, inv.number, inv.order.number, inv.customer, inv.buyer_gstin,
                             inv.place_of_supply_code, inv.place_of_supply_name, inv.intra_state, inv.taxable_total,
                             inv.cgst_total, inv.sgst_total, inv.igst_total, inv.round_off, inv.grand_total, remark, _lines(inv, 1)))
    for cn in CreditNote.objects.filter(note_date__range=(d_from, d_to)).select_related("invoice", "invoice__customer") \
            .prefetch_related("invoice__lines__sku"):
        inv = cn.invoice
        docs.append(SalesDoc("Credit note", -1, cn.note_date, cn.number, inv.number, inv.customer, inv.buyer_gstin,
                             inv.place_of_supply_code, inv.place_of_supply_name, inv.intra_state, -cn.taxable_total,
                             -cn.cgst_total, -cn.sgst_total, -cn.igst_total, -inv.round_off, -cn.grand_total, cn.reason,
                             _lines(inv, -1)))
    return sorted(docs, key=lambda d: (d.date, d.sign < 0, d.number))  # invoices before credit notes on a day


def _sum(docs, field):
    return sum((getattr(d, field) for d in docs), ZERO)


def sales_register(d_from, d_to):
    docs = sales_docs(d_from, d_to)
    rows = [(d.date, d.kind, d.number, d.customer.name, d.gstin or "Unregistered", f"{d.pos_name} ({d.pos_code})",
             d.taxable, d.cgst, d.sgst, d.igst, d.round_off, d.total, d.remark or d.ref) for d in docs]
    rows.append(("", "", "NET TOTAL", "", "", "", *(_sum(docs, f) for f in ("taxable", "cgst", "sgst", "igst", "round_off", "total")), ""))
    return [Table("Sales register", ["Date", "Type", "Number", "Customer", "GSTIN", "Place of supply", "Taxable", "CGST",
                                     "SGST", "IGST", "Round off", "Total", "Order / remark"], rows,
                  "Credit notes appear as negative rows on their own date.")]


def customer_sales(d_from, d_to):
    agg = {}
    for d in sales_docs(d_from, d_to):
        a = agg.setdefault(d.customer.pk, {"c": d.customer, "docs": 0, "taxable": ZERO, "tax": ZERO, "total": ZERO})
        a["docs"] += 1
        a["taxable"] += d.taxable
        a["tax"] += d.cgst + d.sgst + d.igst
        a["total"] += d.total
    rows = [(a["c"].name, a["c"].get_kind_display(), a["c"].gstin or "Unregistered", a["docs"], a["taxable"], a["tax"], a["total"])
            for a in sorted(agg.values(), key=lambda a: -a["total"])]
    return [Table("Customer-wise sales (net of credit notes)", ["Customer", "Type", "GSTIN", "Documents", "Taxable", "GST", "Total"], rows)]


def product_sales(d_from, d_to):
    agg = {}
    for d in sales_docs(d_from, d_to):
        for l in d.lines:
            a = agg.setdefault(l.sku.pk, {"sku": l.sku, "qty": 0, "taxable": ZERO, "tax": ZERO})
            a["qty"] += l.qty
            a["taxable"] += l.taxable
            a["tax"] += l.cgst + l.sgst + l.igst
    rows = [(str(a["sku"]), a["sku"].hsn_code, a["qty"], a["taxable"], a["tax"], a["taxable"] + a["tax"],
             (a["taxable"] / a["qty"]).quantize(Decimal("0.01")) if a["qty"] else ZERO) for a in agg.values()]
    return [Table("Product-wise sales (net of credit notes)", ["Product", "HSN", "Packets", "Taxable", "GST", "Total", "Avg price"], rows)]


def gst_summary(d_from, d_to):
    docs = sales_docs(d_from, d_to)
    t = lambda f: _sum(docs, f)  # noqa: E731
    gstr3b = Table("GSTR-3B 3.1(a): outward taxable supplies (net)", ["Taxable value", "Integrated tax", "Central tax", "State/UT tax", "Cess"],
                   [(t("taxable"), t("igst"), t("cgst"), t("sgst"), ZERO)])
    rate = defaultdict(lambda: [ZERO] * 4)
    hsn = {}
    b2b, b2c, cdn = [], defaultdict(lambda: [ZERO] * 4), []
    for d in docs:
        per_rate = defaultdict(lambda: [ZERO] * 4)
        for l in d.lines:
            for tgt in (per_rate[l.gst_rate], rate[(l.gst_rate, "Intra-state" if d.intra else "Inter-state")]):
                tgt[0] += l.taxable; tgt[1] += l.igst; tgt[2] += l.cgst; tgt[3] += l.sgst  # noqa: E702
            h = hsn.setdefault((l.hsn, l.gst_rate), {"desc": l.description, "qty": 0, "v": [ZERO] * 4})
            h["qty"] += l.qty
            for i, x in enumerate((l.taxable, l.igst, l.cgst, l.sgst)):
                h["v"][i] += x
        for r, v in per_rate.items():
            if d.kind == "Credit note":
                cdn.append((d.gstin or "Unregistered", d.customer.name, d.number, d.date, d.ref, f"{d.pos_name} ({d.pos_code})", r, *v))
            elif d.registered:
                b2b.append((d.gstin, d.customer.name, d.number, d.date, d.total, f"{d.pos_name} ({d.pos_code})", r, *v))
            else:
                for i, x in enumerate(v):
                    b2c[(d.pos_name, r)][i] += x
    heads = ["Taxable", "IGST", "CGST", "SGST"]
    return [
        gstr3b,
        Table("Rate-wise summary", ["GST rate %", "Supply", *heads], [(r, s, *v) for (r, s), v in sorted(rate.items())]),
        Table("B2B invoices (GSTR-1 table 4A)", ["Receiver GSTIN", "Receiver", "Invoice no", "Date", "Invoice value", "Place of supply", "Rate %", *heads], b2b),
        Table("B2C: unregistered buyers (GSTR-1 table 7)", ["Place of supply", "Rate %", *heads], [(p, r, *v) for (p, r), v in sorted(b2c.items())]),
        Table("Credit notes (GSTR-1 table 9B), shown negative", ["Receiver GSTIN", "Receiver", "Credit note no", "Date", "Original invoice", "Place of supply", "Rate %", *heads], cdn),
        Table("HSN summary (GSTR-1 table 12)", ["HSN", "Description", "UQC", "Quantity", "Rate %", *heads],
              [(k[0], h["desc"], "PKT", h["qty"], k[1], *h["v"]) for k, h in sorted(hsn.items())]),
    ]


def purchase_register(d_from, d_to):
    rows = [(p.bill_date, p.bill_no, p.supplier.name, l.item.name, l.qty, l.unit.code, l.rate, l.taxable_value, l.gst_rate, l.tax_amount)
            for p in Purchase.objects.filter(bill_date__range=(d_from, d_to)).select_related("supplier").prefetch_related("lines__item", "lines__unit")
            for l in p.lines.all()]
    return [Table("Purchase register", ["Date", "Bill no", "Supplier", "Item", "Qty", "Unit", "Rate", "Taxable", "GST %", "GST"], rows)]


def _bounds(d_from, d_to):
    from apps.core.dates import aware_range
    return aware_range(d_from, d_to)


def stock_summary(d_from, d_to):
    dt_from, dt_to = _bounds(d_from, d_to)
    out = []
    for title, qs in GROUPS:
        rows = [(str(r.stockable), r.opening, r.receipts, r.issues, r.adjustments, r.closing, r.value.quantize(ZERO))
                for r in stock.stock_summary(qs(), dt_from, dt_to)]
        out.append(Table(f"Stock summary: {title}", ["Item", "Opening", "Receipts", "Issues", "Adjustments", "Closing", "Value (INR)"], rows,
                         "Opening + receipts - issues +/- adjustments = closing."))
    return out


def stock_ledger_all(d_from, d_to):
    dt_from, dt_to = _bounds(d_from, d_to)
    tables = []
    for title, qs in GROUPS:
        rows = []
        for s in qs():
            bf, entries = stock.ledger(s, dt_from, dt_to)
            rows.append((str(s), "", "Brought forward", "", "", bf))
            rows += [(str(s), m.moved_at.strftime("%Y-%m-%d %H:%M"), m.get_movement_type_display(), str(m.source), m.qty, bal) for m, bal in entries]
        tables.append(Table(f"Stock ledger: {title}", ["Item", "Date", "Type", "Source document", "Qty", "Balance"], rows))
    return tables


def production_report(d_from, d_to):
    runs = ProductionRun.objects.filter(started_at__date__range=(d_from, d_to)).exclude(status=RunStatus.VOIDED).select_related("sku")
    lines, yields = [], []
    for run in runs:
        rep = prod.run_report(run)
        for l in rep.lines:
            v = l.variance
            lines.append((run.number, run.started_at.date(), str(run.sku), l.item.name, l.unit, v.planned, v.actual, v.diff,
                          "" if v.pct is None else v.pct, v.status.upper()))
        if rep.yield_variance:
            y = rep.yield_variance
            yields.append((run.number, run.started_at.date(), str(run.sku), y.planned, y.actual, y.pct, y.status.upper(),
                           run.damaged_pouches, rep.wastage_packets, rep.material_cost.quantize(ZERO)))
    tol = "Tolerance: SKU override or company default (3%). FLAG = adverse beyond tolerance; NOTE = favourable beyond tolerance."
    return [Table("Planned vs actual consumption", ["Run", "Date", "Product", "Item", "Unit", "Planned", "Actual", "Difference", "Variance %", "Status"], lines, tol),
            Table("Yield and wastage", ["Run", "Date", "Product", "Expected packets", "Good packets", "Yield variance %", "Status",
                                        "Damaged pouches", "Shortfall (expected - good)", "Material cost"], yields)]


def collections(d_from, d_to):
    payments = list(Payment.objects.filter(paid_on__range=(d_from, d_to)).select_related("customer").prefetch_related("allocations__invoice"))
    by_cust = defaultdict(lambda: {"n": 0, "total": ZERO})
    for p in payments:
        by_cust[p.customer.name]["n"] += 1
        by_cust[p.customer.name]["total"] += p.amount
    detail = [(p.paid_on, p.customer.name, p.amount, p.get_mode_display(), p.reference,
               ", ".join(f"{a.invoice.number} ({a.amount})" for a in p.allocations.all()) + (f" advance {p.unallocated}" if p.unallocated else ""))
              for p in payments]
    return [Table("Collections by customer", ["Customer", "Receipts", "Amount"], [(c, v["n"], v["total"]) for c, v in sorted(by_cust.items())]),
            Table("Payments received", ["Date", "Customer", "Amount", "Mode", "Reference", "Applied to"], detail)]


def outstanding(d_from, d_to):
    from apps.masters.models import Customer
    bal = [(c.name, billing.customer_balance(c)) for c in Customer.objects.all()]
    inv_rows = [(i.customer.name, i.number, i.invoice_date, (d_to - i.invoice_date).days, i.grand_total, i.paid, i.outstanding)
                for i in Invoice.objects.filter(status=InvoiceStatus.ISSUED, invoice_date__lte=d_to).select_related("customer")
                if i.outstanding > 0]
    age = [(r["customer"].name, *r["cells"], r["total"]) for r in billing.ageing(d_to)]
    return [Table("Outstanding receivables by customer (credit shown negative)", ["Customer", "Outstanding"], [b for b in bal if b[1] != 0],
                  f"Balance now. Ageing and open invoices as of {d_to}."),
            Table(f"Ageing as of {d_to} (days since invoice date)", ["Customer", "0-30", "31-60", "61-90", "90+", "Total"], age),
            Table("Open invoices", ["Customer", "Invoice", "Date", "Age (days)", "Invoice value", "Paid", "Outstanding"], inv_rows)]
