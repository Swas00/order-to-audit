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
from .models import (money_sum, CreditNote, Dispatch, DocKind, DocSequence, Invoice, InvoiceLine, InvoiceStatus, Payment,
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


@transaction.atomic
def dispatch_invoice(invoice: Invoice, user, vehicle_ref="") -> Dispatch:
    """Goods leave: finished-goods stock goes down by each invoiced quantity at average cost."""
    invoice = Invoice.objects.select_for_update().get(pk=invoice.pk)
    if invoice.status != InvoiceStatus.ISSUED:
        raise BillingError("A cancelled invoice cannot be dispatched")
    if Dispatch.objects.filter(invoice=invoice).exists():
        raise BillingError(f"{invoice.number} has already been dispatched")
    d = Dispatch.objects.create(invoice=invoice, user=user, vehicle_ref=vehicle_ref)
    for line in invoice.lines.select_related("sku"):
        stock.issue_fefo(line.sku, line.qty, d, MovementType.DISPATCH, note=invoice.number)  # earliest expiry first; raises if short
    Order.objects.filter(pk=invoice.order_id).update(status=OrderStatus.DISPATCHED)
    record(user, invoice, "dispatched", vehicle=vehicle_ref)
    record(user, invoice.order, "dispatched", invoice=invoice.number)
    return d


@transaction.atomic
def cancel_invoice(invoice: Invoice, user, reason: str, goods_returned: bool | None = None) -> CreditNote:
    """Cancel by credit note (the invoice is kept). If it was dispatched and the goods come back,
    stock is restored at the cost they left at. The order returns to 'approved' so it can be re-invoiced."""
    invoice = Invoice.objects.select_for_update().get(pk=invoice.pk)
    if invoice.status == InvoiceStatus.CANCELLED:
        raise BillingError(f"{invoice.number} is already cancelled")
    if not reason.strip():
        raise BillingError("A reason is required")
    dispatched = Dispatch.objects.filter(invoice=invoice).first()
    if goods_returned is None:
        goods_returned = dispatched is not None
    if goods_returned and not dispatched:
        raise BillingError("Nothing was dispatched, so there are no goods to return")
    number, _ = next_number(DocKind.CREDIT_NOTE, timezone.localdate())
    cn = CreditNote.objects.create(
        number=number, invoice=invoice, reason=reason.strip(), goods_returned=goods_returned,
        taxable_total=invoice.taxable_total, cgst_total=invoice.cgst_total, sgst_total=invoice.sgst_total,
        igst_total=invoice.igst_total, grand_total=invoice.grand_total, created_by=user)
    if dispatched and goods_returned:
        ct = ContentType.objects.get_for_model(dispatched)
        for out in StockMovement.objects.filter(source_type=ct, source_id=dispatched.pk, sku__isnull=False).select_related("sku", "batch"):
            # goods go back into the batch they left from, at the cost they left at
            stock.receive(out.sku, -out.qty, out.unit_cost, cn, MovementType.RETURN, note=f"{number} / {invoice.number}", batch=out.batch)
    invoice.status = InvoiceStatus.CANCELLED
    invoice.save()
    Order.objects.filter(pk=invoice.order_id).update(status=OrderStatus.APPROVED)
    record(user, invoice, "cancelled", credit_note=number, reason=reason.strip(), goods_returned=goods_returned)
    return cn


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
    """Invoiced (not cancelled) minus everything received. Negative = customer is in credit."""
    billed = money_sum(customer.invoices.filter(status=InvoiceStatus.ISSUED).aggregate(t=Sum("grand_total"))["t"])
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
    """Where did this batch go? [(invoice, customer, packets)] from dispatch movements. Used for recalls."""
    from apps.inventory.models import StockMovement
    ct = ContentType.objects.get_for_model(Dispatch)
    out = {}
    for m in StockMovement.objects.filter(batch=batch, source_type=ct, movement_type=MovementType.DISPATCH):
        d = Dispatch.objects.select_related("invoice", "invoice__customer").get(pk=m.source_id)
        out[d.invoice_id] = (d.invoice, d.invoice.customer, out.get(d.invoice_id, (None, None, 0))[2] - m.qty)
    return list(out.values())
