"""The ASI:One chat: test users play ASI:One and talk to the real Market over the Agent Chat
Protocol. Claude, Apify, ASI:One's model, and photo downloads are fakes (tests/fakes.py)."""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from uagents import Context
from uagents_core.contrib.protocols.chat import (
    ChatMessage,
    MetadataContent,
    Resource,
    ResourceContent,
)

from tests import fakes, sim
from yardsale import bureau_server, chat, photos
from yardsale.card_schema import Card, CarouselCardPayload, extract_card
from yardsale.models import Comp
from yardsale.store import ACTIVE, CANCELED, PROPOSED, Store

BIKE = fakes.photo_url("bike")
UPLOADS = {"attachments": "true"}
ADDRESS = re.compile(r"agent1q[0-9a-z]{50,}")
COUNTERED = re.compile(r"countered with \$([\d,]+)")


def countered_at(reply: str) -> int:
    """The counter-offer in a chat reply, in cents."""
    found = COUNTERED.search(reply)
    assert found, reply
    return int(found.group(1).replace(",", "")) * 100


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
    text = sim.shown(reply)
    assert "Here's my suggested listing for your Trek FX 2 Disc hybrid bike" in text
    assert "Asking price: $340 (from recent eBay sales)" in text
    assert "Lowest price you'll take: $300 (your price; buyers never see it)" in text
    assert "Based on 6 eBay sales of the same product" in text
    assert 'reply "approve"' in text and sim.button(reply, "approve")
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

    async def flaky_fetch(ctx: Context, photo: chat.Photo) -> bytes:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise fakes.PhotoError("I couldn't download the photo (the link returned 502).")
        return await fakes.fetch(ctx, photo)

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
    first = await seller.next_reply()  # the proposal and its sales, in one message
    assert len(sim.cards_of(first)) == 2
    await seller.send(msg)
    again = await seller.next_reply()
    # The same words, but not the cards again: ASI:One showed resent cards twice.
    assert again.text() == first.text()
    assert sim.cards_of(again) == []
    assert seller.replies.empty()
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
    reply = await market.user("someone").exchange(sim.chat_message(text))
    assert reply.text() == expected
    assert sim.card_of(reply) is not None  # every reply is a card


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
    first = countered_at(replies[-1])
    assert 30000 < first < 36000
    replies.append(await buyer.say("offer $270"))
    second = countered_at(replies[-1])
    assert 30000 < second < first  # comes down, but never to the floor
    replies.append(await buyer.say("yes"))  # a bare yes answers the counter-offer
    price = f"${second // 100}"
    assert f"Deal! The Trek FX 2 Disc hybrid bike, medium is yours for {price}" in replies[-1]
    assert "pay the seller in person" in replies[-1]
    for reply in replies:  # the floor never reaches a buyer
        assert "$300" not in reply and "lowest" not in reply.lower()
    (listing,) = store.owned(proposal_owner(store))
    deal = store.deal_for(listing.item_id)
    assert deal is not None and deal.price_cents == second
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
    assert "agreed to $360" in await alice.say("offer $360")
    replies = await asyncio.gather(alice.say("accept"), bob.say("accept"))
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


def card_in(msg: ChatMessage) -> Card | None:
    for block in msg.content:
        if isinstance(block, MetadataContent) and (card := extract_card(block)) is not None:
            return card
    return None


async def test_proposals_come_with_an_approve_card(market: sim.Market, store: Store) -> None:
    seller = market.user("seller")
    reply = await seller.exchange(sim.chat_message("sell this, at least $300", BIKE))
    (proposal,) = store.owned(proposal_owner(store))
    approve = sim.button(reply, "approve")
    assert approve == {"action": "approve", "item_id": proposal.item_id}
    assert "Asking price: $340" in sim.shown(reply)
    assert {"action": "edit", "item_id": proposal.item_id} == sim.button(reply, "edit")
    assert sim.button(reply, "menu") == {"action": "menu"}

    # Another conversation tapping the same button gets nowhere.
    assert "isn't waiting" in await market.user("mallory").tap(approve)
    assert store.active() == []
    assert "is for sale at $340" in await seller.tap(approve)
    assert store.active()[0].item_id == proposal.item_id
    assert "isn't waiting" in await seller.tap(approve)


async def test_search_and_counter_cards(market: sim.Market, store: Store) -> None:
    await listed_bike(market)
    buyer = market.user("buyer")
    results = await buyer.exchange(sim.chat_message("find me a bike"))
    carousel = card_in(results)
    assert carousel is not None and isinstance(carousel.payload, CarouselCardPayload)
    item, *others = carousel.payload.items
    assert [other.id for other in others] == ["haggle"]
    menu = carousel.payload.root_cta  # Main menu is the button under the carousel
    assert menu is not None and menu.selection == {"action": "menu"}
    assert "$300" not in carousel.payload.model_dump_json()  # no floor in cards either
    assert "Its agent:" in await buyer.tap(item.primary_cta.selection)

    countered = await buyer.exchange(sim.chat_message("offer $250"))
    accept = sim.button(countered, "accept")
    assert accept["cents"] == countered_at(countered.text())
    assert "isn't open anymore" in await buyer.tap({**accept, "cents": 30000})  # tampered
    assert "isn't open anymore" in await market.user("mallory").tap(accept)
    assert "Deal!" in await buyer.tap(accept)
    deal = store.deal_for(item.id)
    assert deal is not None and deal.price_cents == accept["cents"]


async def test_dismissed_cards_and_json_taps(market: sim.Market, store: Store) -> None:
    seller = market.user("seller")
    await seller.say("sell this", BIKE)
    assert (await seller.tap(None)).startswith("OK.")
    (proposal,) = store.owned(proposal_owner(store))
    # Some clients send the selection as JSON text instead (cards README).
    tapped = f'{{"action": "cancel", "item_id": "{proposal.item_id}"}}'
    assert "Nothing was listed" in await seller.say(tapped)


# --- ASI:One uploads and session starts ----------------------------------------------------------


async def test_an_upload_in_agentverse_storage_is_priced(market: sim.Market) -> None:
    seller = market.user("seller")
    reply = await seller.say("sell this", fakes.storage_uri("bike"))
    assert "suggested listing for your Trek FX 2 Disc" in reply


async def test_a_session_start_gets_the_uploads_welcome_and_no_text(market: sim.Market) -> None:
    # ASI:One opens a session with a StartSessionContent-only message. Fetch's examples answer
    # it with the attachments metadata and nothing else.
    seller = market.user("seller")
    await seller.send(sim.chat_message(start_session=True))
    reply = await seller.next_reply()
    assert [type(c) for c in reply.content] == [MetadataContent]
    assert reply.content[0].metadata == {"attachments": "true"}


async def test_a_session_start_with_a_photo_is_priced_in_one_message(market: sim.Market) -> None:
    seller = market.user("seller")
    await seller.send(sim.chat_message("sell this", fakes.storage_uri("bike"), start_session=True))
    proposal = await seller.next_reply()
    assert "suggested listing" in proposal.text()
    # The answer offers uploads itself, rather than a second message doing it.
    uploads = [c for c in proposal.content if getattr(c, "metadata", None) == UPLOADS]
    assert len(uploads) == 1
    await asyncio.sleep(0.2)
    assert seller.replies.empty()


async def test_a_repeated_photo_still_being_priced_says_so(
    store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(chat, "REPLY_WITHIN", 0.05)
    release = asyncio.Event()

    async def slow_research(query: str, condition: str, max_results: int) -> list[Comp]:
        await release.wait()
        return await fakes.research(query, condition, max_results)

    async with sim.running_market(store, research=slow_research) as market:
        seller = market.user("seller")
        await seller.send(sim.chat_message("sell", BIKE))
        assert (await seller.next_reply()).text() == chat.STILL_WORKING
        await seller.send(sim.chat_message("is it done?", BIKE))
        assert (await seller.next_reply()).text() == chat.ALREADY_WORKING
        release.set()
        assert "suggested listing" in (await seller.next_reply()).text()
    assert len(store.owned(proposal_owner(store))) == 1


@pytest.mark.parametrize("mime_type", ["image", "jpeg", "IMAGE/JPEG", "application/octet-stream"])
def test_image_link_is_used_whatever_its_mime_type(mime_type: str) -> None:
    # ASI:One's uploads are Cloudinary image links whose mime_type isn't always "image/...".
    url = "https://res.cloudinary.com/fetch-ai/image/upload/v1/chat-images/kmzr0d8"
    msg = sim.chat_message("i wanna sell this", url, mime_type=mime_type)
    assert [p.url for p in chat.photos_in(msg)] == [url]


def test_storage_uploads_are_photos_with_an_asset_id() -> None:
    msg = sim.chat_message("sell", fakes.storage_uri("bike"))
    (photo,) = chat.photos_in(msg)
    assert photo.url == fakes.storage_uri("bike")
    assert photo.resource_id == str(msg.content[1].resource_id)
    assert chat.clean_text(msg) == "sell"


def test_a_storage_upload_that_says_it_is_not_an_image_is_skipped() -> None:
    msg = sim.chat_message("sell", fakes.storage_uri("doc"), mime_type="application/pdf")
    assert chat.photos_in(msg) == []


def test_attachments_are_summarised_for_the_log() -> None:
    summary = chat.attachments_summary(sim.chat_message("sell", fakes.storage_uri("bike")))
    assert fakes.storage_uri("bike") in summary and "mime_type='image/jpeg'" in summary
    assert chat.attachments_summary(sim.chat_message("hi")) == "none"


async def test_fetch_photo_uses_storage_for_uploads(monkeypatch: pytest.MonkeyPatch) -> None:
    downloaded: list[tuple[str, str]] = []

    def fake_storage(identity: object, asset_id: str) -> bytes:
        downloaded.append((str(identity), asset_id))
        return fakes.photo_bytes("bike")

    async def link_is_dead(url: str) -> bytes:
        raise fakes.PhotoError("I couldn't download the photo (the link returned 403).")

    monkeypatch.setattr(chat, "download_from_storage", fake_storage)
    monkeypatch.setattr(chat, "download_photo", link_is_dead)
    ctx: Any = SimpleNamespace(
        agent=SimpleNamespace(identity="market-identity"), logger=SimpleNamespace(info=print)
    )
    upload = chat.Photo(url=fakes.storage_uri("3f2a"), mime_type="image/jpeg", resource_id="3f2a")
    assert await chat.fetch_photo(ctx, upload) == fakes.photo_bytes("bike")
    # A web link that doesn't open falls back to storage when the upload has an asset ID.
    linked = chat.Photo(url=BIKE, mime_type="image/jpeg", resource_id="9c1d")
    assert await chat.fetch_photo(ctx, linked) == fakes.photo_bytes("bike")
    assert downloaded == [("market-identity", "3f2a"), ("market-identity", "9c1d")]
    with pytest.raises(fakes.PhotoError):
        await chat.fetch_photo(ctx, chat.Photo(url=BIKE, mime_type="image/jpeg"))


def test_storage_download_refuses_garbled_base64(monkeypatch: pytest.MonkeyPatch) -> None:
    class GarbledStorage:
        def __init__(self, **_: object) -> None:
            pass

        def download(self, asset_id: str) -> dict[str, str]:
            return {"contents": "not base64!!"}

    monkeypatch.setattr(photos, "ExternalStorage", GarbledStorage)
    with pytest.raises(photos.PhotoError):
        photos.download_from_storage(object(), "3f2a")


async def test_a_minimum_far_under_the_market_gets_a_plain_warning(market: sim.Market) -> None:
    """The October 24 case: "at least $40" on an item that sells for far more."""
    seller = market.user("seller")
    reply = await seller.say("sell this, at least $40", BIKE)
    assert "Lowest price you'll take: $40 (your price; buyers never see it)" in reply
    assert (
        "Your $40 minimum is far under what these sell for: the last 6 sales were around $340. "
        'You\'ll likely give it away at $40. Keep it, or say "lowest $310".'
    ) in reply
    assert "A fair asking price is $310 to $370." in reply
    reply = await seller.say("ask $900")
    assert "Your $900 asking price is far over what these sell for" in reply
    assert 'Buyers will likely pass at $900. Keep it, or say "ask $340".' in reply
    assert "Check your price" in reply and "Price looks right" not in reply  # the card's badge
    reply = await seller.say("ask $360, lowest $300")
    assert (
        "Your $360 asking price and $300 minimum are in line with the market: the last 6 sales "
        "were around $340."
    ) in reply
    assert "Price looks right" in reply and "Check your price" not in reply
    assert "is for sale at $360" in await seller.say("approve")  # the seller's price still wins


async def test_listing_offers_to_write_the_craigslist_post(
    market: sim.Market, store: Store
) -> None:
    """Approving leads the seller to the next step, Craigslist, instead of ending at the agent."""
    seller = market.user("seller")
    await seller.say("sell this, at least $300", BIKE)
    reply = await seller.exchange(sim.chat_message("approve"))
    assert "Want it on Craigslist too?" in reply.text()
    write = sim.button(reply, "craigslist")
    (listing,) = store.active()
    assert write == {"action": "craigslist", "item_id": listing.item_id}
    post = await seller.say("yes")
    assert "Price:" in post and listing.title in post
    # Asked again by the card later, it writes the same post; "yes" no longer means anything.
    assert "Price:" in await seller.tap(write)
    assert "nothing waiting for approval" in await seller.say("yes")


async def test_not_now_keeps_it_on_yardsale(market: sim.Market, store: Store) -> None:
    seller = market.user("seller")
    await seller.say("sell this, at least $300", BIKE)
    await seller.say("approve")
    assert "listed on Yardsale" in await seller.say("not now")
    assert store.active()  # still for sale
    await seller.say("sell this, at least $300", fakes.photo_url("bike-two"))
    await seller.say("approve")
    assert "listed on Yardsale" in await seller.say("no")
