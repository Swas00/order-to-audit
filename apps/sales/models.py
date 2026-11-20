from decimal import ROUND_HALF_UP, Decimal

from django.db import models
from django.db.models import Q
from django.utils import timezone

PAISA = Decimal("0.01")


class OrderStatus(models.TextChoices):
    SUBMITTED = "submitted", "Submitted"
    APPROVED = "approved", "Approved"
    REJECTED = "rejected", "Rejected"
    INVOICED = "invoiced", "Invoiced"
    DISPATCHED = "dispatched", "Dispatched"


# Orders whose stock has been promised but not yet dispatched.
COMMITTED = (OrderStatus.APPROVED, OrderStatus.INVOICED)


class Order(models.Model):
    customer = models.ForeignKey("masters.Customer", on_delete=models.PROTECT, related_name="orders")
    salesman = models.ForeignKey("accounts.User", on_delete=models.PROTECT, related_name="orders")
    status = models.CharField(max_length=12, choices=OrderStatus.choices, default=OrderStatus.SUBMITTED, db_index=True)
    # Set by the client when the form is opened. A repeat submit with the same value returns the
    # existing order, so double taps and offline re-sync never create duplicates.
    client_uuid = models.UUIDField(null=True, blank=True, unique=True)
    note = models.CharField(max_length=250, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    reviewed_by = models.ForeignKey("accounts.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    reviewed_at = models.DateTimeField(null=True, blank=True)
    reject_reason = models.CharField(max_length=250, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    @property
    def number(self):
        return f"ORD-{self.pk:04d}"

    def __str__(self):
        return self.number

    @property
    def taxable_total(self):
        return sum((l.amount for l in self.lines.all()), Decimal("0"))


class OrderLine(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="lines")
    sku = models.ForeignKey("masters.Sku", on_delete=models.PROTECT)
    ordered_qty = models.PositiveIntegerField(help_text="As submitted by the salesman; never changed")
    qty = models.PositiveIntegerField(help_text="Current quantity (admin may reduce or edit); 0 = dropped")
    original_price = models.DecimalField(max_digits=12, decimal_places=2, help_text="Price when the order was taken")
    price = models.DecimalField(max_digits=12, decimal_places=2, help_text="Current price per packet before tax")

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["order", "sku"], name="uniq_order_sku"),
            models.CheckConstraint(condition=Q(ordered_qty__gt=0), name="orderline_ordered_positive"),
            models.CheckConstraint(condition=Q(price__gte=0), name="orderline_price_nonneg"),
        ]
        ordering = ["sku__product_name", "sku__pack_size"]

    @property
    def amount(self):
        return (self.qty * self.price).quantize(PAISA, ROUND_HALF_UP)
