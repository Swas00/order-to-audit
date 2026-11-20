"""Replay the section 5 scenario on a freshly seeded database and print the results table.

Run after: migrate, seed_users, seed_masters, seed_inventory. Not idempotent: it creates transactions.
"""

from django.core.management.base import BaseCommand, CommandError

from apps.billing.models import Invoice
from apps.core.benchmark import check_results, run_full_benchmark


class Command(BaseCommand):
    help = "Run benchmark steps 1-8 and verify the expected results"

    def handle(self, *args, **options):
        if Invoice.objects.exists():
            raise CommandError("Transactions already exist. Use a fresh database (delete db.sqlite3, migrate, seed).")
        checks = check_results(run_full_benchmark())
        for c in checks:
            mark = self.style.SUCCESS("PASS") if c.ok else self.style.ERROR("FAIL")
            self.stdout.write(f"{mark}  {c.name}: expected {c.expected} | got {c.actual}")
        bad = [c for c in checks if not c.ok]
        if bad:
            raise CommandError(f"{len(bad)} check(s) failed")
        self.stdout.write(self.style.SUCCESS("All benchmark checks passed"))
