"""Server-side role enforcement. Hiding a menu item is never the control; these are."""

from functools import wraps

from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.contrib.auth.decorators import login_required


def roles_required(*roles):
    """View decorator: user must be logged in and have one of `roles`, else 403."""

    def decorator(view):
        @wraps(view)
        @login_required
        def wrapper(request, *args, **kwargs):
            if request.user.role not in roles:
                raise PermissionDenied
            return view(request, *args, **kwargs)

        return wrapper

    return decorator


class RolesRequiredMixin(LoginRequiredMixin):
    """Class-based view equivalent. Set `allowed_roles`."""

    allowed_roles: tuple = ()

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and request.user.role not in self.allowed_roles:
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)
