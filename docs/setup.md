# Setup

Verified on Windows 11 with Python 3.13.5, Docker 28.3.2, Compose v2.38.2.

## Prerequisites

| Tool | Version | Notes |
|---|---|---|
| Python | ≥ 3.12 | 3.13 tested |
| Docker Desktop | ≥ 28 | Must be **running** — Compose fails with a named-pipe error if not |
| Git | any | |

No cloud accounts, no API keys. Every data source this project uses is free and
registration-free; that constraint is what defines the source set.

## 1. Python environment

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -U pip
pip install -e ".[dev]"
```

Bash / macOS / Linux: `source .venv/bin/activate` instead of the Activate call.

Verify:

```powershell
python -c "import duckdb, psycopg, clickhouse_connect, pyarrow, pandas; print('ok')"
```

## 2. Environment file

```powershell
Copy-Item .env.example .env
```

Then edit `.env` and change both passwords. Compose reads this file
automatically. `.env` is gitignored; `.env.example` is the tracked template.

## 3. Start the data stores

```powershell
docker compose up -d
docker compose ps          # both services should report (healthy)
```

First start creates the volumes and runs the schema bootstrap in
`infra/*/init/`. **Those scripts run only against an empty volume** — see
"Re-applying schema changes" below.

## 4. Verify

Postgres — expect `macro_observation`, `macro_release`, `macro_series`:

```powershell
docker compose exec postgres psql -U fx -d fx -c "\dt"
```

Confirm `known_at` came through as `timestamptz`, not `date`:

```powershell
docker compose exec postgres psql -U fx -d fx -c "\d macro_observation"
```

ClickHouse — expect `tick_flag`, `tick_raw`:

```powershell
docker compose exec clickhouse clickhouse-client --query "SHOW TABLES FROM fx"
```

## Common commands

| Task | Command |
|---|---|
| Start stack | `docker compose up -d` |
| Stop, keep data | `docker compose down` |
| Stop, **destroy** data | `docker compose down -v` |
| Service status | `docker compose ps` |
| Logs | `docker compose logs -f clickhouse` |
| Postgres shell | `docker compose exec postgres psql -U fx -d fx` |
| ClickHouse shell | `docker compose exec clickhouse clickhouse-client` |
| Lint | `ruff check .` |
| Format | `ruff format .` |
| Tests | `pytest` |
| Single test | `pytest tests/test_foo.py::test_bar` |
| Acceptance suite only | `pytest -m acceptance` |
| Skip tests needing the stack | `pytest -m "not integration"` |

`pytest` currently exits with code 5 (no tests collected). That is expected
until Phase 0.

## Troubleshooting

**`open //./pipe/dockerDesktopLinuxEngine: The system cannot find the file specified`**
Docker Desktop is not running. Start it and wait for the whale icon to settle.

**Port 9000 already in use.** ClickHouse's native port collides with a lot of
local dev servers. Change `CLICKHOUSE_NATIVE_PORT` in `.env` and restart. Port
5432 collides with a local Postgres install; same fix via `POSTGRES_PORT`.

**Re-applying schema changes.** `docker-entrypoint-initdb.d` scripts run *only*
on first initialisation of an empty volume. Editing a file in `infra/` and
restarting does nothing. Either apply the change by hand, or wipe and rebuild:

```powershell
docker compose down -v
docker compose up -d
```

`down -v` destroys all ingested data. Once real tick data exists, prefer
hand-applied migrations.

**Timezones.** Both containers are pinned to UTC and Postgres additionally sets
`PGTZ=UTC`. Do not change this. Every point-in-time comparison depends on there
being no ambiguity about the session zone, and silent timezone bugs are flagged
as a high-likelihood risk in `planning.md` §10.
