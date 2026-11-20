from django.db import models
from django.db.models import Q
from django.utils import timezone


class OverheadKind(models.TextChoices):
    LABOUR = "labour", "Labour"
    POWER_GAS = "power_gas", "Gas / power"
    OTHER = "other", "Other overhead"


class OverheadEntry(models.Model):
    """Labour, gas/power or other cost.

    With `run` set it belongs to that run in full. With `run` empty it is a monthly pool for the
    calendar month of `incurred_on`, shared by the runs completed that month in proportion to good
    packets produced. Entries are never deleted; a mistaken one is voided with a reason.
    """

    kind = models.CharField(max_length=12, choices=OverheadKind.choices)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    incurred_on = models.DateField(default=timezone.localdate)
    run = models.ForeignKey("production.ProductionRun", null=True, blank=True, on_delete=models.PROTECT,
                            related_name="overheads")
    description = models.CharField(max_length=200, blank=True)
    voided = models.BooleanField(default=False)
    void_reason = models.CharField(max_length=200, blank=True)
    created_by = models.ForeignKey("accounts.User", null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-incurred_on", "-id"]
        constraints = [models.CheckConstraint(condition=Q(amount__gt=0), name="overhead_positive")]
