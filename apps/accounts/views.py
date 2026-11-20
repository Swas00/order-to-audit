from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render

from .models import Role

ROLE_HOME = {
    Role.SALESMAN: "Take orders for your customers and follow their status.",
    Role.ADMIN: "Approve orders, raise GST invoices, record dispatch and payments, and run the reports.",
    Role.PRODUCTION: "Record raw-material receipts, production runs, output and wastage.",
    Role.ACCOUNTANT: "Read-only access to the monthly reports for accounts and audit.",
    Role.OWNER: "",
}


@login_required
def home(request):
    if request.user.role == Role.OWNER:
        return redirect("dashboard")
    return render(request, "home.html", {"coming": ROLE_HOME[request.user.role]})
