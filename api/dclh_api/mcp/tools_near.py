"""`sites_near`: the sites within a radius of a place. The place is a gmina or powiat (name or TERYT code,
resolved from the boundaries in `admin_units`) or a WGS84 point. There is no geocoder: a village or town that
is not a gmina has no boundary here, so it can only be given as lat/lon.

All geometry is EPSG:2180; the point is converted in SQL. `distance_km` is measured from the place's polygon
(or point) to the site's outline, so 0 means the site touches or lies inside the gmina.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from pydantic import Field
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from dclh_api.mcp import areas, search
from dclh_api.mcp.context import GRID_NOTE, run_db
from dclh_api.mcp.search import Near, Verdict
from dclh_api.mcp.tools_read import READ_ONLY, Detail, _clamp

DEFAULT_RADIUS_KM = 20.0
MAX_RADIUS_KM = 100.0
# Poland's bounding box in WGS84, with a margin: it refuses a swapped lat/lon or a point in another country.
LAT_RANGE = (48.9, 55.0)
LON_RANGE = (14.0, 24.3)
LEVEL_BY_CODE_LENGTH: dict[int, areas.Level] = {7: "gmina", 4: "powiat"}
LABEL = {"gmina": "gminy", "powiat": "powiatu"}
MANY = {"gmina": "gmin", "powiat": "powiatów"}
NOT_FOUND = (
    "Nie znaleziono gminy ani powiatu „{value}”. Miejscowości, które nie są gminą, nie mają tu granic: "
    "podaj nazwę gminy (np. „Kutno”), kod TERYT albo współrzędne lat i lon."
)


def resolve_place(session: Session, place: str) -> dict[str, Any]:
    """A gmina or powiat as {teryt, level, name}. A gmina wins over a powiat of the same name ("Wrocław"),
    an exact name over a prefix, and a name that stays ambiguous is an error that lists the candidates."""
    value = place.strip()
    if value.isdigit():
        coded = LEVEL_BY_CODE_LENGTH.get(len(value))
        if coded is None:
            raise ToolError(f"Kod TERYT gminy ma 7 cyfr, a powiatu 4; „{value}” ma {len(value)}.")
        row = (
            session.execute(
                text("SELECT teryt, name FROM admin_units WHERE level = :level AND teryt = :teryt"),
                {"level": coded, "teryt": value},
            )
            .mappings()
            .first()
        )
        if row is None:
            raise ToolError(f"Nie ma {LABEL[coded]} o kodzie TERYT {value}.")
        return {"teryt": row["teryt"], "level": coded, "name": row["name"]}
    for prefix in (False, True):
        for level in ("gmina", "powiat"):
            found = areas.find_units(session, level, value, prefix=prefix)
            if len(found) == 1:
                return {"teryt": found[0]["teryt"], "level": level, "name": found[0]["name"]}
            if found:
                raise ToolError(
                    f"„{value}” pasuje do kilku {MANY[level]}: "
                    f"{areas.describe_units(session, level, found)}. Podaj kod TERYT."
                )
    raise ToolError(NOT_FOUND.format(value=value))


def _near(
    session: Session, place: str | None, lat: float | None, lon: float | None, radius_km: float
) -> tuple[Near, dict[str, Any]]:
    """The `Near` filter and a description of the place for the answer."""
    if radius_km <= 0:
        raise ToolError("Promień (radius_km) musi być większy od zera.")
    capped = radius_km > MAX_RADIUS_KM
    radius_km = min(radius_km, MAX_RADIUS_KM)
    has_place = place is not None and place.strip() != ""
    if has_place == (lat is not None or lon is not None):
        raise ToolError("Podaj albo miejsce (place), albo współrzędne (lat i lon), nie oba naraz.")
    if has_place:
        assert place is not None
        unit = resolve_place(session, place)
        near = Near(
            "(SELECT geom FROM admin_units WHERE teryt = :near_teryt)",
            {"near_teryt": unit["teryt"]},
            radius_km * 1000,
        )
        described: dict[str, Any] = {"kind": unit["level"], **unit}
    else:
        if lat is None or lon is None:
            raise ToolError("Współrzędne wymagają obu wartości: lat i lon.")
        if not (LAT_RANGE[0] <= lat <= LAT_RANGE[1] and LON_RANGE[0] <= lon <= LON_RANGE[1]):
            raise ToolError(
                f"Współrzędne {lat}, {lon} leżą poza Polską (lat {LAT_RANGE[0]}–{LAT_RANGE[1]}, "
                f"lon {LON_RANGE[0]}–{LON_RANGE[1]}). Czy lat i lon nie są zamienione?"
            )
        near = Near(
            "ST_Transform(ST_SetSRID(ST_MakePoint(:near_lon, :near_lat), 4326), 2180)",
            {"near_lon": lon, "near_lat": lat},
            radius_km * 1000,
        )
        described = {"kind": "point", "lat": lat, "lon": lon}
    described["radius_km"] = radius_km
    if capped:
        described["radius_capped"] = f"Promień ograniczono do {MAX_RADIUS_KM:g} km."
    return near, described


def register(mcp: FastMCP, public_url: str) -> None:
    @mcp.tool(annotations=READ_ONLY)
    async def sites_near(
        place: Annotated[
            str | None,
            Field(
                max_length=60,
                description="Gmina or powiat: name (any case, with or without Polish letters) or TERYT code "
                "(7 digits gmina, 4 powiat). Towns and villages that are not a gmina: use lat and lon",
            ),
        ] = None,
        lat: Annotated[float | None, Field(description="WGS84 latitude, instead of `place`")] = None,
        lon: Annotated[float | None, Field(description="WGS84 longitude, instead of `place`")] = None,
        radius_km: Annotated[float, Field(description="Radius in km; capped at 100")] = DEFAULT_RADIUS_KM,
        min_mw: Annotated[float, Field(ge=0, description="Minimum estimated MW (area x density)")] = 60,
        min_ha: Annotated[float, Field(ge=0)] = 0,
        profile: Annotated[
            Literal["built", "container"] | None,
            Field(description="Investor profile that `hide_red`, `verdict` and the reasons refer to"),
        ] = None,
        hide_red: Annotated[
            bool, Field(description="Drop sites `profile` rejects (needs `profile`)")
        ] = False,
        verdict: Annotated[
            list[Verdict] | None,
            Field(description='Keep only these verdicts for `profile`, e.g. ["green", "yellow"]'),
        ] = None,
        max_per_gmina: Annotated[
            int | None, Field(ge=1, le=50, description="At most this many sites per gmina (best first)")
        ] = None,
        sort: Annotated[
            Literal["distance", "score"], Field(description="distance: nearest first; score: best first")
        ] = "distance",
        limit: Annotated[int, Field(description="Sites per page; capped at 50")] = 20,
        cursor: Annotated[str | None, Field(description="`next_cursor` of the previous page")] = None,
        detail: Detail = "short",
    ) -> dict[str, Any]:
        """Sites of 10 ha or more within `radius_km` (default 20, at most 100) of a place: "someone mentioned
        Kutno, what is within 20 km?". The place is a gmina or powiat by name or TERYT code, or a point
        (`lat`, `lon`); villages that are not a gmina can only be given as a point. Each site has
        `distance_km` from the place's border (0 = inside it) and the same fields as `search_sites`, including
        `verdict_counts` for all sites in the radius. Nearest first by default."""

        def query(session: Any, _user: Any) -> dict[str, Any]:
            near, described = _near(session, place, lat, lon, radius_km)
            result = search.search_sites(
                session,
                public_url,
                powiat=None,
                voivodeship=None,
                min_mw=min_mw,
                min_ha=min_ha,
                profile=profile,
                hide_red=hide_red,
                verdicts=verdict,
                sort=sort,
                limit=_clamp(limit),
                cursor=cursor,
                max_per_gmina=max_per_gmina,
                full=detail == "full",
                near=near,
            )
            return {"near": described, **result}

        try:
            return {**await run_db(query), "grid_note": GRID_NOTE}
        except SQLAlchemyError as exc:  # pragma: no cover - search wraps its own queries
            raise ToolError("Baza danych jest niedostępna.") from exc
