"""@yardsale's API for sellers: priceCheck, and the description Fetch builds its tools from.
Runs against the offline Market, whose Researcher is a fake, so nothing reaches Apify."""

from __future__ import annotations

import json
import logging
import sys
import types
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import aiohttp
import pytest
from uagents import Context

from tests import fakes, sim
from tests.helpers import local_server
from tests.test_business_api import KEY, PUBLIC, Client
from tests.test_chat_elsewhere import deal
from yardsale import bureau_server
from yardsale.business_api import BusinessApi, _external_json, comps_summary, openapi_spec
from yardsale.common import PipelineError
from yardsale.models import Comp
from yardsale.researcher import ResearchError
from yardsale.sources import ExternalListing
from yardsale.store import Store

BIKE_QUERY = "Trek FX 2 Disc hybrid bike medium"  # the fake Researcher knows this one


class CountingResearch:
    """fakes.research with a call count, to see the cache work."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def __call__(self, query: str, condition: str, max_results: int) -> list[Comp]:
        self.calls.append(query)
        return await fakes.research(query, condition, max_results)


@pytest.fixture
def research() -> CountingResearch:
    return CountingResearch()


async def api_over(
    store: Store, research: CountingResearch, **options: Any
) -> AsyncIterator[tuple[BusinessApi, Client]]:
    async with sim.running_market(store, research=research) as market:
        api = BusinessApi(
            bureau_server.MarketBackend(bureau_server.runtime()),
            market.agent._build_context,
            KEY,
            PUBLIC,
            **options,
        )
        async with local_server(api.app()) as base, aiohttp.ClientSession() as session:
            yield api, Client(base, session)


@pytest.fixture
async def client(store: Store, research: CountingResearch) -> AsyncIterator[Client]:
    async for _, c in api_over(store, research):
        yield c


# --- The description ----------------------------------------------------------------------------


def test_the_description_leads_with_search_and_price_check() -> None:
    spec = openapi_spec(PUBLIC)
    text = json.dumps(spec)
    ids = [op["operationId"] for path in spec["paths"].values() for op in path.values()]
    assert ids[:5] == [
        "searchListings",
        "searchOtherSites",
        "priceCheck",
        "startNegotiation",
        "getNegotiation",
    ]
    # The haggle and deal calls are in only with the haggle tools on, and then marked advanced.
    haggling = openapi_spec(PUBLIC, haggle=True)
    for advanced in ("startHaggle", "getHaggle", "acceptDeal", "getDeal"):
        path = next(
            p
            for p in haggling["paths"].values()
            for op in p.values()
            if op["operationId"] == advanced
        )
        (operation,) = path.values()
        assert operation["summary"].startswith("Advanced")
        assert operation["description"].startswith("Advanced")
    assert "Never invent listings" in spec["info"]["description"]
    # Claude reads photos, ASI:One's models read typed text; no description says otherwise.
    assert "Claude reads the photo" in spec["info"]["description"]
    assert "ASI:One's models read typed text only" in text
    assert "ASI:One reads" not in text and "asi1 reads" not in text.lower()


def test_price_check_and_other_sites_are_described_for_a_tool_runner() -> None:
    spec = openapi_spec(PUBLIC)
    check = spec["paths"]["/price-check"]["get"]
    assert check["operationId"] == "priceCheck"
    names = {p["name"]: p for p in check["parameters"]}
    assert names["q"]["required"] is True and "zip" in names
    answer = check["responses"]["200"]["content"]["application/json"]["schema"]["properties"]
    assert {"typical_usd", "low_usd", "high_usd", "count", "examples", "local"} <= set(answer)
    assert "Missing otherwise" in answer["local"]["description"]
    assert "matched by title, not by photo" in check["description"]
    assert "@ys-orchestrator" in check["description"]

    other = spec["paths"]["/other-sites"]["get"]
    listing = other["responses"]["200"]["content"]["application/json"]["schema"]
    fields = listing["properties"]["listings"]["items"]["properties"]
    assert fields["score"]["type"] == "number" and fields["reason"]["type"] == "string"
    assert "score" not in listing["properties"]["listings"]["items"].get("required", [])
    for word in ("site", "score", "reason", "url"):
        assert word in other["description"]
    json.dumps(spec)


@dataclass(frozen=True)
class Scored(ExternalListing):
    """An ExternalListing once the deal finder scores it (elsewhere.py, in progress)."""

    score: float | None = None
    reason: str = ""


def test_other_site_listings_carry_a_score_only_when_they_have_one() -> None:
    plain = _external_json(deal("Road bike", 20000))
    assert "score" not in plain and "reason" not in plain
    scored = Scored(**vars(deal("Road bike", 20000)), score=0.82, reason="$60 under typical")
    data = _external_json(scored)
    assert data["score"] == 0.82 and data["reason"] == "$60 under typical"
    unscored = Scored(**vars(deal("Road bike", 20000)), score=None, reason="")
    data = _external_json(unscored)
    assert "score" not in data and "reason" not in data


# --- The summary's math --------------------------------------------------------------------------


def comp(dollars: int, best_offer: bool = False, n: int = 0) -> Comp:
    return Comp(
        title=f"Sale {dollars}",
        price_cents=dollars * 100,
        shipping_cents=None,
        condition="Pre-Owned",
        sold_at=f"2026-10-{n + 1:02d}",
        url=f"https://www.ebay.com/itm/{dollars}",
        image_url=None,
        best_offer=best_offer,
    )


def test_summary_is_median_quartiles_and_nearest_examples() -> None:
    summary = comps_summary("bike", [comp(d, n=i) for i, d in enumerate((300, 320, 340, 360, 380))])
    assert summary["count"] == 5
    assert summary["typical_usd"] == 340 and summary["suggested_asking_usd"] == 340
    assert summary["low_usd"] == 310 and summary["high_usd"] == 370
    assert [e["sold_usd"] for e in summary["examples"]] == [340, 320, 360]
    assert summary["examples"][0]["url"] == "https://www.ebay.com/itm/340"
    assert "matched" in summary["basis"].lower() and "@ys-orchestrator" in summary["basis"]


def test_summary_skips_accepted_best_offers_when_it_can() -> None:
    summary = comps_summary("bike", [comp(300), comp(320), comp(900, best_offer=True)])
    assert summary["count"] == 2 and summary["typical_usd"] == 310
    assert "left out 1 accepted Best Offer sale" in summary["basis"]
    only = comps_summary("bike", [comp(900, best_offer=True)])
    assert only["count"] == 1 and only["typical_usd"] == 900


def test_summary_rounds_the_asking_price_like_a_seller() -> None:
    summary = comps_summary("mug", [comp(22), comp(23)])
    assert summary["typical_usd"] == 22.5 and summary["suggested_asking_usd"] == 25
    assert comps_summary("x", [])["count"] == 0
    assert comps_summary("x", [])["typical_usd"] is None


# --- The route -----------------------------------------------------------------------------------


async def test_price_check_uses_the_researcher_and_caches(
    client: Client, research: CountingResearch
) -> None:
    status, check = await client.call("GET", "/price-check", q=BIKE_QUERY)
    assert status == 200 and check["status"] == "done"
    # The fake has 8 sales for this query. One is an accepted Best Offer and is left out; a lot
    # of wheels ($90) and a road bike ($900) stay in, since nobody checked them against a photo.
    assert check["count"] == 7 and check["typical_usd"] == 340
    assert check["low_usd"] == 300 and check["high_usd"] == 380
    assert check["suggested_asking_usd"] == 340
    assert len(check["examples"]) == 3
    assert all(e["url"].startswith("https://www.ebay.com/itm/") for e in check["examples"])
    assert "local" not in check  # the Scout has no live sources in tests
    assert "real eBay sales" in check["next_step"] and "@ys-orchestrator" in check["next_step"]
    assert "Claude reads the photo" in check["next_step"]
    text = json.dumps(check)
    assert "floor" not in text and "lowest" not in text

    # The same words again, in any case or spacing, cost nothing.
    status, again = await client.call("GET", "/price-check", q=f"  {BIKE_QUERY.upper()} ")
    assert status == 200 and again["typical_usd"] == 340
    assert research.calls == [BIKE_QUERY]


async def test_price_check_checks_its_input_and_says_when_nothing_matched(
    client: Client,
) -> None:
    assert (await client.call("GET", "/price-check", key=None, q="bike"))[0] == 401
    assert (await client.call("GET", "/price-check"))[0] == 400
    assert (await client.call("GET", "/price-check", q="the"))[0] == 400
    assert (await client.call("GET", "/price-check", q="bike", zip="9 4!"))[0] == 400
    status, nothing = await client.call("GET", "/price-check", q="grand piano", zip="94704")
    assert status == 200 and nothing["count"] == 0 and nothing["typical_usd"] is None
    assert "brand and model" in nothing["next_step"]


async def test_a_failed_research_is_a_502_and_is_tried_again(
    client: Client, research: CountingResearch
) -> None:
    status, error = await client.call("GET", "/price-check", q="Polaroid Now instant camera")
    assert status == 502 and error["code"] == "research_failed"
    assert "fake outage" in error["error"]
    await client.call("GET", "/price-check", q="Polaroid Now instant camera")
    assert len(research.calls) == 2  # a failure isn't cached


async def test_calls_are_logged_without_the_query(
    client: Client, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="yardsale.api")
    await client.call("GET", "/price-check", q=BIKE_QUERY, zip="94704")
    lines = [r.getMessage() for r in caplog.records if r.name == "yardsale.api"]
    text = "\n".join(lines)
    assert "API GET /price-check -> 200" in text
    assert "API price check: 7 sale(s)" in text
    assert "Trek" not in text and "94704" not in text


async def test_a_slow_check_answers_running_then_done(
    store: Store, research: CountingResearch
) -> None:
    async for api, client in api_over(store, research, answer_within=0.0):
        status, first = await client.call("GET", "/price-check", q=BIKE_QUERY)
        assert status == 200 and first["status"] == "running"
        assert "same q" in first["next_step"]
        api.answer_within = 10.0
        status, later = await client.call("GET", "/price-check", q=BIKE_QUERY)
        assert status == 200 and later["status"] == "done" and later["count"] == 7
        assert research.calls == [BIKE_QUERY]  # the second call joined the first check


async def test_a_cached_check_expires(store: Store, research: CountingResearch) -> None:
    now = [1000.0]
    async for _, client in api_over(store, research, clock=lambda: now[0], cache_for=600.0):
        await client.call("GET", "/price-check", q=BIKE_QUERY)
        now[0] += 599
        await client.call("GET", "/price-check", q=BIKE_QUERY)
        assert research.calls == [BIKE_QUERY]
        now[0] += 2
        await client.call("GET", "/price-check", q=BIKE_QUERY)
        assert research.calls == [BIKE_QUERY, BIKE_QUERY]


async def test_a_scout_agent_adds_a_local_block(
    client: Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    """yardsale/scout.py is another agent's work; when it exists and has local_prices(query,
    zip_code), its answer is the local block. Here it's a stand-in module."""
    scout = types.ModuleType("yardsale.scout")
    asked: list[tuple[str, str]] = []

    async def local_prices(query: str, zip_code: str) -> dict[str, Any]:
        asked.append((query, zip_code))
        return {"count": 2, "typical_asking_usd": 250, "radius_miles": 25}

    scout.local_prices = local_prices  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "yardsale.scout", scout)
    status, check = await client.call("GET", "/price-check", q=BIKE_QUERY, zip="94704")
    assert status == 200 and check["local"] == {
        "count": 2,
        "typical_asking_usd": 250,
        "radius_miles": 25,
    }
    assert asked == [(BIKE_QUERY, "94704")]
    assert "local block" in check["next_step"]


async def test_a_broken_scout_leaves_the_block_out(
    client: Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    scout = types.ModuleType("yardsale.scout")

    async def local_prices(query: str, zip_code: str) -> dict[str, Any]:
        raise RuntimeError("no scouts nearby")

    scout.local_prices = local_prices  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "yardsale.scout", scout)
    status, check = await client.call("GET", "/price-check", q=BIKE_QUERY, zip="94704")
    assert status == 200 and check["count"] == 7 and "local" not in check


async def test_an_injected_research_can_fail_with_a_reason(store: Store) -> None:
    async def research(ctx: Context, query: str) -> list[Comp]:
        raise PipelineError("APIFY_TOKEN is not set on the server")

    async with sim.running_market(store) as market:
        api = BusinessApi(
            bureau_server.MarketBackend(bureau_server.runtime()),
            market.agent._build_context,
            KEY,
            PUBLIC,
            research=research,
        )
        async with local_server(api.app()) as base, aiohttp.ClientSession() as session:
            status, error = await Client(base, session).call("GET", "/price-check", q="bike")
    assert status == 502 and "APIFY_TOKEN" in error["error"]


def test_research_error_is_what_the_fake_raises() -> None:
    assert issubclass(ResearchError, RuntimeError)
