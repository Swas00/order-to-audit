from django import forms
from django.utils.translation import gettext_lazy as _

from apps.masters.models import Item, Sku

from .models import Batch


class AdjustmentForm(forms.Form):
    item = forms.ModelChoiceField(Item.objects.filter(active=True), required=False, label=_("Material"))
    sku = forms.ModelChoiceField(Sku.objects.filter(active=True), required=False, label=_("Finished good"))
    batch = forms.ModelChoiceField(Batch.objects.all(), required=False, label=_("Batch"),
                                   help_text=_("Finished goods only. Required to add stock; if blank when removing, earliest-expiring stock is used."))
    qty = forms.DecimalField(max_digits=16, decimal_places=4, label=_("Quantity (+ add / - remove), in stock unit"))
    reason = forms.CharField(max_length=200, label=_("Reason"))

    def clean(self):
        data = super().clean()
        if bool(data.get("item")) == bool(data.get("sku")):
            raise forms.ValidationError(_("Choose either a material or a finished good."))
        if data.get("batch") and (not data.get("sku") or data["batch"].sku_id != data["sku"].pk):
            raise forms.ValidationError(_("That batch does not belong to the chosen finished good."))
        if data.get("sku") and data.get("qty") and data["qty"] > 0 and not data.get("batch"):
            raise forms.ValidationError(_("Choose the batch when adding finished goods."))
        return data
