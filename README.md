# Order-to-Audit System (work in progress)

See [docs/PLAN.md](docs/PLAN.md) for decisions, architecture and milestones.

## Run locally (no Docker)

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python manage.py migrate && .venv/bin/python manage.py seed_users && .venv/bin/python manage.py seed_masters && .venv/bin/python manage.py seed_inventory
.venv/bin/python manage.py runserver
```

## Run with Docker Compose (PostgreSQL)

```bash
docker compose up --build
```

Test logins (password `demo12345`): `salesman_a`, `salesman_b`, `admin`, `production`, `accountant`, `owner`.

## Status

Done: M1 scaffold and roles; M2 masters, units, BOM; M3 stock ledger, purchases, stock reports. Docker Compose files are written but not yet run.
