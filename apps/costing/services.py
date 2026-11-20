"""Cost per packet.

Method (also in the design note):
- Materials and packaging: weighted-average cost at the moment each item was issued (inventory ledger).
  Damaged pouches and other wastage are part of the run's cost, so they raise the cost of the good packets.
- Overheads entered against a run are charged to that run in full.
- Overheads entered as a monthly pool are shared among the runs completed in that month in proportion
  to good packets (a simple, explainable rule; every packet absorbs the same overhead).
- Cost per packet = run cost / good packets. Margin = selling price - cost per packet.
Finished-goods stock is valued at material cost only; full cost per packet is a costing report figure.
"""

from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from django.db.models import Sum
from django.utils import timezone

from apps.audit.models import record
from apps.production.models import ProductionRun, RunStatus
from apps.production.services import material_cost

from .models import OverheadEntry, OverheadKind

ZERO = Decimal("0")
PAISA = Decimal("0.01")
COST4 = Decimal("0.0001")


class CostingError(Exception):
    pass


def add_overhead(user, kind, amount, incurred_on=None, run=None, description="") -> OverheadEntry:
    amount = Decimal(amount)
    if amount <= 0:
        raise CostingError("Amount must be more than zero")
    if run is not None and run.status == RunStatus.VOIDED:
        raise CostingError("Cannot add cost to a voided run")
    entry = OverheadEntry.objects.create(kind=kind, amount=amount, run=run, description=description, created_by=user,
                                         **({"incurred_on": incurred_on} if incurred_on else {}))
    record(user, entry, "added", kind=kind, amount=str(amount), run=run.number if run else "monthly pool")
    return entry


def void_overhead(user, entry: OverheadEntry, reason: str) -> OverheadEntry:
    if entry.voided:
        raise CostingError("Already voided")
    if not reason.strip():
        raise CostingError("A reason is required")
    entry.voided, entry.void_reason = True, reason.strip()
    entry.save()
    record(user, entry, "voided", reason=reason.strip())
    return entry


def allocate(pool: Decimal, weights: list[int]) -> list[Decimal]:
    """Split `pool` in proportion to `weights` to the paisa; rounding remainder goes to the last share so
    the parts always add up to exactly `pool`."""
    total = sum(weights)
    if total == 0:
        return [ZERO] * len(weights)
    shares = [(pool * w / total).quantize(PAISA, ROUND_HALF_UP) for w in weights]
    shares[-1] += pool - sum(shares)
    return shares


def month_bounds(year, month):
    start = date(year, month, 1)
    return start, date(year + (month == 12), month % 12 + 1, 1)


def completed_runs_in_month(year, month):
    start, end = month_bounds(year, month)
    return list(ProductionRun.objects.filter(status=RunStatus.COMPLETED, completed_at__date__gte=start,
                                             completed_at__date__lt=end, good_packets__gt=0).order_by("completed_at", "id"))


def monthly_pool(year, month, kind=None) -> Decimal:
    start, end = month_bounds(year, month)
    qs = OverheadEntry.objects.filter(run__isnull=True, voided=False, incurred_on__gte=start, incurred_on__lt=end)
    if kind:
        qs = qs.filter(kind=kind)
    return qs.aggregate(t=Sum("amount"))["t"] or ZERO


def monthly_allocation(year, month) -> tuple[dict, Decimal]:
    """Returns ({run_id: allocated pool}, unallocated pool). Unallocated > 0 means no run completed that month."""
    runs, pool = completed_runs_in_month(year, month), monthly_pool(year, month)
    if not runs:
        return {}, pool
    shares = allocate(pool, [r.good_packets for r in runs])
    return {r.pk: s for r, s in zip(runs, shares)}, ZERO


@dataclass
class RunCost:
    run: ProductionRun
    material: Decimal
    direct: dict = field(default_factory=dict)  # OverheadKind -> amount charged to this run
    allocated: Decimal = ZERO  # share of the monthly pool

    @property
    def overhead(self):
        return sum(self.direct.values(), ZERO) + self.allocated

    @property
    def total(self):
        return self.material + self.overhead

    @property
    def good(self):
        return self.run.good_packets or 0

    @property
    def per_packet(self):
        return (self.total / self.good).quantize(COST4, ROUND_HALF_UP) if self.good else None

    @property
    def price(self):
        return self.run.sku.selling_price

    @property
    def margin(self):
        return (self.price - self.per_packet) if self.per_packet is not None else None

    @property
    def margin_pct(self):
        return (self.margin / self.price * 100).quantize(PAISA, ROUND_HALF_UP) if self.margin is not None and self.price else None


def run_cost(run: ProductionRun, _alloc_cache=None) -> RunCost:
    direct = {k: ZERO for k in OverheadKind.values}
    for o in run.overheads.filter(voided=False):
        direct[o.kind] += o.amount
    allocated = ZERO
    if run.status == RunStatus.COMPLETED and run.completed_at:
        d = timezone.localtime(run.completed_at).date()
        key = (d.year, d.month)
        cache = _alloc_cache if _alloc_cache is not None else {}
        if key not in cache:
            cache[key] = monthly_allocation(*key)[0]
        allocated = cache[key].get(run.pk, ZERO)
    return RunCost(run, material_cost(run), direct, allocated)


def runs_costs(date_from: date, date_to: date) -> list[RunCost]:
    """Cost of every run completed in the date range (voided and unfinished runs are excluded)."""
    runs = ProductionRun.objects.filter(status=RunStatus.COMPLETED, completed_at__date__range=(date_from, date_to)) \
        .select_related("sku").order_by("completed_at", "id")
    cache = {}
    return [run_cost(r, cache) for r in runs]


@dataclass
class SkuCost:
    sku: object
    runs: int
    good: int
    total: Decimal
    per_packet: Decimal | None
    price: Decimal
    margin: Decimal | None
    margin_pct: Decimal | None


def sku_costs(date_from: date, date_to: date) -> list[SkuCost]:
    """Weighted cost per packet for each product over the range: total run cost / total good packets."""
    by_sku = {}
    for rc in runs_costs(date_from, date_to):
        agg = by_sku.setdefault(rc.run.sku_id, {"sku": rc.run.sku, "runs": 0, "good": 0, "total": ZERO})
        agg["runs"] += 1
        agg["good"] += rc.good
        agg["total"] += rc.total
    out = []
    for a in by_sku.values():
        per = (a["total"] / a["good"]).quantize(COST4, ROUND_HALF_UP) if a["good"] else None
        price = a["sku"].selling_price
        margin = price - per if per is not None else None
        pct = (margin / price * 100).quantize(PAISA, ROUND_HALF_UP) if margin is not None and price else None
        out.append(SkuCost(a["sku"], a["runs"], a["good"], a["total"], per, price, margin, pct))
    return out
