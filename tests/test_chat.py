"""The ASI:One chat: test users play ASI:One and talk to the real Market over the Agent Chat
Protocol. Claude, Apify, ASI:One's model, and photo downloads are fakes (yardsale/fakes.py)."""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from uuid import uuid4

import pytest
from uagents_core.contrib.protocols.chat import (
    MetadataContent,
    Resource,
    ResourceContent,
)

from yardsale import bureau_server, chat, fakes, sim
from yardsale.models import Comp
from yardsale.store import ACTIVE, CANCELED, PROPOSED, Store

BIKE = fakes.photo_url("bike")
ADDRESS = re.compile(r"agent1q[0-9a-z]{50,}")


async def listed_bike(market: sim.Market, text: str = "sell this, at least $300") -> str:
    """A seller lists the bike (ask $360, lowest $300) and returns the item agent's address."""
    seller = market.user("seller")
    await seller.say(text, BIKE)
    await seller.say("ask $360")
    found = ADDRESS.search(await seller.say("approve"))
    assert found
    return found.group(0)


async def test_photo_becomes_a_proposal_not_a_listing(market: sim.Market, store: Store) -> None:
    seller = market.user("seller")
    await seller.send(sim.chat_message("Sell this, at least $300", BIKE))
    reply = await seller.next_reply()
    text = reply.text()
    assert "Here's my suggested listing for your Trek FX 2 Disc hybrid bike" in text
    assert "Asking price: $340 (from recent eBay sales)" in text
    assert "Lowest price you'll take: $300 (your price; buyers never see it)" in text
    assert "Based on 6 eBay sales of the same product" in text
    assert 'Reply "approve"' in text
    assert seller.acks == 1
    # The first reply in a conversation tells ASI:One that uploads are welcome.
    assert any(isinstance(c, MetadataContent) for c in reply.content)
    (proposal,) = store.owned(proposal_owner(store))
    assert proposal.status == PROPOSED and proposal.seller_id.startswith("asi1:")
    assert store.active() == []


def proposal_owner(store: Store) -> str:
    row = store._db.execute("SELECT owner FROM listings").fetchone()
    return str(row["owner"])


async def test_seller_edits_then_approves(market: sim.Market, store: Store) -> None:
    seller = market.user("seller")
    await seller.say("sell this", BIKE)
    reply = await seller.say("ask $360")
    assert "Asking price: $360 (your price; recent eBay sales suggest $340)" in reply
    reply = await seller.say("lowest 320")
    assert "Lowest price you'll take: $320 (your price" in reply
    reply = await seller.say("lowest 400")
    assert "above your asking price" in reply
    reply = await seller.say("approve")
    assert "is for sale at $360" in reply
    assert "isn't on the public Almanac" in reply  # offline: made here, registered nowhere
    (listing,) = store.active()
    assert listing.address and listing.address in reply
    assert (listing.asking_cents, listing.floor_cents) == (36000, 32000)
    assert bureau_server.runtime().agents[listing.item_id].address == listing.address
    assert "nothing waiting" in (await seller.say("approve")).lower()


async def test_cancel_lists_nothing(market: sim.Market, store: Store) -> None:
    seller = market.user("seller")
    await seller.say("sell this", BIKE)
    assert "Nothing was listed" in await seller.say("cancel")
    assert store.active() == []
    assert bureau_server.runtime().agents == {}


async def test_other_conversations_cannot_touch_a_proposal(
    market: sim.Market, store: Store
) -> None:
    seller, mallory = market.user("seller"), market.user("mallory")
    await seller.say("sell this, at least $300", BIKE)
    for text in ("approve", "cancel", "ask $1", "my listings"):
        reply = await mallory.say(text)
        assert "$300" not in reply and "Trek" not in reply
    (proposal,) = store.owned(proposal_owner(store))
    assert (proposal.status, proposal.asking_cents) == (PROPOSED, 34000)


async def test_slow_pricing_answers_first_then_sends_the_proposal(
    store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(chat, "REPLY_WITHIN", 0.05)
    release = asyncio.Event()

    async def slow_research(query: str, condition: str, max_results: int) -> list[Comp]:
        await release.wait()
        return await fakes.research(query, condition, max_results)

    async with sim.running_market(store, research=slow_research) as market:
        seller = market.user("seller")
        await seller.send(sim.chat_message("", BIKE))
        assert (await seller.next_reply()).text() == chat.STILL_WORKING
        release.set()
        assert "suggested listing" in (await seller.next_reply()).text()


async def test_a_repeated_photo_link_is_not_priced_twice(market: sim.Market, store: Store) -> None:
    # ASI:One's planner repeats earlier links when it relays the next message.
    seller = market.user("seller")
    await seller.say("Sell this", BIKE)
    reply = await seller.say(f"thanks! [bike]({BIKE})")
    assert "suggested listing" not in reply
    assert len(store.owned(proposal_owner(store))) == 1


async def test_a_photo_that_failed_can_be_sent_again(store: Store) -> None:
    attempts = 0

    async def flaky_fetch(url: str) -> bytes:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise fakes.PhotoError("I couldn't download the photo (the link returned 502).")
        return await fakes.fetch(url)

    async with sim.running_market(store, fetch=flaky_fetch) as market:
        seller = market.user("seller")
        assert "couldn't download" in await seller.say("sell", BIKE)
        assert "suggested listing" in await seller.say("sell", BIKE)


async def test_a_resent_message_is_answered_again_not_redone(
    market: sim.Market, store: Store
) -> None:
    seller = market.user("seller")
    msg = sim.chat_message("sell", BIKE)
    await seller.send(msg)
    first = (await seller.next_reply()).text()
    await seller.send(msg)
    assert (await seller.next_reply()).text() == first
    assert len(store.owned(proposal_owner(store))) == 1


async def test_no_close_sales_then_the_sellers_price(market: sim.Market, store: Store) -> None:
    seller = market.user("seller")
    reply = await seller.say("sell this", fakes.photo_url("mug"))
    assert "no recent eBay sales were close enough" in reply
    reply = await seller.say("sell it for $18")
    assert "Asking price: $18 (your price)" in reply
    assert "Lowest price you'll take: $13 (70% of the asking price" in reply
    assert "is for sale at $18" in await seller.say("approve")


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("hi, what do you do?", chat.HELP),
        ("I want to sell my bike", chat.NEEDS_PHOTO),
        ("I want to sell this, see the photo", chat.PHOTO_MISSING),
    ],
)
async def test_messages_without_a_photo(market: sim.Market, text: str, expected: str) -> None:
    assert await market.user("someone").say(text) == expected


async def test_photo_problems_are_explained(market: sim.Market) -> None:
    seller = market.user("seller")
    reply = await seller.say("sell", "https://example.com/broken.jpg")
    assert reply.startswith("I couldn't price this. I couldn't download")
    reply = await seller.say("sell", fakes.photo_url("camera"))
    assert "Apify returned 503" in reply


async def test_a_buyer_finds_haggles_and_buys(market: sim.Market, store: Store) -> None:
    address = await listed_bike(market)
    buyer = market.user("buyer")
    replies = [await buyer.say("find me a cheap hybrid bike under $400")]
    assert "1. Trek FX 2 Disc hybrid bike, medium: $360 (good)" in replies[-1]
    replies.append(await buyer.say("1"))
    assert address in replies[-1]
    replies.append(await buyer.say("offer $250"))
    assert "countered with $305" in replies[-1]
    replies.append(await buyer.say("yes"))  # a bare yes answers the counter-offer
    assert "Deal! You agreed to buy the Trek FX 2 Disc hybrid bike, medium for $305" in replies[-1]
    assert "No payment has been taken" in replies[-1]
    for reply in replies:  # the floor never reaches a buyer
        assert "$300" not in reply and "lowest" not in reply.lower()
    (listing,) = store.owned(proposal_owner(store))
    deal = store.deal_for(listing.item_id)
    assert deal is not None and deal.price_cents == 30500
    assert "Sold: Trek" in await market.users[0].say("my listings")


async def test_buyer_mistakes(market: sim.Market) -> None:
    await listed_bike(market)
    buyer = market.user("buyer")
    assert "looking for first" in await buyer.say("offer $200")
    assert "no counter-offer" in await buyer.say("accept")
    await buyer.say("bike")
    assert "more than the asking price" in await buyer.say("offer $3600")
    assert "between" in await buyer.say("offer $5,000,000")
    assert "Pick a number from 1 to 1" in await buyer.say("tell me about 4")
    assert "Nothing is for sale" in await buyer.say("find me a surfboard")


async def test_sellers_cannot_buy_their_own_item(market: sim.Market) -> None:
    await listed_bike(market)
    seller = market.users[0]
    assert "(yours)" in await seller.say("find me a bike")
    assert "your own listing" in await seller.say("offer $360 on 1")


async def test_competing_chat_buyers(market: sim.Market) -> None:
    await listed_bike(market)
    alice, bob = market.user("alice"), market.user("bob")
    for buyer in (alice, bob):
        await buyer.say("bike")
    assert "countered" in await bob.say("offer $220")
    replies = await asyncio.gather(alice.say("offer $360"), bob.say("accept"))
    assert sum("Deal!" in reply for reply in replies) == 1
    assert "no longer for sale" in await bob.say("offer $360")


async def test_unlist(market: sim.Market, store: Store) -> None:
    await listed_bike(market)
    seller, buyer = market.users[0], market.user("buyer")
    await buyer.say("bike")
    assert "no longer for sale" in await seller.say("unlist")
    assert store.active() == []
    assert "no longer for sale" in await buyer.say("offer $300")
    assert "Nothing is for sale" in await buyer.say("find me a bike")
    listing = store.get(store.owned(proposal_owner(store))[0].item_id)
    assert listing is not None and listing.status == CANCELED


async def test_restart_keeps_listings_addresses_and_conversations(tmp_path: Path) -> None:
    path = tmp_path / "listings.sqlite3"
    store = Store(path)
    async with sim.running_market(store) as market:
        address = await listed_bike(market)
        seller_session = market.users[0].session
        await market.user("other").say("sell", fakes.photo_url("espresso"))  # left proposed
    store.close()

    store = Store(path)
    async with sim.running_market(store) as market:
        restored = bureau_server.runtime().agents
        assert [agent.address for agent in restored.values()] == [address]
        assert address in await market.user("seller", session=seller_session).say("my listings")
        buyer = market.user("buyer")
        await buyer.say("bike")
        assert "countered" in await buyer.say("offer $250")
        assert [listing.status for listing in store.active()] == [ACTIVE]
    store.close()


async def test_a_photo_lost_in_a_restart_is_mentioned(market: sim.Market, store: Store) -> None:
    seller = market.user("seller")
    await seller.say("help")
    (key,) = [row["key"] for row in store._db.execute("SELECT key FROM conversations")]
    state = store.load_conversation(key)
    state["pricing"] = {BIKE: "an-earlier-boot"}
    store.save_conversation(key, state)
    reply = await seller.say("hello?")
    assert reply.startswith(chat.RESTARTED)
    assert "suggested listing" in await seller.say("sell", BIKE)  # and it can be sent again


def test_photos_are_found_in_resources_and_text() -> None:
    msg = sim.chat_message(
        "@agent1qxyz sell these https://example.com/a.png. Listing like "
        "https://www.ebay.com/itm/123 isn't a photo",
        BIKE,
    )
    msg.content.append(
        ResourceContent(
            type="resource",
            resource_id=uuid4(),
            resource=Resource(
                uri="https://example.com/clip.mp4", metadata={"mime_type": "video/mp4"}
            ),
        )
    )
    assert [p.url for p in chat.photos_in(msg)] == [BIKE, "https://example.com/a.png"]
    assert chat.clean_text(msg) == (
        "sell these Listing like https://www.ebay.com/itm/123 isn't a photo"
    )


def test_resource_link_can_be_in_metadata() -> None:
    msg = sim.chat_message()
    msg.content.append(
        ResourceContent(
            type="resource",
            resource_id=uuid4(),
            resource=Resource(
                uri="agent-storage://abc", metadata={"mime_type": "image/jpeg", "url": BIKE}
            ),
        )
    )
    assert [p.url for p in chat.photos_in(msg)] == [BIKE]


async def test_photo_link_in_the_text_is_used(market: sim.Market) -> None:
    assert "suggested listing" in await market.user("seller").say(f"sell this ![photo]({BIKE})")
