from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import path

from apps.accounts import views as account_views

urlpatterns = [
    path("admin/", admin.site.urls),
    path("login/", auth_views.LoginView.as_view(template_name="login.html"), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("", account_views.home, name="home"),
]
