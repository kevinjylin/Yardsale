"""Buying in the chat when Yardsale has nothing that fits: the best deals on other sites, and
watching them. Sources are fakes, so nothing reaches Apify or spends anything."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator

import pytest
from uagents_core.contrib.protocols.chat import ChatMessage, MetadataContent
from uagents_core.contrib.protocols.chat.cards import Card, CarouselCardPayload, extract_card

from tests import fakes, sim
from yardsale import chat, commands
from yardsale.sources import ExternalListing, SourceError
from yardsale.store import Store

BIKE = fakes.photo_url("bike")


def deal(
    title: str, cents: int, source: str = "craigslist", shipping: int | None = None
) -> ExternalListing:
    return ExternalListing(
        source=source,
        external_id=title.replace(" ", "-"),
        title=title,
        price_cents=cents,
        url=f"https://{source}.example.test/{title.replace(' ', '-')}",
        location="Oakland, CA",
        shipping_cents=shipping,
    )


class Site:
    """A fake source: fixed listings, a call count, and an optional delay."""

    def __init__(self, listings: list[ExternalListing], delay: float = 0.0) -> None:
        self.listings = listings
        self.delay = delay
        self.calls: list[str] = []

    async def __call__(self, query: str, where: str, limit: int) -> list[ExternalListing]:
        self.calls.append(query)
        await asyncio.sleep(self.delay)
        return self.listings


async def down(query: str, where: str, limit: int) -> list[ExternalListing]:
    raise SourceError("OfferUp is down")


def card_in(msg: ChatMessage) -> Card | None:
    for block in msg.content:
        if isinstance(block, MetadataContent) and (card := extract_card(block)) is not None:
            return card
    return None


@pytest.fixture
def ebay() -> Site:
    return Site([deal("Giant road bike", 21000, "ebay", shipping=3000), deal("Road bike", 40000)])


@pytest.fixture
def craigslist() -> Site:
    return Site([deal("Trek road bike 54cm", 18000), deal("Road bike helmet", 2500)])


@pytest.fixture
async def shop(store: Store, ebay: Site, craigslist: Site) -> AsyncIterator[sim.Market]:
    sources = {"ebay": ebay, "craigslist": craigslist, "offerup": down}
    async with sim.running_market(store, sources=sources) as running:
        yield running


async def list_bike(market: sim.Market, asking: int, lowest: int) -> None:
    seller = market.user("seller")
    await seller.say("sell this", BIKE)
    await seller.say(f"ask ${asking}")
    await seller.say(f"lowest ${lowest}")
    assert "agent1q" in await seller.say("approve")


async def test_nothing_on_yardsale_shows_the_best_deals_elsewhere(
    shop: sim.Market, ebay: Site
) -> None:
    buyer = shop.user("buyer")
    reply = await buyer.exchange(sim.chat_message("find me a road bike under $300"))
    text = reply.text()
    assert 'Nothing on Yardsale matches "road bike" under $300' in text
    assert "Best deals on other sites" in text
    lines = text.splitlines()
    first = next(n for n, line in enumerate(lines) if line.startswith("1. "))
    # Best match, then cheapest total: $180 on Craigslist, then $210 + $30 shipping on eBay.
    assert "[Trek road bike 54cm](https://craigslist.example.test/" in lines[first]
    assert "$180 on Craigslist" in lines[first]
    assert "$210 + $30 shipping on eBay" in lines[first + 1]
    assert "Road bike helmet" in lines[first + 2]  # a part: last
    assert "$400" not in text  # over the max
    assert chat.NOT_YARDSALE in text and chat.CRAIGSLIST_OFFERS in text
    assert "OfferUp couldn't be searched" in text
    assert 'Say "watch for it"' in text
    card = card_in(reply)
    assert card is not None and isinstance(card.payload, CarouselCardPayload)
    assert card.payload.title == "On other sites" and len(card.payload.items) == 3
    assert ebay.calls == ["road bike"]


async def test_deals_are_scored_against_what_the_item_sells_for(
    store: Store, ebay: Site, craigslist: Site
) -> None:
    sources = {"ebay": ebay, "craigslist": craigslist}
    async with sim.running_market(
        store, research=fakes.research_with_comps, sources=sources
    ) as market:
        buyer = market.user("buyer")
        reply = await buyer.exchange(sim.chat_message("find me a road bike under $300"))
    text = reply.text()
    assert "(typical sold price on eBay: $310, from 4 sales):" in text
    lines = text.splitlines()
    first = next(n for n, line in enumerate(lines) if line.startswith("1. "))
    assert "$180 on Craigslist, 42% under the $310 typical sold price (Oakland, CA)" in lines[first]
    assert "$210 + $30 shipping on eBay, 23% under the $310 typical sold price" in lines[first + 1]
    assert "looks like an accessory" in lines[first + 2]
    card = card_in(reply)
    assert card is not None and isinstance(card.payload, CarouselCardPayload)
    assert card.payload.items[0].subtitle == "$180, 42% under typical"
    assert card.payload.items[1].subtitle == "$240 with shipping, 23% under typical"
    assert ebay.calls == ["road bike"]


async def test_yardsale_listings_come_first(shop: sim.Market, ebay: Site) -> None:
    await list_bike(shop, 360, 300)
    reply = await shop.user("buyer").say("find me a hybrid bike")
    assert "Here's what's for sale" in reply and "other sites" not in reply
    assert ebay.calls == []  # nothing paid for


async def test_without_sources_nothing_is_searched_elsewhere(store: Store) -> None:
    async with sim.running_market(store) as market:
        buyer = market.user("buyer")
        assert "Nothing is for sale" in await buyer.say("find me a road bike under $300")
        assert chat.SITES_OFF in await buyer.say("search other sites")
        assert chat.SITES_OFF in await buyer.say("watch for it")


async def test_asking_again_uses_the_cached_search(shop: sim.Market, ebay: Site) -> None:
    buyer = shop.user("buyer")
    await buyer.say("find me a road bike under $300")
    again = await buyer.say("search other sites")  # the last search's words and max
    assert 'Checking eBay, Craigslist, and OfferUp for "road bike" under $300' in again
    assert "Trek road bike 54cm" in again
    other = await shop.user("other").say("search ebay and craigslist for a road bike under $300")
    assert "Trek road bike 54cm" in other
    assert ebay.calls == ["road bike"]  # one paid search for all three


async def test_a_slow_search_says_so_first(
    shop: sim.Market, ebay: Site, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(chat, "REPLY_WITHIN", 0.2)
    ebay.delay = 0.6
    buyer = shop.user("buyer")
    reply = await buyer.say("find me a road bike under $300")
    (first,) = buyer.interim
    assert first.startswith('Nothing on Yardsale matches "road bike"')
    assert first.endswith(chat.SEARCHING_ELSEWHERE)
    assert "Best deals on other sites" in reply


async def test_nothing_to_haggle_over_looks_elsewhere(shop: sim.Market) -> None:
    reply = await shop.user("buyer").say("find me a road bike, haggle for me, max $300")
    assert "Nothing on Yardsale is close enough to haggle over" in reply
    assert "Trek road bike 54cm" in reply


async def test_watch_list_news_and_stop(shop: sim.Market, store: Store) -> None:
    buyer = shop.user("buyer")
    await buyer.say("find me a road bike under $300")
    started = await buyer.say("watch for it")
    assert 'I\'ll check eBay, Craigslist, and OfferUp for "road bike" under $300' in started
    (watch,) = store.due_watches()
    assert watch.query == "road bike" and watch.budget_cents == 30000
    assert watch.owner.startswith("asi1:")

    listed = await buyer.say("my watches")
    assert '1. "road bike" under $300, for 7 more day(s)' in listed
    assert "Nothing new has turned up yet" in listed

    # A poll finds something; the buyer hears about it next time they say anything.
    store.record_hits(watch.watch_id, [deal("Cannondale road bike", 25000)])
    nudge = await buyer.say("help")
    assert "Your watches found 1 new listing(s)" in nudge
    news = await buyer.say("my watches")
    assert "Cannondale road bike" in news and chat.NOT_YARDSALE in news
    assert "Your watches found" not in await buyer.say("help")  # told once

    assert 'stopped watching for "road bike"' in await buyer.say("stop watching 1")
    assert store.watches_for(watch.owner) == []
    assert "aren't watching anything" in await buyer.say("my watches")


async def test_watch_with_its_own_words_and_the_card_button(shop: sim.Market, store: Store) -> None:
    buyer = shop.user("buyer")
    reply = await buyer.say("watch for a standing desk under $150")
    assert '"standing desk" under $150' in reply
    await buyer.say("find me a road bike under $300")
    assert 'for "road bike"' in await buyer.tap({"action": "watch"})
    queries = sorted(w.query for w in store.due_watches())
    assert queries == ["road bike", "standing desk"]
    assert "Which one?" in await buyer.say("stop watching")


async def test_watching_needs_something_to_watch(shop: sim.Market) -> None:
    reply = await shop.user("buyer").say("watch for it")
    assert "Tell me what to watch for" in reply


async def test_the_log_has_no_search_words_or_max(
    shop: sim.Market, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.DEBUG):
        await shop.user("buyer").say("find me a zebra bike under $277")
    assert "zebra" not in caplog.text and "277" not in caplog.text


@pytest.mark.parametrize(
    ("text", "kind", "pick", "budget"),
    [
        ("search other sites", "elsewhere", None, None),
        ("check ebay and craigslist", "elsewhere", None, None),
        ("search ebay and craigslist for a road bike under $300", "elsewhere", None, 30000),
        ("try facebook marketplace", "elsewhere", None, None),
        ("watch for it", "watch", None, None),
        ("keep looking", "watch", None, None),
        ("watch for a road bike under $250", "watch", None, 25000),
        ("my watches", "watches", None, None),
        ("what am i watching", "watches", None, None),
        ("stop watching 2", "unwatch", 2, None),
        ("cancel the watch", "unwatch", None, None),
        ("unwatch 1", "unwatch", 1, None),
    ],
)
def test_other_sites_commands(text: str, kind: str, pick: int | None, budget: int | None) -> None:
    command = commands.parse(text)
    assert command is not None
    assert (command.kind, command.pick, command.amount_cents) == (kind, pick, budget)


@pytest.mark.parametrize(
    "text",
    ["can you list it on other sites", "check my ebay listings", "list it on ebay", "stop"],
)
def test_selling_isnt_searching_other_sites(text: str) -> None:
    command = commands.parse(text)
    assert command is None or command.kind not in ("elsewhere", "watch", "unwatch")
