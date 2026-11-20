from django import template
from django.utils.translation import gettext_lazy as _

register = template.Library()

# Audit-log words are stored in English; they are translated when shown.
ACTIONS = {
    "started": _("started"), "issued": _("issued"), "completed": _("completed"), "voided": _("voided"),
    "submitted": _("submitted"), "approved": _("approved"), "rejected": _("rejected"), "invoiced": _("invoiced"),
    "dispatched": _("dispatched"), "cancelled": _("cancelled"), "credited": _("credited"), "received": _("received"), "added": _("added"),
    "checked_in": _("checked in"),
}
KEYS = {
    "sku": _("product"), "batches": _("batches"), "expected_output": _("expected output"), "item": _("item"), "qty": _("qty"),
    "unit": _("unit"), "batch": _("batch"), "good_packets": _("good packets"), "damaged_pouches": _("damaged pouches"),
    "packaging": _("packaging"), "remark": _("remark"), "reason": _("reason"), "customer": _("customer"), "lines": _("lines"),
    "invoice": _("invoice"), "amount": _("amount"), "kind": _("kind"), "run": _("run"), "outcome": _("outcome"),
}


def _fmt(v):
    if isinstance(v, dict):
        return "; ".join(f"{str(KEYS.get(k, k.replace('_', ' ')))} {_fmt(x)}" for k, x in v.items())
    if isinstance(v, (list, tuple)):
        return " → ".join(_fmt(x) for x in v) if len(v) == 2 else ", ".join(_fmt(x) for x in v)
    return "" if v is None else str(v)


@register.filter
def pretty(detail):
    """Audit-log detail dict -> readable text, e.g. {'qty': [120, 88]} -> 'qty 120 → 88' (keys translated)."""
    return _fmt(detail)


@register.filter
def action_label(action):
    return str(ACTIONS.get(action, action))
