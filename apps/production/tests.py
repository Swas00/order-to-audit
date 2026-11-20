from decimal import Decimal as D

import pytest
from django.urls import reverse

from apps.accounts.management.commands.seed_users import seed_users
from apps.accounts.models import User
from apps.audit.models import history
from apps.inventory import services as stock
from apps.inventory.management.commands.seed_inventory import seed_inventory
from apps.inventory.models import StockMovement
from apps.masters.management.commands.seed_masters import seed_masters
from apps.masters.models import Item, Sku, Unit
from apps.production import services as prod
from apps.production.models import RunStatus
from apps.production.variance import FLAG, NOTE, OK, variance
from apps.purchasing.models import Purchase, Supplier, post_purchase

pytestmark = pytest.mark.django_db


@pytest.fixture
def seeded():
    seed_users()
    seed_masters()
    seed_inventory()


def item(code):
    return Item.objects.get(code=code)


def bal(code):
    return stock.balance(item(code))


FG = lambda: Sku.objects.get(code="MUR200")  # noqa: E731


def benchmark_purchase():
    post_purchase(Purchase(supplier=Supplier.objects.get(), bill_no="B1"),
                  [dict(item=item("FLOUR"), qty=D("60"), unit=Unit.objects.get(code="kg"), rate=D("45"))])


def benchmark_run(user=None):
    """Steps 2-3 of the benchmark exactly as written in the problem statement."""
    user = user or User.objects.get(username="production")
    run = prod.start_run(FG(), 1, user)
    prod.issue_material(run, item("FLOUR"), D("40"), user)
    prod.issue_material(run, item("OIL"), D("10.5"), user)
    prod.issue_material(run, item("SPICE"), D("1.2"), user)
    prod.complete_run(run, 188, 6, {item("CARTON20"): D("9")}, user)
    run.refresh_from_db()
    return run


# ---------- variance rules (pure) ----------

def test_variance_percentages_and_status():
    assert variance(10, "10.5", 3).pct == D("5.00") and variance(10, "10.5", 3).status == FLAG
    assert variance(200, 188, 3, higher_is_adverse=False).pct == D("-6.00")
    assert variance(200, 188, 3, higher_is_adverse=False).status == FLAG
    assert variance(40, 40, 3).status == OK
    assert variance(200, 194, 3).status == OK  # exactly -3.00% is within tolerance (strictly greater flags)
    assert variance(10, 9, 3).status == NOTE  # favourable beyond tolerance: noted, not flagged
    assert variance(200, 210, 3, higher_is_adverse=False).status == NOTE  # more output than planned
    assert variance(0, 0, 3).status == OK and variance(0, 2, 3).status == FLAG and variance(0, 2, 3).pct is None


def test_variance_uses_decimal_exactness():
    assert variance("0.3", "0.309", 3).pct == D("3.00") and variance("0.3", "0.309", 3).status == OK
    assert variance("0.3", "0.3091", 3).status == FLAG


# ---------- benchmark steps 1-3 ----------

def test_benchmark_stock_after_run(seeded):
    benchmark_purchase()
    benchmark_run()
    assert bal("FLOUR") == D("120")     # 100 + 60 - 40
    assert bal("OIL") == D("19.5")
    assert bal("SPICE") == D("3.8")
    assert bal("POUCH200") == D("306")  # 500 - 188 used - 6 damaged
    assert bal("CARTON20") == D("21")
    assert stock.balance(FG()) == D("238")  # 50 + 188


def test_benchmark_variances(seeded):
    benchmark_purchase()
    run = benchmark_run()
    r = prod.run_report(run)
    by = {l.item.code: l.variance for l in r.lines}
    assert r.tolerance == D("3.00")
    assert (r.yield_variance.planned, r.yield_variance.actual) == (D("200"), D("188"))
    assert r.yield_variance.pct == D("-6.00") and r.yield_variance.status == FLAG
    assert by["OIL"].pct == D("5.00") and by["OIL"].status == FLAG
    assert by["FLOUR"].status == OK and by["SPICE"].status == OK
    assert by["POUCH200"].actual == D("194") and by["POUCH200"].status == OK
    assert by["CARTON20"].pct == D("-10.00") and by["CARTON20"].status == NOTE
    assert r.alerts == 2  # yield and oil
    assert r.wastage_packets == D("12")


def test_benchmark_material_cost_weighted_average(seeded):
    benchmark_purchase()
    run = benchmark_run()
    # flour 40 x 43.125 + oil 10.5 x 140 + spice 1.2 x 200 + pouches 194 x 2.5 + cartons 9 x 15
    assert prod.material_cost(run) == D("1725") + D("1470") + D("240") + D("485") + D("135") == D("4055")
    fg_move = StockMovement.objects.get(sku=FG(), movement_type="production_output")
    assert fg_move.qty == D("188") and fg_move.unit_cost == (D("4055") / 188).quantize(D("0.0001"))


def test_every_run_movement_links_to_the_run(seeded):
    benchmark_purchase()
    run = benchmark_run()
    moves = prod.run_movements(run)
    assert moves.count() == 7  # flour, oil, spice, pouches used, pouches damaged, cartons, finished goods
    assert all(m.source == run for m in moves)


# ---------- guards ----------

def test_cannot_issue_more_than_stock_and_nothing_is_posted(seeded):
    user = User.objects.get(username="production")
    run = prod.start_run(FG(), 1, user)
    with pytest.raises(stock.InsufficientStock):
        prod.issue_material(run, item("SPICE"), D("6"), user)
    assert bal("SPICE") == D("5")
    assert run.lines.get(item=item("SPICE")).actual_qty == 0


def test_complete_is_atomic_when_pouches_run_out(seeded):
    user = User.objects.get(username="production")
    stock.adjust(item("POUCH200"), D("-400"), "Pouches used elsewhere", user)  # 100 left
    run = prod.start_run(FG(), 1, user)
    with pytest.raises(stock.InsufficientStock):
        prod.complete_run(run, 188, 6, {item("CARTON20"): D("9")}, user)
    run.refresh_from_db()
    assert run.status == RunStatus.IN_PROGRESS
    assert bal("POUCH200") == D("100") and bal("CARTON20") == D("30") and stock.balance(FG()) == D("50")


def test_state_rules(seeded):
    user = User.objects.get(username="production")
    run = prod.start_run(FG(), 1, user)
    with pytest.raises(prod.ProductionError):
        prod.issue_material(run, item("POUCH200"), D("1"), user)  # packaging not issued at stage 1
    prod.complete_run(run, 0, 0, {}, user)
    with pytest.raises(prod.ProductionError):
        prod.complete_run(run, 10, 0, {}, user)
    with pytest.raises(prod.ProductionError):
        prod.issue_material(run, item("FLOUR"), D("1"), user)
    with pytest.raises(prod.ProductionError):
        prod.start_run(FG(), 0, user)


def test_fractional_batch_scales_plan(seeded):
    user = User.objects.get(username="production")
    run = prod.start_run(FG(), D("0.5"), user)
    planned = {l.item.code: l.planned_qty for l in run.lines.all()}
    assert planned["FLOUR"] == D("20") and planned["SPICE"] == D("0.6") and run.expected_output == D("100")


def test_bom_snapshot_survives_bom_edit(seeded):
    user = User.objects.get(username="production")
    run = prod.start_run(FG(), 1, user)
    line = run.bom.lines.get(item__code="FLOUR")
    line.qty_per_batch = D("99")
    line.save()
    assert run.lines.get(item__code="FLOUR").planned_qty == D("40")


# ---------- void and audit ----------

def test_void_reverses_everything_and_is_audited(seeded):
    benchmark_purchase()
    run = benchmark_run()
    admin = User.objects.get(username="admin")
    prod.void_run(run, "Wrong quantities entered", admin)
    assert bal("FLOUR") == D("160") and bal("OIL") == D("30") and bal("POUCH200") == D("500")
    assert bal("CARTON20") == D("30") and stock.balance(FG()) == D("50")
    run.refresh_from_db()
    assert run.status == RunStatus.VOIDED
    actions = [(h.action, h.user.username) for h in history(run)]
    assert actions[0] == ("started", "production") and actions[-1] == ("voided", "admin")
    assert ("completed", "production") in actions
    with pytest.raises(prod.ProductionError):
        prod.void_run(run, "again", admin)


def test_void_refused_when_output_already_sold(seeded):
    benchmark_purchase()
    run = benchmark_run()
    stock.issue(FG(), D("230"), run, "dispatch")  # stand-in for dispatch; leaves 8 < 188 to reverse
    with pytest.raises(stock.InsufficientStock):
        prod.void_run(run, "oops", User.objects.get(username="admin"))
    run.refresh_from_db()
    assert run.status == RunStatus.COMPLETED and bal("FLOUR") == D("120")  # nothing half-reversed


def test_void_needs_reason(seeded):
    run = prod.start_run(FG(), 1, User.objects.get(username="production"))
    with pytest.raises(prod.ProductionError):
        prod.void_run(run, "  ", User.objects.get(username="admin"))


# ---------- views ----------

def post(client, name, data=None, **kw):
    return client.post(reverse(name, kwargs=kw), data or {})


def test_full_run_through_screens(client, seeded):
    benchmark_purchase()
    client.login(username="production", password="demo12345")
    r = post(client, "run_start", {"sku": FG().pk, "batches": "1"})
    run_url = r["Location"]
    pk = int(run_url.strip("/").split("/")[-1])
    kg, litre = Unit.objects.get(code="kg"), Unit.objects.get(code="L")
    for code, qty, unit in [("FLOUR", "40", kg), ("OIL", "10.5", litre), ("SPICE", "1.2", kg)]:
        assert post(client, "run_issue", {"item": item(code).pk, "qty": qty, "unit": unit.pk}, pk=pk).status_code == 302
    r = post(client, "run_complete", {"good_packets": 188, "damaged_pouches": 6, "remark": "Fryer temp",
                                      f"pack_{item('CARTON20').pk}": "9"}, pk=pk)
    assert r.status_code == 302
    page = client.get(run_url).content.decode()
    assert "-6.00%" in page and "5.00%" in page and "FLAGGED" in page
    assert bal("OIL") == D("19.5") and stock.balance(FG()) == D("238")


def test_screen_shows_error_for_insufficient_stock(client, seeded):
    client.login(username="production", password="demo12345")
    run = prod.start_run(FG(), 1, User.objects.get(username="production"))
    r = post(client, "run_issue", {"item": item("SPICE").pk, "qty": "50", "unit": Unit.objects.get(code="kg").pk}, pk=run.pk)
    assert r.status_code == 200 and bal("SPICE") == D("5")  # page re-rendered with the error
    assert "Insufficient stock of Salt and spice mix" in r.content.decode()


def test_role_access(client, seeded):
    run = prod.start_run(FG(), 1, User.objects.get(username="production"))
    for username, view, write, void in [("production", 200, 302, 403), ("admin", 200, 302, 302),
                                        ("accountant", 200, 403, 403), ("owner", 200, 403, 403),
                                        ("salesman_a", 403, 403, 403)]:
        client.login(username=username, password="demo12345")
        assert client.get(reverse("run_detail", args=[run.pk])).status_code == view, username
        assert post(client, "run_issue", {"item": item("FLOUR").pk, "qty": "1", "unit": Unit.objects.get(code="kg").pk},
                    pk=run.pk).status_code == write, username
        assert post(client, "run_void", {"reason": "x"}, pk=run.pk).status_code == void, username
        client.logout()


def test_completion_form_prefills_packaging_as_plain_numbers(seeded):
    from apps.production.forms import CompleteForm
    run = prod.start_run(FG(), 1, User.objects.get(username="production"))
    html = str(CompleteForm(run)["pack_%d" % item("CARTON20").pk])
    assert 'value="10"' in html and "E+" not in html
    half = prod.start_run(FG(), D("0.5"), User.objects.get(username="production"))
    assert 'value="5"' in str(CompleteForm(half)["pack_%d" % item("CARTON20").pk])
