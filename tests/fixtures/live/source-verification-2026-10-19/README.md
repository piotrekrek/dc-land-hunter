# Live samples: source verification round 2 (19.10.2026, VPS in Poland)

Raw responses saved while confirming the open sources in `docs/SOURCES.md`. Read by tests: `isok_nz_fluvial_1.gml`
(`tests/unit/test_isok.py`), `midas_tereny_gorny_*` (`tests/unit/test_midas.py`), `ru_pog_strefa_bbox_1421.gml` and `ru_mpzp_akt_1.gml` (`tests/unit/test_ru.py`) and
`kimpzp_gfi_warszawa_sluzewiec.xml` (`tests/unit/test_kimpzp.py`). The rest are here so the first connector for each
source can be tested against a real payload.

| File | Source | Request |
|---|---|---|
| `isok_nz_fluvial_1.gml` | `isok` | `GET https://wody.isok.gov.pl/wss/INSPIRE/INSPIRE_HY_10k_2019_WFS?service=WFS&request=GetFeature&version=2.0.0&typeNames=wms_nz-core:NZ.Fluvial&count=1` (one 10% zone; geometry in EPSG:4258 by default) |
| `kimpzp_gfi_warszawa_sluzewiec.xml` | `kimpzp` | WMS **1.1.1** GetFeatureInfo, `layers=plany`, `srs=EPSG:4326`, lon/lat bbox around 20.9990 E, 52.1800 N, `info_format=text/html` (Warsaw answers XML regardless) |
| `ru_pog_strefa_bbox_1421.gml` | `ru` | `GET …/uslugi-sieciowe/app-pog/wfs?…&typeNames=app-pog:StrefaPlanistyczna&count=10&bbox=470000,620000,495000,645000,urn:ogc:def:crs:EPSG::2180` (679 matched) |
| `ru_mpzp_akt_1.gml` | `ru` | `GET …/uslugi-sieciowe/app-mpzp/wfs?…&typeNames=app-mpzp:AktPlanowaniaPrzestrzennego&count=1` (plan outline only, no designations) |
| `ru_published_query.json` | `ru` | `POST https://rejestr-urbanistyczny.gov.pl/api/public/published/query` with `plansGroup=LOCAL_SPATIAL_DEVELOPMENT_PLAN`, page size 2 |
| `tauron_gpz_search.json` | `tauron` | `POST https://dostepnemoce.tauron-dystrybucja.pl/wp-json/tauron-connector/v1/gpz/search` body `{"layer_type":"odbiorcy","criteria":{"minimum_mw":0,"year_from":2026,"year_to":2026,"province":""},"page":{"offset":0,"limit":5}}` |
| `midas_tereny_gorny_2features.geojson` | `midas` | `GET https://cbdgmapa.pgi.gov.pl/arcgis/rest/services/midas/MapServer/2/query?where=1=1&geometry=300000,220000,360000,280000&geometryType=esriGeometryEnvelope&inSR=2180&spatialRel=esriSpatialRelIntersects&outSR=2180&outFields=ID_KONTURU,STATUS,NAZWA_TG,KOPALINA&returnGeometry=true&orderByFields=OBJECTID&resultOffset=0&resultRecordCount=2&f=geojson` (21.10.2026) |
| `midas_tereny_gorny_count.json` | `midas` | the same query with `returnCountOnly=true&f=json` (15 features in that box) |
| `pse_closed_20260204.xlsx` | `pse_closed` | `GET https://www.pse.pl/documents/20182/51490/20260204_Lista_rozdzielni-brak_mozliwosci_przylaczenia.xlsx` |

Not committed on purpose: the PSE art. 7 ust. 8l list (it names applicants), the URE concession list (company
addresses and tax ids), the BDOT10k powiat zip (18.6 MB), the ISOK bbox sample (22 MB for 13 zones) and the Energa
PDF (2.4 MB). Their URLs are in `docs/SOURCES.md`.
