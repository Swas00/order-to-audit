import uuid
from datetime import date, timedelta
from decimal import Decimal as D

import pytest
from django.db import IntegrityError, transaction
from django.urls import reverse

from apps.accounts.management.commands.seed_users import seed_users
from apps.accounts.models import User
from apps.billing import services, tax
from apps.billing.models import Dispatch, Invoice, InvoiceStatus
from apps.billing.pdf import credit_note_pdf, invoice_pdf
from apps.inventory import services as stock
from apps.inventory.management.commands.seed_inventory import seed_inventory
from apps.inventory.models import StockMovement
from apps.masters.management.commands.seed_masters import seed_masters
from apps.masters.models import Customer, Sku
from apps.production.tests import benchmark_purchase, benchmark_run
from apps.sales import services as sales
from apps.sales.models import OrderStatus

pytestmark = pytest.mark.django_db


@pytest.fixture
def seeded():
    seed_users()
    seed_masters()
    seed_inventory()


FG = lambda: Sku.objects.get(code="MUR200")  # noqa: E731
user = lambda n: User.objects.get(username=n)  # noqa: E731
cust = lambda n: Customer.objects.get(name=n)  # noqa: E731


def approved_order(customer="Retailer A", salesman="salesman_a", qty=150, cap=False):
    o = sales.create_order(user(salesman), cust(customer), {FG(): qty}, client_uuid=uuid.uuid4())
    sales.approve_order(o, user("admin"), cap_to_available=cap)
    o.refresh_from_db()
    return o


def retailer_a_invoice():
    benchmark_purchase()
    benchmark_run()
    return services.raise_invoice(approved_order(), user("admin"))


# ---------- tax maths (pure) ----------

def test_benchmark_intra_state_tax():
    t = tax.line_tax(150, "50", "5", intra_state=True)
    assert (t.taxable, t.cgst, t.sgst, t.igst, t.total) == (D("7500.00"), D("187.50"), D("187.50"), D("0.00"), D("7875.00"))


def test_inter_state_charges_igst():
    t = tax.line_tax(120, "50", "5", intra_state=False)
    assert (t.taxable, t.cgst, t.sgst, t.igst) == (D("6000.00"), D("0.00"), D("0.00"), D("300.00"))


def test_rounding_is_half_up_to_paisa_and_exact():
    t = tax.line_tax(3, "33.33", "5", True)  # taxable 99.99, half-tax 2.49975 -> 2.50
    assert t.taxable == D("99.99") and t.cgst == t.sgst == D("2.50")
    assert tax.line_tax(1, "0.10", "5", True).cgst == D("0.00")  # 0.0025 -> 0.00
    assert tax.line_tax(1, "0.30", "18", False).igst == D("0.05")  # 0.054 -> 0.05
    assert tax.line_tax(7, "0.1", "0", True).taxable == D("0.70")  # no 0.7000000000000001


def test_round_to_rupee():
    assert tax.round_to_rupee(D("7875.00")) == (D("7875.00"), D("0.00"))
    assert tax.round_to_rupee(D("100.50")) == (D("101.00"), D("0.50"))
    assert tax.round_to_rupee(D("100.49")) == (D("100.00"), D("-0.49"))


def test_financial_year_boundaries():
    assert tax.financial_year(date(2026, 3, 31)) == "2025-26"
    assert tax.financial_year(date(2026, 4, 1)) == "2026-27"
    assert tax.financial_year(date(2026, 11, 20)) == "2026-27"
    assert tax.financial_year(date(2027, 1, 1)) == "2026-27"


def test_amount_in_words():
    assert tax.amount_in_words(D("7875")) == "Rupees Seven Thousand Eight Hundred Seventy Five Only"
    assert tax.amount_in_words(D("125000.50")) == "Rupees One Lakh Twenty Five Thousand and Fifty Paise Only"
    assert tax.amount_in_words(D("0")) == "Rupees Zero Only"
    assert tax.amount_in_words(D("10000000")) == "Rupees One Crore Only"


# ---------- invoice (benchmark step 5) ----------

def test_benchmark_retailer_a_invoice(seeded):
    inv = retailer_a_invoice()
    assert (inv.taxable_total, inv.cgst_total, inv.sgst_total, inv.igst_total, inv.grand_total) == \
        (D("7500.00"), D("187.50"), D("187.50"), D("0.00"), D("7875.00"))
    assert inv.intra_state and inv.number == f"INV/{tax.financial_year(inv.invoice_date)}/0001"
    line = inv.lines.get()
    assert line.hsn_code == "19059030" and line.qty == 150 and line.gst_rate == D("5.00")
    assert inv.buyer_gstin == "33AAACR1111A1Z5" and inv.supplier_gstin == "33AABCD1234E1Z5"
    inv.order.refresh_from_db()
    assert inv.order.status == OrderStatus.INVOICED


def test_invoice_for_karnataka_uses_igst(seeded):
    benchmark_purchase()
    benchmark_run()
    inv = services.raise_invoice(approved_order("Distributor B", "salesman_b", 88, cap=True), user("admin"))
    assert not inv.intra_state and inv.igst_total == D("220.00") and inv.cgst_total == inv.sgst_total == D("0.00")
    assert inv.taxable_total == D("4400.00") and inv.grand_total == D("4620.00")


def test_invoice_numbers_sequential_and_restart_each_fy(seeded):
    benchmark_purchase()
    benchmark_run()
    a = services.raise_invoice(approved_order(qty=10), user("admin"), invoice_date=date(2027, 3, 31))
    b = services.raise_invoice(approved_order(qty=10), user("admin"), invoice_date=date(2027, 3, 31))
    c = services.raise_invoice(approved_order(qty=10), user("admin"), invoice_date=date(2027, 4, 1))
    assert (a.number, b.number, c.number) == ("INV/2026-27/0001", "INV/2026-27/0002", "INV/2027-28/0001")


def test_only_approved_orders_can_be_invoiced_once(seeded):
    benchmark_purchase()
    benchmark_run()
    pending = sales.create_order(user("salesman_a"), cust("Retailer A"), {FG(): 5}, client_uuid=uuid.uuid4())
    with pytest.raises(services.BillingError):
        services.raise_invoice(pending, user("admin"))
    o = approved_order(qty=5)
    services.raise_invoice(o, user("admin"))
    with pytest.raises(services.BillingError):
        services.raise_invoice(o, user("admin"))
    assert Invoice.objects.count() == 1


def test_invoice_keeps_snapshot_when_master_data_changes(seeded):
    inv = retailer_a_invoice()
    Customer.objects.filter(name="Retailer A").update(name="Renamed", gstin="")
    Sku.objects.update(selling_price=D("99"), gst_rate=D("18"))
    inv.refresh_from_db()
    assert inv.buyer_name == "Retailer A" and inv.lines.get().rate == D("50.00") and inv.grand_total == D("7875.00")


def test_one_issued_invoice_per_order_enforced_by_database(seeded):
    inv = retailer_a_invoice()
    with pytest.raises(IntegrityError), transaction.atomic():
        Invoice.objects.create(**{**{f.name: getattr(inv, f.name) for f in Invoice._meta.concrete_fields
                                     if f.name not in ("id", "number", "share_token")}, "number": "X", "share_token": "t"})


# ---------- dispatch (benchmark step 5) ----------

def test_dispatch_reduces_finished_goods(seeded):
    inv = retailer_a_invoice()
    assert stock.balance(FG()) == D("238")
    services.dispatch_invoice(inv, user("admin"), "TN-45-AB-1234")
    assert stock.balance(FG()) == D("88")  # benchmark check
    inv.order.refresh_from_db()
    assert inv.order.status == OrderStatus.DISPATCHED
    moves = StockMovement.objects.filter(sku=FG(), movement_type="dispatch").order_by("id")
    # first-expiry-first-out: the 50 packets of opening stock go first, then 100 from the new batch
    assert [(m.batch.number, m.qty) for m in moves] == [("OPEN-MUR200", D("-50")), (moves[1].batch.number, D("-100"))]
    assert all(isinstance(m.source, Dispatch) and m.source.invoice == inv for m in moves)


def test_cannot_dispatch_twice_or_without_stock(seeded):
    inv = retailer_a_invoice()
    services.dispatch_invoice(inv, user("admin"))
    with pytest.raises(services.BillingError):
        services.dispatch_invoice(inv, user("admin"))
    o = approved_order("Distributor B", "salesman_b", 88, cap=True)
    inv_b = services.raise_invoice(o, user("admin"))
    stock.adjust(FG(), D("-80"), "Damaged in store", user("admin"))  # only 8 left
    with pytest.raises(stock.InsufficientStock):
        services.dispatch_invoice(inv_b, user("admin"))
    assert not Dispatch.objects.filter(invoice=inv_b).exists() and stock.balance(FG()) == D("8")


# ---------- payments (benchmark step 7) ----------

def test_benchmark_payment_and_outstanding(seeded):
    inv = retailer_a_invoice()
    services.dispatch_invoice(inv, user("admin"))
    services.record_payment(cust("Retailer A"), "4000", user("admin"), invoice=inv, mode="upi", reference="UTR1")
    inv.refresh_from_db()
    assert inv.paid == D("4000.00") and inv.outstanding == D("3875.00")
    assert services.customer_balance(cust("Retailer A")) == D("3875.00")  # benchmark check


def test_payment_rules(seeded):
    inv = retailer_a_invoice()
    a = cust("Retailer A")
    with pytest.raises(services.BillingError):
        services.record_payment(a, "7875.01", user("admin"), invoice=inv)
    with pytest.raises(services.BillingError):
        services.record_payment(a, "0", user("admin"))
    with pytest.raises(services.BillingError):
        services.record_payment(cust("Distributor B"), "10", user("admin"), invoice=inv)  # someone else's invoice
    services.record_payment(a, "7875", user("admin"), invoice=inv)
    assert inv.outstanding == D("0.00") and services.customer_balance(a) == D("0.00")


def test_payment_without_invoice_clears_oldest_first_and_keeps_advance(seeded):
    benchmark_purchase()
    benchmark_run()
    i1 = services.raise_invoice(approved_order(qty=10), user("admin"), invoice_date=date.today() - timedelta(days=40))
    i2 = services.raise_invoice(approved_order(qty=10), user("admin"))
    a = cust("Retailer A")  # each invoice: 500 + 25 = 525
    services.record_payment(a, "600", user("admin"))
    assert i1.outstanding == D("0.00") and i2.outstanding == D("450.00")
    services.record_payment(a, "1000", user("admin"))  # clears i2 (450), 550 stays as advance
    assert i2.outstanding == D("0.00") and services.customer_balance(a) == D("-550.00")


def test_ageing_buckets(seeded):
    benchmark_purchase()
    benchmark_run()
    services.raise_invoice(approved_order(qty=10), user("admin"), invoice_date=date.today() - timedelta(days=40))
    services.raise_invoice(approved_order(qty=20), user("admin"), invoice_date=date.today() - timedelta(days=100))
    services.raise_invoice(approved_order(qty=30), user("admin"))
    row = services.ageing()[0]
    assert row["cells"] == [D("1575.00"), D("525.00"), D("0.00"), D("1050.00")] and row["total"] == D("3150.00")


# ---------- cancellation by credit note ----------

def test_cancel_undispatched_invoice(seeded):
    inv = retailer_a_invoice()
    cn = services.cancel_invoice(inv, user("admin"), "Wrong GSTIN on invoice")
    inv.refresh_from_db()
    assert inv.status == InvoiceStatus.CANCELLED and Invoice.objects.filter(pk=inv.pk).exists()  # kept, not deleted
    assert cn.grand_total == D("7875.00") and cn.number.startswith("CN/") and not cn.goods_returned
    assert stock.balance(FG()) == D("238") and inv.outstanding == D("0.00")
    inv.order.refresh_from_db()
    assert inv.order.status == OrderStatus.APPROVED
    again = services.raise_invoice(inv.order, user("admin"))  # corrected invoice for the same order
    assert again.number.endswith("0002")


def test_cancel_dispatched_invoice_returns_goods_at_original_cost(seeded):
    inv = retailer_a_invoice()
    services.dispatch_invoice(inv, user("admin"))
    out = list(StockMovement.objects.filter(sku=FG(), movement_type="dispatch").order_by("id"))
    services.cancel_invoice(inv, user("admin"), "Customer refused delivery")
    assert stock.balance(FG()) == D("238")
    back = list(StockMovement.objects.filter(sku=FG(), movement_type="return").order_by("id"))
    # each batch gets its own packets back, at the cost they left at
    assert [(b.batch, b.qty, b.unit_cost) for b in back] == [(o.batch, -o.qty, o.unit_cost) for o in out]
    assert all(b.source.invoice == inv for b in back)
    assert {stock.batch_balance(o.batch) for o in out} == {D("50"), D("188")}


def test_cancel_rules(seeded):
    inv = retailer_a_invoice()
    with pytest.raises(services.BillingError):
        services.cancel_invoice(inv, user("admin"), " ")
    with pytest.raises(services.BillingError):
        services.cancel_invoice(inv, user("admin"), "x", goods_returned=True)  # never dispatched
    services.cancel_invoice(inv, user("admin"), "ok")
    with pytest.raises(services.BillingError):
        services.cancel_invoice(inv, user("admin"), "again")
    with pytest.raises(services.BillingError):
        services.dispatch_invoice(inv, user("admin"))
    with pytest.raises(services.BillingError):
        services.record_payment(cust("Retailer A"), "10", user("admin"), invoice=inv)


def test_payment_before_cancel_leaves_customer_in_credit(seeded):
    inv = retailer_a_invoice()
    services.record_payment(cust("Retailer A"), "4000", user("admin"), invoice=inv)
    services.cancel_invoice(inv, user("admin"), "Cancelled")
    assert services.customer_balance(cust("Retailer A")) == D("-4000.00")


# ---------- PDF ----------

def test_invoice_pdf_is_a_valid_pdf(seeded):
    inv = retailer_a_invoice()
    data = invoice_pdf(inv)
    assert data.startswith(b"%PDF") and len(data) > 1500
    services.cancel_invoice(inv, user("admin"), "x")
    assert credit_note_pdf(inv.credit_note).startswith(b"%PDF")


# ---------- screens and access ----------

def test_admin_raises_dispatches_and_pays_via_screens(client, seeded):
    benchmark_purchase()
    benchmark_run()
    o = approved_order()
    client.login(username="admin", password="demo12345")
    r = client.post(reverse("invoice_raise", args=[o.pk]))
    inv = Invoice.objects.get()
    assert r["Location"] == reverse("invoice_detail", args=[inv.pk])
    assert client.post(reverse("invoice_dispatch", args=[inv.pk]), {"vehicle_ref": "V1"}).status_code == 302
    assert stock.balance(FG()) == D("88")
    client.post(reverse("payment_new"), {"customer": cust("Retailer A").pk, "invoice": inv.pk, "amount": "4000", "mode": "upi"})
    page = client.get(reverse("invoice_detail", args=[inv.pk])).content.decode()
    assert "outstanding ₹3875.00" in page and "wa.me" in page
    assert "3875.00" in client.get(reverse("receivables")).content.decode()
    pdf = client.get(reverse("invoice_pdf", args=[inv.pk]))
    assert pdf["Content-Type"] == "application/pdf" and pdf.content.startswith(b"%PDF")


def test_shared_pdf_link_needs_the_token_not_a_login(client, seeded):
    inv = retailer_a_invoice()
    assert client.get(f"/i/{inv.share_token}.pdf").content.startswith(b"%PDF")
    assert client.get("/i/not-a-real-token.pdf").status_code == 404
    assert client.get(reverse("invoice_pdf", args=[inv.pk])).status_code == 302  # internal link still needs login


def test_role_access_to_billing(client, seeded):
    inv = retailer_a_invoice()
    for name, can_view in {"admin": 200, "accountant": 200, "owner": 200, "production": 403, "salesman_a": 403}.items():
        client.login(username=name, password="demo12345")
        assert client.get(reverse("invoice_detail", args=[inv.pk])).status_code == can_view, name
        assert client.get(reverse("receivables")).status_code == can_view, name
        client.logout()
    for name in ["accountant", "owner", "production", "salesman_a"]:  # read-only roles cannot act
        client.login(username=name, password="demo12345")
        for url in (reverse("invoice_dispatch", args=[inv.pk]), reverse("invoice_cancel", args=[inv.pk]),
                    reverse("invoice_raise", args=[inv.order.pk])):
            assert client.post(url, {"reason": "x"}).status_code == 403, (name, url)
        assert client.get(reverse("payment_new")).status_code == 403, name
        client.logout()
    assert Invoice.objects.get().status == InvoiceStatus.ISSUED


def test_salesman_sees_invoiced_status_but_not_billing(client, seeded):
    inv = retailer_a_invoice()
    client.login(username="salesman_a", password="demo12345")
    page = client.get(reverse("order_detail", args=[inv.order.pk])).content.decode()
    assert "Invoiced" in page and inv.number in page and reverse("invoice_detail", args=[inv.pk]) not in page


def test_money_sums_do_not_drift(seeded):
    """Payments in awkward paise amounts must add up exactly (no float residue), including on SQLite."""
    inv = retailer_a_invoice()
    for amt in ["0.10", "0.20", "33.33", "33.33", "33.34"]:
        services.record_payment(cust("Retailer A"), amt, user("admin"), invoice=inv)
    assert inv.paid == D("100.30") and str(inv.paid) == "100.30"
    assert services.customer_balance(cust("Retailer A")) == D("7774.70") and str(services.customer_balance(cust("Retailer A"))) == "7774.70"
    assert str(inv.outstanding) == "7774.70"
