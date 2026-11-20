from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.accounts.models import Role
from apps.accounts.permissions import roles_required
from apps.core.dates import date_range
from apps.core.exports import export_response

from . import services
from .forms import OverheadForm
from .models import OverheadEntry

READ = (Role.ADMIN, Role.PRODUCTION, Role.ACCOUNTANT, Role.OWNER)
WRITE = (Role.ADMIN, Role.PRODUCTION)


@roles_required(*READ)
def costing(request, form=None):
    d_from, d_to, _, _ = date_range(request)
    runs = services.runs_costs(d_from, d_to)
    skus = services.sku_costs(d_from, d_to)
    if request.GET.get("export") in ("csv", "xlsx"):
        rows = [(rc.run.number, str(rc.run.sku), rc.good, rc.material, rc.direct["labour"], rc.direct["power_gas"],
                 rc.direct["other"], rc.allocated, rc.total, rc.per_packet, rc.price, rc.margin, rc.margin_pct) for rc in runs]
        return export_response(request.GET["export"], f"cost-per-packet-{d_from}-{d_to}",
                               ["Run", "Product", "Good packets", "Materials", "Labour", "Gas/power", "Other", "Monthly share",
                                "Run cost", "Cost per packet", "Selling price", "Margin", "Margin %"], rows)
    entries = OverheadEntry.objects.filter(incurred_on__range=(d_from, d_to)).select_related("run")
    return render(request, "costing/costing.html", {
        "runs": runs, "skus": skus, "entries": entries, "d_from": d_from, "d_to": d_to,
        "form": form or OverheadForm(), "can_write": request.user.role in WRITE})


@roles_required(*WRITE)
@require_POST
def overhead_add(request):
    form = OverheadForm(request.POST)
    if form.is_valid():
        d = form.cleaned_data
        try:
            services.add_overhead(request.user, d["kind"], d["amount"], d["incurred_on"], d["run"], d["description"])
            messages.success(request, "Cost recorded")
            return redirect("costing")
        except services.CostingError as e:
            form.add_error(None, str(e))
    return costing(request, form=form)


@roles_required(*WRITE)
@require_POST
def overhead_void(request, pk):
    try:
        services.void_overhead(request.user, get_object_or_404(OverheadEntry, pk=pk), request.POST.get("reason", ""))
        messages.success(request, "Entry voided")
    except services.CostingError as e:
        messages.error(request, str(e))
    return redirect("costing")
