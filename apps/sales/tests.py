import uuid
from decimal import Decimal as D

import pytest
from django.urls import reverse

from apps.accounts.management.commands.seed_users import seed_users
from apps.accounts.models import User
from apps.audit.models import history
from apps.inventory import services as stock
from apps.inventory.management.commands.seed_inventory import seed_inventory
from apps.masters.management.commands.seed_masters import seed_masters
from apps.masters.models import Customer, CustomerPrice, Sku
from apps.production.tests import benchmark_purchase, benchmark_run
from apps.sales import services
from apps.sales.models import Order, OrderStatus

pytestmark = pytest.mark.django_db


@pytest.fixture
def seeded():
    seed_users()
    seed_masters()
    seed_inventory()


FG = lambda: Sku.objects.get(code="MUR200")  # noqa: E731
user = lambda n: User.objects.get(username=n)  # noqa: E731
cust = lambda n: Customer.objects.get(name=n)  # noqa: E731


def with_stock_238():
    benchmark_purchase()
    benchmark_run()
    assert stock.balance(FG()) == D("238")


def order_a(qty=150):
    return services.create_order(user("salesman_a"), cust("Retailer A"), {FG(): qty}, client_uuid=uuid.uuid4())


# ---------- taking orders ----------

def test_create_order_uses_list_price(seeded):
    o = order_a()
    line = o.lines.get()
    assert (line.ordered_qty, line.qty, line.price) == (150, 150, D("50.00"))
    assert o.status == OrderStatus.SUBMITTED and o.taxable_total == D("7500.00")  # benchmark invoice taxable value


def test_customer_specific_price_overrides_list(seeded):
    CustomerPrice.objects.create(customer=cust("Retailer A"), sku=FG(), price=D("47.50"))
    assert order_a(10).lines.get().price == D("47.50")
    assert services.price_for(cust("Distributor B"), FG()) == D("50.00")


def test_duplicate_submission_returns_same_order(seeded):
    key = uuid.uuid4()
    a = services.create_order(user("salesman_a"), cust("Retailer A"), {FG(): 5}, client_uuid=key)
    b = services.create_order(user("salesman_a"), cust("Retailer A"), {FG(): 5}, client_uuid=key)
    assert a.pk == b.pk and Order.objects.count() == 1


def test_order_validation(seeded):
    sa = user("salesman_a")
    with pytest.raises(services.OrderError):
        services.create_order(sa, cust("Distributor B"), {FG(): 5})  # not A's customer
    with pytest.raises(services.OrderError):
        services.create_order(sa, cust("Retailer A"), {})
    with pytest.raises(services.OrderError):
        services.create_order(sa, cust("Retailer A"), {FG(): 0})
    FG().__class__.objects.filter(pk=FG().pk).update(active=False)
    with pytest.raises(services.OrderError):
        services.create_order(sa, cust("Retailer A"), {FG(): 5})
    assert Order.objects.count() == 0


# ---------- stock check and approval (benchmark steps 4 and 6) ----------

def test_coverage_for_retailer_a_order(seeded):
    with_stock_238()
    c = services.coverage(order_a())[0]
    assert (c.in_stock, c.committed, c.available, c.shortage) == (D("238"), 0, D("238"), 0) and c.covered


def test_benchmark_distributor_b_shortage_of_32(seeded):
    with_stock_238()
    a = order_a()
    services.approve_order(a, user("admin"))
    b = services.create_order(user("salesman_b"), cust("Distributor B"), {FG(): 120}, client_uuid=uuid.uuid4())
    c = services.coverage(b)[0]
    # 238 in stock, 150 already promised to Retailer A's approved order => 88 available
    assert (c.in_stock, c.committed, c.available, c.shortage) == (D("238"), 150, D("88"), 32)


def test_benchmark_shortage_after_dispatch_too(seeded):
    with_stock_238()
    a = order_a()
    services.approve_order(a, user("admin"))
    Order.objects.filter(pk=a.pk).update(status=OrderStatus.DISPATCHED)
    stock.issue(FG(), D("150"), a, "dispatch")  # stand-in for the M6 dispatch
    b = services.create_order(user("salesman_b"), cust("Distributor B"), {FG(): 120}, client_uuid=uuid.uuid4())
    c = services.coverage(b)[0]
    assert (c.in_stock, c.committed, c.available, c.shortage) == (D("88"), 0, D("88"), 32)


def test_cannot_approve_beyond_available(seeded):
    with_stock_238()
    services.approve_order(order_a(), user("admin"))
    b = services.create_order(user("salesman_b"), cust("Distributor B"), {FG(): 120}, client_uuid=uuid.uuid4())
    with pytest.raises(services.OrderError, match="Only 88 packets"):
        services.approve_order(b, user("admin"))
    b.refresh_from_db()
    assert b.status == OrderStatus.SUBMITTED and b.lines.get().qty == 120  # nothing half-applied


def test_approve_only_available_caps_quantity(seeded):
    with_stock_238()
    services.approve_order(order_a(), user("admin"))
    b = services.create_order(user("salesman_b"), cust("Distributor B"), {FG(): 120}, client_uuid=uuid.uuid4())
    services.approve_order(b, user("admin"), cap_to_available=True)
    b.refresh_from_db()
    line = b.lines.get()
    assert b.status == OrderStatus.APPROVED and (line.ordered_qty, line.qty) == (120, 88)
    assert history(b).last().detail["changes"]["MUR200"]["qty"] == [120, 88]


def test_approve_with_edited_qty_and_price_is_audited(seeded):
    with_stock_238()
    o = order_a(100)
    line = o.lines.get()
    services.approve_order(o, user("admin"), edits={line.pk: (90, D("48"))})
    line.refresh_from_db()
    assert (line.ordered_qty, line.qty, line.original_price, line.price) == (100, 90, D("50"), D("48"))
    entry = history(o).last()
    assert entry.action == "approved" and entry.user.username == "admin"
    assert entry.detail["changes"]["MUR200"] == {"qty": [100, 90], "price": ["50.00", "48"]}


def test_zero_everything_cannot_be_approved(seeded):
    with_stock_238()
    o = order_a(10)
    with pytest.raises(services.OrderError):
        services.approve_order(o, user("admin"), edits={o.lines.get().pk: (0, D("50"))})


def test_negative_edit_rejected(seeded):
    with_stock_238()
    o = order_a(10)
    with pytest.raises(services.OrderError):
        services.approve_order(o, user("admin"), edits={o.lines.get().pk: (-1, D("50"))})


def test_reject_needs_reason_and_is_final(seeded):
    o = order_a()
    with pytest.raises(services.OrderError):
        services.reject_order(o, user("admin"), " ")
    services.reject_order(o, user("admin"), "Credit limit exceeded")
    o.refresh_from_db()
    assert o.status == OrderStatus.REJECTED and o.reject_reason == "Credit limit exceeded"
    with pytest.raises(services.OrderError):
        services.approve_order(o, user("admin"))
    with pytest.raises(services.OrderError):
        services.reject_order(o, user("admin"), "again")


def test_approval_does_not_move_stock(seeded):
    with_stock_238()
    services.approve_order(order_a(), user("admin"))
    assert stock.balance(FG()) == D("238")  # stock only drops at dispatch (M6)


def test_customer_wise_sales_counts_only_approved(seeded):
    with_stock_238()
    services.approve_order(order_a(100), user("admin"))
    services.create_order(user("salesman_a"), cust("Retailer A"), {FG(): 5}, client_uuid=uuid.uuid4())  # pending
    rows = services.customer_wise_sales(user("salesman_a"))
    assert len(rows) == 1 and rows[0]["value"] == D("5000.00") and rows[0]["orders"] == 1
    assert services.customer_wise_sales(user("salesman_b")) == []


# ---------- screens ----------

def test_salesman_places_order_on_screen_and_double_submit_is_safe(client, seeded):
    client.login(username="salesman_a", password="demo12345")
    page = client.get(reverse("order_new"))
    assert b"Retailer A" in page.content and b"Distributor B" not in page.content
    key = str(uuid.uuid4())
    data = {"customer": cust("Retailer A").pk, f"qty_{FG().pk}": "150", "client_uuid": key}
    r1, r2 = client.post(reverse("order_new"), data), client.post(reverse("order_new"), data)
    assert r1.status_code == 302 == r2.status_code and Order.objects.count() == 1


def test_screen_errors_keep_the_form(client, seeded):
    client.login(username="salesman_a", password="demo12345")
    data = {"customer": cust("Retailer A").pk, f"qty_{FG().pk}": "abc", "client_uuid": str(uuid.uuid4())}
    r = client.post(reverse("order_new"), data)
    assert r.status_code == 200 and b"whole numbers" in r.content and Order.objects.count() == 0
    r = client.post(reverse("order_new"), {"customer": "", "client_uuid": str(uuid.uuid4())})
    assert b"Choose a customer" in r.content


def test_salesman_sees_only_own_orders(client, seeded):
    o = order_a()
    client.login(username="salesman_b", password="demo12345")
    assert client.get(reverse("order_detail", args=[o.pk])).status_code == 404
    assert o.number.encode() not in client.get(reverse("order_list")).content
    client.login(username="salesman_a", password="demo12345")
    assert client.get(reverse("order_detail", args=[o.pk])).status_code == 200


def test_only_admin_can_approve_or_reject(client, seeded):
    with_stock_238()
    o = order_a()
    for name in ["salesman_a", "production", "accountant", "owner"]:
        client.login(username=name, password="demo12345")
        assert client.post(reverse("order_approve", args=[o.pk])).status_code == 403, name
        assert client.post(reverse("order_reject", args=[o.pk]), {"reason": "x"}).status_code == 403, name
    o.refresh_from_db()
    assert o.status == OrderStatus.SUBMITTED


def test_admin_screen_shows_shortage_and_approves_available(client, seeded):
    with_stock_238()
    services.approve_order(order_a(), user("admin"))
    b = services.create_order(user("salesman_b"), cust("Distributor B"), {FG(): 120}, client_uuid=uuid.uuid4())
    client.login(username="admin", password="demo12345")
    page = client.get(reverse("order_detail", args=[b.pk])).content.decode()
    assert "short by 32" in page
    r = client.post(reverse("order_approve", args=[b.pk]), {f"qty_{b.lines.get().pk}": "120", f"price_{b.lines.get().pk}": "50.00"})
    assert "Only 88 packets" in client.get(r["Location"]).content.decode()
    client.post(reverse("order_approve", args=[b.pk]), {f"qty_{b.lines.get().pk}": "120", f"price_{b.lines.get().pk}": "50.00", "available": "1"})
    b.refresh_from_db()
    assert b.status == OrderStatus.APPROVED and b.lines.get().qty == 88


def test_garbage_edit_values_are_handled(client, seeded):
    with_stock_238()
    o = order_a()
    client.login(username="admin", password="demo12345")
    r = client.post(reverse("order_approve", args=[o.pk]), {f"qty_{o.lines.get().pk}": "x", f"price_{o.lines.get().pk}": "y"})
    assert r.status_code == 302 and Order.objects.get().status == OrderStatus.SUBMITTED
