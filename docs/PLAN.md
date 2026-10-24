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
   ULDK resolves each input to a parcel (id and geometry); each parcel is its own site and card (decision 2).
   No grid pre-filter, no `min_parcel_ha` drop, no `ha_min` drop: a small or far-away parcel is scored and shown with
   its hard-filter reasons ("poniżej 10 ha"), not silently omitted.
2. **Layer loading, split in two.** Refactor `search_powiat` into (a) *static layers* from saved snapshots (grid,
   gas, runways, industrial, residential clusters, GDOŚ, DSO/PSE capacity, heat, PSI, cities), built once per worker
   process and cached in memory, and (b) *area layers* downloaded for a bbox (flood, mining, RU, BDOT10k, EGiB
   neighbours for `expansion_ha`, KIMPZP and KIUG points). For a check the bbox is the parcel buffered by the
   largest distance any constraint needs. `search_powiat` keeps its behaviour and calls the same two functions, so
   there is one implementation (PLAN key decision).
   - BDOT10k is per powiat (a big download). Use the existing 40-day response cache; the first check in a powiat is
     "cold" and slower, which the loader and estimate cover (decision 4). No OSM fallback.
3. **Async by job.** New job kind `check_parcel` (payload: inputs, `requested_by`). The API creates a `parcel_checks`
   row (`pending`) and returns its id; the page polls it. Progress is a list of layer steps with status, so a cold
   run shows "Pobieranie: strefy zalewowe…" rather than a frozen spinner. Worker poll is 10 s today: add a short
   poll for this kind, or a wake-up, so a warm check is not 10 s late.
4. **Storage, migration `0009`.** Tables `check_step_stats` (loader estimates), `parcel_checks` (`id`, `requested_by`, `created_at`, `status`, `steps` jsonb,
   `inputs` text[], `error`) and `parcel_check_sites` (one row per resulting site: the same columns as `sites`,
   including `detail` and `geom`). Not the `sites` table: its writer deletes by powiat. Parcel ids and geometry
   only; no owner data, no notes (brief 9.6). Checks are visible to all users.
5. **API.** `POST /api/parcels/resolve` (inputs → parcel id, outline, gmina, obręb, registered area, within the
   seconds; synchronous, no job; powers the "Czy to ta działka?" screen and the map click). `POST /api/checks` (body: `parcels: string[]` or `point: {x,y}`; max 20 ids; 422 on malformed ids),
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

### UI and UX design

**Why this needs its own design.** The app today is the standard kit: cool grey, steel-blue accent, round score
badge, a stack of identical bordered cards. Sprawdź działkę is the screen where someone holds a broker's message and
a decision to make, so it gets a point of view instead of the default.

**The person and the moment.** A partner gets "I have a plot for you, id 141906_2.0001.141/9" in a chat, often on a
phone. They want, in this order: (1) is this the plot I think it is, (2) should I spend time on it, (3) why, (4) what
do I do next. Waiting up to two minutes is acceptable only if the wait shows real progress and teaches them
something.

**Design idea: the check is a survey drawing that fills in as the work is done.** Cadastral and utility maps are
this domain's own visual language: thin ink outlines, hatch patterns for zones, a scale bar, a north arrow, and a
title block in the corner. One big drawing, centred on the parcel, is the memorable element. Everything around it
stays quiet. The loader *is* that drawing: each finished step lays its layer onto the sheet and writes its finding
into the ledger beside it, so the wait is the investigation happening in front of the user.

**Flow (three screens, one URL each)**

1. `/sprawdz-dzialke` **Wskaż działkę.** One field that takes what people really have: a TERYT id
   (`306201_1.0002.1573`), "obręb + numer", coordinates, a Google Maps link, or several of these pasted at once. A map
   beside it takes a click. Both lead to the same next step.
2. **Czy to ta działka?** (a confirmation, before any expensive work). A fast lookup (seconds) draws the outline on
   the orthophoto with gmina, obręb and registered area. Wrong id or wrong click costs two seconds, not two minutes.
   Only then "Sprawdź" starts the job.
3. `/sprawdz-dzialke/:checkId` **Wynik**, which first shows the progress state and then becomes the result in place,
   so the URL never changes under the user and can be shared with a partner.

**Wireframes (desktop; mobile stacks the columns in the same order)**

Step 1, input:
```
 Sprawdź działkę
 ┌──────────────────────────────────────────────┐   ┌───────────────────────────┐
 │ Wklej numer działki, współrzędne lub link     │   │                           │
 │ z Map Google. Możesz wkleić kilka naraz.      │   │   map (click to pick)     │
 │                                               │   │                           │
 └──────────────────────────────────────────────┘   └───────────────────────────┘
  Rozpoznano: [306201_1.0002.1573  identyfikator]  [52,13 N 20,80 E  współrzędne]
  ✕ „141/9": brak obrębu. Dopisz obręb albo wklej pełny identyfikator.
                                           [ Sprawdź działki ]
  Ostatnie sprawdzenia
  141/9 Jawczyce      24.10.2026   odpada: strefa zalewowa
  ...
```
Step 3, running (the drawing fills in):
```
 ┌─ sheet ─────────────────────────────────────┐  Sprawdzam działkę 1573, obręb 0002
 │      N↑                 ┄┄┄┄ 110 kV (laid)   │  ████████░░░░  5 z 9 · ok. 1 min 10 s
 │   ░░░░ flood hatch (laid)  ┌────┐           │  ✓ Linia 110 kV: 1,4 km
 │                            │parc│           │  ✓ Strefa zalewowa: brak
 │   ▒▒▒ forest (waiting…)    └────┘           │  ⟳ Budynki mieszkalne (BDOT10k)
 │ ─────── 0   250   500 m                     │  · Plany miejscowe
 └─ 1573 · Jawczyce · 12,5 ha · 24.10.2026 ────┘  Możesz wyjść z tej strony. Wynik zapisze się
                                                  na liście „Ostatnie sprawdzenia".
```
Step 3, result:
```
 Odpada: 62% działki leży w strefie zalewowej 1%.          (verdict, one sentence, the reason first)
 ┌ sheet (large, layer toggles) ──────────┐  Wynik 38 ├──────●────┼──────────┤
 │                                        │             0   40    70       100
 │                                        │  do 61 pkt po wyjaśnieniu danych (hatched part of the ruler)
 └ title block: id · gmina · ha · MW · date┘  Nie wiemy: plan miejscowy, grunty rolne klasy I–II
                                              Następne kroki (checklist)
 Protokół sprawdzenia (full width, below): one row per criterion, finding + source + mark
```

**What makes it not the standard kit (each one encodes information)**
- *The sheet.* Hatches, not colour fills: flood = wave lines, forest = stipple, protected nature = cross-hatch,
  housing = small squares, 110 kV = dashed ink line with its voltage written on it, nodes 220/400 kV = solid.
  Colour-blind safe by construction, and it prints well for the PDF one-pager later.
- *The title block* in the sheet's corner (parcel id, gmina, obręb, area, estimated MW, check date, who asked) is
  the card's header. It replaces the "Lokalizacja: 12,5 ha" heading and the round badge.
- *The ruler instead of a round badge.* The score sits on a 0–100 ruler with ticks at the 40 and 70 thresholds. The
  solid part runs to the score, the hatched part to the "after the data is clarified" potential. Current and
  potential are the two numbers the user needs; the ruler shows both at a glance.
- *The verdict is a sentence, not a colour.* Written first, the deciding reason leading. Three outcomes, derived
  from existing logic only (no new thresholds): "Odpada: …" when a hard filter fires; "Wymaga wyjaśnień: …" when no
  filter fires but completeness is low or the grid is proxy-only; "Warto sprawdzać dalej: …" otherwise. State is
  carried by words and a shape (✕, ?, ✓) as well as colour.
- *The Protokół instead of category cards.* A ruled table like an inspection record: criterion, finding in plain
  Polish, mark, source and date in a quieter second line. Marks: ✓ sprawdzone, ≈ szacunek, ? brak danych (the brief's
  ✓ / ~ / ?). Rows with no data are grouped under "Nie wiemy" at the top, not hidden at the bottom.
- *One boldness budget.* The sheet gets the character. Buttons, inputs and tables stay on the existing shadcn
  components and tokens. No gradients, no all-caps labels, no numbered steps (the flow is a flow, but the user
  never needs to count it), no cards of equal weight.

**Type and colour.** Keep IBM Plex Sans for the UI and tabular numbers (already in the app). Add *IBM Plex Sans
Condensed* for the sheet's annotations, which is close to technical drafting lettering, and use *IBM Plex Serif* for
the one-sentence verdict only, so it reads like the opinion line of a report. Both are in `@fontsource` (no new
service). Tokens: reuse `--ok/--warn/--bad` and `--accent`; add `--ink` (sheet lines, = foreground), `--paper`
(sheet background, = surface, slightly lifted from the page), `--layer-flood`, `--layer-forest`, `--layer-nature`,
`--layer-housing`, `--layer-grid`, each defined for light and dark. Contrast pairs checked for AA in both themes,
and hatches stay legible at 1× and 2× zoom.

**Waiting well (the loader)**
- Real progress only: the bar is steps done over steps total, never a fake animation. The estimate comes from the
  history table (decision 4) and says *why* when slow ("pierwsze sprawdzenie w tym powiecie pobiera dane o
  budynkach"). The first useful number appears within 2 s; before then the page says "Sprawdzam…" with no figure.
- Each step, when it ends, adds its layer to the sheet and its finding to the ledger. This needs the pipeline to
  return, per step, a short finding string and a simplified geometry clipped to the sheet (cap about 500 KB per
  check; if over the cap the layer is listed but not drawn).
- The browser tab title carries the state ("(5/9) Sprawdzam…", then "Gotowe: …") so people can switch tabs. The
  job continues if they leave. No push or email in this scope.
- Failure is visible and local: a failed step shows ✕ in the ledger with the reason and a "Spróbuj tę warstwę
  jeszcze raz" action; the check still completes with that layer under "Nie wiemy".

**Several parcels.** One card per parcel (decision 2). The result page becomes a compact comparison list while they
run: one row per parcel (id, gmina, ha, MW, verdict, ruler) with its own mini progress; a finished row opens its
full result immediately. Rows sort by completion, then can sort by score. Duplicates collapse with a note. More than
20 ids: the form says how many were kept and why.

**Map click.** Hover does nothing (a lookup per hover would hammer ULDK). Click drops a pin, resolves the parcel,
outlines it and offers "Sprawdź tę działkę". The same "Sprawdź tę działkę" action is added to map popups and the
site card, so an existing site can be re-checked with fresh live layers.

**Accessibility and input quality**
- Everything the sheet shows is also in the ledger as text, so the sheet is never the only carrier of information.
- Progress is `role="progressbar"` with `aria-valuetext` ("5 z 9 kroków, ok. 1 min 10 s"); step changes go to one
  polite live region, not once per second. After submit, focus moves to the progress heading; after completion, to
  the verdict.
- Input errors are inline, tied to the field with `aria-describedby`, name the exact token and say how to fix it. A
  focusable summary appears when several tokens fail. Validation runs on paste and blur, not per keystroke.
- Touch targets 44 px, the textarea works with mobile paste, the map is optional on phones (the id path is
  primary), layers toggles are buttons not a drag. `prefers-reduced-motion`: layers appear without animation.
- Polish formatting through `format.ts`; dates `dd.mm.rrrr`; ids selectable and with a "Kopiuj" button.

**Copy (Polish, sentence case, one name per action)**
Actions: "Sprawdź", "Sprawdź ponownie", "Kopiuj link", "Dodaj do projektów". Errors say what happened and what to do:
"Nie znaleziono działki 141/9. Dopisz obręb albo wklej pełny identyfikator, np. 306201_1.0002.1573." "Serwer
GUGiK nie odpowiedział. Spróbuj za chwilę." "Sprawdzenie przerwane przez restart serwera. Uruchom je ponownie."
Empty list: "Jeszcze nic nie sprawdzano. Wklej numer działki od pośrednika."

**Look-and-feel check before merging web code.** Screenshots (Playwright) at 390, 768 and 1440 px in light and
dark, for: empty, recognised input, invalid input, confirm, running (cold and warm), done (odpada / wymaga wyjaśnień /
warto), partial failure, multi-parcel, ULDK error. Reviewed against the list under "What makes it not the standard
kit" and the owner's reaction; `axe` run on each state.

### Steps (one PR each, stacked in this order)

1. **Spike and measurement (no feature code).** `dclh check-spike <id>`: ULDK by id, then time each area layer
   for one parcel in Reguły (Pruszków, golden set) and one far from the existing powiats. Record the seconds,
   peak memory and cold/warm difference in STATUS.md; confirm assumptions B and C; save the ULDK `GetParcelById`
   answers as fixtures. *Done when* the numbers seed the loader's default estimates and say what "warm" costs.
2. **ULDK by id, by point and by "obręb + numer"** (name search unverified: the spike checks `GetParcelByIdOrNr`) (`parse_by_id`, `fetch_by_id`/`by_xy` through `PoliteClient`) with tests on the live fixtures:
   ok, not found, malformed id, multi-record, ULDK error text, geometry in EPSG:2180.
3. **Refactor `search_powiat` into static + area layers** with no behaviour change. *Done when* the existing
   search tests pass unchanged and a powiat search produces the same sites as before on the fixture.
4. **`check_parcels` + `dclh check <ids>` CLI.** Prints the score breakdown. *Done when* the consistency test
   passes: checking the parcels of a site already in `sites` gives the same score and features as the search did.
5. **Migration `0009`, job `check_parcel`, step tracking and estimate, API.** Tests: 401 without login, 422 on bad input, the 20-id cap,
   a failing source gives `missing` and an "nie sprawdzono" line, DB tests under `*_test` only.
6a. **Design prototype (static HTML, fixture data, no React).** The sheet, ruler, Protokół and loader in light and
   dark at three widths, for the owner to react to *before* the React code. *Done when* the owner approves or redirects.
6b. **Web.** Page, polling, loader with progress bar and estimated time, shared card component, empty/loading/error states, map click. Vitest for parsing the
   id list and for each state; one Playwright smoke test (paste a golden id, see a card).
7. **Polish.** "Sprawdź ponownie", "Dodaj do projektów" (arrives with the Moje projekty kanban, so only a stub link
   until then), check history.

### Test cases that matter (beyond the happy path)
Malformed and duplicated ids, an id that does not exist, ids from different powiats in one request, two
adjacent parcels (two cards), a parcel under 1 ha, a parcel over 80 ha (split suggestion), a parcel straddling a powiat
border, a parcel outside the AOI (labelled, not refused), outside Poland (ULDK error), ULDK or one area source down (partial result, not a
failed check), the worker restarting mid-check (`fail_interrupted` marks it failed, the page shows it), two users
checking the same parcel at once, and a parcel in a powiat with no saved snapshots.

### Risks
| Risk | Mitigation |
|---|---|
| Cold powiat takes minutes (BDOT10k, RU) and the 30 s promise breaks | Measure in step 1; loader with estimated time; promise 30 s only for warm checks |
| Memory: static layers held in the worker on a 1.9 GB VPS | Build lazily, drop after idle, measure peak in step 1; the worker already runs one job at a time |
| ULDK blocks non-Polish IPs and rate-limits | Runs on the VPS only; `PoliteClient` rate limit; tests use fixtures |
| A check and a nightly search disagree on the same land | The consistency test in step 4 |
| Scoring a small or off-grid parcel looks like a recommendation | Hard-filter reasons shown first; the "poza wyszukiwaniem" and "poza obszarem" labels; no rank, only a score and its explanation |

### Decisions (owner, 24.10.2026)
1. A parcel outside the AOI is scored and labelled "poza obszarem zainteresowania".
2. Adjacent ids are **not** merged: one card per parcel for now. Step 1 of "Design" therefore drops the clustering
   of inputs; `expansion_ha` still counts neighbouring parcels. Combining parcels is a later option.
3. "Check only on demand" (interpreted): a check runs only when a user asks. No automatic re-check or refresh, and
   no expiry job; a result is re-run by pressing "Sprawdź ponownie". The card always shows the check date and
   layer ages. *To confirm with the owner: does this also mean old checks are kept indefinitely?*
4. BDOT10k housing stays, with no OSM fallback. A cold powiat is slow, so the page must show a loader with an
   estimated time instead of shortening the check (below).

### Loader and estimated time
- The job writes `steps` as it goes: `[{key, label_pl, status: waiting|running|done|failed|skipped, started_at,
  seconds}]`, in layer order (ULDK, EGiB neighbours, flood, mining, RU, BDOT10k housing and forest, KIMPZP, KIUG,
  scoring). The page polls every 2 s and shows a progress bar (done steps over all), the running step's Polish
  label ("Pobieranie budynków mieszkalnych (BDOT10k)…") and a step list with ticks.
- Estimate: a table `check_step_stats` (`step_key`, `warm`, `seconds`, `at`) filled by every check. The estimate
  is the median of the last 10 runs of each step, **cold** when the powiat's BDOT10k/RU answers are not in the
  response cache and **warm** otherwise, summed over the steps still to do. Before any history exists, the step 1
  spike numbers are the defaults. The page shows "Szacowany czas: ok. 2 min (pierwsze sprawdzenie w tym powiecie)"
  or "ok. 25 s", counts down, and says "trwa dłużej niż zwykle" when it overruns instead of showing 100% early.
- The user can leave the page: the job continues and the check shows in the recent list ("w trakcie").
- Tests: estimate from history, default without history, cold vs warm, a failed or skipped step counts as done
  for progress, and the overrun message.

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
