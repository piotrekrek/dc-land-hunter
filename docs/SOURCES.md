# Data sources

Status of every source in `config/sources.yaml` (brief section 4). Each MVP source gets a verified
status in Phase 0: ✓ verified, ⚠ reachable but the data endpoint still needs confirming (or some probes
failed), ✗ unreachable, ⏳ not yet checked.

## Current state (10.10.2026): not yet verified

**No source has been verified yet.** The Claude Code cloud environment used to build Phase 0 cannot
reach any Polish public service: its network policy answers 403 for every one of these hosts (and also
for geofabrik.de). That says nothing about the sources themselves. Several of them also block non-Polish
IP addresses, so verification has to run from a machine in Poland anyway.

To verify, on a machine in Poland (laptop or the future VPS):

```bash
uv sync --extra discovery                       # Playwright, for interactive maps
uv run playwright install chromium              # once
export DCLH_CONTACT_EMAIL=you@example.com       # goes into the User-Agent
uv run dclh discover --capture-network --write-docs --ran-from "laptop, Warszawa"
git add data/discovery docs/SOURCES.md && git commit -m "Phase 0: source discovery results"
```

This writes `data/discovery/discovery-<date>.json` (full detail: capabilities, layer names, WFS
`CountDefault`, endpoint candidates found in pages and scripts, XHR calls captured from interactive maps)
and replaces the generated table below. Then fill in the "Verified endpoint" notes by hand for the
⚠ sources, using the JSON report.

## Generated discovery table

<!-- discovery:begin -->
Not run yet.
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
