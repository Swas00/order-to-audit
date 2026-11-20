from django.contrib import messages
from django.shortcuts import redirect, render
from django.utils.translation import gettext as _

from apps.accounts.models import Role
from apps.accounts.permissions import roles_required
from apps.core.dates import date_range
from apps.core.exports import export_response

from .forms import LineFormSet, PurchaseForm
from .models import Purchase, post_purchase


@roles_required(Role.ADMIN, Role.PRODUCTION, Role.ACCOUNTANT, Role.OWNER)
def purchase_list(request):
    d_from, d_to, _, _ = date_range(request)
    purchases = Purchase.objects.filter(bill_date__range=(d_from, d_to)).select_related("supplier").prefetch_related("lines__item")
    if request.GET.get("export") in ("csv", "xlsx"):  # purchase register
        rows = [(p.bill_date, p.bill_no, p.supplier.name, l.item.name, l.qty, l.unit.code, l.rate, l.taxable_value, l.gst_rate, l.tax_amount)
                for p in purchases for l in p.lines.all()]
        return export_response(request.GET["export"], f"purchase-register-{d_from}-{d_to}",
                               ["Date", "Bill no", "Supplier", "Item", "Qty", "Unit", "Rate", "Taxable", "GST %", "GST"], rows)
    return render(request, "purchasing/list.html", {"purchases": purchases, "d_from": d_from, "d_to": d_to})


@roles_required(Role.ADMIN, Role.PRODUCTION)
def purchase_new(request):
    form = PurchaseForm(request.POST or None)
    formset = LineFormSet(request.POST or None, instance=Purchase())
    if request.method == "POST" and form.is_valid() and formset.is_valid():
        lines = [f.cleaned_data for f in formset.forms if f.cleaned_data and f.cleaned_data.get("item")]
        if not lines:
            form.add_error(None, _("Add at least one line."))
        else:
            post_purchase(form.save(commit=False), lines, request.user)
            messages.success(request, _("Purchase recorded and stock updated"))
            return redirect("purchase_list")
    return render(request, "purchasing/new.html", {"form": form, "formset": formset})
