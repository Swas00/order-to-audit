"""Master data: units, stock items, sellable SKUs, BOM, customers, company profile.

Design notes
- Every stock item has one *stock unit* (e.g. flour in kg). All ledger quantities are stored in it.
  Quantities entered in another unit of the same dimension are converted on entry (see units.py).
- A Sku is a sellable finished good: product name + pack size (Murukku 200 g, Murukku 500 g).
  Finished-goods stock is counted in packets per Sku.
- A Bom belongs to a Sku. Adding a new pack size = add a Sku and copy/edit a Bom (admin action).
"""

import re
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q

GSTIN_RE = re.compile(r"^\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")


def validate_gstin(value: str):
    if not GSTIN_RE.match(value or ""):
        raise ValidationError("GSTIN must be 15 characters in the standard format.")


class Dimension(models.TextChoices):
    MASS = "mass", "Mass"
    VOLUME = "volume", "Volume"
    COUNT = "count", "Count"
    PACK = "pack", "Pack (carton of packets; ratio is per SKU)"


class Unit(models.Model):
    """`factor` converts one of this unit into the dimension's base (g, ml, or 1 piece)."""

    code = models.CharField(max_length=10, unique=True)
    name = models.CharField(max_length=40)
    dimension = models.CharField(max_length=10, choices=Dimension.choices)
    factor = models.DecimalField(max_digits=14, decimal_places=6)

    def __str__(self):
        return self.code


class ItemKind(models.TextChoices):
    RAW = "raw", "Raw material"
    PACKAGING = "packaging", "Packaging material"


class Item(models.Model):
    """A purchasable, consumable stock item (raw or packaging)."""

    code = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=80)
    kind = models.CharField(max_length=12, choices=ItemKind.choices)
    unit = models.ForeignKey(Unit, on_delete=models.PROTECT, help_text="Stock unit; ledger is kept in this unit")
    standard_rate = models.DecimalField(
        max_digits=12, decimal_places=4, default=Decimal("0"),
        help_text="Rate per stock unit used for opening stock until purchases set a weighted average",
    )
    reorder_level = models.DecimalField(max_digits=14, decimal_places=4, null=True, blank=True)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["kind", "name"]

    def __str__(self):
        return f"{self.name} ({self.unit})"

    def clean(self):
        if self.unit_id and self.unit.dimension == Dimension.PACK:
            raise ValidationError("Items cannot use a carton-of-packets unit.")


class Sku(models.Model):
    """Finished good: product + pack size. Stock is counted in packets."""

    code = models.CharField(max_length=20, unique=True)
    product_name = models.CharField(max_length=80)
    pack_size = models.CharField(max_length=20, help_text="e.g. 200 g")
    hsn_code = models.CharField(max_length=8)
    gst_rate = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("5.00"))
    selling_price = models.DecimalField(max_digits=12, decimal_places=2, help_text="Per packet, before tax")
    packets_per_carton = models.PositiveIntegerField(default=20)
    variance_tolerance_pct = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True,
        help_text="Overrides the global tolerance (3%) for this SKU",
    )
    shelf_life_days = models.PositiveIntegerField(default=90, help_text="Days from manufacture to expiry; sets each new batch's expiry")
    pouch_item = models.ForeignKey(
        "Item", null=True, blank=True, on_delete=models.PROTECT, related_name="+",
        help_text="Primary pack (pouch): one used per good packet, plus damaged ones recorded as wastage",
    )
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["product_name", "pack_size"]
        verbose_name = "SKU (finished good)"

    def __str__(self):
        return f"{self.product_name} {self.pack_size}"


class Bom(models.Model):
    """Standard recipe for one batch of a SKU."""

    sku = models.ForeignKey(Sku, on_delete=models.PROTECT, related_name="boms")
    version = models.PositiveIntegerField(default=1)
    batch_yield = models.PositiveIntegerField(help_text="Expected good packets per standard batch")
    is_active = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["sku", "version"], name="uniq_bom_sku_version"),
            models.UniqueConstraint(fields=["sku"], condition=Q(is_active=True), name="one_active_bom_per_sku"),
            models.CheckConstraint(condition=Q(batch_yield__gt=0), name="bom_yield_positive"),
        ]

    def __str__(self):
        return f"BOM {self.sku} v{self.version}"


class BomLine(models.Model):
    bom = models.ForeignKey(Bom, on_delete=models.CASCADE, related_name="lines")
    item = models.ForeignKey(Item, on_delete=models.PROTECT)
    qty_per_batch = models.DecimalField(max_digits=14, decimal_places=4)
    unit = models.ForeignKey(Unit, on_delete=models.PROTECT, help_text="Unit this quantity is written in")

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["bom", "item"], name="uniq_bom_item"),
            models.CheckConstraint(condition=Q(qty_per_batch__gt=0), name="bomline_qty_positive"),
        ]

    def __str__(self):
        return f"{self.item.name} {self.qty_per_batch} {self.unit}"

    def clean(self):
        if self.item_id and self.unit_id and self.unit.dimension != self.item.unit.dimension:
            raise ValidationError(f"{self.unit} cannot be converted to the stock unit {self.item.unit} of {self.item}.")


class CustomerKind(models.TextChoices):
    RETAILER = "retailer", "Retailer"
    DISTRIBUTOR = "distributor", "Distributor"


class Customer(models.Model):
    name = models.CharField(max_length=120)
    kind = models.CharField(max_length=12, choices=CustomerKind.choices)
    gstin = models.CharField(max_length=15, blank=True, validators=[validate_gstin],
                             help_text="Blank = unregistered buyer")
    state_code = models.CharField(max_length=2, help_text="GST state code = place of supply (TN 33, KA 29)")
    state_name = models.CharField(max_length=40)
    address = models.CharField(max_length=250, blank=True)
    phone = models.CharField(max_length=15, blank=True)
    pincode = models.CharField(max_length=6, blank=True, help_text="Needed for e-Invoice / e-Way Bill data")
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True, help_text="Shop location for visit check-in distance check")
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    salesman = models.ForeignKey("accounts.User", null=True, blank=True, on_delete=models.SET_NULL,
                                 related_name="customers", limit_choices_to={"role": "salesman"})
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    def clean(self):
        if self.gstin and self.gstin[:2] != self.state_code:
            raise ValidationError("GSTIN's first two digits must equal the state code.")


class CustomerPrice(models.Model):
    """Optional customer-specific price overriding Sku.selling_price."""

    customer = models.ForeignKey(Customer, on_delete=models.CASCADE, related_name="prices")
    sku = models.ForeignKey(Sku, on_delete=models.CASCADE)
    price = models.DecimalField(max_digits=12, decimal_places=2)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["customer", "sku"], name="uniq_customer_sku_price")]


class CompanyProfile(models.Model):
    """The factory itself (single row). State code decides CGST+SGST vs IGST."""

    name = models.CharField(max_length=120)
    gstin = models.CharField(max_length=15, validators=[validate_gstin])
    state_code = models.CharField(max_length=2)
    state_name = models.CharField(max_length=40)
    address = models.CharField(max_length=250, blank=True)
    pincode = models.CharField(max_length=6, blank=True)
    global_variance_tolerance_pct = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("3.00"))

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def get(cls):
        return cls.objects.get(pk=1)

    def __str__(self):
        return self.name
