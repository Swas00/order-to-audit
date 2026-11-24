from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.contrib.auth.views import LoginView, PasswordChangeView
from django.core.cache import cache
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


# ---------- login throttling ----------
MAX_FAILURES, LOCK_SECONDS = 5, 15 * 60


def _throttle_key(request):
    from django.core.cache import cache  # noqa: F401
    who = (request.POST.get("username") or "").strip().lower()[:150]
    ip = request.META.get("REMOTE_ADDR", "")
    return f"login-fail:{who}:{ip}"


class ThrottledLoginView(LoginView):
    """Five wrong passwords for one username from one address lock that pair out for 15 minutes (even a correct
    password is refused while locked). Best effort: the counter lives in the process cache, so with several worker
    processes the limit applies per process."""

    template_name = "login.html"
    redirect_authenticated_user = True  # someone already signed in is sent home, not shown the form again

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        if settings.DEMO_LOGINS:  # public demo deployments only; off by default
            from .management.commands.seed_users import DEMO_PASSWORD, DEMO_USERS
            ctx["demo_logins"] = [(u, f"{f} {l}", Role(r).label) for u, f, l, r in DEMO_USERS]
            ctx["demo_password"] = DEMO_PASSWORD
        return ctx

    def post(self, request, *args, **kwargs):
        key = _throttle_key(request)
        if cache.get(key, 0) >= MAX_FAILURES:
            form = self.get_form()
            form.add_error(None, "Too many failed attempts. Try again in 15 minutes.")
            return self.render_to_response(self.get_context_data(form=form, locked=True), status=429)
        return super().post(request, *args, **kwargs)

    def form_invalid(self, form):
        key = _throttle_key(self.request)
        cache.add(key, 0, LOCK_SECONDS)
        cache.incr(key)
        return super().form_invalid(form)

    def form_valid(self, form):
        cache.delete(_throttle_key(self.request))
        return super().form_valid(form)


class SafePasswordChangeView(PasswordChangeView):
    """On a public demo (DEMO_LOGINS=1) the shared test passwords are shown on the login page, so letting a visitor
    change one would lock everyone else out. Refused there; normal installs are unaffected."""

    template_name = "password.html"
    success_url = "/"

    def dispatch(self, request, *args, **kwargs):
        if settings.DEMO_LOGINS and request.user.is_authenticated:
            messages.error(request, "Password changes are switched off on this public demo.")
            return redirect("home")
        return super().dispatch(request, *args, **kwargs)
