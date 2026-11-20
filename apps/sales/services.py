"""Order workflow: take (salesman) -> approve / reject (admin). Invoicing and dispatch come in M6."""

from dataclasses import dataclass
from decimal import Decimal

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from apps.audit.models import record
from apps.inventory import services as stock
from apps.masters.models import Customer, CustomerPrice, Sku

from .models import COMMITTED, Order, OrderLine, OrderStatus


class OrderError(Exception):
    pass


MAX_QTY = 1_000_000  # packets on one line; guards against typos and overflow
MAX_PRICE = Decimal("1000000")


def price_for(customer: Customer, sku: Sku) -> Decimal:
    """Customer-specific price if one is set, otherwise the SKU list price."""
    special = CustomerPrice.objects.filter(customer=customer, sku=sku).first()
    return special.price if special else sku.selling_price


@transaction.atomic
def create_order(salesman, customer: Customer, quantities: dict, client_uuid=None, note="", created_at=None) -> Order:
    """`quantities` maps Sku -> packets. Zero or blank quantities are ignored.

    Idempotent on `client_uuid`: submitting the same form twice returns the first order.
    """
    if client_uuid:
        existing = Order.objects.filter(client_uuid=client_uuid).first()
        if existing:
            if existing.salesman_id != salesman.pk:
                raise OrderError("This submission reference is already used")
            return existing
    if customer.salesman_id != salesman.pk or not customer.active:
        raise OrderError("This customer is not on your list")
    wanted = {sku: int(q) for sku, q in quantities.items() if q and int(q) > 0}
    if not wanted:
        raise OrderError("Add at least one product")
    if any(q > MAX_QTY for q in wanted.values()):
        raise OrderError(f"Quantity per product cannot exceed {MAX_QTY:,} packets")
    if any(not sku.active for sku in wanted):
        raise OrderError("An inactive product cannot be ordered")
    order = Order.objects.create(customer=customer, salesman=salesman, client_uuid=client_uuid, note=note,
                                 created_at=created_at or timezone.now())
    for sku, qty in wanted.items():
        p = price_for(customer, sku)
        OrderLine.objects.create(order=order, sku=sku, ordered_qty=qty, qty=qty, original_price=p, price=p)
    record(salesman, order, "submitted", customer=customer.name, lines={s.code: q for s, q in wanted.items()})
    return order


def committed_qty(sku: Sku, exclude_order=None) -> int:
    """Packets on approved/invoiced orders that have not been dispatched yet."""
    qs = OrderLine.objects.filter(sku=sku, order__status__in=COMMITTED)
    if exclude_order is not None:
        qs = qs.exclude(order=exclude_order)
    return qs.aggregate(t=Sum("qty"))["t"] or 0


@dataclass
class Coverage:
    line: OrderLine
    in_stock: Decimal
    committed: int  # promised to other approved orders
    available: Decimal
    shortage: int  # qty above what is available, 0 if covered

    @property
    def covered(self):
        return self.shortage == 0


def coverage(order: Order) -> list[Coverage]:
    """Does finished-goods stock cover each line? (Shown to the admin before approval.)

    There is no reservation: stock is deducted at dispatch. But packets already promised to other
    approved orders are subtracted so the same stock is not approved twice.
    """
    out = []
    for line in order.lines.select_related("sku"):
        in_stock = stock.balance(line.sku)
        committed = committed_qty(line.sku, exclude_order=order)
        available = max(in_stock - committed, Decimal("0"))
        out.append(Coverage(line, in_stock, committed, available, max(int(line.qty - available), 0)))
    return out


def _check_pending(order):
    if order.status != OrderStatus.SUBMITTED:
        raise OrderError(f"{order.number} is already {order.get_status_display().lower()}")


@transaction.atomic
def approve_order(order: Order, user, edits: dict | None = None, cap_to_available=False) -> Order:
    """Approve, optionally after editing. `edits` maps line id -> (qty, price).

    Approval is refused if any line exceeds available stock, unless `cap_to_available` reduces
    those lines to what is available (a partial approval). Lines reduced to 0 are dropped.
    """
    order = Order.objects.select_for_update().get(pk=order.pk)
    _check_pending(order)
    lines = {l.pk: l for l in order.lines.select_related("sku")}
    changes = {}
    for line_id, (qty, price) in (edits or {}).items():
        line = lines[line_id]
        qty, price = int(qty), Decimal(price)
        if not price.is_finite() or qty < 0 or price < 0:
            raise OrderError("Quantity and price cannot be negative")
        if qty > MAX_QTY or price > MAX_PRICE:
            raise OrderError("Quantity or price is unrealistically large")
        if (qty, price) != (line.qty, line.price):
            changes[line.sku.code] = {"qty": [line.qty, qty], "price": [str(line.price), str(price)]}
            line.qty, line.price = qty, price
            line.save()
    for c in coverage(order):
        if c.shortage:
            if not cap_to_available:
                raise OrderError(f"Only {int(c.available)} packets of {c.line.sku} are available; "
                                 f"{c.line.qty} requested. Reduce the quantity or approve the available amount.")
            new_qty = int(c.available)
            changes.setdefault(c.line.sku.code, {})["qty"] = [c.line.qty, new_qty]
            c.line.qty = new_qty
            c.line.save()
    if not any(l.qty > 0 for l in order.lines.all()):
        raise OrderError("Nothing left to approve: every line is zero. Reject the order instead.")
    order.status, order.reviewed_by, order.reviewed_at = OrderStatus.APPROVED, user, timezone.now()
    order.save()
    record(user, order, "approved", changes=changes, capped=cap_to_available)
    return order


@transaction.atomic
def reject_order(order: Order, user, reason: str) -> Order:
    order = Order.objects.select_for_update().get(pk=order.pk)
    _check_pending(order)
    if not reason.strip():
        raise OrderError("A reason is required to reject an order")
    order.status, order.reviewed_by, order.reviewed_at, order.reject_reason = (
        OrderStatus.REJECTED, user, timezone.now(), reason.strip())
    order.save()
    record(user, order, "rejected", reason=reason.strip())
    return order


def customer_wise_sales(salesman=None):
    """Value and count of orders that were approved (approved, invoiced, dispatched), per customer."""
    qs = Order.objects.filter(status__in=[OrderStatus.APPROVED, OrderStatus.INVOICED, OrderStatus.DISPATCHED])
    if salesman is not None:
        qs = qs.filter(salesman=salesman)
    totals = {}
    for o in qs.select_related("customer").prefetch_related("lines"):
        row = totals.setdefault(o.customer_id, {"customer": o.customer, "orders": 0, "packets": 0, "value": Decimal("0")})
        row["orders"] += 1
        row["packets"] += sum(l.qty for l in o.lines.all())
        row["value"] += o.taxable_total
    return sorted(totals.values(), key=lambda r: -r["value"])
