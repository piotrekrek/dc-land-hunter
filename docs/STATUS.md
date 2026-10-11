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

What works (tests: 317 Python passing with PostGIS, 34 web):
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
- **OSM substation names**: the matcher needs `Substation.name`, which only an OSM connector can fill. The OSM
  connector is still a Phase 1 item, so the matching cannot run on the real grid yet.
- **Acceptance (≥70% of Enea/Energa GPZs matched)** cannot be measured. It needs the OSM names and the real DSO
  files, both on the VPS. The report command for the match rate is not written yet.
- **Scoring run**: there is still no `dclh score` command (Phase 2 item), so the new layers are not yet loaded in a
  real run. `build_site_features` and the engine already consume them, and tests cover that path.

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
