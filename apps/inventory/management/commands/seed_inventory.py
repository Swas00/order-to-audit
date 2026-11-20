"""Opening stock and supplier from problem statement section 5. Run after seed_users and seed_masters.

Opening balances are dated one minute before the start of the current month so a monthly report
shows them in its Opening column. Idempotent: skips items that already have movements.
Finished-goods opening cost is not given by the brief; it is loaded at 0 (assumption, see README).
"""

from datetime import datetime, time, timedelta
from decimal import Decimal as D

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.accounts.models import User
from apps.inventory import services
from apps.inventory.models import Batch, StockMovement
from apps.masters.models import Item, Sku
from apps.purchasing.models import Supplier

OPENING = {"FLOUR": "100", "OIL": "30", "SPICE": "5", "POUCH200": "500", "CARTON20": "30"}
OPENING_FG = {"MUR200": "50"}
OPENING_FG_SHELF_DAYS = 60


def seed_inventory():
    first = timezone.localdate().replace(day=1)
    when = timezone.make_aware(datetime.combine(first, time.min)) - timedelta(minutes=1)
    admin = User.objects.filter(username="admin").first()
    for code, qty in OPENING.items():
        item = Item.objects.get(code=code)
        if not StockMovement.objects.filter(item=item).exists():
            services.adjust(item, D(qty), "Opening balance", admin, unit_cost=item.standard_rate, is_opening=True, moved_at=when)
    for code, qty in OPENING_FG.items():
        sku = Sku.objects.get(code=code)
        if not StockMovement.objects.filter(sku=sku).exists():
            # Opening stock is one batch. Its age is not given by the brief: assumed made on the opening date and
            # to have 60 days of life left when loaded, so it expires before any batch produced later (shows FEFO).
            batch = Batch.objects.create(sku=sku, number=f"OPEN-{sku.code}", mfg_date=when.date(),
                                         expiry_date=timezone.localdate() + timedelta(days=OPENING_FG_SHELF_DAYS), note="Opening stock")
            services.adjust(sku, D(qty), "Opening balance", admin, unit_cost=D("0"), is_opening=True, moved_at=when, batch=batch)
    Supplier.objects.get_or_create(name="Demo Flour Mills", defaults=dict(address="Salem, Tamil Nadu"))


class Command(BaseCommand):
    help = "Seed opening stock and a demo supplier"

    def handle(self, *args, **options):
        seed_inventory()
        self.stdout.write(self.style.SUCCESS("Seeded inventory"))
