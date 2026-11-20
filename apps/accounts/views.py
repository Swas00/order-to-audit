from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from .models import Role

# Role-specific landing pages are placeholders until their milestones land.
ROLE_HOME = {
    Role.SALESMAN: "Orders (Milestone 5)",
    Role.ADMIN: "Order approval, invoices, dispatch, payments (Milestones 5-6)",
    Role.PRODUCTION: "Raw-material receipts, issues, production runs (Milestones 3-4)",
    Role.ACCOUNTANT: "Read-only reports (Milestone 8)",
    Role.OWNER: "Dashboard (Milestone 8)",
}


@login_required
def home(request):
    return render(request, "home.html", {"coming": ROLE_HOME[request.user.role]})
