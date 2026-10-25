# Status
## Forest filter live: `dclh search 1421` on main (22.10.2026, VPS)

- Forest (`forest_pct_over`, 30%) is fed from the BDOT10k land cover (`rodzaj = "las"`, PR #81, merged and deployed as dc92df1).
  The same run loaded the psi and heat bonus layers and the KIUG soil penalty, all from main.
- Funnel: 138,232 parcels fetched, 112,763 in the seed area, 786 clusters, 318 scored, **94 kept and 224 excluded**. 73 of the
  excluded sites carry a forest exclusion (`las: 100% terenu` in the top example). Zoning: KIMPZP `no_plan` for 282 sites, 94 `unknown`.
- Runtime: parcels 533 s, KIMPZP 294 s, KIUG 473 s, score 10 s. Peak memory 808 MB, no memory guard trip.
- **Not measured:** the effect of forest alone. The 19.10 baseline (355 scored, 194 excluded) ran on older code (site split, soil,
  flood 0.2%, bonus layers), so the difference is not attributable to the forest filter.
- Still missing (per the CLI line): soil I–II hard filter, heritage (`nid`, no coordinates in the open data).
- Log on the VPS: `data/reports/search-1421-forest.log` (not committed).

## Penalty sources: landscape parks, gas, airport OLS, soil class III (21.10.2026, PRs #76–#78)

Before: seven penalties showed "Brak danych". Three (flood 0,2%, mining terrain, housing) already had sources and
only need a rerun. The other four had no source. After these PRs:

| Penalty | Source | Confidence | Notes |
|---|---|---|---|
| Park krajobrazowy / OChK | GDOŚ WFS `ParkiKrajobrazowe`, `ObszaryChronionegoKrajobrazu` | verified | penalty only, never the protected-area exclusion; buffer zones dropped |
| Strefa gazociągu | OSM `man_made=pipeline` + gas (877 ways, 8,771 km) | estimated | about 80% of the transmission network; distribution mostly missing |
| OLS lotniska | OSM `aeroway=runway` (327 of 413 used) | estimated | ICAO Annex 14 inner horizontal radius by runway length |
| Gleby klasy III | KIUG GetFeatureInfo per parcel, kept sites only | estimated | contour shares estimated; I–II exclusion not fed; powiat 1432 publishes no contours |

Verified live on the VPS: GDOŚ layers (213 and 406 features), the OSM reader on the real extract, KIUG on 5 sites of
1421 (43 points in 43 s, 0–32%). **Not done:** merge, deploy, `dclh fetch gdos` and the rerun of the 8 powiats with
sites, so no card shows the new values yet. KIUG adds about 1 s per sample point (5 per kept site): about 67 min for
0201, about 6 hours for all 8.

## Phase 3 live data: DSO and PSE loaded, GPZ match report (19.10.2026, VPS)

- Connectors on live data: `dclh fetch tauron` (513 GPZs, locations published, 100% matched by location) and
  `dclh fetch energa` (PDF of 31.08.2026, 48 groups, 396 stations). `dclh import-manual pse_closed` (37 rows, 33 stations) and
  `dclh import-manual pse_odb` (ODB only, 24 stations; the applicant column is not stored).
- `dclh gpz-report` (CSV under `data/reports/`, not committed):

| Source | Matched | Rate | Method |
|---|---|---|---|
| Energa (acceptance ≥ 70%) | 274 / 396 | 69% | fuzzy name match, score ≥ 90 |
| PSE closed switchyards | 32 / 33 | 97% | fuzzy, on OSM 220/400 kV nodes |
| PSE ODB | 18 / 24 | 75% | fuzzy, on OSM 220/400 kV nodes |
| Tauron | 513 / 513 | 100% | published location |

Energa misses 122 names. Most are below the threshold ("najlepsza zgodność" 60–89) or are ambiguous between
two OSM stations of the same name. Energa at 69% is just under the 70% acceptance line; the gap is documented,
not forced with guessed mappings. Next step: add reviewed lines to `data/manual/gpz_mapping.csv` for the
ambiguous names (Sławno, Pomorska, Ostrów, Bojanowo, Kalisz Pomorski), then re-run the report.
- Matching fixes made on the live names: OSM voltage specs ("110/15kV") and "SE"/"RPZ"/"Systemowa" prefixes
  removed, roman numerals equal digits, busbar sections folded, a DSO's name does not match another DSO's
  station, and one OSM station keeps only its best name. Threshold raised 88 → 90 (88 let "Ostrowite" match
  "Ostrowiec").
- **Not done:** `dclh search 1421 --write` has not been run with these layers, so the score changes are not yet measured.
  The baseline for comparison is the 19.10.2026 run above (315 sites, all "Bliskość sieci", 22 not excluded).
  `pse_avail` (planned stations) still needs its ~24 stations geocoded by hand.

Last updated: 19.10.2026 · Phase 1 closed (admin_units loaded, substation counts per voivodeship); Zoning layer (`ru`, `kimpzp`), 1421 rerun (writes 315 sites); Phase 3 live DSO/PSE data and GPZ match report; earlier: Flood (`isok`) and housing (`bdot10k`) connectors, pilot rerun; earlier: source verification round 2 (20 of 23 MVP sources ✓, Enea manual-only); earlier: Phase 1.5 pilot run on the VPS (powiat 1421); Phase 1 started; Phase 2 code done on test data; Phase 3 code done on fixtures (no live data yet)

## Zoning layer: `ru` and `kimpzp` connectors (19.10.2026, run on the VPS)

- `sources/ru.py` pages the RU WFS over the powiat bbox: `app-pog:StrefaPlanistyczna` (plan ogólny zones) and
  `app-mpzp:AktPlanowaniaPrzestrzennego` (MPZP outlines, no designations). 10 features per request (the server's
  limit), `startIndex` paging, `numberMatched` check, northing-first axes checked. The zone type and plan status are
  xlinks, read via GDAL's `GML_ATTRIBUTES_TO_OGR_FIELDS`. The server refuses (400) any page that holds a broken
  feature; the connector then asks that page one by one and skips only the refused features (logged, kept in
  `skipped.json`; it fails above 20). In 1421 one MPZP outline is broken and skipped.
- `sources/kimpzp.py` asks WMS 1.1.1 GetFeatureInfo at up to 3 points per site (middle, then each half), **only for
  the sites the hard filters keep**, and keeps every raw answer in the snapshot. It reads Warsaw's XML rows
  (`FUN_SYMB`), GeoServer tables, ESRI `FIELDS` and "brak wyniku"/"brak serwisu". Anything else is "unparsed". A gmina
  server that fails leaves that point without a plan; it does not fail the search.
- **Correction: the strefa gospodarcza is `SP`, not `SG`** (`nazwa = …/strefaGospodarcza`). The zoning model reads the
  zone type, not the symbol. The UI label now says "(SP)"; the config key `plan_ogolny_SG` keeps its name.
- Mapping (`model/zoning.py`): MPZP P / PU / P-U / U-P / E / IT → production (area numbers like `3k U-O/US` are read).
  Plan ogólny in force: SP → `plan_ogolny_SG` (80), other zones → `other`. Plan ogólny draft: SP → `procedure_in_progress`
  (50), other draft zones are ignored (not binding). MPZP outline in procedure → `procedure_in_progress`; in force →
  ignored (no designation; KIMPZP has to say what it allows).
- Precedence, first layer with a zone covering ≥1% of the site wins: KIMPZP designation (`estimated`, point samples) >
  RU MPZP procedure > RU plan ogólny (`verified`). No plan anywhere stays `unknown` (30%, `missing`).

Layers for 1421 (bbox of the powiat): 52 MPZP outlines (42 in force, 10 in procedure; 1 more skipped as broken) and
1,149 plan ogólny zones, **all drafts** (`elaboration`), of which 49 SP. KIMPZP: 66 points at the 22 kept sites, all
"no plan" (gminas answer "brak wyniku dla wskazanego obszaru" or an empty GeoServer page). RU fetch about 2.5 min, KIMPZP 68 s.

`dclh search 1421 --write` rerun (branch code, same 315 sites and the same 293 exclusions):

| Zoning of the 315 sites | Before | After |
|---|---|---|
| `unknown`, no source (`missing`) | 315 | 0 |
| `unknown`, register loaded but no plan (`missing`) | – | 310 |
| `procedure_in_progress` from RU (`verified`) | – | 5 (1 not excluded) |
| MPZP production / plan ogólny SP in force | – | 0 |

- Score change: +5.0 points for those 5 sites (zoning 7.5 → 12.5 of 25), completeness 0.314 → 0.343. The other 310
  sites are unchanged (unknown scores the same 30% as before, now with a source).
- The one kept site that changed is `site-87f9a89cc8ea` (10.6 ha, Ożarów Mazowiecki): 52.9 → 57.9, now first among
  the 22 kept sites (it was third). The two top-scored sites of the powiat (`site-26997a0277e7` 61.6,
  `site-fb36cfdca6bf` 60.6) are procedure sites but excluded by the hard filters.
- Zoning confidence among the 22 kept sites: 21 `missing`, 1 `verified`. Zoning is still mostly unknown in 1421,
  because no plan ogólny there is in force yet and KIMPZP has no vector plan at any kept site.
- Not done: the zoning seed (search inside SP zones far from the grid); plan ogólny drafts of other zones as a
  negative signal; a flag for "MPZP in force, designation unknown". **Rerun after 30.11.2026**, when the Rejestr
  Urbanistyczny must be complete and plans ogólne come into force. The nightly job runs the old code until this
  branch is merged and deployed.


## Housing filter and cluster split: why Reguły was excluded (19.10.2026)

The 19.10.2026 run of `dclh search 1421 --write` (section below) excluded 293 of 315 sites, all six sites in obręb
0013 Reguły among them, and no golden parcel reached a scored site. Replayed offline from the same snapshots
(identical funnel: 315 scored, 293 excluded) and checked:

- **(a) Which buildings trigger it.** Real homes, not a data fault. Of 43,015 residential buildings in the powiat,
  94% are `budynek jednorodzinny`, 3% `wielorodzinny`, 1% `dom letniskowy`; 99.1% are in use. Only 24 are tagged
  together with `budynek gospodarczy` (farm outbuildings are a separate function and do not pass the filter). 159 of the 286 housing-excluded sites have a home inside the cluster
  (median 1, max 195: town fringes); the other 127 have one 0–100 m outside the edge.
- **(b) How distance was measured.** From the site edge to the nearest building; a building inside the site is 0 m.
  That is the fault: a 69.8 ha Reguły site was excluded for one house 2.3 m past its boundary although 57.9 ha of it
  lie more than 100 m from every home. Of the 199 sites excluded by housing alone, 127 still have a connected
  part of 10 ha or more beyond 100 m from any home.
- **(c) Threshold.** 100 m is fine and unchanged. The brief's deal killer is "housing too close" to the data centre,
  which on a 30–70 ha site can stand 100 m+ from the fence.
- **(d) Golden parcels.** `.409` (0.22 ha) falls under the 1 ha lot cut, which leaves `.408` (1.77 ha) on its own.
  `.562/2`, `.562/3` and `.562/4` touch each other (13.1 ha together), but the 80 ha grid cut put them in different
  cells: one piece of 4.3 ha and one of 8.75 ha, both dropped as under 10 ha. `.564/2` was in a 35.6 ha site,
  excluded for a house 1.2 m away. The cut dropped 546 pieces (2,044 ha) under 10 ha across the powiat.

**Fix** (PR "Housing filter: clear area, not edge distance; keep split pieces of 10 ha+"):

- New fact `residential_clear_ha`: the largest connected part of the site 100 m or more from every residential
  building (buildings inside count). `residential_within_m` excludes a site when that is under `target.ha_min`
  (10 ha). `residential_min_m` stays for display; the 10-point penalty now applies to any kept site with a home
  under 300 m (it was 100–300 m, and under 100 m was always excluded).
- After the 80 ha cut, a piece under 10 ha joins the smallest piece it touches. Separate clusters never merge.

Replay with the fix (same snapshots):

| Measure | Before | After |
|---|---|---|
| Clusters / under 10 ha | 1,239 / 924 | 825 / 470 |
| Scored sites | 315 | 355 |
| Excluded | 293 | 194 |
| · housing (only) | 286 (199) | 111 (83) |
| · flood (only) | 85 (6) | 102 (75) |
| · GDOŚ | 9 | 9 |
| Not excluded | 22 | 161 |

Live rerun on the VPS with this branch (`dclh search 1421 --write`, 19.10.2026): 355 sites written, 194 excluded, same as the replay. The first attempt stopped on the OSM memory guard (745 MB free, 800 MB wanted) and wrote nothing.

Reguły after the fix: `.562/2`–`.562/4` form an 18.1 ha site (a home inside, 11.9 ha clear), not excluded, rank 75
of 161. `.564/2` is in a 41.1 ha site (33.1 ha clear), rank 19 of 161 (top 12%). The 69.8 ha site is still out, for
flood. Two small Reguły sites stay excluded for housing (1.4 and 2.4 ha clear). `.408` is still not scored (1 ha lot
cut). The golden expectation (top 20%) is met by the `.564/2` site; the golden coordinates are still unverified.


## Flood and housing hard filters: `isok` and `bdot10k` connectors (19.10.2026, run on the VPS)

- `sources/isok.py` fetches the ISOK `wms_nz-core:NZ.Fluvial` zones with `loo_ql_probabilityofoccurrence = 0.01`
  (Q1%, including the levee-breach scenario) for the powiat bbox, pages with `startIndex`, checks `numberMatched`,
  swaps the northing-first axes and clips the zones to the bbox. It fails if no zone touches the bbox (wrong axes).
  The 10% zones are not fetched: they lie inside the 1% extent. The 0.2% zones (a penalty) are not fetched yet.
- `sources/bdot10k.py` looks up `URL_GML` for the powiat in `ms:BDOT10k_powiaty`, downloads the zip (only from
  `opendata.geoportal.gov.pl`, only that powiat's file) and keeps `OT_BUBD_A` buildings with
  `funkcjaOgolnaBudynku = "budynki mieszkalne"` that are not `zniszczony` (buildings under construction count).
- `dclh search` (and the nightly `search` job) download both for the powiat at run time, like the parcels, and feed
  them as `flood_10_or_1` and `residential` (confidence `verified`). A failed download fails the search rather than
  scoring without the filters. Neither is in `dclh fetch` (both need a powiat).
- Layer sizes for 1421: 6 Q1% zones (9.1 km² of the 246 km² powiat; maps valid to 21.12.2025, second cycle);
  43,015 residential buildings (of 60,434). Download: ISOK 3.4 MB zip, BDOT10k 18.6 MB.

`dclh search 1421 --write` rerun (same funnel as 14.10.2026; 315 sites written):

| Measure | 14.10.2026 | 19.10.2026 |
|---|---|---|
| Scored sites | 315 | 315 |
| **Excluded by hard filters** | 0 | **293** |
| · housing within 100 m (`residential_within_m`) | – | 286 (199 for this reason only) |
| · flood zone 10% or 1% (`flood_10_or_1_pct`) | – | 85 (6 only) |
| · Natura 2000 / park / reserve over 20% (`protected_nature_pct_over`) | – | 9 |
| Not excluded | 315 | 22 |
| EGiB download | 1,300 s | 513 s |
| Peak memory | 703 MB | 747 MB |

Flood vs housing among the 293: 199 housing only, 6 flood only, 79 both; the other 9 include the GDOŚ filter.
Removing the flood filter would bring back 6 sites; removing the housing filter would bring back 199.

Hillwood / Reguły: none of the three candidate parcels in `tests/golden/sites.yaml` (obręb 0013: `.408`, `.409`,
`.562/2`) is in any scored site, so the Hillwood plot is not among the 315 (not checked why: too small after the
1 ha lot cut, or outside the seed). Six scored sites contain other parcels of obręb 0013 Reguły; all six are excluded
by housing (nearest residential building 0–17 m), two of them also by flood. The three sites nearest the Reguły village
centre (394–456 m away) are among them. The golden-set coordinates are still unverified.

Reading the numbers: Pruszków is suburban, so almost every 10 ha+ cluster of farm parcels has a house within 100 m.
The 100 m threshold (`config/scoring.yaml`) is the owner's call; with it, 22 sites survive. The top ten by score are
all excluded.


## Phase 1 close-out (19.10.2026, on the VPS)

Phase 1 is finished: both acceptance items pass on live data.
- `dclh load-admin` loads the PRG snapshot of 12.10.2026 into `admin_units`: 16 voivodeships, 380 powiats, 2,479 gminas,
  parent TERYT from the code prefix. TERYT lengths were checked on the real snapshot (2 / 4 / 7 digits, no duplicates).
- `dclh grid-counts` (read-only) counts the loaded substations per voivodeship, by class and, for GPZs, by DSO. A station
  on a shared border is counted once.
- Tests: `tests/unit/test_admin_units.py` (parents, Polygon → MultiPolygon, reload replaces, bad TERYT keeps the old rows,
  wrong CRS refused, border and outside-Poland stations, counts need `admin_units`).

| TERYT | Voivodeship | GPZ 110/SN | Node 220/400 | Unknown voltage | GPZ by DSO tag |
|---|---|---:|---:|---:|---|
| 02 | dolnośląskie | 145 | 11 | 2,132 | Tauron 120, Energa 1, none 24 |
| 04 | kujawsko-pomorskie | 77 | 6 | 559 | Energa 40, Enea 7, none 30 |
| 06 | lubelskie | 47 | 6 | 827 | PGE 27, none 20 |
| 08 | lubuskie | 52 | 5 | 337 | Enea 33, none 19 |
| 10 | łódzkie | 87 | 9 | 1,016 | PGE 45, Energa 9, Tauron 1, none 32 |
| 12 | małopolskie | 137 | 7 | 124 | Tauron 124, PGE 2, none 11 |
| 14 | mazowieckie | 183 | 21 | 774 | PGE 72, Stoen 45, Energa 29, none 37 |
| 16 | opolskie | 62 | 5 | 296 | Tauron 54, none 8 |
| 18 | podkarpackie | 51 | 5 | 422 | PGE 24, Tauron 6, none 21 |
| 20 | podlaskie | 50 | 2 | 370 | PGE 45, none 5 |
| 22 | pomorskie | 106 | 9 | 1,069 | Energa 69, none 37 |
| 24 | śląskie | 222 | 23 | 1,135 | Tauron 206, none 16 |
| 26 | świętokrzyskie | 58 | 5 | 132 | PGE 51, Tauron 1, none 6 |
| 28 | warmińsko-mazurskie | 72 | 5 | 336 | Energa 46, PGE 11, none 15 |
| 30 | wielkopolskie | 152 | 10 | 896 | Enea 49, Energa 39, none 64 |
| 32 | zachodniopomorskie | 92 | 10 | 912 | Enea 31, Energa 7, PSE 1, none 53 |
| | outside Poland | 1 | 5 | 125 | none 1 |
| | **total** | **1,594** | **144** | **11,462** | Tauron 512, PGE 277, Energa 240, Enea 120, Stoen 45, PSE 1, none 399 |

Plausibility:
- **220/400 kV nodes:** 107 of the 144 carry the PSE operator tag. PSE reports 110 extra-high-voltage stations
  ("Inwestycje sieciowe PSE", 03.2024). The other 37 are power-plant and industrial switchyards (23 foreign or untagged)
  and DSO stations with a 220 kV side (14).
- **GPZs:** Tauron-tagged GPZs (512) match the 513 GPZs in Tauron's load-capacity API (`docs/SOURCES.md`). No published
  GPZ count was found for Enea, Energa or PGE. Their tagged counts are lower bounds: 399 GPZs (25%) have no DSO in the
  `operator` tag, most in Enea and Energa territory (wielkopolskie 64, zachodniopomorskie 53). The territory-polygon
  fallback for those is now a Phase 3 task.
- **Distribution:** śląskie, mazowieckie, wielkopolskie and dolnośląskie lead; lubuskie, opolskie and podlaskie trail.
  That matches population and industry.
- **Unknown voltage (11,462):** stations without a readable voltage tag, mostly small MV/LV transformer stations (only
  1,246 have a name, 911 a DSO). They are on the map at confidence `missing`. Search seeds and scoring do not use them.

Moved out of Phase 1 (see `docs/PLAN.md`): `osm_land` and the `buildings` table to Phase 2; the DSO territory fallback and
the count of independent circuits to Phase 3. Toruń is in the AOI by the owner's decision of 20.10.2026 (see "The AOI" below).

## Source verification round 2 (19.10.2026, from the VPS)

Details and request recipes: `docs/SOURCES.md` ("Current state"). Samples: `tests/fixtures/live/source-verification-2026-10-19/`.
Docs, probe config and fixtures only. No connector code changed.

- **20 ✓, 1 ⚠, 2 manual.** Newly confirmed: `bdot10k`, `ru`, `isok`, `pse_closed`, `pse_odb`, `pse_avail`, `energa`,
  `tauron`, `psi`, `heat`. Still ⚠: `uke` (lead only). `enea` is manual CSV only (owner decision 19.10.2026): its Power BI map covers MV/LV only and Enea has no statutory load-capacity PDF; its art. 7 ust. 8l PDFs list about 82 load entries at 110 kV (queue signal, hard to parse).
- Hard filters now have verified data: flood = ISOK `NZ.Fluvial` with probability 0.01; housing = BDOT10k
  `OT_BUBD_A` with `funkcjaOgolnaBudynku = "budynki mieszkalne"`. Both are now loaded (section above).
- Zoning: RU WFS gives plan ogólny zones (`symbol`; the strefa gospodarcza is SP, see the zoning section) and MPZP **outlines** only. MPZP designations come from
  KIMPZP GetFeatureInfo (WMS 1.1.1 only), point by point and in a different format per gmina.
- Grid: Tauron has a JSON API per GPZ (513 GPZs). Energa publishes a statutory PDF per group of 110 kV nodes. PSE's
  art. 7 ust. 8l list is an XLSX with 33 load (ODB) projects, mostly data centres. Enea's map is Power BI and covers
  MV/LV only.
- Axis order differs per service: ISOK and RU answer northing-first in EPSG:2180, BDOT10k is easting-first.
- `dclh discover-followup` reported the KIMPZP WFS as "available" although it lists 0 layers (wrong verdict text, not
  fixed here). It also missed the RU route modules; the RU WFS addresses were found in the browser instead.
- Decisions for the owner: (1) Tauron terms before a scheduled connector; (2) the PSE and DSO 8l lists name applicants.
  The plan is to store station, MW, type and status only.

## Protected areas: `gdos` connector (14.10.2026, code and tests)

- `sources/gdos.py` fetches the four GDOŚ WFS layers (Natura 2000 SOO and OSO, national parks, reserves) into one zip
  snapshot, with the same paging and truncation checks as PRG. `dclh fetch gdos` saves and parses it.
- `dclh search` now needs a saved `gdos` snapshot (like `prg` and `osm_power`) and feeds it as `protected_nature`, so the
  hard filter `protected_nature_pct` (threshold 20%) acts. Before, that filter never ran.
- Fetched on the VPS on 14.10.2026 (snapshot in `data/raw/gdos`, 26 MB). In the 19.10.2026 rerun of 1421 it
  excludes 9 sites (see the flood and housing section).

## Phase 1.5: pilot powiat on the VPS (14.10.2026)

`dclh search 1421 --write` (pruszkowski) ran end to end on the VPS and wrote 315 sites, so the app's list now has real rows.
DB before the run: 1 AOI (about 188,000 km², valid), 13,200 substations, 16,105 lines; `admin_units` is empty (PRG is read from the snapshot at run time).

| Measure | Value |
|---|---|
| Grid seed area | 246 km² |
| EGiB tiles (1 km) / HTTP requests | 314 / 350 |
| Parcels downloaded / inside seed | 138,183 / 113,969 |
| Parcels under the minimum area (dropped) | 110,319 (97%) |
| Clusters / under minimum area / scored | 1,239 / 924 / 315 |
| Excluded by hard filters | 0 (no constraint layers loaded yet) |
| Runtime | parcels 1,300 s (about 4.1 s per tile); seed, cluster, score under 2 s |
| Peak memory | 703 MB |

Reading the numbers:
- EGiB download is the whole cost: about 5.3 s per km² of seed. At that rate every 10,000 km² of seed takes about 15 h, so a full AOI run needs a smaller seed or a smarter tile choice (97% of downloaded parcels are discarded as too small). This is risk R7.
- Scores are 57.8 to 59.3 and all 315 sites are close together: zoning, constraints, housing and DSO/PSE data are all `missing`, so the ranking only reflects grid distance and size. Do not read it as a real ranking yet.
- Not checked: whether Reguły (Hillwood) is among the sites; its coordinates are still unverified.

## Discovery follow-up for the open sources (code on mock answers; not yet run from Poland)

`dclh discover-followup` (`followup.py`, run instructions in `docs/RUNBOOK.md`) asks the next question for `bdot10k`
(download link in the powiat index?), `kimpzp` (WFS next to the WMS? answer at Reguły), `isok` (services on the INSPIRE
page) and `ru` (API addresses in the JavaScript of its federated modules). It exists because the 12.10.2026 discovery run
found only that these are reachable: `ru` is an Angular app whose data calls the browser capture never saw, `bdot10k` has a
single WFS layer and a dead page, and the `isok` capture stopped at the home page. KIMPZP is included because it is already
verified and has vector layers (`wektor-pow`, `plany`), so it may give zoning without `ru`.
14 tests on canned answers (parsing, WMS 1.1.1 axis order, per-section failure isolation, CLI). **Next on the VPS:** run it,
paste the report, and record the confirmed endpoints in `docs/SOURCES.md`.

## Per-powiat search and nightly queue (code, tested; deploy after merge)

- Sites now belong to a powiat (migration `0005`, rows from the 1421 pilot tagged as such). A search replaces only its own
  powiat's sites (`write_sites(..., powiat=)`), which fixes a bug that would have wiped the other powiats.
- `search_powiat` (`powiat_search.py`) is shared by `dclh search` and the `search` job. A failed attempt is recorded in
  `search_runs` in its own transaction, so the planner moves a failing powiat to the back of the queue.
- `plan_searches` job (cron `0 1 * * *` Warsaw): queues the 8 powiats attempted longest ago (never-attempted first), skipping
  any already queued. Each search job carries `not_after` = 05:00 Warsaw and is skipped if it starts after that.
- Not done yet: the tile skip for built-up areas (needs `osm_land`), the per-tile parcel cache, and the admin_units load.
  Measure the seed (step 1 of the optimisation plan) before tuning these.

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
- **Scoring run**: `dclh search --write` scores and writes sites (there is no separate `dclh score`), but the new
  layers are not yet loaded in a real run. `build_site_features` and the engine already consume them, and tests cover that path.

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
  9 tests on synthetic parcels. `expansion_ha` (score item "Rezerwa na rozbudowę"): the area of other fetched EGiB
  parcels within 20 m of the site (`EXPANSION_GAP_M`, about one road width), outside the site, overlaps counted once.
  It includes the small lots dropped by `search.min_parcel_ha`, parcels of other sites and parcels across a powiat
  border. Fact: source `egib`, confidence `verified` (the geometry is authoritative). Limits: EGiB has no land use
  here, so road and water parcels count too; nobody checks a neighbour's zoning, owner or whether it is for sale (the
  card says so); land beyond the fetched 1 km tiles is not seen.
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
  need a Polish machine (Phase 0). Not yet: the map choropleth, the `dclh score` command, and the
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
- Click popups (`web/src/components/map/popup.ts`): clicking a station or a 110 kV+ line opens a Polish popup with name,
  class, voltages, operator (+ DSO, "obcy operator" flag), confidence and source, WGS84 coordinates with a copy button,
  and links to Google Maps and the OSM object. The position is labelled "wg OpenStreetMap, nie pomiar geodezyjny".
  Tested with a mocked MapLibre (no WebGL in vitest) and pure-function tests, including that OSM text is never
  interpreted as HTML. **Not yet seen in a real browser against Martin tiles:** how Martin encodes the `voltages` array
  is unverified, so the parser accepts an array, a JSON string and `{a,b}` text. The tile has no `as_of`, so the popup
  shows no date. Station coordinates come from the tile geometry (quantised, roughly metres at high zoom).
- Not done: clipping to the AOI.

## The AOI: `dclh aoi build` (12.10.2026 run on the real PRG and OSM snapshots)

- Builds `data/aoi/aoi.geojson` (WGS84) and replaces the `aoi` row (EPSG:2180, one MultiPolygon, 187,923 km²
  against 322,508 km² for the PRG country polygon). Martin serves it as `tiles/aoi`.
- The Vistula is **not** one way in OSM. The name `Wisła` matches about 50 `waterway=river` ways: the stem, parallel
  channels, loops round weirs and stubs. `main_stem` takes the path between the two free ends that are farthest apart,
  so side channels and stubs cannot cut the country. The stem is 1,013 km long. Stream ways named `Wisła` are excluded.
- Result, share of each voivodeship inside the AOI: 100% for wielkopolskie, łódzkie, dolnośląskie, lubuskie, opolskie,
  zachodniopomorskie and świętokrzyskie; 87% śląskie; 85% pomorskie; 64% kujawsko-pomorskie; 33% mazowieckie;
  22% małopolskie; 1% lubelskie; 0% podkarpackie, warmińsko-mazurskie and podlaskie.
  Checked points: Bielsko-Biała, Żywiec, Lublin, Praga, Elbląg, Malbork, Tarnów and the south bank of Kraków are out;
  Cieszyn, Kraków north of the river, Warsaw's left bank, Gdańsk, Bydgoszcz and Łódź are in.
- **Toruń is in the AOI (owner decision, 20.10.2026).** The river bends west there (through Fordon), so the split alone
  puts Toruń's centre (18.60 E, 53.01 N) on the south bank. `config/aoi.yaml` now lists the units to add whole after
  the split: gmina `0463011` Toruń (PRG name `Toruń`, which contains the centre). Each TERYT must match one PRG unit by
  level and code, or the build fails. The build prints the area of each unit it adds and records the codes in the
  `aoi` note. The committed `data/aoi/aoi.geojson` and the VPS `aoi` row still show the split without Toruń until
  `dclh aoi build` is run again (see the PR for the command).
- Checked locally on the real PRG and OSM snapshots (21.10.2026, no DB or file writes): the AOI goes from 187,923 km²
  to 188,009 km². Gmina 0463011 adds 115.6 km², of which about 86 km² was not already in the AOI. Toruń's centre is
  out without the list and in with it. Praga (21.05 E, 52.25 N) and Lublin (22.57 E, 51.25 N) stay out. Warsaw's
  left bank and Poznań stay in.
- **Run it in the container** (21.10.2026): `docker compose -f infra/docker-compose.yml exec api dclh aoi build` (about 4.5 min).
  The GeoJSON is written to `<data dir>/aoi/aoi.geojson`: the data volume in the containers, the repo's tracked
  `data/aoi/aoi.geojson` on the host. Before this fix the container run computed for minutes and then failed on
  `PermissionError: /app/data/aoi` (nothing was written to the database). The output folder is now created first, so a
  bad path fails at once. To refresh the tracked file, run the build on the host and commit the result; the container
  copy is identical (same SHA-256 on the 12.10.2026 snapshots).
- **Martin returned empty tiles after the first load.** `tiles/aoi` gave 204 from zoom 5 up, although PostGIS returned
  the feature for the same tile. A restart of `martin` fixed it. The cause is not confirmed (most likely its cached
  table metadata from startup, when the table was empty). Until it is understood, restart Martin after a first load
  of `aoi`, `substations` or `lines`: `docker compose -f infra/docker-compose.yml restart martin`.
- Tests: `tests/unit/test_aoi_build.py` (main stem with a loop, a stub and shuffled, reversed ways; the PBF reader on a
  synthetic extract; GeoJSON round trip; voivodeship shares; the include list: TERYT matching by level, config
  validation, the committed `config/aoi.yaml`; the DB row replace, which needs PostGIS). `tests/unit/test_aoi.py`
  checks that an included unit on the excluded side is added whole and that a neighbour not on the list stays out
  (synthetic, so Toruń and Praga are stand-ins).

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

## Ops: job health panel "Zadania" (21.10.2026, tested on PostGIS; not yet deployed)

- Admin-only `GET /api/ops/jobs` and a **Zadania** page: job counts (24 h), powiats whose latest search job failed
  (with the error), and the last 50 jobs. Read-only, from `jobs`; no migration.
- Alert: a red count on the nav entry (admins only) = failing powiats + failed non-search jobs of the last 24 h.
  Refreshes every 5 minutes.
- Why `jobs` and not `search_runs`: a failed search rolls back, so `search_runs` kept showing `ok` for powiats whose
  nightly job had been failing since 14 Oct (duplicate `site_id`, fixed by giving each border parcel to one powiat).
- Not done on purpose: email/push alerts (no SMTP until the Phase 5 digest), retry button, log viewer, charts.

## Powiat checklist and priority list "Powiaty" (23.10.2026, tested on PostGIS; not yet deployed)

- New **Powiaty** page: all powiats grouped by województwo, *checked* = a successful search exists (automatic), a
  "Priorytet" toggle for not-checked powiats, and Zapisz / Cofnij. Backed by `GET /api/powiats` and
  `PUT /api/powiats/priority` (any logged-in user) and the new table `powiat_priority` (migration `0008`).
- `plan_searches` merges the cron payload's `priority` (PR #90: 0805, 3021, 1465) with the saved list and forces them
  ahead of the rotation until each has one successful search. A saved list applies from the next 01:00 run.
- Not done on purpose: manual "checked" ticks, re-prioritising a checked powiat, "search now" button.
- Deploy needs the migration (`alembic upgrade head` runs with the stack) and a hard refresh.

## Powiaty tab: search and "Następne w kolejce" (23.10.2026, tested on PostGIS; not yet deployed)

- Search box above the list: from 3 letters it filters live (name, TERYT or województwo name; case and Polish accents
  ignored) and opens only the województwa with a match. It combines with the Sprawdzone / Niesprawdzone filter.
- New card next to "Kolejka priorytetowa": the next 4 powiats the nightly run would search, from the saved list
  (`next_up` in `GET/PUT /api/powiats`, computed with the planner's own `pick_powiats`). Unsaved toggles are not simulated.
- Migration `0009` moves the starter priorities (0805, 3021, 1465) from `infra/crontab` into `powiat_priority`
  (`added_by = 'seed'`); the crontab payload no longer carries `priority`, so the tab is the only place to change it.


## Navbar: "Kolejka" second, Źródła danych under Ustawienia (23.10.2026, web tests green; not yet deployed)

- The "Powiaty" tab is now **Kolejka** (route is `/kolejka` since 23.10.2026; `/powiaty` redirects) and sits second in the navbar, right after "Mapa i lista".
- "Źródła danych" left the navbar: it is a section at the bottom of Ustawienia. `/zrodla` redirects to `/ustawienia`.

## Sprawdź działkę, step 1: spike on the VPS (24.10.2026)

`dclh check-spike` run in the worker container (Poland), one parcel in a powiat searched before and one in a powiat never
searched. Area layers only, for a 1.5 km buffer; the static layers (grid, OSM cuts, GDOŚ and so on) are not in these
numbers. "Warm" is the second pass, read from the response cache. No step failed. Raw log: `data/reports/check-spike-2026-10-24.log` (on the VPS, not in git).

| Step | Reguły `142104_2.0013.562/2` cold / warm | Konin `306201_1.0002.1573` cold / warm |
|---|---|---|
| ULDK by id, by point | 0 s, 1.0 s / 0 s | 0 s, 0.9 s / 0 s |
| EGiB neighbours (1 km tiles, 1 request/s) | **34.7 s** / 7.4 s (16,245 rows) | **24.4 s** / 5.2 s (9,669 rows) |
| Flood zones (isok) | 8.5 s / 1.2 s (6.7 MB) | 10.8 s / 1.5 s (8.6 MB) |
| Mining (midas) | 4.1 s / 0 s | 4.1 s / 0 s |
| RU zoning (MPZP + plan ogólny) | 2.1 s / 0 s | 2.1 s / 0 s |
| BDOT10k powiat zip (housing, forest) | 4.4 s / 3.0 s (18.6 MB, 43,015 rows) | 2.5 s / 1.1 s (9.6 MB, 8,043 rows) |
| KIMPZP and KIUG point | 1.4 s / 0 s | 1.6 s / 0 s |
| **Total** | **56.2 s / 11.6 s** | **46.5 s / 7.8 s** |

Peak process memory 282 MB and 273 MB (about 157 MB of it is the Python start-up).

What this settles for the plan:
- **Assumption C holds:** `GetParcelById` returns EPSG:2180 geometry, the same parcel as the by-point lookup
  (fixtures in `tests/fixtures/live/check-spike-2026-10-24/`). An unknown id and a malformed id give the same
  `-1 brak wyników` answer with HTTP 200.
- **The 30 s promise holds for a warm check** (8 to 12 s of area layers). A cold check is about a minute, below the
  75 s the plan assumed. Default estimates for the loader: cold about 55 s, warm about 12 s.
- **EGiB neighbours are the cold cost** (25 to 35 s: tiles at 1 request/s, used only for the expansion land). A smaller
  radius or fewer tiles is the first lever if cold checks must be faster.
- **BDOT10k is not the bottleneck** (2.5 to 4.4 s cold, 18.6 MB), so no derived per-powiat file: a small in-process
  cache of the parsed frames is enough (step 3).
- **Not measured:** memory of the static layers. Step 3 must measure the check worker's peak before the `mem_limit` is final.
- **Name search is not usable yet:** `GetParcelByIdOrNr` with "Konin 0002 1573" returns no result, so "obręb + numer"
  needs another request form before the UI offers it.
- Bug found and fixed: `check-spike --out` failed when the target folder did not exist.

## Sprawdź działkę, step 2: the job queue splits by kind (24.10.2026, tested on PostGIS; not yet deployed)

- `dclh worker` takes `--kinds` and `--exclude-kinds` (comma lists); `claim_next`, `fail_interrupted` and `work` take the
  same filters. Without them nothing changes.
- **`fail_interrupted` is scoped to the worker's own kinds.** It used to mark every running job failed at start-up, which
  assumed one worker: restarting the check worker would have killed a search that was still running.
- Compose: `worker` runs with `--exclude-kinds check_parcel` (so a new job kind is never orphaned) and the new
  `check-worker` with `--kinds check_parcel --interval 2` and `mem_limit: 800m`. The check worker is idle until the
  `check_parcel` job exists (step 5). A test keeps every handled kind with exactly one worker.
- The Zadania page labels the new kind "Sprawdzenie działki".
- Deploying adds one idle container; no migration.

## Sprawdź działkę, step 6: web foundations (24.10.2026; not yet deployed)

- Tokens for the survey sheet (`--paper`, `--ink`, `--ink-2`, `--ink-3`, `--layer-*`, `--fill-*`) in light and dark, and
  the font IBM Plex Sans Condensed (`@fontsource/ibm-plex-sans-condensed` 5.3.0, 400 and 500) for sheet annotations.
- Shared components in `web/src/components/site/`: `Mark` and `MarkLegend` (✓ ≈ ? ✕, each a shape and a word),
  `Ruler` (score on a 0 to 100 ruler with the potential hatched and ticks at 40 and 70) and `Protokol` (the ruled
  inspection table, with failed conditions first).
- **The site card page now uses them** (owner verdict, 24.10.2026): the round badge becomes the ruler, the stack of
  category cards becomes the Protokół, and the "Lokalizacja nie spełnia warunków minimalnych" card becomes the
  "Warunki konieczne" group. The list keeps its round badge.
- Not done on purpose: the verdict sentence (needs the pipeline, step 4), the sheet (step 7), a visual review in a browser.

## Sprawdź działkę, steps 3 to 5: the check pipeline, job and API (25.10.2026; not yet deployed)

- **Layers (`layers.py`):** `search_powiat` now builds `StaticLayers` (grid, OSM proxies, GDOŚ, DSO/PSE capacity, heat, PSI,
  cities; `cached_static_layers` keeps them per process, rebuilt when any snapshot manifest changes) and `AreaLayers`
  (flood, mining, BDOT10k, RU) through `load_static_layers` and `load_area_layers`. A search is `strict` (a failed download
  fails it); a check is not: the step is reported and the criteria it feeds stay missing. Behaviour of the search is
  unchanged (compared on powiat 0213, see the PR).
- **`parcel_check.py`:** ULDK by id or point, one `Site` per parcel (a parcel over 80 ha is kept whole and flagged), the same
  scorer, layers for the parcel plus 500 m, KIMPZP and KIUG always asked (also for an excluded parcel). The verdict
  ("Odpada" / "Wymaga wyjaśnień" / "Warto sprawdzać dalej") uses only scorer output; "warto" needs a score of 70 and 70% of
  the data (the ruler's upper tick). Hard filters that could not be evaluated are listed as "Nie sprawdzono: …". The
  survey sheet is made on the server (`check_sheet.py`): layers as simplified paths in local metres, capped per layer.
- **CLI:** `dclh check <id|coordinates|link>` prints the steps, verdict, score, unknowns and next steps. No DB writes.
- **Job `check_parcel` (`check_job.py`):** commits the step list after every step (progress is visible while it runs),
  stores one result per parcel, records step timings in `check_step_stats` (warm = every answer from the response cache)
  and fails the check when the worker restarts (`fail_interrupted`), so the page never waits for ever.
- **Migration `0010`:** `parcel_checks`, `parcel_check_sites` (the `sites` columns plus verdict, unknowns, next steps, sheet,
  layer ages), `check_step_stats`. Checks are kept, not expired.
- **API:** `POST /api/parcels/resolve` (server-side parsing, ULDK, WGS84 outline), `POST /api/checks` (ids or points, 20 at
  most, 3 open per user), `GET /api/checks`, `GET /api/checks/{id}` (steps, remaining-time estimate), `GET /api/checks/{id}/sites/{n}`
  (a `SiteDetail` plus verdict, sheet, unknowns, next steps, layer ages).
- Not done: the web pages (steps 7 to 10) and the orthophoto.

## Sprawdź działkę, steps 7 to 10: the pages (25.10.2026; not yet deployed)

- **`/sprawdz-dzialke`:** one field for what people are sent (ids, coordinates, a Google Maps link, several at once; the server
  parses it), "Wskaż na mapie" (a click drops a pin and looks the parcel up), then **"Czy to ta działka?"** (outline, gmina,
  obręb, area, "poza obszarem zainteresowania") before anything expensive starts. Problems are listed by entry with what to do.
  The recent checks of the whole team are below.
- **`/sprawdz-dzialke/:checkId`:** while it runs, a progress bar (steps done over all, never a fake animation), the remaining
  time, a ledger of what each step found, a polite live region and the state in the browser tab; the same address becomes the
  result: the verdict sentence, the ruler (score and potential), "Nie wiemy", next steps with copyable letters, the survey
  sheet (SVG: hatches, scale bar, north arrow, layer toggles) with the title block, the Protokół and the layer ages. Several
  parcels get a comparison list (`?n=` picks one). A failed layer is shown as such and the check goes on; a failed check keeps
  the parcels that finished. "Sprawdź ponownie" starts a new check with the same parcels.
- The site card links to a check of its parcels ("Sprawdź tę działkę z aktualnymi danymi").
- Not done on purpose: the drawing appears when a parcel is finished, not layer by layer (the server saves the sheet per
  parcel); "Dodaj do projektów" (the kanban does not exist yet); "Sprawdź tę działkę" in the map popups; the orthophoto;
  a Playwright test that pastes a real id (it needs live GUGiK services).

## Sprawdź działkę: first real checks on the VPS (25.10.2026)

Deployed #102 to #106. A real check (Żarów `021908_5.0007.238/1`) takes about 28 s through the queue (static layers 16 s the
first time after a worker start, then 8 to 10 s of area layers). What the first runs found and fixed:
- ISOK and RU rejected loosely matched answers ("a feature must touch the box"); they now share `bbox_check`: a feature
  within 2 km of the box passes, swapped axes are still caught. The RU plan ogólny service returned a zone 213 m outside a
  parcel-sized box. Flood and zoning steps no longer fail on parcels with nothing nearby.
- A check gives the **same score and score items as the nightly search** for the same land (three single-parcel sites of
  powiat 0219 compared: identical scores).
- **Memory:** the OSM parse refuses to start below 800 MB available on the host, which a nightly search can use up. The check
  worker waits (6 x 30 s) and then fails with "Na serwerze brakuje teraz wolnej pamięci…", and gives the static layers back
  after 10 minutes without a check, so a quiet check worker does not starve the nightly search. Its container limit is 1200 MB
  (first check peaked at about 740 MB).
- The first check after a worker restart is slower (the static layers are loaded again); the estimate counts that step.

## Workers exit when idle (25.10.2026)

A Python worker keeps the memory a job used: after a nightly search the general worker held about 790 MB, which left too little
for a check's OSM parse (it refuses to start below 800 MB free), and the first check of the day failed. `dclh worker
--exit-after-idle N` now ends the loop once the worker has run a job and then sat idle N seconds; `restart: unless-stopped`
starts the container lean. Compose: general worker 900 s, check worker 600 s (the release of the static layers did not shrink
its process, so it restarts too). A worker that has run nothing keeps waiting. The cost is one restart per quiet spell and
a 16 s static-layer load on the first check after it.

## Checks: own only, steps from the start (25.10.2026)

- A member sees only their own checks (the list and the check and result addresses; someone else's check answers 404, like
  a check that does not exist); an admin sees everyone's. The page says which it is.
- The step list is written when a check is created, so the page shows the whole plan and a progress bar from the first poll
  instead of "0 z 0 kroków". With no steps at all (an old check) it says "Przygotowuję sprawdzenie…".

