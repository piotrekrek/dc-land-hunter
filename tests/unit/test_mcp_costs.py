"""connection_costs: the arithmetic, the refusals, and that it equals the web calculator's constants."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from dclh_api.mcp import tools_costs
from dclh_pipeline.config import load_scoring
from tests.unit.test_mcp_tools import anyio_backend, call, mcp  # noqa: F401

pytestmark = pytest.mark.anyio
WEB = Path(__file__).parents[2] / "web" / "src" / "lib" / "connectionCost.ts"


async def test_60_mw_is_5_4_mln_frozen(mcp: Any) -> None:  # noqa: F811
    out = await call(mcp, "connection_costs", mw=60)
    assert out["application_fee"]["pln"] == 60_000
    assert out["advance"]["pln"] == 3_600_000
    assert out["security"]["pln"] == 1_800_000
    assert out["frozen_pln"] == 5_400_000
    assert not any(out[k]["cap_applied"] for k in ("application_fee", "advance", "security"))


async def test_caps_apply_from_100_mw_and_security_stops_at_100(mcp: Any) -> None:  # noqa: F811
    at_100 = await call(mcp, "connection_costs", mw=100)
    assert at_100["application_fee"]["pln"] == 100_000
    assert at_100["advance"]["pln"] == 6_000_000
    assert not at_100["application_fee"]["cap_applied"]  # exactly at the cap
    big = await call(mcp, "connection_costs", mw=250)
    assert big["application_fee"]["cap_applied"] and big["advance"]["cap_applied"]
    assert big["advance"]["pln"] == 6_000_000
    assert big["security"]["pln"] == 3_000_000 and big["security"]["cap_applied"]
    assert "open_question" in big and "open_question" not in at_100


@pytest.mark.parametrize("mw", [0, -5, 1000.5, float("nan")])
async def test_invalid_power_is_refused_in_polish(mcp: Any, mw: float) -> None:  # noqa: F811
    with pytest.raises(ToolError, match="Moc musi być"):
        await call(mcp, "connection_costs", mw=mw)


async def test_needs_mw_or_site(mcp: Any) -> None:  # noqa: F811
    with pytest.raises(ToolError, match="Podaj moc"):
        await call(mcp, "connection_costs")


async def test_metadata_and_caveats(mcp: Any) -> None:  # noqa: F811
    out = await call(mcp, "connection_costs", mw=80)
    for key in ("application_fee", "advance", "security", "validity"):
        assert out[key]["confidence"] == "estimated" and out[key]["source_id"] and out[key]["as_of"]
    assert out["validity"]["conditions_valid_months"] == 12
    text = " ".join(out["caveats"])
    assert "weryfikacji prawnej" in text and "SPV" in text and "porada prawna" in text


def test_python_rules_equal_the_web_constants() -> None:
    src = WEB.read_text(encoding="utf-8")
    block = src.split("DEFAULT_CONNECTION_RULES")[1].split("};")[0]
    web = {k: float(v.replace("_", "")) for k, v in re.findall(r"(\w+):\s*([\d_]+)", block)}
    cc = load_scoring().connection_cost
    assert web == {
        "feePlnPerKw": cc.application_fee_pln_per_kw,
        "feeCapPln": cc.application_fee_max_pln,
        "advancePlnPerKw": cc.advance_pln_per_kw,
        "advanceCapPln": cc.advance_max_pln,
        "securityPlnPerKw": cc.security_pln_per_kw,
        "securityMaxMw": cc.security_applies_up_to_mw,
    }


def test_compute_matches_the_web_formula() -> None:
    cc = load_scoring().connection_cost
    for mw in (0.5, 60, 99.9, 150, 400):
        kw = mw * 1000
        out = tools_costs.compute(cc, mw)
        assert out["application_fee"]["pln"] == min(kw * 1, 100_000)
        assert out["advance"]["pln"] == min(kw * 60, 6_000_000)
        assert out["security"]["pln"] == min(kw, 100_000) * 30
