from decimal import Decimal as D

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from apps.accounts.management.commands.seed_users import seed_users
from apps.masters.bom import planned_consumption, planned_output
from apps.masters.management.commands.seed_masters import seed_masters
from apps.masters.models import Bom, BomLine, Customer, Item, Sku, Unit, validate_gstin
from apps.masters.units import UnitError, cartons_to_packets, convert, packets_to_cartons

pytestmark = pytest.mark.django_db


@pytest.fixture
def seeded():
    seed_users()
    seed_masters()


def u(code):
    return Unit.objects.get(code=code)


def test_seed_is_idempotent(seeded):
    seed_masters()
    assert Unit.objects.count() == 7 and Item.objects.count() == 5
    assert Bom.objects.count() == 1 and BomLine.objects.count() == 5 and Customer.objects.count() == 2


def test_unit_conversions_exact(seeded):
    assert convert("40", u("kg"), u("g")) == D("40000")
    assert convert("200", u("g"), u("kg")) == D("0.2")
    assert convert("10.5", u("L"), u("ml")) == D("10500")
    assert convert("1", u("packet"), u("piece")) == D("1")
    assert convert("0.1", u("kg"), u("g")) == D("100")  # no float drift


def test_cross_dimension_and_carton_conversion_refused(seeded):
    with pytest.raises(UnitError):
        convert("1", u("kg"), u("L"))
    with pytest.raises(UnitError):
        convert("1", u("carton"), u("packet"))


def test_carton_helpers():
    assert cartons_to_packets("9", 20) == D("180")
    assert packets_to_cartons(188, 20) == (9, 8)


def test_bom_planned_consumption_and_scaling(seeded):
    bom = Bom.objects.get()
    planned = {p.item_name: p.planned_qty for p in planned_consumption(bom, D("1"))}
    assert planned == {"Flour": D("40"), "Cooking oil": D("10"), "Salt and spice mix": D("1.2"),
                       "200 g pouch": D("200"), "Carton (holds 20 packets)": D("10")}
    half = {p.item_name: p.planned_qty for p in planned_consumption(bom, D("0.5"))}
    assert half["Flour"] == D("20") and half["Salt and spice mix"] == D("0.6")
    assert planned_output(bom, D("1")) == 200 and planned_output(bom, D("2.5")) == 500


def test_bom_line_in_grams_converts_to_stock_unit(seeded):
    bom = Bom.objects.get()
    line = bom.lines.get(item__code="SPICE")
    line.qty_per_batch, line.unit = D("1200"), u("g")
    line.save()
    spice = [p for p in planned_consumption(bom, D("1")) if p.item_name.startswith("Salt")][0]
    assert spice.planned_qty == D("1.2") and spice.unit == "kg"


def test_bom_line_wrong_dimension_rejected(seeded):
    line = BomLine.objects.get(item__code="FLOUR")
    line.unit = u("L")
    with pytest.raises(ValidationError):
        line.full_clean()


def test_only_one_active_bom_per_sku(seeded):
    sku = Sku.objects.get()
    with pytest.raises(IntegrityError), transaction.atomic():
        Bom.objects.create(sku=sku, version=2, batch_yield=100, is_active=True)
    Bom.objects.create(sku=sku, version=2, batch_yield=100, is_active=False)


def test_non_positive_bom_quantity_rejected(seeded):
    line = Bom.objects.get().lines.first()
    with pytest.raises(IntegrityError), transaction.atomic():
        BomLine.objects.filter(pk=line.pk).update(qty_per_batch=D("0"))


def test_admin_screens_gated_by_role(client, seeded):
    from django.urls import reverse
    expectations = {"admin": (200, 200), "production": (200, 403), "salesman_a": (302, 302), "owner": (302, 302)}
    for username, (view_status, add_status) in expectations.items():
        client.login(username=username, password="demo12345")
        assert client.get(reverse("admin:masters_item_changelist")).status_code == view_status, username
        assert client.get(reverse("admin:masters_item_add")).status_code == add_status, username
        client.logout()


def test_second_pack_size_shares_structure(seeded):
    """Interview-style change: 500 g SKU with its own BOM copy, no code change."""
    Sku.objects.create(code="MUR500", product_name="Murukku", pack_size="500 g", hsn_code="19059030",
                       selling_price=D("120"), packets_per_carton=10)
    assert Sku.objects.filter(product_name="Murukku").count() == 2


def test_gstin_and_state_validation(seeded):
    validate_gstin("33AAACR1111A1Z5")
    with pytest.raises(ValidationError):
        validate_gstin("BADGSTIN")
    c = Customer.objects.get(name="Retailer A")
    c.state_code = "29"
    with pytest.raises(ValidationError):
        c.clean()


def test_benchmark_customers_place_of_supply(seeded):
    assert Customer.objects.get(name="Retailer A").state_code == "33"
    assert Customer.objects.get(name="Distributor B").state_code == "29"
