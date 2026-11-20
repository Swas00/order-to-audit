from django import forms
from django.utils import timezone

from apps.production.models import ProductionRun, RunStatus

from .models import OverheadKind


class OverheadForm(forms.Form):
    kind = forms.ChoiceField(choices=OverheadKind.choices)
    amount = forms.DecimalField(min_value=0.01, max_digits=14, decimal_places=2, label="Amount (₹)")
    run = forms.ModelChoiceField(ProductionRun.objects.exclude(status=RunStatus.VOIDED), required=False,
                                 empty_label="Monthly pool (shared by output)", label="Charge to run")
    incurred_on = forms.DateField(initial=timezone.localdate, widget=forms.DateInput(attrs={"type": "date"}), label="Date")
    description = forms.CharField(max_length=200, required=False)
