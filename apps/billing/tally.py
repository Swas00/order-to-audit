"""Tally-compatible XML export (Tally Prime / ERP 9 "Import Data" format).

Tally is optional here: this file is a one-way export so the accountant can load the month into Tally.
It writes, in order: customer ledgers, Sales vouchers (one per invoice), Credit Note vouchers (one per
cancelled invoice) and Receipt vouchers (one per payment).

Every voucher balances: debit entries carry a negative AMOUNT with ISDEEMEDPOSITIVE=Yes, credit entries a
positive AMOUNT with ISDEEMEDPOSITIVE=No, and the AMOUNTs of a voucher always sum to zero.
Ledger names below must exist in (or be created in) the company in Tally; they are plain constants.
"""

from decimal import Decimal
from xml.etree import ElementTree as ET

LEDGER_SALES = "Sales"
LEDGER_CGST, LEDGER_SGST, LEDGER_IGST = "Output CGST", "Output SGST", "Output IGST"
LEDGER_ROUND = "Round Off"
LEDGER_FOR_MODE = {"cash": "Cash", "upi": "Bank Account", "bank": "Bank Account", "cheque": "Bank Account"}


def _d(x) -> str:
    return f"{x:.2f}"


def _date(d) -> str:
    return d.strftime("%Y%m%d")


def _entry(parent, ledger, amount: Decimal, debit: bool):
    """One ledger line. `amount` is positive; `debit` decides the sign convention Tally expects."""
    e = ET.SubElement(parent, "ALLLEDGERENTRIES.LIST")
    ET.SubElement(e, "LEDGERNAME").text = ledger
    ET.SubElement(e, "ISDEEMEDPOSITIVE").text = "Yes" if debit else "No"
    ET.SubElement(e, "AMOUNT").text = _d(-amount if debit else amount)


def _voucher(msg, vtype, number, date, party, narration):
    v = ET.SubElement(ET.SubElement(msg, "TALLYMESSAGE", {"xmlns:UDF": "TallyUDF"}), "VOUCHER",
                      {"VCHTYPE": vtype, "ACTION": "Create", "OBJVIEW": "Accounting Voucher View"})
    ET.SubElement(v, "DATE").text = _date(date)
    ET.SubElement(v, "VOUCHERTYPENAME").text = vtype
    ET.SubElement(v, "VOUCHERNUMBER").text = number
    ET.SubElement(v, "PARTYLEDGERNAME").text = party
    ET.SubElement(v, "NARRATION").text = narration
    return v


def _sales_like(msg, vtype, number, date, party, narration, taxable, cgst, sgst, igst, round_off, total, sale_is_credit):
    """Sales: party Dr, sales/taxes Cr. Credit note: the opposite."""
    v = _voucher(msg, vtype, number, date, party, narration)
    _entry(v, party, total, debit=sale_is_credit)
    _entry(v, LEDGER_SALES, taxable, debit=not sale_is_credit)
    for ledger, amt in ((LEDGER_CGST, cgst), (LEDGER_SGST, sgst), (LEDGER_IGST, igst)):
        if amt:
            _entry(v, ledger, amt, debit=not sale_is_credit)
    if round_off:
        # total = taxable + tax + round_off. For a sale a positive round-off is extra money from the party
        # (credit to Round Off) and a negative one is a discount (debit). A credit note reverses both.
        _entry(v, LEDGER_ROUND, abs(round_off), debit=(round_off < 0) if sale_is_credit else (round_off > 0))


def build_tally_xml(invoices, credit_notes, payments, company_name) -> bytes:
    env = ET.Element("ENVELOPE")
    hdr = ET.SubElement(env, "HEADER")
    ET.SubElement(hdr, "TALLYREQUEST").text = "Import Data"
    body = ET.SubElement(env, "BODY")
    imp = ET.SubElement(body, "IMPORTDATA")
    desc = ET.SubElement(ET.SubElement(imp, "REQUESTDESC"), "STATICVARIABLES")
    ET.SubElement(desc, "SVCURRENTCOMPANY").text = company_name
    ET.SubElement(ET.SubElement(imp, "REQUESTDESC"), "REPORTNAME").text = "Vouchers"
    msg = ET.SubElement(imp, "REQUESTDATA")

    parties = sorted({i.buyer_name for i in invoices} | {c.invoice.buyer_name for c in credit_notes} | {p.customer.name for p in payments})
    for name in parties:
        led = ET.SubElement(ET.SubElement(msg, "TALLYMESSAGE", {"xmlns:UDF": "TallyUDF"}), "LEDGER", {"NAME": name, "ACTION": "Create"})
        ET.SubElement(led, "NAME").text = name
        ET.SubElement(led, "PARENT").text = "Sundry Debtors"

    for inv in invoices:
        _sales_like(msg, "Sales", inv.number, inv.invoice_date, inv.buyer_name, f"Order {inv.order.number}",
                    inv.taxable_total, inv.cgst_total, inv.sgst_total, inv.igst_total, inv.round_off, inv.grand_total, True)
    for cn in credit_notes:
        inv = cn.invoice
        _sales_like(msg, "Credit Note", cn.number, cn.note_date, inv.buyer_name, f"Against {inv.number}: {cn.reason}",
                    cn.taxable_total, cn.cgst_total, cn.sgst_total, cn.igst_total, inv.round_off, cn.grand_total, False)
    for p in payments:
        v = _voucher(msg, "Receipt", f"RCPT-{p.pk:05d}", p.paid_on, p.customer.name, f"{p.get_mode_display()} {p.reference}".strip())
        _entry(v, LEDGER_FOR_MODE[p.mode], p.amount, debit=True)
        _entry(v, p.customer.name, p.amount, debit=False)
    ET.indent(env)
    return b'<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(env, encoding="utf-8")
