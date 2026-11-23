"""Login throttling and password change."""

import pytest
from django.core.cache import cache
from django.test import Client
from django.urls import reverse

from apps.accounts.management.commands.seed_users import seed_users

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def fresh():
    cache.clear()
    seed_users()


def attempt(c, user="admin", pw="wrong"):
    return c.post(reverse("login"), {"username": user, "password": pw})


def test_five_wrong_passwords_lock_the_pair_even_for_the_right_password():
    c = Client()
    for _ in range(5):
        assert attempt(c).status_code == 200
    r = attempt(c, pw="demo12345")
    assert r.status_code == 429 and "Too many failed attempts" in r.content.decode()
    assert "_auth_user_id" not in c.session


def test_lock_is_per_username_and_a_success_resets_the_counter():
    c = Client()
    for _ in range(4):
        attempt(c)
    assert attempt(Client(), user="owner", pw="demo12345").status_code == 302   # someone else is unaffected
    assert attempt(c, pw="demo12345").status_code == 302                          # 4 failures then success: allowed, counter reset
    c.logout()
    for _ in range(4):
        attempt(c)
    assert attempt(c, pw="demo12345").status_code == 302


def test_password_change_works_and_old_password_stops_working():
    c = Client()
    c.login(username="salesman_a", password="demo12345")
    assert c.get(reverse("password_change")).status_code == 200
    r = c.post(reverse("password_change"), {"old_password": "demo12345", "new_password1": "Fresh-Pass-4821", "new_password2": "Fresh-Pass-4821"})
    assert r.status_code == 302
    assert Client().login(username="salesman_a", password="demo12345") is False
    assert Client().login(username="salesman_a", password="Fresh-Pass-4821") is True


def test_password_change_rejects_wrong_old_and_weak_new_passwords():
    c = Client()
    c.login(username="salesman_a", password="demo12345")
    r = c.post(reverse("password_change"), {"old_password": "nope", "new_password1": "Fresh-Pass-4821", "new_password2": "Fresh-Pass-4821"})
    assert r.status_code == 200
    r = c.post(reverse("password_change"), {"old_password": "demo12345", "new_password1": "12345678", "new_password2": "12345678"})
    assert r.status_code == 200
    assert Client().login(username="salesman_a", password="demo12345") is True
    assert Client().get(reverse("password_change")).status_code == 302   # needs login
