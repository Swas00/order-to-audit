"""Production workflow. Every state change is one transaction, writes ledger movements through the
inventory service, and is recorded in the audit log."""

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from apps.audit.models import record
from apps.inventory import services as stock
from apps.inventory.models import Batch, MovementType, StockMovement
from apps.masters.bom import planned_consumption, planned_output
from apps.masters.models import CompanyProfile, Item, ItemKind, Sku

from . import variance as var
from .models import ProductionRun, RunLine, RunStatus

ZERO = Decimal("0")


class ProductionError(Exception):
    pass


def _locked(run) -> ProductionRun:
    return ProductionRun.objects.select_for_update().get(pk=run.pk)


def tolerance_for(sku: Sku) -> Decimal:
    return sku.variance_tolerance_pct if sku.variance_tolerance_pct is not None else CompanyProfile.get().global_variance_tolerance_pct


@transaction.atomic
def start_run(sku: Sku, batches, user, started_at=None) -> ProductionRun:
    batches = Decimal(batches)
    if batches <= 0:
        raise ProductionError("Batch size must be positive")
    bom = sku.boms.filter(is_active=True).first()
    if bom is None:
        raise ProductionError(f"{sku} has no active BOM")
    run = ProductionRun.objects.create(sku=sku, bom=bom, batches=batches, expected_output=planned_output(bom, batches),
                                       created_by=user, started_at=started_at or timezone.now())
    for p in planned_consumption(bom, batches):
        RunLine.objects.create(run=run, item_id=p.item_id, planned_qty=p.planned_qty)
    record(user, run, "started", sku=str(sku), batches=str(batches), expected_output=str(run.expected_output))
    return run


def _add_actual(run, item, qty):
    line, created = RunLine.objects.get_or_create(run=run, item=item, defaults={"planned_qty": ZERO})
    RunLine.objects.filter(pk=line.pk).update(actual_qty=F("actual_qty") + qty)


@transaction.atomic
def issue_material(run, item: Item, qty, user, unit=None) -> StockMovement:
    """Stage 1: issue raw material to the run (consumption is recorded now)."""
    run = _locked(run)
    if run.status != RunStatus.IN_PROGRESS:
        raise ProductionError("Materials can only be issued to a run that is in progress")
    if item.kind != ItemKind.RAW:
        raise ProductionError("Only raw materials are issued here; packaging is recorded on completion")
    m = stock.issue(item, qty, run, MovementType.PRODUCTION_ISSUE, unit=unit, note=run.number)
    _add_actual(run, item, -m.qty)
    record(user, run, "issued", item=item.name, qty=str(-m.qty), unit=item.unit.code)
    return m


@transaction.atomic
def complete_run(run, good_packets, damaged_pouches, packaging_used, user, remark="") -> ProductionRun:
    """Stage 2: record actual output and packaging.

    `packaging_used` maps packaging Item -> quantity in stock unit for packaging other than the SKU's
    pouch. The pouch is derived: good packets are packed in pouches (packaging use) and damaged
    pouches are written off (wastage). Finished goods are added at material cost per good packet.
    """
    run = _locked(run)
    if run.status != RunStatus.IN_PROGRESS:
        raise ProductionError("Only a run that is in progress can be completed")
    good, damaged = int(good_packets), int(damaged_pouches)
    if good < 0 or damaged < 0:
        raise ProductionError("Quantities cannot be negative")
    pouch = run.sku.pouch_item
    if pouch is None:
        raise ProductionError(f"{run.sku} has no pouch item set")

    def use(item, qty, mtype):
        qty = Decimal(qty)
        if qty > 0:
            m = stock.issue(item, qty, run, mtype, note=run.number)
            _add_actual(run, item, qty)
        return qty

    use(pouch, good, MovementType.PRODUCTION_PACKAGING)
    use(pouch, damaged, MovementType.PRODUCTION_WASTAGE)
    for item, qty in packaging_used.items():
        if item.kind != ItemKind.PACKAGING or item == pouch:
            raise ProductionError(f"{item.name} is not a packaging item other than the pouch")
        use(item, qty, MovementType.PRODUCTION_PACKAGING)

    if good > 0:
        cost = material_cost(run)
        made = timezone.localdate()
        batch = Batch.objects.create(sku=run.sku, number=f"{run.sku.code}-{made:%y%m%d}-{run.pk:03d}", mfg_date=made,
                                     expiry_date=made + timedelta(days=run.sku.shelf_life_days), run=run)
        stock.receive(run.sku, good, cost / good, run, MovementType.PRODUCTION_OUTPUT, note=f"{run.number} batch {batch.number}",
                      batch=batch)
    run.good_packets, run.damaged_pouches, run.remark = good, damaged, remark
    run.status, run.completed_at = RunStatus.COMPLETED, timezone.now()
    run.save()
    record(user, run, "completed", batch=batch.number if good > 0 else None, good_packets=good, damaged_pouches=damaged,
           packaging={i.name: str(q) for i, q in packaging_used.items()}, remark=remark)
    return run


@transaction.atomic
def void_run(run, reason, user) -> ProductionRun:
    """Correct a mistaken run: reverse every stock movement, keep the run and its history."""
    run = _locked(run)
    if run.status == RunStatus.VOIDED:
        raise ProductionError("Run is already voided")
    if not reason.strip():
        raise ProductionError("A reason is required")
    stock.reverse_source(run, f"Void {run.number}: {reason}")
    run.status, run.void_reason = RunStatus.VOIDED, reason
    run.save()
    record(user, run, "voided", reason=reason)
    return run


def run_movements(run):
    ct = ContentType.objects.get_for_model(run)
    return StockMovement.objects.filter(source_type=ct, source_id=run.pk).exclude(movement_type=MovementType.REVERSAL)


CONSUMPTION_TYPES = (MovementType.PRODUCTION_ISSUE, MovementType.PRODUCTION_PACKAGING, MovementType.PRODUCTION_WASTAGE)


def material_cost(run) -> Decimal:
    """Cost of everything consumed by the run at weighted-average cost when it was issued."""
    return sum((-m.qty * m.unit_cost for m in run_movements(run) if m.movement_type in CONSUMPTION_TYPES), ZERO)


@dataclass
class LineReport:
    item: Item
    unit: str
    variance: var.Variance


@dataclass
class RunReport:
    run: ProductionRun
    tolerance: Decimal
    lines: list
    yield_variance: var.Variance | None
    wastage_packets: Decimal | None  # expected - good (shortfall); damaged pouches shown separately
    material_cost: Decimal
    alerts: int


def run_report(run: ProductionRun) -> RunReport:
    tol = tolerance_for(run.sku)
    lines = [LineReport(l.item, l.item.unit.code, var.variance(l.planned_qty, l.actual_qty, tol, True))
             for l in run.lines.select_related("item", "item__unit")]
    completed = run.status == RunStatus.COMPLETED
    yv = var.variance(run.expected_output, run.good_packets, tol, higher_is_adverse=False) if completed else None
    alerts = sum(1 for l in lines if l.variance.status == var.FLAG) + (1 if yv and yv.status == var.FLAG else 0)
    return RunReport(run, tol, lines, yv, (run.expected_output - run.good_packets) if completed else None,
                     material_cost(run), alerts)
