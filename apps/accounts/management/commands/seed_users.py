"""Create one demo user per role. Idempotent. Demo passwords only; never use real credentials."""

from django.core.management.base import BaseCommand

from django.contrib.auth.models import Group

from apps.accounts.models import Role, User
from apps.accounts.roles_setup import setup_role_groups

DEMO_PASSWORD = "demo12345"

DEMO_USERS = [
    ("salesman_a", "Salesman", "A", Role.SALESMAN),
    ("salesman_b", "Salesman", "B", Role.SALESMAN),
    ("admin", "Factory", "Admin", Role.ADMIN),
    ("production", "Production", "Supervisor", Role.PRODUCTION),
    ("accountant", "External", "Accountant", Role.ACCOUNTANT),
    ("owner", "Factory", "Owner", Role.OWNER),
]


def seed_users():
    setup_role_groups()
    for username, first, last, role in DEMO_USERS:
        user, _ = User.objects.get_or_create(username=username)
        user.first_name, user.last_name, user.role = first, last, role
        user.set_password(DEMO_PASSWORD)
        user.save()
        user.groups.set(Group.objects.filter(name=role))


class Command(BaseCommand):
    help = "Create demo users for every role (password: %s)" % DEMO_PASSWORD

    def handle(self, *args, **options):
        seed_users()
        self.stdout.write(self.style.SUCCESS(f"Seeded {len(DEMO_USERS)} users"))
