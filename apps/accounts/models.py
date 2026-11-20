from django.contrib.auth.models import AbstractUser
from django.db import models


class Role(models.TextChoices):
    SALESMAN = "salesman", "Salesman"
    ADMIN = "admin", "Factory admin"
    PRODUCTION = "production", "Production supervisor"
    ACCOUNTANT = "accountant", "Accountant / auditor (read-only)"
    OWNER = "owner", "Owner"


class User(AbstractUser):
    """One role per user. Role drives every access decision (see permissions.py)."""

    role = models.CharField(max_length=20, choices=Role.choices, default=Role.SALESMAN)

    def save(self, *args, **kwargs):
        # Only admin and production staff may open the master-data screens (Django admin);
        # what they can do there is limited by the role groups in roles_setup.py.
        self.is_staff = self.is_superuser or self.role in (Role.ADMIN, Role.PRODUCTION)
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.get_full_name() or self.username} ({self.role})"
