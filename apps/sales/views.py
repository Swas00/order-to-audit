import uuid
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from apps.accounts.models import Role
from apps.accounts.permissions import roles_required
from apps.audit.models import history
from apps.core.dates import to_pk
from apps.masters.models import Customer, Sku

from . import services
from .models import Order, OrderStatus

ALL_VIEW = (Role.SALESMAN, Role.ADMIN, Role.ACCOUNTANT, Role.OWNER)


@roles_required(Role.SALESMAN)
def order_new(request):
    customers = Customer.objects.filter(salesman=request.user, active=True)
    skus = list(Sku.objects.filter(active=True))
    selected = None
    if request.method == "POST":
        selected = customers.filter(pk=to_pk(request.POST.get("customer"))).first()
        try:
            quantities = {s: int(request.POST.get(f"qty_{s.pk}") or 0) for s in skus}
        except ValueError:
            messages.error(request, "Quantities must be whole numbers.")
        else:
            try:
                if selected is None:
                    raise services.OrderError("Choose a customer")
                if any(q < 0 for q in quantities.values()):
                    raise services.OrderError("Quantities cannot be negative")
                key = uuid.UUID(request.POST.get("client_uuid", ""))
                order = services.create_order(request.user, selected, quantities, client_uuid=key, note=request.POST.get("note", ""))
                messages.success(request, f"{order.number} submitted to the factory")
                return redirect("order_detail", pk=order.pk)
            except (services.OrderError, ValueError) as e:
                messages.error(request, str(e) if isinstance(e, services.OrderError) else "Please try again.")
    for s in skus:  # keep what was typed if the form is shown again with an error
        s.entered = request.POST.get(f"qty_{s.pk}", "") if request.method == "POST" else ""
    prices = {}
    for c in customers:
        prices[c.pk] = {s.pk: str(services.price_for(c, s)) for s in skus}
    return render(request, "sales/order_new.html", {"customers": customers, "skus": skus, "selected": selected,
                                                    "client_uuid": uuid.uuid4(), "prices": prices,
                                                    "offline_config": {"userId": request.user.pk, "submitUrl": reverse("order_api_submit")}})


@roles_required(*ALL_VIEW)
def order_list(request):
    orders = Order.objects.select_related("customer", "salesman").prefetch_related("lines")
    if request.user.role == Role.SALESMAN:
        orders = orders.filter(salesman=request.user)
    status = request.GET.get("status")
    if status in OrderStatus.values:
        orders = orders.filter(status=status)
    return render(request, "sales/order_list.html", {"orders": orders, "status": status, "statuses": OrderStatus.choices})


@roles_required(*ALL_VIEW)
def order_detail(request, pk):
    order = get_object_or_404(Order.objects.select_related("customer", "salesman"), pk=pk)
    if request.user.role == Role.SALESMAN and order.salesman_id != request.user.pk:
        raise Http404
    cov = services.coverage(order) if order.status == OrderStatus.SUBMITTED else None
    return render(request, "sales/order_detail.html", {
        "order": order, "coverage": cov, "history": history(order),
        "can_review": request.user.role == Role.ADMIN and order.status == OrderStatus.SUBMITTED})


def _edits(request, order):
    edits = {}
    for line in order.lines.all():
        try:
            edits[line.pk] = (int(request.POST.get(f"qty_{line.pk}", line.qty)),
                              Decimal(request.POST.get(f"price_{line.pk}", line.price)))
        except (ValueError, InvalidOperation, OverflowError):
            raise services.OrderError("Quantities must be whole numbers and prices valid amounts")
    return edits


@roles_required(Role.ADMIN)
@require_POST
def order_approve(request, pk):
    order = get_object_or_404(Order, pk=pk)
    try:
        services.approve_order(order, request.user, _edits(request, order), cap_to_available="available" in request.POST)
        messages.success(request, f"{order.number} approved")
    except services.OrderError as e:
        messages.error(request, str(e))
    return redirect("order_detail", pk=pk)


@roles_required(Role.ADMIN)
@require_POST
def order_reject(request, pk):
    order = get_object_or_404(Order, pk=pk)
    try:
        services.reject_order(order, request.user, request.POST.get("reason", ""))
        messages.success(request, f"{order.number} rejected")
    except services.OrderError as e:
        messages.error(request, str(e))
    return redirect("order_detail", pk=pk)


@roles_required(Role.SALESMAN, Role.ADMIN, Role.OWNER, Role.ACCOUNTANT)
def customer_sales(request):
    mine = request.user if request.user.role == Role.SALESMAN else None
    return render(request, "sales/customer_sales.html", {"rows": services.customer_wise_sales(mine)})


@roles_required(Role.SALESMAN)
def visit_new(request):
    from .models import VisitOutcome
    customers = Customer.objects.filter(salesman=request.user, active=True)
    if request.method == "POST":
        try:
            customer = customers.filter(pk=to_pk(request.POST.get("customer"))).first()
            if customer is None:
                raise services.OrderError("Choose a customer")
            services.check_in(request.user, customer, request.POST.get("outcome", ""), request.POST.get("latitude"),
                              request.POST.get("longitude"), request.POST.get("accuracy"), request.POST.get("note", ""),
                              uuid.UUID(request.POST.get("client_uuid", "")))
            messages.success(request, f"Checked in at {customer.name}")
            return redirect("visit_list")
        except ValueError:
            messages.error(request, "Please try again.")
        except services.OrderError as e:
            messages.error(request, str(e))
    return render(request, "sales/visit_new.html", {"customers": customers, "outcomes": VisitOutcome.choices, "client_uuid": uuid.uuid4()})


@roles_required(Role.SALESMAN, Role.ADMIN, Role.OWNER)
def visit_list(request):
    from .models import Visit
    visits = Visit.objects.select_related("customer", "salesman")
    if request.user.role == Role.SALESMAN:
        visits = visits.filter(salesman=request.user)
    return render(request, "sales/visit_list.html", {"visits": visits[:200]})
