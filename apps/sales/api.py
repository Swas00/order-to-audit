"""JSON endpoint used by the order page's offline queue.

The phone may submit the same order several times (retry after a dropped connection, two tabs, re-sync).
`client_uuid` makes that safe: the first submission creates the order, repeats return the same order.
"""

import json
import uuid
from datetime import timedelta

from django.http import JsonResponse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.http import require_POST

from apps.accounts.models import Role
from apps.core.dates import to_pk
from apps.masters.models import Customer, Sku

from . import services

MAX_AGE = timedelta(days=30)  # an order captured longer ago than this is stamped with the arrival time


def _err(message, status=400):
    return JsonResponse({"ok": False, "error": message}, status=status)


def _captured_at(raw):
    """Trust the phone's capture time only if it is plausible; otherwise use now."""
    now = timezone.now()
    try:
        t = parse_datetime(raw) if isinstance(raw, str) else None
    except ValueError:
        t = None
    if t is None or timezone.is_naive(t) or t > now or now - t > MAX_AGE:
        return now
    return t


@require_POST
def submit_order(request):
    if not request.user.is_authenticated:
        return _err("Please log in again", 401)  # JSON, not a redirect, so the queue can react
    if request.user.role != Role.SALESMAN:
        return _err("Only salesmen can submit orders", 403)
    try:
        body = json.loads(request.body or b"{}")
        if not isinstance(body, dict):
            raise ValueError
        key = uuid.UUID(str(body.get("client_uuid", "")))
    except (ValueError, TypeError):
        return _err("Malformed request")
    customer = Customer.objects.filter(pk=to_pk(body.get("customer")), salesman=request.user, active=True).first()
    if customer is None:
        return _err("This customer is not on your list")
    raw = body.get("quantities")
    if not isinstance(raw, dict):
        return _err("Add at least one product")
    skus = {s.pk: s for s in Sku.objects.filter(active=True)}
    quantities = {}
    for k, v in raw.items():
        sku = skus.get(to_pk(k))
        if sku is None:
            return _err("A product in this order is no longer available")
        if isinstance(v, bool) or not isinstance(v, (int, str)) or not str(v).lstrip("-").isdigit():
            return _err("Quantities must be whole numbers")
        quantities[sku] = int(v)
    note = body.get("note", "")
    try:
        order = services.create_order(request.user, customer, quantities, client_uuid=key,
                                      note=note[:250] if isinstance(note, str) else "",
                                      created_at=_captured_at(body.get("captured_at")))
    except services.OrderError as e:
        return _err(str(e), 409 if "reference" in str(e) else 400)
    return JsonResponse({"ok": True, "order": {"id": order.pk, "number": order.number, "status": order.status,
                                               "url": f"/orders/{order.pk}/"}})
