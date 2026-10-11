# Manual data

Files here are maintained by hand and committed.

| File | Used by | Notes |
|---|---|---|
| `invest_offers_template.csv` | `invest_offers` import (Phase 5) | Copy, fill and import. `teryt_parcels` are `;`-separated parcel ids. Do not put personal data in `contact_note`. |
| `gpz_mapping.csv` | GPZ ↔ DSO capacity matching (Phase 3) | Columns `osm_id,dso,dso_gpz_name,note`. Overrides fuzzy name matching; add a line per GPZ the matcher misses. |
Phase 3 imports (`dclh import-manual`, see `docs/RUNBOOK.md`). Headers are matched case-insensitively, spaces become `_`:

| File (any name) | Source id | Columns |
|---|---|---|
| DSO capacity, one DSO per file | `enea`, `energa`, `tauron` | `dso, gpz_name, published_mw, as_of, note`. `dso` must match the source id. Blank `published_mw` = not published. `as_of` = YYYY-MM-DD. |
| PSE point lists | `pse_closed`, `pse_odb`, `pse_avail` | `name, as_of` and `x_2180, y_2180` (metres) or `lat, lon` (WGS84). |

| `psi_aid.csv` | `psi` bonus (Phase 2) | Aid intensity per gmina from the regional aid map regulation (to prepare). |
