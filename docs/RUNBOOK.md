# Runbook

How to run, develop and operate DC Land Hunter. Production deployment (Polish VPS, HTTPS, backups)
is added in Phase 6.

## Run the whole stack (one command)

Requirements: Docker with Compose v2.24+.

```bash
cp infra/.env.example infra/.env        # optional: every value has a local default
docker compose -f infra/docker-compose.yml up --build
```

Open http://localhost:8080. Services:

| Service | What it does | Port (inside) |
|---|---|---|
| `db` | PostgreSQL 16 + PostGIS 3.5 | 5432 (host: 127.0.0.1:55432) |
| `migrate` | `alembic upgrade head`, then exits | – |
| `api` | FastAPI under `/api` (docs at `/api/docs`) | 8000 |
| `worker` | Polls the `jobs` table and runs jobs one at a time | – |
| `scheduler` | supercronic; enqueues scheduled jobs (`infra/crontab`, Europe/Warsaw) | – |
| `martin` | Vector tiles under `/tiles` (publishes only the `tiles` schema) | 3000 |
| `web` | Caddy: static web app, proxies `/api` and `/tiles` | 80 (host: 8080) |

Useful commands:

```bash
docker compose -f infra/docker-compose.yml ps
docker compose -f infra/docker-compose.yml logs -f api worker
docker compose -f infra/docker-compose.yml exec api dclh enqueue discover   # queue a job by hand
docker compose -f infra/docker-compose.yml down        # stop (data volumes are kept)
docker compose -f infra/docker-compose.yml down -v     # stop and DELETE all data
```

## Local development (without Docker for the app)

```bash
uv sync                                  # Python 3.12 workspace (pipeline + api)
cp .env.example .env                     # DATABASE_URL points at the compose db on :55432
docker compose -f infra/docker-compose.yml up -d db migrate
uv run uvicorn dclh_api.main:app --reload --port 8000
cd web && pnpm install && pnpm dev       # http://localhost:5173, proxies /api to :8000
```

Checks (run before every commit; CI runs the same):

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest
cd web && pnpm lint && pnpm typecheck && pnpm test && pnpm build
```

Database tests (`tests/unit/test_db.py`) run when `DATABASE_URL` reaches a PostGIS database and are
skipped otherwise. **They downgrade and re-create the schema**, so point them at a scratch database, never
at one with real data.

End-to-end smoke tests (Playwright, against the running stack):

```bash
cd tests/e2e && pnpm install && pnpm exec playwright install chromium && pnpm test
```

## API types for the web app

The TypeScript types are generated from the FastAPI schema. After changing an API model:

```bash
uv run python -m dclh_api.export_openapi    # writes api/openapi.json (a test fails if it is stale)
cd web && pnpm gen:api                      # writes web/src/api/schema.d.ts (CI fails if it is stale)
```

## Source discovery (Phase 0 spike, and the weekly live check)

Must run from a machine in Poland; see `docs/SOURCES.md`.

```bash
uv sync --extra discovery && uv run playwright install chromium
DCLH_CONTACT_EMAIL=you@example.com uv run dclh discover --capture-network --write-docs --ran-from "…"
```

`--source egib --source gdos` limits the run. The scheduler runs the same check every Monday at 05:00
(without `--capture-network`) and stores the report in the data volume; the Sources page shows the latest
status from either place.

## Database migrations

Migrations are hand-written in `api/alembic/versions/` (no autogenerate for PostGIS types).

```bash
uv run alembic -c api/alembic.ini upgrade head
uv run alembic -c api/alembic.ini revision -m "add substations"   # then edit the new file
```

## Adding a source connector

1. Add or update the entry in `config/sources.yaml` (id, plan, cadence, probes).
2. Create `pipeline/dclh_pipeline/sources/<id>.py` with a `Source` subclass: `fetch()` saves the raw response
   through `SnapshotStore`, and `parse()` returns a normalised (Geo)DataFrame in EPSG:2180.
3. Save a small real response as a fixture under `tests/fixtures/<id>/` and test `parse()` against it.
4. Run it through `run_source()`, which handles failure, fallback to the last good snapshot and the
   `SOURCE_FAILURE` event.

## Troubleshooting

| Symptom | Check |
|---|---|
| Top bar says "Baza danych niedostępna" | `docker compose … ps db` and `logs migrate` |
| Top bar says "API niedostępne" | `logs api`; is `migrate` finished (exit 0)? |
| Map has no background ("Podkład mapowy niedostępny") | The OpenFreeMap style URL is unreachable; set `VITE_BASEMAP_STYLE` at build time |
| `/tiles/catalog` is empty | Expected until Phase 1 creates views in the `tiles` schema |
| Discovery shows ✗ everywhere | Running outside Poland, or outbound traffic is filtered |
| Scheduler restarts in a loop | It must be started with the absolute path `/usr/local/bin/supercronic` |
