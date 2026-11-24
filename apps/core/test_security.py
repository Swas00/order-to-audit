"""Access control, CSRF, escaping and input-hardening tests across the whole URL surface."""

import uuid

import pytest
from django.test import Client
from django.urls import get_resolver, reverse

from apps.accounts.management.commands.seed_users import seed_users
from apps.accounts.models import Role, User
from apps.core.benchmark import run_full_benchmark
from apps.inventory.management.commands.seed_inventory import seed_inventory
from apps.masters.management.commands.seed_masters import seed_masters
from apps.masters.models import Customer, Sku
from apps.sales import services as sales

pytestmark = pytest.mark.django_db
PUBLIC = {"login", "logout", "shared_invoice_pdf", "service_worker", "manifest", "health", "order_api_submit"}  # api: JSON 401, tested separately


@pytest.fixture
def world():
    seed_users()
    seed_masters()
    seed_inventory()
    return run_full_benchmark()


def all_named_routes():
    """(name, reversed url) for every named non-admin route, using pk=1 style dummy arguments."""
    out = []
    for pattern in get_resolver().url_patterns:
        name = getattr(pattern, "name", None)
        if not name or name in PUBLIC or str(pattern.pattern).startswith("admin"):
            continue
        kwargs = {k: (1 if k in ("pk", "order_pk") else "x") for k in pattern.pattern.regex.groupindex}
        out.append((name, reverse(name, kwargs=kwargs)))
    return out


def test_every_route_requires_login(world):
    anon = Client()
    routes = all_named_routes()
    assert len(routes) > 30  # the loop really covers the app
    for name, url in routes:
        for method in (anon.get, anon.post):
            r = method(url)
            assert r.status_code == 302 and "/login/" in r["Location"], (name, url, method.__name__, r.status_code)


def test_order_api_rejects_anonymous_with_401(world):
    r = Client().post(reverse("order_api_submit"), "{}", content_type="application/json")
    assert r.status_code == 401 and r.json()["ok"] is False


# role -> routes (GET) it must NOT be able to open: expect 403 (or 404 for object lookups of others' data)
FORBIDDEN_GET = {
    Role.SALESMAN: ["stock_summary", "stock_ledger", "purchase_list", "run_list", "invoice_list", "payment_list", "receivables",
                    "costing", "reports", "dashboard", "stock_adjust", "purchase_new", "payment_new"],
    Role.PRODUCTION: ["invoice_list", "payment_list", "receivables", "dashboard", "order_new", "payment_new"],
    Role.ACCOUNTANT: ["order_new", "stock_adjust", "purchase_new", "payment_new", "dashboard"],
    Role.OWNER: ["order_new", "stock_adjust", "purchase_new", "payment_new"],
    Role.ADMIN: ["order_new"],
}
FORBIDDEN_POST = {  # write endpoints each role must never be able to use
    Role.SALESMAN: ["run_start", "overhead_add"],
    Role.PRODUCTION: ["invoice_dispatch", "invoice_cancel", "invoice_raise", "order_approve", "order_reject", "run_void"],
    Role.ACCOUNTANT: ["run_start", "run_issue", "run_complete", "run_void", "overhead_add", "order_approve", "invoice_dispatch"],
    Role.OWNER: ["run_start", "run_issue", "run_complete", "run_void", "overhead_add", "order_approve", "invoice_dispatch"],
}


def test_forbidden_pages_per_role(world):
    for role, names in FORBIDDEN_GET.items():
        user = User.objects.filter(role=role).first()
        c = Client()
        c.force_login(user)
        for n in names:
            assert c.get(reverse(n)).status_code == 403, (role, n)


def test_forbidden_actions_per_role(world):
    args = {"invoice_dispatch": [1], "invoice_cancel": [1], "invoice_raise": [1], "order_approve": [1], "order_reject": [1],
            "run_void": [1], "run_issue": [1], "run_complete": [1]}
    inv_before = world["inv_a"].status
    for role, names in FORBIDDEN_POST.items():
        c = Client()
        c.force_login(User.objects.filter(role=role).first())
        for n in names:
            assert c.post(reverse(n, args=args.get(n, [])), {"reason": "x"}).status_code == 403, (role, n)
    world["inv_a"].refresh_from_db()
    assert world["inv_a"].status == inv_before


def test_post_only_endpoints_reject_get(world):
    c = Client()
    c.force_login(User.objects.get(username="admin"))
    for n in ["order_approve", "order_reject", "invoice_dispatch", "invoice_cancel", "invoice_raise", "run_void"]:
        assert c.get(reverse(n, args=[1])).status_code == 405, n


def test_csrf_is_enforced_on_writes(world):
    c = Client(enforce_csrf_checks=True)
    c.force_login(User.objects.get(username="admin"))
    assert c.post(reverse("invoice_cancel", args=[world["inv_a"].pk]), {"reason": "x"}).status_code == 403
    world["inv_a"].refresh_from_db()
    assert world["inv_a"].status == "issued"
    c2 = Client(enforce_csrf_checks=True)
    assert c2.post(reverse("login"), {"username": "admin", "password": "demo12345"}).status_code == 403


def test_salesman_cannot_read_or_order_for_others(world):
    b = Client()
    b.force_login(User.objects.get(username="salesman_b"))
    assert b.get(reverse("order_detail", args=[world["order_a"].pk])).status_code == 404
    a_customer = Customer.objects.get(name="Retailer A")
    r = b.post(reverse("order_new"), {"customer": a_customer.pk, f"qty_{Sku.objects.get().pk}": "5", "client_uuid": str(uuid.uuid4())})
    assert r.status_code == 200 and b"Choose a customer" in r.content  # A's customer is not in B's list


def test_user_input_is_escaped_in_pages(world):
    Customer.objects.filter(name="Retailer A").update(name="<script>alert(1)</script>")
    c = Client()
    c.force_login(User.objects.get(username="admin"))
    page = c.get(reverse("order_detail", args=[world["order_a"].pk])).content.decode()
    assert "<script>alert(1)</script>" not in page and "&lt;script&gt;alert(1)&lt;/script&gt;" in page
    c.force_login(User.objects.get(username="owner"))
    assert "<script>alert(1)" not in c.get(reverse("dashboard")).content.decode()


def test_hostile_query_params_do_not_crash(world):
    c = Client()
    c.force_login(User.objects.get(username="admin"))
    evil = ["' OR 1=1 --", "1; DROP TABLE masters_item", "../../etc/passwd", "\x00", "item:abc", "sku:99999999999999999999", "9" * 400]
    for v in evil:
        assert c.get(reverse("stock_ledger"), {"stock": v, "from": v, "to": v}).status_code == 200
        assert c.get(reverse("order_list"), {"status": v}).status_code == 200
        assert c.get(reverse("report", args=["sales-register"]), {"from": v, "to": v, "export": v}).status_code == 200
    from apps.masters.models import Item
    assert Item.objects.count() == 5


def test_garbage_post_bodies_are_handled(world):
    c = Client()
    c.force_login(User.objects.get(username="salesman_a"))
    for payload in ({}, {"customer": "abc"}, {"customer": "-1", "client_uuid": "nope"}, {"client_uuid": "x" * 5000}):
        assert c.post(reverse("order_new"), payload).status_code == 200
    c.force_login(User.objects.get(username="admin"))
    for payload in ({}, {"amount": "abc"}, {"customer": "1", "amount": "1e999999"}, {"customer": "1", "amount": "-5"}):
        assert c.post(reverse("payment_new"), payload).status_code == 200


def test_inactive_user_cannot_log_in(world):
    User.objects.filter(username="owner").update(is_active=False)
    assert not Client().login(username="owner", password="demo12345")


def test_sensitive_data_not_in_pages(world):
    c = Client()
    c.force_login(User.objects.get(username="admin"))
    for url in (reverse("home"), reverse("order_list"), reverse("invoice_list")):
        body = c.get(url).content.decode().lower()
        assert "pbkdf2" not in body and "secret_key" not in body and "demo12345" not in body and "password_hash" not in body


def test_shared_pdf_token_is_unguessable_and_long(world):
    assert len(world["inv_a"].share_token) >= 32


def test_absurdly_large_numbers_are_rejected_not_crashing(world):
    big = "9" * 30
    c = Client()
    c.force_login(User.objects.get(username="salesman_a"))
    sku = Sku.objects.get()
    r = c.post(reverse("order_new"), {"customer": Customer.objects.get(name="Retailer A").pk, f"qty_{sku.pk}": big,
                                     "client_uuid": str(uuid.uuid4())})
    assert r.status_code == 200
    c.force_login(User.objects.get(username="admin"))
    o = sales.create_order(User.objects.get(username="salesman_a"), Customer.objects.get(name="Retailer A"), {sku: 5}, client_uuid=uuid.uuid4())
    line = o.lines.get()
    r = c.post(reverse("order_approve", args=[o.pk]), {f"qty_{line.pk}": big, f"price_{line.pk}": big})
    assert r.status_code == 302
    r = c.post(reverse("payment_new"), {"customer": Customer.objects.get(name="Retailer A").pk, "amount": big, "mode": "upi"})
    assert r.status_code == 200
    c.force_login(User.objects.get(username="production"))
    from apps.production.models import ProductionRun
    run = ProductionRun.objects.first()
    r = c.post(reverse("run_start"), {"sku": sku.pk, "batches": big})
    assert r.status_code == 302
    r = c.post(reverse("run_complete", args=[run.pk]), {"good_packets": big, "damaged_pouches": big})
    assert r.status_code == 200
    r = c.post(reverse("purchase_new"), {"supplier": 1, "bill_no": "X", "lines-TOTAL_FORMS": 1, "lines-INITIAL_FORMS": 0,
                                         "lines-0-item": 1, "lines-0-qty": big, "lines-0-unit": 1, "lines-0-rate": big, "lines-0-gst_rate": "0"})
    assert r.status_code == 200


# ---------- irreversible actions ask first ----------

def test_irreversible_actions_carry_a_confirmation_and_a_danger_button():
    from apps.billing.models import Invoice
    from apps.core.benchmark import run_full_benchmark
    from apps.production.models import ProductionRun
    from apps.sales.models import Order
    seed_users(); seed_masters(); seed_inventory()
    run_full_benchmark()
    c = Client()
    c.login(username="admin", password="demo12345")
    reject = c.get(f"/orders/{Order.objects.get(status='submitted').pk}/").content.decode()
    assert 'data-confirm="Reject this order' in reject and 'btn danger' in reject
    inv = c.get(f"/invoices/{Invoice.objects.first().pk}/").content.decode()
    assert "data-confirm=\"Cancel this whole invoice" in inv and "data-confirm=\"Issue a credit note" in inv
    run = c.get(f"/production/{ProductionRun.objects.first().pk}/").content.decode()
    assert "data-confirm=\"Void this run" in run


def test_signed_in_user_is_sent_home_from_the_login_page():
    seed_users()
    c = Client()
    c.login(username="admin", password="demo12345")
    r = c.get("/login/")
    assert r.status_code == 302 and r["Location"] == "/"
