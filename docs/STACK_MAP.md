# Stack map

Where everything lives on the VPS, what each piece does and why it is there. Read this before changing infra, deploy or data paths.
Product goal and rules: `PROJECT_BRIEF.md` and `CLAUDE.md`. Phase plan: `docs/PLAN.md`. Current state: `docs/STATUS.md`.

Last checked: 19.10.2026 (host `vps37201457`).

## 1. Runtime: one Docker Compose project

Compose file: `infra/docker-compose.yml`, project name `dclh`. Start or update with `scripts/deploy.sh` (or the command in `CLAUDE.md`).

| Service | Image | What it does | Why it exists |
|---|---|---|---|
| `db` | `postgis/postgis:16-3.5` | PostgreSQL with PostGIS. Stores jobs, sources, snapshots, search runs, sites, users, sessions, substations, the `tiles` schema. | All geometry is EPSG:2180 in PostGIS. Published on `127.0.0.1:55432` only, for local tools. |
| `migrate` | `dclh-python:local` | One-off `alembic upgrade head` (`api/alembic/versions/0001`–`0005`). | Schema changes run before API, worker and scheduler start. |
| `api` | `dclh-python:local` | FastAPI app (`api/dclh_api`, routers `auth`, `config`, `health`, `sites`, `sources`). Port 8000 inside the network only. | Serves the web app's JSON. Health check on `/api/health`. |
| `worker` | `dclh-python:local` | Runs `dclh worker --exclude-kinds check_parcel`: takes queued jobs from the `jobs` table one at a time. | Long work (EGiB downloads, searches, discovery) must not run inside a web request. |
| `check-worker` | `dclh-python:local` | Runs `dclh worker --kinds check_parcel --interval 2` (`mem_limit` 800m). Idle until the Sprawdź działkę job exists. | A person's check must not wait behind a nightly search that runs for hours. The two workers split the queue by job kind. |
| `scheduler` | `dclh-python:local` | supercronic reads `infra/crontab`. It only enqueues jobs, the worker runs them. TZ `Europe/Warsaw`. | Nightly powiat searches (01:00) and the Monday discovery check (05:00). |
| `martin` | `ghcr.io/maplibre/martin:1.16.1` | Vector tile server. Publishes only the `tiles` schema (`infra/martin/config.yaml`). Routed at `/tiles`. | Map layers come from database views the pipeline creates. The PostGIS image also has TIGER geocoder tables, which must never be exposed. |
| `web` | `dclh-web:local` | Caddy: serves the built React app from `/srv`, reverse-proxies `/api/*` and `/tiles/*`, proxies the basemap at `/basemap/*` to OpenFreeMap. Ports 80 and 443. | One origin for the browser (no CORS), security headers, HTTPS automatically when `SITE_ADDRESS` is a domain. |

Shared settings for the Python services are YAML anchors in the compose file (`x-python`). Every Python service uses the same image, the same `DATABASE_URL` and `DCLH_CONTACT_EMAIL` (sent in the User-Agent to public data services, politeness rule).

### Volumes and bind mounts

| Mount | Type | Used by | Why |
|---|---|---|---|
| `pgdata` | named volume (`dclh_pgdata`, about 195 MB) | `db` | Database files. |
| `dclh-data` | named volume (`dclh_dclh-data`, about 2.5 GB) | `api`, `worker`, `scheduler`, `migrate` at `/data` | Discovery reports and the OSM fetch log. |
| `../data/raw` | bind mount to `/data/raw` | Same Python services | Raw source snapshots (see section 3). Lives in the repo checkout, gitignored, so the host and containers read one store. |
| `caddy-data`, `caddy-config` | named volumes | `web` | Caddy certificates and config. |
| `./martin/config.yaml` | read-only bind | `martin` | Martin config. |

Important: the bind mount on `/data/raw` hides whatever is under `/data/raw` in the `dclh-data` volume. See section 6.

### Settings files

| File | Read by | In git | Purpose |
|---|---|---|---|
| `infra/.env` | Compose, for `${VAR}` substitution (DB password, `SITE_ADDRESS`, `HTTP_PORT`, `DCLH_CONTACT_EMAIL`) | No (gitignored). **VPS only.** Never overwrite. | Production values. Template: `infra/.env.example`. |
| `.env` (repo root) | Local development only: `pipeline/dclh_pipeline/settings.py` reads it from the working directory when you run `uv run` without Docker | No (gitignored). Template: `.env.example`. | Points at the compose db on `:55432`. Not used on the VPS. |
| `config/sources.yaml` | `dclh` CLI and pipeline | Yes | Every data source: id, URL, probes, plan (`mvp` / `v2` / `manual`). |
| `config/scoring.yaml` | Scoring engine | Yes | Weights and thresholds for the site score. |
| `infra/crontab` | `scheduler` | Yes | Schedule. |

## 2. Code: what is where

| Path | What | Notes |
|---|---|---|
| `api/` | FastAPI app and Alembic migrations | `dclh_api/main.py` wires routers. `export_openapi.py` writes the OpenAPI schema used by the web app's typed client. |
| `pipeline/dclh_pipeline/` | The `dclh` CLI and all data work | `cli.py` is the entry point (`discover`, `fetch`, `search`, `load-grid`, `kw-probe`, `worker`, `enqueue`, `user`, and more). |
| `pipeline/dclh_pipeline/sources/` | One module per data source: `prg`, `egib`, `gdos`, `osm_power`, `uldk`, `manual`, plus `runner.py` and `base.py` | Each source writes snapshots through `snapshots.py`. |
| `pipeline/dclh_pipeline/snapshots.py` | Content-addressed raw store | Writes `data/raw/<source>/objects/…` and `manifest.jsonl`. See section 3. |
| `pipeline/dclh_pipeline/search.py`, `powiat_search.py`, `grid_load.py`, `aoi_build.py` | Site search: seed area, clusters, hard filters, scoring, writing sites | `powiat_search` is the nightly per-powiat job. |
| `pipeline/dclh_pipeline/scoring/` | Scoring engine | Config in `config/scoring.yaml`. |
| `pipeline/dclh_pipeline/discovery.py`, `followup.py`, `kw_probe.py`, `capture.py` | Source discovery, the weekly live check, KW probe, Playwright network capture | Discovery output goes to `docs/SOURCES.md` and `data/discovery/`. |
| `pipeline/dclh_pipeline/db/models.py` | SQLAlchemy tables | `sources`, `snapshots`, `runs`, `search_runs`, `jobs`, `substations`, `users`, `sessions`. |
| `web/` | React + Vite + TypeScript SPA, all UI text in Polish | `src/pages` (Home, Settings, Sources, Site card, Login), `src/components/map` (MapLibre), `src/lib/format.ts` (Polish number and date formats), `src/api` (typed client, generated). |
| `infra/docker/` | `python.Dockerfile`, `web.Dockerfile` | Both images are built on the VPS by compose. |
| `infra/martin/` | Martin config | |
| `scripts/deploy.sh` | Safe deploy: refuses dirty tree or non-`main` branch, `git pull --ff-only`, rebuild | |
| `scripts/publish_discovery.sh` | Runs discovery and pushes the report to git | |
| `scripts/clip_prg_sample.py` | Dev helper for a sample of PRG data | |
| `data/aoi/aoi.geojson` | Area of interest (west and north of the Vistula) | Tracked in git (about 1.8 MB). |
| `data/manual/` | CSV templates for manually imported sources (PSI, investment offers) | Tracked. |
| `tests/` | `unit`, `golden`, `fixtures` (recorded responses, `live`, `egib`, `ogc`, `prg`, `pages`), `e2e` | |
| `.github/workflows/ci.yml` | CI: Python (ruff, mypy, `dclh config-check`, pytest); Web (lint, typecheck, test, build, generated API types up to date); Docker images and Playwright smoke | |

## 3. Data: the raw snapshot store

Each source fetch becomes a snapshot under `data/raw/<source>/` (on the VPS this is `/home/piotr/dc-land-hunter/data/raw`):

- `objects/<sha256[:2]>/<sha256>.<ext>`: the raw bytes, stored once per distinct content.
- `manifest.jsonl`: one line per fetch, with date, hash, URL and whether the parse succeeded. Its `as_of` moves forward even when the bytes are the same.

Why: every derived value must carry `source_id`, `as_of` and `confidence` (CLAUDE.md). Keeping the exact bytes lets a result be re-derived and audited later.

Current content (19.10.2026):

| Source | Objects | Size | Manifest lines | Notes |
|---|---|---|---|---|
| `egib` (cadastral parcels, WFS pages) | 25,117 | 4.8 GB | 38 | Biggest consumer. See section 6. |
| `osm_power` (OSM power network, Poland extract) | 2 | 2.0 GB | 1 | One large extract, expected. |
| `prg` (administrative boundaries) | n/a | 476 MB | n/a | Files dated 11–12.10.2026; read at search time (lists the powiats). |
| `gdos` (protected areas, four WFS layers in one zip) | n/a | 26 MB | n/a | Feeds the Natura 2000 hard filter. |

## 4. Schedule

| When (Warsaw time) | Job | Effect |
|---|---|---|
| Daily 01:00 | `plan_searches`, limit 8 powiats, window ends 05:00 | Queues the 8 powiats searched longest ago, after the priority powiats (the list saved in the Powiaty tab, table `powiat_priority`) that have no successful search yet. Each search fetches EGiB parcels for its seed area. |
| Monday 05:00 | `discover` | Live check of every source; reports only, never blocks. |

Manual runs: `docker compose -f infra/docker-compose.yml exec worker dclh …` (see `docs/RUNBOOK.md`). The "Uruchom teraz" button on the Sources page is for admins.

## 5. Who talks to whom

```
browser ──HTTPS──> web (Caddy)
                     ├─ /api/*     ──> api (FastAPI) ──┐
                     ├─ /tiles/*   ──> martin ─────────┤
                     ├─ /basemap/* ──> OpenFreeMap     │
                     └─ /          ──> static SPA      ▼
scheduler (cron) ──enqueue──> db.jobs <──poll── worker (dclh CLI) ──> public data services (WFS, PRG, OSM, GDOŚ, …)
                                 │                    │
                                 └──── PostGIS ◄──────┴──> data/raw (snapshots, bind mount)
```

Only `web` is exposed to the internet (ports 80 and 443). `db` is bound to localhost. Everything else is on the Compose network.

## 6. Disk usage (checked 19.10.2026: `/` at 97%, 981 MB free)

Total used: 27 GB of 29 GB.

| Area | Size | Reclaimable without loss? | Why it is big |
|---|---|---|---|
| `data/raw/egib` | 4.8 GB | No (source data) but see the cause below | 25,117 WFS pages. Every search run re-downloads the parcels for its area and stores the response again. |
| `data/raw/osm_power` | 2.0 GB | No | Poland OSM extract, one fetch. |
| `data/raw/prg` | 476 MB | No | Boundaries. |
| `.venv` (repo) | 476 MB | Rebuildable (`uv sync`) | Local Python environment. |
| `web/` (node_modules and pnpm store) | 299 MB | Rebuildable | Local frontend dependencies. |
| Docker images | 5.2 GB, 4.6 GB reclaimable | Mostly yes | Unused `node:22-slim`, `node:22.22-bookworm-slim` and `hello-world`, plus old layers. Active: `dclh-python` (1.4 GB), `dclh-web`, `postgis`, `martin`. |
| Docker build cache | 2.3 GB, 1.5 GB reclaimable | Yes | Leftovers of `up --build`. |
| Docker volume `dclh_dclh-data` | 2.5 GB | Probably, see below | Contains `raw/` (2.3 GB) that the containers cannot see, because the bind mount on `/data/raw` covers it. |
| `~/.cache/uv` | 673 MB | Yes (rebuilt on demand) | uv download cache. |
| `~/.cache/ms-playwright` | 658 MB | Only if Playwright capture is not needed | Browser binaries for discovery capture. |
| `~/.local/share/claude` | 781 MB | Old versions only | Claude CLI installs. |
| `/var/cache/apt`, `/var/lib/apt` | 0.7 GB | Yes, with sudo | Package caches. |

### Root cause: EGiB re-downloads the same tiles on every run

Measured on the 38 EGiB bundles (19.10.2026):

- The bundles reference 10,429 distinct 1 km tiles, but 24,671 tile downloads. Most tiles were fetched again in later runs (the most repeated tile was fetched 10 times). Every run downloads its tiles again; there is no tile cache.
- Each download is stored as new objects. The WFS response carries a `timeStamp` attribute, so even identical tiles hash differently. Stripping it cuts the distinct pages from 24,800 to 19,522 (about 20%). The rest of the duplication is re-downloading, not the timestamp.
- Nothing ever deletes old objects. There is no retention or prune code in `pipeline/dclh_pipeline`.
- The nightly search adds 8 powiats a night, so the store grew by roughly 5,000 objects a day from 15.10.2026 (pilot run was 14.10).

Fixes:

1. Done (PR #46, branch `fix/egib-dedup-timestamp`): strip the response `timeStamp` before storing EGiB pages. Stops the timestamp-only duplicates for new fetches; existing objects are unchanged.
2. Not done, needs a decision: reuse tiles already fetched recently instead of downloading them again. This is the bigger saving (about 58% of tile downloads were repeats) but it trades freshness for cost, so `as_of` rules need to be agreed.
3. Not done: a retention rule for `egib` objects that no longer appear in any manifest line marked good, plus a `dclh` command to prune them.
4. Remove the stale `raw/` in the `dclh-data` volume after confirming nothing reads it.
5. Prune Docker: unused images and build cache.

## 7. Operations quick reference

- Deploy: `scripts/deploy.sh` (on the VPS). Refuses to run with local changes or off `main`.
- Logs: `docker compose -f infra/docker-compose.yml logs -f <service>`.
- Health: `curl -s localhost/api/health` (through Caddy) or the `api` container health check.
- Create a user: `docker compose -f infra/docker-compose.yml exec api dclh user create …`.
- Procedures and troubleshooting: `docs/RUNBOOK.md`. Source status and endpoints: `docs/SOURCES.md`. Open risks: `docs/PLAN.md` (R7 is the EGiB cost).
