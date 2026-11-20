from decimal import ROUND_HALF_UP, Decimal

from django.db import models, transaction
from django.db.models import Q
from django.utils import timezone

from apps.inventory import services as stock
from apps.inventory.models import MovementType

PAISA = Decimal("0.01")


class Supplier(models.Model):
    name = models.CharField(max_length=120)
    gstin = models.CharField(max_length=15, blank=True)
    phone = models.CharField(max_length=15, blank=True)
    address = models.CharField(max_length=250, blank=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Purchase(models.Model):
    """A supplier bill. Posting it receives every line into stock (one ledger movement per line)."""

    supplier = models.ForeignKey(Supplier, on_delete=models.PROTECT)
    bill_no = models.CharField(max_length=40)
    bill_date = models.DateField(default=timezone.localdate)
    created_by = models.ForeignKey("accounts.User", null=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-bill_date", "-id"]
        constraints = [models.UniqueConstraint(fields=["supplier", "bill_no"], name="uniq_supplier_bill")]

    def __str__(self):
        return f"Purchase {self.bill_no} ({self.supplier})"

    @property
    def taxable_total(self):
        return sum((l.taxable_value for l in self.lines.all()), Decimal("0"))

    @property
    def tax_total(self):
        return sum((l.tax_amount for l in self.lines.all()), Decimal("0"))


class PurchaseLine(models.Model):
    purchase = models.ForeignKey(Purchase, on_delete=models.CASCADE, related_name="lines")
    item = models.ForeignKey("masters.Item", on_delete=models.PROTECT)
    qty = models.DecimalField(max_digits=14, decimal_places=4)
    unit = models.ForeignKey("masters.Unit", on_delete=models.PROTECT)
    rate = models.DecimalField(max_digits=12, decimal_places=4, help_text="Per entered unit, before tax")
    gst_rate = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("0"))

    class Meta:
        constraints = [models.CheckConstraint(condition=Q(qty__gt=0), name="purchaseline_qty_positive")]

    @property
    def taxable_value(self):
        return (self.qty * self.rate).quantize(PAISA, ROUND_HALF_UP)

    @property
    def tax_amount(self):
        return (self.taxable_value * self.gst_rate / 100).quantize(PAISA, ROUND_HALF_UP)


@transaction.atomic
def post_purchase(purchase: Purchase, lines: list[dict], user=None) -> Purchase:
    """Create the bill with its lines and receive them into stock at the purchase rate.

    `lines`: dicts with item, qty, unit, rate, gst_rate (optional).
    """
    if not lines:
        raise stock.StockError("A purchase needs at least one line")
    purchase.created_by = user
    purchase.save()
    for l in lines:
        line = PurchaseLine.objects.create(purchase=purchase, item=l["item"], qty=l["qty"], unit=l["unit"],
                                           rate=l["rate"], gst_rate=l.get("gst_rate", Decimal("0")))
        stock.receive(line.item, line.qty, line.rate, purchase, MovementType.PURCHASE, unit=line.unit,
                      note=f"Bill {purchase.bill_no}")
    return purchase
