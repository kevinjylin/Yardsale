"""Shopping is listings-first: what a buyer asks for is searched on Yardsale and other sites,
and Yardsale's buyer agent haggles only when asked to in so many words. Sources are fakes, and
haggle requests to the Shopper are recorded, so nothing leaves the machine."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import pytest

from tests import fakes, sim
from yardsale import bureau_server, chat
from yardsale.models import HaggleResult
from yardsale.sources import ExternalListing
from yardsale.store import Listing, Store

BIKE = fakes.photo_url("bike")


class Site:
    """A fake source with a listing for whatever is searched, and the searches it got."""

    def __init__(self, source: str) -> None:
        self.source = source
        self.calls: list[str] = []

    async def __call__(self, query: str, where: str, limit: int) -> list[ExternalListing]:
        self.calls.append(query)
        await asyncio.sleep(0)
        slug = query.replace(" ", "-")
        return [
            ExternalListing(
                source=self.source,
                external_id=f"{slug}-1",
                title=f"Used {query}",
                price_cents=15000,
                url=f"https://{self.source}.example.test/{slug}",
                location="Oakland, CA",
            )
        ]


@pytest.fixture
def ebay() -> Site:
    return Site("ebay")


@pytest.fixture
def haggles(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """How many items each HaggleRequest to the Shopper named. The haggle still runs."""
    sent: list[int] = []
    real = bureau_server.haggle_for

    async def recording(
        ctx: Any, buyer_ref: str, max_cents: int, listings: list[Listing]
    ) -> HaggleResult:
        sent.append(len(listings))
        return await real(ctx, buyer_ref, max_cents, listings)

    monkeypatch.setattr(bureau_server, "haggle_for", recording)
    return sent


@pytest.fixture
async def shop(store: Store, ebay: Site) -> AsyncIterator[sim.Market]:
    async with sim.running_market(store, sources={"ebay": ebay}) as running:
        yield running


async def list_bike(market: sim.Market, asking: int = 360, lowest: int = 300) -> None:
    seller = market.user("seller")
    await seller.say("sell this", BIKE)
    await seller.say(f"ask ${asking}")
    await seller.say(f"lowest ${lowest}")
    assert "agent1q" in await seller.say("approve")


@pytest.mark.parametrize(
    ("text", "query"),
    [
        ("find me the best deal on Jordan 4s under $200", "jordan 4s"),
        ("best price on a hydro flask", "hydro flask"),
        ("The buyer wants Jordans under $200", "jordan"),  # ASI:One's planner, relaying
    ],
)
async def test_shopping_words_search_other_sites(
    shop: sim.Market, ebay: Site, haggles: list[int], text: str, query: str
) -> None:
    reply = await shop.user("buyer").say(text)
    assert reply.startswith("Found it:") and "Nothing on Yardsale matches" in reply
    assert "Best deals on other sites" in reply
    assert f"https://ebay.example.test/{query.replace(' ', '-')}" in reply  # a direct link
    assert ebay.calls == [query]
    assert "haggle" not in reply.lower().replace("can't haggle", "")
    assert haggles == []


async def test_a_good_deal_on_something_listed_shows_it_and_other_sites(
    shop: sim.Market, ebay: Site, haggles: list[int], store: Store
) -> None:
    await list_bike(shop)
    buyer = shop.user("buyer")
    reply = await buyer.say("get me a good deal on a bike")
    assert reply.startswith("Here's what's for sale for \"bike\"")
    assert "Trek FX 2 Disc hybrid bike" in reply and "$360" in reply
    assert "also checking" in reply
    deals = await buyer.next_reply()
    assert "Best deals on other sites" in deals.text()
    assert ebay.calls == ["bike"]
    assert haggles == []
    assert store.deals() == []


async def test_haggling_is_opt_in_after_results(
    shop: sim.Market, haggles: list[int], store: Store
) -> None:
    await list_bike(shop)
    buyer = shop.user("buyer")
    assert "haggle with the seller on 1" in await buyer.say("find me a hybrid bike")
    await buyer.next_reply()  # the other sites' deals
    assert haggles == []
    reply = await buyer.say("haggle with the seller on 1, max $340")
    assert "haggled with 1 seller agent(s)" in reply
    assert haggles == [1]
    assert "Deal!" in await buyer.say("accept")
    assert len(store.deals()) == 1


async def test_haggling_needs_results_first(market: sim.Market, haggles: list[int]) -> None:
    await list_bike(market)
    assert await market.user("buyer").say("haggle with the seller") == chat.HAGGLE_FIRST
    assert haggles == []


async def test_a_budget_after_a_search_searches_again(
    shop: sim.Market, ebay: Site, haggles: list[int], store: Store
) -> None:
    await list_bike(shop)
    buyer = shop.user("buyer")
    await buyer.say("find me a hybrid bike")
    await buyer.next_reply()
    for budget in ("max $300", "$300"):
        reply = await buyer.say(budget)
        # The $360 bike is over the budget, so it's other sites; no offer is made.
        assert 'Nothing on Yardsale matches "hybrid bike" under $300' in reply
        assert "offer" not in reply.lower()
    assert set(ebay.calls) == {"hybrid bike"}
    assert haggles == [] and store.deals() == []


async def test_shopping_prose_never_takes_a_price_on_offer(
    shop: sim.Market, ebay: Site, haggles: list[int], store: Store
) -> None:
    await list_bike(shop)
    buyer = shop.user("buyer")
    await buyer.say("find me a hybrid bike")
    await buyer.next_reply()
    assert "Yours for" in await buyer.say("haggle with the seller, max $340")
    # The planner relays a new shopping request while that price is open: a search, not a buy.
    reply = await buyer.say("The buyer wants Jordans under $200. Please proceed with the purchase.")
    assert 'Nothing on Yardsale matches "jordan" under $200' in reply
    assert "Deal!" not in reply
    assert "Deal!" not in await buyer.say("sure, find me a hydro flask under $40")
    assert "Deal!" not in await buyer.say("ok")  # the search ended the haggle
    assert store.deals() == []
    assert haggles == [1]
    assert "jordan" in ebay.calls and "hydro flask" in ebay.calls
