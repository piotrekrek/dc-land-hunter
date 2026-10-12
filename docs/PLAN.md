# DC Land Hunter: build plan

This plan turns `PROJECT_BRIEF.md` into phases with concrete tasks, acceptance checks and risks.
It is a living document: update it when a phase finishes or the plan changes.

Last updated: 12.10.2026 (plan review: vertical slice gate, grid-first seed, owner count in scope; Phase 3 code on fixtures, acceptance pending live data).

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
- [ ] **Run `dclh discover` from a machine in Poland** and record ✓/⚠/✗ per source in `docs/SOURCES.md` (blocked: see risk R1).
- [ ] Research golden-set coordinates (Hillwood Reguły, Tauron Jaworzno, Bełchatów/Domiechowice, CGE Konin, Data4 Jawczyce, Atman Duchnice, Switch Mory) and verify them on Geoportal.

Acceptance: `docker compose up` starts everything; CI green; every MVP source has a verified status.

## Phase 1: Base and grid layers

Tasks:
- `prg` connector: voivodeship, powiat and gmina boundaries with TERYT → `admin_units`.
- `osm_power` and `osm_land` connectors: download the Geofabrik `poland-latest.osm.pbf` (with ETag/size check), extract with pyosmium into substations, lines, generators, landuse and buildings.
- AOI builder (`dclh aoi build`): country polygon from PRG, Vistula main stem from OSM (`waterway=river` + `name=Wisła`, longest connected chain), extended upstream to the border, split, keep the western/northern part. Write `data/aoi/aoi.geojson`.
  - Unit tests: Warsaw left bank, Kraków north bank, Katowice, Łódź, Poznań, Gdańsk-Wrzeszcz (west of the Vistula) are in; Bielsko-Biała, Lublin, Białystok and Warsaw Praga are out.
- Grid model: classify substations (`GPZ_110_SN`, `NODE_220_400`, `OTHER`, `UNKNOWN_VOLTAGE`), derive DSO from the `operator` tag with a territory polygon fallback, lines by voltage, and a count of independent circuits.
- `bdot10k` connector: residential buildings (per powiat ATOM/WFS), cached.
- Tables: `admin_units`, `substations`, `lines`, `buildings`, `users`, `settings`.
- Martin layers for the AOI, lines by voltage and substations by class. Map toggles.
- Sources page (Źródła danych) live from the `sources` and `runs` tables. "Uruchom teraz" creates a job.
- Docs: substation counts per voivodeship compared with plausibility references (PSE and DSO annual reports).

Acceptance: plausible substation counts are documented and AOI tests pass.

## Phase 1.5: Vertical slice (gate before more phases)

Goal: prove the whole chain on real data for one powiat before widening any layer. Phase 3 live work does not continue until this passes.

Tasks:
- [ ] Real AOI (`dclh aoi build`) and PRG loaded; run on the VPS.
- [ ] OSM power layers loaded (substations, lines) with the measured counts and peak memory recorded.
- [ ] `dclh search --powiat <teryt>` chains: seeds → grid pre-filter → EGiB parcels → cluster → constraints → score → write sites. Missing layers stay `missing`, never zero.
- [ ] Pilot powiat: the one containing Reguły (Pruszków). Check whether the Hillwood site appears and where it ranks.
- [ ] Measure per powiat: EGiB request count, runtime, peak memory. Extrapolate to the AOI and fix the weekly-run plan (R7).
- [ ] "Sprawdź działkę" minimal version (pulled forward from Phase 4): one parcel via ULDK → live layers → the same scorer. Fast test of every layer on real land.

Acceptance: one powiat runs end to end on the VPS; the sites list in the app shows real results; the measurements are recorded in STATUS.md.

## Phase 2: Candidates, sites, constraints, scoring v1

Tasks:
- `ru` connector: MPZP designations (P, PU, P/U, U/P, E, infrastructure) and plan ogólny SG zones from the APP GML. Zone codes are checked against the APP schema. `kimpzp` WMS GetFeatureInfo fallback, sampled per candidate.
- Fallback zones from OSM and BDOT10k industrial areas (`zoning=unknown`). Powered-brownfield detection.
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
- [~] `enea`, `energa` and `tauron` connectors, each following discovery's fallback order: JSON endpoint → Playwright → manual CSV. **Manual CSV done** (`dclh import-manual`). JSON endpoint and Playwright wait for a verified endpoint (none in the 10.10.2026 discovery).
- [x] GPZ name matching (rapidfuzz) plus `data/manual/gpz_mapping.csv`, with the match score stored. Matching needs OSM substation names (Phase 1 OSM connector).
- [~] `pse_closed` (XLSX), `pse_odb` (PDF, pdfplumber), `pse_avail` (PDF) parsers with saved fixtures. **XLSX/CSV point lists done** through the manual import. PDF parsing waits for a real file.
- [x] Grid confidence badges: Opublikowana moc / Bliskość sieci / Brak danych (list and card, from Phase 2).
- [~] OSM power connector (`osm_power`): written and tested on a generated file; first real run on the VPS. Brings substation names and operators.
- [ ] GPZ match report (the match rate per DSO) and a `dclh score` run that loads these layers. Both need the real OSM extract and real files on the VPS.

Acceptance: at least 70% of GPZs in Enea and Energa areas are matched to published capacity (or the reason is documented), and scores use them.

## Phase 4: Investor workflow

Tasks: Sprawdź działkę (ULDK + cached layers, under 30 s), kanban (Moje projekty) with drag and drop, notes with RODO notice and full delete, watches, XLSX export, PDF one-pager (WeasyPrint), connection-cost calculator (configurable figures, flagged "wymaga weryfikacji prawnej"), login UI, keyboard shortcuts.

Acceptance: E2E flows pass (login, filter + open card, check parcel, move deal, export, watch → Nowości).

## Phase 5: Monitoring

Tasks: snapshot diffs → events (all nine types), Nowości feed with "mark as seen", watches, weekly digest email (SMTP, Monday 07:00), scheduled runs (Monday 05:00 Europe/Warsaw plus per-source cadence), `kowr` connector (after a terms check) and manual offer CSV import with template.

Acceptance: two consecutive fixture runs produce the expected events, and a test digest renders correctly.

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
