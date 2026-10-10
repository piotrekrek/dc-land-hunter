# Data sources

Status of every source in `config/sources.yaml` (brief section 4). Each MVP source gets a verified
status in Phase 0: ✓ verified, ⚠ reachable but the data endpoint still needs confirming (or some probes
failed), ✗ unreachable, ⏳ not yet checked.

## Current state (run of 10.10.2026, 22:55 UTC)

Phase 0 verification is done for all 24 MVP sources: each has a status from a run on a Polish server
(`data/discovery/discovery-2026-10-10T2255.json`, the detailed report).

| Status | Count | Meaning |
|---|---|---|
| ✓ | 10 | Verified: a data endpoint answered with real capabilities (WFS/WMS/ArcGIS) or a downloadable file |
| ⚠ | 11 | Reachable, but the data endpoint is not confirmed yet (candidate links only, or a 404) |
| ✗ | 2 | Unusable at the time of the run: `enea` (domain-for-sale page, since fixed); `kowr` (bot-protection challenge) |
| – | 1 | `invest_offers`: manual CSV import by design, no probe |

**Verified (✓):** `prg`, `osm_power`, `osm_land`, `kimpzp`, `egib`, `uldk`, `kiug`, `gdos`, `midas`, `nid`.

**Open before Phase 1 can rely on them:**
- `bdot10k`: the OPENDATA page returns 404; the WFS offers only one layer. Find the current download URL.
- `ru`, `enea`, `energa`, `tauron`: the browser capture did not run (Playwright was not installed on the server).
  Run `uv sync --extra discovery` on the server, then rerun discovery with `--capture-network`.
- `enea`: the old domain is parked. The probe now points at `www.operator.enea.pl/przylaczenie-do-sieci/dostepne-moce`
  (commit c67e187), but the ✗ above is from the run before that change. Rerun discovery to confirm. The page is
  renewables-oriented and county-level, so check it against the GPZ-level signal the brief assumes.
- `uke` and `isok`, `pse_*`, `psi`, `heat`: candidate links were found in pages; each still needs its real
  data file or service confirmed.
- `kowr`: bot protection; plan is manual CSV import unless its terms allow automated access.

Unverified endpoints are not used by any connector until they are confirmed in this file.

## Generated discovery table

<!-- discovery:begin -->
Run: 2026-10-10 23:03 UTC · from: unknown

| Status | Source | Plan | Probes |
|---|---|---|---|
| ✓ | `prg` GUGiK PRG – granice administracyjne | mvp | ogc: ok · 200 · 2.0.0 · 52 layers<br>http: ok · 200 |
| ✓ | `osm_power` OpenStreetMap – sieć elektroenergetyczna (Geofabrik) | mvp | http: ok · 200<br>http: ok · 200 |
| ✓ | `osm_land` OpenStreetMap – użytkowanie terenu i budynki (Geofabrik) | mvp | http: ok · 200 |
| ⚠ | `bdot10k` GUGiK BDOT10k | mvp | ogc: ok · 200 · 2.0.0 · 1 layers<br>page: failed · 404 |
| ⚠ | `ru` Rejestr Urbanistyczny | mvp | page: ok · 200 · 1 endpoint candidates<br>browser: failed · Error: BrowserType.launch: Executable doesn't exist at /home/piotr/.cache/ms-pla |
| ✓ | `kimpzp` KIMPZP – Krajowa Integracja MPZP | mvp | ogc: ok · 200 · 1.3.0 · 9 layers |
| ✓ | `egib` EGiB – zbiorcza usługa WFS działek | mvp | ogc: ok · 200 · 2.0.0 · 2 layers · CountDefault 1000 |
| ✓ | `uldk` ULDK – Usługa Lokalizacji Działek Katastralnych | mvp | http: ok · 200 |
| ✓ | `kiug` KIUG – użytki gruntowe i klasy bonitacyjne | mvp | ogc: ok · 200 · 1.3.0 · 4 layers |
| ✓ | `gdos` GDOŚ Geoserwis – formy ochrony przyrody | mvp | ogc: ok · 200 · 2.0.0 · 23 layers · CountDefault 1000000 |
| ⚠ | `isok` ISOK – mapy zagrożenia powodziowego | mvp | page: ok · 200 · 1 endpoint candidates |
| ✓ | `midas` PIG-PIB MIDAS – obszary i tereny górnicze | mvp | arcgis: ok · 200 |
| ⚠ | `nid` NID – rejestr zabytków i stanowiska archeologiczne | mvp | http: ok · 200<br>page: failed · ReadTimeout: The read operation timed out |
| ⚠ | `pse_closed` PSE – zamknięte rozdzielnie (brak warunków przyłączenia) | mvp | page: ok · 200 · 3 endpoint candidates |
| ⚠ | `pse_odb` PSE – wykaz wydanych warunków przyłączenia (odbiorcy) | mvp | page: ok · 200 · 3 endpoint candidates |
| ⚠ | `pse_avail` PSE – raport o dostępnych mocach przyłączeniowych | mvp | page: ok · 200 · 3 endpoint candidates |
| ⚠ | `enea` Enea Operator – mapa dostępnych mocy dla odbiorców | mvp | page: ok · 200 · 1 endpoint candidates<br>browser: failed · Error: BrowserType.launch: Executable doesn't exist at /home/piotr/.cache/ms-pla |
| ⚠ | `energa` Energa-Operator – dostępne moce odbiorcze | mvp | page: ok · 200 · 0 endpoint candidates<br>browser: failed · Error: BrowserType.launch: Executable doesn't exist at /home/piotr/.cache/ms-pla |
| ⚠ | `tauron` Tauron Dystrybucja – dostepnemoce.pl | mvp | page: ok · 200 · 12 endpoint candidates<br>browser: failed · Error: BrowserType.launch: Executable doesn't exist at /home/piotr/.cache/ms-pla |
| – | `dso_portals` Nowe portale mocy OSD (nowelizacja 2026) | v2 | – |
| ⚠ | `uke` UKE – infrastruktura telekomunikacyjna | mvp | page: ok · 200 · 5 endpoint candidates |
| ⚠ | `psi` Polska Strefa Inwestycji – mapa pomocy regionalnej | mvp | page: ok · 200 · 38 endpoint candidates |
| ✗ | `kowr` KOWR – oferty nieruchomości rolnych | mvp | page: failed · 200 · strona ochrony antybotowej (challenge), nie treść |
| – | `invest_offers` Oferty inwestycyjne gmin / SSE / COI | mvp | – |
| ⚠ | `heat` Sieci ciepłownicze (proxy – koncesje URE) | mvp | page: ok · 200 · 1 endpoint candidates |
| – | `bazaoos` GDOŚ – Baza OOŚ (decyzje środowiskowe) | manual | – |
| – | `ekw` Księgi wieczyste (EKW) | manual | – |
| – | `bip` BIP gmin | v2 | – |
<!-- discovery:end -->

## MVP sources: candidate endpoints, format, licence, fallback

All endpoints below are **candidates from desk research** (October 2026) and are probed by `dclh discover`.
Fallback order for interactive maps (brief): JSON endpoint → Playwright → manual CSV import.

| Status | Source | Candidate endpoints (probes) | Format | Licence (expected, to confirm) | Fallback |
|---|---|---|---|---|---|
| ⏳ | `prg` | ogc: `https://mapy.geoportal.gov.pl/wss/service/PZGIK/PRG/WFS/AdministrativeBoundaries`<br>http: `https://opendata.geoportal.gov.pl/prg/granice/00_jednostki_administracyjne.zip` | WFS (GML); bulk ZIP (SHP) | Otwarte dane GUGiK (bez opłat, art. 40a Pgik) | Bulk ZIP from opendata.geoportal.gov.pl; manual download |
| ⏳ | `osm_power` (core) | http: `https://download.geofabrik.de/europe/poland-latest.osm.pbf`<br>http: `https://download.geofabrik.de/europe/poland-latest.osm.pbf.md5` | .osm.pbf (~2 GB) | ODbL 1.0 (© OpenStreetMap contributors) | Previous snapshot; another Geofabrik-compatible mirror |
| ⏳ | `osm_land` | http: `https://download.geofabrik.de/europe/poland-latest.osm.pbf` | .osm.pbf | ODbL 1.0 (© OpenStreetMap contributors) | Previous snapshot |
| ⏳ | `bdot10k` | ogc: `https://mapy.geoportal.gov.pl/wss/service/PZGIK/BDOT/WFS/PobieranieBDOT10k`<br>page: `https://opendata.geoportal.gov.pl/bdot10k/` | Per-powiat ZIP (GML/SHP); WFS | Otwarte dane GUGiK | OSM buildings and landuse (lower confidence) |
| ⏳ | `ru` (core) | page: `https://rejestr-urbanistyczny.gov.pl/`<br>browser: `https://rejestr-urbanistyczny.gov.pl/` | WFS/WMS/CSW, APP GML (endpoint to find) | Do ustalenia (dane publiczne, APP) | KIMPZP GetFeatureInfo; missing plan = `unknown` |
| ⏳ | `kimpzp` | ogc: `https://mapy.geoportal.gov.pl/wss/ext/KrajowaIntegracjaMiejscowychPlanowZagospodarowaniaPrzestrzennego` | WMS (often raster) + GetFeatureInfo | Otwarte dane GUGiK | None: zoning stays `unknown` |
| ⏳ | `egib` (core) | ogc: `https://mapy.geoportal.gov.pl/wss/service/PZGIK/EGIB/WFS/UslugaZbiorcza` | WFS 2.0 GML, EPSG:2180 | Otwarte dane GUGiK (geometria działek) | ULDK per parcel (slow); county WFS services |
| ⏳ | `uldk` | http: `https://uldk.gugik.gov.pl/?request=GetParcelByXY&xy=637500,486500,2180&result=teryt,voivodeship,county,commune` | Plain text / WKT | Otwarte dane GUGiK | EGiB WFS by bbox |
| ⏳ | `kiug` | ogc: `https://integracja.gugik.gov.pl/cgi-bin/KrajowaIntegracjaUzytkowGruntowych` | WMS GetFeatureInfo | Otwarte dane GUGiK | BDOT10k/OSM forest; soil class `missing` |
| ⏳ | `gdos` (core) | ogc: `https://sdi.gdos.gov.pl/wfs` | WFS (GML) | Dane publiczne GDOŚ | GDOŚ SHP downloads; WMS for display only |
| ⏳ | `isok` (core) | page: `https://wody.isok.gov.pl/` | WFS 2.0 / ATOM (endpoint to find) | Dane publiczne PGW Wody Polskie | ATOM bulk download per river basin |
| ⏳ | `midas` | arcgis: `https://cbdgmapa.pgi.gov.pl/arcgis/rest/services`<br>page: `https://cbdgmapa.pgi.gov.pl/` | ArcGIS REST / WFS (to confirm) | Dane publiczne PIG-PIB | CBDG SHP downloads |
| ⏳ | `nid` | http: `https://api.dane.gov.pl/1.4/datasets?q=rejestr%20zabytk%C3%B3w%20nieruchomych&per_page=5`<br>page: `https://mapy.zabytek.gov.pl/` | WMS/WFS; CSV on dane.gov.pl | Dane publiczne NID / dane.gov.pl | CSV + geocoding by address/TERYT |
| ⏳ | `pse_closed` | page: `https://www.pse.pl/` | XLSX (page to find) | Informacja publiczna PSE | Manual download into `data/manual/` |
| ⏳ | `pse_odb` | page: `https://www.pse.pl/` | PDF (page to find) | Informacja publiczna PSE | Claude API table extraction (flag); manual CSV |
| ⏳ | `pse_avail` | page: `https://www.pse.pl/` | Monthly PDF (page to find) | Informacja publiczna PSE | Claude API table extraction (flag); manual CSV |
| ⏳ | `enea` | page: `https://www.enea-operator.pl/`<br>browser: `https://www.enea-operator.pl/` | Interactive map → JSON? (to find) | Informacja publiczna OSD (sprawdzić regulamin) | Playwright capture → manual CSV |
| ⏳ | `energa` | page: `https://energa-operator.pl/`<br>browser: `https://energa-operator.pl/` | Unknown: existence to confirm | Informacja publiczna OSD (sprawdzić regulamin) | Playwright capture → manual CSV |
| ⏳ | `tauron` | page: `https://dostepnemoce.pl/`<br>browser: `https://dostepnemoce.pl/` | Interactive map (to find) | Informacja publiczna OSD (sprawdzić regulamin) | Playwright capture → manual CSV |
| ⏳ | `uke` | page: `https://www.uke.gov.pl/` | WMS / export (to find) | Do ustalenia | None: connectivity uses city distance only |
| ⏳ | `psi` | page: `https://www.gov.pl/web/fundusze-regiony/polska-strefa-inwestycji` | Regulation text → static CSV | Akt prawny (rozporządzenie) – domena publiczna | n/a (static, prepared by hand) |
| ⏳ | `kowr` | page: `https://nieruchomoscikowr.gov.pl/` | HTML listings | Sprawdzić regulamin i robots.txt przed automatyzacją | Manual CSV import if terms forbid scraping |
| ⏳ | `invest_offers` | – | Manual CSV (template) | – | n/a |
| ⏳ | `heat` | page: `https://bip.ure.gov.pl/` | URE concession list (XLSX/PDF on BIP) | Informacja publiczna URE | Hand-made list of towns with district heating |

## Not automated (v2 or link only)

| Source | Plan | Name | Link |
|---|---|---|---|
| `dso_portals` | v2 | Nowe portale mocy OSD (nowelizacja 2026) | – |
| `bazaoos` | manual | GDOŚ – Baza OOŚ (decyzje środowiskowe) | `https://bazaoos.gdos.gov.pl/` |
| `ekw` | manual | Księgi wieczyste (EKW) | `https://przegladarka-ekw.ms.gov.pl/` |
| `bip` | v2 | BIP gmin | – |

## Politeness rules (all connectors)

- User-Agent `DCLandHunter/<version> (+mailto:$DCLH_CONTACT_EMAIL)`.
- At most 1 request/s per host (`config/sources.yaml: defaults.rate_limit_per_s`), robots.txt honoured,
  retries with exponential backoff on 429/5xx (honouring `Retry-After`).
- Raw responses are stored once per distinct content (`data/raw/<source>/`, date + sha256 in a manifest).
  Conditional requests (ETag / Last-Modified) for large downloads such as the OSM extract arrive with the
  Phase 1 connectors.
- Never automate a source marked "manual"; ask the owner before automating anything new.

## Open questions per source (to resolve during verification)

- **ru**: actual WFS/CSW endpoint and the APP zone codes for SG and production designations.
- **isok**: WFS 2.0 vs ATOM; layer names for the 10%, 1% and 0.2% hazard zones.
- **midas**: which service exposes obszary/tereny górnicze as vectors.
- **pse_closed / pse_odb / pse_avail**: exact pages and file naming (monthly cadence).
- **enea / energa / tauron**: JSON endpoints behind the maps; terms of use; whether Energa publishes
  load capacity at all.
- **uke**: which public dataset gives telecom node locations.
- **kowr**: terms of use and robots.txt before any scraping (else manual CSV).
- **heat**: whether URE publishes concession holders as a machine-readable list.
