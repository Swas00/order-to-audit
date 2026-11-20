"""Stretch goals: reorder levels / low-stock alerts, and production planning from pending orders."""

import uuid
from decimal import Decimal as D

import pytest
from django.urls import reverse

from apps.accounts.management.commands.seed_users import seed_users
from apps.accounts.models import User
from apps.core.benchmark import run_full_benchmark
from apps.inventory import services as stock
from apps.inventory.management.commands.seed_inventory import seed_inventory
from apps.masters.management.commands.seed_masters import seed_masters
from apps.masters.models import Customer, Item, Sku
from apps.production import planning
from apps.production.models import ProductionRun
from apps.reports import builders
from apps.sales import services as sales

pytestmark = pytest.mark.django_db
user = lambda n: User.objects.get(username=n)  # noqa: E731
FG = lambda: Sku.objects.get(code="MUR200")  # noqa: E731


@pytest.fixture
def seeded():
    seed_users()
    seed_masters()
    seed_inventory()


def order(qty, customer="Retailer A", salesman="salesman_a", approve=False):
    o = sales.create_order(user(salesman), Customer.objects.get(name=customer), {FG(): qty}, client_uuid=uuid.uuid4())
    if approve:
        sales.approve_order(o, user("admin"), cap_to_available=True)
    return o


# ---------- reorder levels ----------

def test_seeded_stock_is_above_every_reorder_level(seeded):
    assert stock.low_stock() == []


def test_benchmark_trips_oil_and_carton_alerts(seeded):
    run_full_benchmark()
    low = {l.item.code: (l.balance, l.level, l.shortfall) for l in stock.low_stock()}
    assert low == {"OIL": (D("19.5"), D("20.0000"), D("0.5")), "CARTON20": (D("21"), D("25.0000"), D("4"))}


def test_items_without_a_level_are_ignored_and_equal_counts_as_low(seeded):
    Item.objects.update(reorder_level=None)
    assert stock.low_stock() == []
    Item.objects.filter(code="SPICE").update(reorder_level=D("5"))  # balance is exactly 5
    assert [l.item.code for l in stock.low_stock()] == ["SPICE"]


def test_low_stock_report_and_screens(client, seeded):
    run_full_benchmark()
    t = builders.low_stock_report(None, None)[0]
    rows = {r[1]: (r[3], r[4], r[5], r[6]) for r in t.rows}
    assert rows == {"Cooking oil": (D("19.5"), D("20.0000"), D("0.5"), "At or below reorder level"),
                    "Carton (holds 20 packets)": (D("21"), D("25.0000"), D("4"), "At or below reorder level")}
    client.login(username="production", password="demo12345")
    page = client.get(reverse("stock_summary")).content.decode()
    assert "Low stock:" in page and "Cooking oil" in page
    assert client.get(reverse("report", args=["low-stock"]), {"export": "csv"}).content.decode().count("At or below") == 2
    client.login(username="owner", password="demo12345")
    assert "Cooking oil is at or below its reorder level" in client.get(reverse("dashboard")).content.decode()


# ---------- production planning ----------

def test_plan_nothing_when_stock_covers_orders(seeded):
    order(30)  # 50 in stock
    row = planning.plan()[0]
    assert (row.in_stock, row.demand, row.net_need, row.batches) == (D("50"), 30, D("0"), 0) and row.materials == []


def test_plan_rounds_up_to_whole_batches_and_scales_bom(seeded):
    order(300, approve=False)  # 50 in stock -> need 250 -> 2 batches of 200
    row = planning.plan()[0]
    assert (row.net_need, row.batches, row.output) == (D("250"), 2, 400)
    needs = {m.item_name: m.needed for m in row.materials}
    assert needs["Flour"] == D("80") and needs["Cooking oil"] == D("20") and needs["Salt and spice mix"] == D("2.4")
    assert row.can_make and row.shortages == []  # opening stock (100 kg flour, 30 L oil ...) covers 2 batches


def test_plan_lists_material_shortages(seeded):
    order(700)  # need 650 -> 4 batches -> flour 160 > 100, oil 40 > 30, spice 4.8 < 5 ok, pouches 800 > 500, cartons 40 > 30
    row = planning.plan()[0]
    assert row.batches == 4 and not row.can_make
    assert {m.item_name: m.short for m in row.shortages} == {"Flour": D("60"), "Cooking oil": D("10"), "200 g pouch": D("300"),
                                                            "Carton (holds 20 packets)": D("10")}


def test_plan_counts_approved_orders_and_can_exclude_submitted(seeded):
    order(100, approve=True)   # approved: committed against 50 in stock (cap_to_available shrinks it to 50)
    order(120, customer="Distributor B", salesman="salesman_b")  # waiting approval
    both, approved_only = planning.plan(True)[0], planning.plan(False)[0]
    assert (both.committed, both.submitted, both.demand) == (50, 120, 170)
    assert both.net_need == D("120") and both.batches == 1
    assert approved_only.submitted == 0 and approved_only.net_need == D("0") and approved_only.batches == 0


def test_benchmark_after_step_6_says_make_one_batch(seeded):
    run_full_benchmark()  # 88 in stock, Distributor B waiting for 120 -> net need 32 -> 1 batch
    row = planning.plan()[0]
    assert (row.in_stock, row.submitted, row.committed, row.net_need, row.batches, row.output) == (D("88"), 120, 0, D("32"), 1, 200)
    assert row.can_make  # flour 120 >= 40, oil 19.5 >= 10, pouches 306 >= 200, cartons 21 >= 10


def test_dispatched_and_rejected_orders_do_not_count(seeded):
    o = order(40, approve=True)
    sales.reject_order(order(500), user("admin"), "no")
    from apps.sales.models import Order, OrderStatus
    Order.objects.filter(pk=o.pk).update(status=OrderStatus.DISPATCHED)
    row = planning.plan()[0]
    assert row.demand == 0


def test_plan_screen_and_start_run_from_it(client, seeded):
    run_full_benchmark()
    client.login(username="production", password="demo12345")
    page = client.get(reverse("run_plan")).content.decode()
    assert "Make 1 batch" in page and "Materials in stock cover it" in page and "Start run of 1 batch" in page
    r = client.post(reverse("run_start"), {"sku": FG().pk, "batches": "1"})
    assert r.status_code == 302 and ProductionRun.objects.count() == 2
    assert "(excluded)" in client.get(reverse("run_plan"), {"submitted": "0"}).content.decode()


def test_plan_access(client, seeded):
    for name, expected in {"production": 200, "admin": 200, "owner": 200, "accountant": 200, "salesman_a": 403}.items():
        client.login(username=name, password="demo12345")
        assert client.get(reverse("run_plan")).status_code == expected, name
    client.login(username="owner", password="demo12345")
    assert "Start run" not in client.get(reverse("run_plan")).content.decode()  # read-only roles get no button
