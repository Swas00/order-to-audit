from decimal import Decimal

from django.db import models
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


class RunStatus(models.TextChoices):
    IN_PROGRESS = "in_progress", _("In progress")
    COMPLETED = "completed", _("Completed")
    VOIDED = "voided", _("Voided")


class ProductionRun(models.Model):
    """One production run of a SKU against its BOM, in two stages.

    Stage 1 (start + issue): raw materials are issued from stock as the floor takes them.
    Stage 2 (complete): good output, damaged pouches and packaging used are recorded; packaging is
    consumed and finished goods are added. Planned figures are snapshotted when the run starts so
    later BOM edits never rewrite history.
    """

    sku = models.ForeignKey("masters.Sku", on_delete=models.PROTECT, related_name="runs")
    bom = models.ForeignKey("masters.Bom", on_delete=models.PROTECT)
    batches = models.DecimalField(max_digits=8, decimal_places=3, default=Decimal("1"))
    expected_output = models.DecimalField(max_digits=14, decimal_places=2, help_text="Packets, from the BOM")
    status = models.CharField(max_length=12, choices=RunStatus.choices, default=RunStatus.IN_PROGRESS)
    good_packets = models.PositiveIntegerField(null=True, blank=True)
    damaged_pouches = models.PositiveIntegerField(default=0)
    remark = models.CharField(max_length=250, blank=True)
    void_reason = models.CharField(max_length=250, blank=True)
    started_at = models.DateTimeField(default=timezone.now)
    completed_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey("accounts.User", null=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["-started_at", "-id"]
        constraints = [models.CheckConstraint(condition=Q(batches__gt=0), name="run_batches_positive")]

    @property
    def number(self):
        return f"RUN-{self.pk:04d}"

    def __str__(self):
        return f"{self.number} {self.sku}"


class RunLine(models.Model):
    """Planned (snapshot) and actual consumption of one item in a run, in the item's stock unit."""

    run = models.ForeignKey(ProductionRun, on_delete=models.CASCADE, related_name="lines")
    item = models.ForeignKey("masters.Item", on_delete=models.PROTECT)
    planned_qty = models.DecimalField(max_digits=16, decimal_places=4)
    actual_qty = models.DecimalField(max_digits=16, decimal_places=4, default=Decimal("0"))

    class Meta:
        constraints = [models.UniqueConstraint(fields=["run", "item"], name="uniq_run_item")]
        ordering = ["item__kind", "item__name"]
