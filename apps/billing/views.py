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
from .models import CreditNote, Dispatch, Invoice, Payment

READ = (Role.ADMIN, Role.ACCOUNTANT, Role.OWNER)


@roles_required(*READ)
def invoice_list(request):
    d_from, d_to, _, _ = date_range(request)
    invoices = Invoice.objects.filter(invoice_date__range=(d_from, d_to)).select_related("customer", "order")
    return render(request, "billing/invoice_list.html", {"invoices": invoices, "d_from": d_from, "d_to": d_to})


def whatsapp_number(raw):
    """wa.me needs the number with country code and nothing else. Indian mobiles are often stored as 10 digits
    (or with a leading 0), so assume India (+91) for those; anything longer is taken as already complete."""
    digits = "".join(c for c in (raw or "") if c.isdigit()).lstrip("0")
    return "91" + digits if len(digits) == 10 else digits


@roles_required(*READ)
def invoice_detail(request, pk):
    inv = get_object_or_404(Invoice.objects.select_related("customer", "order"), pk=pk)
    public = request.build_absolute_uri(f"/i/{inv.share_token}.pdf")
    text = quote(f"Tax invoice {inv.number} from {inv.supplier_name}, total Rs. {inv.grand_total}. PDF: {public}")
    phone = whatsapp_number(inv.customer.phone)
    shipped = []
    from apps.inventory.models import StockMovement
    from django.contrib.contenttypes.models import ContentType
    ct = ContentType.objects.get_for_model(Dispatch)
    for d in inv.dispatches.order_by("id"):
        shipped.append((d, [(m.batch, -m.qty) for m in StockMovement.objects.filter(source_type=ct, source_id=d.pk).select_related("batch")]))
    lines = [(l, l.dispatched_qty, l.returned_qty, l.credited_qty, l.remaining_to_dispatch, l.creditable_qty) for l in inv.lines.all()]
    return render(request, "billing/invoice_detail.html", {
        "shipped": shipped, "inv": inv, "line_rows": lines, "credit_notes": inv.credit_notes.order_by("id"), "history": history(inv), "is_admin": request.user.role == Role.ADMIN,
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


def _quantities(request, prefix):
    """{invoice line id: qty text} from form fields named <prefix><line id>; blank fields are left out."""
    out = {}
    for key, value in request.POST.items():
        if key.startswith(prefix) and value.strip() != "":
            try:
                out[int(key[len(prefix):])] = value.strip()
            except ValueError:
                raise Http404
    return out


@roles_required(Role.ADMIN)
@require_POST
def invoice_dispatch(request, pk):
    inv = get_object_or_404(Invoice, pk=pk)
    try:
        typed = _quantities(request, "dispatch_qty_")
        # quantities typed in => a partial lot, even if the "everything" radio was left selected (never ship more than typed)
        lot = typed if (request.POST.get("lot") == "part" or typed) else None
        services.dispatch_invoice(inv, request.user, request.POST.get("vehicle_ref", ""), lot)
        messages.success(request, "Dispatched. Finished-goods stock reduced." + (" Part of the invoice is still to go." if inv.dispatch_state == "partial" else ""))
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


@roles_required(Role.ADMIN)
@require_POST
def invoice_return(request, pk):
    """Partial credit note: some packets credited, optionally taken back into stock."""
    inv = get_object_or_404(Invoice, pk=pk)
    returned = {"yes": True, "no": False}.get(request.POST.get("goods_returned"))
    try:
        cn = services.issue_credit_note(inv, request.user, request.POST.get("reason", ""), _quantities(request, "credit_qty_"), returned)
        messages.success(request, f"Credit note {cn.number} for ₹{cn.grand_total}" + (" (goods back in stock)" if cn.goods_returned else ""))
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


@roles_required(*READ)
def tally_export(request):
    """Vouchers for the date range as Tally import XML: invoices, credit notes and receipts."""
    from django.utils import timezone  # noqa: F401
    from apps.masters.models import CompanyProfile
    from . import tally
    d_from, d_to, _, _ = date_range(request)
    invoices = Invoice.objects.filter(invoice_date__range=(d_from, d_to)).select_related("order").order_by("invoice_date", "id")
    credit_notes = CreditNote.objects.filter(note_date__range=(d_from, d_to)).select_related("invoice", "invoice__order").order_by("note_date", "id")
    payments = Payment.objects.filter(paid_on__range=(d_from, d_to)).select_related("customer").order_by("paid_on", "id")
    xml = tally.build_tally_xml(invoices, credit_notes, payments, CompanyProfile.get().name)
    resp = HttpResponse(xml, content_type="application/xml")
    resp["Content-Disposition"] = f'attachment; filename="tally-{d_from}-{d_to}.xml"'
    return resp


def _json_download(data, filename):
    import json
    resp = HttpResponse(json.dumps(data, indent=2, ensure_ascii=False), content_type="application/json")
    resp["Content-Disposition"] = f'attachment; filename="{filename}"'
    return resp


@roles_required(*READ)
def einvoice_json(request, pk):
    from . import einvoice
    inv = get_object_or_404(Invoice, pk=pk)
    return _json_download(einvoice.build_einvoice(inv), f"einvoice-{inv.number.replace('/', '-')}.json")


@roles_required(*READ)
def eway_json(request, pk):
    from . import einvoice
    inv = get_object_or_404(Invoice, pk=pk)
    last = inv.dispatch
    vehicle = last.vehicle_ref if last else ""
    return _json_download(einvoice.build_eway_bill(inv, vehicle), f"ewaybill-{inv.number.replace('/', '-')}.json")
