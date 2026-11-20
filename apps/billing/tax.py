"""GST arithmetic. Pure functions on Decimal. Every amount is rounded half-up to the paisa per line."""

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

PAISA = Decimal("0.01")
ZERO = Decimal("0.00")


def q(x) -> Decimal:
    return Decimal(x).quantize(PAISA, ROUND_HALF_UP)


@dataclass(frozen=True)
class LineTax:
    taxable: Decimal
    cgst: Decimal
    sgst: Decimal
    igst: Decimal

    @property
    def tax(self):
        return self.cgst + self.sgst + self.igst

    @property
    def total(self):
        return self.taxable + self.tax


def line_tax(qty, rate, gst_rate, intra_state: bool) -> LineTax:
    """Taxable value = qty x rate. Intra-state splits the GST rate equally into CGST and SGST;
    inter-state charges the whole rate as IGST."""
    taxable = q(Decimal(qty) * Decimal(rate))
    gst_rate = Decimal(gst_rate)
    if intra_state:
        half = q(taxable * gst_rate / 200)
        return LineTax(taxable, half, half, ZERO)
    return LineTax(taxable, ZERO, ZERO, q(taxable * gst_rate / 100))


def round_to_rupee(total: Decimal):
    """Returns (rounded grand total, round-off amount)."""
    grand = total.quantize(Decimal("1"), ROUND_HALF_UP).quantize(PAISA)
    return grand, grand - total


def financial_year(d: date) -> str:
    """Indian FY runs April-March: 2026-11-20 -> '2026-27'."""
    start = d.year if d.month >= 4 else d.year - 1
    return f"{start}-{(start + 1) % 100:02d}"


_ONES = ("Zero One Two Three Four Five Six Seven Eight Nine Ten Eleven Twelve Thirteen Fourteen Fifteen Sixteen "
         "Seventeen Eighteen Nineteen").split()
_TENS = "_ _ Twenty Thirty Forty Fifty Sixty Seventy Eighty Ninety".split()


def _below_100(n):
    return _ONES[n] if n < 20 else _TENS[n // 10] + (f" {_ONES[n % 10]}" if n % 10 else "")


def _below_1000(n):
    out = []
    if n >= 100:
        out.append(f"{_ONES[n // 100]} Hundred")
        n %= 100
    if n:
        out.append(_below_100(n))
    return " ".join(out)


def amount_in_words(amount: Decimal) -> str:
    """Indian numbering: 7875.00 -> 'Rupees Seven Thousand Eight Hundred Seventy Five Only'."""
    amount = q(amount)
    rupees, paise = int(amount), int((amount - int(amount)) * 100)
    parts = []
    for div, name in ((10**7, "Crore"), (10**5, "Lakh"), (1000, "Thousand")):
        if rupees >= div:
            parts.append(f"{_below_100(rupees // div) if rupees // div < 100 else _below_1000(rupees // div)} {name}")
            rupees %= div
    if rupees:
        parts.append(_below_1000(rupees))
    words = " ".join(parts) or "Zero"
    out = f"Rupees {words}"
    if paise:
        out += f" and {_below_100(paise)} Paise"
    return out + " Only"
