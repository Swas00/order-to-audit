from django import forms
from django.utils.translation import gettext_lazy as _
from django.forms import inlineformset_factory

from .models import Purchase, PurchaseLine


class PurchaseForm(forms.ModelForm):
    class Meta:
        model = Purchase
        fields = ["supplier", "bill_no", "bill_date"]
        labels = {"supplier": _("Supplier"), "bill_no": _("Bill no"), "bill_date": _("Bill date")}
        widgets = {"bill_date": forms.DateInput(attrs={"type": "date"})}


class LineForm(forms.ModelForm):
    class Meta:
        model = PurchaseLine
        fields = ["item", "qty", "unit", "rate", "gst_rate"]
        labels = {"item": _("Item"), "qty": _("Quantity"), "unit": _("Unit"), "rate": _("Rate (before tax)"), "gst_rate": _("GST %")}
        help_texts = {"rate": ""}

    def clean(self):
        data = super().clean()
        item, unit = data.get("item"), data.get("unit")
        if item and unit and unit.dimension != item.unit.dimension:
            raise forms.ValidationError(_("%(unit)s cannot be converted to %(stock)s for %(item)s.") % {"unit": unit, "stock": item.unit, "item": item.name})
        return data


LineFormSet = inlineformset_factory(Purchase, PurchaseLine, form=LineForm, extra=3, can_delete=False)
