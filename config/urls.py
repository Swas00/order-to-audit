from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import path

from apps.accounts import views as account_views
from apps.billing import views as bill
from apps.core import views as core_views
from apps.costing import views as cost
from apps.inventory import views as inv
from apps.production import views as prod
from apps.purchasing import views as pur
from apps.reports import views as rep
from apps.sales import api as sales_api
from apps.sales import views as sales

urlpatterns = [
    path("admin/", admin.site.urls),
    path("login/", auth_views.LoginView.as_view(template_name="login.html"), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("", account_views.home, name="home"),
    path("stock/", inv.stock_summary, name="stock_summary"),
    path("stock/ledger/", inv.stock_ledger, name="stock_ledger"),
    path("stock/adjust/", inv.adjust, name="stock_adjust"),
    path("sw.js", core_views.service_worker, name="service_worker"),
    path("manifest.webmanifest", core_views.manifest, name="manifest"),
    path("orders/api/submit/", sales_api.submit_order, name="order_api_submit"),
    path("orders/", sales.order_list, name="order_list"),
    path("orders/new/", sales.order_new, name="order_new"),
    path("orders/<int:pk>/", sales.order_detail, name="order_detail"),
    path("orders/<int:pk>/approve/", sales.order_approve, name="order_approve"),
    path("orders/<int:pk>/reject/", sales.order_reject, name="order_reject"),
    path("sales/customers/", sales.customer_sales, name="customer_sales"),
    path("invoices/", bill.invoice_list, name="invoice_list"),
    path("invoices/<int:pk>/", bill.invoice_detail, name="invoice_detail"),
    path("invoices/<int:pk>/pdf/", bill.invoice_pdf, name="invoice_pdf"),
    path("invoices/<int:pk>/dispatch/", bill.invoice_dispatch, name="invoice_dispatch"),
    path("invoices/<int:pk>/cancel/", bill.invoice_cancel, name="invoice_cancel"),
    path("orders/<int:order_pk>/invoice/", bill.invoice_raise, name="invoice_raise"),
    path("credit-notes/<int:pk>/pdf/", bill.credit_note_pdf, name="credit_note_pdf"),
    path("i/<str:token>.pdf", bill.shared_invoice_pdf, name="shared_invoice_pdf"),
    path("payments/", bill.payment_list, name="payment_list"),
    path("payments/new/", bill.payment_new, name="payment_new"),
    path("receivables/", bill.receivables, name="receivables"),
    path("costing/", cost.costing, name="costing"),
    path("costing/add/", cost.overhead_add, name="overhead_add"),
    path("costing/<int:pk>/void/", cost.overhead_void, name="overhead_void"),
    path("reports/", rep.hub, name="reports"),
    path("reports/<slug:slug>/", rep.report, name="report"),
    path("dashboard/", rep.dashboard, name="dashboard"),
    path("production/", prod.run_list, name="run_list"),
    path("production/start/", prod.run_start, name="run_start"),
    path("production/<int:pk>/", prod.run_detail, name="run_detail"),
    path("production/<int:pk>/issue/", prod.run_issue, name="run_issue"),
    path("production/<int:pk>/complete/", prod.run_complete, name="run_complete"),
    path("production/<int:pk>/void/", prod.run_void, name="run_void"),
    path("purchases/", pur.purchase_list, name="purchase_list"),
    path("purchases/new/", pur.purchase_new, name="purchase_new"),
]
