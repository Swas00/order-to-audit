import secrets
from decimal import Decimal

from django.db import models
from django.db.models import Q, Sum
from django.utils import timezone

ZERO = Decimal("0.00")


def money_sum(value):
    """Database SUM as an exact 2-place Decimal (SQLite may hand back an unscaled number)."""
    return Decimal(value or 0).quantize(Decimal("0.01"))


class DocKind(models.TextChoices):
    INVOICE = "INV", "Tax invoice"
    CREDIT_NOTE = "CN", "Credit note"


class DocSequence(models.Model):
    """Gap-free numbering per document kind and financial year. Rows are locked while issuing."""

    kind = models.CharField(max_length=3, choices=DocKind.choices)
    fy = models.CharField(max_length=7)
    last = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["kind", "fy"], name="uniq_sequence")]


class InvoiceStatus(models.TextChoices):
    ISSUED = "issued", "Issued"
    CANCELLED = "cancelled", "Cancelled (credit note raised)"


def _token():
    return secrets.token_urlsafe(24)


class Invoice(models.Model):
    """A posted tax invoice. Immutable: it can only be cancelled, by a credit note, never edited or deleted.
    Supplier and buyer details are copied onto it so later master-data edits cannot alter history."""

    number = models.CharField(max_length=30, unique=True)
    fy = models.CharField(max_length=7)
    order = models.ForeignKey("sales.Order", on_delete=models.PROTECT, related_name="invoices")
    customer = models.ForeignKey("masters.Customer", on_delete=models.PROTECT, related_name="invoices")
    invoice_date = models.DateField(default=timezone.localdate)
    status = models.CharField(max_length=10, choices=InvoiceStatus.choices, default=InvoiceStatus.ISSUED)
    # snapshots
    supplier_name = models.CharField(max_length=120)
    supplier_gstin = models.CharField(max_length=15)
    supplier_address = models.CharField(max_length=250, blank=True)
    supplier_state_code = models.CharField(max_length=2)
    buyer_name = models.CharField(max_length=120)
    buyer_gstin = models.CharField(max_length=15, blank=True)
    buyer_address = models.CharField(max_length=250, blank=True)
    place_of_supply_code = models.CharField(max_length=2)
    place_of_supply_name = models.CharField(max_length=40)
    intra_state = models.BooleanField()
    # totals
    taxable_total = models.DecimalField(max_digits=14, decimal_places=2)
    cgst_total = models.DecimalField(max_digits=14, decimal_places=2, default=ZERO)
    sgst_total = models.DecimalField(max_digits=14, decimal_places=2, default=ZERO)
    igst_total = models.DecimalField(max_digits=14, decimal_places=2, default=ZERO)
    round_off = models.DecimalField(max_digits=6, decimal_places=2, default=ZERO)
    grand_total = models.DecimalField(max_digits=14, decimal_places=2)
    share_token = models.CharField(max_length=40, unique=True, default=_token)
    created_by = models.ForeignKey("accounts.User", null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-invoice_date", "-id"]
        constraints = [
            models.UniqueConstraint(fields=["order"], condition=Q(status="issued"), name="one_issued_invoice_per_order"),
        ]

    def __str__(self):
        return self.number

    @property
    def tax_total(self):
        return self.cgst_total + self.sgst_total + self.igst_total

    @property
    def paid(self):
        return money_sum(self.allocations.aggregate(t=Sum("amount"))["t"])

    @property
    def outstanding(self):
        return ZERO if self.status == InvoiceStatus.CANCELLED else self.grand_total - self.paid

    @property
    def is_dispatched(self):
        return hasattr(self, "dispatch")


class InvoiceLine(models.Model):
    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name="lines")
    sku = models.ForeignKey("masters.Sku", on_delete=models.PROTECT)
    description = models.CharField(max_length=120)
    hsn_code = models.CharField(max_length=8)
    qty = models.PositiveIntegerField()
    rate = models.DecimalField(max_digits=12, decimal_places=2)
    gst_rate = models.DecimalField(max_digits=5, decimal_places=2)
    taxable_value = models.DecimalField(max_digits=14, decimal_places=2)
    cgst = models.DecimalField(max_digits=14, decimal_places=2, default=ZERO)
    sgst = models.DecimalField(max_digits=14, decimal_places=2, default=ZERO)
    igst = models.DecimalField(max_digits=14, decimal_places=2, default=ZERO)

    @property
    def total(self):
        return self.taxable_value + self.cgst + self.sgst + self.igst


class Dispatch(models.Model):
    """Goods leave the factory. This is the source document of the finished-goods stock reduction."""

    invoice = models.OneToOneField(Invoice, on_delete=models.PROTECT, related_name="dispatch")
    dispatched_at = models.DateTimeField(default=timezone.now)
    vehicle_ref = models.CharField(max_length=40, blank=True)
    user = models.ForeignKey("accounts.User", null=True, on_delete=models.SET_NULL)

    def __str__(self):
        return f"Dispatch of {self.invoice.number}"


class CreditNote(models.Model):
    """Full cancellation of an invoice. Amounts are the invoice's, shown as a reduction."""

    number = models.CharField(max_length=30, unique=True)
    invoice = models.OneToOneField(Invoice, on_delete=models.PROTECT, related_name="credit_note")
    note_date = models.DateField(default=timezone.localdate)
    reason = models.CharField(max_length=250)
    goods_returned = models.BooleanField(default=False)
    taxable_total = models.DecimalField(max_digits=14, decimal_places=2)
    cgst_total = models.DecimalField(max_digits=14, decimal_places=2)
    sgst_total = models.DecimalField(max_digits=14, decimal_places=2)
    igst_total = models.DecimalField(max_digits=14, decimal_places=2)
    grand_total = models.DecimalField(max_digits=14, decimal_places=2)
    created_by = models.ForeignKey("accounts.User", null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.number


class PaymentMode(models.TextChoices):
    CASH = "cash", "Cash"
    UPI = "upi", "UPI"
    BANK = "bank", "Bank transfer"
    CHEQUE = "cheque", "Cheque"


class Payment(models.Model):
    customer = models.ForeignKey("masters.Customer", on_delete=models.PROTECT, related_name="payments")
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    paid_on = models.DateField(default=timezone.localdate)
    mode = models.CharField(max_length=10, choices=PaymentMode.choices, default=PaymentMode.BANK)
    reference = models.CharField(max_length=60, blank=True)
    created_by = models.ForeignKey("accounts.User", null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-paid_on", "-id"]
        constraints = [models.CheckConstraint(condition=Q(amount__gt=0), name="payment_positive")]

    @property
    def unallocated(self):
        return self.amount - money_sum(self.allocations.aggregate(t=Sum("amount"))["t"])

    def __str__(self):
        return f"Payment #{self.pk} {self.customer} {self.amount}"


class PaymentAllocation(models.Model):
    payment = models.ForeignKey(Payment, on_delete=models.CASCADE, related_name="allocations")
    invoice = models.ForeignKey(Invoice, on_delete=models.PROTECT, related_name="allocations")
    amount = models.DecimalField(max_digits=14, decimal_places=2)

    class Meta:
        constraints = [models.CheckConstraint(condition=Q(amount__gt=0), name="allocation_positive")]
