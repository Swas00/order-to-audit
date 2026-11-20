"""Generic landscape PDF for report tables."""

from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table as RLTable, TableStyle

styles = getSampleStyleSheet()


def _cell(v):
    if v is None:
        return ""
    if hasattr(v, "as_tuple"):  # Decimal: at least 2 places, at most 4, thousands separators
        txt = f"{v:,.4f}".rstrip("0")
        whole, _, frac = txt.partition(".")
        return f"{whole}.{frac.ljust(2, '0')}"
    return str(v)


def tables_pdf(title, subtitle, tables) -> bytes:
    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(A4), leftMargin=10 * mm, rightMargin=10 * mm, topMargin=10 * mm,
                            bottomMargin=10 * mm, title=title)
    small = styles["BodyText"].clone("s", fontSize=7, leading=9)
    story = [Paragraph(f"<b>{title.replace('-', ' ')}</b>", styles["Heading2"])]
    if subtitle:
        story.append(Paragraph(subtitle, small))
    for t in tables:
        story += [Spacer(1, 4 * mm), Paragraph(f"<b>{t.title}</b>", styles["Heading4"])]
        if t.note:
            story.append(Paragraph(t.note, small))
        data = [[Paragraph(f"<b>{h}</b>", small) for h in t.headers]]
        data += [[Paragraph(_cell(v), small) for v in r] for r in t.rows] or [[Paragraph("No data", small)]]
        tbl = RLTable(data, repeatRows=1)
        tbl.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.3, colors.grey), ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f0e6d6")),
                                 ("VALIGN", (0, 0), (-1, -1), "TOP")]))
        story.append(tbl)
    doc.build(story)
    return buf.getvalue()
