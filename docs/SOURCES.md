# Data sources

Status of every source in `config/sources.yaml` (brief section 4). Each MVP source gets a verified
status in Phase 0: ✓ verified, ⚠ reachable but the data endpoint still needs confirming (or some probes
failed), ✗ unreachable, ⏳ not yet checked.

## Current state (19.10.2026, from the owner's VPS in Poland)

Round 2 of the verification: `dclh discover-followup` (`data/discovery/followup-2026-10-19T0948.md`), a full
`dclh discover` with corrected probes (`data/discovery/discovery-2026-10-19T100250.json`), and requests by hand and in a
browser for the sources whose data sits behind a form or in a file. Raw samples: `tests/fixtures/live/source-verification-2026-10-19/`.

| Status | Count | Sources |
|---|---|---|
| ✓ | 19 | `prg`, `osm_power`, `osm_land`, `bdot10k`, `ru`, `kimpzp`, `egib`, `uldk`, `kiug`, `gdos`, `isok`, `midas`, `pse_closed`, `pse_odb`, `pse_avail`, `energa`, `tauron`, `psi`, `heat` |
| ⚠ | 2 | `uke` (lead only); `nid` (endpoint reachable, but no geometry to filter on, see Hard filters below: not loaded) |
| – | 2 | `invest_offers`: manual CSV import by design; `enea`: manual CSV only (owner decision 19.10.2026) |

`kowr` (12.10.2026) and `enea` (19.10.2026) are manual-only by owner decision. The generated table below still shows ⚠ for `pse_odb`, `pse_avail`,
`energa` and `tauron`: a `page` probe can only show that the page answers. Those four were confirmed by downloading the
file or calling the endpoint, as described here.

### Hard filters and zoning

- **`midas` ✓ mining (21.10.2026, loaded live for powiat 1421).** ArcGIS MapServer
  `https://cbdgmapa.pgi.gov.pl/arcgis/rest/services/midas/MapServer`: layer 1 "obszary górnicze" (`mining_area`, 6,201
  current features) and layer 2 "tereny górnicze" (`mining_terrain`, 6,022). Query with `inSR=2180&outSR=2180`,
  `f=geojson` (the answer is in EPSG:2180), `maxRecordCount` 1000, paged by `resultOffset`. The count-only request
  (`returnCountOnly=true`) gives the total to check against. Layers 3–5 are archive, layer 0 is deposits: not used.
  The `status` field is `aktualny`. `dclh search` loads it per powiat (`sources/midas.py`).
- **`isok` 0.2% zones (21.10.2026).** The same WFS now also returns `loo_ql_probabilityofoccurrence = 0.002` in one request
  with the 1% zones (`fes:Or`). `flood_0_2` is the 10-point penalty; the 1% zones stay the hard filter.
- **`gdos` landscape parks and OChK (21.10.2026).** The same WFS has `GDOS:ParkiKrajobrazowe` (213 features, 25 MB)
  and `GDOS:ObszaryChronionegoKrajobrazu` (406, 38 MB), with the same fields (`nazwa`, `kodinspire`). Each comes back
  whole in one request (under 2 s). They feed only the 8-point penalty `landscape_park_or_ochk`, never the
  `protected_nature_pct` hard filter. 87 park features are buffer zones ("… - otulina", 15,066 km²). They are not
  the park and are dropped, which leaves 26,512 km² of parks and 71,838 km² of OChK. A `gdos` snapshot from before
  this change lacks the two layers and is refused, so run `dclh fetch gdos` once after the deploy.
- **`nid` ⚠ not loaded (21.10.2026).** Reachable, but the open data has no coordinates. The archaeological register
  (`https://api.zabytek.gov.pl/nidrestapi/api/data/geoportal/otwarteDaneZestawienieZra`, also CSV on dane.gov.pl dataset 210)
  gives only the gmina and town, with `dokladnosc_polozenia` "przybliżony". The immovable register
  (`…/otwarteDaneZestawienieZrn`) gives an address (street and number) for many objects but no point or polygon.
  The NID extent layer (`usluga.zabytek.gov.pl/ZN_ZASIEGI`, `Zabytki_nieruchome_I–IV`) renders no features at all over
  Poland (blank WMS answers, 21.10.2026), and `INSPIRE_AMD` has no WFS. So `heritage_register` stays `missing`
  ("Brak danych") and does not exclude anything. Next step: geocode the address register against PRG address points,
  or find a WFS with the heritage extents.

- **`isok` ✓ flood.** Use the flat layer `wms_nz-core:NZ.Fluvial` on
  `https://wody.isok.gov.pl/wss/INSPIRE/INSPIRE_HY_10k_2019_WFS` (6,744 zones in Poland). Attributes:
  `loo_ql_probabilityofoccurrence` (0.1 / 0.01 / 0.002), `loo_ql_returnperiod`, `loo_qualitativelikelihood`,
  `validityperiod_end`. **The 1% zone is `probabilityofoccurrence = 0.01`** (3,966 zones). This includes the scenario
  "całkowite zniszczenie wału przy przepływie Q 1%" (levee breach), which the filter should treat as 1% as well. bbox, FES filter and
  paging (`startIndex`) work. Ask for `srsName=urn:ogc:def:crs:EPSG::2180`. The answer then has **northing-first**
  coordinates (unlike EGiB). Zones are whole river reaches: 13 zones near Warsaw took 22 MB, so fetch per powiat bbox
  with the 0.01 filter, not the whole country. The nested INSPIRE layer `nz-core:HazardArea` on the
  `…_NZ_HY_MZPMRP_WFS` service fails with a server NullPointerException when a bbox is given. Do not use it.
  `validityperiod_end` is 21.12.2025 on the samples, which is the second-cycle maps. Watch for the new cycle.
- **`bdot10k` ✓ housing.** Per-powiat zip from `URL_GML` in `ms:BDOT10k_powiaty`
  (`https://opendata.geoportal.gov.pl/bdot10k/schemat2021/<woj>/<teryt>_GML.zip`, 18.6 MB for 1421). Buildings are in
  `…__OT_BUBD_A.xml` (60,434 in 1421), EPSG:2180, **easting-first**. Residential = `funkcjaOgolnaBudynku = "budynki
  mieszkalne"` (43,085 in 1421). Also use `kategoriaIstnienia` and skip `zniszczony`. `funkcjaSzczegolowaBudynku`
  separates `budynek jednorodzinny` / `wielorodzinny` / `dom letniskowy`.
- **`ru` ✓ zoning** (addresses from the RU "Usługi sieciowe" page):
  - `https://rejestr-urbanistyczny.gov.pl/uslugi-sieciowe/app-pog/wfs`: `app-pog:StrefaPlanistyczna` (506,565 plan
    ogólny zones). The fields `symbol` (e.g. `SJ`, `SU`, `SI`) and `oznaczenie` are readable with pyogrio. EPSG:2180,
    northing-first. **CountDefault is 10** (at most 10 features per request whatever `count` asks), but `startIndex`
    paging works: the 679 zones around Pruszków take 68 requests (46 s). A FES filter on `nazwa` returned 0, so filter on the client.
    **Correction (19.10.2026, `ru` connector): the strefa gospodarcza is symbol `SP`, not `SG`.** Its `nazwa` is the
    xlink `…/codelist/RodzajStrefyPlanistycznejKod/strefaGospodarcza` (title "strefa gospodarcza"). Around Pruszków:
    54 SP zones, 0 SG. The connector reads `nazwa`, not the symbol. GDAL exposes the xlinks as `nazwa_href` and
    `status_href` with the open option `GML_ATTRIBUTES_TO_OGR_FIELDS=YES`. `status` is the INSPIRE
    `ProcessStepGeneralValue`: `legalForce` (in force), `elaboration` ("w opracowaniu"), `adoption` ("w trakcie
    przyjmowania"). Every zone around Pruszków is still `elaboration` (475) or `adoption` (204): no plan ogólny there
    is in force yet.
  - `…/app-mpzp/wfs`: `app-mpzp:AktPlanowaniaPrzestrzennego` = **MPZP outlines only** (3,951; 97 around Pruszków),
    no designations (P, PU…). They tell "a plan exists here", not what it allows. Their `status` (same code list)
    marks plans in procedure: around Pruszków 67 `legalForce`, 9 `elaboration`, 1 `adoption` in the first 77.
  - JSON: `POST https://rejestr-urbanistyczny.gov.pl/api/public/published/query` (criteria `parcelIdentifier`,
    `tercCodeList`, `plansGroup`). It works over plain HTTP. Reguły parcel `142104_2.0013.562/2` has no published MPZP.
- **`kimpzp` ✓ MPZP designations, point queries only.** WMS GetFeatureInfo works with **version 1.1.1, `srs=EPSG:4326`,
  lon/lat bbox**. With 1.3.0 every point answers "brak serwisu dla wskazanego obszaru". In Warsaw it returns plan name
  and `FUN_SYMB` (e.g. `3k U-O/US`, `2 KDD`). Each gmina's server answers in its own format (Warsaw: XML with
  `MPZP_PRZEZNACZENIE_TERENU` rows), so the parser must tolerate several layouts. Seen on 19.10.2026 at 34 candidate
  sites in powiat 1421: "<gmina>: brak wyniku dla wskazanego obszaru" (Brwinów, Nadarzyn, Michałowice, Raszyn,
  Pruszków, Podkowa Leśna, Ożarów Mazowiecki, Lesznowola, Żabia Wola) and, twice, an empty GeoServer HTML page
  (`table.featureInfo`, no rows). **No site in 1421 got a designation.** The KIMPZP WFS lists 0 layers.
  The 14.10 follow-up verdict "WFS available" was wrong: it should say 0 layers. At Reguły the answer is "brak wyniku"
  (no vector plan there).

### Grid (DSO and PSE)

- **`tauron` ✓.** `POST https://dostepnemoce.tauron-dystrybucja.pl/wp-json/tauron-connector/v1/gpz/search`, JSON body
  `{"layer_type":"odbiorcy","criteria":{"minimum_mw":0,"year_from":2026,"year_to":2026,"province":""},"page":{"offset":0,"limit":100}}`.
  It works from the VPS **without a WordPress nonce or cookie**. 513 GPZs for loads, each with `gpz_code`, `name`,
  `group_name` (GPZs that share capacity), `value_mw` and `lat`/`lng`. Years 2026–2031. The server rate-limits (429,
  `Retry-After`, code `API_RATE_LIMITED`). robots.txt allows all. **Terms read 19.10.2026:** the portal has no terms
  page of its own; the Tauron service rules (`tauron-dystrybucja.pl/regulamin-swiadczenia-uslug`, §11) only forbid
  overloading their systems, and the map says the data are "orientacyjne" (indicative). Nothing forbids automated
  reading, so the `tauron` connector (`sources/tauron.py`, `dclh fetch tauron`) reads it politely (about 6 requests).
- **`energa` ✓.** Since the 30.04.2026 amendment, Energa publishes "Informacja o wartości łącznej dostępnej odbiorczej mocy
  przyłączeniowej" as a PDF (16 pages, stan na 31.08.2026) on
  `https://energa-operator.pl/przylaczenie-do-sieci/informacje-o-stanie-przylaczen` (the link redirects to a signed CDN URL).
  Capacity is given **per group of coherent 110 kV nodes** (named GPZ lists per group), with a 5-year horizon. This is
  the 110 kV-level signal the brief wanted. pdfplumber extracts the tables. The `energa` connector
  (`sources/energa.py`, `dclh fetch energa`) reads Tabela 2.1 (stations per group, 2026 model) and Tabela 2.3 (MW per
  group): 48 groups, 401 stations (396 after merging busbar sections) on 19.10.2026.
- **`pse_closed` ✓.** `https://www.pse.pl/documents/20182/51490/20260204_Lista_rozdzielni-brak_mozliwosci_przylaczenia.xlsx`
  (stan na 4.02.2026): columns `ZKO PSE`, `Symbol stacji`, `Nazwa stacji`, `Rozdzielnia`, `Liczba dostępnych miejsc
  przyłączenia`. The file name carries the date. Find the newest one on the page linked by the `pse_odb` probe.
  `dclh import-manual pse_closed <file.xlsx>` reads PSE's file as it is (`sources/pse.py`): 37 switchyards at 33
  stations. The tables below the first one list planned switchyards by plan number only and are skipped.
- **`pse_odb` ✓, an XLSX, not a PDF.** The art. 7 ust. 8l PE list:
  `https://www.pse.pl/documents/20182/51490/Informacje_publikowane_zgodnie_z_Art_7_ust_8l_ustawy_PE_stan_na_31_08_2026.xlsx`
  (monthly, the date is in the name). 886 rows for the PSE network. `Rodzaj instalacji = ODB` gives **33 load projects, mostly data
  centres** (e.g. 100 MW at Mikułowa 110 kV, 150 MW Warszawa, 800 MW Krzemienica), with station, voltage, MW and
  status/dates. This is a competitor and queue signal. **The column "Nazwa podmiotu ubiegającego się o przyłączenie"
  names the applicant. Do not store it** (it can be a person; brief 9.6). Keep station, voltage, type, MW, status and dates only.
  `dclh import-manual pse_odb <file.xlsx>` does exactly that and stores the reduced CSV, never PSE's file.
- **`pse_avail` ✓ PDF.** `informacja_o_dostepnosci_mocy_przylaczeniowej_do_sieci_przesylowej.pdf` on
  `…/informacja-o-dostepnosci-mocy-przylaczeniowej`. Existing stations have 0 MW for loads. Page 12 lists **planned load
  stations** with MW (e.g. WYP Wypędy 200, MKA Legnica 1000, ZER Poznań 500, GNA Konin 500, DZO Toruń 1000; 24 rows),
  given by region name, not coordinates. pdfplumber reads the table cleanly. Geocode the ~24 stations by hand
  into `data/manual/` (estimated confidence).
- **`enea` – manual CSV only (owner decision 19.10.2026).** "Mapa Dostępnych Mocy Przyłączeniowych" is a public Power BI
  report (`app.powerbi.com/view?r=…`), with no file or stable API. Its own disclaimer says the values are for **MV/LV end
  users and do not apply to connection group II** (110 kV), the group for a 60 MW site. So Enea stays a weak signal,
  typed in by hand with `dclh import-manual enea`. No probe and no connector.
  Checked on 19.10.2026: Enea publishes **no statutory load-capacity document** like Energa's. Its pages
  `…/przylaczenie-do-sieci/informacje-o-przylaczeniach`, `…/dostepne-moce` and `…/przylaczone-i-dostepne-moce-oze`
  link only Power BI reports and these files:
  - **Art. 7 ust. 8l list**, two PDFs, stan na 31.08.2026: `https://www.operator.enea.pl/media/5086/wspolny-wykaz-informacje-niejawnev-czesc-1pdf.pdf`
    and `…/media/5089/wspolny-wykaz-informacje-niejawne-czesc-2pdf.pdf` (about 22 MB each, 57 pages each). It contains about 82
    load (`ODB`) entries at 110 kV, with the station or line, MW and status. This is a queue signal like the PSE list, but the
    PDF text extracts badly (rotated and wrapped cells), so it needs a table parser before use. The same rule applies:
    it names applicants and sometimes their addresses, so do not store them.
  - `…/media/3193/wykaz-gpz.pdf`: GPZs under the MV generation-connection criteria (generation only, not loads).

### Bonus layers

- **`heat` ✓.** URE concession register JSON: `https://api.ure.gov.pl/api/ConcessionOtherFuel?rodzajKoncesji=PCC`
  (344 heat transmission/distribution licences, with `miejscowosc` and `wojewodztwo`). It is a town-level proxy for "district
  heating exists here". Store the town and licence type only (the rows carry addresses and tax ids).
  **Loaded live 21.10.2026** with `dclh fetch heat` (344 rows, from the VPS). The connector keeps town, voivodeship and end date.
  Towns are placed by their urban gmina (TERYT type digit 1); miejsko-wiejska seats are not placed (their polygon covers farmland).
- **`psi` ✓.** Rozporządzenie RM z 14.12.2021 w sprawie mapy pomocy regionalnej na lata 2022–2027 (Dz.U. 2021 poz.
  2422), in force. **Loaded live 21.10.2026** with `dclh fetch psi` from the ELI text
  (`api.sejm.gov.pl/eli/acts/DU/2021/2422/text.html`, 200). The map is not a gmina table: it is voivodeship rates (50/40/30/25 %) with
  exceptions, so the rates are constants in `model/psi.py`, checked against that text on every fetch. No `psi_aid.csv` is needed.
  Still unknown, so missing: the Warsaw regional area's powiats (only its listed gminas are rated), the Poznań subregion (all Wielkopolska
  outside Poznań) and the FST +10 points (§ 5).
- **`uke` ⚠ lead only, not obtainable as data (checked 21.10.2026).** `pitmap.uke.gov.pl` is a map SPA. Its GeoServer
  (`/pit2/PitMapa/backend/geoserver/wms` and `/ows`) answers `404 Użytkownik niezalogowany` (login required), and the WMTS
  is the same. The map's search API (`/search/api/getLayerInPolygon`) answers anonymously but is not documented and its terms
  are unknown. No connector until the owner decides (see docs/STATUS.md).

Unverified endpoints are not used by any connector until they are confirmed in this file.

## Generated discovery table

<!-- discovery:begin -->
Run: 2026-10-19 10:02 UTC · from: VPS (owner's Polish-IP machine)

| Status | Source | Plan | Probes |
|---|---|---|---|
| ✓ | `prg` GUGiK PRG – granice administracyjne | mvp | ogc: ok · 200 · 2.0.0 · 52 layers<br>http: ok · 200 |
| ✓ | `osm_power` OpenStreetMap – sieć elektroenergetyczna (Geofabrik) | mvp | http: ok · 200<br>http: ok · 200 |
| ✓ | `osm_land` OpenStreetMap – użytkowanie terenu i budynki (Geofabrik) | mvp | http: ok · 200 |
| ✓ | `bdot10k` GUGiK BDOT10k | mvp | ogc: ok · 200 · 2.0.0 · 1 layers<br>http: ok · 200 |
| ✓ | `ru` Rejestr Urbanistyczny | mvp | ogc: ok · 200 · 2.0.0 · 6 layers · CountDefault 10<br>ogc: ok · 200 · 2.0.0 · 3 layers · CountDefault 100 |
| ✓ | `kimpzp` KIMPZP – Krajowa Integracja MPZP | mvp | ogc: ok · 200 · 1.3.0 · 9 layers |
| ✓ | `egib` EGiB – zbiorcza usługa WFS działek | mvp | ogc: ok · 200 · 2.0.0 · 2 layers · CountDefault 1000 |
| ✓ | `uldk` ULDK – Usługa Lokalizacji Działek Katastralnych | mvp | http: ok · 200 |
| ✓ | `kiug` KIUG – użytki gruntowe i klasy bonitacyjne | mvp | ogc: ok · 200 · 1.3.0 · 4 layers |
| ✓ | `gdos` GDOŚ Geoserwis – formy ochrony przyrody | mvp | ogc: ok · 200 · 2.0.0 · 23 layers · CountDefault 1000000 |
| ✓ | `isok` ISOK – mapy zagrożenia powodziowego | mvp | ogc: ok · 200 · 2.0.0 · 20 layers · CountDefault 10000 |
| ✓ | `midas` PIG-PIB MIDAS – obszary i tereny górnicze | mvp | arcgis: ok · 200 |
| ✓ | `nid` NID – rejestr zabytków i stanowiska archeologiczne | mvp | http: ok · 200<br>page: ok · 200 · 4 endpoint candidates |
| ✓ | `pse_closed` PSE – zamknięte rozdzielnie (brak warunków przyłączenia) | mvp | http: ok · 200 |
| ⚠ | `pse_odb` PSE – wykaz podmiotów ubiegających się o przyłączenie (art. 7 ust. 8l PE) | mvp | page: ok · 200 · 12 endpoint candidates |
| ⚠ | `pse_avail` PSE – raport o dostępnych mocach przyłączeniowych | mvp | page: ok · 200 · 2 endpoint candidates |
| – | `enea` Enea Operator – mapa dostępnych mocy dla odbiorców | manual | – |
| ⚠ | `energa` Energa-Operator – dostępne moce odbiorcze | mvp | page: ok · 200 · 4 endpoint candidates |
| ⚠ | `tauron` Tauron Dystrybucja – dostepnemoce.pl | mvp | page: ok · 200 · 12 endpoint candidates |
| – | `dso_portals` Nowe portale mocy OSD (nowelizacja 2026) | v2 | – |
| ⚠ | `uke` UKE – infrastruktura telekomunikacyjna | mvp | page: ok · 200 · 5 endpoint candidates |
| ✓ | `psi` Polska Strefa Inwestycji – mapa pomocy regionalnej | mvp | http: ok · 200 |
| – | `kowr` KOWR – oferty nieruchomości rolnych | manual | – |
| – | `invest_offers` Oferty inwestycyjne gmin / SSE / COI | mvp | – |
| ✓ | `heat` Sieci ciepłownicze (proxy – koncesje URE) | mvp | http: ok · 200 |
| – | `bazaoos` GDOŚ – Baza OOŚ (decyzje środowiskowe) | manual | – |
| – | `ekw` Księgi wieczyste (EKW) | manual | – |
| – | `bip` BIP gmin | v2 | – |

### Przekierowania do przeglądu (nie zastosowane automatycznie)

| Source | Status | Z | Na | Uwaga |
|---|---|---|---|---|
| `osm_power` | 307 | https://download.geofabrik.de/europe/poland-latest.osm.pbf | https://download.geofabrik.de/europe/poland-261018.osm.pbf | ta sama domena |
| `osm_land` | 307 | https://download.geofabrik.de/europe/poland-latest.osm.pbf | https://download.geofabrik.de/europe/poland-261018.osm.pbf | ta sama domena |
| `nid` | 302 | https://mapy.zabytek.gov.pl/ | https://mapy.zabytek.gov.pl/nid/ | ta sama domena |
<!-- discovery:end -->

## MVP sources: candidate endpoints, format, licence, fallback

The endpoints in this table are the **October 2026 desk-research candidates**. The status column shows the result of
19.10.2026. The confirmed endpoints are in "Current state" above and in `config/sources.yaml`.
Fallback order for interactive maps (brief): JSON endpoint → Playwright → manual CSV import.

| Status | Source | Candidate endpoints (probes) | Format | Licence (expected, to confirm) | Fallback |
|---|---|---|---|---|---|
| ✓ | `prg` | ogc: `https://mapy.geoportal.gov.pl/wss/service/PZGIK/PRG/WFS/AdministrativeBoundaries`<br>http: `https://opendata.geoportal.gov.pl/prg/granice/00_jednostki_administracyjne.zip` | WFS (GML); bulk ZIP (SHP) | Otwarte dane GUGiK (bez opłat, art. 40a Pgik) | Bulk ZIP from opendata.geoportal.gov.pl; manual download |
| ✓ | `osm_power` (core) | http: `https://download.geofabrik.de/europe/poland-latest.osm.pbf`<br>http: `https://download.geofabrik.de/europe/poland-latest.osm.pbf.md5` | .osm.pbf (~2 GB) | ODbL 1.0 (© OpenStreetMap contributors) | Previous snapshot; another Geofabrik-compatible mirror |
| ✓ | `osm_land` | http: `https://download.geofabrik.de/europe/poland-latest.osm.pbf` | .osm.pbf | ODbL 1.0 (© OpenStreetMap contributors) | Previous snapshot |
| ✓ | `bdot10k` | ogc: `https://mapy.geoportal.gov.pl/wss/service/PZGIK/BDOT/WFS/PobieranieBDOT10k`<br>page: `https://opendata.geoportal.gov.pl/bdot10k/` | Per-powiat ZIP (GML/SHP); WFS | Otwarte dane GUGiK | OSM buildings and landuse (lower confidence) |
| ✓ | `ru` (core) | page: `https://rejestr-urbanistyczny.gov.pl/`<br>browser: `https://rejestr-urbanistyczny.gov.pl/` | WFS/WMS/CSW, APP GML (endpoint to find) | Do ustalenia (dane publiczne, APP) | KIMPZP GetFeatureInfo; missing plan = `unknown` |
| ✓ | `kimpzp` | ogc: `https://mapy.geoportal.gov.pl/wss/ext/KrajowaIntegracjaMiejscowychPlanowZagospodarowaniaPrzestrzennego` | WMS (often raster) + GetFeatureInfo | Otwarte dane GUGiK | None: zoning stays `unknown` |
| ✓ | `egib` (core) | ogc: `https://mapy.geoportal.gov.pl/wss/service/PZGIK/EGIB/WFS/UslugaZbiorcza` | WFS 2.0 GML, EPSG:2180 | Otwarte dane GUGiK (geometria działek) | ULDK per parcel (slow); county WFS services |
| ✓ | `uldk` | http: `https://uldk.gugik.gov.pl/?request=GetParcelByXY&xy=637500,486500,2180&result=teryt,voivodeship,county,commune` | Plain text / WKT | Otwarte dane GUGiK | EGiB WFS by bbox |
| ✓ | `kiug` | ogc: `https://integracja.gugik.gov.pl/cgi-bin/KrajowaIntegracjaUzytkowGruntowych` | WMS GetFeatureInfo | Otwarte dane GUGiK | BDOT10k/OSM forest; soil class `missing` |
| ✓ | `gdos` (core) | ogc: `https://sdi.gdos.gov.pl/wfs` | WFS (GML) | Dane publiczne GDOŚ | GDOŚ SHP downloads; WMS for display only |
| ✓ | `isok` (core) | page: `https://wody.isok.gov.pl/` | WFS 2.0 / ATOM (endpoint to find) | Dane publiczne PGW Wody Polskie | ATOM bulk download per river basin |
| ✓ | `midas` | arcgis: `https://cbdgmapa.pgi.gov.pl/arcgis/rest/services`<br>page: `https://cbdgmapa.pgi.gov.pl/` | ArcGIS REST / WFS (to confirm) | Dane publiczne PIG-PIB | CBDG SHP downloads |
| ✓ | `nid` | http: `https://api.dane.gov.pl/1.4/datasets?q=rejestr%20zabytk%C3%B3w%20nieruchomych&per_page=5`<br>page: `https://mapy.zabytek.gov.pl/` | WMS/WFS; CSV on dane.gov.pl | Dane publiczne NID / dane.gov.pl | CSV + geocoding by address/TERYT |
| ✓ | `pse_closed` | page: `https://www.pse.pl/` | XLSX (page to find) | Informacja publiczna PSE | Manual download into `data/manual/` |
| ✓ | `pse_odb` | page: `https://www.pse.pl/` | PDF (page to find) | Informacja publiczna PSE | Claude API table extraction (flag); manual CSV |
| ✓ | `pse_avail` | page: `https://www.pse.pl/` | Monthly PDF (page to find) | Informacja publiczna PSE | Claude API table extraction (flag); manual CSV |
| – | `enea` | page: `https://www.enea-operator.pl/`<br>browser: `https://www.enea-operator.pl/` | Interactive map → JSON? (to find) | Informacja publiczna OSD (sprawdzić regulamin) | Playwright capture → manual CSV |
| ✓ | `energa` | page: `https://energa-operator.pl/`<br>browser: `https://energa-operator.pl/` | Unknown: existence to confirm | Informacja publiczna OSD (sprawdzić regulamin) | Playwright capture → manual CSV |
| ✓ | `tauron` | page: `https://dostepnemoce.pl/`<br>browser: `https://dostepnemoce.pl/` | Interactive map (to find) | Informacja publiczna OSD (sprawdzić regulamin) | Playwright capture → manual CSV |
| ⚠ | `uke` | page: `https://www.uke.gov.pl/` | WMS / export (to find) | Do ustalenia | None: connectivity uses city distance only |
| ✓ | `psi` | page: `https://www.gov.pl/web/fundusze-regiony/polska-strefa-inwestycji` | Regulation text → static CSV | Akt prawny (rozporządzenie) – domena publiczna | n/a (static, prepared by hand) |
| – | `kowr` | page: `https://nieruchomoscikowr.gov.pl/` | HTML listings | Sprawdzić regulamin i robots.txt przed automatyzacją | Manual CSV import if terms forbid scraping |
| – | `invest_offers` | – | Manual CSV (template) | – | n/a |
| ✓ | `heat` | page: `https://bip.ure.gov.pl/` | URE concession list (XLSX/PDF on BIP) | Informacja publiczna URE | Hand-made list of towns with district heating |

## Not automated (v2 or link only)

| Source | Plan | Name | Link |
|---|---|---|---|
| `dso_portals` | v2 | Nowe portale mocy OSD (nowelizacja 2026) | – |
| `enea` | manual | Enea Operator – mapa dostępnych mocy dla odbiorców (Power BI, SN/nN) | `https://www.operator.enea.pl/przylaczenie-do-sieci/dostepne-moce` |
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

## Open questions per source

- **ru**: confirm the `SG` symbol on a real plan ogólny with a strefa gospodarcza (none in the 1421 sample). Re-run
  after 30.11.2026, when the register is complete.
- **isok**: the third-cycle flood maps (the samples end 21.12.2025); check `validityperiod_end` on each refresh.
- **tauron / PGE Dystrybucja**: look for a statutory load-capacity document like Energa's. Enea has none (checked
  19.10.2026). PGE (Łódź, centre) published nothing for loads before.
- **DSO art. 7 ust. 8l lists** (Enea PDFs, likely also Tauron and PGE): a 110 kV load queue signal; needs a PDF table
  parser and must drop applicant names.
- **tauron**: read the site's terms before a scheduled connector; keep to the server's rate limit.
- **psi**: the Warsaw regional area's powiat list (GUS NUTS PL91) and the Poznań subregion's powiats, to replace the
  "unknown" rates with real ones (the owner or GUS).
- **uke**: owner decision needed: a login for the PIT map, or an agreed use of the undocumented search API.
- **kowr**: manual-only (owner decision 12.10.2026); no scraping.
