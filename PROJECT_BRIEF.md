# DC Land Hunter: MVP brief for Claude Code

Paste this file into an empty repo as `PROJECT_BRIEF.md` and also copy sections 1–3 into `CLAUDE.md`.
It is the full starting context for building the app from scratch.

## 0. How to work on this project

- Read the whole brief first. Start in plan mode, write `docs/PLAN.md` (phases, tasks, risks), then build phase by phase (section 11).
- After every phase: run all tests, update `docs/STATUS.md` (what works, what is stubbed, known issues) and give a short demo script.
- Every URL and endpoint in this brief comes from desk research done in October 2026. Treat them as **unverified until Phase 0 confirms them**. Record the verified ones in `docs/SOURCES.md`.
- Ask the owner before you add a paid service, automate a source marked "manual only", or store personal data.
- Code, comments and docs are in English. **The whole UI is in Polish.**
- Prefer boring, well-known tools. No custom frameworks.

## 1. Goal

The app helps a small investor team **hunt for land west of the Vistula that can host a small AI data centre**, and find it before the market does.

It produces a ranked, explained shortlist of sites, a weekly "what changed" feed and a lightweight deal pipeline. It does not promise grid capacity. It finds the most promising plots and tells the user exactly what still needs checking by phone or letter.

**Users:** 2–5 Polish-speaking people, a product manager and investor partners. They research on a desktop and check alerts on a phone.

**Jobs to be done:**
- "Show me the best sites in region X for an AI data centre of 60 MW or more, and why."
- "What changed since last week?" (new zones, new grid data, new land offers, score changes)
- "Someone offered me this plot. Is it any good?"
- "Track the deals I'm working on."
- "Export a one-pager to send to a partner."

## 2. Scope

### 2.1 Area of interest (AOI)

The AOI is Poland on the left (west and north) bank of the Vistula: from the German border to the river.

How to build it: take the Polish boundary from PRG (GUGiK) and the Vistula centreline from OSM (`waterway=river`, `name=Wisła`, main stem only). Extend the upstream end to the national border near the source in Beskid Śląski, then split the country polygon. Keep the western/northern part.

By this rule, Warsaw's left bank, Kraków north of the river, Silesia, Łódź, Wielkopolska, Pomorze west of the Vistula and Świętokrzyskie are **in**. Bielsko-Biała and Podbeskidzie (south of the upper Vistula) are **out**.

Store the AOI as `data/aoi/aoi.geojson`. The owner can edit it in Settings, for example to add Bielsko-Biała back.

### 2.2 Target profile: a small AI data centre of 60 MW or more

The owner's rule of thumb is **60 MW needs about 10 ha of industrial land**, which is about 6 MW/ha. "Small" here means not hyperscale, so there is no hard upper limit.

| Parameter | Value |
|---|---|
| Connection power | **min 60 MW**. More is better: 60–150 MW is the sweet spot, and above 150 MW is flagged "duży kampus" but not excluded. |
| Land | **min 10 ha** of industrial land (a cluster of adjacent parcels is fine). 10–30 ha is ideal; expansion land next to the site is a bonus. |
| Estimated capacity | `szacowana_moc_MW = area_ha × 6` (density configurable), shown on every site |
| Grid | **110 kV**: a new GPZ 110/SN on site, a dedicated 110 kV bay in an existing GPZ, or a tap from a 110 kV line. Two independent 110 kV feeds is a plus. For more than about 150 MW, a 220/400 kV PSE node nearby is a strong plus. |
| Key distances | 110 kV line ≤ 1 km is ideal, ≤ 3 km acceptable. GPZ with 110 kV ≤ 5 km. 220/400 kV node ≤ 25 km is a bonus. |
| Typical use | AI inference campus, regional GPU cloud, colocation with AI halls |
| Cooling | Closed-loop liquid or dry coolers; **no river water** |

Out of scope by design: dense urban sites under 10 ha (for example Warsaw colocation on 4–5 ha) and hyperscale projects above 500 MW.

### 2.3 Non-goals for the MVP

- No hyperscale (>100 MW) screening.
- No automated land and mortgage register (EKW) lookups: CAPTCHA, terms, personal data. Provide a manual link only.
- No scraping of property portals (Otodom, OLX and similar) until their terms are checked.
- No BIP (municipal bulletin) crawler. That is v2.
- No owner contact data collection. Users may type notes manually; treat them as personal data (section 9.6).

## 3. Domain notes (must-know)

- **The grid is the bottleneck.** PSE (the transmission operator) reports zero free load capacity at existing 220/400 kV stations. Its queue is full of speculative applications. Public data gives proxies, not guarantees, so always show the confidence of grid data.
- **60–150 MW connects to the 110 kV network.** In Poland that network belongs to the distribution operators (OSD/DSO), not PSE. Above about 150 MW, PSE's 220/400 kV network usually comes into play.
- **DSOs west of the Vistula:** **Enea Operator** (west), **Energa-Operator** (north), **Tauron Dystrybucja** (south-west and south) and **PGE Dystrybucja** (Łódź and the centre).
- **What they publish:** Enea and Energa are the only DSOs that publish available capacity **for loads**, and mostly at medium voltage per GPZ. Treat that as a signal of GPZ headroom, not a 60 MW answer. Tauron publishes planned capacity, which it calls "directional". PGE publishes nothing for loads.
- **Grid score at 60 MW+ is mostly proxy-based:**
  - Distance to 110 kV lines and GPZs.
  - Number of independent lines nearby.
  - PSE planned load stations, its closed-switchyard list and nearby ODB entries.
  - Powered brownfields.

  Label it honestly in the UI.
- **Connection rules since 30 Apr 2026** (Prawo energetyczne amendment):
  - Conditions are valid for 12 months.
  - Application fee: 1 PLN/kW, max 100k PLN.
  - Connection advance: 60 PLN/kW, max 6 mln PLN.
  - Security: 30 PLN/kW up to 100 MW.
  - The agreement expires if building-permit milestones are missed.
  - New flexible and configurable connection agreements.
  - The operator re-verifies conditions before signing.
  - Example: securing 60 MW costs about 60k PLN in fees and ties up about 3.6 mln PLN in advance plus 1.8 mln PLN in security. That is roughly 5.4 mln PLN frozen while the project waits.
  - Show these numbers in a calculator on the site card. **The figures need legal verification and are configurable.**
- **Transferring grid conditions to a buyer is legally contested.** A 2013 Court of Appeal ruling treated them as public-law rights that cannot be assigned. The usual workaround is holding a site in an SPV and selling shares. The app only flags this; it gives no legal advice.
- **Zoning:**
  - Best: MPZP with production/service designations (P, PU, P/U, U/P, also E or technical infrastructure).
  - Next: a plan ogólny "strefa gospodarcza" (SG).
  - WZ decisions are a weak route for new sites: infill only and 5-year validity.
  - The Rejestr Urbanistyczny is incomplete until 30 Nov 2026, so a missing plan means **unknown**, not **bad**.
- **Heat reuse:** EU EED Art. 26 requires data centres above 1 MW to reuse waste heat unless shown infeasible. A district heating network nearby is a plus.
- **"Powered brownfields"** are gold. These are closed or underused large industrial sites (steel, chemicals, paper, cement, glass, former power plants and mines) that already have their own 110 kV substation, so an existing connection may be reusable. Detect them explicitly.
- **Investment value drivers:** grid conditions > zoning > size and shape > few owners > access and fibre > environmental decision. **Deal killers:** flood zone, Natura 2000, forest, top soil classes, mining area, housing too close, too many owners.

## 4. Data sources

Status legend: **MVP** = build now; **v2** = later; **manual** = link only, never automated.

| ID | Source | Used for | Access (verify!) | Plan |
|---|---|---|---|---|
| prg | GUGiK PRG boundaries | AOI, gmina/powiat names, TERYT | WFS / bulk download, geoportal.gov.pl | MVP |
| osm_power | OpenStreetMap (Geofabrik PL extract, not Overpass, for bulk) | Substations (voltage, operator), 110–400 kV lines, generators (RES clusters) | `.osm.pbf` from download.geofabrik.de, parsed with `pyosmium`/`osmium` | MVP |
| osm_land | OSM landuse / building | Industrial/brownfield fallback zones, residential proximity | Same extract | MVP |
| bdot10k | GUGiK BDOT10k | Buildings (residential distance), roads, industrial areas | WFS/ATOM by powiat | MVP |
| ru | Rejestr Urbanistyczny | Plan ogólny zones (SG), MPZP designations, procedures in progress | WFS/WMS/CSW at rejestr-urbanistyczny.gov.pl; APP GML schema | MVP |
| kimpzp | KIMPZP | Zoning fallback (often raster) | WMS GetFeatureInfo, sampled per candidate | MVP (fallback) |
| egib | EGiB aggregate WFS | Parcel IDs and geometry, inside candidate zones only | `https://mapy.geoportal.gov.pl/wss/service/PZGIK/EGIB/WFS/UslugaZbiorcza` | MVP |
| uldk | ULDK | Single parcel lookup, "Sprawdź działkę" | `https://uldk.gugik.gov.pl/?request=GetParcelById&id=…` | MVP |
| kiug | KIUG (land use and soil classes) | Forest (Ls), soil class I–III | WMS GetFeatureInfo sampled per parcel | MVP |
| gdos | GDOŚ Geoserwis | Natura 2000, national parks, reserves, landscape parks, OChK | WFS `https://sdi.gdos.gov.pl/wfs` | MVP |
| isok | ISOK flood maps | Flood hazard zones 10%, 1%, 0.2% | WFS 2.0 / ATOM at wody.isok.gov.pl | MVP |
| midas | PIG-PIB MIDAS | Mining areas (obszar/teren górniczy) | WFS at cbdgmapa.pgi.gov.pl | MVP |
| nid | NID heritage | Heritage register, archaeological sites | WMS/WFS, CSV on dane.gov.pl | MVP |
| pse_closed | PSE closed switchyards | Penalty for closed nodes | XLSX on pse.pl | MVP |
| pse_odb | PSE issued conditions list | Load (ODB) entries as a demand heat signal; diff monthly | PDF on pse.pl, parsed with pdfplumber | MVP |
| pse_avail | PSE capacity report | Planned load stations (bonus) | Monthly PDF | MVP |
| enea | Enea "Mapa dostępnych mocy dla odbiorców" | Available MV load capacity per GPZ | Interactive map: find its JSON in Phase 0 | MVP |
| energa | Energa-Operator load capacity | Same | Verify existence and format in Phase 0 | MVP |
| tauron | dostepnemoce.pl | Planned capacity 2027–2028, issued conditions | Interactive map | MVP |
| dso_portals | New DSO capacity portals required by the 2026 amendment | Future replacement for the above | Monitor | v2 |
| uke | UKE search (telecom nodes) | Fibre proximity proxy | WMS / export | MVP (proxy) |
| psi | Polska Strefa Inwestycji aid map | Tax-relief intensity per gmina | Static table | MVP |
| kowr | KOWR land offers | New land leads ≥1 ha in AOI | HTML (nieruchomoscikowr.gov.pl), polite scrape if terms allow, else manual CSV | MVP |
| invest_offers | Municipal/SSE/COI investment land offers | Leads | Manual CSV import (template provided) | MVP |
| heat | District heating presence | Heat reuse bonus | Proxy: cities/towns with a heat concession (URE list) plus distance to built-up area | MVP (proxy) |
| bazaoos | GDOŚ Baza OOŚ | Environmental decisions | Geo-blocked outside PL, no API | manual link, v2 agent |
| ekw | Księgi wieczyste | Ownership | Anti-bot, personal data | manual link only |
| bip | Gmina BIPs | WZ, environmental decisions, tenders | Heterogeneous | v2 crawler |

### Rules for every connector

- One module per source in `pipeline/sources/<id>.py` with `fetch()` → raw snapshot (stored with date and hash) → `parse()` → normalised table.
- Phase 0 discovery spike per source:
  - Confirm the URL, format, licence and rate limits.
  - For interactive maps, inspect the network requests and look for a JSON/ArcGIS REST endpoint.
  - Fallback order: JSON endpoint → Playwright → manual CSV import.
  - Write the result into `docs/SOURCES.md`.
- Be polite: identify with a User-Agent and contact email, cache, keep to ≤1 request/s by default and honour robots.txt.
- **Graceful degradation:** if a source fails, keep the last good snapshot, mark it stale in the UI and raise a `SOURCE_FAILURE` event. A single source failure never blocks a run.
- Every derived value carries `source_id`, `as_of` and `confidence` (`verified` | `estimated` | `missing`).

## 5. Pipeline (the funnel)

1. **Base layers:** AOI, admin units, OSM power grid and buildings, all in EPSG:2180.
2. **Grid model:**
   - Classify substations: `GPZ_110_SN` (voltage contains 110 kV plus an MV level), `NODE_220_400`, `OTHER`, and `UNKNOWN_VOLTAGE`. Keep `UNKNOWN_VOLTAGE` at low confidence; never drop it.
   - Derive the DSO from the `operator` tag, falling back to a DSO territory polygon.
   - Join DSO capacity data to substations by name with fuzzy matching (rapidfuzz) plus a manual mapping table `data/manual/gpz_mapping.csv`. Store the match score.
3. **Candidate zones**, merged with provenance:
   - (a) MPZP P/PU-type designations from RU.
   - (b) Plan ogólny SG zones. Verify zone codes against the APP schema in Phase 0.
   - (c) Fallback where no plan exists: OSM/BDOT10k industrial and brownfield areas, flagged `zoning=unknown`.
   - (d) Leads from KOWR and manual offer imports.
   - (e) **Powered brownfields:** industrial areas containing or touching a substation (`power=substation`) whose operator is not a DSO or PSE, or which sits inside the site.
4. **Grid pre-filter:** keep zones within 3 km of a 110 kV line, within 5 km of a GPZ with 110 kV, or within 25 km of a 220/400 kV node. All three distances are configurable.
5. **Parcels:** fetch EGiB parcels inside the remaining zones only, by bbox tiles with retries, and cache them.
6. **Sites:**
   - A site is a cluster of adjacent parcels (touching, or gap ≤ 5 m for roads and ditches) inside one candidate zone.
   - Compute geometric area, compactness (Polsby-Popper), number of parcels and a stable `site_id` (hash of sorted parcel IDs).
   - Large zones produce one site per connected cluster. Clusters over 80 ha are flagged "duży teren, do podziału" and get a suggested 10–30 ha sub-site nearest the grid.
   - Also compute `expansion_ha`: free land in the same zone, or zoning-compatible land adjacent to the site.
7. **Constraints:** intersect with the exclusion and penalty layers. Store the percentage of area affected and the minimum distances.
8. **Scoring** (section 6). Write `site_scores` with a per-criterion breakdown.
9. **Diff** against the previous run → events (section 7).
10. **Data quality report** per run: row counts per source, % missing per criterion, stale sources and failures → `runs` table plus the Sources page.

Run targets: a full AOI run in under 3 hours on a 4 vCPU / 16 GB VPS; incremental source refreshes in under 15 minutes.

## 6. Scoring

### 6.1 Hard filters

A site is excluded with a reason shown, unless the owner switches a filter to "penalty" in Settings.

- Area below 10 ha (configurable).
- More than 20% of the site in Natura 2000, a national park or a nature reserve.
- Any part in a flood zone with 10% or 1% probability (ISOK).
- Inside an active mining area (obszar górniczy).
- More than 30% forest (Ls).
- More than 30% soil class I–II.
- On the heritage register.
- Residential building within 100 m.

### 6.2 Weighted criteria

Weights live in `config/scoring.yaml`, are editable in Settings and re-rank live.

```yaml
target:
  mw_min: 60
  mw_per_ha: 6            # 60 MW ≈ 10 ha
  ha_min: 10
  ha_ideal: [10, 30]
  large_campus_mw: 150    # above: flag "duży kampus", needs 220/400 kV
weights: {grid: 45, zoning: 25, land: 15, connectivity: 5, bonus: 10}   # sum = 100
grid:
  line110_km_full: 1.0
  line110_km_zero: 3.0
  gpz110_km_full: 2.0
  gpz110_km_zero: 8.0
  two_independent_110_lines_within_km: 3     # N-1 bonus
  node_220_400_km_bonus: 25                  # required-ish when estimated MW > large_campus_mw
  planned_pse_load_station_km_bonus: 25
  closed_switchyard_penalty: 15
  published_gpz_headroom_bonus: 5            # Enea/Energa MV data, signal only
  pse_odb_nearby_km: 15                      # demand signal, shown, small bonus
  powered_brownfield_bonus: 15
zoning: {mpzp_production: 100, plan_ogolny_SG: 80, procedure_in_progress: 50, unknown: 30, other: 0}
land:   {compactness_min: 0.35, parcels_full: 3, parcels_zero: 30, expansion_ha_bonus_per_10ha: 3}
connectivity: {telecom_node_km_full: 2, telecom_node_km_zero: 15, city_km_full: 30, city_km_zero: 120,
               cities: [Poznań, Wrocław, Szczecin, Gdańsk, Katowice, Łódź, Kraków, Warszawa, Bydgoszcz, Opole, Zielona Góra]}
bonus:  {heat_network_km: 5, psi_aid_max_pts: 3, res_cluster_km: 10, env_decision_found: 3, public_seller: 2}
penalties:
  flood_0_2pct: 10
  landscape_park_or_ochk: 8
  mining_terrain: 8
  soil_class_III_pct_over_30: 8
  residential_100_300m: 10
  gas_pipeline_zone: 5
  airport_ols: 5
```

- Each criterion returns `{value, points, max_points, confidence, source_id, as_of, explanation_pl}`.
- Missing data scores 0 but shows as "?" and lowers a separate **data completeness %**. The list can sort by score, completeness or "score if unknowns were good" (the upside view).
- **Grid confidence badge:**
  - "Opublikowana moc" (published by the DSO, verified).
  - "Bliskość sieci" (distance proxy, estimated).
  - "Brak danych" (missing).

## 7. Monitoring

A weekly scheduled run fires on Monday at 05:00 Europe/Warsaw. Each source also has its own cadence: PSE monthly, DSO maps weekly, KOWR daily.

Event types:
- `NEW_SITE`
- `SCORE_CHANGE` (≥10 points)
- `ZONING_NEW` (new plan or SG zone in the AOI)
- `ZONING_PROCEDURE` (a new procedure in progress)
- `GRID_CAPACITY_CHANGE` (a GPZ's published capacity changed by ≥2 MW)
- `PSE_ODB_NEW` (a new load entry; geocode it to its station)
- `PSE_CLOSED_CHANGE`
- `LAND_OFFER_NEW`
- `SOURCE_FAILURE`

Every event links to the affected site, GPZ or source.

Digest: an email on Monday at 07:00 with the top 10 events ranked by the affected site's score, plus an in-app "Nowości" feed. Users can watch a site, gmina or GPZ to always be notified about it.

## 8. App: features and UX

### 8.1 Screens (Polish labels)

1. **Mapa i lista** (home).
   - Split view: a filterable ranked list on the left (cards) and a MapLibre map on the right.
   - Filters: minimum estimated MW (default 60), area range (default ≥10 ha), minimum score, voivodeship/powiat/gmina, zoning type, grid confidence, "powered brownfield", "only new this week", pipeline status.
   - Layer toggles: grid (lines and substations by voltage), DSO capacity per GPZ (coloured dots), candidate sites (choropleth by score), exclusions (Natura, flood, mining, forest), PSE ODB heat, land offers.
   - Exclusions are drawn from source WMS where possible, so the app doesn't have to store them for display.
   - The whole map and filter state lives in the URL, so a view can be shared as a link.
2. **Karta lokalizacji** (site card, a drawer or full page).
   - Header: score, completeness %, area, estimated MW, expansion ha, gmina, status chip, "Obserwuj".
   - Mini-map with orthophoto (Geoportal WMTS), parcels, nearest GPZ and the route-distance line.
   - Score breakdown: a horizontal bar per criterion. Each row shows a ✓ / ~ / ? icon, the explanation in Polish, the source and the date.
   - Grid section: nearest GPZs and lines (km, voltage, DSO, published capacity if any) and the connection cost calculator (section 3).
   - Constraints section, with the percentage affected.
   - Parcels table (TERYT IDs, area) with links:
     - Geoportal (parcel)
     - Google Maps
     - Rejestr Urbanistyczny
     - EKW search page (manual)
     - Baza OOŚ (manual)
   - **"Następne kroki"**: an auto-generated checklist from missing or uncertain items, for example "Złóż zapytanie do Enea o moc 8 MW w GPZ X", "Sprawdź KW ręcznie", "Zapytaj gminę o MPZP / plan ogólny".
   - Notes, activity log, pipeline status.
3. **Nowości**: the event feed with filters and "mark as seen".
4. **Sprawdź działkę**: paste a parcel ID or several, or click the map. Runs the full scoring on the fly via ULDK plus cached layers and returns a site card in under 30 s.
5. **Moje projekty**: a kanban pipeline with columns:
   - Nowe
   - Obserwuję
   - Analiza
   - Kontakt z właścicielem
   - Negocjacje
   - Odrzucone
   - Kupione/Zabezpieczone

   Drag and drop. Columns show counts and total hectares.
6. **Eksport**: XLSX of the current list view, and a PDF one-pager per site (map snapshot, score, key facts, next steps, sources).
7. **Źródła danych**: one row per source with last success, freshness, row counts, errors and a "Run now" button for admins.
8. **Ustawienia**: scoring weights and thresholds (sliders with live preview of rank changes), AOI editor, users, email digest settings.

### 8.2 UX principles

- **Map-first, explanation-always.** No score without "dlaczego?". Be honest about uncertainty and never show a guess as a fact.
- Polish formatting: `12,5 ha`, `8 MW`, `2,3 km`, dates `dd.mm.rrrr`.
- **Speed:** first map paint under 2 s and filter changes under 300 ms (server-side filtering with indexed columns, vector tiles via Martin).
- **Mobile:** list ⇄ map toggle, readable site cards and the Nowości feed. Editing is desktop-first, but notes and status changes work on a phone.
- Designed empty, loading and error states, including "this source is stale since …".
- Light and dark themes, WCAG AA contrast and a colour-blind-safe score scale (sequential, plus a numeric label).
- Keyboard: `/` focuses search, `j`/`k` move through the list, `o` opens the card, `w` watches.
- **Visual style:** calm, professional, data-dense but airy. A neutral base with one accent colour. Use shadcn/ui components, no default-template look.

## 9. Architecture

### 9.1 Repo layout

```
/pipeline   Python 3.12 (uv): sources/, model/, scoring/, events/, cli.py (typer)
/api        FastAPI + SQLAlchemy 2 + Alembic
/web        React + TypeScript + Vite + MapLibre GL + TanStack Query/Table + shadcn/ui + Tailwind
/config     scoring.yaml, sources.yaml, aoi
/data       raw/ (snapshots, gitignored), manual/ (CSV mappings, offer imports)
/docs       PLAN.md, STATUS.md, SOURCES.md, SCORING.md, RUNBOOK.md
/infra      docker-compose.yml, Caddyfile, cron, backup scripts
/tests      unit/, contract/ (recorded HTTP), e2e/ (Playwright), golden/
```

### 9.2 Components

- **PostgreSQL 16 + PostGIS 3.** Main tables:
  - `sources`, `snapshots`, `runs`
  - `substations`, `lines`, `gpz_capacity`
  - `zones`, `parcels`, `sites`, `site_parcels`
  - `constraints`, `site_scores`, `score_items`
  - `events`, `watches`
  - `pipeline_items`, `notes`
  - `users`, `settings`
- **Martin** vector tile server for sites, grid and zones.
- **FastAPI** for REST endpoints (sites list with filters and pagination, site detail, check-parcel, events, pipeline, exports, settings, sources) and an OpenAPI schema. Generate the TypeScript client from it.
- **Web:** a static build served by Caddy.
- **Worker:** the pipeline CLI scheduled with supercronic in its own container. The "Run now" button triggers it through a job table that the worker polls; no message broker needed.
- **Email:** SMTP via environment variables.
- **Optional Claude API** (feature flag, cached by content hash):
  - A fallback for PDF tables pdfplumber can't parse.
  - Extracting the zoning symbol and description from plan text.
  - A Polish 5-sentence summary on the site card.
  - Never use it as a source of facts without a citation to the underlying data.

### 9.3 Auth

Email and password (argon2), with users seeded via the CLI and sessions in http-only cookies. Two roles: `admin` (settings, run sources) and `member`.

### 9.4 Hosting

Docker Compose on **a VPS located in Poland**, because some Polish public services block foreign IPs. Caddy handles HTTPS, and nightly `pg_dump` backups go to off-site object storage with 30-day retention.

### 9.5 CI

GitHub Actions run ruff, mypy, pytest, eslint, tsc, vitest, a Playwright smoke test against docker-compose, and build the images.

### 9.6 Privacy

The app stores no personal data apart from users and free-text notes. Show a notice under notes about RODO and keeping personal data minimal, and allow a note to be deleted entirely. EKW and Baza OOŚ are links only.

## 10. QA

- **Unit:**
  - Geometry: AOI split, parcel clustering, area and compactness, distances in EPSG:2180.
  - Scoring functions (property-based tests with hypothesis for monotonicity: closer GPZ never lowers the score).
  - Parsers with saved fixtures (PSE PDF and XLSX, DSO JSON, RU GML).
- **Contract:** recorded HTTP responses (pytest-recording) per source, plus a weekly **live smoke test** job that only alerts and never fails the deploy.
- **Data quality gates per run:** do not publish new scores if a core source (osm_power, egib, ru, gdos, isok) fails or the row count drops by more than 30% against the last run. Keep the previous scores and raise `SOURCE_FAILURE`.
- **Golden set** in `tests/golden/sites.yaml`. These known DC locations west of the Vistula, all 10 ha or more, must **not** be hard-excluded, and should rank in the top 20% of their powiat:
  - Hillwood Reguły (130 MW, 22–40 ha)
  - Tauron Jaworzno / Wysoki Brzeg (150 MW, 54 ha)
  - Bełchatów / Domiechowice (500 MW, ~50 ha)
  - CGE Konin (260 MW)

  Data4 Jawczyce, Atman Duchnice and Switch Mory are below 10 ha, so they should be **excluded with the reason "za mała powierzchnia"**. That proves the size rule works.

  Coordinates are to be researched in Phase 0 and verified on Geoportal. This is a sanity check, not ground truth. A miss means investigate the filter, not tune to pass.
- **Manual review loop:** each phase from 2 on, review the top 30 sites on orthophoto and label them sensible or not in an admin view. Target at least 60% sensible in the top 30 by the end of the MVP. Track this in `docs/STATUS.md`.
- **E2E (Playwright):**
  - Login.
  - Filter and open a site card.
  - Check a parcel by ID.
  - Move a deal on the kanban.
  - Export XLSX and PDF.
  - Watch a site and see its event in Nowości.
- Accessibility check with axe in E2E. Lighthouse performance ≥ 85 on the home view.

## 11. Build phases (with acceptance criteria)

**Phase 0: Setup and source discovery**
- Repo, docker-compose (PostGIS, Martin, API, web, worker), CI and a hello-world map.
- Discovery spike for every MVP source. `docs/SOURCES.md` lists each one as status ✓/⚠/✗ with the verified URL, format, licence and fallback.
- Done when compose starts with one command, CI is green and every MVP source has a verified status.

**Phase 1: Base and grid layers**
- AOI, PRG, OSM grid (substations classified, lines) and BDOT10k buildings.
- Map shows the AOI and the grid by voltage. Sources page live.
- Done when substation counts per voivodeship are plausible (documented) and AOI tests pass.

**Phase 2: Candidates, sites, constraints, scoring v1**
- Zones (RU + fallback + powered brownfields), EGiB parcels inside zones, site clustering, all constraint layers, and the scoring config.
- List plus map plus site card with the score breakdown.
- Done when a full AOI run completes, the golden set check is reported and the first top-30 manual review is logged.

**Phase 3: Grid capacity and PSE data**
- Enea, Energa and Tauron connectors (or their documented fallbacks), the GPZ mapping table, the PSE closed/ODB/planned lists and the grid confidence badges.
- Done when at least 70% of GPZs in Enea and Energa areas are matched to published capacity (or the reason is documented), and the scores use them.

**Phase 4: Investor workflow**
- Sprawdź działkę, the pipeline kanban, notes, watches, XLSX and PDF export, and the connection-cost calculator.
- Done when the E2E flows pass.

**Phase 5: Monitoring**
- Snapshots and diffs, events, the Nowości feed, the weekly email digest, KOWR and manual offer imports.
- Done when two consecutive runs on fixture data produce the expected events, and a test digest email renders correctly.

**Phase 6: Hardening and deploy**
- Polish VPS, HTTPS, backups and restore test, RUNBOOK, performance and a11y targets, final golden-set and manual-review report.
- Done when it is deployed, the restore has been tested, all checks are green and STATUS.md shows no blockers.

## 12. Definition of done (MVP)

- The two users log in and see a ranked, explained list of sites of 10 ha or more (60 MW or more estimated) across the whole AOI.
- Every score shows its sources, dates and confidence. Missing data is visible, not hidden.
- Users can check any parcel by ID, track deals, and export XLSX and PDF.
- The weekly run updates data, produces events and sends a digest. Source failures degrade gracefully.
- Tests, CI, backups and the RUNBOOK are in place.

## 13. Open questions for the owner (do not block on these; use defaults)

- Final AOI: include Bielsko-Biała / Podbeskidzie? (Default: no.)
- Investment strategy (resell with conditions, lease/JV, option and flip). It affects future features such as valuation, not the MVP.
- Budget per deal. Later: land price data (RCN transaction prices) and a filter.
- Who gets the email digest and how often.
