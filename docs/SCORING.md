# Scoring

The scoring model follows brief section 6. Defaults live in `config/scoring.yaml` (validated by
`dclh_pipeline.config.ScoringConfig`: weights must sum to 100, distance ranges must be ordered).
The engine is implemented in Phase 2; this page will describe each criterion as it lands.

## Shape of a score

- **Hard filters** (6.1) exclude a site with a reason in Polish, or subtract points when the owner
  switches the filter to `penalty` mode.
- **Weighted criteria** (6.2): grid 45, zoning 25, land 15, connectivity 5, bonus 10, then penalties.
- Every criterion returns `{value, points, max_points, confidence, source_id, as_of, explanation_pl}`.
- Missing data scores 0, shows as "?" and lowers a separate **data completeness %**. The list can sort by
  score, completeness, or the upside view ("score if unknowns were good").
- Grid confidence badge: "Opublikowana moc" (verified DSO data), "Bliskość sieci" (distance proxy),
  "Brak danych" (missing).

## Estimated capacity

`szacowana_moc_MW = area_ha × mw_per_ha` (default 6 MW/ha, so 60 MW ≈ 10 ha). Above
`large_campus_mw` (150 MW) a site is flagged "duży kampus" and a 220/400 kV node nearby becomes
important.

## Connection-cost calculator

Figures from the Prawo energetyczne amendment in force since 30.04.2026 are in `connection_cost`
(`verified: false`). They **need legal verification** and the UI labels them that way.
Example for 60 MW: 60 000 PLN application fee, 3.6 mln PLN advance, 1.8 mln PLN security.
Open question: whether "30 PLN/kW up to 100 MW" caps the secured capacity at 100 MW or limits the
obligation to connections up to 100 MW (to confirm with a lawyer before Phase 4).

## Tests

Property-based tests (hypothesis) will check monotonicity, e.g. a closer GPZ never lowers the score.
The golden set (`tests/golden/sites.yaml`) is a sanity check, not a tuning target.
