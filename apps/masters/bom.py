"""BOM calculations: scale a standard batch to an arbitrary number of batches or target packets."""

from dataclasses import dataclass
from decimal import Decimal

from .models import Bom
from .units import convert


@dataclass(frozen=True)
class PlannedLine:
    item_id: int
    item_name: str
    unit: str  # stock unit
    planned_qty: Decimal  # in stock unit


def planned_consumption(bom: Bom, batches: Decimal) -> list[PlannedLine]:
    """Standard consumption for `batches` batches, expressed in each item's stock unit."""
    batches = Decimal(batches)
    return [
        PlannedLine(
            item_id=line.item_id,
            item_name=line.item.name,
            unit=line.item.unit.code,
            planned_qty=convert(line.qty_per_batch, line.unit, line.item.unit) * batches,
        )
        for line in bom.lines.select_related("item", "item__unit", "unit")
    ]


def planned_output(bom: Bom, batches: Decimal) -> Decimal:
    return Decimal(bom.batch_yield) * Decimal(batches)
