"""All writes to stock go through here. Views and other apps never create movements directly."""

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from apps.masters.models import Item, Sku
from apps.masters.units import convert

from .models import ADJUSTMENT_TYPES, MovementType, StockAdjustment, StockMovement

COST_PLACES = Decimal("0.0001")
ZERO = Decimal("0")


class StockError(Exception):
    pass


class InsufficientStock(StockError):
    def __init__(self, stockable, available, requested):
        super().__init__(f"Insufficient stock of {stockable}: available {available}, requested {requested}")
        self.available, self.requested = available, requested


def _filter(stockable):
    return {"sku": stockable} if isinstance(stockable, Sku) else {"item": stockable}


def balance(stockable, as_of=None) -> Decimal:
    qs = StockMovement.objects.filter(**_filter(stockable))
    if as_of is not None:
        qs = qs.filter(moved_at__lte=as_of)
    return qs.aggregate(t=Sum("qty"))["t"] or ZERO


def average_cost(stockable) -> Decimal:
    """Weighted-average cost per stock unit, replayed from the ledger in order.

    Receipts (qty > 0 with a cost) blend into the average; issues leave the average unchanged;
    opening balances count as receipts. Replay keeps the ledger the single source of truth.
    """
    qty, value = ZERO, ZERO
    for m in StockMovement.objects.filter(**_filter(stockable)).order_by("moved_at", "id"):
        if m.qty > 0:
            value += m.qty * m.unit_cost
            qty += m.qty
        else:
            avg = (value / qty) if qty > 0 else ZERO
            qty += m.qty
            value = qty * avg if qty > 0 else ZERO
    return (value / qty).quantize(COST_PLACES, ROUND_HALF_UP) if qty > 0 else _fallback_cost(stockable)


def _fallback_cost(stockable) -> Decimal:
    return stockable.standard_rate if isinstance(stockable, Item) else ZERO


def _post(stockable, movement_type, qty, unit_cost, source, moved_at=None, note="") -> StockMovement:
    if source is None or source.pk is None:
        raise StockError("Every stock movement needs a saved source document")
    return StockMovement.objects.create(
        **_filter(stockable), movement_type=movement_type, qty=qty, unit_cost=unit_cost,
        moved_at=moved_at or timezone.now(), source_type=ContentType.objects.get_for_model(source),
        source_id=source.pk, note=note,
    )


def _lock(stockable):
    """Serialise concurrent postings on the same item (row lock on PostgreSQL)."""
    type(stockable).objects.select_for_update().get(pk=stockable.pk)


@transaction.atomic
def receive(stockable, qty, unit_cost, source, movement_type=MovementType.PURCHASE, moved_at=None, note="", unit=None):
    """Add stock. If `unit` is given, `qty` and `unit_cost` (per that unit) are converted to the stock unit."""
    qty, unit_cost = Decimal(qty), Decimal(unit_cost)
    if unit is not None and isinstance(stockable, Item):
        ratio = convert(1, unit, stockable.unit)  # stock units per entered unit
        qty, unit_cost = qty * ratio, unit_cost / ratio
    if qty <= 0:
        raise StockError("Receipt quantity must be positive")
    _lock(stockable)
    return _post(stockable, movement_type, qty, unit_cost.quantize(COST_PLACES, ROUND_HALF_UP), source, moved_at, note)


@transaction.atomic
def issue(stockable, qty, source, movement_type, moved_at=None, note="", unit=None, allow_negative=False) -> StockMovement:
    """Remove stock at the current weighted-average cost. Refuses to go below zero unless allowed."""
    qty = Decimal(qty)
    if unit is not None and isinstance(stockable, Item):
        qty = convert(qty, unit, stockable.unit)
    if qty <= 0:
        raise StockError("Issue quantity must be positive")
    _lock(stockable)
    available = balance(stockable)
    if qty > available and not allow_negative:
        raise InsufficientStock(stockable, available, qty)
    return _post(stockable, movement_type, -qty, average_cost(stockable), source, moved_at, note)


@transaction.atomic
def adjust(stockable, qty, reason, user, unit_cost=None, is_opening=False, moved_at=None) -> StockMovement:
    """Manual correction (signed qty in stock unit) or opening balance, recorded as its own document."""
    qty = Decimal(qty)
    if qty == 0:
        raise StockError("Adjustment quantity cannot be zero")
    _lock(stockable)
    if qty < 0 and balance(stockable) + qty < 0:
        raise InsufficientStock(stockable, balance(stockable), -qty)
    cost = Decimal(unit_cost) if unit_cost is not None else (average_cost(stockable) if qty < 0 else _fallback_cost(stockable))
    doc = StockAdjustment.objects.create(**_filter(stockable), qty=qty, unit_cost=cost, is_opening=is_opening,
                                         reason=reason, user=user)
    mtype = MovementType.OPENING if is_opening else MovementType.ADJUSTMENT
    return _post(stockable, mtype, qty, cost, doc, moved_at, reason)


@dataclass
class SummaryRow:
    stockable: object
    opening: Decimal
    receipts: Decimal
    issues: Decimal
    adjustments: Decimal
    closing: Decimal
    value: Decimal


def stock_summary(stockables, date_from=None, date_to=None) -> list[SummaryRow]:
    """opening + receipts - issues +/- adjustments = closing, per stock item over [date_from, date_to].

    Opening balance movements dated inside the range count as adjustments only if is_opening is False;
    an opening movement dated at or before `date_from` simply contributes to the opening column.
    """
    rows = []
    for s in stockables:
        movements = StockMovement.objects.filter(**_filter(s))
        opening = movements.filter(moved_at__lt=date_from) if date_from else movements.none()
        opening_qty = opening.aggregate(t=Sum("qty"))["t"] or ZERO
        period = movements
        if date_from:
            period = period.filter(moved_at__gte=date_from)
        if date_to:
            period = period.filter(moved_at__lte=date_to)
        receipts = issues = adjustments = ZERO
        for m in period:
            if m.movement_type in ADJUSTMENT_TYPES:
                adjustments += m.qty
            elif m.qty > 0:
                receipts += m.qty
            else:
                issues += -m.qty
        closing = opening_qty + receipts - issues + adjustments
        rows.append(SummaryRow(s, opening_qty, receipts, issues, adjustments, closing, closing * average_cost(s)))
    return rows


def ledger(stockable, date_from=None, date_to=None):
    """Returns (balance brought forward at date_from, [(movement, running balance)]) oldest first."""
    movements = StockMovement.objects.filter(**_filter(stockable)).select_related("source_type")
    running = (movements.filter(moved_at__lt=date_from).aggregate(t=Sum("qty"))["t"] or ZERO) if date_from else ZERO
    brought_forward = running
    period = movements
    if date_from:
        period = period.filter(moved_at__gte=date_from)
    if date_to:
        period = period.filter(moved_at__lte=date_to)
    out = []
    for m in period:
        running += m.qty
        out.append((m, running))
    return brought_forward, out
