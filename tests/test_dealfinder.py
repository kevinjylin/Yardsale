"""The Deal Finder agent: an agent's FindDeals -> DealsFound round trip, the message
conversions, and the finder the agent shares with the chat and the API. Sources and comps are
fakes; nothing is paid."""

from __future__ import annotations

import dataclasses
import logging

import pytest

from tests import fakes, sim
from tests.helpers import Peer
from tests.test_chat_elsewhere import Site, deal, down
from yardsale import bureau_server
from yardsale.dealfinder import deals_found, found_from
from yardsale.deals import MarketPrice, ScoredDeal
from yardsale.elsewhere import DealFinder, Found
from yardsale.models import DealsFound, FindDeals, ServiceError
from yardsale.store import Store


@pytest.fixture
def sources() -> dict[str, Site]:
    return {
        "ebay": Site([deal("Giant road bike", 21000, "ebay", shipping=3000)]),
        "craigslist": Site([deal("Trek road bike 54cm", 18000), deal("Road bike helmet", 2500)]),
    }


async def test_the_market_gets_scored_deals_back(store: Store, sources: dict[str, Site]) -> None:
    async with sim.running_market(
        store, research=fakes.research_with_comps, sources={**sources, "offerup": down}
    ):
        finder_address = bureau_server.runtime().services.dealfinder
        assert finder_address
        buyer = Peer("buyer", DealsFound, ServiceError)
        try:
            request = FindDeals(request_id="r1", query="find me a road bike", max_cents=30000)
            found = await buyer.ask(finder_address, request, DealsFound)
        finally:
            await sim.stop([buyer.agent])
    assert found.request_id == "r1" and found.query == "road bike" and found.max_cents == 30000
    assert found.typical_cents == 31000 and found.comps_used == 4
    assert found.searched == ["ebay", "craigslist"] and found.failed == ["offerup"]
    # The $25 helmet is an accessory far under the $310 market: dropped, not shown.
    assert [d.title for d in found.deals] == ["Trek road bike 54cm", "Giant road bike"]
    trek, giant = found.deals
    assert trek.versus_market_pct == 42 and trek.why.startswith("42% under the $310 typical")
    assert giant.total_cents == 24000 and giant.shipping_cents == 3000
    assert trek.score > giant.score and found.considered == 3
    assert sources["ebay"].calls == ["road bike"]  # one paid search


async def test_a_search_with_nothing_to_look_for_is_a_service_error(
    store: Store, sources: dict[str, Site]
) -> None:
    async with sim.running_market(store, sources=sources):
        finder_address = bureau_server.runtime().services.dealfinder
        buyer = Peer("buyer", DealsFound, ServiceError)
        try:
            request = FindDeals(request_id="r2", query="search other sites", max_cents=None)
            error = await buyer.ask(finder_address, request, ServiceError)
        finally:
            await sim.stop([buyer.agent])
    assert error.request_id == "r2" and "tell me what to look for" in error.reason
    assert sources["ebay"].calls == []


async def test_the_agent_log_has_no_search_words_or_max(
    store: Store, sources: dict[str, Site], caplog: pytest.LogCaptureFixture
) -> None:
    async with sim.running_market(store, research=fakes.research_with_comps, sources=sources):
        finder_address = bureau_server.runtime().services.dealfinder
        buyer = Peer("buyer", DealsFound, ServiceError)
        try:
            with caplog.at_level(logging.DEBUG):
                request = FindDeals(request_id="r3", query="zebra road bike", max_cents=27700)
                await buyer.ask(finder_address, request, DealsFound)
        finally:
            await sim.stop([buyer.agent])
    assert caplog.records
    assert "zebra" not in caplog.text and "277" not in caplog.text


def test_messages_carry_everything_the_finder_found() -> None:
    market = MarketPrice(31000, 28000, 34000, 4, "4 eBay sales of the same thing")
    listing = deal("Trek road bike 54cm", 18000)
    scored = ScoredDeal(listing, 18000, 45.0, 42, "42% under the $310 typical sold price", ())
    found = Found("road bike", 30000, (scored,), ("craigslist",), ("offerup",), 7, market)

    message = deals_found("r9", found)
    assert message.request_id == "r9" and message.typical_cents == 31000
    back = found_from(message)
    assert back.query == "road bike" and back.budget_cents == 30000 and back.considered == 7
    assert back.searched == ("craigslist",) and back.failed == ("offerup",)
    assert back.typical_cents == 31000 and back.market is not None and back.market.sales == 4
    (again,) = back.deals
    assert again.listing == listing and again.score == 45.0 and again.versus_market_pct == 42
    assert again.why == scored.why and back.listings == (listing,)

    without = found_from(deals_found("r10", Found("road bike", None, (), ("ebay",), (), 0)))
    assert without.market is None and without.deals == ()


def test_an_unpriced_or_over_budget_deal_stays_so_through_the_message() -> None:
    no_price = dataclasses.replace(deal("Trek road bike", 0), price_cents=None)
    unpriced = ScoredDeal(no_price, 0, 5.0, None, "", ("no_price",))
    over = ScoredDeal(deal("Giant road bike", 33000), 33000, 9.0, None, "", ("over_budget",))
    found = Found("road bike", 30000, (over, unpriced), ("craigslist",), (), 2)
    back = found_from(deals_found("r11", found))
    assert [d.flags for d in back.deals] == [("over_budget",), ("no_price",)]
    assert back.deals[0].listing.price_cents == 33000
    assert back.deals[1].listing.price_cents is None


async def test_the_market_searches_in_process_with_the_agents_finder(
    store: Store, sources: dict[str, Site]
) -> None:
    """The chat and the API don't wait on the agent's one reply; they share its finder, so a
    search the agent ran is a cache hit for the chat, and the other way round."""
    async with sim.running_market(store, sources=sources):
        rt = bureau_server.runtime()
        assert isinstance(rt.finder, DealFinder)
        buyer = Peer("buyer", DealsFound, ServiceError)
        try:
            request = FindDeals(request_id="r4", query="road bike", max_cents=30000)
            await buyer.ask(rt.services.dealfinder, request, DealsFound)
        finally:
            await sim.stop([buyer.agent])
        found = await rt.finder.find("bike road", 30000)  # the same words, in another order
    assert found.searched == ("ebay", "craigslist")
    assert sources["ebay"].calls == ["road bike"]  # one paid search for both
