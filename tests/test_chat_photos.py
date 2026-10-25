"""Several photos in one ASI:One message: one numbered proposal per photo, priced a few at a time,
then approved, priced, or canceled by number. Claude, Apify, and downloads are fakes."""

from __future__ import annotations

import asyncio
import re
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest
from uagents_core.contrib.protocols.chat import (
    ChatMessage,
    Resource,
    ResourceContent,
    TextContent,
)

from tests import fakes, sim
from yardsale import chat
from yardsale.models import Comp
from yardsale.store import ACTIVE, CANCELED, PROPOSED, Store

ADDRESS = re.compile(r"agent1q[0-9a-z]{50,}")
DONE = "photos are done"


def photos_message(text: str, *names: str) -> ChatMessage:
    """One message with several uploads, the way the ASI:One app sends them."""
    content: list[Any] = [TextContent(type="text", text=text)] if text else []
    for name in names:
        resource = Resource(uri=fakes.storage_uri(name), metadata={"mime_type": "image/jpeg"})
        content.append(ResourceContent(type="resource", resource_id=uuid4(), resource=resource))
    return ChatMessage(timestamp=datetime.now(UTC), msg_id=uuid4(), content=content)


async def replies_until_done(user: sim.ChatUser) -> list[ChatMessage]:
    """Every message the Market sends for a batch, through the closing summary."""
    replies = [await user.next_reply()]
    while DONE not in replies[-1].text():
        replies.append(await user.next_reply())
    return replies


def owned(store: Store, *statuses: str) -> list[Any]:
    row = store._db.execute("SELECT owner FROM listings").fetchone()
    return store.owned(str(row["owner"]), *statuses) if row else []


def approve_card(msg: ChatMessage) -> dict[str, Any] | None:
    try:
        return sim.button(msg, "approve")
    except AssertionError:
        return None


async def test_several_photos_become_numbered_proposals(market: sim.Market, store: Store) -> None:
    seller = market.user("seller")
    await seller.send(
        photos_message("selling all of this before finals", "bike", "espresso", "mug")
    )
    replies = await replies_until_done(seller)
    texts = [reply.text() for reply in replies]
    assert texts[0].startswith("Here are suggested listings for your 3 photos")
    assert texts[1].startswith("#1: Here's my suggested listing for your Trek FX 2 Disc")
    assert 'reply "approve 1"' in texts[1] and approve_card(replies[1]) is not None
    assert texts[2].startswith("#2: Here's my suggested listing for your Breville Bambino")
    assert texts[3].startswith("#3: This looks like Handmade speckled ceramic mug")
    assert '"ask 3 $40"' in texts[3]
    assert "1. Trek FX 2 Disc hybrid bike, medium: asking $340" in texts[4]
    assert "3. Handmade speckled ceramic mug: needs your price" in texts[4]
    assert "approve all" in texts[4]
    assert len(owned(store, PROPOSED)) == 2 and store.active() == []  # nothing for sale yet

    which = await seller.say("approve")  # several waiting: it asks rather than guess
    assert which.startswith('Which one? Say "approve 3", or "approve all"')
    assert store.active() == []
    reply = await seller.say("ask 3 $25")
    assert reply.startswith("#3: Here's my suggested listing for your Handmade speckled")
    assert "Asking price: $25 (your price)" in reply
    reply = await seller.say("lowest 2 $200")
    assert reply.startswith("#2:") and "Lowest price you'll take: $200 (your price" in reply

    reply = await seller.say("approve all")
    assert len(ADDRESS.findall(reply)) == 3
    for line in ("#1 Trek FX 2 Disc", "#2 Breville Bambino Plus", "#3 Handmade speckled"):
        assert line in reply
    assert sorted(listing.asking_cents for listing in store.active()) == [2500, 27000, 34000]
    espresso = next(listing for listing in store.active() if "Breville" in listing.title)
    assert espresso.floor_cents == 20000
    assert "nothing waiting" in (await seller.say("approve")).lower()


async def test_a_price_in_a_message_with_several_photos_is_not_used(
    market: sim.Market, store: Store
) -> None:
    seller = market.user("seller")
    await seller.send(photos_message("sell these, at least $300", "bike", "espresso"))
    first = (await replies_until_done(seller))[0].text()
    assert "didn't use the price in your message" in first
    assert all(listing.seller_floor_cents is None for listing in owned(store, PROPOSED))


async def test_approve_and_cancel_by_number(market: sim.Market, store: Store) -> None:
    seller = market.user("seller")
    await seller.send(photos_message("", "bike", "espresso"))
    await replies_until_done(seller)
    assert "Pick a number from 1 to 2" in await seller.say("approve 5")
    reply = await seller.say("cancel 1")
    assert reply.startswith("Canceled #1, the Trek FX 2 Disc hybrid bike, medium.")
    assert "Your Breville Bambino Plus espresso machine is still waiting" in reply
    assert "#1 isn't waiting for approval" in await seller.say("approve 1")
    # One proposal left: a plain "approve" works as it always has.
    assert "Your Breville Bambino Plus espresso machine is for sale" in await seller.say("approve")
    assert [listing.status for listing in owned(store)] == [ACTIVE, CANCELED]


async def test_card_taps_and_several_numbers(market: sim.Market, store: Store) -> None:
    seller = market.user("seller")
    await seller.send(photos_message("", "bike", "espresso", "bike-2"))
    replies = await replies_until_done(seller)
    tap = approve_card(replies[2])
    assert tap is not None
    reply = await seller.tap(tap)
    assert "Breville Bambino Plus espresso machine is for sale" in reply
    assert "Still waiting for you:\n1. Trek" in reply and "\n3. Trek" in reply
    reply = await seller.say("approve 1 and 3")
    assert len(ADDRESS.findall(reply)) == 2
    assert len(store.active()) == 3


async def test_a_repeated_batch_link_is_not_priced_again(market: sim.Market, store: Store) -> None:
    seller = market.user("seller")
    await seller.send(photos_message("", "bike", "espresso"))
    await replies_until_done(seller)
    reply = await seller.say("thanks", fakes.storage_uri("espresso"))
    assert "suggested listing" not in reply
    assert len(owned(store, PROPOSED)) == 2


async def test_too_many_photos(market: sim.Market, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(chat, "MAX_PHOTOS", 2)
    seller = market.user("seller")
    await seller.send(photos_message("", "bike", "espresso", "mug"))
    replies = await replies_until_done(seller)
    assert "so I took the first 2. Attach the other 1 in another message" in replies[0].text()
    assert "All 2 photos are done" in replies[-1].text()


async def test_a_failed_photo_is_reported_and_can_be_sent_again(market: sim.Market) -> None:
    seller = market.user("seller")
    await seller.send(photos_message("", "bike", "camera"))
    texts = [reply.text() for reply in await replies_until_done(seller)]
    assert any(t.startswith("#2: I couldn't price this photo.") for t in texts)
    assert "2. Couldn't be priced" in texts[-1]
    assert "Apify returned 503" in await seller.say("sell", fakes.storage_uri("camera"))


async def test_slow_photos_follow_the_first_reply(
    store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(chat, "REPLY_WITHIN", 0.5)
    monkeypatch.setattr(chat, "PHOTOS_AT_ONCE", 2)
    release = asyncio.Event()
    running = most = 0

    async def research(query: str, condition: str, max_results: int) -> list[Comp]:
        nonlocal running, most
        running += 1
        most = max(most, running)
        try:
            if "Breville" in query:
                await release.wait()
            else:
                await asyncio.sleep(0.02)
            return await fakes.research(query, condition, max_results)
        finally:
            running -= 1

    async with sim.running_market(store, research=research) as market:
        seller = market.user("seller")
        await seller.send(photos_message("", "bike", "espresso", "bike-2", "bike-3"))
        first = await seller.next_reply()
        assert first.text().startswith("Got your 4 photos. Here's what's priced so far (3 of 4)")
        ready = [(await seller.next_reply()).text() for _ in range(3)]
        assert [text[:3] for text in ready] == ["#1:", "#3:", "#4:"]
        release.set()
        assert (await seller.next_reply()).text().startswith("#2: Here's my suggested listing")
        assert "All 4 photos are done" in (await seller.next_reply()).text()
    assert most == 2  # never more than PHOTOS_AT_ONCE photos at a time
