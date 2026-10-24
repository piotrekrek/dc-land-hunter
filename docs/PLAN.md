# DC Land Hunter: build plan

This plan turns `PROJECT_BRIEF.md` into phases with concrete tasks, acceptance checks and risks.
It is a living document: update it when a phase finishes or the plan changes.

Last updated: 19.10.2026 (Phase 1 closed; source verification round 2); 12.10.2026 (plan review: vertical slice gate, grid-first seed, owner count in scope; Phase 3 code on fixtures, acceptance pending live data).

**Status words used in checklists:** `[x]` = verified on live data; `[f]` = done on fixtures/test data only; `[ ]` = not done.
A phase is finished only when its acceptance check passes on live data.

## Key decisions

| Topic | Decision | Why |
|---|---|---|
| Python layout | One `uv` workspace at repo root, with two packages: `pipeline` (`dclh_pipeline`) and `api` (`dclh_api`). The API imports scoring and config code from the pipeline package. | Scoring runs both in batch (pipeline) and on the fly (Sprawdź działkę). One shared implementation means no drift between the two. |
| Python version | 3.12, pinned in `.python-version`. | Brief. |
| Geo stack | PostGIS for heavy spatial work. GeoPandas, Shapely 2 and pyogrio for per-source parsing and unit-testable geometry. pyosmium for the OSM extract. | Boring and well known. PostGIS does the big joins; Python stays testable. |
| CRS | EPSG:2180 for all storage and computation. 4326 only in tile and API output. | Metric distances and areas, matching GUGiK data. |
| DB migrations | Alembic, with hand-written migrations (no autogenerate for PostGIS types). | Predictable DDL. |
| Tiles | Martin serving PostGIS tables and functions. | Brief. |
| Web | React 18, TypeScript, Vite, Tailwind, shadcn/ui (copied components), MapLibre GL, TanStack Query and Table, react-router. pnpm. | Brief. |
| Basemap | OpenFreeMap "dark" vector style (matches the dark UI) (free, no key, OSM data), configurable through `VITE_BASEMAP_STYLE`. The Geoportal orthophoto WMTS is used on the site card. | No paid service. Calm neutral look. |
| API client | `openapi-typescript` generates types from FastAPI's OpenAPI schema into `web/src/api/schema.d.ts`. | Brief: generate the TS client. |
| Jobs | A `jobs` table polled by the worker (no broker). The worker is the pipeline image running supercronic. | Brief. |
| Auth | argon2 password hashes with server-side sessions in an http-only cookie. Phase 4 adds the UI; tables come in Phase 1. | Brief. |
| Contact identity | The User-Agent contact email comes from `DCLH_CONTACT_EMAIL` (env), never hard-coded. | The repo is public. |

## Phase 0: Setup and source discovery

Goal: one-command local stack, green CI, hello-world map, and a verified status for every MVP source.

Tasks:
- [x] Repo layout from brief 9.1, `CLAUDE.md`, this plan.
- [x] Python workspace: config loaders (`config/scoring.yaml`, `config/sources.yaml`) with pydantic validation.
- [x] Connector framework: `pipeline/sources/base.py` (`fetch()` → snapshot with date + sha256 → `parse()`), polite HTTP client (User-Agent with contact email, ≤1 req/s per host, robots.txt, retries, cache).
- [x] Discovery spike tooling: `dclh discover` probes every MVP source (OGC GetCapabilities, robots.txt, HEAD/GET, content type, response time) and writes `data/discovery/<date>.json` plus a Markdown table for `docs/SOURCES.md`.
- [x] FastAPI app: `/api/health`, `/api/sources`. Alembic migration with PostGIS plus `sources`, `snapshots`, `runs`, `jobs`.
- [x] Web: Polish app shell (nav for all 8 screens, placeholder pages), MapLibre map centred on the AOI, Polish number and date formatters, light and dark themes.
- [x] Docker Compose: `db` (PostGIS 16-3.4), `martin`, `api`, `web` (Caddy serving the static build and proxying `/api` and `/tiles`), `worker` (supercronic).
- [x] CI: ruff, mypy, pytest, eslint, tsc, vitest, image builds, compose smoke test with Playwright.
- [x] **Run `dclh discover` from a machine in Poland** and record ✓/⚠/✗ per source in `docs/SOURCES.md` (19.10.2026: 20 ✓, 1 ⚠ `uke`; `enea` manual CSV only by owner decision).
- [ ] Research golden-set coordinates (Hillwood Reguły, Tauron Jaworzno, Bełchatów/Domiechowice, CGE Konin, Data4 Jawczyce, Atman Duchnice, Switch Mory) and verify them on Geoportal.

Acceptance: `docker compose up` starts everything; CI green; every MVP source has a verified status.

## Phase 1: Base and grid layers

**Finished 19.10.2026.** Acceptance met on live data: the per-voivodeship counts are in STATUS.md ("Phase 1 close-out"),
and the AOI tests pass. Toruń was decided on 20.10.2026: it is in the AOI via the include list in `config/aoi.yaml`.

Tasks:
- [x] `prg` connector: voivodeship, powiat and gmina boundaries with TERYT → `admin_units` (`dclh load-admin`: 16 / 380 / 2,479 units).
- [x] `osm_power` connector: download the Geofabrik `poland-latest.osm.pbf` (with ETag/size check), extract with pyosmium into substations, lines and generators. (`osm_land`, for landuse and buildings, moved to Phase 2.)
- [x] AOI builder (`dclh aoi build`): country polygon from PRG, Vistula main stem from OSM (`waterway=river` + `name=Wisła`, longest connected chain), extended upstream to the border, split, keep the western/northern part. Write `data/aoi/aoi.geojson`.
  - Unit tests: Warsaw left bank, Kraków north bank, Katowice, Łódź, Poznań, Gdańsk-Wrzeszcz (west of the Vistula) are in; Bielsko-Biała, Lublin, Białystok and Warsaw Praga are out.
- [x] Grid model: classify substations (`GPZ_110_SN`, `NODE_220_400`, `OTHER`, `UNKNOWN_VOLTAGE`), derive DSO from the `operator` tag, lines by voltage. (The DSO territory-polygon fallback and the count of independent circuits moved to Phase 3.)
- [x] Tables: `admin_units`, `substations`, `lines`, `buildings`, `users`, `settings` (migration 0002; `buildings` is still empty, see Phase 2).
- [x] Martin layers for the AOI, lines by voltage and substations by class. Map toggles.
- [x] Sources page (Źródła danych) live from the `sources` and `runs` tables. "Uruchom teraz" creates a job (admin only, after login).
- [x] Docs: substation counts per voivodeship compared with plausibility references (`dclh grid-counts`; PSE 107 tagged nodes vs 110 published stations, Tauron 512 OSM GPZs vs 513 in its API).

Acceptance: plausible substation counts are documented and AOI tests pass.

## Phase 1.5: Vertical slice (gate before more phases)

Goal: prove the whole chain on real data for one powiat before widening any layer. Phase 3 live work does not continue until this passes.

Tasks:
- [x] Real AOI (`dclh aoi build`) loaded on the VPS (about 188,000 km²). PRG admin units loaded on 19.10.2026 (`dclh load-admin`).
- [x] OSM power layers loaded (13,200 substations, 16,105 lines).
- [x] `dclh search <teryt>` chains: seeds → grid pre-filter → EGiB parcels → cluster → constraints → score → write sites. Missing layers stay `missing`, never zero.
- [ ] Pilot powiat: the one containing Reguły (Pruszków). Check whether the Hillwood site appears and where it ranks.
- [ ] Measure per powiat: EGiB request count, runtime, peak memory. Extrapolate to the AOI and fix the weekly-run plan (R7).
- [ ] "Sprawdź działkę" minimal version (pulled forward from Phase 4): one parcel via ULDK → live layers → the same scorer. Fast test of every layer on real land.

Acceptance: one powiat runs end to end on the VPS; the sites list in the app shows real results; the measurements are recorded in STATUS.md.

## Phase 2: Candidates, sites, constraints, scoring v1

Tasks:
- `ru` connector: MPZP designations (P, PU, P/U, U/P, E, infrastructure) and plan ogólny SG zones from the APP GML. Zone codes are checked against the APP schema. `kimpzp` WMS GetFeatureInfo fallback, sampled per candidate.
- Fallback zones from OSM and BDOT10k industrial areas (`zoning=unknown`). Powered-brownfield detection.
- `osm_land` connector (moved from Phase 1, 19.10.2026): landuse and buildings from the same Geofabrik extract. Feeds the fallback zones above and the tile skip for built-up areas in `dclh search`.
- `buildings` table (moved from Phase 1, 19.10.2026): residential buildings from BDOT10k (per powiat), cached, so the housing distance does not need a live request per search.
- **Grid-first seed** (decision 12.10.2026, runs next to the zoning seed): candidate land is every parcel cluster of at least `ha_min` inside the grid buffers (110 kV line ≤ 3 km, GPZ ≤ 5 km, node ≤ 25 km), whether or not a plan exists. Un-zoned or unplanned land gets `zoning=unknown` (30 points, `missing` or `estimated` confidence) and a flag "wymaga zmiany planu"; it can rank but never above a comparable site with production zoning. Seeds are merged by parcel id so a site found twice is scored once, and the seed(s) that found it are stored.
- Re-run the seeds after 30.11.2026, when the Rejestr Urbanistyczny is complete.
- Grid pre-filter (3 km line / 5 km GPZ / 25 km node, configurable).
- Owner count (decision 12.10.2026: in scope, automatic): `owners_count` per site from the parcel register if a verified source gives it; store the count only, never names or other personal data. Feeds "few owners" (value driver) and "too many owners" (deal killer, threshold in `scoring.yaml`). If no source is verified, the fact is `missing`. Needs an owner check before any personal data is read or stored (brief 0, 9.6).
- `egib` connector: parcels by bbox tiles inside the pre-filtered zones, with retries and cache. `uldk` for single lookups.
- Site clustering: adjacent parcels (touching or gap ≤ 5 m) per zone, area, Polsby-Popper compactness, stable `site_id`, >80 ha split suggestion, `expansion_ha`.
- Constraint connectors: `gdos`, `isok`, `midas`, `nid`, `kiug` (sampled), residential distance. A `constraints` table with % area and minimum distances.
- Scoring engine: hard filters, weighted criteria, a per-criterion `ScoreItem {value, points, max_points, confidence, source_id, as_of, explanation_pl}`, data completeness and the upside score. Hypothesis tests for monotonicity.
- API: sites list (filters, pagination, sort), site detail. Web: ranked card list, map choropleth, site card with score breakdown, filter state in the URL.
- Golden-set check (`tests/golden/sites.yaml`) and the first top-30 manual review (admin view to label sites "sensowna / niesensowna").

Acceptance: a full AOI run completes, the golden-set report is produced and the review is logged in STATUS.md.

## Phase 3: Grid capacity and PSE data

Tasks:
- [x] `energa` and `tauron` connectors on live data (19.10.2026): Tauron JSON search endpoint (513 GPZs with locations), Energa statutory PDF (396 stations in 48 groups of 110 kV nodes). `enea` stays manual CSV by owner decision. Manual CSV (`dclh import-manual`) remains the fallback for all three.
- [x] GPZ name matching (rapidfuzz) plus `data/manual/gpz_mapping.csv`, with the match score stored. Matching needs OSM substation names (Phase 1 OSM connector).
- [x] `pse_closed` and `pse_odb` (both XLSX as published) through `dclh import-manual`, located on OSM 220/400 kV nodes by name; the art. 7 ust. 8l applicant column is never stored. `pse_avail` (planned stations, PDF): [ ] still to geocode by hand into `data/manual/` (canonical CSV with coordinates).
- [x] Grid confidence badges: Opublikowana moc / Bliskość sieci / Brak danych (list and card, from Phase 2).
- [~] OSM power connector (`osm_power`): written and tested on a generated file; first real run on the VPS. Brings substation names and operators.
- [x] GPZ match report (`dclh gpz-report`, the match rate per source) and `dclh search` loading these layers; run on the VPS 19.10.2026 (results in `docs/STATUS.md`).
- [ ] DSO territory-polygon fallback (moved from Phase 1, 19.10.2026): 399 of 1,594 OSM GPZs have no DSO in their `operator` tag.
- [ ] Count of independent 110 kV circuits per station (moved from Phase 1, 19.10.2026): `lines.circuits` is still empty.

Acceptance: at least 70% of GPZs in Enea and Energa areas are matched to published capacity (or the reason is documented), and scores use them.

## Penalty sources (21.10.2026)

Four penalties had no source and showed "Brak danych" (0 pts) on every card. Owner decisions 21.10.2026: gas and
airport OLS from an OSM proxy (no public source exists); soil from KIUG as an estimate that feeds only the
class III penalty, never the I–II exclusion. One PR each, stacked (#76 → #77 → #78), merged in that order.

Tasks:
- [x] Landscape parks and OChK from GDOŚ, penalty only, park buffer zones ("otulina") dropped (#76). Deploy step: `dclh fetch gdos`.
- [x] Gas pipelines (zone `proxies.gas_zone_m`) and runways (ICAO Annex 14 radius by length) from the `osm_power` extract, `estimated` (#77).
- [x] Class III soil share from KIUG point samples on kept sites, `estimated`, at most `search.soil_points_per_site` per site (#78).
- [ ] After merge and deploy: `dclh fetch gdos`, then `dclh search <TERYT> --write` for the 8 powiats with sites (0201, 0202, 0203, 0205, 0206, 0213, 0215, 1421). This also fills the already-wired flood 0,2%, mining terrain and housing penalties. KIUG adds about 6 hours over all 8.
- [ ] Owner: tune `proxies.gas_zone_m` (15 m) once the controlled-zone widths are checked.

Acceptance: on a rescored site card none of the seven penalties shows "Brak danych" unless the source has nothing there (e.g. KIUG parcels a starosta does not publish).

## Phase 4: Investor workflow

Tasks: Sprawdź działkę (ULDK + cached layers, under 30 s), kanban (Moje projekty) with drag and drop, notes with RODO notice and full delete, watches, XLSX export, PDF one-pager (WeasyPrint), connection-cost calculator (configurable figures, flagged "wymaga weryfikacji prawnej"), login UI, keyboard shortcuts.

Acceptance: E2E flows pass (login, filter + open card, check parcel, move deal, export, watch → Nowości).

## Phase 5: Monitoring

Tasks: snapshot diffs → events (all nine types), Nowości feed with "mark as seen", watches, weekly digest email (SMTP, Monday 07:00), scheduled runs (Monday 05:00 Europe/Warsaw plus per-source cadence), manual offer CSV import (`kowr` is manual-only, no connector) with template.

Acceptance: two consecutive fixture runs produce the expected events, and a test digest renders correctly.

## Ops: job health panel ("Zadania")

**Problem.** The nightly search failed for a week (14–20 Oct, 39 failed jobs) and nobody saw it; it was found by querying
`jobs` by hand. `search_runs` cannot show it: a failed job rolls back, so it never updates its powiat's row.

**Users.** Admins only (the owner). Members have no use for it.

**Must have**
1. `GET /api/ops/jobs` (admin only): job counts for the last 24 h by status, the powiats whose *latest* search job
   failed (with the error), and the last 50 jobs. Read-only, from `jobs` and `admin_units`; no new tables.
2. A "Zadania" page for admins: three blocks (counts, powiats needing attention, recent jobs with the error text).
3. An in-app alert: the nav entry shows a red count when a powiat's latest search failed or any job failed in the
   last 24 h. This is the "someone should look" signal that was missing.

**Deliberately not now**
- Grafana, Kibana, Loki, Prometheus: the data already sits in Postgres and the stack is one VPS.
- Email, push or Slack alerts: no SMTP exists yet (it arrives with the Phase 5 digest) and a channel means a
  new service or credentials, which the owner must approve. Revisit with the digest.
- Retry buttons, log viewer, charts, history beyond 50 jobs, per-source health (the Sources page covers it).

**Done when:** an admin sees yesterday's failures within one page load, a member gets 403 and no nav entry, and
the numbers match `SELECT status, count(*) FROM jobs GROUP BY 1`.

## Ops: powiat checklist and priority list (tab "Kolejka", formerly "Powiaty")

**Problem.** The nightly planner picks the powiats searched longest ago, and the only way to push a powiat to the front
was editing `infra/crontab` (PR #90). The team needs to see which of the 380 powiats still have no successful search and
to say which ones come first.

**Must have**
1. `GET /api/powiats` (any logged-in user): every powiat grouped by województwo, with *checked* (= it has a successful
   search, `search_runs.last_ok_at`), the last attempt or error, sites found and whether it is on the priority list.
2. `PUT /api/powiats/priority`: replaces the saved list (table `powiat_priority`, migration `0008`). Unknown codes give 422.
3. A "Powiaty" page: counts, filter, one section per województwo, a "Priorytet" toggle per not-checked powiat, and
   Zapisz / Cofnij. Changes are staged until saved.
4. `plan_searches` takes the cron payload's `priority` first, then the saved list, and forces them ahead of the rotation
   until each has one successful search. After that they join the normal rotation.

**Deliberately not now**
- A manual "checked" tick (checked is automatic), re-prioritising an already checked powiat, a "search now" button
  (a saved list applies from the next 01:00 run), per-user lists, and drag-and-drop ordering.

**Done when:** saving 0805 in the tab makes the next `plan_searches` queue it first, and the page shows it as checked
after its search succeeds.

## Sprawdź działkę (plan of 24.10.2026)

**Problem.** "Someone offered me this plot. Is it any good?" The nightly search only finds land inside the grid
pre-filter and writes it per powiat. The team has no way to ask about one given parcel, and no way to test every
layer on land whose answer they already know (golden set).

**Users.** All logged-in users, desktop first, phone for a quick look.

**What exists.** `uldk.py` parses `GetParcelByXY` only. `search_powiat` loads everything for a whole powiat (flood,
mining, BDOT10k housing, RU, plus three OSM parses of the full extract) and `run_search` drops parcels under
`min_parcel_ha` and clusters under `ha_min`. `write_sites` replaces a powiat's rows. The site card and
`GET /api/sites/{id}` already render a scored site from `sites.detail`.

**Assumptions (to verify in step 1, none are measured yet)**
- A: a check can reuse the scorer unchanged. It can: `score_candidates(sites, layers, cfg)` takes any `Site`.
- B: the heavy part is loading layers, not scoring. Per-powiat downloads (BDOT10k, flood, RU) and the OSM parses
  are too slow and too big for 30 s on the 1.9 GB VPS if repeated per request (R7).
- C: ULDK `GetParcelById` returns the same `SRID=2180` geometry as `GetParcelByXY` (only the XY form is confirmed live).

### Design

1. **Pipeline, new `dclh_pipeline/parcel_check.py`.** `check_parcels(ids_or_points) -> list[ParcelCheck]`:
   ULDK resolves each input to a parcel (id and geometry). Adjacent parcels (gap ≤ 5 m, same rule as
   `cluster_sites`) become one site, others separate sites, so "I was offered parcels 12/1 and 12/2" gives one card.
   No grid pre-filter, no `min_parcel_ha` drop, no `ha_min` drop: a small or far-away parcel is scored and shown with
   its hard-filter reasons ("poniżej 10 ha"), not silently omitted.
2. **Layer loading, split in two.** Refactor `search_powiat` into (a) *static layers* from saved snapshots (grid,
   gas, runways, industrial, residential clusters, GDOŚ, DSO/PSE capacity, heat, PSI, cities), built once per worker
   process and cached in memory, and (b) *area layers* downloaded for a bbox (flood, mining, RU, BDOT10k, EGiB
   neighbours for `expansion_ha`, KIMPZP and KIUG points). For a check the bbox is the parcel buffered by the
   largest distance any constraint needs. `search_powiat` keeps its behaviour and calls the same two functions, so
   there is one implementation (PLAN key decision).
   - BDOT10k is per powiat (a big download). Use the existing 40-day response cache; the first check in a powiat is
     "cold" and slower. Step 1 measures this and decides whether housing falls back to `osm_res` for checks (then
     marked `estimated`).
3. **Async by job.** New job kind `check_parcel` (payload: inputs, `requested_by`). The API creates a `parcel_checks`
   row (`pending`) and returns its id; the page polls it. Progress is a list of layer steps with status, so a cold
   run shows "Pobieranie: strefy zalewowe…" rather than a frozen spinner. Worker poll is 10 s today: add a short
   poll for this kind, or a wake-up, so a warm check is not 10 s late.
4. **Storage, migration `0009`.** Table `parcel_checks` (`id`, `requested_by`, `created_at`, `status`, `steps` jsonb,
   `inputs` text[], `error`) and `parcel_check_sites` (one row per resulting site: the same columns as `sites`,
   including `detail` and `geom`). Not the `sites` table: its writer deletes by powiat. Parcel ids and geometry
   only; no owner data, no notes (brief 9.6). Checks are visible to all users.
5. **API.** `POST /api/checks` (body: `parcels: string[]` or `point: {x,y}`; max 20 ids; 422 on malformed ids),
   `GET /api/checks/{id}` (status, steps, sites), `GET /api/checks/{id}/sites/{n}` (same shape as
   `SiteDetail`), `GET /api/checks` (my recent). A map click calls `POST` with a point; ULDK is reached only from
   the worker, never from the browser.
6. **Web.** `/sprawdz-dzialke`: textarea (ids separated by space, comma or newline) and "Wskaż na mapie", recent
   checks list. `/sprawdz-dzialke/:checkId`: progress, then the result cards. The site card body is extracted from
   `SiteCardPage` into a shared component so both routes render the same thing. The card says "Sprawdzenie
   jednej działki, poza wyszukiwaniem" and shows the layer ages. All text Polish, `12,5 ha`, `dd.mm.rrrr`.
7. **Honesty rules.**
   - A layer that failed or was skipped is `missing`, and an inactive hard filter is listed as "nie sprawdzono"
     (R13). A check never reports "no flood" because the flood download failed.
   - A parcel outside the AOI is scored and labelled "poza obszarem zainteresowania" (decision 1 below).
   - Zoning "unknown" stays "unknown", not "bad" (brief 3).
   - The card carries the manual next steps (KW, MPZP query to the gmina, DSO query), as for any site.

### Steps (one PR each, stacked in this order)

1. **Spike and measurement (no feature code).** `dclh check-spike <id>`: ULDK by id, then time each area layer
   for one parcel in Reguły (Pruszków, golden set) and one far from the existing powiats. Record the seconds,
   peak memory and cold/warm difference in STATUS.md; confirm assumptions B and C; save the ULDK `GetParcelById`
   answers as fixtures. *Done when* the numbers decide the BDOT10k question and the 30 s budget is realistic.
2. **ULDK by id** (`parse_by_id`, `fetch_by_id`/`by_xy` through `PoliteClient`) with tests on the live fixtures:
   ok, not found, malformed id, multi-record, ULDK error text, geometry in EPSG:2180.
3. **Refactor `search_powiat` into static + area layers** with no behaviour change. *Done when* the existing
   search tests pass unchanged and a powiat search produces the same sites as before on the fixture.
4. **`check_parcels` + `dclh check <ids>` CLI.** Prints the score breakdown. *Done when* the consistency test
   passes: checking the parcels of a site already in `sites` gives the same score and features as the search did.
5. **Migration `0009`, job `check_parcel`, API.** Tests: 401 without login, 422 on bad input, the 20-id cap,
   a failing source gives `missing` and an "nie sprawdzono" line, DB tests under `*_test` only.
6. **Web.** Page, polling, shared card component, empty/loading/error states, map click. Vitest for parsing the
   id list and for each state; one Playwright smoke test (paste a golden id, see a card).
7. **Polish.** "Dodaj do projektów" (arrives with the Moje projekty kanban, so only a stub link until then),
   check history, cache a result by parcel id for a short time.

### Test cases that matter (beyond the happy path)
Malformed and duplicated ids, an id that does not exist, ids from different powiats in one request, two
non-adjacent parcels, a parcel under 1 ha, a parcel over 80 ha (split suggestion), a parcel straddling a powiat
border, a parcel outside the AOI, outside Poland (ULDK error), ULDK or one area source down (partial result, not a
failed check), the worker restarting mid-check (`fail_interrupted` marks it failed, the page shows it), two users
checking the same parcel at once, and a parcel in a powiat with no saved snapshots.

### Risks
| Risk | Mitigation |
|---|---|
| Cold powiat takes minutes (BDOT10k, RU) and the 30 s promise breaks | Measure in step 1; show progress; state "do 30 s po pierwszym sprawdzeniu w powiecie"; housing fallback to OSM marked `estimated` |
| Memory: static layers held in the worker on a 1.9 GB VPS | Build lazily, drop after idle, measure peak in step 1; the worker already runs one job at a time |
| ULDK blocks non-Polish IPs and rate-limits | Runs on the VPS only; `PoliteClient` rate limit; tests use fixtures |
| A check and a nightly search disagree on the same land | The consistency test in step 4 |
| Scoring a small or off-grid parcel looks like a recommendation | Hard-filter reasons shown first; the "poza wyszukiwaniem" label; no rank, only a score and its explanation |

### Decisions for the owner
1. A parcel outside the AOI (east of the Vistula): score it with a label (recommended), or refuse?
2. Several adjacent ids: one combined site (recommended), or always one card per parcel?
3. Keep checks forever, or expire them after N days?
4. If the spike shows BDOT10k is too slow for a cold powiat: accept the OSM-buildings housing estimate for checks?

**Done when:** pasting the golden-set parcel of Data4 Jawczyce or Reguły returns a card with all layers and their
confidence in under 30 s (warm), the card equals the search result for the same land, and a layer failure shows as
"nie sprawdzono".

## Phase 6: Hardening and deploy

Tasks: Polish VPS, Caddy HTTPS, nightly `pg_dump` to off-site storage (30 days) plus a restore test, RUNBOOK, performance (first paint under 2 s, filter under 300 ms, Lighthouse ≥ 85), axe a11y, final golden-set and manual-review report.

Acceptance: deployed, restore tested, all checks green, no blockers in STATUS.md.

## Risks

| ID | Risk | Impact | Mitigation |
|---|---|---|---|
| R1 | **Source access.** The build environment (Claude Code cloud) cannot reach any Polish public service: its egress policy blocks them. Many of them also block non-Polish IPs. | Phase 0 discovery and all live connector work need a Polish machine. | `dclh discover` is one command that writes a report to commit. Connector tests use recorded fixtures. Live smoke tests run from the Polish VPS. Option: allow the hosts in the cloud environment's network settings (geo-blocking still applies). |
| R2 | EGiB aggregate WFS has gaps (some powiats not federated), limits on features per request, and slow responses. | Missing parcels mean missing sites. | Fetch inside pre-filtered zones only, with bbox tiling, retries and cache. ULDK for single lookups. Report coverage per powiat in the data-quality report. |
| R3 | Rejestr Urbanistyczny is incomplete until 30.11.2026, and APP GML zone codes vary. | Zoning shows as unknown for many sites. | Missing plan means "unknown", not "bad". KIMPZP fallback. Monitor RU for `ZONING_NEW` events. |
| R4 | DSO capacity maps are interactive and may change format without notice. | Phase 3 connectors break. | Snapshot plus hash, graceful degradation, and Playwright or manual CSV fallback. Weekly live smoke test alerts. |
| R5 | GPZ name matching between OSM and DSO lists falls below 70%. | Published capacity isn't used. | Manual mapping table, match score in the UI, and a documented shortfall. |
| R6 | OSM voltage tags are incomplete. | Substations get misclassified. | `UNKNOWN_VOLTAGE` is kept at low confidence and never dropped. Cross-check against DSO GPZ lists. |
| R7 | **The VPS has 1.9 GB RAM** (the earlier 4 vCPU / 16 GB assumption was wrong; the OSM parse was already killed once). EGiB at 1 req/s and 1000 features per page may also be the bottleneck. | A full AOI run may not fit in memory or in a week. | Run per powiat; measure requests, time and memory in Phase 1.5; funnel order (pre-filter before parcels); incremental refresh; upsize the VPS or run heavy jobs elsewhere if the measurements say so. |
| R13 | `ru`, `isok` (flood), `bdot10k` (housing) and all DSO/PSE feeds are not verified (`docs/SOURCES.md`). Flood and housing are hard exclusions. | A dead source leaves hard filters inactive. | Verify each from Poland before relying on it; fallbacks: flood from KZGW/ISOK WMS or manual layer, housing from OSM buildings. An inactive hard filter is shown on every site as "nie sprawdzono". |
| R14 | Grid is 45% of the score but built on the thinnest data. | Ranking looks precise and is not. | Show confidence on every site; measure OSM voltage coverage in Phase 1.5; do not tune weights until live grid data exists. |
| R15 | Golden set is weak: unverified coordinates, one site with no source, one (Bełchatów, 500 MW) out of scope. | Cannot tell whether the ranking is good. | Reclassify Bełchatów as "reference only"; add negative controls (flood, forest, under 10 ha) and a recall check on verified sites. |
| R16 | Owner count may need personal data from the register. | RODO. | Count only; ask the owner before reading or storing anything else. |
| R17 | **VPS drift.** The VPS checkout was edited by hand (OSM memory/voltage work found there) and absolute snapshot paths differed between host and Docker. | Lost or unreviewed changes; a pipeline that works in one place and fails in the other. | Deploy only from `main` with `scripts/deploy.sh` (refuses dirty tree or other branch); manifest paths relative to the data folder; RUNBOOK rule "no edits on the VPS". |
| R8 | Connection-fee figures and the conditions-transfer note are legal interpretations. | Misleading numbers. | Configurable values, visible "wymaga weryfikacji prawnej" label, no legal advice. |
| R9 | Terms and robots rules for KOWR and other HTML sources. | Scraping isn't allowed. | Check the terms before Phase 5. Manual CSV import is always available. Ask the owner before automating anything marked manual. |
| R10 | AOI split topology: the Vistula centreline has gaps, braids and reservoirs. | Wrong in/out decisions near the river. | Longest-chain main stem, snapping, explicit test cities, and owner-editable AOI in Settings. |
| R11 | **The GitHub repo is public.** | Code, docs and the brief are visible to anyone. | No secrets, contact emails or personal data in the repo (`.env` is gitignored). The owner can decide whether to make the repo private. |
| R12 | Golden-set coordinates are approximate (press releases). | False alarms in the sanity check. | Verify on Geoportal and treat as a sanity check, not ground truth. |
