from datetime import date, timedelta
from decimal import Decimal as D

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.accounts.management.commands.seed_users import seed_users
from apps.accounts.models import User
from apps.costing import services
from apps.costing.models import OverheadEntry
from apps.inventory.management.commands.seed_inventory import seed_inventory
from apps.masters.management.commands.seed_masters import seed_masters
from apps.production import services as prod
from apps.production.models import ProductionRun
from apps.production.tests import benchmark_purchase, benchmark_run
from apps.masters.models import Item, Sku

pytestmark = pytest.mark.django_db
user = lambda n: User.objects.get(username=n)  # noqa: E731
FG = lambda: Sku.objects.get(code="MUR200")  # noqa: E731
today = lambda: timezone.localdate()  # noqa: E731


@pytest.fixture
def seeded():
    seed_users()
    seed_masters()
    seed_inventory()


def benchmark_costed_run():
    benchmark_purchase()
    run = benchmark_run()
    services.add_overhead(user("production"), "labour", "1200", run=run, description="Labour for the run")
    services.add_overhead(user("production"), "power_gas", "600", run=run, description="Gas and power")
    return run


# ---------- benchmark step 8 ----------

def test_benchmark_cost_per_packet(seeded):
    run = benchmark_costed_run()
    rc = services.run_cost(run)
    assert rc.material == D("4055")                       # weighted-average materials, see production tests
    assert rc.direct == {"labour": D("1200"), "power_gas": D("600"), "other": D("0")}
    assert rc.total == D("5855") and rc.good == 188
    assert rc.per_packet == D("31.1436")                  # 5855 / 188
    assert rc.price == D("50.00") and rc.margin == D("18.8564") and rc.margin_pct == D("37.71")


def test_cost_per_packet_before_overheads_is_material_only(seeded):
    benchmark_purchase()
    run = benchmark_run()
    assert services.run_cost(run).per_packet == D("21.5691")  # 4055 / 188


def test_sku_summary_matches_single_run(seeded):
    benchmark_costed_run()
    s = services.sku_costs(today(), today())[0]
    assert (s.runs, s.good, s.total, s.per_packet, s.margin_pct) == (1, 188, D("5855"), D("31.1436"), D("37.71"))


def test_wastage_is_in_cost_and_valuation_method_changes_cost(seeded):
    """Damaged pouches (6 x 2.50 = 15) are included in the run cost, so they raise the cost per good packet."""
    run = benchmark_costed_run()
    pouches = D("194") * D("2.5")  # 188 used + 6 damaged
    assert pouches == D("485") and services.run_cost(run).material == D("1725") + D("1470") + D("240") + pouches + D("135")


# ---------- overhead rules ----------

def test_overhead_validation_and_voiding(seeded):
    run = benchmark_costed_run()
    with pytest.raises(services.CostingError):
        services.add_overhead(user("admin"), "labour", "0", run=run)
    entry = OverheadEntry.objects.get(kind="labour")
    with pytest.raises(services.CostingError):
        services.void_overhead(user("admin"), entry, " ")
    services.void_overhead(user("admin"), entry, "Entered twice")
    assert services.run_cost(run).total == D("4655")  # 4055 + 600
    assert OverheadEntry.objects.filter(kind="labour").exists()  # kept, not deleted
    with pytest.raises(services.CostingError):
        services.void_overhead(user("admin"), entry, "again")


def test_cannot_charge_a_voided_run(seeded):
    benchmark_purchase()
    run = benchmark_run()
    prod.void_run(run, "mistake", user("admin"))
    run.refresh_from_db()
    with pytest.raises(services.CostingError):
        services.add_overhead(user("admin"), "labour", "100", run=run)
    assert services.sku_costs(today(), today()) == []


def test_unfinished_run_has_no_cost_per_packet(seeded):
    run = prod.start_run(FG(), 1, user("production"))
    assert services.run_cost(run).per_packet is None
    assert services.sku_costs(today(), today()) == []


def test_allocation_adds_up_exactly():
    parts = services.allocate(D("1000.00"), [188, 100, 52])
    assert sum(parts) == D("1000.00") and parts == [D("552.94"), D("294.12"), D("152.94")]
    assert sum(services.allocate(D("100.01"), [1, 1, 1])) == D("100.01")
    assert services.allocate(D("50"), [0, 0]) == [D("0"), D("0")]


def test_monthly_pool_is_shared_by_good_packets(seeded):
    benchmark_purchase()
    first = benchmark_run()
    stock_in = prod.start_run(FG(), 1, user("production"))
    prod.issue_material(stock_in, Item.objects.get(code="FLOUR"), D("20"), user("production"))
    prod.complete_run(stock_in, 62, 0, {}, user("production"))  # 188 + 62 = 250 good packets this month
    services.add_overhead(user("admin"), "other", "1000", description="Rent share")  # monthly pool
    alloc, left = services.monthly_allocation(today().year, today().month)
    assert left == D("0") and alloc == {first.pk: D("752.00"), stock_in.pk: D("248.00")}
    assert services.run_cost(first).allocated == D("752.00")
    assert services.run_cost(first).total == D("4055") + D("752.00")


def test_pool_with_no_runs_is_reported_unallocated(seeded):
    services.add_overhead(user("admin"), "labour", "500")
    assert services.monthly_allocation(today().year, today().month) == ({}, D("500"))


def test_monthly_pool_uses_calendar_month_of_entry(seeded):
    benchmark_costed_run()
    last_month = today().replace(day=1) - timedelta(days=1)
    services.add_overhead(user("admin"), "other", "999", incurred_on=last_month)
    assert services.run_cost(ProductionRun.objects.get()).allocated == D("0")


# ---------- screens ----------

def test_costing_screen_and_export(client, seeded):
    benchmark_costed_run()
    client.login(username="owner", password="demo12345")
    page = client.get(reverse("costing")).content.decode()
    assert "31.14" in page and "37.71%" in page
    csv = client.get(reverse("costing"), {"export": "csv"}).content.decode()
    assert "RUN-0001" in csv and "31.1436" in csv


def test_add_and_void_through_screen_and_roles(client, seeded):
    benchmark_purchase()
    run = benchmark_run()
    client.login(username="production", password="demo12345")
    r = client.post(reverse("overhead_add"), {"kind": "labour", "amount": "1200", "run": run.pk, "incurred_on": today().isoformat()})
    assert r.status_code == 302 and OverheadEntry.objects.count() == 1
    r = client.post(reverse("overhead_add"), {"kind": "labour", "amount": "-5", "run": run.pk, "incurred_on": today().isoformat()})
    assert r.status_code == 200 and OverheadEntry.objects.count() == 1
    entry = OverheadEntry.objects.get()
    for name, status in {"accountant": 403, "owner": 403, "salesman_a": 403}.items():
        client.login(username=name, password="demo12345")
        assert client.post(reverse("overhead_add"), {"kind": "labour", "amount": "5"}).status_code == status, name
        assert client.post(reverse("overhead_void", args=[entry.pk]), {"reason": "x"}).status_code == status, name
    assert client.get(reverse("costing")).status_code == 403  # salesman
    client.login(username="accountant", password="demo12345")
    assert client.get(reverse("costing")).status_code == 200
