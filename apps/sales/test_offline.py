"""Server side of offline order capture: JSON submit API, service worker and manifest, and the JS unit tests."""

import json
import shutil
import subprocess
import uuid
from datetime import timedelta
from pathlib import Path

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.accounts.management.commands.seed_users import seed_users
from apps.accounts.models import User
from apps.audit.models import history
from apps.masters.management.commands.seed_masters import seed_masters
from apps.masters.models import Customer, Sku
from apps.sales.models import Order

pytestmark = pytest.mark.django_db
URL = lambda: reverse("order_api_submit")  # noqa: E731


@pytest.fixture
def seeded():
    seed_users()
    seed_masters()


def salesman(name="salesman_a", **kw):
    c = Client(**kw)
    c.force_login(User.objects.get(username=name))
    return c


def body(**over):
    sku = Sku.objects.get()
    d = {"client_uuid": str(uuid.uuid4()), "customer": Customer.objects.get(name="Retailer A").pk,
         "quantities": {str(sku.pk): 150}, "note": "", "captured_at": (timezone.now() - timedelta(hours=2)).isoformat()}
    d.update(over)
    return d


def post(client, data):
    return client.post(URL(), json.dumps(data), content_type="application/json")


def test_submit_creates_order_with_capture_time(seeded):
    data = body()
    r = post(salesman(), data)
    assert r.status_code == 200 and r.json()["ok"] and r.json()["order"]["number"] == "ORD-0001"
    o = Order.objects.get()
    assert o.status == "submitted" and o.lines.get().qty == 150 and o.salesman.username == "salesman_a"
    assert timedelta(hours=1, minutes=59) < timezone.now() - o.created_at < timedelta(hours=2, minutes=1)  # phone's time kept


def test_resubmitting_same_uuid_returns_same_order(seeded):
    c, data = salesman(), body()
    first, second = post(c, data).json(), post(c, data).json()
    assert first["order"]["id"] == second["order"]["id"] and Order.objects.count() == 1


def test_uuid_of_another_salesmans_order_is_refused(seeded):
    data = body()
    post(salesman("salesman_a"), data)
    r = post(salesman("salesman_b"), {**data, "customer": Customer.objects.get(name="Distributor B").pk})
    assert r.status_code == 409 and Order.objects.count() == 1


def test_implausible_capture_times_are_replaced_by_arrival_time(seeded):
    for bad in ["not a date", "2999-01-01T00:00:00Z", "2020-01-01T00:00:00Z", "2026-11-20T10:00:00", None, 12345]:
        before = timezone.now()
        r = post(salesman(), body(captured_at=bad))
        assert r.status_code == 200, bad
        assert Order.objects.get(pk=r.json()["order"]["id"]).created_at >= before, bad


@pytest.mark.parametrize("mutate,message", [
    (lambda d: d.update(customer=Customer.objects.get(name="Distributor B").pk), "not on your list"),
    (lambda d: d.update(customer=None), "not on your list"),
    (lambda d: d.update(customer="abc"), "not on your list"),
    (lambda d: d.update(quantities={}), "at least one product"),
    (lambda d: d.update(quantities=None), "at least one product"),
    (lambda d: d.update(quantities={"999999": 5}), "no longer available"),
    (lambda d: d.update(quantities={"x": 5}), "no longer available"),
    (lambda d: d.update(quantities={str(Sku.objects.get().pk): "abc"}), "whole numbers"),
    (lambda d: d.update(quantities={str(Sku.objects.get().pk): 1.5}), "whole numbers"),
    (lambda d: d.update(quantities={str(Sku.objects.get().pk): True}), "whole numbers"),
    (lambda d: d.update(quantities={str(Sku.objects.get().pk): 0}), "at least one product"),
    (lambda d: d.update(quantities={str(Sku.objects.get().pk): 10 ** 12}), "cannot exceed"),
    (lambda d: d.update(client_uuid="nope"), "Malformed"),
])
def test_validation_errors_are_clean_400s(seeded, mutate, message):
    data = body()
    mutate(data)
    r = post(salesman(), data)
    assert r.status_code == 400 and not r.json()["ok"] and message in r.json()["error"]
    assert Order.objects.count() == 0


def test_malformed_bodies(seeded):
    c = salesman()
    for raw in [b"", b"[]", b"null", b"{bad json", b'"text"']:
        assert c.post(URL(), raw, content_type="application/json").status_code == 400, raw
    assert Order.objects.count() == 0


def test_negative_quantity_is_ignored_not_ordered(seeded):
    r = post(salesman(), body(quantities={str(Sku.objects.get().pk): -5}))
    assert r.status_code == 400 and Order.objects.count() == 0


def test_inactive_product_rejected(seeded):
    Sku.objects.update(active=False)
    r = post(salesman(), body())
    assert r.status_code == 400 and Order.objects.count() == 0


def test_auth_and_roles_return_json_not_redirects(seeded):
    assert post(Client(), body()).status_code == 401
    for name in ["admin", "production", "accountant", "owner"]:
        r = post(salesman(name), body())
        assert r.status_code == 403 and r.json()["ok"] is False, name
    assert Order.objects.count() == 0


def test_get_not_allowed_and_csrf_enforced(seeded):
    assert salesman().get(URL()).status_code == 405
    r = post(salesman(enforce_csrf_checks=True), body())
    assert r.status_code == 403 and Order.objects.count() == 0  # the phone must send X-CSRFToken


def test_offline_submission_is_audited(seeded):
    r = post(salesman(), body())
    assert history(Order.objects.get(pk=r.json()["order"]["id"])).get().action == "submitted"


# ---------- service worker, manifest, page wiring ----------

def test_service_worker_is_served_from_root_with_scope_header(client):
    r = client.get("/sw.js")
    assert r.status_code == 200 and r["Content-Type"].startswith("application/javascript")
    assert r["Service-Worker-Allowed"] == "/" and r["Cache-Control"] == "no-cache"
    text = r.content.decode()
    assert "/orders/new/" in text and "networkFirst" in text and "/static/sales/offline.js" in text


def test_manifest(client):
    r = client.get("/manifest.webmanifest")
    data = json.loads(r.content)
    assert r["Content-Type"].startswith("application/manifest+json")
    assert data["start_url"] == "/orders/new/" and data["display"] == "standalone"
    assert {i["sizes"] for i in data["icons"]} == {"192x192", "512x512"}
    for i in data["icons"]:  # icons really exist and are PNGs
        path = Path(__file__).resolve().parents[1] / "core" / "static" / "core" / Path(i["src"]).name
        assert path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_order_page_has_offline_hooks(seeded):
    page = salesman().get(reverse("order_new")).content.decode()
    for needle in ('id="offline-config"', 'id="pendingOrders"', 'id="syncBanner"', "sales/offline.js", 'name="client_uuid"',
                   'rel="manifest"', '/orders/api/submit/'):
        assert needle in page, needle


# ---------- JavaScript unit tests (queue + sync logic) ----------

@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_javascript_queue_and_sync_logic():
    root = Path(__file__).resolve().parent / "jstests" / "offline.test.js"
    r = subprocess.run(["node", "--test", str(root)], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-1000:]
    assert "fail 0" in r.stdout
