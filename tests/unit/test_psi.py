"""psi bonus: the regulation text is checked, gminas get the right rate, and missing rules stay missing."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import geopandas as gpd
import httpx
import pandas as pd
import pytest
from shapely.geometry import box
from shapely.geometry.base import BaseGeometry

from dclh_pipeline.config import ScoringConfig, SourceSpec, load_scoring
from dclh_pipeline.http import PoliteClient
from dclh_pipeline.model.psi import WARSAW_GMINY_PCT, PsiLayer, city_pct, intensity_pct, psi_points_fact
from dclh_pipeline.snapshots import Snapshot, SnapshotStore
from dclh_pipeline.sources.psi import ACT_URL, PsiConnector, load_psi_layer

SPEC = SourceSpec(id="psi", name="PSI", used_for="t", plan="mvp", cadence="static")
REGULATION = Path(__file__).parents[1] / "fixtures/psi/regulation_2021_2422.html"
CFG: ScoringConfig = load_scoring()
YEAR = 2026
# The listed Warsaw-area gminas, with TERYT codes as PRG has them (Grodzisk Mazowiecki 25 %, Tłuszcz 35 %).
WARSAW = {"1405043": 25, "1434113": 35}
NOW = datetime(2026, 10, 21, tzinfo=UTC)


def _square(x: float, y: float, size: float = 1000.0) -> BaseGeometry:
    return box(x, y, x + size, y + size)


def _snapshot(store: SnapshotStore, text: str | None = None) -> Snapshot:
    content = text.encode("utf-8") if text is not None else REGULATION.read_bytes()
    return store.save("psi", content, suffix=".html", url=ACT_URL, content_type="text/html")


def _mazowsze_gminas() -> gpd.GeoDataFrame:
    """Every listed Warsaw-area gmina once, under a TERYT in Mazowsze (the real codes are in WARSAW)."""
    names = [name for names in WARSAW_GMINY_PCT.values() for name in names]
    teryts = [f"1499{i:03d}" for i in range(len(names))]
    teryts[names.index("Grodzisk Mazowiecki")] = "1405043"
    teryts[names.index("Tłuszcz")] = "1434113"
    geoms = [_square(20_000 * i, 0) for i in range(len(names))]
    return gpd.GeoDataFrame({"teryt": teryts, "name": names}, geometry=geoms, crs="EPSG:2180")


def test_regulation_fixture_matches_every_rule(store: SnapshotStore) -> None:
    rules = PsiConnector(SPEC).parse(_snapshot(store))
    assert set(rules["scope"]) == {"voivodeship", "warsaw_gmina", "city"}
    assert rules[rules["scope"] == "warsaw_gmina"]["name"].str.contains("Żabia Wola").any()
    assert set(rules.loc[rules["scope"] == "voivodeship", "pct"]) == {25, 30, 40, 50}


def test_a_changed_rate_fails_instead_of_scoring_with_old_rates(store: SnapshotStore) -> None:
    text = REGULATION.read_text(encoding="utf-8").replace("50% - na obszarach", "45% - na obszarach", 1)
    with pytest.raises(ValueError, match="no longer has"):
        PsiConnector(SPEC).parse(_snapshot(store, text))


def test_a_renamed_gmina_fails_the_run(store: SnapshotStore) -> None:
    text = REGULATION.read_text(encoding="utf-8").replace("Kampinos", "Kampin0s")
    with pytest.raises(ValueError, match="Kampinos"):
        PsiConnector(SPEC).parse(_snapshot(store, text))


def test_fetch_refuses_a_page_that_is_not_the_regulation(
    store: SnapshotStore, make_client: Callable[..., PoliteClient]
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow:\n")
        return httpx.Response(200, text="<html>Strona błędu</html>")

    with pytest.raises(ValueError, match="does not look like"):
        PsiConnector(SPEC).fetch(make_client(handler), store)


@pytest.mark.parametrize(
    ("teryt", "expected"),
    [
        ("0601011", 50),  # Lublin voivodeship
        ("0201011", 25),  # Dolnośląskie outside Wrocław
        ("0401011", 40),  # kujawsko-pomorskie
        ("2201011", 30),  # pomorskie
        ("1405043", 25),  # listed Warsaw-area gmina
        ("1434113", 35),  # listed Warsaw-area gmina at 35 %
        ("1419011", None),  # Mazowsze, not listed: the Warsaw-area boundary is unknown
        ("3011011", None),  # Wielkopolska outside Poznań: the Poznań subregion is unknown
        ("3064011", 15),  # Poznań, 2025–2027
        ("0264011", 15),  # Wrocław, 2025–2027
    ],
)
def test_intensity_by_gmina(teryt: str, expected: int | None) -> None:
    assert intensity_pct(teryt, YEAR, WARSAW) == expected


def test_city_rates_change_with_the_year() -> None:
    assert city_pct(2023) == 20
    assert city_pct(2025) == 15
    assert city_pct(2028) is None


def _layer(gminy: list[tuple[str, BaseGeometry]]) -> PsiLayer:
    return PsiLayer.build("psi", NOW, gminy, WARSAW)


def test_points_scale_with_the_rate_and_cap_at_the_maximum() -> None:
    layer = _layer(
        [
            ("1405043", _square(0, 0)),  # 25 %
            ("0601011", _square(5000, 0)),  # 50 %, the top of the map
            ("1419011", _square(10000, 0)),  # unknown
        ]
    )
    fact = psi_points_fact(_square(100, 100, 300), layer, CFG)
    assert fact is not None
    assert fact.value == pytest.approx(3 * 25 / 50)
    fact = psi_points_fact(_square(5100, 100, 300), layer, CFG)
    assert fact is not None
    assert fact.value == pytest.approx(3.0)
    assert psi_points_fact(_square(10100, 100, 300), layer, CFG) is None  # unknown rate: missing, not 0
    assert psi_points_fact(_square(-50_000, 0, 300), layer, CFG) is None  # in no gmina


def test_the_site_takes_the_gmina_that_covers_most_of_it() -> None:
    layer = _layer([("1405043", _square(0, 0)), ("0601011", _square(1000, 0))])
    site = box(700, 100, 1700, 200)  # 300 m x 100 m in the Warsaw-area gmina, 700 m x 100 m in the Lublin one
    fact = psi_points_fact(site, layer, CFG)
    assert fact is not None
    assert fact.value == pytest.approx(3.0)  # Lublin (50 %) wins


def test_no_layer_means_missing() -> None:
    assert psi_points_fact(_square(0, 0), None, CFG) is None


def test_load_psi_layer_places_warsaw_names_in_mazowsze_only(store: SnapshotStore) -> None:
    store.mark_good(_snapshot(store))
    gminas = _mazowsze_gminas()
    # Baranów also exists outside Mazowsze in PRG. Only the Mazowsze gmina may take the rate.
    other = gpd.GeoDataFrame(
        {"teryt": ["0614022"], "name": ["Baranów"]}, geometry=[_square(0, 9000)], crs="EPSG:2180"
    )
    layer = load_psi_layer(store, SPEC, gpd.GeoDataFrame(pd.concat([gminas, other], ignore_index=True)))
    assert layer is not None
    assert layer.warsaw["1405043"] == 25
    assert layer.warsaw["1434113"] == 35
    assert len(layer.warsaw) == sum(len(v) for v in WARSAW_GMINY_PCT.values())
    assert "0614022" not in layer.warsaw


def test_load_psi_layer_fails_when_a_listed_gmina_is_missing(store: SnapshotStore) -> None:
    store.mark_good(_snapshot(store))
    gminas = _mazowsze_gminas().iloc[1:]  # drops the first listed gmina (Baranów) from PRG
    with pytest.raises(ValueError, match="Baranów"):
        load_psi_layer(store, SPEC, gminas)


def test_load_psi_layer_is_missing_without_a_snapshot(store: SnapshotStore) -> None:
    gminas = gpd.GeoDataFrame({"teryt": [], "name": []}, geometry=[], crs="EPSG:2180")
    assert load_psi_layer(store, SPEC, gminas) is None
