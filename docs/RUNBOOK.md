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

## Users and login

There is no self-signup. Create users from the command line (passwords: at least 12 characters, stored as argon2id;
sessions last 14 days, 5 failed logins per email+IP block further attempts for 15 minutes):

```bash
docker compose -f infra/docker-compose.yml exec api dclh user create you@example.org --role admin   # prompts for the password
docker compose -f infra/docker-compose.yml exec api dclh user create partner@example.org
docker compose -f infra/docker-compose.yml exec api dclh user list
docker compose -f infra/docker-compose.yml exec api dclh user set-password you@example.org          # signs the user out everywhere
```

Non-interactive (scripts, CI): `--password-env VAR` reads the password from an environment variable.
Admins see "Uruchom teraz" on the Sources page; everything except `/api/health` requires login.
The session cookie is `Secure` automatically when the request arrives over HTTPS (Caddy forwards the scheme).

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

## Grid capacity files (Phase 3, manual import)

Enea, Energa, Tauron and the PSE lists have no verified data endpoint yet (see `docs/SOURCES.md`). Until one is
verified, save the published file as CSV or XLSX with the canonical columns (`data/manual/README.md`), then:

```bash
uv run dclh import-manual enea  data/manual/enea_2026-10.csv     # or energa, tauron
uv run dclh import-manual pse_closed  data/manual/pse_closed_2026-10.xlsx   # or pse_odb, pse_avail
```

The command checks every row first and stores a snapshot under `data/raw/<source>/` only if all are valid (the
original file stays where it is). A bad row fails the whole import and prints its row number; nothing is stored,
so fix the file and run it again. The GPZ names in the
DSO file are matched to OSM substations by name: a fuzzy match needs a score of at least `grid.gpz_match_min_score`
(88) and a clear winner. Put any other match in `data/manual/gpz_mapping.csv` (it always wins).

## OSM power grid (substations and lines)

```bash
uv run dclh fetch osm_power   # streams the ~2 GB Poland extract; run on the VPS
```

A download whose size differs from `Content-Length` is rejected, as is an extract with fewer than 1,000
substations or substations outside Poland. Substation names and operators feed the GPZ matching.

The download is about 2 GB and takes minutes. To parse the snapshot you already have without downloading again:

```bash
uv run dclh fetch osm_power --reuse
```

The last line before the counts shows the peak memory of the parse. On the 2 GB VPS the first version of the parser
(one pass with a location index for every node in Poland) was killed by the out-of-memory killer (`Killed` at the end
of the log). The parser now reads in three passes and keeps only the nodes that power ways use. Run long fetches in the
background (`exec -d worker sh -c '... > /data/osm_fetch.log 2>&1'`) and check the end of the log.

## Troubleshooting

| Symptom | Check |
|---|---|
| Top bar says "Baza danych niedostępna" | `docker compose … ps db` and `logs migrate` |
| Top bar says "API niedostępne" | `logs api`; is `migrate` finished (exit 0)? |
| Map has no background ("Podkład mapowy niedostępny") | The VPS cannot reach `tiles.openfreemap.org` through Caddy `/basemap/*` (check `docker compose logs caddy`), or `VITE_BASEMAP_STYLE` points to an unreachable style |
| `/tiles/catalog` is empty | Expected until Phase 1 creates views in the `tiles` schema |
| Discovery shows ✗ everywhere | Running outside Poland, or outbound traffic is filtered |
| Scheduler restarts in a loop | It must be started with the absolute path `/usr/local/bin/supercronic` |

## Source discovery on the VPS (automatic publish)

`scripts/publish_discovery.sh` runs `dclh discover` with the browser capture, commits the new report and
`docs/SOURCES.md` only when they changed, then pulls and pushes to `main`. It never force-pushes; if the
rebase or push fails it stops and leaves the commit local.

Prerequisites on the VPS: the repo clone with push access, `uv sync --extra discovery`, Chromium and its
system libraries (`playwright install chromium` and `install-deps`), and a clean tracked tree.

Weekly cron (Mondays 05:30 Warsaw time, the VPS's local time zone):

```
30 5 * * 1 cd /home/piotr/dc-land-hunter && RAN_FROM="VPS" scripts/publish_discovery.sh >> /home/piotr/discovery.log 2>&1
```

Check the log after each run. A failed run prints the reason and exits non-zero.

## Live samples for test fixtures (run once per place, on the VPS)

`dclh capture-sample` records one small live response from EGiB (WFS, 20 parcels) and ULDK for a point, plus a
metadata file with URLs, status codes and hashes. It writes to `tests/fixtures/live/<place>/` and is meant to be
committed, so the parsers can be tested against what the services really return.

```bash
cd /home/piotr/dc-land-hunter
git pull --ff-only origin claude/bold-mayer-shpf2t
uv sync
export DCLH_CONTACT_EMAIL="<your contact address>"   # goes into the User-Agent, not into git
uv run dclh capture-sample --name "CGE Konin" --lon 18.2514 --lat 52.2360 --radius-m 300
git add tests/fixtures/live && git commit -m "Live sample: CGE Konin" && git push origin claude/bold-mayer-shpf2t
```

The Konin coordinates are an estimate ("north of Konin near a retail park"; the plot is not public). Check the
point on the Geoportal map first and pass the right `--lon`/`--lat`. Keep `--radius-m` small.

## Deploying to the VPS

The repo is checked out at `/home/piotr/dc-land-hunter`. To deploy the latest `main`:

```
cd ~/dc-land-hunter
git pull
docker compose -f infra/docker-compose.yml up --build -d
docker compose -f infra/docker-compose.yml ps
```

Hard-refresh the browser afterwards (Ctrl+Shift+R). `.env` exists only on the VPS and is not in git.
