# Scoring

Implemented in `pipeline/dclh_pipeline/scoring/` (`score_site(features, config)`, a pure function), following
brief section 6. Defaults live in `config/scoring.yaml` (validated by `ScoringConfig`: weights must sum to 100,
distance ranges must be ordered). 54 tests, including property-based ones.

## How a score is built

1. **Hard filters** (6.1) run first. A site that trips an `exclude` filter is marked `excluded` with the reason in
   Polish, but it is **still scored** so the list can show how good it would have been. A filter in `penalty` mode
   subtracts its `penalty_points` instead. Missing input data never excludes a site (it lowers completeness).
2. **Five categories** each score 0–100 internally, then are multiplied by their weight (grid 45, zoning 25,
   land 15, connectivity 5, bonus 10), so the total is on 0–100 and the weights alone decide importance.
3. **Penalties** are subtracted afterwards (floor 0).
4. Every line of the breakdown is a `ScoreItem {points, max_points, confidence, source_id, as_of, explanation_pl}`.

## Interpretations (the brief does not fix these, so please confirm or change)

The brief gives distances and bonus sizes but not how sub-criteria share a category. These are my choices:

| Category | Sub-criterion | Max (of 100) |
|---|---|---|
| **Grid** | Proximity: the better of "110 kV line ≤ 1 km → 3 km" and "GPZ ≤ 2 km → 8 km", linear in between | 60 |
| | Two independent 110 kV lines within 3 km (N-1) | 10 |
| | 220/400 kV node within 25 km (20 instead of 10 when est. power > 150 MW) | 10 / 20 |
| | Planned PSE load station within 25 km | 5 |
| | DSO-published headroom at the nearest GPZ (signal only) | 5 |
| | PSE ODB entry within 15 km (demand signal) | 3 |
| | Powered brownfield | 15 |
| | Closed PSE switchyard nearby | −15 |
| **Land** | Size: 35 in the ideal 10–30 ha, 25–35 sliding down to 80 ha, 20 above (flagged "do podziału") | 35 |
| | Compactness: 0 at the configured minimum (0.35), linear to 1.0 | 25 |
| | Parcels: full at ≤ 3, zero at ≥ 30 (fewer owners) | 30 |
| | Expansion land: 3 points per 10 ha (config), capped. Hectares of other EGiB parcels within 20 m of the site | 10 |
| **Connectivity** | Telecom node: full ≤ 2 km, zero ≥ 15 km | 50 |
| | Large city: full ≤ 30 km, zero ≥ 120 km | 50 |
| **Zoning** | Lookup from `zoning:` in the config (MPZP 100, SG 80, procedure 50, unknown 30, other 0) | 100 |
| **Bonus** | In final-score points, capped at 10: heat network ≤ 5 km (3), PSI aid (up to 3), RES cluster ≤ 10 km (2), environmental decision (3), public seller (2) | 13 → 10 |

- The grid sub-criteria add up to 118 and are capped at 100: a site can reach the full grid score without every bonus.
- "Closer is never worse" is enforced with property tests (line, GPZ, telecom, city, parcels, compactness, flood).
- Hard filters: "any part in a 10%/1% flood zone" and "inside an active mining area" use `threshold_pct: 0`
  (any overlap); raise it in the config if overlap of a few percent should be tolerated.
- A 110 kV-only station is not a GPZ (brief: GPZ = 110 kV plus a medium-voltage level).

## Missing data

- Missing data scores **0**, is shown as "?" (confidence `missing`) and lowers **completeness** (the share of
  scored criteria and hard-filter inputs that are backed by data).
- `upside_score` is what the site would score if every missing criterion turned out best-case; the list can sort by
  score, completeness or upside.
- **Zoning is the one exception**, because the config gives "unknown" its own value: no plan found scores
  30% of the zoning points (still counted as missing for completeness; upside assumes MPZP). The register is
  incomplete until 30.11.2026, so "no plan" means unknown, not bad, and must not rank below a known non-industrial plan.
- Missing penalty inputs count as no penalty (their upside is also 0).

## Grid badge

"Opublikowana moc" when DSO capacity data exists for the nearest GPZ, "Bliskość sieci" when only distances are
known, "Brak danych" when neither. The score is a proxy: it never promises connection capacity.

## Estimated power and flags

`szacowana_moc_MW = area_ha × mw_per_ha` (6). Flags: above 150 MW "duży kampus" (needs a 220/400 kV node),
above 80 ha "duży teren, do podziału", under 60 MW a note that the estimate is below the minimum.

## Connection-cost calculator

Figures from the Prawo energetyczne amendment in force since 30.04.2026 are in `connection_cost`
(`verified: false`). They **need legal verification** and the UI labels them that way.
Example for 60 MW: 60 000 PLN application fee, 3.6 mln PLN advance, 1.8 mln PLN security.
Open question: whether "30 PLN/kW up to 100 MW" caps the secured capacity at 100 MW or limits the
obligation to connections up to 100 MW (to confirm with a lawyer before the calculator ships).

## Investor profiles (DC budowane / DC kontenerowe)

A lens on the same features as the score (`pipeline/dclh_pipeline/profiles.py`, values in `config/profiles.yaml`). It does
not change the score. Each profile gets a category: **green** (worth pursuing), **yellow** (conditional), **red** (a blocker
or a low soft score) or **manual** (a missing answer decides it, mostly the zoning).

- **Blockers** use data we have: area below the profile minimum (10 ha built, 0.15 ha container), overlap with Natura
  2000 / park / reserve, flood zones Q10/Q1, forest, mining area, heritage register. `built` also blocks on a zoning
  that is neither production nor service; for `container` that is "to check" (we cannot tell U from other symbols yet).
- **Soft score** (0-100): distance to housing (400 m built, 150 m container, zero at 250/80 m), distance to a 110 kV GPZ,
  published GPZ headroom, soil classes. An input with no data counts 50 and is listed, never passed silently.
- **Road access** is measured: the distance to the nearest public road (BDOT10k, every road class except
  `droga wewnętrzna`). Up to 25 m it is a plus; up to 300 m it is listed with the distance and the easement question;
  beyond that it caps the result at yellow. It never rejects a plot (the legal right to use a road is a land-register
  question), and with no road measured the old manual wording stays.
- **To check** is always shown: closed areas (MON), MPZP text (container and temporary-structure bans,
  minimum intensity and height), land register. A plan ogólny zone caps the result at yellow until OUZ is confirmed.
- Not covered yet because no data feeds it: the raw MPZP symbol, MPZP text parsing, OUZ, closed areas,
  fibre, an acoustic calculator. All numbers are practice assumptions from the desk research, not law; legal questions
  (is a container DC a building, is a data centre "infrastruktura techniczna", art. 5b ugrl) are for a lawyer and an urban planner.
- A container used permanently is a building for building-law purposes; the 180-day notification route is not offered.

## What is not built yet

The engine scores whatever features it is given. `model/features.py` now computes the grid, zoning, brownfield,
constraint and residential features from geometry (see `docs/STATUS.md`), and missing layers stay missing. The
live connectors that load those layers, the site list and card in the web app, and the golden-set check
(`tests/golden/sites.yaml`, which needs real parcel data) are still to do.

## Rules added in the Phase 3 review

- **Powered brownfield** needs a 110 kV substation (within 100 m of an industrial area within 200 m of the site)
  whose OSM `operator` names an owner that is neither a DSO nor PSE. A utility GPZ next to a factory does not count,
  and a substation without an operator tag is not counted either.
- **Zoning** uses a plan zone only if it covers at least 1% of the site. A zone that merely touches the site is
  ignored, so the site falls back to "unknown" (not bad).
- **Published headroom** (Opublikowana moc) comes from the nearest matched GPZ within 5 km that has a published
  value. A fuzzy name match gives `estimated` confidence, a manual mapping `verified`.
