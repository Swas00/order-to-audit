"""Tally XML export and the (mock) e-Invoice / e-Way Bill JSON."""

import hashlib
import json
import uuid
from datetime import date
from decimal import Decimal as D
from xml.etree import ElementTree as ET

import pytest
from django.urls import reverse

from apps.accounts.management.commands.seed_users import seed_users
from apps.accounts.models import User
from apps.billing import einvoice, services, tally
from apps.core.benchmark import run_full_benchmark
from apps.inventory import services as stock
from apps.inventory.models import Batch
from apps.inventory.management.commands.seed_inventory import seed_inventory
from apps.masters.management.commands.seed_masters import seed_masters
from apps.masters.models import Customer, Sku
from apps.sales import services as sales
from django.utils import timezone
from datetime import timedelta

pytestmark = pytest.mark.django_db
user = lambda n: User.objects.get(username=n)  # noqa: E731
FG = lambda: Sku.objects.get(code="MUR200")  # noqa: E731
cust = lambda n: Customer.objects.get(name=n)  # noqa: E731


@pytest.fixture
def seeded():
    seed_users()
    seed_masters()
    seed_inventory()


def stock_up(qty):
    b = Batch.objects.create(sku=FG(), number=f"T{uuid.uuid4().hex[:6]}", mfg_date=timezone.localdate(), expiry_date=timezone.localdate() + timedelta(days=90))
    stock.adjust(FG(), D(qty), "test stock", user("admin"), unit_cost=D("20"), batch=b)


def invoice_for(customer, salesman, qty, price=None):
    o = sales.create_order(user(salesman), cust(customer), {FG(): qty}, client_uuid=uuid.uuid4())
    line = o.lines.get()
    edits = {line.pk: (qty, price)} if price is not None else None
    sales.approve_order(o, user("admin"), edits=edits)
    o.refresh_from_db()
    return services.raise_invoice(o, user("admin"))


def vouchers(xml: bytes):
    root = ET.fromstring(xml)
    return root, root.findall(".//VOUCHER")


def assert_balanced(v):
    assert sum(D(a.text) for a in v.findall("ALLLEDGERENTRIES.LIST/AMOUNT")) == D("0.00"), ET.tostring(v)


def entries(v):
    return {e.find("LEDGERNAME").text: (e.find("AMOUNT").text, e.find("ISDEEMEDPOSITIVE").text) for e in v.findall("ALLLEDGERENTRIES.LIST")}


# ---------- Tally ----------

def test_benchmark_sales_voucher(seeded):
    ctx = run_full_benchmark()
    from apps.billing.models import Invoice, Payment, CreditNote
    xml = tally.build_tally_xml(Invoice.objects.all(), CreditNote.objects.all(), Payment.objects.all(), "Demo Snacks Pvt Ltd")
    root, vs = vouchers(xml)
    assert root.tag == "ENVELOPE" and root.find("HEADER/TALLYREQUEST").text == "Import Data"
    assert root.find(".//SVCURRENTCOMPANY").text == "Demo Snacks Pvt Ltd"
    sales_v = [v for v in vs if v.get("VCHTYPE") == "Sales"]
    assert len(sales_v) == 1
    v = sales_v[0]
    assert v.find("VOUCHERNUMBER").text == ctx["inv_a"].number and v.find("PARTYLEDGERNAME").text == "Retailer A"
    assert v.find("DATE").text == ctx["inv_a"].invoice_date.strftime("%Y%m%d")
    assert entries(v) == {"Retailer A": ("-7875.00", "Yes"), "Sales": ("7500.00", "No"),
                          "Output CGST": ("187.50", "No"), "Output SGST": ("187.50", "No")}
    assert_balanced(v)


def test_interstate_uses_igst_ledger(seeded):
    stock_up(500)
    inv = invoice_for("Distributor B", "salesman_b", 100)
    _, vs = vouchers(tally.build_tally_xml([inv], [], [], "X"))
    assert entries(vs[0]) == {"Distributor B": ("-5250.00", "Yes"), "Sales": ("5000.00", "No"), "Output IGST": ("250.00", "No")}
    assert_balanced(vs[0])


@pytest.mark.parametrize("qty,price", [(3, "33.33"), (7, "12.34"), (1, "0.10"), (11, "99.99"), (5, "50.00")])
def test_every_voucher_balances_including_round_off(seeded, qty, price):
    stock_up(100)
    inv = invoice_for("Retailer A", "salesman_a", qty, D(price))
    xml = tally.build_tally_xml([inv], [], [], "X")
    _, vs = vouchers(xml)
    assert_balanced(vs[0])
    e = entries(vs[0])
    if inv.round_off > 0:
        assert e["Round Off"] == (f"{inv.round_off:.2f}", "No")     # extra money from the party: credit
    elif inv.round_off < 0:
        assert e["Round Off"] == (f"{-abs(inv.round_off):.2f}", "Yes")  # discount to the party: debit (debits are negative)
    else:
        assert "Round Off" not in e
    # credit note reverses the invoice exactly and also balances
    cn = services.cancel_invoice(inv, user("admin"), "test")
    _, vs = vouchers(tally.build_tally_xml([inv], [cn], [], "X"))
    credit = [v for v in vs if v.get("VCHTYPE") == "Credit Note"][0]
    assert_balanced(credit)
    sale, reverse = entries(vs[0]), entries(credit)
    assert set(sale) == set(reverse)
    for ledger, (amount, debit_flag) in sale.items():  # same ledgers, opposite side, opposite sign
        r_amount, r_flag = reverse[ledger]
        assert D(r_amount) == -D(amount) and r_flag != debit_flag, ledger


def test_receipts_ledgers_and_cancelled_invoice(seeded):
    ctx = run_full_benchmark()
    from apps.billing.models import Invoice, Payment, CreditNote
    services.record_payment(cust("Retailer A"), "100", user("admin"), mode="cash")
    root, vs = vouchers(tally.build_tally_xml(Invoice.objects.all(), CreditNote.objects.all(), Payment.objects.all(), "X"))
    receipts = {v.find("NARRATION").text: v for v in vs if v.get("VCHTYPE") == "Receipt"}
    upi, cash = receipts["UPI BENCH-UTR"], receipts["Cash"]
    assert entries(upi) == {"Bank Account": ("-4000.00", "Yes"), "Retailer A": ("4000.00", "No")}
    assert entries(cash) == {"Cash": ("-100.00", "Yes"), "Retailer A": ("100.00", "No")}
    assert all(abs(sum(D(a.text) for a in v.findall("ALLLEDGERENTRIES.LIST/AMOUNT"))) == 0 for v in vs)
    ledgers = [l.get("NAME") for l in root.iter("LEDGER")]
    assert ledgers == ["Retailer A"] and root.find(".//LEDGER/PARENT").text == "Sundry Debtors"


def test_tally_xml_is_well_formed_and_escapes_text(seeded):
    ctx = run_full_benchmark()
    from apps.billing.models import Invoice
    Invoice.objects.filter(pk=ctx["inv_a"].pk).update(buyer_name='A & B <Traders> "x"')
    xml = tally.build_tally_xml(Invoice.objects.all(), [], [], "Co & Sons")
    assert xml.startswith(b'<?xml version="1.0" encoding="UTF-8"?>')
    root = ET.fromstring(xml)
    assert root.find(".//PARTYLEDGERNAME").text == 'A & B <Traders> "x"' and root.find(".//SVCURRENTCOMPANY").text == "Co & Sons"


def test_tally_export_view_range_and_access(client, seeded):
    run_full_benchmark()
    client.login(username="accountant", password="demo12345")
    r = client.get(reverse("tally_export"))
    assert r.status_code == 200 and r["Content-Type"] == "application/xml" and "tally-" in r["Content-Disposition"]
    _, vs = vouchers(r.content)
    assert {v.get("VCHTYPE") for v in vs} == {"Sales", "Receipt"}
    old = client.get(reverse("tally_export"), {"from": "2020-01-01", "to": "2020-01-31"})
    assert vouchers(old.content)[1] == []
    for name, status in {"admin": 200, "owner": 200, "production": 403, "salesman_a": 403}.items():
        client.login(username=name, password="demo12345")
        assert client.get(reverse("tally_export")).status_code == status, name


# ---------- e-Invoice ----------

def test_irn_is_the_documented_hash(seeded):
    ctx = run_full_benchmark()
    inv = ctx["inv_a"]
    expected = hashlib.sha256(f"33AABCD1234E1Z5{inv.fy}INV{inv.number}".encode()).hexdigest()
    assert einvoice.irn_for("33AABCD1234E1Z5", inv.fy, "INV", inv.number) == expected == einvoice.build_einvoice(inv)["Irn"]
    assert len(expected) == 64
    assert einvoice.irn_for("33AABCD1234E1Z5", inv.fy, "INV", "INV/2026-27/0002") != expected


def test_einvoice_payload_matches_the_invoice(seeded):
    ctx = run_full_benchmark()
    doc = einvoice.build_einvoice(ctx["inv_a"])
    assert doc["Version"] == "1.1" and doc["TranDtls"] == {"TaxSch": "GST", "SupTyp": "B2B", "RegRev": "N", "IgstOnIntra": "N"}
    assert doc["DocDtls"]["Typ"] == "INV" and doc["DocDtls"]["No"] == ctx["inv_a"].number
    assert doc["DocDtls"]["Dt"] == ctx["inv_a"].invoice_date.strftime("%d/%m/%Y")
    assert doc["SellerDtls"]["Gstin"] == "33AABCD1234E1Z5" and doc["SellerDtls"]["Pin"] == 620001 and doc["SellerDtls"]["Stcd"] == "33"
    assert doc["BuyerDtls"]["Gstin"] == "33AAACR1111A1Z5" and doc["BuyerDtls"]["Pos"] == "33" and doc["BuyerDtls"]["Pin"] == 620002
    item = doc["ItemList"][0]
    assert (item["SlNo"], item["HsnCd"], item["Qty"], item["UnitPrice"], item["AssAmt"], item["GstRt"]) == ("1", "19059030", 150, 50.0, 7500.0, 5.0)
    assert (item["CgstAmt"], item["SgstAmt"], item["IgstAmt"], item["TotItemVal"]) == (187.5, 187.5, 0.0, 7875.0)
    v = doc["ValDtls"]
    assert (v["AssVal"], v["CgstVal"], v["SgstVal"], v["IgstVal"], v["TotInvVal"]) == (7500.0, 187.5, 187.5, 0.0, 7875.0)
    assert v["AssVal"] + v["CgstVal"] + v["SgstVal"] + v["IgstVal"] + v["RndOffAmt"] == v["TotInvVal"]
    assert "MOCK" in doc["_mock"]["note"]
    json.dumps(doc)  # serialisable


def test_einvoice_interstate_and_unregistered(seeded):
    stock_up(300)
    inv = invoice_for("Distributor B", "salesman_b", 100)
    doc = einvoice.build_einvoice(inv)
    assert doc["ItemList"][0]["IgstAmt"] == 250.0 and doc["ItemList"][0]["CgstAmt"] == 0 and doc["BuyerDtls"]["Stcd"] == "29"
    from apps.billing.models import Invoice
    Invoice.objects.filter(pk=inv.pk).update(buyer_gstin="")
    inv.refresh_from_db()
    doc = einvoice.build_einvoice(inv)
    assert doc["TranDtls"]["SupTyp"] == "B2C" and doc["BuyerDtls"]["Gstin"] == "URP"


def test_eway_bill_payload_and_threshold(seeded):
    ctx = run_full_benchmark()
    from apps.billing.services import dispatch_invoice  # noqa: F401
    ewb = einvoice.build_eway_bill(ctx["inv_a"], "tn 45-ab 1234")
    assert ewb["supplyType"] == "O" and ewb["docNo"] == ctx["inv_a"].number and ewb["vehicleNo"] == "TN45AB1234"
    assert (ewb["fromStateCode"], ewb["toStateCode"], ewb["fromPincode"], ewb["toPincode"]) == (33, 33, 620001, 620002)
    assert ewb["totalValue"] == 7500.0 and ewb["cgstValue"] == 187.5 and ewb["totInvValue"] == 7875.0
    assert ewb["itemList"][0]["hsnCode"] == 19059030 and ewb["itemList"][0]["cgstRate"] == 2.5 and ewb["itemList"][0]["igstRate"] == 0
    assert ewb["_mock"]["ewbRequired"] is False
    stock_up(1500)
    big = invoice_for("Distributor B", "salesman_b", 1100)  # 55,000 + 2,750 GST
    big_ewb = einvoice.build_eway_bill(big)
    assert big_ewb["_mock"]["ewbRequired"] is True and big_ewb["itemList"][0]["igstRate"] == 5.0 and big_ewb["itemList"][0]["cgstRate"] == 0


def test_json_downloads_and_access(client, seeded):
    ctx = run_full_benchmark()
    inv = ctx["inv_a"]
    client.login(username="accountant", password="demo12345")
    r = client.get(reverse("einvoice_json", args=[inv.pk]))
    assert r["Content-Type"] == "application/json" and "einvoice-" in r["Content-Disposition"]
    assert json.loads(r.content)["Irn"] == einvoice.irn_for("33AABCD1234E1Z5", inv.fy, "INV", inv.number)
    e = json.loads(client.get(reverse("eway_json", args=[inv.pk])).content)
    assert e["vehicleNo"] == ""  # benchmark dispatch recorded no vehicle
    page = client.get(reverse("invoice_detail", args=[inv.pk])).content.decode()
    assert "e-Invoice JSON (mock)" in page and "e-Way Bill JSON (mock)" in page
    for name in ["production", "salesman_a"]:
        client.login(username=name, password="demo12345")
        assert client.get(reverse("einvoice_json", args=[inv.pk])).status_code == 403
        assert client.get(reverse("eway_json", args=[inv.pk])).status_code == 403
