from decimal import Decimal

from django.contrib import messages
from django.shortcuts import redirect, render

from apps.accounts.models import Role
from apps.accounts.permissions import roles_required
from apps.core.dates import date_range, to_pk
from apps.core.exports import export_response
from apps.masters.models import Item, ItemKind, Sku

from . import services
from .forms import AdjustmentForm

VIEW_ROLES = (Role.ADMIN, Role.PRODUCTION, Role.ACCOUNTANT, Role.OWNER)
WRITE_ROLES = (Role.ADMIN, Role.PRODUCTION)

GROUPS = {"raw": ("Raw materials", lambda: Item.objects.filter(kind=ItemKind.RAW)),
          "packaging": ("Packaging materials", lambda: Item.objects.filter(kind=ItemKind.PACKAGING)),
          "fg": ("Finished goods", lambda: Sku.objects.all())}


@roles_required(*VIEW_ROLES)
def stock_summary(request):
    d_from, d_to, dt_from, dt_to = date_range(request)
    sections = []
    for key, (title, qs) in GROUPS.items():
        sections.append((title, services.stock_summary(qs(), dt_from, dt_to)))
    if request.GET.get("export") in ("csv", "xlsx"):
        rows = [(title, str(r.stockable), r.opening, r.receipts, r.issues, r.adjustments, r.closing, r.value.quantize(Decimal("0.01")))
                for title, data in sections for r in data]
        return export_response(request.GET["export"], f"stock-summary-{d_from}-{d_to}",
                               ["Group", "Item", "Opening", "Receipts", "Issues", "Adjustments", "Closing", "Value (INR)"], rows)
    return render(request, "inventory/summary.html", {"sections": sections, "d_from": d_from, "d_to": d_to})


@roles_required(*VIEW_ROLES)
def stock_ledger(request):
    d_from, d_to, dt_from, dt_to = date_range(request)
    choice = request.GET.get("stock", "")
    stockable = None
    if choice.startswith("item:"):
        stockable = Item.objects.filter(pk=to_pk(choice[5:])).first()
    elif choice.startswith("sku:"):
        stockable = Sku.objects.filter(pk=to_pk(choice[4:])).first()
    brought_forward, entries = services.ledger(stockable, dt_from, dt_to) if stockable else (0, [])
    if stockable and request.GET.get("export") in ("csv", "xlsx"):
        rows = [(d_from, "Brought forward", "", "", "", brought_forward)] + [(m.moved_at.strftime("%Y-%m-%d %H:%M"), m.get_movement_type_display(), str(m.source), m.qty, m.unit_cost, bal)
                for m, bal in entries]
        return export_response(request.GET["export"], f"stock-ledger-{stockable}-{d_from}-{d_to}",
                               ["Date", "Type", "Source document", "Qty", "Unit cost", "Balance"], rows)
    options = [(f"item:{i.pk}", str(i)) for i in Item.objects.all()] + [(f"sku:{s.pk}", str(s)) for s in Sku.objects.all()]
    return render(request, "inventory/ledger.html", {"entries": entries, "stockable": stockable, "options": options,
                                                     "choice": choice, "brought_forward": brought_forward, "d_from": d_from, "d_to": d_to})


@roles_required(*WRITE_ROLES)
def adjust(request):
    form = AdjustmentForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        try:
            services.adjust(d["item"] or d["sku"], d["qty"], d["reason"], request.user)
        except services.StockError as e:
            form.add_error(None, str(e))
        else:
            messages.success(request, "Adjustment recorded")
            return redirect("stock_summary")
    return render(request, "inventory/adjust.html", {"form": form})
