from django import template

register = template.Library()


def _fmt(v):
    if isinstance(v, dict):
        return "; ".join(f"{k.replace('_', ' ')} {_fmt(x)}" for k, x in v.items())
    if isinstance(v, (list, tuple)):
        return " → ".join(_fmt(x) for x in v) if len(v) == 2 else ", ".join(_fmt(x) for x in v)
    return "" if v is None else str(v)


@register.filter
def pretty(detail):
    """Audit-log detail dict -> readable text, e.g. {'qty': [120, 88]} -> 'qty 120 → 88'."""
    return _fmt(detail)
