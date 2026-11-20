from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.accounts.models import Role
from apps.accounts.permissions import roles_required
from apps.audit.models import history
from apps.inventory.services import StockError

from . import services
from .forms import CompleteForm, IssueForm, StartRunForm
from .models import ProductionRun, RunStatus

VIEW = (Role.PRODUCTION, Role.ADMIN, Role.ACCOUNTANT, Role.OWNER)
WRITE = (Role.PRODUCTION, Role.ADMIN)


def _fail(request, exc):
    messages.error(request, str(exc))


@roles_required(*VIEW)
def run_list(request):
    runs = ProductionRun.objects.select_related("sku")
    return render(request, "production/list.html", {"runs": runs, "form": StartRunForm()})


@roles_required(*WRITE)
@require_POST
def run_start(request):
    form = StartRunForm(request.POST)
    if form.is_valid():
        try:
            run = services.start_run(form.cleaned_data["sku"], form.cleaned_data["batches"], request.user)
            return redirect("run_detail", pk=run.pk)
        except services.ProductionError as e:
            _fail(request, e)
    else:
        messages.error(request, "Check the product and batch size.")
    return redirect("run_list")


@roles_required(*VIEW)
def run_detail(request, pk, issue_form=None, complete_form=None):
    run = get_object_or_404(ProductionRun, pk=pk)
    ctx = {"report": services.run_report(run), "run": run, "history": history(run),
           "issue_form": issue_form or IssueForm(), "complete_form": complete_form or CompleteForm(run),
           "can_write": request.user.role in WRITE, "in_progress": run.status == RunStatus.IN_PROGRESS}
    return render(request, "production/detail.html", ctx)


@roles_required(*WRITE)
@require_POST
def run_issue(request, pk):
    run = get_object_or_404(ProductionRun, pk=pk)
    form = IssueForm(request.POST)
    if form.is_valid():
        d = form.cleaned_data
        try:
            services.issue_material(run, d["item"], d["qty"], request.user, unit=d["unit"])
            messages.success(request, f"Issued {d['qty']} {d['unit']} of {d['item'].name}")
            return redirect("run_detail", pk=pk)
        except (StockError, services.ProductionError) as e:
            _fail(request, e)
    return run_detail(request, pk, issue_form=form)


@roles_required(*WRITE)
@require_POST
def run_complete(request, pk):
    run = get_object_or_404(ProductionRun, pk=pk)
    form = CompleteForm(run, request.POST)
    if form.is_valid():
        d = form.cleaned_data
        try:
            services.complete_run(run, d["good_packets"], d["damaged_pouches"], form.packaging_used(), request.user, d["remark"])
            messages.success(request, "Run completed. Stock updated.")
            return redirect("run_detail", pk=pk)
        except (StockError, services.ProductionError) as e:
            _fail(request, e)
    return run_detail(request, pk, complete_form=form)


@roles_required(Role.ADMIN)
@require_POST
def run_void(request, pk):
    run = get_object_or_404(ProductionRun, pk=pk)
    try:
        services.void_run(run, request.POST.get("reason", ""), request.user)
        messages.success(request, "Run voided and its stock movements reversed.")
    except (StockError, services.ProductionError) as e:
        _fail(request, e)
    return redirect("run_detail", pk=pk)


@roles_required(*VIEW)
def run_plan(request):
    from . import planning
    include = request.GET.get("submitted", "1") != "0"
    return render(request, "production/plan.html", {"rows": planning.plan(include), "include": include, "can_write": request.user.role in WRITE})
