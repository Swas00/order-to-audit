import pytest
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse
from django.test import RequestFactory
from django.urls import reverse

from apps.accounts.management.commands.seed_users import DEMO_PASSWORD, seed_users
from apps.accounts.models import Role, User
from apps.accounts.permissions import roles_required

pytestmark = pytest.mark.django_db


def test_seed_creates_every_role_and_is_idempotent():
    seed_users()
    seed_users()
    assert User.objects.count() == 6
    assert {u.role for u in User.objects.all()} == set(Role.values)


def test_home_requires_login(client):
    resp = client.get(reverse("home"))
    assert resp.status_code == 302 and "/login/" in resp["Location"]


def test_login_and_home_for_each_role(client):
    seed_users()
    for user in User.objects.all():
        assert client.login(username=user.username, password=DEMO_PASSWORD)
        resp = client.get(reverse("home"))
        if user.role == Role.OWNER:  # the owner lands on the dashboard
            assert resp.status_code == 302 and resp["Location"] == reverse("dashboard")
            resp = client.get(resp["Location"])
        assert resp.status_code == 200
        assert user.get_role_display().encode() in resp.content
        client.logout()


def test_bad_password_rejected(client):
    seed_users()
    resp = client.post(reverse("login"), {"username": "admin", "password": "wrong"})
    assert resp.status_code == 200 and b"Wrong username" in resp.content


def test_roles_required_blocks_other_roles():
    seed_users()

    @roles_required(Role.ADMIN)
    def view(request):
        return HttpResponse("ok")

    rf = RequestFactory()
    for user in User.objects.all():
        request = rf.get("/x")
        request.user = user
        if user.role == Role.ADMIN:
            assert view(request).status_code == 200
        else:
            with pytest.raises(PermissionDenied):
                view(request)


# ---------- demo logins panel (public demo deployments only) ----------

def test_login_page_hides_demo_logins_by_default(client, settings):
    settings.DEMO_LOGINS = False
    page = client.get(reverse("login")).content.decode()
    assert "demo12345" not in page and "Demo logins" not in page


def test_login_page_shows_demo_logins_when_enabled(client, settings):
    settings.DEMO_LOGINS = True
    page = client.get(reverse("login")).content.decode()
    assert "Demo logins" in page and DEMO_PASSWORD in page
    for username in ("salesman_a", "salesman_b", "admin", "production", "accountant", "owner"):
        assert username in page


def test_password_change_is_refused_on_a_public_demo_but_works_normally(client, settings, db):
    seed_users()
    client.login(username="admin", password=DEMO_PASSWORD)
    settings.DEMO_LOGINS = True
    r = client.post(reverse("password_change"), {"old_password": DEMO_PASSWORD, "new_password1": "Another-Pass-9731", "new_password2": "Another-Pass-9731"})
    assert r.status_code == 302 and r["Location"] == "/"
    assert User.objects.get(username="admin").check_password(DEMO_PASSWORD)   # unchanged
    settings.DEMO_LOGINS = False
    r = client.post(reverse("password_change"), {"old_password": DEMO_PASSWORD, "new_password1": "Another-Pass-9731", "new_password2": "Another-Pass-9731"})
    assert r.status_code == 302 and User.objects.get(username="admin").check_password("Another-Pass-9731")
