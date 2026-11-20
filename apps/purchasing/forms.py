from django import forms
from django.forms import inlineformset_factory

from .models import Purchase, PurchaseLine


class PurchaseForm(forms.ModelForm):
    class Meta:
        model = Purchase
        fields = ["supplier", "bill_no", "bill_date"]
        widgets = {"bill_date": forms.DateInput(attrs={"type": "date"})}


class LineForm(forms.ModelForm):
    class Meta:
        model = PurchaseLine
        fields = ["item", "qty", "unit", "rate", "gst_rate"]

    def clean(self):
        data = super().clean()
        item, unit = data.get("item"), data.get("unit")
        if item and unit and unit.dimension != item.unit.dimension:
            raise forms.ValidationError(f"{unit} cannot be converted to {item.unit} for {item.name}.")
        return data


LineFormSet = inlineformset_factory(Purchase, PurchaseLine, form=LineForm, extra=3, can_delete=False)
