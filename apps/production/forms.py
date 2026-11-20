from django import forms

from apps.masters.models import Item, ItemKind, Sku, Unit


class StartRunForm(forms.Form):
    sku = forms.ModelChoiceField(Sku.objects.filter(active=True), label="Product")
    batches = forms.DecimalField(min_value=0.001, max_value=1000, max_digits=8, decimal_places=3, initial=1,
                                 label="Batches (1 = standard batch)")


class IssueForm(forms.Form):
    item = forms.ModelChoiceField(Item.objects.filter(kind=ItemKind.RAW, active=True), label="Raw material")
    qty = forms.DecimalField(min_value=0.0001, max_digits=14, decimal_places=4)
    unit = forms.ModelChoiceField(Unit.objects.exclude(dimension="pack"))

    def clean(self):
        d = super().clean()
        if d.get("item") and d.get("unit") and d["unit"].dimension != d["item"].unit.dimension:
            raise forms.ValidationError(f"{d['unit']} cannot be converted to {d['item'].unit} for {d['item'].name}.")
        return d


class CompleteForm(forms.Form):
    good_packets = forms.IntegerField(min_value=0, max_value=1_000_000, label="Good packets produced")
    damaged_pouches = forms.IntegerField(min_value=0, max_value=1_000_000, initial=0, label="Damaged pouches")
    remark = forms.CharField(max_length=250, required=False, label="Remark (cause of wastage, etc.)")

    def __init__(self, run, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.packaging = []
        pouch = run.sku.pouch_item
        for line in run.lines.select_related("item", "item__unit"):
            if line.item.kind == ItemKind.PACKAGING and line.item != pouch:
                name = f"pack_{line.item_id}"
                self.fields[name] = forms.DecimalField(
                    min_value=0, max_digits=14, decimal_places=4, initial=line.planned_qty.normalize(),
                    label=f"{line.item.name} used ({line.item.unit.code})")
                self.packaging.append((line.item, name))

    def packaging_used(self):
        return {item: self.cleaned_data[name] for item, name in self.packaging}
