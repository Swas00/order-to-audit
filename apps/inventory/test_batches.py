"""Batch and expiry tracking for finished goods, with first-expiry-first-out dispatch."""

import uuid
from datetime import timedelta
from decimal import Decimal as D

import pytest
from django.db import IntegrityError, transaction
from django.urls import reverse
from django.utils import timezone

from apps.accounts.management.commands.seed_users import seed_users
from apps.accounts.models import User
from apps.billing import services as billing
from apps.core.benchmark import check_results, run_full_benchmark
from apps.inventory import services as stock
from apps.inventory.management.commands.seed_inventory import seed_inventory
from apps.inventory.models import Batch, MovementType, StockAdjustment, StockMovement
from apps.masters.management.commands.seed_masters import seed_masters
from apps.masters.models import Customer, Item, Sku
from apps.production import planning, services as prod
from apps.reports import builders
from apps.sales import services as sales

pytestmark = pytest.mark.django_db
user = lambda n: User.objects.get(username=n)  # noqa: E731
FG = lambda: Sku.objects.get(code="MUR200")  # noqa: E731
today = lambda: timezone.localdate()  # noqa: E731


@pytest.fixture
def seeded():
    seed_users()
    seed_masters()
    seed_inventory()


def add_batch(number, expires_in_days, qty, made_days_ago=10):
    b = Batch.objects.create(sku=FG(), number=number, mfg_date=today() - timedelta(days=made_days_ago),
                             expiry_date=today() + timedelta(days=expires_in_days))
    stock.adjust(FG(), D(qty), "Test batch", user("admin"), unit_cost=D("20"), batch=b)
    return b


def dispatch_doc():
    return StockAdjustment.objects.first()


# ---------- model rules ----------

def test_opening_stock_is_a_batch(seeded):
    b = Batch.objects.get()
    assert b.number == "OPEN-MUR200" and stock.batch_balance(b) == D("50") and b.expiry_date == today() + timedelta(days=60)


def test_database_enforces_batch_rules(seeded):
    doc = dispatch_doc()
    from django.contrib.contenttypes.models import ContentType
    ct = ContentType.objects.get_for_model(doc)
    with pytest.raises(IntegrityError), transaction.atomic():  # finished goods without a batch
        StockMovement.objects.create(sku=FG(), movement_type="adjustment", qty=1, source_type=ct, source_id=doc.pk)
    with pytest.raises(IntegrityError), transaction.atomic():  # material with a batch
        StockMovement.objects.create(item=Item.objects.get(code="FLOUR"), batch=Batch.objects.get(), movement_type="adjustment",
                                     qty=1, source_type=ct, source_id=doc.pk)
    with pytest.raises(IntegrityError), transaction.atomic():  # expiry before manufacture
        Batch.objects.create(sku=FG(), number="BAD", mfg_date=today(), expiry_date=today() - timedelta(days=1))


def test_service_refuses_missing_or_foreign_batch(seeded):
    with pytest.raises(stock.StockError):
        stock.adjust(FG(), D("5"), "no batch", user("admin"))
    with pytest.raises(stock.StockError):
        stock.receive(FG(), D("5"), D("1"), dispatch_doc(), MovementType.ADJUSTMENT)
    other = Sku.objects.create(code="X1", product_name="Other", pack_size="1 kg", hsn_code="1905", selling_price=D("1"))
    with pytest.raises(stock.StockError):
        stock.adjust(other, D("5"), "wrong sku", user("admin"), batch=Batch.objects.get())


# ---------- production creates batches ----------

def test_completed_run_creates_a_batch_with_expiry(seeded):
    ctx = run_full_benchmark()
    b = Batch.objects.get(run=ctx["run"])
    assert b.number == f"MUR200-{today():%y%m%d}-{ctx['run'].pk:03d}"
    assert b.mfg_date == today() and b.expiry_date == today() + timedelta(days=90)
    assert stock.batch_balance(b) == D("88")  # 188 made - 100 dispatched from it


def test_shelf_life_comes_from_the_product(seeded):
    Sku.objects.update(shelf_life_days=30)
    run = prod.start_run(FG(), 1, user("production"))
    prod.complete_run(run, 10, 0, {}, user("production"))
    b = Batch.objects.get(run=run)
    assert (b.expiry_date - b.mfg_date).days == 30


def test_run_with_no_output_creates_no_batch(seeded):
    run = prod.start_run(FG(), 1, user("production"))
    prod.complete_run(run, 0, 0, {}, user("production"))
    assert not Batch.objects.filter(run=run).exists()


# ---------- FEFO dispatch (benchmark step 5) ----------

def test_benchmark_dispatch_takes_opening_batch_first(seeded):
    ctx = run_full_benchmark()
    opening, made = Batch.objects.get(number="OPEN-MUR200"), Batch.objects.get(run=ctx["run"])
    assert stock.batch_balance(opening) == D("0") and stock.batch_balance(made) == D("88")
    assert stock.balance(FG()) == D("88")
    assert all(c.ok for c in check_results(ctx))  # batch tracking did not change any benchmark number


def test_fefo_orders_by_expiry_not_by_creation(seeded):
    add_batch("LATE", 80, 100)
    add_batch("SOON", 20, 30)     # created after LATE, but expires before it
    add_batch("MID", 40, 50)      # opening batch expires in 60 days, so the order is SOON, MID, OPEN, LATE
    assert [b.number for b, _ in stock.batches_with_stock(FG())] == ["SOON", "MID", "OPEN-MUR200", "LATE"]
    moves = stock.issue_fefo(FG(), D("160"), dispatch_doc(), MovementType.DISPATCH)
    assert [(m.batch.number, -m.qty) for m in moves] == [("SOON", D("30")), ("MID", D("50")), ("OPEN-MUR200", D("50")), ("LATE", D("30"))]
    assert stock.batch_balance(Batch.objects.get(number="LATE")) == D("70") and stock.balance(FG()) == D("70")


def test_fefo_stops_mid_batch(seeded):
    add_batch("SOON", 20, 30)
    moves = stock.issue_fefo(FG(), D("45"), dispatch_doc(), MovementType.DISPATCH)
    assert [(m.batch.number, -m.qty) for m in moves] == [("SOON", D("30")), ("OPEN-MUR200", D("15"))]
    assert stock.batch_balance(Batch.objects.get(number="OPEN-MUR200")) == D("35")


def test_dispatch_cost_is_the_skus_average(seeded):
    add_batch("B1", 30, 50)
    moves = stock.issue_fefo(FG(), D("10"), dispatch_doc(), MovementType.DISPATCH)
    assert len({m.unit_cost for m in moves}) == 1


# ---------- expiry ----------

def test_expired_batches_are_never_sold(seeded):
    add_batch("OLD", -1, 40, made_days_ago=100)           # expired yesterday
    assert stock.saleable_balance(FG()) == D("50") and stock.balance(FG()) == D("90") and stock.expired_balance(FG()) == D("40")
    with pytest.raises(stock.InsufficientStock, match="past expiry"):
        stock.issue_fefo(FG(), D("60"), dispatch_doc(), MovementType.DISPATCH)
    moves = stock.issue_fefo(FG(), D("50"), dispatch_doc(), MovementType.DISPATCH)
    assert {m.batch.number for m in moves} == {"OPEN-MUR200"} and stock.batch_balance(Batch.objects.get(number="OLD")) == 40


def test_batch_expiring_today_is_still_saleable(seeded):
    add_batch("TODAY", 0, 10)
    assert stock.saleable_balance(FG()) == D("60")


def test_order_coverage_and_planning_ignore_expired_stock(seeded):
    add_batch("OLD", -5, 100, made_days_ago=100)
    o = sales.create_order(user("salesman_a"), Customer.objects.get(name="Retailer A"), {FG(): 120}, client_uuid=uuid.uuid4())
    c = sales.coverage(o)[0]
    assert (c.in_stock, c.available, c.shortage) == (D("50"), D("50"), 70)  # 100 expired packets do not count
    with pytest.raises(sales.OrderError, match="Only 50"):
        sales.approve_order(o, user("admin"))
    row = planning.plan()[0]
    assert row.in_stock == D("50") and row.net_need == D("70") and row.batches == 1


def test_expired_stock_can_be_written_off(seeded):
    old = add_batch("OLD", -1, 40, made_days_ago=100)
    stock.adjust(FG(), D("-40"), "Expired: destroyed", user("admin"), batch=old)
    assert stock.batch_balance(old) == 0 and stock.balance(FG()) == D("50")
    old2 = add_batch("OLD2", -1, 10, made_days_ago=100)
    # no batch named: earliest expiry first, expired included
    stock.adjust(FG(), D("-10"), "Expired: destroyed", user("admin"))
    assert stock.batch_balance(old2) == 0 and stock.balance(FG()) == D("50")
    with pytest.raises(stock.InsufficientStock):
        stock.adjust(FG(), D("-1"), "too much", user("admin"), batch=old)


def test_expiry_alerts(seeded):
    add_batch("SOON", 5, 20)
    add_batch("FAR", 80, 20)
    gone = add_batch("EMPTY", 3, 5)
    stock.adjust(FG(), D("-5"), "gone", user("admin"), batch=gone)   # soon but no stock: no alert
    add_batch("OLD", -3, 7, made_days_ago=100)
    alerts = {a.batch.number: (a.on_hand, a.days_left, a.expired) for a in stock.expiry_alerts()}
    assert alerts == {"OLD": (D("7"), -3, True), "SOON": (D("20"), 5, False)}
    assert "FAR" not in alerts and "EMPTY" not in alerts


# ---------- voiding and returns keep batches consistent ----------

def test_void_run_removes_its_batch_stock(seeded):
    run = prod.start_run(FG(), 1, user("production"))
    prod.issue_material(run, Item.objects.get(code="FLOUR"), D("20"), user("production"))
    prod.complete_run(run, 100, 0, {}, user("production"))
    b = Batch.objects.get(run=run)
    prod.void_run(run, "Entered wrong", user("admin"))
    assert stock.batch_balance(b) == 0 and stock.balance(FG()) == D("50")


def test_void_refused_once_its_batch_has_been_sold(seeded):
    run = prod.start_run(FG(), 1, user("production"))
    prod.issue_material(run, Item.objects.get(code="FLOUR"), D("20"), user("production"))
    prod.complete_run(run, 100, 0, {}, user("production"))
    stock.issue_fefo(FG(), D("120"), dispatch_doc(), MovementType.DISPATCH)  # 50 opening + 70 from the new batch
    with pytest.raises(stock.InsufficientStock):
        prod.void_run(run, "too late", user("admin"))
    assert stock.batch_balance(Batch.objects.get(run=run)) == D("30")


# ---------- traceability / recall ----------

def test_batch_trace_shows_customers_who_received_it(seeded):
    ctx = run_full_benchmark()
    made = Batch.objects.get(run=ctx["run"])
    shipments = billing.batch_trace(made)
    assert [(i.number, c.name, q) for i, c, q in shipments] == [(ctx["inv_a"].number, "Retailer A", D("100"))]
    opening = billing.batch_trace(Batch.objects.get(number="OPEN-MUR200"))
    assert [(c.name, q) for _, c, q in opening] == [("Retailer A", D("50"))]


# ---------- reports and screens ----------

def test_batch_report_and_status(seeded):
    add_batch("SOON", 5, 20)
    add_batch("OLD", -2, 7, made_days_ago=100)
    rows = {r[1]: r for r in builders.batch_report(None, None)[0].rows}
    assert rows["SOON"][5:] == (5, D("20"), "Expires soon") and rows["OLD"][5:] == (-2, D("7"), "EXPIRED: cannot be sold")
    assert rows["OPEN-MUR200"][6] == D("50")


def test_screens_show_batches(client, seeded):
    ctx = run_full_benchmark()
    client.login(username="admin", password="demo12345")
    inv_page = client.get(reverse("invoice_detail", args=[ctx["inv_a"].pk])).content.decode()
    assert "Dispatched from batches" in inv_page and "OPEN-MUR200" in inv_page
    assert "Finished-goods batch" in client.get(reverse("run_detail", args=[ctx["run"].pk])).content.decode()
    ledger = client.get(reverse("stock_ledger"), {"stock": f"sku:{FG().pk}"}).content.decode()
    assert "OPEN-MUR200" in ledger and "<th>Batch</th>" in ledger
    made = Batch.objects.get(run=ctx["run"])
    assert "Retailer A" in client.get(reverse("batch_trace", args=[made.pk])).content.decode()
    assert made.number in client.get(reverse("batch_list")).content.decode()
    assert client.get(reverse("report", args=["batches"]), {"export": "csv"}).status_code == 200
    ledger_csv = client.get(reverse("stock_ledger"), {"stock": f"sku:{FG().pk}", "export": "csv"}).content.decode()
    assert "Batch" in ledger_csv.splitlines()[0]


def test_dashboard_and_stock_page_show_expiry_alerts(client, seeded):
    add_batch("SOON", 5, 20)
    client.login(username="owner", password="demo12345")
    page = client.get(reverse("dashboard")).content.decode()
    assert "Expiry alerts" in page and "SOON" in page and "expires in 5 days" in page
    assert "Expiry:" in client.get(reverse("stock_summary")).content.decode()


def test_adjustment_screen_requires_batch_for_additions(client, seeded):
    client.login(username="production", password="demo12345")
    base = {"sku": FG().pk, "qty": "10", "reason": "Found stock"}
    r = client.post(reverse("stock_adjust"), base)
    assert r.status_code == 200 and "Choose the batch" in r.content.decode() and stock.balance(FG()) == D("50")
    b = Batch.objects.get()
    assert client.post(reverse("stock_adjust"), {**base, "batch": b.pk}).status_code == 302
    assert stock.batch_balance(b) == D("60")


def test_trace_pages_access(client, seeded):
    b = Batch.objects.get()
    for name, expected in {"admin": 200, "production": 200, "accountant": 200, "owner": 200, "salesman_a": 403}.items():
        client.login(username=name, password="demo12345")
        assert client.get(reverse("batch_trace", args=[b.pk])).status_code == expected, name
        assert client.get(reverse("batch_list")).status_code == expected, name


def run_full_benchmark_():  # pragma: no cover  (kept for symmetry with other test modules)
    return run_full_benchmark()
