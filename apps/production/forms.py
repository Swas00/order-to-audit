from django import forms
from django.utils.translation import gettext_lazy as _

from apps.masters.models import Item, ItemKind, Sku, Unit


class StartRunForm(forms.Form):
    sku = forms.ModelChoiceField(Sku.objects.filter(active=True), label=_("Product"))
    batches = forms.DecimalField(min_value=0.001, max_value=1000, max_digits=8, decimal_places=3, initial=1,
                                 label=_("Batches (1 = standard batch)"))


class IssueForm(forms.Form):
    item = forms.ModelChoiceField(Item.objects.filter(kind=ItemKind.RAW, active=True), label=_("Raw material"))
    qty = forms.DecimalField(min_value=0.0001, max_digits=14, decimal_places=4, label=_("Quantity"))
    unit = forms.ModelChoiceField(Unit.objects.exclude(dimension="pack"), label=_("Unit"))

    def clean(self):
        d = super().clean()
        if d.get("item") and d.get("unit") and d["unit"].dimension != d["item"].unit.dimension:
            raise forms.ValidationError(_("%(unit)s cannot be converted to %(stock)s for %(item)s.") % {"unit": d["unit"], "stock": d["item"].unit, "item": d["item"].name})
        return d


class CompleteForm(forms.Form):
    good_packets = forms.IntegerField(min_value=0, max_value=1_000_000, label=_("Good packets produced"))
    damaged_pouches = forms.IntegerField(min_value=0, max_value=1_000_000, initial=0, label=_("Damaged pouches"))
    remark = forms.CharField(max_length=250, required=False, label=_("Remark (cause of wastage, etc.)"))

    def __init__(self, run, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.packaging = []
        pouch = run.sku.pouch_item
        for line in run.lines.select_related("item", "item__unit"):
            if line.item.kind == ItemKind.PACKAGING and line.item != pouch:
                name = f"pack_{line.item_id}"
                self.fields[name] = forms.DecimalField(
                    min_value=0, max_digits=14, decimal_places=4, initial=line.planned_qty.normalize(),
                    label=_("%(item)s used (%(unit)s)") % {"item": line.item.name, "unit": line.item.unit.code})
                self.packaging.append((line.item, name))

    def packaging_used(self):
        return {item: self.cleaned_data[name] for item, name in self.packaging}
