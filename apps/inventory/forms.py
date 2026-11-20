from django import forms

from apps.masters.models import Item, Sku

from .models import Batch


class AdjustmentForm(forms.Form):
    item = forms.ModelChoiceField(Item.objects.filter(active=True), required=False)
    sku = forms.ModelChoiceField(Sku.objects.filter(active=True), required=False, label="Finished good")
    batch = forms.ModelChoiceField(Batch.objects.all(), required=False,
                                   help_text="Finished goods only. Required to add stock; if blank when removing, earliest-expiring stock is used.")
    qty = forms.DecimalField(max_digits=16, decimal_places=4, label="Quantity (+ add / - remove), in stock unit")
    reason = forms.CharField(max_length=200)

    def clean(self):
        data = super().clean()
        if bool(data.get("item")) == bool(data.get("sku")):
            raise forms.ValidationError("Choose either a material or a finished good.")
        if data.get("batch") and (not data.get("sku") or data["batch"].sku_id != data["sku"].pk):
            raise forms.ValidationError("That batch does not belong to the chosen finished good.")
        if data.get("sku") and data.get("qty") and data["qty"] > 0 and not data.get("batch"):
            raise forms.ValidationError("Choose the batch when adding finished goods.")
        return data
