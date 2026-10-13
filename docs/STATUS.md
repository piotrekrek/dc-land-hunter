# Status

Last updated: 11.10.2026 · Phase 0 done except source verification; Phase 1 started; Phase 2 code done on test data; Phase 3 code done on fixtures (no live data yet)

## Phase 0: what works

- Repo layout, `CLAUDE.md`, `docs/PLAN.md` (phases, decisions, risks).
- `docker compose -f infra/docker-compose.yml up --build` starts db (PostGIS), migrate, api, worker,
  scheduler, martin and web (Caddy). Verified locally: all services healthy, `/api/health` reports PostGIS,
  `/tiles/health` OK, deep links work, security headers set.
- Python workspace (`pipeline` + `api`): typed config loaders for `scoring.yaml` / `sources.yaml`,
  polite HTTP client (User-Agent + contact, 1 req/s per host, robots.txt, backoff), content-addressed
  snapshot store, connector base class, runner with graceful degradation (last good snapshot + `SOURCE_FAILURE`),
  job queue worker, `dclh discover` spike tool. 50 tests (45 without a database).
- API: `/api/health`, `/api/sources`, `/api/config/scoring`; OpenAPI exported and TS types generated.
- Web (Polish UI): shell with all screens, MapLibre map centred on the AOI (position kept in URL),
  Źródła danych and Ustawienia (read-only) pages, light/dark themes, mobile layout, Polish formatters.
  12 unit tests; Playwright smoke + axe tests pass on desktop and mobile.
- CI workflow (`.github/workflows/ci.yml`): ruff, mypy, pytest with PostGIS, eslint, tsc, vitest, build,
  generated-types check, image build, compose + Playwright smoke. **Green on GitHub** (run 1 on `dc-land-hunter` main, with the real Martin image).

## Phase 0: NOT done

- **Source verification (the main acceptance item) is not done.** The cloud environment this was built in
  cannot reach any Polish public host (403 from its egress policy), and many of those services block foreign
  IPs anyway. All 24 MVP sources are "not yet checked" in `docs/SOURCES.md`.
  **Next step for the owner:** run `uv run dclh discover --capture-network --write-docs` from a machine in
  Poland and commit `data/discovery/` + `docs/SOURCES.md` (instructions in `docs/SOURCES.md`).
- Golden-set coordinates: a first web search found descriptions but no verifiable coordinates (left empty on purpose). Hillwood Reguły, Bełchatów/Domiechowice and CGE Konin are located textually in `tests/golden/sites.yaml`; the Tauron Jaworzno / Wysoki Brzeg data centre could not be confirmed by any source found.

## Stubbed / placeholders

- Home list is an empty state; Nowości, Sprawdź działkę, Moje projekty, Eksport are placeholder pages.
- `tiles` schema is empty, so the map shows only the basemap.

## Known issues / caveats

- Martin image: this sandbox could not pull `ghcr.io`, so Martin was tested with the official 1.16.1 release
  binary in a local stand-in image. The compose file uses the official `ghcr.io/maplibre/martin:1.16.1` image;
  CI will exercise it for real.
- Basemap is the free OpenFreeMap style; it could not be loaded in the sandbox (blocked), so only the
  no-basemap fallback was seen. Check it renders on a normal network.
- Docker Hub rate limits (429) can slow image pulls on shared IPs.
- The GitHub repository is public: keep secrets and personal data out (`.env` is gitignored).

## Demo script

1. `docker compose -f infra/docker-compose.yml up --build`, open http://localhost:8080.
2. Mapa i lista: Polish shell, map, empty-state list. Toggle the theme (top right); resize to phone width.
3. Źródła danych: 28 sources with status; run `docker compose -f infra/docker-compose.yml exec api dclh enqueue discover`
   and refresh after ~30 s to see the discovery result appear.
4. Ustawienia: scoring weights and profile read from `config/scoring.yaml`.

## Phase 3: grid capacity and PSE data (code and fixtures; acceptance not yet measurable)

What works (tests: 339 Python passing with PostGIS, 34 web):
- **GPZ name matching** (`model/gpz_match.py`): names are folded (diacritics, case, "GPZ"/"stacja") and matched with
  rapidfuzz at `grid.gpz_match_min_score` (88). A tie with another station is ambiguous and stays unmatched, with
  the reason kept. A manual mapping in `data/manual/gpz_mapping.csv` always wins (verified, confidence "verified").
- **Manual CSV/XLSX imports** (`sources/manual.py`, `dclh import-manual <source> <file>`) for `enea`, `energa`,
  `tauron` (published MW per GPZ) and `pse_closed`, `pse_odb`, `pse_avail` (point lists). Every row is validated and
  a bad row fails the import with its row number. The file is copied into the snapshot store; the original stays put.
- **Layers and features** (`model/features.py`, `model/capacity.py`): `published_gpz_headroom_mw` from the nearest
  matched GPZ within `grid.published_gpz_km` (5 km) that has a published value; `closed_switchyard_nearby` within
  `closed_switchyard_km` (3 km); `dist_pse_odb_km` and `dist_pse_planned_station_km`. A list that is not loaded gives
  missing, never zero. A published 0 MW is a real zero.
- **Grid badges**: "Opublikowana moc" is set when a published headroom exists (engine, Phase 2). The list and card show
  the three badges (Opublikowana moc / Bliskość sieci / Brak danych).

Not done, and why:
- **No live endpoints.** Discovery (10.10.2026) found no data endpoint for Enea, Energa or Tauron, and only candidate
  links for the PSE lists. No connector calls a live URL. The manual CSV is the fallback in the plan; a JSON endpoint
  or Playwright capture comes only after one is verified in `docs/SOURCES.md`.
- **PSE PDFs (`pse_odb`, `pse_avail`)**: the pdfplumber parser is not written. Without a real file the layout is a guess,
  so the parser would be untested. Until then, the owner converts the PDF table to CSV/XLSX.
- **OSM connector, first real run (12.10.2026, VPS with 1.9 GB RAM).** The 2.1 GB download worked (extract
  `poland-261010`, size check passed). The first parser was killed for memory (`Killed`): it kept a location for every
  node in Poland. It now reads in three passes and keeps only the nodes that power ways use, with `dclh fetch osm_power
  --reuse` to re-parse the saved snapshot. **The new parser is tested on generated files only; the next real run on the
  VPS will show the counts and the peak memory.** Relation-mapped substations and generators are not read. `dclh aoi
  build` and the BDOT10k/PRG loading are still open.
- **OSM extract `poland-261010`, what the real data showed (12.10.2026).** 32,277 substations and 17,274 `power=line`
  ways. Of 1,591 GPZs (110 kV + MV), 1,581 (99%) have a name and 1,343 (84%) an operator tag, so name matching has data
  to work with. Two parser faults found only on real data, both fixed with tests built from the real tag strings:
  1. `voltage=15000;400` (a 15 kV station with a 400 V side) was read as a 400 kV node: 16,426 "220/400 kV nodes"
     instead of about a hundred. Bare numbers are now volts, except `110`. This also decides the node pre-filter.
  2. Operators written as `Tauron` (510), `Energa` (203), `Enea` (72) were treated as foreign owners, which would have
     marked utility GPZs as "powered brownfields". Short names now count as DSOs, and `Stoen Operator` (Warsaw) was added.
     Generation and industry (`Tauron Ekoenergia`, `PGE GiEK`, `KGHM`) stay foreign. Rail traction stations
     (`PGE Energetyka Kolejowa`, 49) are still counted as foreign owners; whether they belong in the brownfield bonus
     is a question for the owner.
  Peak memory of the parse was 1,689 MB with one `IdFilter` over all node ids: it keeps a dense bitmap over the id
  range (ids reach about 13 billion), about 1.3 GB. The node pass now runs in id-range batches (`MAX_ID_SPAN`), and the
  parse refuses to start with less than 800 MB available. **Measured on the VPS: 470 MB peak**, with the cut cached.
  These memory changes and the refined voltage rule (known low-voltage levels, all-kV tags such as `110;15`) were
  found as uncommitted edits in the VPS checkout, written there and tested on the real file; they are ported here with
  tests, together with the operator fix that the VPS did not have.
  Real counts after the voltage fix: 144 220/400 kV nodes (was 16,426), 1,594 GPZs of which 1,584 have a name.
- **Acceptance (≥70% of Enea/Energa GPZs matched)** cannot be measured. It needs the real OSM extract and the real
  DSO files, both on the VPS. The report command for the match rate is not written yet.
- **Scoring run**: there is still no `dclh score` command (Phase 2 item), so the new layers are not yet loaded in a
  real run. `build_site_features` and the engine already consume them, and tests cover that path.

Code review and QA of this work (high effort; every finding reproduced before fixing, each has a regression test):
- Matching: a number in a name must be identical ("Gdańsk 10" no longer matches "Gdańsk 1"); "Stacja
  elektroenergetyczna X" and "X 110/15 kV" normalise to "X"; tied stations within 500 m count as one station mapped
  twice in OSM; a manual mapping may point at any substation, including unnamed ones.
- Import: Excel date cells are accepted; names that normalise alike count as duplicates; `import-manual` now validates
  before storing, so a rejected file leaves no trace, and an accepted one is marked good (usable as the fallback
  snapshot).
- Download: asks for an uncompressed body (so the size check is exact) and honours `Retry-After`.
- **Two changes to Phase 2 behaviour, found by the review (please confirm you agree):**
  1. *Powered brownfield* now requires the OSM `operator` tag to name an owner that is not a DSO or PSE. Before, any
     industrial area near any 110 kV station got the +15, including every utility GPZ. A substation with no operator
     tag is not counted (unknown, not claimed), so recall drops until OSM operators are checked.
  2. *Zoning* ignores a plan zone that covers less than 1% of the site. Before, a zone that only touched the site
     (or overlapped by centimetres) counted as the site's zoning, verified, and earned the full zoning points.
- Open, not fixed: nothing from the review. The end-to-end QA test (`test_phase3_end_to_end.py`) runs the chain
  OSM file → DSO/PSE files → matching → features → score.

Decisions to check:
- Fuzzy-matched GPZs get "estimated" confidence; manual mappings get "verified". Fuzzy matching below 88 is not used.
- PSE point layers are "estimated" (coordinates from a list are approximate).
- Empty PSE lists give missing distances, not "far away", so an empty file does not inflate the score.

Demo script (Phase 3):
1. `uv run pytest tests/unit/test_gpz_match.py tests/unit/test_manual_sources.py tests/unit/test_capacity_features.py -q`
2. Save a DSO file with the columns from `data/manual/README.md`, then `uv run dclh import-manual enea <file>`.
   A bad value prints the row number and nothing is stored as valid.
3. `uv run dclh config-check` passes with the three new grid keys (`published_gpz_km`, `closed_switchyard_km`,
   `gpz_match_min_score`) in `config/scoring.yaml`.

## Manual review loop (top 30 sites)

Starts in Phase 2.

## Phase 1 progress (code, fixtures only)

Done:
- Substation classifier (`GPZ_110_SN`, `NODE_220_400`, `OTHER`, `UNKNOWN_VOLTAGE`) and operator → DSO mapping, with
  tests (incl. hypothesis). Decision: a 110 kV-only station is `OTHER` (the brief defines a GPZ as 110 kV + SN).
- AOI builder (extend the river line, split the country, keep the side with a reference point) with synthetic tests.
- Migration 0002: `aoi`, `admin_units`, `substations`, `lines`, `buildings`, `users`, `settings`, and Martin views in `tiles`.
  Checked with the real Martin 1.16.1 binary: the three layers are published and a tile containing a test
  substation is served.
- Map: AOI, lines by voltage (110/220/400 kV) and substations layers with a toggle panel and legend.

Not done yet (needs real data from a machine in Poland):
- PRG, OSM and BDOT10k connectors, the real AOI (`data/aoi/aoi.geojson`), loading substations and lines,
  per-voivodeship counts and the plausibility check (Phase 1 acceptance).
- "Uruchom teraz" on the Sources page: deliberately not added until login exists (Phase 4), because an
  unauthenticated button would let anyone trigger crawls.

## Login (pulled forward from Phase 4)

- Email + password (argon2id, min 12 chars), server-side sessions in an http-only `SameSite=Lax` cookie
  (token hashed in the DB), `Secure` over HTTPS, 14-day expiry, logout, password change signs out everywhere.
- Roles `admin` / `member`; users are created with `dclh user create|set-password|list`; no self-signup.
- Every API route except `/api/health` requires login; the web app redirects to `/logowanie`.
- "Uruchom teraz" on the Sources page (admin only) queues a discovery check for the worker.
- Login throttle is in memory (one API process): 5 failures per email+IP / 15 min.
- Not done: CSRF tokens (mitigated by `SameSite=Lax` + JSON-only bodies, no CORS), password reset by email,
  per-user audit log. Fine for 2–5 invited users; revisit before wider use.
- Tests: 101 Python (with PostGIS), 16 web unit, 16 Playwright (desktop + mobile, incl. axe on the login page).

## Phase 2: scoring engine (done, on test data)

- `score_site()` with hard filters, five weighted categories, penalties, completeness and the upside score;
  Polish explanation per line; grid badge; estimated MW and flags. Details and my interpretation choices:
  `docs/SCORING.md` (**please review the table of sub-criterion weights**).
- 54 tests, including hypothesis properties (monotonicity, bounds, well-formed output for random sites).
- Not yet: live data for the features (EGiB connector written, not yet run live), the golden-set run. Sites list and card are built (see below).
- Site clustering (`model/sites.py`): parcels touching or within 5 m in the same zone form one site; area in ha,
  Polsby-Popper compactness, estimated MW (6 MW/ha), stable `site_id`, >80 ha split suggestion (flag only).
  9 tests on synthetic parcels. `expansion_ha` is not implemented: the brief does not define how to compute it.
- Feature layer (`model/features.py`, `model/zoning.py`): grid distances (110 kV line, GPZ, 220/400 kV node), N-1
  count of distinct 110 kV lines within 3 km, the grid pre-filter (3 / 5 / 25 km, any one keeps a zone), powered
  brownfields (industrial area ≤ 200 m from the site with a 110 kV substation ≤ 100 m from that area), zoning
  (RU zone with most overlap → verified; industrial area without a plan → `unknown`, estimated; loaded register
  without any plan → `unknown`, missing), constraint overlap in % of site area, boolean touch checks, residential
  distance in metres, and `build_site_features()` which assembles `SiteFeatures` from a `Site` and the loaded layers.
  Every fact carries source_id, as_of and confidence; a layer that is not loaded gives a missing fact, never zero.
  Zone-code mapping (`P`, `PU`, `P/U`, `U/P`, `E`, `IT` → production; `SG` → plan ogólny; procedures outrank codes)
  is an assumption until the APP schema is checked on real data. 43 new tests on synthetic geometry with exact
  distances.
- Scored sites are stored: migration `0004` (`sites` table with PostGIS geometry, provenance-carrying `detail`
  JSON), `scoring/run.py` (`score_candidates`, `write_sites`: one run replaces the table, excluded sites kept).
- API: `GET /api/sites` (sort by score, upside, completeness, area or MW; filters `min_mw`, `min_ha`,
  `include_excluded`; paging) and `GET /api/sites/{id}` (score breakdown, features with source, confidence and
  as-of, exclusion reasons, GeoJSON outline). Login required, whitelisted sort columns.
- Web: the home list ("Lokalizacje") is the ranked list with filters that live in the URL (defaults 60 MW and
  10 ha, the brief's target) and "Pokaż więcej" paging; the site card at `/lokalizacje/:id` shows the score,
  the breakdown per category with confidence and source, and the exclusion reasons. All UI text is Polish.
  26 web tests; Playwright smoke text updated.
- Tests: 223 Python tests. The 16 PostGIS tests now run locally too: PostGIS 3.4 is installed in this
  sandbox's PostgreSQL 16 (`dclh` role and database). Sites DB tests cover the write, list, filters, paging,
  card and login requirement against real PostGIS.
- Not done in this step: the map choropleth over the sites, and a `dclh score` command. The command has no
  parcel input until the `egib` connector loads parcels, so it would produce an empty table today.
- EGiB connector (`sources/egib.py`): parcels by WFS in 1 km tiles aligned to the grid, inside candidate zones
  only. Paged with `numberReturned`/`numberMatched`, WFS exceptions raised (HTTP 200 included), each page kept as
  a raw object, the snapshot is a JSON bundle, and parcels on tile edges are de-duplicated. Layer `ms:dzialki` and
  EPSG:2180 come from the 10.10.2026 discovery report. **Attribute names (`identyfikator`, `gml_id`) are not yet
  checked against a real response**: the first live run from Poland must confirm them. Tests use synthetic pages
  (`tests/fixtures/egib/`). Not yet wired into the scheduled runner (it needs the candidate zones from the DB).
- Live sample capture (`dclh capture-sample`, see `docs/RUNBOOK.md`; the bbox is now northing-first): one EGiB WFS page (20 parcels) and one
  ULDK lookup for a point, with metadata, for committing as fixtures. Built and tested with mocked responses. To
  be run on the VPS for CGE Konin (estimated point, to be checked on Geoportal).
- **Real EGiB and ULDK responses** (`tests/fixtures/live/poddebice-axis-order-check/`, captured 11.10.2026 on the
  VPS). The request bbox was sent easting-first, but WFS 2.0 with the EPSG:2180 URN expects northing-first, so the
  sample came back as **Poddębice** parcels, not Konin. The request now sends northing first (test:
  `test_request_bbox_is_northing_first`). Confirmed against ULDK: parcel `101103_5.0011.1` is in Poddębice gmina,
  at about 51.90 N, 18.78 E, and the parcel attributes were correct.
  Also from the sample: the parcel id is `ID_DZIALKI` (the same form as ULDK, e.g. `306201_1.0002.1573`); GDAL
  gives easting-northing geometries (no swap, the earlier swap was wrong); `numberMatched="unknown"`, so paging
  continues while pages are full; the registered area (`POLE_EWIDENYJNE`) and land-use class are empty, so the
  land score uses the geometry area.
  Next: capture the CGE Konin sample again with the corrected request (`dclh capture-sample`).
- Still missing for Phase 2: the live connectors (`ru`, `egib` + `uldk`, `gdos`, `isok`, `midas`, `nid`, `kiug`,
  residential buildings) that fill the layers, the golden-set run, and the first manual top-30 review. They all
  need a Polish machine (Phase 0). Not yet: the map choropleth, the `dclh score` command, expansion land, and the
  PSE/telecom/city/heat-network distances.

## Ready for real data (PRG first)

- `sources/prg.py`: PRG connector. Fetches the WFS layers A00 (country), A01 (voivodeships), A02 (powiats)
  and A03 (gminas) and bundles them in one zip snapshot. Parses to one table in EPSG:2180 with
  `level`, `teryt`, `name`. Fails loudly on missing fields or coordinates outside Poland.
- `dclh fetch prg` runs fetch and parse without the database and prints row counts per level.
- 4 tests on synthetic GML. **Not yet checked against a real PRG payload**: the field names
  (`jpt_kod_je`, `jpt_nazwa_`) and the axis order are assumptions until the first VPS run.

Next on the VPS: `uv run dclh fetch prg`. If it fails on fields or bounds, paste the error; the report lists the
available columns, which settles the names.

## Stations on the map: `dclh load-grid` (12.10.2026, tested on PostGIS, not yet run on the VPS)

- `dclh load-grid` parses the saved `osm_power` snapshot (no download) and replaces `substations` and `lines` in one
  transaction, so the existing Martin views and map layers show them. Loaded: 110 kV + SN stations, 220/400 kV
  nodes, stations with an unreadable voltage (confidence "missing"), and lines of 110 kV and above. Not loaded: the
  medium-voltage-only and 110 kV-only stations (`OTHER`), which would bury the GPZs. A 110 kV-only switchyard is
  therefore not on the map; say so if you want it.
- `dso` and `foreign_operator` come from the OSM `operator` tag. `circuits` and line `operator` stay empty.
- Tests: `tests/unit/test_grid_load.py` (rows, SRID, tile views, reload replaces, failed load keeps the old grid).
- **Next on the VPS:** deploy, `dclh fetch osm_power --reuse`, `dclh load-grid`, hard-refresh and zoom in (stations
  from zoom 5, lines from zoom 4). Compare the printed counts with 1,594 GPZs and 144 nodes at 220/400 kV.
- Not done: a station popup (name, operator, voltages), clipping to the AOI.

## Phase 1.5: `dclh search` (code on fixtures; not yet run live)

- `dclh search <powiat TERYT> [--write]` (`search.py`): grid-first seed (110 kV line 3 km / GPZ 5 km / node 25 km,
  from `scoring.yaml` `prefilter`, cut to the powiat) → 1 km EGiB tiles that touch the seed → parcels in the seed →
  clusters → clusters under `target.ha_min` dropped and counted → `score_candidates` → optional `write_sites`.
  Prints the funnel counts, timings per stage and peak memory (the R7 measurement).
- Needs saved `prg` and `osm_power` snapshots (`dclh fetch prg`, `dclh fetch osm_power`) and runs from Poland.
- 9 tests on synthetic geometry (funnel counts, seed shape, tiles, empty cases, CLI preconditions).
- Not yet: the zoning seed (no `ru` connector), constraint and housing layers (sites are scored with those facts
  missing, so hard filters for flood, Natura, forest and housing do not act), DSO/PSE layers, the "wymaga zmiany
  planu" flag, the owner count. The `parcel_id` is the only EGiB attribute used.
- **First real run, powiat 1421 (12.10.2026, VPS 3.9 GB):** 314 tiles, 138,160 parcels fetched (473 s), 113,957 in
  the seed, peak memory 827 MB. Result: **one** cluster of 26,174 ha, because adjacency chained the whole built-up
  area together; the Hillwood area could not be told apart. Fix: parcels under `search.min_parcel_ha` (1 ha) are
  dropped before clustering and clusters above `target.large_site_split_ha` (80 ha) are cut on a grid into pieces of
  about that size (flag `split_cluster`). Both are blunt stand-ins for the missing zoning layer. **Next on the VPS:**
  rerun `uv run dclh search 1421` and check whether the Hillwood area appears as its own site.
