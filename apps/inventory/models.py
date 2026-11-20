"""Append-only stock ledger.

Stock balance is never stored or overwritten: it is the sum of StockMovement.qty. A movement is
immutable once written; mistakes are fixed by a compensating movement. Every movement points to
its source document (purchase, production run, invoice/dispatch, adjustment) via a generic FK.
"""

from decimal import Decimal

from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.db.models import Q
from django.utils import timezone


class ImmutableError(Exception):
    pass


class MovementQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ImmutableError("Stock movements are append-only")

    def delete(self):
        raise ImmutableError("Stock movements are append-only")


class MovementType(models.TextChoices):
    OPENING = "opening", "Opening balance"
    PURCHASE = "purchase", "Purchase receipt"
    PRODUCTION_ISSUE = "production_issue", "Issue to production"
    PRODUCTION_PACKAGING = "production_packaging", "Packaging used"
    PRODUCTION_WASTAGE = "production_wastage", "Damaged / wasted"
    PRODUCTION_OUTPUT = "production_output", "Finished goods produced"
    DISPATCH = "dispatch", "Dispatch (sale)"
    RETURN = "return", "Sales return"
    ADJUSTMENT = "adjustment", "Adjustment"


# Used by the stock summary: opening + receipts - issues +/- adjustments = closing.
ADJUSTMENT_TYPES = {MovementType.ADJUSTMENT, MovementType.OPENING}


class StockMovement(models.Model):
    item = models.ForeignKey("masters.Item", null=True, blank=True, on_delete=models.PROTECT, related_name="movements")
    sku = models.ForeignKey("masters.Sku", null=True, blank=True, on_delete=models.PROTECT, related_name="movements")
    movement_type = models.CharField(max_length=24, choices=MovementType.choices)
    qty = models.DecimalField(max_digits=16, decimal_places=4, help_text="Signed, in the item's stock unit (SKU: packets)")
    unit_cost = models.DecimalField(max_digits=14, decimal_places=4, default=Decimal("0"))
    moved_at = models.DateTimeField(default=timezone.now, db_index=True)
    source_type = models.ForeignKey(ContentType, on_delete=models.PROTECT)
    source_id = models.PositiveBigIntegerField()
    source = GenericForeignKey("source_type", "source_id")
    note = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = MovementQuerySet.as_manager()

    class Meta:
        ordering = ["moved_at", "id"]
        constraints = [
            models.CheckConstraint(
                condition=(Q(item__isnull=False) & Q(sku__isnull=True)) | (Q(item__isnull=True) & Q(sku__isnull=False)),
                name="movement_item_xor_sku",
            ),
            models.CheckConstraint(condition=~Q(qty=0), name="movement_qty_nonzero"),
        ]
        indexes = [models.Index(fields=["item", "moved_at"]), models.Index(fields=["sku", "moved_at"])]

    @property
    def stockable(self):
        return self.item or self.sku

    @property
    def value(self):
        return self.qty * self.unit_cost

    def save(self, *args, **kwargs):
        if self.pk:
            raise ImmutableError("Stock movements are append-only")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ImmutableError("Stock movements are append-only")


class StockAdjustment(models.Model):
    """Source document for opening balances and manual corrections (count differences, etc.)."""

    item = models.ForeignKey("masters.Item", null=True, blank=True, on_delete=models.PROTECT)
    sku = models.ForeignKey("masters.Sku", null=True, blank=True, on_delete=models.PROTECT)
    qty = models.DecimalField(max_digits=16, decimal_places=4, help_text="Signed, in stock unit")
    unit_cost = models.DecimalField(max_digits=14, decimal_places=4, default=Decimal("0"))
    is_opening = models.BooleanField(default=False)
    reason = models.CharField(max_length=200)
    user = models.ForeignKey("accounts.User", null=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Adjustment #{self.pk}: {self.reason}"
