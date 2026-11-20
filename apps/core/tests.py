import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.accounts.management.commands.seed_users import seed_users
from apps.core.benchmark import check_results, run_full_benchmark
from apps.inventory.management.commands.seed_inventory import seed_inventory
from apps.masters.management.commands.seed_masters import seed_masters

pytestmark = pytest.mark.django_db


@pytest.fixture
def seeded():
    seed_users()
    seed_masters()
    seed_inventory()


def test_section_5_results_table(seeded):
    """Every row of 'Results we will check', evaluated against live data after steps 1-8."""
    checks = check_results(run_full_benchmark())
    assert len(checks) == 11
    failed = [f"{c.name}: expected {c.expected!r}, got {c.actual!r}" for c in checks if not c.ok]
    assert not failed, failed


def test_run_benchmark_command(seeded, capsys):
    call_command("run_benchmark")
    out = capsys.readouterr().out
    assert out.count("PASS") == 11 and "FAIL" not in out and "All benchmark checks passed" in out
    with pytest.raises(CommandError):  # refuses to run twice
        call_command("run_benchmark")


def test_seed_commands_are_rerunnable(seeded):
    for cmd in ("seed_users", "seed_masters", "seed_inventory"):
        call_command(cmd)
    assert len(check_results(run_full_benchmark())) == 11
