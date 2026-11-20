"""The interview scenario: "add a second pack size of 500 g sharing the same recipe".

Done purely with master data (what an admin does in /admin/), no code change. This test is the proof."""

import uuid
from decimal import Decimal as D

import pytest

from apps.accounts.management.commands.seed_users import seed_users
from apps.accounts.models import User
from apps.billing import services as billing
from apps.core.benchmark import run_full_benchmark
from apps.inventory import services as stock
from apps.inventory.management.commands.seed_inventory import seed_inventory
from apps.masters.management.commands.seed_masters import seed_masters
from apps.masters.models import Bom, BomLine, Customer, Item, ItemKind, Sku, Unit
from apps.production import services as prod
from apps.sales import services as sales

pytestmark = pytest.mark.django_db
user = lambda n: User.objects.get(username=n)  # noqa: E731


@pytest.fixture
def seeded():
    seed_users()
    seed_masters()
    seed_inventory()


def add_500g_pack():
    """Steps an admin performs in the admin site."""
    kg, piece = Unit.objects.get(code="kg"), Unit.objects.get(code="piece")
    pouch = Item.objects.create(code="POUCH500", name="500 g pouch", kind=ItemKind.PACKAGING, unit=piece, standard_rate=D("5"))
    carton = Item.objects.create(code="CARTON10", name="Carton (holds 10 packets)", kind=ItemKind.PACKAGING, unit=piece, standard_rate=D("18"))
    sku = Sku.objects.create(code="MUR500", product_name="Murukku", pack_size="500 g", hsn_code="19059030", gst_rate=D("5"),
                             selling_price=D("120"), packets_per_carton=10, shelf_life_days=90, pouch_item=pouch)
    bom = Bom.objects.create(sku=sku, version=1, batch_yield=80)  # the same 40 kg dough makes 80 x 500 g
    for code, qty, unit in (("FLOUR", "40", kg), ("OIL", "10", Unit.objects.get(code="L")), ("SPICE", "1.2", kg)):  # same dough as the 200 g recipe
        BomLine.objects.create(bom=bom, item=Item.objects.get(code=code), qty_per_batch=D(qty), unit=unit)
    BomLine.objects.create(bom=bom, item=pouch, qty_per_batch=D("80"), unit=piece)
    BomLine.objects.create(bom=bom, item=carton, qty_per_batch=D("8"), unit=piece)
    stock.adjust(pouch, D("200"), "Opening", user("admin"), unit_cost=D("5"), is_opening=True)
    stock.adjust(carton, D("20"), "Opening", user("admin"), unit_cost=D("18"), is_opening=True)
    return sku, carton


def test_new_pack_size_works_through_production_sales_and_costing(seeded):
    run_full_benchmark()                      # the 200 g benchmark is untouched by the new SKU
    sku, carton = add_500g_pack()
    prodn = user("production")
    run = prod.start_run(sku, 1, prodn)
    assert run.expected_output == D("80")
    for code, qty in (("FLOUR", "40"), ("OIL", "10"), ("SPICE", "1.2")):
        prod.issue_material(run, Item.objects.get(code=code), D(qty), prodn)
    prod.complete_run(run, 78, 1, {carton: D("7")}, prodn)
    assert stock.balance(sku) == D("78") and stock.balance(Sku.objects.get(code="MUR200")) == D("88")  # separate stock per pack size
    assert stock.balance(Item.objects.get(code="POUCH500")) == D("121")                                 # 200 - 78 used - 1 damaged
    batch = run.fg_batches.get()
    assert batch.number.startswith("MUR500-")
    order = sales.create_order(user("salesman_a"), Customer.objects.get(name="Retailer A"), {sku: 10}, client_uuid=uuid.uuid4())
    assert order.lines.get().price == D("120.00")
    sales.approve_order(order, user("admin"))
    order.refresh_from_db()
    inv = billing.raise_invoice(order, user("admin"))
    assert (inv.taxable_total, inv.cgst_total, inv.sgst_total, inv.grand_total) == (D("1200.00"), D("30.00"), D("30.00"), D("1260.00"))
    billing.dispatch_invoice(inv, user("admin"))
    assert stock.balance(sku) == D("68")
    run.refresh_from_db()
    assert prod.run_report(run).yield_variance.actual == 78


def test_second_pack_size_does_not_disturb_the_benchmark_checks(seeded):
    from apps.core.benchmark import check_results
    add_500g_pack()
    assert all(c.ok for c in check_results(run_full_benchmark()))
