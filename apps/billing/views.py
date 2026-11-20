from urllib.parse import quote

from django.contrib import messages
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.accounts.models import Role
from apps.accounts.permissions import roles_required
from apps.audit.models import history
from apps.core.dates import date_range
from apps.inventory.services import StockError
from apps.sales.models import Order

from . import pdf, services
from .forms import PaymentForm
from .models import CreditNote, Invoice, Payment

READ = (Role.ADMIN, Role.ACCOUNTANT, Role.OWNER)


@roles_required(*READ)
def invoice_list(request):
    d_from, d_to, _, _ = date_range(request)
    invoices = Invoice.objects.filter(invoice_date__range=(d_from, d_to)).select_related("customer", "order")
    return render(request, "billing/invoice_list.html", {"invoices": invoices, "d_from": d_from, "d_to": d_to})


@roles_required(*READ)
def invoice_detail(request, pk):
    inv = get_object_or_404(Invoice.objects.select_related("customer", "order"), pk=pk)
    public = request.build_absolute_uri(f"/i/{inv.share_token}.pdf")
    text = quote(f"Tax invoice {inv.number} from {inv.supplier_name}, total Rs. {inv.grand_total}. PDF: {public}")
    phone = "".join(c for c in inv.customer.phone if c.isdigit())
    shipped = []
    if inv.is_dispatched:
        from apps.inventory.models import StockMovement
        from django.contrib.contenttypes.models import ContentType
        ct = ContentType.objects.get_for_model(inv.dispatch)
        shipped = [(m.batch, -m.qty) for m in StockMovement.objects.filter(source_type=ct, source_id=inv.dispatch.pk).select_related("batch")]
    return render(request, "billing/invoice_detail.html", {
        "shipped": shipped, "inv": inv, "history": history(inv), "is_admin": request.user.role == Role.ADMIN,
        "wa_link": f"https://wa.me/{phone}?text={text}", "mail_link": f"mailto:?subject={quote('Tax invoice ' + inv.number)}&body={text}"})


@roles_required(*READ)
def invoice_pdf(request, pk):
    inv = get_object_or_404(Invoice, pk=pk)
    resp = HttpResponse(pdf.invoice_pdf(inv), content_type="application/pdf")
    resp["Content-Disposition"] = f'inline; filename="{inv.number.replace("/", "-")}.pdf"'
    return resp


def shared_invoice_pdf(request, token):
    """Unlisted link for sending to the customer (WhatsApp/email). The 32-character random token is the access control."""
    inv = Invoice.objects.filter(share_token=token).first()
    if inv is None:
        raise Http404
    return HttpResponse(pdf.invoice_pdf(inv), content_type="application/pdf")


@roles_required(*READ)
def credit_note_pdf(request, pk):
    cn = get_object_or_404(CreditNote, pk=pk)
    return HttpResponse(pdf.credit_note_pdf(cn), content_type="application/pdf")


@roles_required(Role.ADMIN)
@require_POST
def invoice_raise(request, order_pk):
    order = get_object_or_404(Order, pk=order_pk)
    try:
        inv = services.raise_invoice(order, request.user)
    except services.BillingError as e:
        messages.error(request, str(e))
        return redirect("order_detail", pk=order_pk)
    messages.success(request, f"Invoice {inv.number} raised")
    return redirect("invoice_detail", pk=inv.pk)


@roles_required(Role.ADMIN)
@require_POST
def invoice_dispatch(request, pk):
    inv = get_object_or_404(Invoice, pk=pk)
    try:
        services.dispatch_invoice(inv, request.user, request.POST.get("vehicle_ref", ""))
        messages.success(request, "Dispatched. Finished-goods stock reduced.")
    except (services.BillingError, StockError) as e:
        messages.error(request, str(e))
    return redirect("invoice_detail", pk=pk)


@roles_required(Role.ADMIN)
@require_POST
def invoice_cancel(request, pk):
    inv = get_object_or_404(Invoice, pk=pk)
    returned = {"yes": True, "no": False}.get(request.POST.get("goods_returned"))
    try:
        cn = services.cancel_invoice(inv, request.user, request.POST.get("reason", ""), returned)
        messages.success(request, f"Invoice cancelled by credit note {cn.number}")
    except (services.BillingError, StockError) as e:
        messages.error(request, str(e))
    return redirect("invoice_detail", pk=pk)


@roles_required(*READ)
def payment_list(request):
    d_from, d_to, _, _ = date_range(request)
    payments = Payment.objects.filter(paid_on__range=(d_from, d_to)).select_related("customer").prefetch_related("allocations__invoice")
    return render(request, "billing/payment_list.html", {"payments": payments, "d_from": d_from, "d_to": d_to})


@roles_required(Role.ADMIN)
def payment_new(request):
    form = PaymentForm(request.POST or None, initial={"customer": request.GET.get("customer"), "invoice": request.GET.get("invoice")})
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        try:
            services.record_payment(d["customer"], d["amount"], request.user, d["invoice"], d["mode"], d["reference"])
            messages.success(request, f"Payment of ₹{d['amount']} recorded. Outstanding for {d['customer']}: ₹{services.customer_balance(d['customer'])}")
            return redirect("receivables")
        except services.BillingError as e:
            form.add_error(None, str(e))
    return render(request, "billing/payment_new.html", {"form": form})


@roles_required(*READ)
def receivables(request):
    from apps.masters.models import Customer
    rows = [{"customer": c, "balance": services.customer_balance(c)} for c in Customer.objects.all()]
    return render(request, "billing/receivables.html", {"rows": [r for r in rows if r["balance"] != 0], "ageing": services.ageing()})


@roles_required(Role.ADMIN, Role.PRODUCTION, Role.ACCOUNTANT, Role.OWNER)
def batch_list(request):
    from apps.inventory import services as stock
    from apps.inventory.models import Batch
    rows = [(b, stock.batch_balance(b)) for b in Batch.objects.select_related("sku", "run")]
    return render(request, "billing/batch_list.html", {"rows": rows})


@roles_required(Role.ADMIN, Role.PRODUCTION, Role.ACCOUNTANT, Role.OWNER)
def batch_trace(request, pk):
    from apps.inventory import services as stock
    from apps.inventory.models import Batch
    batch = get_object_or_404(Batch.objects.select_related("sku", "run"), pk=pk)
    return render(request, "billing/batch_trace.html", {"batch": batch, "on_hand": stock.batch_balance(batch),
                                                       "shipments": services.batch_trace(batch)})
