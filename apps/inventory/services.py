"""All writes to stock go through here. Views and other apps never create movements directly."""

from dataclasses import dataclass
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from apps.masters.models import Item, Sku
from apps.masters.units import convert

from .models import ADJUSTMENT_TYPES, Batch, MovementType, StockAdjustment, StockMovement

COST_PLACES = Decimal("0.0001")
QTY_PLACES = Decimal("0.0001")
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
    return Decimal(qs.aggregate(t=Sum("qty"))["t"] or 0).quantize(QTY_PLACES)


def average_cost(stockable) -> Decimal:
    """Weighted-average cost per stock unit, replayed from the ledger in order.

    Receipts (qty > 0 with a cost) blend into the average; issues leave the average unchanged;
    opening balances count as receipts. Replay keeps the ledger the single source of truth.
    """
    qty, value = ZERO, ZERO
    rows = StockMovement.objects.filter(**_filter(stockable)).order_by("moved_at", "id").values_list("qty", "unit_cost")
    for mqty, cost in rows.iterator(chunk_size=5000):  # plain tuples: far cheaper than model instances
        if mqty > 0:
            value += mqty * cost
            qty += mqty
        else:
            avg = (value / qty) if qty > 0 else ZERO
            qty += mqty
            value = qty * avg if qty > 0 else ZERO
    return (value / qty).quantize(COST_PLACES, ROUND_HALF_UP) if qty > 0 else _fallback_cost(stockable)


def _fallback_cost(stockable) -> Decimal:
    return stockable.standard_rate if isinstance(stockable, Item) else ZERO


def _post(stockable, movement_type, qty, unit_cost, source, moved_at=None, note="", batch=None) -> StockMovement:
    if source is None or source.pk is None:
        raise StockError("Every stock movement needs a saved source document")
    if isinstance(stockable, Sku) != (batch is not None):
        raise StockError("Finished goods need a batch; materials must not have one")
    if batch is not None and batch.sku_id != stockable.pk:
        raise StockError(f"Batch {batch.number} belongs to a different product")
    return StockMovement.objects.create(
        **_filter(stockable), batch=batch, movement_type=movement_type, qty=qty, unit_cost=unit_cost,
        moved_at=moved_at or timezone.now(), source_type=ContentType.objects.get_for_model(source),
        source_id=source.pk, note=note,
    )


def _lock(stockable):
    """Serialise concurrent postings on the same item (row lock on PostgreSQL)."""
    type(stockable).objects.select_for_update().get(pk=stockable.pk)


@transaction.atomic
def receive(stockable, qty, unit_cost, source, movement_type=MovementType.PURCHASE, moved_at=None, note="", unit=None, batch=None):
    """Add stock. If `unit` is given, `qty` and `unit_cost` (per that unit) are converted to the stock unit.
    Finished goods must name the `batch` they join."""
    qty, unit_cost = Decimal(qty), Decimal(unit_cost)
    if unit is not None and isinstance(stockable, Item):
        ratio = convert(1, unit, stockable.unit)  # stock units per entered unit
        qty, unit_cost = qty * ratio, unit_cost / ratio
    if qty <= 0:
        raise StockError("Receipt quantity must be positive")
    _lock(stockable)
    return _post(stockable, movement_type, qty, unit_cost.quantize(COST_PLACES, ROUND_HALF_UP), source, moved_at, note, batch)


# ---------- batches (finished goods) ----------

def batch_balance(batch: Batch) -> Decimal:
    return Decimal(StockMovement.objects.filter(batch=batch).aggregate(t=Sum("qty"))["t"] or 0).quantize(QTY_PLACES)


def batches_with_stock(sku: Sku, include_expired=True, today=None):
    """[(batch, on hand)] with stock > 0, first-expiry-first. Expired batches are optional."""
    today = today or timezone.localdate()
    qs = Batch.objects.filter(sku=sku)
    if not include_expired:
        qs = qs.filter(expiry_date__gte=today)
    out = []
    for b in qs.order_by("expiry_date", "mfg_date", "id"):
        bal = batch_balance(b)
        if bal > 0:
            out.append((b, bal))
    return out


def saleable_balance(sku: Sku, today=None) -> Decimal:
    """Finished goods that may be sold: stock in batches that have not expired."""
    return sum((bal for _, bal in batches_with_stock(sku, include_expired=False, today=today)), ZERO).quantize(QTY_PLACES)


def expired_balance(sku: Sku, today=None) -> Decimal:
    return (balance(sku) - saleable_balance(sku, today)).quantize(QTY_PLACES)


def _allocate(sku, qty, include_expired):
    """Split `qty` over batches, earliest expiry first. Returns [(batch, qty)] or raises InsufficientStock."""
    plan, left = [], qty
    for b, bal in batches_with_stock(sku, include_expired=include_expired):
        take = min(bal, left)
        plan.append((b, take))
        left -= take
        if left <= 0:
            return plan
    available = sum((bal for _, bal in batches_with_stock(sku, include_expired=include_expired)), ZERO)
    err = InsufficientStock(sku, available, qty)
    if not include_expired and expired_balance(sku) > 0:
        err.args = (f"{err.args[0]} (a further {expired_balance(sku)} packets are past expiry and cannot be sold)",)
    raise err


@transaction.atomic
def issue_fefo(sku: Sku, qty, source, movement_type, moved_at=None, note="", include_expired=False) -> list[StockMovement]:
    """Issue finished goods first-expiry-first-out: one movement per batch drawn from, all at the SKU's
    weighted-average cost. Expired batches are skipped (sale) unless `include_expired` (write-off)."""
    qty = Decimal(qty)
    if qty <= 0:
        raise StockError("Issue quantity must be positive")
    _lock(sku)
    plan = _allocate(sku, qty, include_expired)
    cost = average_cost(sku)
    return [_post(sku, movement_type, -take, cost, source, moved_at, note, batch) for batch, take in plan]


@transaction.atomic
def issue(stockable, qty, source, movement_type, moved_at=None, note="", unit=None, allow_negative=False, batch=None):
    """Remove stock at the current weighted-average cost. Refuses to go below zero unless allowed.

    Finished goods: with `batch`, issue from that batch only; without, first-expiry-first-out (returns a list)."""
    qty = Decimal(qty)
    if isinstance(stockable, Sku):
        if batch is None:
            return issue_fefo(stockable, qty, source, movement_type, moved_at, note)
        if qty <= 0:
            raise StockError("Issue quantity must be positive")
        _lock(stockable)
        if qty > batch_balance(batch):
            raise InsufficientStock(f"{stockable} batch {batch.number}", batch_balance(batch), qty)
        return _post(stockable, movement_type, -qty, average_cost(stockable), source, moved_at, note, batch)
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
def adjust(stockable, qty, reason, user, unit_cost=None, is_opening=False, moved_at=None, batch=None) -> StockMovement:
    """Manual correction (signed qty in stock unit) or opening balance, recorded as its own document.

    Finished goods: an addition needs a `batch`; a removal names a batch or, if none, takes the
    earliest-expiring stock (expired batches included, so write-offs work)."""
    qty = Decimal(qty)
    if qty == 0:
        raise StockError("Adjustment quantity cannot be zero")
    _lock(stockable)
    is_sku = isinstance(stockable, Sku)
    if is_sku and qty > 0 and batch is None:
        raise StockError("Choose the batch the finished goods belong to")
    if qty < 0 and balance(stockable) + qty < 0:
        raise InsufficientStock(stockable, balance(stockable), -qty)
    cost = Decimal(unit_cost) if unit_cost is not None else (average_cost(stockable) if qty < 0 else _fallback_cost(stockable))
    doc = StockAdjustment.objects.create(**_filter(stockable), qty=qty, unit_cost=cost, is_opening=is_opening,
                                         reason=reason, user=user)
    mtype = MovementType.OPENING if is_opening else MovementType.ADJUSTMENT
    if not is_sku:
        return _post(stockable, mtype, qty, cost, doc, moved_at, reason)
    if qty > 0:
        return _post(stockable, mtype, qty, cost, doc, moved_at, reason, batch)
    if batch is not None:
        if -qty > batch_balance(batch):
            raise InsufficientStock(f"{stockable} batch {batch.number}", batch_balance(batch), -qty)
        return _post(stockable, mtype, qty, cost, doc, moved_at, reason, batch)
    moves = [_post(stockable, mtype, -take, cost, doc, moved_at, reason, b) for b, take in _allocate(stockable, -qty, True)]
    return moves[-1]


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


@transaction.atomic
def reverse_source(source, note, moved_at=None):
    """Void a document's stock effect: post an opposite movement for each of its movements.

    Original movements stay in the ledger; the reversal rows cancel them. A reversal that would
    take stock below zero (e.g. finished goods already sold) is refused and nothing is posted.
    """
    ct = ContentType.objects.get_for_model(source)
    originals = list(StockMovement.objects.filter(source_type=ct, source_id=source.pk)
                     .exclude(movement_type=MovementType.REVERSAL).select_related("item", "sku", "batch"))
    if StockMovement.objects.filter(source_type=ct, source_id=source.pk, movement_type=MovementType.REVERSAL).exists():
        raise StockError("This document has already been reversed")
    for m in originals:
        stockable = m.stockable
        _lock(stockable)
        on_hand = batch_balance(m.batch) if m.batch_id else balance(stockable)  # the same batch must still hold it
        if m.qty > 0 and on_hand < m.qty:
            raise InsufficientStock(stockable, on_hand, m.qty)
        _post(stockable, MovementType.REVERSAL, -m.qty, m.unit_cost, source, moved_at, note, m.batch)
    return len(originals)


@dataclass
class LowStock:
    item: Item
    balance: Decimal
    level: Decimal

    @property
    def shortfall(self):
        return self.level - self.balance


def low_stock() -> list[LowStock]:
    """Active items whose balance is at or below their reorder level (items with no level set are ignored)."""
    out = []
    for item in Item.objects.filter(active=True, reorder_level__isnull=False):
        b = balance(item)
        if b <= item.reorder_level:
            out.append(LowStock(item, b, item.reorder_level))
    return sorted(out, key=lambda x: x.item.name)


@dataclass
class ExpiryAlert:
    batch: Batch
    on_hand: Decimal
    days_left: int  # negative = already expired

    @property
    def expired(self):
        return self.days_left < 0


NEAR_EXPIRY_DAYS = 14


def expiry_alerts(days=NEAR_EXPIRY_DAYS, today=None) -> list[ExpiryAlert]:
    """Batches with stock that are expired or expire within `days` days, soonest first."""
    today = today or timezone.localdate()
    out = []
    for b in Batch.objects.select_related("sku").filter(expiry_date__lte=today + timedelta(days=days)).order_by("expiry_date"):
        bal = batch_balance(b)
        if bal > 0:
            out.append(ExpiryAlert(b, bal, (b.expiry_date - today).days))
    return out
