from django import forms

from apps.masters.models import Item, Sku


class AdjustmentForm(forms.Form):
    item = forms.ModelChoiceField(Item.objects.filter(active=True), required=False)
    sku = forms.ModelChoiceField(Sku.objects.filter(active=True), required=False, label="Finished good")
    qty = forms.DecimalField(max_digits=16, decimal_places=4, label="Quantity (+ add / - remove), in stock unit")
    reason = forms.CharField(max_length=200)

    def clean(self):
        data = super().clean()
        if bool(data.get("item")) == bool(data.get("sku")):
            raise forms.ValidationError("Choose either a material or a finished good.")
        return data
