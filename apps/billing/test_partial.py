"""Partial dispatch (one invoice shipped in several lots) and partial returns (credit notes for some packets)."""

import xml.etree.ElementTree as ET
from datetime import date
from decimal import Decimal as D

import pytest
from django.urls import reverse

from apps.billing import services, tally
from apps.billing.models import CreditNote, Dispatch, Invoice, InvoiceStatus, Payment
from apps.billing.pdf import credit_note_pdf
from apps.billing.tests import FG, approved_order, cust, retailer_a_invoice, seeded, user  # noqa: F401
from apps.inventory import services as stock
from apps.inventory.models import Batch, StockMovement
from apps.reports import builders
from apps.sales import services as sales
from apps.sales.models import OrderStatus

pytestmark = pytest.mark.django_db
admin = lambda: user("admin")  # noqa: E731


def pdf_text(data: bytes) -> str:
    """Text drawn on a ReportLab PDF (streams are ASCII85 + Flate)."""
    import base64, re, zlib
    out = ""
    for m in re.finditer(rb"stream\r?\n(.*?)endstream", data, re.S):
        try:
            out += zlib.decompress(base64.a85decode(m.group(1).strip(), adobe=True)).decode("latin-1")
        except Exception:
            pass
    return "".join(re.findall(r"\(([^)]*)\)", out)) if out else ""


def line(inv):
    return inv.lines.get()


def batch_qty(number):
    return stock.batch_balance(Batch.objects.get(number=number))


def new_batch_number():
    return Batch.objects.exclude(number="OPEN-MUR200").get().number


# ---------- partial dispatch ----------

def test_invoice_ships_in_two_lots_and_stock_follows(seeded):
    inv = retailer_a_invoice()
    l = line(inv)
    services.dispatch_invoice(inv, admin(), "LOT-1", {l.pk: 60})
    inv = Invoice.objects.get(pk=inv.pk)
    assert stock.balance(FG()) == D("178") and inv.dispatch_state == "partial"
    assert inv.order.status == OrderStatus.INVOICED          # not 'dispatched' until everything has gone
    assert (l.dispatched_qty, l.remaining_to_dispatch) == (60, 90)
    first = [(m.batch.number, m.qty) for m in StockMovement.objects.filter(sku=FG(), movement_type="dispatch").order_by("id")]
    assert first == [("OPEN-MUR200", D("-50")), (new_batch_number(), D("-10"))]   # first-expiry-first-out inside the lot

    services.dispatch_invoice(inv, admin(), "LOT-2")          # no quantities: everything still to go
    inv = Invoice.objects.get(pk=inv.pk)
    assert stock.balance(FG()) == D("88")                      # same end state as the benchmark's single dispatch
    assert inv.dispatch_state == "full" and inv.order.status == OrderStatus.DISPATCHED
    assert inv.dispatches.count() == 2 and l.dispatched_qty == 150 and inv.dispatch.vehicle_ref == "LOT-2"


def test_every_lot_movement_names_its_own_dispatch(seeded):
    inv = retailer_a_invoice()
    l = line(inv)
    services.dispatch_invoice(inv, admin(), "", {l.pk: 40})
    services.dispatch_invoice(inv, admin(), "", {l.pk: 110})
    d1, d2 = inv.dispatches.order_by("id")
    for d, total in ((d1, 40), (d2, 110)):
        moves = StockMovement.objects.filter(source_type__model="dispatch", source_id=d.pk)
        assert -sum(m.qty for m in moves) == total and d.lines.get().qty == total


@pytest.mark.parametrize("bad", [151, 0, "", "abc", -5, "1.5"])
def test_bad_lot_quantities_are_refused_and_post_nothing(seeded, bad):
    inv = retailer_a_invoice()
    with pytest.raises(services.BillingError):
        services.dispatch_invoice(inv, admin(), "", {line(inv).pk: bad})
    assert stock.balance(FG()) == D("238") and not Dispatch.objects.exists()


def test_lot_from_another_invoice_line_is_refused(seeded):
    inv = retailer_a_invoice()
    with pytest.raises(services.BillingError):
        services.dispatch_invoice(inv, admin(), "", {999999: 5})


def test_cannot_dispatch_more_than_invoiced_across_lots(seeded):
    inv = retailer_a_invoice()
    l = line(inv)
    services.dispatch_invoice(inv, admin(), "", {l.pk: 100})
    with pytest.raises(services.BillingError, match="only 50"):
        services.dispatch_invoice(inv, admin(), "", {l.pk: 51})
    services.dispatch_invoice(inv, admin(), "", {l.pk: 50})
    with pytest.raises(services.BillingError, match="in full"):
        services.dispatch_invoice(inv, admin())


def test_lot_is_all_or_nothing_when_stock_is_short(seeded):
    inv = retailer_a_invoice()
    l = line(inv)
    stock.adjust(FG(), D("-200"), "Damaged in store", admin())   # 38 left
    with pytest.raises(stock.InsufficientStock):
        services.dispatch_invoice(inv, admin(), "", {l.pk: 60})
    assert stock.balance(FG()) == D("38") and not Dispatch.objects.exists()
    services.dispatch_invoice(inv, admin(), "", {l.pk: 38})        # what exists can still go
    assert stock.balance(FG()) == D("0")


def test_approval_does_not_count_packets_that_already_left(seeded):
    """Part-dispatched packets are out of stock; they must not also be counted as 'promised', or stock is double-counted."""
    inv = retailer_a_invoice()
    services.dispatch_invoice(inv, admin(), "", {line(inv).pk: 60})
    assert sales.committed_qty(FG()) == 90 and stock.balance(FG()) == D("178")
    o = sales.create_order(user("salesman_b"), cust("Distributor B"), {FG(): 88}, client_uuid=__import__("uuid").uuid4())
    cov = sales.coverage(o)[0]
    assert (cov.in_stock, cov.committed, cov.available, cov.shortage) == (D("178"), 90, D("88"), 0)


# ---------- partial returns and credit ----------

def dispatched_invoice():
    inv = retailer_a_invoice()
    services.dispatch_invoice(inv, admin())
    return Invoice.objects.get(pk=inv.pk)


def test_partial_return_restocks_credits_and_leaves_invoice_open(seeded):
    inv = dispatched_invoice()
    l = line(inv)
    cn = services.issue_credit_note(inv, admin(), "20 packets damaged in transit", {l.pk: 20}, True)
    inv.refresh_from_db()
    assert (cn.taxable_total, cn.cgst_total, cn.sgst_total, cn.grand_total, cn.round_off) == (
        D("1000.00"), D("25.00"), D("25.00"), D("1050.00"), D("0.00"))
    assert stock.balance(FG()) == D("108") and cn.goods_returned
    back = StockMovement.objects.get(movement_type="return")
    assert back.batch.number == "OPEN-MUR200" and back.qty == D("20") and back.source == cn   # to the batch it left from
    assert inv.status == InvoiceStatus.ISSUED and inv.order.status == OrderStatus.DISPATCHED
    assert inv.outstanding == D("6825.00") and services.customer_balance(cust("Retailer A")) == D("6825.00")
    assert (l.credited_qty, l.returned_qty, l.creditable_qty, l.remaining_to_dispatch) == (20, 20, 130, 0)


def test_returns_then_final_credit_add_up_to_the_invoice_exactly(seeded):
    inv = dispatched_invoice()
    l = line(inv)
    notes = [services.issue_credit_note(inv, admin(), f"return {q}", {l.pk: q}, True) for q in (37, 41, 34)]
    notes.append(services.issue_credit_note(inv, admin(), "the rest", None, True))      # 150 - 112 = 38
    inv.refresh_from_db()
    assert [n.lines.get().qty for n in notes] == [37, 41, 34, 38]
    for f in ("taxable_total", "cgst_total", "sgst_total", "igst_total", "grand_total", "round_off"):
        assert sum(getattr(n, f) for n in notes) == getattr(inv, f), f
    assert inv.status == InvoiceStatus.CANCELLED and inv.order.status == OrderStatus.APPROVED
    assert stock.balance(FG()) == D("238")
    assert batch_qty("OPEN-MUR200") == D("50") and batch_qty(new_batch_number()) == D("188")   # every packet found its batch
    with pytest.raises(services.BillingError, match="already cancelled"):
        services.issue_credit_note(inv, admin(), "again", {l.pk: 1}, True)


def test_credit_without_returning_goods_leaves_stock_alone(seeded):
    inv = dispatched_invoice()
    cn = services.issue_credit_note(inv, admin(), "Price dispute", {line(inv).pk: 10}, False)
    assert stock.balance(FG()) == D("88") and not cn.goods_returned and cn.grand_total == D("525.00")
    assert not StockMovement.objects.filter(movement_type="return").exists()


def test_credit_before_dispatch_takes_nothing_from_stock_and_shrinks_what_is_left_to_send(seeded):
    inv = retailer_a_invoice()
    l = line(inv)
    cn = services.issue_credit_note(inv, admin(), "Customer reduced the order", {l.pk: 30})   # goods_returned defaults to False
    assert not cn.goods_returned and stock.balance(FG()) == D("238")
    assert l.remaining_to_dispatch == 120
    services.dispatch_invoice(inv, admin())
    assert stock.balance(FG()) == D("118") and Invoice.objects.get(pk=inv.pk).dispatch_state == "full"


def test_return_asked_for_but_nothing_dispatched_is_refused(seeded):
    inv = retailer_a_invoice()
    with pytest.raises(services.BillingError, match="Nothing was dispatched"):
        services.issue_credit_note(inv, admin(), "x", {line(inv).pk: 5}, True)
    assert not CreditNote.objects.exists()


@pytest.mark.parametrize("qty", [0, 151, -1, "x", ""])
def test_bad_credit_quantities_refused(seeded, qty):
    inv = dispatched_invoice()
    with pytest.raises(services.BillingError):
        services.issue_credit_note(inv, admin(), "x", {line(inv).pk: qty}, True)
    assert stock.balance(FG()) == D("88") and not CreditNote.objects.exists()


def test_cannot_credit_more_than_invoiced_across_notes(seeded):
    inv = dispatched_invoice()
    l = line(inv)
    services.issue_credit_note(inv, admin(), "a", {l.pk: 100}, True)
    with pytest.raises(services.BillingError, match="only 50"):
        services.issue_credit_note(inv, admin(), "b", {l.pk: 51}, True)
    with pytest.raises(services.BillingError, match="reason"):
        services.issue_credit_note(inv, admin(), " ", {l.pk: 1}, True)


def test_cancel_after_partial_dispatch_returns_only_what_left(seeded):
    inv = retailer_a_invoice()
    services.dispatch_invoice(inv, admin(), "", {line(inv).pk: 60})
    cn = services.cancel_invoice(inv, admin(), "Customer cancelled")
    inv.refresh_from_db()
    assert stock.balance(FG()) == D("238") and cn.lines.get().qty == 150 and cn.lines.get().qty_returned == 60
    assert cn.grand_total == D("7875.00") and inv.status == InvoiceStatus.CANCELLED


def test_cancel_after_a_partial_return_credits_only_the_rest(seeded):
    inv = dispatched_invoice()
    first = services.issue_credit_note(inv, admin(), "20 back", {line(inv).pk: 20}, True)
    last = services.cancel_invoice(inv, admin(), "Rest cancelled")
    inv.refresh_from_db()
    assert last.lines.get().qty == 130 and first.grand_total + last.grand_total == inv.grand_total == D("7875.00")
    assert stock.balance(FG()) == D("238") and inv.status == InvoiceStatus.CANCELLED


def test_payment_beyond_what_is_owed_after_credit_is_refused(seeded):
    inv = dispatched_invoice()
    services.issue_credit_note(inv, admin(), "a", {line(inv).pk: 20}, True)         # owes 6825
    with pytest.raises(services.BillingError, match="outstanding"):
        services.record_payment(cust("Retailer A"), "6826", admin(), invoice=inv)
    services.record_payment(cust("Retailer A"), "6825", admin(), invoice=inv)
    assert Invoice.objects.get(pk=inv.pk).outstanding == D("0.00")


# ---------- reports, exports, trace ----------

def test_sales_register_and_gst_summary_net_a_partial_credit(seeded):
    inv = dispatched_invoice()
    cn = services.issue_credit_note(inv, admin(), "20 damaged", {line(inv).pk: 20}, True)
    today = date.today()
    table = builders.sales_register(today, today)[0]
    kinds = [r[1] for r in table.rows[:-1]]
    assert kinds == ["Invoice", "Credit note"]
    net = table.rows[-1]
    assert net[6] == D("6500.00") and net[11] == D("6825.00")            # taxable 7500 - 1000, total 7875 - 1050
    assert [r for r in table.rows if r[1] == "Credit note"][0][11] == -cn.grand_total
    prod = builders.product_sales(today, today)[0].rows[0]
    assert prod[2] == 130                                                # packets net of the 20 credited
    assert builders.gst_summary(today, today)                            # renders without error


def test_tally_credit_note_for_part_of_an_invoice_balances(seeded):
    inv = dispatched_invoice()
    services.issue_credit_note(inv, admin(), "part", {line(inv).pk: 33}, True)
    xml = tally.build_tally_xml(Invoice.objects.all(), CreditNote.objects.all(), Payment.objects.all(), "X")
    vs = ET.fromstring(xml).findall(".//VOUCHER")
    assert {v.get("VCHTYPE") for v in vs} == {"Sales", "Credit Note"}
    for v in vs:
        assert sum(D(a.text) for a in v.findall("ALLLEDGERENTRIES.LIST/AMOUNT")) == 0


def test_batch_trace_is_net_of_returns(seeded):
    inv = dispatched_invoice()
    services.issue_credit_note(inv, admin(), "back", {line(inv).pk: 20}, True)
    open_batch = Batch.objects.get(number="OPEN-MUR200")
    assert [(i.number, q) for i, _, q in services.batch_trace(open_batch)] == [(inv.number, D("30"))]
    services.issue_credit_note(inv, admin(), "rest of it", None, True)
    assert services.batch_trace(open_batch) == []


def test_credit_note_pdf_lists_the_packets(seeded):
    inv = dispatched_invoice()
    cn = services.issue_credit_note(inv, admin(), "20 damaged", {line(inv).pk: 20}, True)
    data = credit_note_pdf(cn)
    assert data.startswith(b"%PDF")
    text = pdf_text(data)
    assert cn.number in text and "20 damaged" in text and "1,050.00" in text and "Taxable" in text


# ---------- screens ----------

@pytest.fixture
def admin_client(client, seeded):
    client.login(username="admin", password="demo12345")
    return client


def test_screen_flow_partial_dispatch_then_partial_return(admin_client):
    inv = retailer_a_invoice()
    l = line(inv)
    page = admin_client.get(reverse("invoice_detail", args=[inv.pk])).content.decode()
    assert "Still to dispatch" in page and f'name="dispatch_qty_{l.pk}"' in page and "Partial return or credit" in page

    r = admin_client.post(reverse("invoice_dispatch", args=[inv.pk]), {"lot": "part", f"dispatch_qty_{l.pk}": "60", "vehicle_ref": "V1"}, follow=True)
    assert "Part of the invoice is still to go" in r.content.decode() and "partly dispatched" in r.content.decode()
    assert stock.balance(FG()) == D("178")

    r = admin_client.post(reverse("invoice_return", args=[inv.pk]), {"reason": "damaged", f"credit_qty_{l.pk}": "10", "goods_returned": "yes"}, follow=True)
    assert "Credit note CN/" in r.content.decode() and stock.balance(FG()) == D("188")
    cn = CreditNote.objects.get()
    assert admin_client.get(reverse("credit_note_pdf", args=[cn.pk])).content.startswith(b"%PDF")

    r = admin_client.post(reverse("invoice_dispatch", args=[inv.pk]), {"lot": "all"}, follow=True)   # sends the 90 still owed (150 - 10 credited - 50 out)
    assert Invoice.objects.get(pk=inv.pk).dispatch_state == "full" and stock.balance(FG()) == D("98")


def test_screen_bad_input_gives_a_message_not_a_server_error(admin_client):
    inv = retailer_a_invoice()
    l = line(inv)
    for url, data in (("invoice_dispatch", {"lot": "part", f"dispatch_qty_{l.pk}": "abc"}),
                      ("invoice_dispatch", {"lot": "part", f"dispatch_qty_{l.pk}": "999"}),
                      ("invoice_dispatch", {"lot": "part"}),
                      ("invoice_return", {"reason": "x", f"credit_qty_{l.pk}": "-4"}),
                      ("invoice_return", {"reason": "x"})):
        r = admin_client.post(reverse(url, args=[inv.pk]), data, follow=True)
        assert r.status_code == 200
    assert admin_client.post(reverse("invoice_dispatch", args=[inv.pk]), {"lot": "part", "dispatch_qty_zz": "1"}).status_code == 404
    assert not Dispatch.objects.exists() and not CreditNote.objects.exists()


def test_only_admin_may_dispatch_in_lots_or_return(client, seeded):
    inv = retailer_a_invoice()
    for who, expected in (("salesman_a", 403), ("production", 403), ("accountant", 403), ("owner", 403)):
        client.login(username=who, password="demo12345")
        assert client.post(reverse("invoice_dispatch", args=[inv.pk]), {"lot": "all"}).status_code == expected
        assert client.post(reverse("invoice_return", args=[inv.pk]), {"reason": "x"}).status_code == expected
        client.logout()
    assert not Dispatch.objects.exists()


# ---------- upgrading existing data ----------

@pytest.mark.django_db(transaction=True)
def test_migration_backfills_lines_for_existing_dispatches_and_credit_notes():
    """Databases from before this feature: every old dispatch shipped all lines, every old credit note cancelled everything."""
    from django.db import connection
    from django.db.migrations.executor import MigrationExecutor
    ex = MigrationExecutor(connection)
    ex.migrate([("billing", "0001_initial")])
    old = ex.loader.project_state([("billing", "0001_initial")]).apps
    Sup = lambda n: old.get_model("billing", n)  # noqa: E731
    from apps.accounts.management.commands.seed_users import seed_users
    from apps.masters.management.commands.seed_masters import seed_masters
    from apps.masters.models import Customer as C, Sku as S
    from apps.sales.models import Order as O
    from apps.accounts.models import User
    seed_users()
    seed_masters()   # masters and sales tables are not touched by the billing migration, so real rows are fine
    c, sku = C.objects.get(name="Retailer A"), S.objects.get(code="MUR200")
    o = O.objects.create(customer=c, salesman=User.objects.get(username="salesman_a"))
    inv = Sup("Invoice").objects.create(number="INV/1", fy="2026-27", order_id=o.pk, customer_id=c.pk, supplier_state_code="33",
        place_of_supply_code="33", place_of_supply_name="TN", intra_state=True, taxable_total=100, cgst_total=2.5, sgst_total=2.5,
        round_off=0, grand_total=105, supplier_name="S", supplier_gstin="x", buyer_name="C")
    il = Sup("InvoiceLine").objects.create(invoice=inv, sku_id=sku.pk, description="d", hsn_code="1905", qty=2, rate=50, gst_rate=5,
        taxable_value=100, cgst=2.5, sgst=2.5)
    Sup("Dispatch").objects.create(invoice=inv)
    Sup("CreditNote").objects.create(number="CN/1", invoice=inv, reason="r", goods_returned=True, taxable_total=100, cgst_total=2.5,
        sgst_total=2.5, igst_total=0, grand_total=105)
    ex = MigrationExecutor(connection)
    ex.migrate(ex.loader.graph.leaf_nodes())
    new = ex.loader.project_state(ex.loader.graph.leaf_nodes()).apps
    dl = new.get_model("billing", "DispatchLine").objects.get()
    cl = new.get_model("billing", "CreditNoteLine").objects.get()
    assert (dl.qty, dl.invoice_line_id) == (2, il.pk)
    assert (cl.qty, cl.qty_returned, cl.taxable_value, cl.cgst) == (2, 2, 100, D("2.50"))


def test_typed_quantity_wins_over_the_everything_option(admin_client):
    """A quantity typed with the 'everything' radio still selected must ship only what was typed."""
    inv = retailer_a_invoice()
    admin_client.post(reverse("invoice_dispatch", args=[inv.pk]), {"lot": "all", f"dispatch_qty_{line(inv).pk}": "25"})
    assert stock.balance(FG()) == D("213") and Invoice.objects.get(pk=inv.pk).dispatch_state == "partial"
