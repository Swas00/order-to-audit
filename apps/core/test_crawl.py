"""Site crawl: log in as every role on a fully populated database, follow every link and form-less GET page,
and fail on any server error or any link that leads to a 404 / 403 (a link a user can see should work for that user)."""

import re
from urllib.parse import urldefrag, urlparse

import pytest
from django.test import Client

from apps.accounts.management.commands.seed_users import seed_users
from apps.core.benchmark import run_full_benchmark
from apps.inventory.management.commands.seed_inventory import seed_inventory
from apps.masters.management.commands.seed_masters import seed_masters

pytestmark = pytest.mark.django_db
ROLES = ["salesman_a", "admin", "production", "accountant", "owner"]
SKIP = ("/logout", "/admin/logout", "/i18n/setlang")  # state-changing or leaves the app
HREF = re.compile(r'href="([^"#][^"]*)"')


def crawl(username):
    c = Client()
    assert c.login(username=username, password="demo12345")
    seen, queue, problems = set(), ["/"], []
    while queue:
        url = queue.pop(0)
        if url in seen:
            continue
        seen.add(url)
        r = c.get(url, follow=False)
        if r.status_code in (301, 302):
            loc = r["Location"]
            if urlparse(loc).netloc == "" and loc not in seen:
                queue.append(loc)
            continue
        if r.status_code != 200:
            problems.append((url, r.status_code))
            continue
        if "text/html" not in r["Content-Type"]:
            continue
        for href in HREF.findall(r.content.decode()):
            href = urldefrag(href.replace("&amp;", "&"))[0]
            p = urlparse(href)
            if p.scheme or p.netloc or not href.startswith("/") or href.startswith(SKIP) or href.startswith("/admin/"):
                continue
            if href.startswith("/static/") or "export=" in href and False:
                continue
            queue.append(href)
    return seen, problems


@pytest.mark.parametrize("username", ROLES)
def test_every_link_a_role_can_see_works_for_that_role(username):
    seed_users(); seed_masters(); seed_inventory()
    run_full_benchmark()
    seen, problems = crawl(username)
    assert len(seen) > 5
    assert problems == [], f"{username}: broken links {problems}"


def test_anonymous_public_endpoints():
    c = Client()
    assert c.get("/health/").content == b"ok"
    assert c.get("/login/").status_code == 200 and c.get("/sw.js").status_code == 200
    assert c.get("/manifest.webmanifest").status_code == 200
    for icon in ("/static/core/icon-192.png", "/static/core/icon-512.png", "/static/sales/offline.js"):
        assert c.get(icon).status_code == 200, icon
    assert c.get("/orders/").status_code == 302   # private pages bounce to login


@pytest.mark.parametrize("username", ["admin", "production"])
def test_django_admin_pages_open_for_every_registered_model(username):
    """Master data is edited in /admin/: list, add and change pages must open (no 500) for the roles that may use them."""
    from django.contrib import admin as dj_admin
    from django.urls import reverse
    seed_users(); seed_masters(); seed_inventory()
    run_full_benchmark()
    c = Client()
    c.login(username=username, password="demo12345")
    opened, bad = 0, []
    for model, model_admin in dj_admin.site._registry.items():
        meta = model._meta
        for name, args in ((f"admin:{meta.app_label}_{meta.model_name}_changelist", []), (f"admin:{meta.app_label}_{meta.model_name}_add", [])):
            r = c.get(reverse(name, args=args))
            if r.status_code >= 500:
                bad.append((name, r.status_code))
            elif r.status_code == 200:
                opened += 1
        obj = model.objects.first()
        if obj is not None:
            r = c.get(reverse(f"admin:{meta.app_label}_{meta.model_name}_change", args=[obj.pk]))
            if r.status_code >= 500:
                bad.append((f"{meta.label} change", r.status_code))
            elif r.status_code == 200:
                opened += 1
    assert bad == [] and opened > 0, bad
