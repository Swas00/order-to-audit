"""Role -> permission groups for the master-data screens.

Factory admin: add/change/delete/view masters. Production supervisor: view only.
Other roles are not staff and cannot open /admin/ at all.
"""

from django.contrib.auth.models import Group, Permission

from .models import Role


def setup_role_groups():
    perms = Permission.objects.filter(content_type__app_label="masters")
    admin_group, _ = Group.objects.get_or_create(name=Role.ADMIN)
    admin_group.permissions.set(perms)
    prod_group, _ = Group.objects.get_or_create(name=Role.PRODUCTION)
    prod_group.permissions.set(perms.filter(codename__startswith="view_"))
