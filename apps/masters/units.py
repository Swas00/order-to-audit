"""Unit conversion. All arithmetic is Decimal; floats are never used."""

from decimal import Decimal

from .models import Dimension, Unit


class UnitError(ValueError):
    pass


def convert(qty, from_unit: Unit, to_unit: Unit) -> Decimal:
    """Convert within one dimension (kg<->g, L<->ml, piece<->packet). Raises UnitError otherwise."""
    qty = Decimal(qty)
    if from_unit.dimension != to_unit.dimension:
        raise UnitError(f"Cannot convert {from_unit} ({from_unit.dimension}) to {to_unit} ({to_unit.dimension})")
    if from_unit.dimension == Dimension.PACK and from_unit != to_unit:
        raise UnitError("Carton conversions depend on the SKU; use cartons_to_packets().")
    return qty * from_unit.factor / to_unit.factor


def cartons_to_packets(cartons, packets_per_carton: int) -> Decimal:
    return Decimal(cartons) * packets_per_carton


def packets_to_cartons(packets, packets_per_carton: int):
    """Returns (full cartons, loose packets)."""
    return divmod(int(packets), packets_per_carton)
