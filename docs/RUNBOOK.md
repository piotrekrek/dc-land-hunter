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
| `api` | FastAPI under `/api` (docs at `/api/docs` only with `DCLH_API_DOCS=1`) | 8000 |
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

Checks (run before every commit; CI runs the same; `ci.yml` runs Python and web on every PR except docs-only ones, `stack.yml` the
Docker + Playwright smoke test on `main` and on PRs that touch `web/`, `api/`, `infra/` or `tests/e2e/`; GitHub's free minutes for a
private repo are limited, see STATUS.md):

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest
cd web && pnpm lint && pnpm typecheck && pnpm test && pnpm build
```

Database tests (`migrated_db` in `tests/conftest.py`) **downgrade and re-create the schema**, so they never
use `DATABASE_URL`. They run only when `DCLH_TEST_DATABASE_URL` is set and are skipped otherwise. The run
aborts (exit 3) if that database's name does not end in `_test` or its `sites` table has rows. Use a
throwaway container, never the compose `db` service (on the VPS it is production):

```bash
docker run -d --rm --name dclh-test-pg -e POSTGRES_USER=dclh -e POSTGRES_PASSWORD=dclh -e POSTGRES_DB=dclh_test -p 127.0.0.1:55439:5432 postgis/postgis:16-3.4
DCLH_TEST_DATABASE_URL=postgresql+psycopg://dclh:dclh@127.0.0.1:55439/dclh_test uv run pytest
docker rm -f dclh-test-pg
```

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

## Investor profile verdicts (backfill)

`dclh search` writes the profile verdicts with every site. Sites stored before migration 0013 have none until:

```bash
uv run alembic -c api/alembic.ini upgrade head
uv run dclh reprofile        # recomputes every stored site from detail.features, no new search
```

Run it again after editing `config/profiles.yaml`. Until then a profile filter does not hide those rows (no verdict is not red).

## Adding a source connector

1. Add or update the entry in `config/sources.yaml` (id, plan, cadence, probes).
2. Create `pipeline/dclh_pipeline/sources/<id>.py` with a `Source` subclass: `fetch()` saves the raw response
   through `SnapshotStore`, and `parse()` returns a normalised (Geo)DataFrame in EPSG:2180.
3. Save a small real response as a fixture under `tests/fixtures/<id>/` and test `parse()` against it.
4. Run it through `run_source()`, which handles failure, fallback to the last good snapshot and the
   `SOURCE_FAILURE` event.

## Grid capacity: DSO and PSE data (Phase 3)

Tauron and Energa have connectors (endpoints verified 19.10.2026, `docs/SOURCES.md`). Run them from the VPS:

```bash
uv run dclh fetch tauron    # 513 GPZs with MW, group and location; about 6 POST requests
uv run dclh fetch energa    # the statutory load-capacity PDF, per group of 110 kV nodes
```

The PSE lists are monthly XLSX files on the PSE page "Wykaz obiektów planowanych do przyłączenia" (the date is in
the file name). Download the newest ones and import them as they are:

```bash
uv run dclh import-manual pse_closed ~/20260204_Lista_rozdzielni-brak_mozliwosci_przylaczenia.xlsx
uv run dclh import-manual pse_odb ~/Informacje_publikowane_zgodnie_z_Art_7_ust_8l_ustawy_PE_stan_na_31_08_2026.xlsx
rm ~/Informacje_publikowane_*.xlsx   # it names applicants; only the reduced table is stored
```

The planned load stations come from PSE's monthly PDF "Informacja o dostępności mocy przyłączeniowej do sieci
przesyłowej" (link on `…/krajowy-system-elektroenergetyczny/informacja-o-dostepnosci-mocy-przylaczeniowej`). Import
the PDF as it is; the stations are placed from `data/manual/pse_planned_station_coords.csv` and any station without a
row there is reported in the log and left out:

```bash
uv run dclh import-manual pse_avail ~/informacja_o_dostepnosci_mocy_przylaczeniowej_do_sieci_przesylowej.pdf
```

Sites already stored pick the new layer up at their next scoring; a new search or check uses it at once.

`pse_odb` keeps only the `ODB` rows and only station, voltage, MW, status and dates: the applicant column is
never read, and the snapshot is the reduced CSV, not PSE's file. Enea (owner decision) and anything else without
a connector still go through the canonical CSV/XLSX columns (`data/manual/README.md`):

```bash
uv run dclh import-manual enea  data/manual/enea_2026-10.csv     # or energa, tauron as a fallback
uv run dclh import-manual pse_avail  data/manual/pse_avail_2026-10.csv   # planned stations as a CSV with coordinates (or PSE's PDF)
```

The command checks every row first and stores a snapshot under `data/raw/<source>/` only if all are valid (the
original file stays where it is). A bad row fails the whole import and prints its row number; nothing is stored,
so fix the file and run it again.

`dclh search` uses the newest good snapshot of each of these sources; a source never loaded stays "brak danych".
Tauron gives locations. Energa and Enea GPZ names are matched to OSM GPZs, and the PSE stations to OSM 220/400 kV
nodes, by name: a fuzzy match needs a score of at least `grid.gpz_match_min_score` (90), a clear winner and no
station of another DSO. Check the result, and put any other match in `data/manual/gpz_mapping.csv` (it always
wins; it is copied into the image, so rebuild after editing it):

```bash
uv run dclh gpz-report   # match rate per source; CSV of every name and its reason under data/reports/
```

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

After the row counts, `fetch osm_power` prints a breakdown: substations by class, how many 110 kV GPZs have a name
and an operator tag (and which DSO), 110 kV substations owned by someone else (powered-brownfield candidates), and
lines by voltage. The first real numbers go into `docs/STATUS.md`.

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

## Discovery follow-up for the open sources (run on the VPS)

`dclh discover` only says a source is reachable. `dclh discover-followup` asks the next question for the sources that
block the hard filters and the zoning layer, and prints a verdict for each (it needs `DCLH_CONTACT_EMAIL`, which the
containers take from the VPS `infra/.env`):

```bash
cd ~/dc-land-hunter
docker compose -f infra/docker-compose.yml exec worker dclh discover-followup
```

It takes a few minutes (one request per second per host). The report is printed first, then saved to
`<data dir>/discovery/followup-<date>.md` (`/data/discovery` in Docker, `data/discovery` on the host). If the save fails
it says so and the printed report is still complete. Keep a copy of the output with `| tee ~/followup.md`. Sections:
1. **bdot10k**: does the powiat index (`BDOT10k_powiaty`) carry a download link per powiat?
2. **kimpzp**: is there a WFS next to the WMS, and what does the WMS answer at a parcel in the Reguły plan area?
3. **isok**: which WFS, WMS or ATOM services does the INSPIRE page list, and do they answer GetCapabilities?
4. **ru**: which API addresses are in the JavaScript of the Rejestr Urbanistyczny modules?

A section that fails prints `FAILED` and a verdict "could not be checked"; the others still run. Paste the report back,
then record the confirmed endpoints in `docs/SOURCES.md`. No connector uses an endpoint before that.

## KW number coverage probe (test ride, run on the VPS)

`dclh kw-probe` measures how many parcels of the scored sites show a land register (KW) number in the public EGiB
services. For each parcel it asks ULDK for a point inside it and KIEG (`integracja.gugik.gov.pl`) GetFeatureInfo at that
point. It writes nothing to the database. Each parcel costs about 3 s, so the defaults take the top 50 sites and stop at
300 parcels (about 15 minutes):

```bash
cd ~/dc-land-hunter
docker compose -f infra/docker-compose.yml exec worker dclh kw-probe | tee ~/kw-probe.txt
# options: --sites 50 --max-parcels 300 --powiat 1421
```

The report shows each status as a share of all parcels (`kw`, `answer_without_kw`, `no_answer`, `error`), a table per
powiat, and how many sites have at least one KW number. It ends with one raw answer per powiat and status. It is saved
to `<data dir>/discovery/kw-probe-<date>.md` and `.csv`. Paste the report back. The raw samples show whether a
county's answer has a KW field under another format, which the pattern `AAAA/NNNNNNNN/C` would miss.

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

The repo is checked out at `/home/piotr/dc-land-hunter`. **No edits on the VPS, deploy only from `main`.**
Every change goes through a pull request and is merged to `main` first; the VPS only ever pulls.
Editing files there (it happened with the OSM memory and voltage work) makes the checkout drift from git and
the next `git pull` fails or silently loses the change.

```
cd ~/dc-land-hunter
scripts/deploy.sh
```

The script refuses to run on a dirty tree or on any branch other than `main`, fast-forwards to
`origin/main`, rebuilds, shows `docker compose ps`, removes dangling images and caps the BuildKit cache at 2 GB
(`DCLH_BUILD_CACHE_KEEP=3GB scripts/deploy.sh` changes the cap), prints the free disk and the commit it deployed.
Docker has no "keep the last N images" setting; tagged images (`postgis`, `martin`, the `node` build base) are
deliberately kept, so they are not pulled again from Docker Hub. If it refuses because
of local changes, do not stash them blindly: look at `git diff`, move anything worth keeping into a branch
on your own machine and open a pull request, then `git checkout -- .` on the VPS.

Hard-refresh the browser afterwards (Ctrl+Shift+R). `infra/.env` exists only on the VPS and is not in git.

Snapshot manifests (`data/raw/<source>/manifest.jsonl`) store paths relative to the data folder, and the
compose file bind-mounts the repo's `data/raw` at `/data/raw`. The host and the containers therefore share
**one** snapshot store: `uv run dclh ...` on the host and `docker compose exec worker dclh ...` see the same
snapshots. The container user `dclh` is uid 1000, the same as the VPS owner; keep it that way or the mount
becomes unwritable. Other runtime output (discovery reports, logs) stays in the `dclh-data` volume.

## Production checklist

Before the VPS serves real users, the owner confirms these in `infra/.env` on the VPS (the file is not in git, so the
compose defaults apply to anything unset):

- `POSTGRES_PASSWORD` is set to a strong value. The compose default is `dclh`, which must not reach production.
- `DCLH_CONTACT_EMAIL` is a real address. The pipeline puts it in the User-Agent of its requests to public sources,
  and the default is a placeholder (`.invalid`).
- `SITE_ADDRESS` is the public domain, so Caddy serves HTTPS with its own certificate. The default `:80` is plain HTTP.
- Port 8080 (`HTTP_PORT`) must not be reachable from the internet. The compose file publishes it on all interfaces
  (`"${HTTP_PORT:-8080}:80"`), and Docker-published ports bypass UFW, so `ufw deny 8080` does not close it. Check
  from outside (for example `curl -m 5 http://<vps-ip>:8080/` from another machine), then set
  `HTTP_PORT=127.0.0.1:8080` in `infra/.env` if it answers, and recreate the web container.
