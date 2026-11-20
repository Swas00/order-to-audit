"""Load the benchmark master data (problem statement section 5). Idempotent.

Opening stock quantities are loaded by the inventory seed in Milestone 3 (they need the ledger).
"""

from decimal import Decimal as D

from django.core.management.base import BaseCommand

from apps.accounts.models import User
from apps.masters.models import Bom, BomLine, CompanyProfile, Customer, Item, ItemKind, Sku, Unit

UNITS = [
    ("kg", "Kilogram", "mass", "1000"), ("g", "Gram", "mass", "1"),
    ("L", "Litre", "volume", "1000"), ("ml", "Millilitre", "volume", "1"),
    ("piece", "Piece", "count", "1"), ("packet", "Packet", "count", "1"),
    ("carton", "Carton of packets", "pack", "1"),
]

# code, name, kind, unit, standard rate per stock unit
ITEMS = [
    ("FLOUR", "Flour", ItemKind.RAW, "kg", "42"),
    ("OIL", "Cooking oil", ItemKind.RAW, "L", "140"),
    ("SPICE", "Salt and spice mix", ItemKind.RAW, "kg", "200"),
    ("POUCH200", "200 g pouch", ItemKind.PACKAGING, "piece", "2.50"),
    ("CARTON20", "Carton (holds 20 packets)", ItemKind.PACKAGING, "piece", "15"),
]

# Reorder levels (our assumption; the brief gives none). Chosen so the benchmark visibly trips two alerts.
REORDER = {"FLOUR": "50", "OIL": "20", "SPICE": "2", "POUCH200": "200", "CARTON20": "25"}

# item code, quantity per batch, unit
BOM_LINES = [("FLOUR", "40", "kg"), ("OIL", "10", "L"), ("SPICE", "1.2", "kg"),
             ("POUCH200", "200", "piece"), ("CARTON20", "10", "piece")]


def seed_masters():
    units = {c: Unit.objects.update_or_create(code=c, defaults=dict(name=n, dimension=d, factor=D(f)))[0]
             for c, n, d, f in UNITS}
    CompanyProfile(name="Demo Snacks Pvt Ltd", gstin="33AABCD1234E1Z5", state_code="33",
                   state_name="Tamil Nadu", address="Tiruchirappalli, Tamil Nadu", pincode="620001").save()
    items = {c: Item.objects.update_or_create(code=c, defaults=dict(name=n, kind=k, unit=units[u], standard_rate=D(r)))[0]
             for c, n, k, u, r in ITEMS}
    for code, level in REORDER.items():
        Item.objects.filter(code=code).update(reorder_level=D(level))
    sku, _ = Sku.objects.update_or_create(code="MUR200", defaults=dict(
        product_name="Murukku", pack_size="200 g", hsn_code="19059030", gst_rate=D("5"),
        selling_price=D("50"), packets_per_carton=20, shelf_life_days=90, pouch_item=items["POUCH200"]))
    bom, _ = Bom.objects.get_or_create(sku=sku, version=1, defaults=dict(batch_yield=200, is_active=True))
    for code, qty, unit in BOM_LINES:
        BomLine.objects.update_or_create(bom=bom, item=items[code], defaults=dict(qty_per_batch=D(qty), unit=units[unit]))
    sa, sb = User.objects.filter(username__in=["salesman_a", "salesman_b"]).order_by("username")
    Customer.objects.update_or_create(name="Retailer A", defaults=dict(
        kind="retailer", gstin="33AAACR1111A1Z5", state_code="33", state_name="Tamil Nadu",
        address="Trichy, Tamil Nadu", pincode="620002", latitude=D("10.805000"), longitude=D("78.685000"), phone="9000000001", salesman=sa))
    Customer.objects.update_or_create(name="Distributor B", defaults=dict(
        kind="distributor", gstin="29AAACD2222B1Z6", state_code="29", state_name="Karnataka",
        address="Bengaluru, Karnataka", pincode="560001", latitude=D("12.971600"), longitude=D("77.594600"), phone="9000000002", salesman=sb))


class Command(BaseCommand):
    help = "Seed benchmark master data. Run seed_users first."

    def handle(self, *args, **options):
        seed_masters()
        self.stdout.write(self.style.SUCCESS("Seeded masters"))
