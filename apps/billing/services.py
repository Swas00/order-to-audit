"""Invoice, dispatch, payment and credit-note workflow. Each public function is one transaction."""

from datetime import date
from decimal import Decimal

from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from apps.audit.models import record
from apps.inventory import services as stock
from apps.inventory.models import MovementType, StockMovement
from apps.masters.models import CompanyProfile, Customer
from apps.sales.models import Order, OrderStatus

from . import tax
from .models import (money_sum, CreditNote, CreditNoteLine, Dispatch, DispatchLine, DocKind, DocSequence, Invoice, InvoiceLine, InvoiceStatus, Payment,
                     PaymentAllocation)

ZERO = Decimal("0.00")


class BillingError(Exception):
    pass


def next_number(kind: str, on: date) -> tuple[str, str]:
    """Returns (document number, financial year). Locks the sequence row so numbers never repeat or skip."""
    fy = tax.financial_year(on)
    seq, _ = DocSequence.objects.select_for_update().get_or_create(kind=kind, fy=fy)
    seq.last += 1
    seq.save()
    return f"{kind}/{fy}/{seq.last:04d}", fy


@transaction.atomic
def raise_invoice(order: Order, user, invoice_date: date | None = None) -> Invoice:
    order = Order.objects.select_for_update().get(pk=order.pk)
    if order.status != OrderStatus.APPROVED:
        raise BillingError(f"{order.number} is {order.get_status_display().lower()}; only approved orders can be invoiced")
    lines = [l for l in order.lines.select_related("sku") if l.qty > 0]
    if not lines:
        raise BillingError("The order has no quantity to invoice")
    company, customer = CompanyProfile.get(), order.customer
    invoice_date = invoice_date or timezone.localdate()
    intra = customer.state_code == company.state_code
    number, fy = next_number(DocKind.INVOICE, invoice_date)

    computed = [(l, tax.line_tax(l.qty, l.price, l.sku.gst_rate, intra)) for l in lines]
    taxable = sum((t.taxable for _, t in computed), ZERO)
    cgst, sgst, igst = (sum((getattr(t, f) for _, t in computed), ZERO) for f in ("cgst", "sgst", "igst"))
    grand, round_off = tax.round_to_rupee(taxable + cgst + sgst + igst)

    inv = Invoice.objects.create(
        number=number, fy=fy, order=order, customer=customer, invoice_date=invoice_date,
        supplier_name=company.name, supplier_gstin=company.gstin, supplier_address=company.address,
        supplier_state_code=company.state_code, buyer_name=customer.name, buyer_gstin=customer.gstin,
        buyer_address=customer.address, place_of_supply_code=customer.state_code, place_of_supply_name=customer.state_name,
        intra_state=intra, taxable_total=taxable, cgst_total=cgst, sgst_total=sgst, igst_total=igst,
        round_off=round_off, grand_total=grand, created_by=user)
    for l, t in computed:
        InvoiceLine.objects.create(invoice=inv, sku=l.sku, description=str(l.sku), hsn_code=l.sku.hsn_code, qty=l.qty,
                                   rate=l.price, gst_rate=l.sku.gst_rate, taxable_value=t.taxable,
                                   cgst=t.cgst, sgst=t.sgst, igst=t.igst)
    order.status = OrderStatus.INVOICED
    order.save()
    record(user, order, "invoiced", invoice=number)
    record(user, inv, "issued", order=order.number, grand_total=str(grand))
    return inv


def _whole_qty(value, what):
    try:
        n = int(value)
    except (TypeError, ValueError):
        raise BillingError(f"{what}: quantity must be a whole number of packets")
    if n < 0:
        raise BillingError(f"{what}: quantity cannot be negative")
    return n


def _chosen(invoice, quantities, limit_of, none_means_all_msg):
    """Validate {invoice line id: qty}. None means 'everything still possible'. Returns [(line, qty)] with qty > 0."""
    lines = list(invoice.lines.select_related("sku"))
    if quantities is None:
        picked = [(l, limit_of(l)) for l in lines]
    else:
        by_id = {l.pk: l for l in lines}
        picked = []
        for line_id, raw in quantities.items():
            if line_id not in by_id:
                raise BillingError("That line does not belong to this invoice")
            l = by_id[line_id]
            n = _whole_qty(raw, str(l.sku))
            if n > limit_of(l):
                raise BillingError(f"{l.sku}: only {limit_of(l)} packets available for this, you asked for {n}")
            picked.append((l, n))
    picked = [(l, n) for l, n in picked if n > 0]
    if not picked:
        raise BillingError(none_means_all_msg)
    return picked


@transaction.atomic
def dispatch_invoice(invoice: Invoice, user, vehicle_ref="", quantities: dict | None = None) -> Dispatch:
    """Goods leave: finished-goods stock goes down (first-expiry-first-out). `quantities` maps invoice line id to
    packets sent in this lot (a partial dispatch); None sends everything still outstanding. An invoice can be
    dispatched in several lots until every line is complete."""
    invoice = Invoice.objects.select_for_update().get(pk=invoice.pk)
    if invoice.status != InvoiceStatus.ISSUED:
        raise BillingError("A cancelled invoice cannot be dispatched")
    picked = _chosen(invoice, quantities, lambda l: l.remaining_to_dispatch,
                     f"{invoice.number} has already been dispatched in full" if invoice.is_dispatched
                     else "Enter a quantity to dispatch")
    d = Dispatch.objects.create(invoice=invoice, user=user, vehicle_ref=vehicle_ref)
    for line, qty in picked:
        DispatchLine.objects.create(dispatch=d, invoice_line=line, qty=qty)
        stock.issue_fefo(line.sku, qty, d, MovementType.DISPATCH, note=invoice.number)  # raises if short, rolling everything back
    complete = invoice.dispatch_state == "full"
    Order.objects.filter(pk=invoice.order_id).update(status=OrderStatus.DISPATCHED if complete else OrderStatus.INVOICED)
    detail = {"vehicle": vehicle_ref, "lot": {str(l.sku.code): q for l, q in picked}, "complete": complete}
    record(user, invoice, "dispatched", **detail)
    record(user, invoice.order, "dispatched", invoice=invoice.number, complete=complete)
    return d


def _return_to_stock(invoice, line, qty, cn):
    """Put `qty` packets of `line` back into the batches they left from, at the cost they left at."""
    d_ct, c_ct = ContentType.objects.get_for_model(Dispatch), ContentType.objects.get_for_model(CreditNote)
    outs = StockMovement.objects.filter(source_type=d_ct, source_id__in=invoice.dispatches.values("pk"), sku=line.sku).order_by("id")
    back = {}
    for m in StockMovement.objects.filter(source_type=c_ct, source_id__in=invoice.credit_notes.exclude(pk=cn.pk).values("pk"),
                                          sku=line.sku, movement_type=MovementType.RETURN):
        back[m.batch_id] = back.get(m.batch_id, 0) + m.qty
    need = Decimal(qty)
    for out in outs:
        used = min(back.get(out.batch_id, Decimal("0")), -out.qty)
        back[out.batch_id] = back.get(out.batch_id, Decimal("0")) - used
        take = min(need, -out.qty - used)
        if take > 0:
            stock.receive(line.sku, take, out.unit_cost, cn, MovementType.RETURN, note=f"{cn.number} / {invoice.number}", batch=out.batch)
            need -= take
        if need <= 0:
            return
    if need > 0:
        raise BillingError("More goods returned than were dispatched")


@transaction.atomic
def issue_credit_note(invoice: Invoice, user, reason: str, quantities: dict | None = None,
                      goods_returned: bool | None = None) -> CreditNote:
    """Credit part or all of an invoice. `quantities` maps invoice line id to packets credited; None credits everything
    not yet credited (a full cancellation). If goods are returned, they go back into the batches they left from.
    When every packet has been credited the invoice becomes cancelled and its order returns to 'approved'.
    The invoice itself is never edited or deleted."""
    invoice = Invoice.objects.select_for_update().get(pk=invoice.pk)
    if invoice.status == InvoiceStatus.CANCELLED:
        raise BillingError(f"{invoice.number} is already cancelled")
    if not reason.strip():
        raise BillingError("A reason is required")
    picked = _chosen(invoice, quantities, lambda l: l.creditable_qty, "Enter a quantity to credit")
    if goods_returned is None:
        goods_returned = any(l.returnable_qty > 0 for l, _ in picked)
    returned = {l.pk: min(q, l.returnable_qty) if goods_returned else 0 for l, q in picked}
    if goods_returned and not any(returned.values()):
        raise BillingError("Nothing was dispatched, so there are no goods to return")

    lines_tax = []
    for l, q in picked:
        if q == l.creditable_qty:  # last credit on this line: take exactly what is left so the pieces add up to the invoice
            prev = l.credit_lines.aggregate(t=Sum("taxable_value"), c=Sum("cgst"), s=Sum("sgst"), i=Sum("igst"))
            t = tax.LineTax(l.taxable_value - money_sum(prev["t"]), l.cgst - money_sum(prev["c"]),
                            l.sgst - money_sum(prev["s"]), l.igst - money_sum(prev["i"]))
        else:
            t = tax.line_tax(q, l.rate, l.gst_rate, invoice.intra_state)
        lines_tax.append((l, q, t))
    taxable, cgst, sgst, igst = (sum((getattr(t, f) for *_, t in lines_tax), ZERO) for f in ("taxable", "cgst", "sgst", "igst"))
    chosen = {l.pk: q for l, q in picked}
    finishes = all(l.creditable_qty == chosen.get(l.pk, 0) for l in invoice.lines.all())
    if finishes:  # credit notes together equal the invoice to the paisa, round-off included
        grand = invoice.grand_total - invoice.credited_total
        round_off = grand - (taxable + cgst + sgst + igst)
    else:
        grand, round_off = tax.round_to_rupee(taxable + cgst + sgst + igst)

    number, _ = next_number(DocKind.CREDIT_NOTE, timezone.localdate())
    cn = CreditNote.objects.create(
        number=number, invoice=invoice, reason=reason.strip(), goods_returned=any(returned.values()),
        taxable_total=taxable, cgst_total=cgst, sgst_total=sgst, igst_total=igst, round_off=round_off,
        grand_total=grand, created_by=user)
    for l, q, t in lines_tax:
        CreditNoteLine.objects.create(credit_note=cn, invoice_line=l, qty=q, qty_returned=returned[l.pk],
                                      taxable_value=t.taxable, cgst=t.cgst, sgst=t.sgst, igst=t.igst)
        if returned[l.pk]:
            _return_to_stock(invoice, l, returned[l.pk], cn)
    if finishes:
        invoice.status = InvoiceStatus.CANCELLED
        invoice.save()
        Order.objects.filter(pk=invoice.order_id).update(status=OrderStatus.APPROVED)
    else:
        Order.objects.filter(pk=invoice.order_id).update(
            status=OrderStatus.DISPATCHED if invoice.dispatch_state == "full" else OrderStatus.INVOICED)
    record(user, invoice, "cancelled" if finishes else "credited", credit_note=number, reason=reason.strip(),
           goods_returned=cn.goods_returned, lot={str(l.sku.code): q for l, q in picked})
    return cn


def cancel_invoice(invoice: Invoice, user, reason: str, goods_returned: bool | None = None) -> CreditNote:
    """Cancel everything still uncredited with a credit note. Goods that left and come back are restocked."""
    return issue_credit_note(invoice, user, reason, None, goods_returned)


# ---------- payments and receivables ----------

def customer_open_invoices(customer):
    return [i for i in customer.invoices.filter(status=InvoiceStatus.ISSUED).order_by("invoice_date", "id") if i.outstanding > 0]


@transaction.atomic
def record_payment(customer: Customer, amount, user, invoice: Invoice | None = None, mode="bank", reference="",
                   paid_on: date | None = None) -> Payment:
    """Record money received. With `invoice`, it is applied to that invoice (never above what is owed).
    Without, it clears the customer's oldest invoices first; anything left stays as an advance."""
    amount = tax.q(amount)
    if amount <= 0:
        raise BillingError("Payment must be more than zero")
    Customer.objects.select_for_update().get(pk=customer.pk)
    pay = Payment.objects.create(customer=customer, amount=amount, mode=mode, reference=reference,
                                 paid_on=paid_on or timezone.localdate(), created_by=user)
    if invoice is not None:
        invoice = Invoice.objects.select_for_update().get(pk=invoice.pk)
        if invoice.customer_id != customer.pk:
            raise BillingError("That invoice belongs to a different customer")
        if invoice.status != InvoiceStatus.ISSUED:
            raise BillingError("Cannot apply a payment to a cancelled invoice")
        if amount > invoice.outstanding:
            raise BillingError(f"{invoice.number} has only ₹{invoice.outstanding} outstanding")
        PaymentAllocation.objects.create(payment=pay, invoice=invoice, amount=amount)
    else:
        left = amount
        for inv in customer_open_invoices(customer):
            part = min(left, inv.outstanding)
            PaymentAllocation.objects.create(payment=pay, invoice=inv, amount=part)
            left -= part
            if left <= 0:
                break
    record(user, pay, "received", customer=customer.name, amount=str(amount), invoice=invoice.number if invoice else None)
    return pay


def customer_balance(customer: Customer) -> Decimal:
    """Invoiced (not cancelled) less credit notes, minus everything received. Negative = customer is in credit."""
    issued = customer.invoices.filter(status=InvoiceStatus.ISSUED)
    billed = money_sum(issued.aggregate(t=Sum("grand_total"))["t"]) - money_sum(
        CreditNote.objects.filter(invoice__in=issued).aggregate(t=Sum("grand_total"))["t"])
    received = money_sum(customer.payments.aggregate(t=Sum("amount"))["t"])
    return billed - received


AGEING_BUCKETS = (("0-30", 0, 30), ("31-60", 31, 60), ("61-90", 61, 90), ("90+", 91, 10**6))


def ageing(as_of: date | None = None):
    """Outstanding per customer in age buckets, by days since invoice date.
    Assumption: no credit terms are given, so age runs from the invoice date."""
    as_of = as_of or timezone.localdate()
    rows = {}
    for inv in Invoice.objects.filter(status=InvoiceStatus.ISSUED, invoice_date__lte=as_of).select_related("customer"):
        due = inv.outstanding
        if due <= 0:
            continue
        days = (as_of - inv.invoice_date).days
        row = rows.setdefault(inv.customer_id, {"customer": inv.customer, "buckets": {b[0]: ZERO for b in AGEING_BUCKETS}, "total": ZERO})
        for name, lo, hi in AGEING_BUCKETS:
            if lo <= days <= hi:
                row["buckets"][name] += due
        row["total"] += due
    for r in rows.values():
        r["cells"] = [r["buckets"][b[0]] for b in AGEING_BUCKETS]  # in bucket order, for templates and exports
    return sorted(rows.values(), key=lambda r: r["customer"].name)


def batch_trace(batch):
    """Where did this batch go? [(invoice, customer, packets)] from dispatch movements, net of goods returned. Used for recalls."""
    d_ct, c_ct = ContentType.objects.get_for_model(Dispatch), ContentType.objects.get_for_model(CreditNote)
    out = {}
    for m in StockMovement.objects.filter(batch=batch, source_type=d_ct, movement_type=MovementType.DISPATCH):
        d = Dispatch.objects.select_related("invoice", "invoice__customer").get(pk=m.source_id)
        out[d.invoice_id] = (d.invoice, d.invoice.customer, out.get(d.invoice_id, (None, None, 0))[2] - m.qty)
    for m in StockMovement.objects.filter(batch=batch, source_type=c_ct, movement_type=MovementType.RETURN):
        inv_id = CreditNote.objects.get(pk=m.source_id).invoice_id
        if inv_id in out:
            inv, cust, q = out[inv_id]
            out[inv_id] = (inv, cust, q - m.qty)
    return [r for r in out.values() if r[2] > 0]
