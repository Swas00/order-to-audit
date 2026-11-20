from django import forms

from apps.masters.models import Customer

from .models import Invoice, InvoiceStatus, PaymentMode


class PaymentForm(forms.Form):
    customer = forms.ModelChoiceField(Customer.objects.filter(active=True))
    invoice = forms.ModelChoiceField(Invoice.objects.filter(status=InvoiceStatus.ISSUED), required=False,
                                     empty_label="Oldest invoices first (automatic)")
    amount = forms.DecimalField(min_value=0.01, max_digits=14, decimal_places=2)
    mode = forms.ChoiceField(choices=PaymentMode.choices)
    reference = forms.CharField(max_length=60, required=False, label="Reference (UTR / cheque no.)")
