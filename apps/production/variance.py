"""Planned-vs-actual variance. Pure functions, Decimal only, no database access.

Rule: variance % = (actual - planned) / planned * 100, rounded to 2 places.
A line is FLAG when the variance is *adverse* and its size is strictly greater than the tolerance
(materials: used more than planned; yield: produced less than planned). A variance beyond tolerance
in the favourable direction is NOTE (worth a look, not an alert). Anything else is OK.
"""

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

OK, FLAG, NOTE = "ok", "flag", "note"
HUNDRED = Decimal("100")
PCT = Decimal("0.01")


@dataclass(frozen=True)
class Variance:
    planned: Decimal
    actual: Decimal
    diff: Decimal
    pct: Decimal | None  # None when nothing was planned
    status: str


def variance(planned, actual, tolerance_pct, higher_is_adverse=True) -> Variance:
    """`higher_is_adverse=True` for consumption, False for yield/output."""
    planned, actual, tol = Decimal(planned), Decimal(actual), Decimal(tolerance_pct)
    diff = actual - planned
    if planned == 0:
        # Unplanned usage: no percentage exists. Using something never planned is adverse.
        status = FLAG if (actual != 0 and higher_is_adverse) else OK
        return Variance(planned, actual, diff, None, status)
    pct = (diff / planned * HUNDRED).quantize(PCT, ROUND_HALF_UP)
    if abs(pct) <= tol:
        status = OK
    else:
        adverse = diff > 0 if higher_is_adverse else diff < 0
        status = FLAG if adverse else NOTE
    return Variance(planned, actual, diff, pct, status)
