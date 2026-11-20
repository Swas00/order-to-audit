"""Production planning from pending orders: "to fulfil today's orders, make N batches".

For each SKU:  demand = packets on submitted orders (optional) + approved/invoiced orders not yet dispatched
               net need = demand - finished goods in stock
               batches  = ceil(net need / BOM batch yield)
then the BOM is scaled to those batches and compared with raw/packaging stock to list what to buy.
"""

import math
from dataclasses import dataclass, field
from decimal import Decimal

from django.db.models import Sum

from apps.inventory import services as stock
from apps.masters.bom import planned_consumption
from apps.masters.models import Item, Sku
from apps.sales.models import COMMITTED, OrderLine, OrderStatus

ZERO = Decimal("0")


@dataclass
class MaterialNeed:
    item_name: str
    unit: str
    needed: Decimal
    on_hand: Decimal

    @property
    def short(self):
        return max(self.needed - self.on_hand, ZERO)


@dataclass
class PlanRow:
    sku: Sku
    in_stock: Decimal
    submitted: int
    committed: int
    batches: int
    batch_yield: int
    materials: list = field(default_factory=list)

    @property
    def demand(self):
        return self.submitted + self.committed

    @property
    def net_need(self):
        return max(Decimal(self.demand) - self.in_stock, ZERO)

    @property
    def output(self):
        return self.batches * self.batch_yield

    @property
    def can_make(self):
        return all(m.short == 0 for m in self.materials)

    @property
    def shortages(self):
        return [m for m in self.materials if m.short > 0]


def _demand(sku, statuses):
    return OrderLine.objects.filter(sku=sku, order__status__in=statuses).aggregate(t=Sum("qty"))["t"] or 0


def plan(include_submitted=True) -> list[PlanRow]:
    rows = []
    for sku in Sku.objects.filter(active=True):
        bom = sku.boms.filter(is_active=True).first()
        if bom is None:
            continue
        submitted = _demand(sku, [OrderStatus.SUBMITTED]) if include_submitted else 0
        row = PlanRow(sku, stock.saleable_balance(sku), submitted, _demand(sku, COMMITTED), 0, bom.batch_yield)
        row.batches = math.ceil(row.net_need / bom.batch_yield) if row.net_need > 0 else 0
        if row.batches:
            row.materials = [MaterialNeed(p.item_name, p.unit, p.planned_qty, stock.balance(Item.objects.get(pk=p.item_id)))
                             for p in planned_consumption(bom, Decimal(row.batches))]
        rows.append(row)
    return rows

