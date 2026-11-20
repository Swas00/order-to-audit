"""Tax invoice / credit note PDFs (ReportLab, pure Python)."""

from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from .tax import amount_in_words

styles = getSampleStyleSheet()
SMALL = styles["BodyText"].clone("small", fontSize=8, leading=10)
GRID = TableStyle([("GRID", (0, 0), (-1, -1), 0.4, colors.grey), ("FONTSIZE", (0, 0), (-1, -1), 8),
                   ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f0e6d6")), ("VALIGN", (0, 0), (-1, -1), "TOP")])


def _m(x):
    return f"{x:,.2f}"


def invoice_pdf(invoice) -> bytes:
    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=12 * mm, rightMargin=12 * mm, topMargin=12 * mm, bottomMargin=12 * mm,
                            title=f"Tax Invoice {invoice.number}")
    cancelled = invoice.status == "cancelled"
    story = [Paragraph("<b>TAX INVOICE</b>" + (" - CANCELLED" if cancelled else ""), styles["Title"]),
             Paragraph(f"<b>{invoice.supplier_name}</b><br/>{invoice.supplier_address}<br/>GSTIN: {invoice.supplier_gstin}", styles["BodyText"]),
             Spacer(1, 4 * mm)]
    meta = [[Paragraph(f"<b>Invoice no:</b> {invoice.number}<br/><b>Date:</b> {invoice.invoice_date:%d-%m-%Y}<br/>"
                       f"<b>Order ref:</b> {invoice.order.number}", SMALL),
             Paragraph(f"<b>Bill to:</b> {invoice.buyer_name}<br/>{invoice.buyer_address}<br/>"
                       f"GSTIN: {invoice.buyer_gstin or 'Unregistered'}<br/>"
                       f"<b>Place of supply:</b> {invoice.place_of_supply_name} ({invoice.place_of_supply_code})", SMALL)]]
    story += [Table(meta, colWidths=[90 * mm, 96 * mm], style=TableStyle([("BOX", (0, 0), (-1, -1), 0.4, colors.grey), ("VALIGN", (0, 0), (-1, -1), "TOP")])),
              Spacer(1, 4 * mm)]
    intra = invoice.intra_state
    head = ["#", "Description", "HSN", "Qty", "Rate", "Taxable"] + (["CGST", "SGST"] if intra else ["IGST"]) + ["Total"]
    rows = [head]
    for i, l in enumerate(invoice.lines.all(), 1):
        tax_cells = ([f"{l.gst_rate / 2:g}% {_m(l.cgst)}", f"{l.gst_rate / 2:g}% {_m(l.sgst)}"] if intra
                     else [f"{l.gst_rate:g}% {_m(l.igst)}"])
        rows.append([i, l.description, l.hsn_code, l.qty, _m(l.rate), _m(l.taxable_value)] + tax_cells + [_m(l.total)])
    totals = ["", "Total", "", sum(l.qty for l in invoice.lines.all()), "", _m(invoice.taxable_total)]
    totals += [_m(invoice.cgst_total), _m(invoice.sgst_total)] if intra else [_m(invoice.igst_total)]
    story.append(Table(rows + [totals + [_m(invoice.taxable_total + invoice.tax_total)]], style=GRID, repeatRows=1))
    story += [Spacer(1, 3 * mm),
              Paragraph(f"Taxable value: {_m(invoice.taxable_total)} &nbsp; Tax: {_m(invoice.tax_total)} &nbsp; "
                        f"Round off: {_m(invoice.round_off)}", SMALL),
              Paragraph(f"<b>Grand total: Rs. {_m(invoice.grand_total)}</b>", styles["Heading3"]),
              Paragraph(f"Amount in words: {amount_in_words(invoice.grand_total)}", SMALL), Spacer(1, 6 * mm),
              Paragraph("This is a computer generated invoice. Prototype with dummy data.", SMALL)]
    doc.build(story)
    return buf.getvalue()


def credit_note_pdf(cn) -> bytes:
    inv = cn.invoice
    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=14 * mm, rightMargin=14 * mm, title=f"Credit Note {cn.number}")
    items = [["Item", "HSN", "Packets", "Rate", "Taxable"]] + [
        [l.invoice_line.description, l.invoice_line.hsn_code, str(l.qty), _m(l.invoice_line.rate), _m(l.taxable_value)]
        for l in cn.lines.select_related("invoice_line")]
    rows = [["Taxable value", _m(cn.taxable_total)], ["CGST", _m(cn.cgst_total)], ["SGST", _m(cn.sgst_total)],
            ["IGST", _m(cn.igst_total)], ["Round off", _m(cn.round_off)], ["Credit note total", _m(cn.grand_total)]]
    story = [Paragraph("<b>CREDIT NOTE</b>", styles["Title"]),
             Paragraph(f"<b>{inv.supplier_name}</b> (GSTIN {inv.supplier_gstin})", styles["BodyText"]),
             Paragraph(f"Credit note no: {cn.number} &nbsp; Date: {cn.note_date:%d-%m-%Y}<br/>Against invoice: {inv.number} dated "
                       f"{inv.invoice_date:%d-%m-%Y}<br/>To: {inv.buyer_name} (GSTIN {inv.buyer_gstin or 'Unregistered'})<br/>"
                       f"Reason: {cn.reason}", styles["BodyText"]), Spacer(1, 5 * mm),
             Table(items, style=GRID, colWidths=[62 * mm, 24 * mm, 24 * mm, 30 * mm, 38 * mm]), Spacer(1, 4 * mm),
             Table(rows, style=GRID, colWidths=[60 * mm, 40 * mm]), Spacer(1, 3 * mm),
             Paragraph(f"Amount in words: {amount_in_words(cn.grand_total)}", SMALL)]
    doc.build(story)
    return buf.getvalue()
