"""Tests on the real live responses in tests/fixtures/live/poddebice-axis-order-check (captured from the services)."""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest
from pyproj import Transformer

from dclh_pipeline.config import load_sources
from dclh_pipeline.http import PoliteClient
from dclh_pipeline.snapshots import SnapshotStore
from dclh_pipeline.sources.egib import EgibConnector, read_page
from dclh_pipeline.sources.uldk import parse_point
from tests.conftest import FIXTURES

LIVE = FIXTURES / "live" / "poddebice-axis-order-check"
EGIB_SAMPLE = LIVE / "egib_getfeature.gml"
ULDK_SAMPLE = LIVE / "uldk_getparcelbyxy.txt"
PODDEBICE_BOX = (18.70, 51.85, 18.90, 51.95)  # lon/lat around parcel 101103_5.0011.1 (confirmed by ULDK)
ID_PATTERN = re.compile(r"^\d{6}_\d+\.\d{4}\.\S+$")
_to_wgs = Transformer.from_crs(2180, 4326, always_xy=True)


@pytest.fixture(scope="module")
def parcels():  # type: ignore[no-untyped-def]
    return read_page(EGIB_SAMPLE)


def test_sample_has_twenty_polygon_parcels_in_epsg_2180(parcels) -> None:  # type: ignore[no-untyped-def]
    assert len(parcels) == 20
    assert parcels.crs.to_epsg() == 2180
    assert set(parcels.geom_type) == {"Polygon"}
    assert parcels.geometry.is_valid.all()


def test_parcel_ids_have_the_teryt_form_and_are_unique(parcels) -> None:  # type: ignore[no-untyped-def]
    ids = parcels["ID_DZIALKI"].tolist()
    assert all(ID_PATTERN.match(i) for i in ids), ids
    assert len(set(ids)) == len(ids)


def test_parcels_are_where_uldk_puts_them(parcels) -> None:  # type: ignore[no-untyped-def]
    # Parcel 101103_5.0011.1: ULDK puts it in Poddębice, and GDAL's easting-northing geometry agrees.
    lon_min, lat_min, lon_max, lat_max = PODDEBICE_BOX
    for geom in parcels.geometry:
        lon, lat = _to_wgs.transform(geom.centroid.x, geom.centroid.y)
        assert lon_min < lon < lon_max and lat_min < lat < lat_max, (lon, lat)


def test_request_bbox_is_northing_first() -> None:
    from dclh_pipeline.sources.egib import getfeature_params

    params = getfeature_params((448_594.0, 485_514.0, 449_194.0, 486_114.0), start=0, count=20)
    assert params["bbox"] == "485514.0,448594.0,486114.0,449194.0,urn:ogc:def:crs:EPSG::2180"


def test_registered_area_is_empty_so_the_geometry_area_is_used(parcels) -> None:  # type: ignore[no-untyped-def]
    # POLE_EWIDENYJNE is empty in this sample. Depending on the GDAL version, the column is either dropped
    # (local) or kept with empty values (CI), so only the values are checked. The land score uses geometry area.
    if "POLE_EWIDENYJNE" in parcels.columns:
        assert (parcels["POLE_EWIDENYJNE"].fillna("") == "").all()


def test_uldk_point_matches_the_parcel_id_form() -> None:
    parcel = parse_point(ULDK_SAMPLE.read_text(encoding="utf-8"))
    assert ID_PATTERN.match(parcel.parcel_id)
    assert parcel.voivodeship == "wielkopolskie"
    assert parcel.commune == "Konin (miasto)"
    assert parcel.wkt is not None and parcel.wkt.startswith("SRID=2180;POLYGON")


def test_uldk_error_status_is_an_error() -> None:
    with pytest.raises(ValueError, match="błąd"):
        parse_point("-1\nbrak wyników\n")


def test_unknown_total_keeps_paging_until_a_short_page(
    tmp_path: Path, make_client: Callable[..., PoliteClient]
) -> None:
    """The service says numberMatched="unknown". A full page means there may be more, so the connector asks again."""
    spec = next(s for s in load_sources().sources if s.id == "egib")
    body = EGIB_SAMPLE.read_bytes()
    requests: list[int] = []

    # The page after the last one: same envelope, no members, numberReturned="0".
    head = body.split(b"<wfs:member>")[0].replace(b'numberReturned="20"', b'numberReturned="0"')
    empty = head + b"</wfs:FeatureCollection>\n"

    def handler(request: httpx.Request) -> httpx.Response:
        if "bbox" not in request.url.params:
            return httpx.Response(404)
        start = int(request.url.params["startIndex"])
        requests.append(start)
        return httpx.Response(200, content=body if start == 0 else empty)

    connector = EgibConnector(spec, bboxes=[(448_600, 485_600, 448_700, 485_700)], page_size=20)
    with make_client(handler) as client:
        table = connector.parse(connector.fetch(client, SnapshotStore(tmp_path)))
    assert requests == [0, 20]
    assert len(table) == 20


KONIN_LIVE = FIXTURES / "live" / "cge-konin"
KONIN_NORTH_BOX = (18.20, 52.22, 18.30, 52.26)  # lon/lat: Konin, north of the centre (captured 11.10.2026)


@pytest.fixture(scope="module")
def konin_parcels():  # type: ignore[no-untyped-def]
    return read_page(KONIN_LIVE / "egib_getfeature.gml")


def test_konin_sample_is_in_the_city_and_has_teryt_ids(konin_parcels) -> None:  # type: ignore[no-untyped-def]
    assert len(konin_parcels) == 20
    assert konin_parcels.crs.to_epsg() == 2180
    assert set(konin_parcels["NAZWA_GMINY"]) == {"Miasto Konin"}
    assert all(ID_PATTERN.match(i) for i in konin_parcels["ID_DZIALKI"]), konin_parcels["ID_DZIALKI"].tolist()
    assert len(set(konin_parcels["ID_DZIALKI"])) == 20


def test_konin_sample_parcels_are_north_of_the_centre(konin_parcels) -> None:  # type: ignore[no-untyped-def]
    lon_min, lat_min, lon_max, lat_max = KONIN_NORTH_BOX
    for geom in konin_parcels.geometry:
        lon, lat = _to_wgs.transform(geom.centroid.x, geom.centroid.y)
        assert lon_min < lon < lon_max and lat_min < lat < lat_max, (lon, lat)


def test_konin_uldk_point_is_the_same_city(konin_parcels) -> None:  # type: ignore[no-untyped-def]
    parcel = parse_point((KONIN_LIVE / "uldk_getparcelbyxy.txt").read_text(encoding="utf-8"))
    assert parcel.commune == "Konin (miasto)"
    assert parcel.parcel_id.startswith("306201_1.")
