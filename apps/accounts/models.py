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

    def __str__(self):
        return f"{self.get_full_name() or self.username} ({self.role})"
