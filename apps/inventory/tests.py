from datetime import timedelta
from decimal import Decimal as D

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.accounts.management.commands.seed_users import seed_users
from apps.accounts.models import User
from apps.inventory import services
from apps.inventory.management.commands.seed_inventory import seed_inventory
from apps.inventory.models import ImmutableError, MovementType, StockAdjustment, StockMovement
from apps.masters.management.commands.seed_masters import seed_masters
from apps.masters.models import Item, Sku, Unit
from apps.purchasing.models import Purchase, Supplier, post_purchase

pytestmark = pytest.mark.django_db


@pytest.fixture
def seeded():
    seed_users()
    seed_masters()
    seed_inventory()


def item(code):
    return Item.objects.get(code=code)


def test_opening_balances_from_benchmark(seeded):
    assert services.balance(item("FLOUR")) == D("100")
    assert services.balance(item("OIL")) == D("30")
    assert services.balance(item("SPICE")) == D("5")
    assert services.balance(item("POUCH200")) == D("500")
    assert services.balance(item("CARTON20")) == D("30")
    assert services.balance(Sku.objects.get(code="MUR200")) == D("50")


def test_seed_inventory_idempotent(seeded):
    seed_inventory()
    assert StockMovement.objects.count() == 6


def test_every_movement_has_a_source_document(seeded):
    for m in StockMovement.objects.all():
        assert isinstance(m.source, StockAdjustment)


def test_movements_are_immutable(seeded):
    m = StockMovement.objects.first()
    m.qty = D("999")
    with pytest.raises(ImmutableError):
        m.save()
    with pytest.raises(ImmutableError):
        m.delete()
    with pytest.raises(ImmutableError):
        StockMovement.objects.all().update(qty=1)
    with pytest.raises(ImmutableError):
        StockMovement.objects.all().delete()


def test_movement_requires_saved_source(seeded):
    with pytest.raises(services.StockError):
        services.receive(item("FLOUR"), 1, 1, Purchase())


def test_benchmark_purchase_and_weighted_average(seeded):
    """Step 1: 60 kg flour at 45; opening 100 kg at 42 -> balance 160, average 43.125."""
    post_purchase(Purchase(supplier=Supplier.objects.get(), bill_no="B1"),
                  [dict(item=item("FLOUR"), qty=D("60"), unit=Unit.objects.get(code="kg"), rate=D("45"))])
    flour = item("FLOUR")
    assert services.balance(flour) == D("160")
    assert services.average_cost(flour) == D("43.125")
    m = services.issue(flour, D("40"), StockAdjustment.objects.first(), MovementType.PRODUCTION_ISSUE)
    assert m.qty == D("-40") and m.unit_cost == D("43.1250")
    assert services.balance(flour) == D("120")  # benchmark check: flour closing
    assert services.average_cost(flour) == D("43.125")  # issues do not change the average


def test_purchase_in_other_unit_converted_to_stock_unit(seeded):
    """500 g bought at 0.20/g = 0.5 kg at 200/kg."""
    spice = item("SPICE")
    post_purchase(Purchase(supplier=Supplier.objects.get(), bill_no="B2"),
                  [dict(item=spice, qty=D("500"), unit=Unit.objects.get(code="g"), rate=D("0.2"))])
    assert services.balance(spice) == D("5.5")
    last = StockMovement.objects.filter(item=spice).last()
    assert last.qty == D("0.5") and last.unit_cost == D("200.0000")


def test_cannot_issue_more_than_balance(seeded):
    src = StockAdjustment.objects.first()
    with pytest.raises(services.InsufficientStock) as e:
        services.issue(item("SPICE"), D("5.0001"), src, MovementType.PRODUCTION_ISSUE)
    assert e.value.available == D("5")
    assert services.balance(item("SPICE")) == D("5")  # nothing posted


def test_issue_in_other_unit(seeded):
    services.issue(item("OIL"), D("10500"), StockAdjustment.objects.first(), MovementType.PRODUCTION_ISSUE,
                   unit=Unit.objects.get(code="ml"))
    assert services.balance(item("OIL")) == D("19.5")  # benchmark: oil closing


def test_adjustment_recorded_as_document_and_cannot_go_negative(seeded):
    admin = User.objects.get(username="admin")
    services.adjust(item("POUCH200"), D("-4"), "Count difference", admin)
    assert services.balance(item("POUCH200")) == D("496")
    adj = StockAdjustment.objects.get(reason="Count difference")
    assert adj.user == admin
    with pytest.raises(services.InsufficientStock):
        services.adjust(item("POUCH200"), D("-497"), "Too much", admin)


def test_summary_equation_holds(seeded):
    """opening + receipts - issues +/- adjustments = closing, with a month boundary."""
    admin = User.objects.get(username="admin")
    flour = item("FLOUR")
    start = timezone.localdate().replace(day=1)
    dt_from = timezone.make_aware(timezone.datetime.combine(start, timezone.datetime.min.time()))
    post_purchase(Purchase(supplier=Supplier.objects.get(), bill_no="B3"),
                  [dict(item=flour, qty=D("60"), unit=Unit.objects.get(code="kg"), rate=D("45"))])
    services.issue(flour, D("40"), StockAdjustment.objects.first(), MovementType.PRODUCTION_ISSUE)
    services.adjust(flour, D("-2"), "Spillage", admin)
    row = services.stock_summary([flour], dt_from, timezone.now() + timedelta(minutes=1))[0]
    assert (row.opening, row.receipts, row.issues, row.adjustments, row.closing) == (D("100"), D("60"), D("40"), D("-2"), D("118"))
    assert row.opening + row.receipts - row.issues + row.adjustments == row.closing == services.balance(flour)


def test_ledger_running_balance(seeded):
    flour = item("FLOUR")
    services.issue(flour, D("10"), StockAdjustment.objects.first(), MovementType.PRODUCTION_ISSUE)
    brought_forward, entries = services.ledger(flour)
    assert brought_forward == D("0") and [b for _, b in entries] == [D("100"), D("90")]
    bf, later = services.ledger(flour, date_from=entries[1][0].moved_at)
    assert bf == D("100") and [b for _, b in later] == [D("90")]


def test_average_cost_falls_back_to_standard_rate_when_empty():
    from apps.masters.models import ItemKind
    u = Unit.objects.create(code="kg", name="Kg", dimension="mass", factor=1000)
    i = Item.objects.create(code="X", name="X", kind=ItemKind.RAW, unit=u, standard_rate=D("12"))
    assert services.average_cost(i) == D("12")


# --- views ---

def test_stock_views_role_access(client, seeded):
    for username, status in {"admin": 200, "production": 200, "accountant": 200, "owner": 200, "salesman_a": 403}.items():
        client.login(username=username, password="demo12345")
        assert client.get(reverse("stock_summary")).status_code == status, username
        client.logout()
    client.login(username="accountant", password="demo12345")
    assert client.get(reverse("stock_adjust")).status_code == 403  # read-only role
    assert client.get(reverse("purchase_new")).status_code == 403


def test_stock_summary_and_export(client, seeded):
    client.login(username="owner", password="demo12345")
    resp = client.get(reverse("stock_summary"), {"export": "csv"})
    assert resp.status_code == 200 and resp["Content-Type"].startswith("text/csv")
    body = resp.content.decode()
    assert "Flour" in body and "Murukku 200 g" in body
    xl = client.get(reverse("stock_summary"), {"export": "xlsx"})
    assert xl.content[:2] == b"PK"  # zip container


def test_ledger_page_and_export(client, seeded):
    client.login(username="accountant", password="demo12345")
    choice = f"item:{item('FLOUR').pk}"
    page = client.get(reverse("stock_ledger"), {"stock": choice})
    assert b"Brought forward" in page.content
    exp = client.get(reverse("stock_ledger"), {"stock": choice, "export": "csv"})
    assert "Brought forward" in exp.content.decode()


def test_record_purchase_through_form(client, seeded):
    client.login(username="production", password="demo12345")
    sup, kg, flour = Supplier.objects.get(), Unit.objects.get(code="kg"), item("FLOUR")
    data = {"supplier": sup.pk, "bill_no": "F-100", "bill_date": timezone.localdate().isoformat(),
            "lines-TOTAL_FORMS": 3, "lines-INITIAL_FORMS": 0, "lines-MIN_NUM_FORMS": 0, "lines-MAX_NUM_FORMS": 1000,
            "lines-0-item": flour.pk, "lines-0-qty": "60", "lines-0-unit": kg.pk, "lines-0-rate": "45", "lines-0-gst_rate": "0", "lines-1-gst_rate": "0", "lines-2-gst_rate": "0"}
    resp = client.post(reverse("purchase_new"), data)
    assert resp.status_code == 302
    assert services.balance(flour) == D("160")
    assert Purchase.objects.get().created_by.username == "production"


def test_purchase_form_rejects_wrong_unit(client, seeded):
    client.login(username="production", password="demo12345")
    litre = Unit.objects.get(code="L")
    data = {"supplier": Supplier.objects.get().pk, "bill_no": "F-101", "bill_date": timezone.localdate().isoformat(),
            "lines-TOTAL_FORMS": 3, "lines-INITIAL_FORMS": 0, "lines-MIN_NUM_FORMS": 0, "lines-MAX_NUM_FORMS": 1000,
            "lines-0-item": item("FLOUR").pk, "lines-0-qty": "1", "lines-0-unit": litre.pk, "lines-0-rate": "45", "lines-0-gst_rate": "0", "lines-1-gst_rate": "0", "lines-2-gst_rate": "0"}
    resp = client.post(reverse("purchase_new"), data)
    assert resp.status_code == 200 and Purchase.objects.count() == 0
    assert services.balance(item("FLOUR")) == D("100")
