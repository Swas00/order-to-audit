"""e-Invoice and e-Way Bill data in the government JSON shapes. MOCK: nothing is sent to the IRP / NIC.

e-Invoice: field names follow the NIC "INV-01" schema (version 1.1). The IRN is computed with the real
rule: SHA-256 of  supplier GSTIN + financial year + document type + document number.
The acknowledgement number/date and the signed QR are placeholders, marked as such, because only the
government portal can issue them.

e-Way Bill: field names follow the NIC e-Way Bill bulk-upload JSON. Required by law when the consignment
value exceeds Rs 50,000; the file can be generated for any dispatched invoice.
"""

import hashlib
from decimal import Decimal

EWB_THRESHOLD = Decimal("50000")
MOCK_NOTE = "MOCK DATA: not registered with the Invoice Registration Portal / e-Way Bill portal"


def irn_for(supplier_gstin: str, fy: str, doc_type: str, doc_no: str) -> str:
    return hashlib.sha256(f"{supplier_gstin}{fy}{doc_type}{doc_no}".encode()).hexdigest()


def _f(x) -> float:
    return float(Decimal(x).quantize(Decimal("0.01")))  # JSON numbers; amounts are Decimal until this last step


def build_einvoice(inv) -> dict:
    """INV-01 style payload for one invoice."""
    lines = []
    for n, l in enumerate(inv.lines.all(), 1):
        lines.append({
            "SlNo": str(n), "PrdDesc": l.description, "IsServc": "N", "HsnCd": l.hsn_code,
            "Qty": l.qty, "Unit": "PAC", "UnitPrice": _f(l.rate), "TotAmt": _f(l.taxable_value),
            "Discount": 0, "AssAmt": _f(l.taxable_value), "GstRt": _f(l.gst_rate),
            "IgstAmt": _f(l.igst), "CgstAmt": _f(l.cgst), "SgstAmt": _f(l.sgst), "CesRt": 0, "CesAmt": 0,
            "TotItemVal": _f(l.total),
        })
    irn = irn_for(inv.supplier_gstin, inv.fy, "INV", inv.number)
    cust = inv.customer
    return {
        "Version": "1.1",
        "Irn": irn,
        "TranDtls": {"TaxSch": "GST", "SupTyp": "B2B" if inv.buyer_gstin else "B2C", "RegRev": "N", "IgstOnIntra": "N"},
        "DocDtls": {"Typ": "INV", "No": inv.number, "Dt": inv.invoice_date.strftime("%d/%m/%Y")},
        "SellerDtls": {"Gstin": inv.supplier_gstin, "LglNm": inv.supplier_name, "Addr1": inv.supplier_address or "NA",
                       "Loc": "Tiruchirappalli", "Pin": _pin(_company_pin()), "Stcd": inv.supplier_state_code},
        "BuyerDtls": {"Gstin": inv.buyer_gstin or "URP", "LglNm": inv.buyer_name, "Pos": inv.place_of_supply_code,
                      "Addr1": inv.buyer_address or "NA", "Loc": inv.place_of_supply_name, "Pin": _pin(cust.pincode),
                      "Stcd": inv.place_of_supply_code},
        "ItemList": lines,
        "ValDtls": {"AssVal": _f(inv.taxable_total), "CgstVal": _f(inv.cgst_total), "SgstVal": _f(inv.sgst_total),
                    "IgstVal": _f(inv.igst_total), "CesVal": 0, "Discount": 0, "RndOffAmt": _f(inv.round_off),
                    "TotInvVal": _f(inv.grand_total)},
        "_mock": {"note": MOCK_NOTE, "AckNo": int(irn[:12], 16) % 10**15, "AckDt": inv.created_at.strftime("%Y-%m-%d %H:%M:%S"),
                  "SignedQRCode": "MOCK-NOT-A-REAL-QR"},
    }


def _company_pin():
    from apps.masters.models import CompanyProfile
    return CompanyProfile.get().pincode


def _pin(value) -> int:
    return int(value) if value and str(value).isdigit() and len(str(value)) == 6 else 0


def build_eway_bill(inv, vehicle_no: str = "") -> dict:
    """E-way bill JSON for a dispatched invoice (road transport)."""
    items = [{
        "productName": l.description, "productDesc": l.description, "hsnCode": int(l.hsn_code), "quantity": l.qty,
        "qtyUnit": "PAC", "taxableAmount": _f(l.taxable_value), "sgstRate": _f(l.gst_rate / 2) if inv.intra_state else 0,
        "cgstRate": _f(l.gst_rate / 2) if inv.intra_state else 0, "igstRate": 0 if inv.intra_state else _f(l.gst_rate), "cessRate": 0,
    } for l in inv.lines.all()]
    cust = inv.customer
    return {
        "supplyType": "O", "subSupplyType": "1", "docType": "INV", "docNo": inv.number,
        "docDate": inv.invoice_date.strftime("%d/%m/%Y"),
        "fromGstin": inv.supplier_gstin, "fromTrdName": inv.supplier_name, "fromAddr1": inv.supplier_address or "NA",
        "fromPlace": "Tiruchirappalli", "fromPincode": _pin(_company_pin()), "fromStateCode": int(inv.supplier_state_code),
        "actFromStateCode": int(inv.supplier_state_code),
        "toGstin": inv.buyer_gstin or "URP", "toTrdName": inv.buyer_name, "toAddr1": inv.buyer_address or "NA",
        "toPlace": inv.place_of_supply_name, "toPincode": _pin(cust.pincode), "toStateCode": int(inv.place_of_supply_code),
        "actToStateCode": int(inv.place_of_supply_code),
        "totalValue": _f(inv.taxable_total), "cgstValue": _f(inv.cgst_total), "sgstValue": _f(inv.sgst_total),
        "igstValue": _f(inv.igst_total), "cessValue": 0, "totInvValue": _f(inv.grand_total),
        "transMode": "1", "transDistance": "0", "transporterName": "", "transporterId": "",
        "vehicleNo": vehicle_no.replace(" ", "").replace("-", "").upper(), "vehicleType": "R",
        "itemList": items,
        "_mock": {"note": MOCK_NOTE, "ewbRequired": inv.grand_total > EWB_THRESHOLD,
                  "reason": "Consignment value above Rs 50,000" if inv.grand_total > EWB_THRESHOLD else "Below the Rs 50,000 threshold: optional"},
    }
