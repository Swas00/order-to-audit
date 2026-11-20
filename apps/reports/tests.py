"""Report tests. `run_full_benchmark` plays the problem statement's section 5 end to end through the
service layer and is reused by the final benchmark test."""

import uuid
from datetime import date, timedelta
from decimal import Decimal as D

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.accounts.management.commands.seed_users import seed_users
from apps.accounts.models import User
from apps.billing import services as billing
from apps.costing import services as costing
from apps.inventory import services as stock
from apps.inventory.management.commands.seed_inventory import seed_inventory
from apps.masters.management.commands.seed_masters import seed_masters
from apps.masters.models import Customer, Item, Sku, Unit
from apps.production import services as prod
from apps.reports import builders
from apps.sales import services as sales

pytestmark = pytest.mark.django_db
user = lambda n: User.objects.get(username=n)  # noqa: E731
cust = lambda n: Customer.objects.get(name=n)  # noqa: E731
item = lambda c: Item.objects.get(code=c)  # noqa: E731
FG = lambda: Sku.objects.get(code="MUR200")  # noqa: E731
today = lambda: timezone.localdate()  # noqa: E731


@pytest.fixture
def seeded():
    seed_users()
    seed_masters()
    seed_inventory()


def run_full_benchmark():
    """Section 5, steps 1-8 in order. Returns a dict of the objects created."""
    from apps.purchasing.models import Purchase, Supplier, post_purchase
    prodn, admin = user("production"), user("admin")
    post_purchase(Purchase(supplier=Supplier.objects.get(), bill_no="BM-1"),                       # 1 purchase
                  [dict(item=item("FLOUR"), qty=D("60"), unit=Unit.objects.get(code="kg"), rate=D("45"))], prodn)
    run = prod.start_run(FG(), 1, prodn)                                                           # 2 run + issues
    for code, qty in (("FLOUR", "40"), ("OIL", "10.5"), ("SPICE", "1.2")):
        prod.issue_material(run, item(code), D(qty), prodn)
    prod.complete_run(run, 188, 6, {item("CARTON20"): D("9")}, prodn)                              # 3 output
    order_a = sales.create_order(user("salesman_a"), cust("Retailer A"), {FG(): 150}, client_uuid=uuid.uuid4())  # 4
    sales.approve_order(order_a, admin)                                                            # 5
    inv_a = billing.raise_invoice(order_a, admin)
    billing.dispatch_invoice(inv_a, admin)
    order_b = sales.create_order(user("salesman_b"), cust("Distributor B"), {FG(): 120}, client_uuid=uuid.uuid4())  # 6
    cov_b = sales.coverage(order_b)
    billing.record_payment(cust("Retailer A"), "4000", admin, invoice=inv_a, mode="upi", reference="UTR-1")  # 7
    costing.add_overhead(prodn, "labour", "1200", run=run)                                         # 8 overheads
    costing.add_overhead(prodn, "power_gas", "600", run=run)
    run.refresh_from_db()
    return dict(run=run, order_a=order_a, inv_a=inv_a, order_b=order_b, cov_b=cov_b)


def table(tables, title_part):
    return next(t for t in tables if title_part in t.title)


# ---------- the month's reports after the benchmark ----------

def test_sales_register_benchmark(seeded):
    run_full_benchmark()
    t = builders.sales_register(date.today().replace(day=1), today())[0]
    assert len(t.rows) == 2  # the invoice and the NET TOTAL row
    row = t.rows[0]
    assert (row[2], row[3], row[6], row[7], row[8], row[9], row[11]) == (
        f"INV/{billing.tax.financial_year(today())}/0001", "Retailer A", D("7500.00"), D("187.50"), D("187.50"), D("0.00"), D("7875.00"))
    assert t.rows[-1][2] == "NET TOTAL" and t.rows[-1][11] == D("7875.00")


def test_customer_and_product_wise_sales(seeded):
    run_full_benchmark()
    c = builders.customer_sales(today().replace(day=1), today())[0].rows
    assert c == [("Retailer A", "Retailer", "33AAACR1111A1Z5", 1, D("7500.00"), D("375.00"), D("7875.00"))]
    p = builders.product_sales(today().replace(day=1), today())[0].rows
    assert p == [("Murukku 200 g", "19059030", 150, D("7500.00"), D("375.00"), D("7875.00"), D("50.00"))]


def test_gst_summary_shapes(seeded):
    run_full_benchmark()
    tables = builders.gst_summary(today().replace(day=1), today())
    g3b = table(tables, "GSTR-3B").rows[0]
    assert g3b == (D("7500.00"), D("0.00"), D("187.50"), D("187.50"), D("0.00"))  # taxable, IGST, CGST, SGST, cess
    b2b = table(tables, "B2B").rows
    assert len(b2b) == 1 and b2b[0][0] == "33AAACR1111A1Z5" and b2b[0][6] == D("5.00") and b2b[0][7] == D("7500.00")
    assert table(tables, "HSN").rows[0][:5] == ("19059030", "Murukku 200 g", "PKT", 150, D("5.00"))
    assert table(tables, "Rate-wise").rows[0][:2] == (D("5.00"), "Intra-state")
    assert table(tables, "B2C").rows == [] and table(tables, "Credit notes").rows == []


def test_gst_summary_with_igst_b2c_and_credit_note(seeded):
    run_full_benchmark()
    admin = user("admin")
    b = sales.create_order(user("salesman_b"), cust("Distributor B"), {FG(): 88}, client_uuid=uuid.uuid4())
    sales.approve_order(b, admin)
    inv_b = billing.raise_invoice(b, admin)
    Customer.objects.filter(name="Retailer A").update(gstin="")  # not used by old invoice (snapshot)
    billing.cancel_invoice(inv_b, admin, "Order withdrawn")
    tables = builders.gst_summary(today().replace(day=1), today())
    assert table(tables, "GSTR-3B").rows[0] == (D("7500.00"), D("0.00"), D("187.50"), D("187.50"), D("0.00"))  # B's invoice nets to zero
    assert len(table(tables, "B2B").rows) == 2 and len(table(tables, "Credit notes").rows) == 1
    cdn = table(tables, "Credit notes").rows[0]
    assert cdn[7] == D("-4400.00") and cdn[8] == D("-220.00")  # taxable, IGST negative
    reg = builders.sales_register(today().replace(day=1), today())[0]
    assert [r[1] for r in reg.rows[:-1]] == ["Invoice", "Invoice", "Credit note"] and reg.rows[-1][11] == D("7875.00")


def test_stock_reports_after_benchmark(seeded):
    run_full_benchmark()
    t = builders.stock_summary(today().replace(day=1), today())
    raw = {r[0]: r for r in table(t, "Raw").rows}
    assert raw["Flour (kg)"][1:6] == (D("100"), D("60"), D("40"), D("0"), D("120"))
    assert raw["Cooking oil (L)"][5] == D("19.5")
    pack = {r[0]: r for r in table(t, "Packaging").rows}
    assert pack["200 g pouch (piece)"][5] == D("306")
    fg = table(t, "Finished").rows[0]
    assert fg[1:6] == (D("50"), D("188"), D("150"), D("0"), D("88"))
    for tbl in t:  # the stock equation holds on every row
        for r in tbl.rows:
            assert r[1] + r[2] - r[3] + r[4] == r[5]


def test_production_report(seeded):
    run_full_benchmark()
    t = builders.production_report(today().replace(day=1), today())
    lines = {r[3]: r for r in table(t, "consumption").rows}
    assert lines["Cooking oil"][5:10] == (D("10"), D("10.5"), D("0.5"), D("5.00"), "FLAG")
    assert lines["Flour"][9] == "OK" and lines["Carton (holds 20 packets)"][9] == "NOTE"
    y = table(t, "Yield").rows[0]
    assert y[3:9] == (D("200"), 188, D("-6.00"), "FLAG", 6, D("12"))


def test_collections_and_outstanding(seeded):
    run_full_benchmark()
    t = builders.collections(today().replace(day=1), today())
    assert t[0].rows == [("Retailer A", 1, D("4000.00"))]
    assert t[1].rows[0][2:5] == (D("4000.00"), "UPI", "UTR-1")
    o = builders.outstanding(today().replace(day=1), today())
    assert o[0].rows == [("Retailer A", D("3875.00"))]
    assert o[1].rows[0][1:] == (D("3875.00"), D("0.00"), D("0.00"), D("0.00"), D("3875.00"))
    assert o[2].rows[0][-1] == D("3875.00")


def test_purchase_register_report(seeded):
    run_full_benchmark()
    r = builders.purchase_register(today().replace(day=1), today())[0].rows
    assert len(r) == 1 and r[0][3:8] == ("Flour", D("60"), "kg", D("45"), D("2700.00"))


# ---------- screens and exports ----------

def test_every_report_renders_and_exports_in_every_format(client, seeded):
    run_full_benchmark()
    client.login(username="admin", password="demo12345")
    for slug in ["sales-register", "customer-sales", "product-sales", "gst-summary", "purchase-register", "stock-summary",
                 "stock-ledger", "production", "collections", "outstanding"]:
        assert client.get(reverse("report", args=[slug])).status_code == 200, slug
        csv = client.get(reverse("report", args=[slug]), {"export": "csv"})
        assert csv.status_code == 200 and csv["Content-Type"].startswith("text/csv"), slug
        assert client.get(reverse("report", args=[slug]), {"export": "xlsx"}).content[:2] == b"PK", slug
        assert client.get(reverse("report", args=[slug]), {"export": "pdf"}).content.startswith(b"%PDF"), slug


def test_date_range_filter_excludes_other_periods(client, seeded):
    run_full_benchmark()
    client.login(username="accountant", password="demo12345")
    old = client.get(reverse("report", args=["sales-register"]), {"from": "2020-01-01", "to": "2020-01-31"}).content.decode()
    assert "INV/" not in old
    cur = client.get(reverse("report", args=["sales-register"]), {"export": "csv"}).content.decode()
    assert "7875" in cur and "Retailer A" in cur


def test_report_access_by_role(client, seeded):
    allowed = {"sales-register": {"admin", "accountant", "owner"}, "gst-summary": {"admin", "accountant", "owner"},
               "stock-summary": {"admin", "accountant", "owner", "production"}, "production": {"admin", "accountant", "owner", "production"}}
    for slug, roles in allowed.items():
        for name in ["admin", "accountant", "owner", "production", "salesman_a"]:
            client.login(username=name, password="demo12345")
            expected = 200 if name in roles else 403
            assert client.get(reverse("report", args=[slug])).status_code == expected, (slug, name)
            client.logout()
    client.login(username="admin", password="demo12345")
    assert client.get(reverse("report", args=["nope"])).status_code == 404


# ---------- owner dashboard ----------

def test_owner_dashboard_shows_the_benchmark_story(client, seeded):
    run_full_benchmark()
    client.login(username="owner", password="demo12345")
    assert client.get("/").status_code == 302 and client.get("/")["Location"] == reverse("dashboard")
    page = client.get(reverse("dashboard")).content.decode()
    assert "Yield -6.00% (188 vs 200)" in page and "Cooking oil +5.00%" in page   # variance alerts
    assert "3875.00" in page                                                       # outstanding dues
    assert "31.14" in page                                                         # cost per packet
    assert "awaiting approval" in page and "Murukku 200 g" in page


def test_dashboard_low_stock_alert(client, seeded):
    Item.objects.filter(code="OIL").update(reorder_level=D("40"))
    client.login(username="owner", password="demo12345")
    assert "Cooking oil is at or below its reorder level" in client.get(reverse("dashboard")).content.decode()


def test_dashboard_access(client, seeded):
    for name, status in {"owner": 200, "admin": 200, "accountant": 403, "production": 403, "salesman_a": 403}.items():
        client.login(username=name, password="demo12345")
        assert client.get(reverse("dashboard")).status_code == status, name
        client.logout()
