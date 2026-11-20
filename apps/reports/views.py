from datetime import timedelta
from decimal import Decimal

from django.http import Http404
from django.shortcuts import render
from django.utils import timezone

from apps.accounts.models import Role
from apps.accounts.permissions import roles_required
from apps.billing import services as billing
from apps.core.dates import date_range
from apps.core.exports import export_tables
from apps.costing import services as costing
from apps.inventory import services as stock
from apps.masters.models import Customer, Item, Sku
from apps.production import services as prod
from apps.production import variance as var
from apps.production.models import ProductionRun, RunStatus
from apps.sales.models import Order, OrderStatus

from . import builders

FIN = (Role.ADMIN, Role.ACCOUNTANT, Role.OWNER)
FLOOR = (Role.ADMIN, Role.PRODUCTION, Role.ACCOUNTANT, Role.OWNER)

# slug -> (title, builder, roles, description)
REPORTS = {
    "sales-register": ("Sales register", builders.sales_register, FIN, "Every invoice and credit note with GST split"),
    "customer-sales": ("Customer-wise sales", builders.customer_sales, FIN, "Net sales per customer"),
    "product-sales": ("Product-wise sales", builders.product_sales, FIN, "Packets and value per product"),
    "gst-summary": ("GST summary (GSTR-1 / GSTR-3B)", builders.gst_summary, FIN, "Outward supplies in GSTR-1 table shapes"),
    "visit-log": ("Salesman visit log", builders.visit_log, (Role.ADMIN, Role.OWNER), "GPS check-ins with distance from the shop"),
    "purchase-register": ("Purchase register", builders.purchase_register, FLOOR, "Supplier bills line by line"),
    "batches": ("Finished goods by batch and expiry", builders.batch_report, FLOOR, "Batch numbers, expiry dates and stock on hand"),
    "low-stock": ("Low-stock alerts", builders.low_stock_report, FLOOR, "Raw and packaging items at or below reorder level"),
    "stock-summary": ("Stock summary", builders.stock_summary, FLOOR, "Opening, receipts, issues, adjustments, closing"),
    "stock-ledger": ("Stock ledger (all items)", builders.stock_ledger_all, FLOOR, "Every movement with running balance"),
    "production": ("Production report", builders.production_report, FLOOR, "Planned vs actual consumption, yield, wastage"),
    "collections": ("Payments and collections", builders.collections, FIN, "Money received by customer and mode"),
    "outstanding": ("Outstanding and ageing", builders.outstanding, FIN, "Receivables by customer with ageing"),
}


def _plain(x):
    """200.00 -> '200', 0.50 -> '0.5' (no exponent form)."""
    return format(Decimal(x).normalize(), "f")


@roles_required(*FLOOR)
def hub(request):
    mine = [(slug, r[0], r[3]) for slug, r in REPORTS.items() if request.user.role in r[2]]
    return render(request, "reports/hub.html", {"reports": mine})


@roles_required(*FLOOR)
def report(request, slug):
    if slug not in REPORTS:
        raise Http404
    title, builder, roles, _ = REPORTS[slug]
    if request.user.role not in roles:
        from django.core.exceptions import PermissionDenied
        raise PermissionDenied
    d_from, d_to, _, _ = date_range(request)
    tables = builder(d_from, d_to)
    fmt = request.GET.get("export")
    if fmt in ("csv", "xlsx", "pdf"):
        return export_tables(fmt, f"{slug}-{d_from}-{d_to}", tables, f"{title}, {d_from} to {d_to}")
    return render(request, "reports/report.html", {"title": title, "tables": tables, "d_from": d_from, "d_to": d_to, "slug": slug})


@roles_required(Role.OWNER, Role.ADMIN)
def dashboard(request):
    today = timezone.localdate()
    month_start = today.replace(day=1)
    todays = Order.objects.filter(created_at__date=today).prefetch_related("lines")
    by_status = {s: 0 for s in OrderStatus.values}
    packets, value = 0, Decimal("0")
    for o in todays:
        by_status[o.status] += 1
        packets += sum(l.qty for l in o.lines.all())
        value += o.taxable_total
    docs = builders.sales_docs(month_start, today)
    stock_rows = []
    low_items = {l.item.pk for l in stock.low_stock()}
    low = [l.item for l in stock.low_stock()]
    for item in Item.objects.filter(active=True):
        stock_rows.append((item.get_kind_display(), str(item), stock.balance(item), item.pk in low_items))
    for sku in Sku.objects.filter(active=True):
        stock_rows.append(("Finished goods", str(sku), stock.balance(sku), False))
    alerts = []
    since = timezone.now() - timedelta(days=30)
    for run in ProductionRun.objects.filter(status=RunStatus.COMPLETED, completed_at__gte=since).select_related("sku"):
        rep = prod.run_report(run)
        flagged = [f"{l.item.name} {l.variance.pct:+}%" for l in rep.lines if l.variance.status == var.FLAG and l.variance.pct is not None]
        if rep.yield_variance and rep.yield_variance.status == var.FLAG:
            flagged.insert(0, f"Yield {rep.yield_variance.pct:+}% ({_plain(rep.yield_variance.actual)} vs {_plain(rep.yield_variance.planned)})")
        if flagged:
            alerts.append((run, flagged))
    balances = sorted(((c, billing.customer_balance(c)) for c in Customer.objects.all()), key=lambda x: -x[1])
    owing = [b for b in balances if b[1] > 0]
    return render(request, "reports/dashboard.html", {
        "today": today, "orders_today": todays.count(), "by_status": [(OrderStatus(s).label, n) for s, n in by_status.items()],
        "packets": packets, "value": value, "pending": Order.objects.filter(status=OrderStatus.SUBMITTED).count(),
        "month_taxable": builders._sum(docs, "taxable"), "month_total": builders._sum(docs, "total"),
        "stock_rows": stock_rows, "low": low, "expiry": stock.expiry_alerts(), "alerts": alerts, "costs": costing.sku_costs(month_start, today),
        "total_due": sum((b[1] for b in owing), Decimal("0")), "top_due": owing[:5]})
