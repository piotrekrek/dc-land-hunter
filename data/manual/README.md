# Manual data

Files here are maintained by hand and committed.

| File | Used by | Notes |
|---|---|---|
| `invest_offers_template.csv` | `invest_offers` import (Phase 5) | Copy, fill and import. `teryt_parcels` are `;`-separated parcel ids. Do not put personal data in `contact_note`. |
| `gpz_mapping.csv` | GPZ ↔ DSO capacity matching (Phase 3) | Columns `osm_id,dso,dso_gpz_name,note`. `osm_id` is `123` or `n123` (node) or `w123` (way), as in OpenStreetMap; it may point at any substation, named or not. Overrides fuzzy name matching; add a line per GPZ the matcher misses. |
Phase 3 imports (`dclh import-manual`, see `docs/RUNBOOK.md`). Headers are matched case-insensitively, spaces become `_`:

| File (any name) | Source id | Columns |
|---|---|---|
| DSO capacity, one DSO per file | `enea`, `energa`, `tauron` | `dso, gpz_name, published_mw, as_of, note`. `dso` must match the source id. Blank `published_mw` = not published. `as_of` = YYYY-MM-DD. |
| `pse_planned_station_coords.csv` | `pse_avail` PDF import | Columns `symbol,lat,lon,basis`. PSE's PDF names planned load stations by region only; this table places each at the centre of the town it names. Add a row for a new station; one without a row is left out of the import. |
| PSE point lists | `pse_closed`, `pse_odb`, `pse_avail` | `name, as_of` and `x_2180, y_2180` (metres) or `lat, lon` (WGS84). `pse_closed` and `pse_odb` also take PSE's own XLSX as published, with no coordinates (located by station name). |

