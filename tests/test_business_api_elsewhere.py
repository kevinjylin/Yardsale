"""@yardsale's API when Yardsale has nothing: searchOtherSites. Sources are fakes."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator
from typing import Any

import aiohttp
import pytest

from tests import fakes, sim
from tests.helpers import local_server
from tests.test_business_api import KEY, PUBLIC, Client
from tests.test_chat_elsewhere import Site, deal, down
from yardsale import bureau_server
from yardsale.business_api import BusinessApi, openapi_spec
from yardsale.store import Store


@pytest.fixture
def ebay() -> Site:
    return Site([deal("Giant road bike", 21000, "ebay", shipping=3000), deal("Road bike", 40000)])


async def api_over(
    store: Store, sources: dict[str, Any], answer_within: float = 25.0
) -> AsyncIterator[Client]:
    async with sim.running_market(store, sources=sources) as market:
        api = BusinessApi(
            bureau_server.MarketBackend(bureau_server.runtime()),
            market.agent._build_context,
            KEY,
            PUBLIC,
            answer_within=answer_within,
        )
        async with local_server(api.app()) as base, aiohttp.ClientSession() as session:
            yield Client(base, session)


@pytest.fixture
async def client(store: Store, ebay: Site) -> AsyncIterator[Client]:
    async for c in api_over(store, {"ebay": ebay, "offerup": down}):
        yield c


def test_the_description_has_search_other_sites() -> None:
    spec = openapi_spec(PUBLIC)
    operation = spec["paths"]["/other-sites"]["get"]
    assert operation["operationId"] == "searchOtherSites"
    assert "NOT Yardsale listings" in operation["description"]
    json.dumps(spec)


async def test_listings_point_to_other_sites_when_empty(client: Client) -> None:
    status, found = await client.call("GET", "/listings", q="road bike", max_usd=300)
    assert status == 200 and found["count"] == 0
    assert "searchOtherSites" in found["next_step"]


async def test_other_sites_returns_deals_under_the_max(client: Client, ebay: Site) -> None:
    status, found = await client.call("GET", "/other-sites", q="road bike", max_usd=300)
    assert status == 200 and found["status"] == "done" and found["count"] == 1
    (bike,) = found["listings"]
    assert bike["site"] == "eBay" and bike["title"] == "Giant road bike"
    assert bike["price_usd"] == 210 and bike["shipping_usd"] == 30 and bike["total_usd"] == 240
    assert bike["url"].startswith("https://ebay.example.test/")
    assert found["searched"] == ["eBay"] and found["failed"] == ["OfferUp"]
    assert "aren't Yardsale listings" in found["next_step"]
    await client.call("GET", "/other-sites", q="road bike", max_usd=300)
    assert ebay.calls == ["road bike"]  # cached


async def test_other_sites_explains_each_deal_against_the_market(store: Store, ebay: Site) -> None:
    async with sim.running_market(
        store, research=fakes.research_with_comps, sources={"ebay": ebay}
    ) as market:
        api = BusinessApi(
            bureau_server.MarketBackend(bureau_server.runtime()),
            market.agent._build_context,
            KEY,
            PUBLIC,
        )
        async with local_server(api.app()) as base, aiohttp.ClientSession() as session:
            client = Client(base, session)
            status, found = await client.call("GET", "/other-sites", q="road bike", max_usd=300)
    assert status == 200 and found["typical_sold_usd"] == 310 and found["comps_used"] == 4
    assert "typical sold price on eBay, $310" in found["summary"]
    (bike,) = found["listings"]
    assert bike["versus_market_pct"] == 23 and bike["flags"] == []
    assert bike["reason"] == "$210 + $30 shipping on eBay, 23% under the $310 typical sold price"
    assert isinstance(bike["score"], float)
    assert "each one's reason" in found["next_step"]


async def test_other_sites_checks_its_input(client: Client) -> None:
    assert (await client.call("GET", "/other-sites", key=None, q="bike"))[0] == 401
    assert (await client.call("GET", "/other-sites", q="please"))[0] == 400
    assert (await client.call("GET", "/other-sites", q="bike", max_usd="lots"))[0] == 400


async def test_a_slow_search_answers_running_then_done(store: Store, ebay: Site) -> None:
    ebay.delay = 0.5
    async for client in api_over(store, {"ebay": ebay}, answer_within=0.1):
        status, first = await client.call("GET", "/other-sites", q="road bike", max_usd=300)
        assert status == 200 and first["status"] == "running"
        assert "same q and max_usd" in first["next_step"]
        ebay.delay = 0
        client_wait = 0
        while client_wait < 20:
            status, later = await client.call("GET", "/other-sites", q="road bike", max_usd=300)
            if later["status"] == "done":
                break
            client_wait += 1
        assert later["status"] == "done" and later["count"] == 1
        assert ebay.calls == ["road bike"]  # the second call joined the first search


async def test_a_slow_site_is_pending_not_running(store: Store, ebay: Site) -> None:
    """With per-site deadlines the API answers done with what's in, naming the slow sites,
    and the next call picks up what came in since without searching again."""
    craigslist = Site([deal("Trek road bike 54cm", 18000)], delay=1.0)
    async for client in api_over(store, {"ebay": ebay, "craigslist": craigslist}, 5.0):
        bureau_server.runtime().finder.within = 0.2
        started = time.monotonic()
        status, first = await client.call("GET", "/other-sites", q="road bike", max_usd=300)
        assert time.monotonic() - started < 0.8
        assert status == 200 and first["status"] == "done"
        assert first["searched"] == ["eBay"] and first["pending"] == ["Craigslist"]
        assert [x["title"] for x in first["listings"]] == ["Giant road bike"]
        assert "Craigslist still searching" in first["summary"]
        assert "call searchOtherSites again" in first["next_step"]
        await asyncio.sleep(1.0)
        status, later = await client.call("GET", "/other-sites", q="road bike", max_usd=300)
        assert later["pending"] == [] and later["searched"] == ["eBay", "Craigslist"]
        assert later["listings"][0]["title"] == "Trek road bike 54cm"
    assert ebay.calls == ["road bike"] and craigslist.calls == ["road bike"]


async def test_off_without_sources(store: Store, caplog: pytest.LogCaptureFixture) -> None:
    async for client in api_over(store, {}):
        with caplog.at_level(logging.INFO):
            status, body = await client.call("GET", "/other-sites", q="zebra bike", max_usd=277)
        assert status == 503 and body["code"] == "sources_off"
        # Yardsale's own lines; the test server's access log isn't the API's (serve() has none).
        ours = " ".join(r.getMessage() for r in caplog.records if r.name.startswith("yardsale"))
        assert "/other-sites" in ours
        assert "zebra" not in ours and "277" not in ours
