from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import path

from apps.accounts import views as account_views
from apps.inventory import views as inv
from apps.purchasing import views as pur

urlpatterns = [
    path("admin/", admin.site.urls),
    path("login/", auth_views.LoginView.as_view(template_name="login.html"), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("", account_views.home, name="home"),
    path("stock/", inv.stock_summary, name="stock_summary"),
    path("stock/ledger/", inv.stock_ledger, name="stock_ledger"),
    path("stock/adjust/", inv.adjust, name="stock_adjust"),
    path("purchases/", pur.purchase_list, name="purchase_list"),
    path("purchases/new/", pur.purchase_new, name="purchase_new"),
]
