Real ULDK answers captured on 24.10.2026 from the VPS (Poland) with `PoliteClient`, for Sprawdź działkę
(docs/PLAN.md, steps 1 and 2). Every request is `result=teryt,voivodeship,county,commune,region,parcel,geom_wkt` with
`srid=2180` (`by_id_params` / `by_name_params` in `sources/uldk.py`). Parcel ids and outlines only; no owner data.

| File | Request | Answer |
|---|---|---|
| `uldk_by_id_reguly_562_2.txt` | `GetParcelById` `142104_2.0013.562/2` (Reguły, golden set) | ok, `SRID=2180;POLYGON`, one record |
| `uldk_by_id_konin_1573.txt` | `GetParcelById` `306201_1.0002.1573` (Konin, never searched) | ok, one record |
| `uldk_by_id_data4_jawczyce_141_9.txt` | `GetParcelById` `143206_5.0005.141/9` (Data4 Jawczyce, golden set) | ok, one record |
| `uldk_by_id_not_found.txt` | `GetParcelById` `142104_2.0013.99999` | HTTP 200, `-1 brak wyników` plus a confusing XML error line |
| `uldk_by_id_malformed.txt` | `GetParcelById` `not-an-id` | the same text as "not found": ULDK does not tell them apart |
| `uldk_by_name_konin_0002_1573.txt` | `GetParcelByIdOrNr` `Konin 0002 1573` | HTTP 200, `-1 brak wyników`: this name form is **not** accepted |

What they show: assumption C holds (by id returns the same EPSG:2180 geometry as by point; the spike's by-point
lookup at the centroid returned the same id). An error is still HTTP 200 and starts with `-1`. The "obręb + numer"
form needs another request format before it can be offered in the UI.
