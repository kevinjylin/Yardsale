"""How the chat's answers go out: one message per answer, every card in it once, and the parts of
a priced photo's answer in order.

On October 25 the real ASI:One app showed every card of a turn twice, a stale "Pricing your
stuff" card and the home menu beside a finished proposal, and the "You're in" sign-up line run
into the proposal's text. These tests play the same conversation (campus mode, a photo sent
before signing up) and count what the Market sends for each message.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from uagents_core.contrib.protocols.chat import ChatMessage, MetadataContent
from uagents_core.contrib.protocols.chat.cards import (
    Card,
    CarouselCardPayload,
    create_card_response_content,
    extract_card,
)

from tests import fakes, sim
from tests.test_advice import listing as shoes_listing
from yardsale import chat, onboarding
from yardsale.models import Comp
from yardsale.store import PROPOSED, Listing, Store

BOTTLE = fakes.photo_url("bike")  # the fakes price a bike; what it is doesn't matter here
SETTLE = 0.3  # seconds to wait for anything else the Market might still send
UPLOADS = {"attachments": "true"}


@pytest.fixture
def campus(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Campus mode on, without the campus lookup (a Claude call) that joining starts."""

    async def no_lookup(*args: object, **kwargs: object) -> None:
        return None

    monkeypatch.setenv("YARDSALE_CAMPUS", "1")
    monkeypatch.setattr(onboarding, "warm", no_lookup)
    yield


def tap(selection: dict[str, str]) -> ChatMessage:
    return ChatMessage(
        timestamp=datetime.now(UTC),
        msg_id=uuid4(),
        content=[create_card_response_content(selection=selection)],
    )


async def turn(user: sim.ChatUser, msg: ChatMessage, wait: float = SETTLE) -> list[ChatMessage]:
    """Everything the Market sends for one message: the first answer, then whatever follows
    within `wait` seconds."""
    await user.send(msg)
    got = [await user.next_reply()]
    while True:
        try:
            got.append(await asyncio.wait_for(user.replies.get(), wait))
        except TimeoutError:
            return got


def cards_in(messages: list[ChatMessage]) -> list[Card]:
    return [card for msg in messages for card in sim.cards_of(msg)]


def title(card: Card) -> str:
    payload = card.payload
    root = getattr(payload, "root", None)
    return str(getattr(payload, "title", None) or getattr(root, "title", None) or "")


def metadata(msg: ChatMessage) -> list[MetadataContent]:
    return [block for block in msg.content if isinstance(block, MetadataContent)]


def assert_each_card_once(messages: list[ChatMessage]) -> None:
    """No card goes out twice for one message, whether in one ChatMessage or across several."""
    shown = [
        chat.card_key(block)
        for msg in messages
        for block in metadata(msg)
        if extract_card(block) is not None
    ]
    assert len(shown) == len(set(shown)), [title(c) for c in cards_in(messages)]


def approve_buttons(cards: list[Card]) -> list[dict[str, object]]:
    return [
        selection
        for card in cards
        for _, selection in sim.card_buttons(card)
        if selection.get("action") == "approve"
    ]


# --- A student who sends a photo before signing up --------------------------------------------


async def test_signing_up_with_a_photo_is_one_message_per_turn(campus: None, store: Store) -> None:
    searches = 0

    async def counted(query: str, condition: str, max_results: int) -> list[Comp]:
        nonlocal searches
        searches += 1
        return await fakes.research(query, condition, max_results)

    async with sim.running_market(store, research=counted) as market:
        seller = market.user("seller")
        first = sim.chat_message(
            "I wanna sell this waterbottle, under 10 dollars minimum", BOTTLE, start_session=True
        )

        # Turn 1: the welcome, once, and nothing about the photo yet.
        welcome = await turn(seller, first)
        assert len(welcome) == 1
        assert welcome[0].text() == onboarding.ARE_YOU_A_STUDENT
        assert [title(c) for c in cards_in(welcome)] == [""]  # the "Yardsale on campus" card
        assert sum(block.metadata == UPLOADS for block in metadata(welcome[0])) == 1
        assert_each_card_once(welcome)

        # Turn 2: the email form, once.
        asked = await turn(seller, tap({"action": "student"}))
        assert [m.text() for m in asked] == [onboarding.ASK_FOR_EMAIL]
        assert [title(c) for c in cards_in(asked)] == ["Your school email"]

        # Turn 3: signed up, and the photo's proposal with it, in one message.
        joined = await turn(seller, tap({"action": "student_email", "email": "sam@ucr.edu"}))
        assert len(joined) == 1
        text = joined[0].text()
        assert text.startswith("You're in, at UCR.")
        assert "\n\nHere's my suggested listing" in text  # its own paragraph, not run together
        assert chat.STILL_WORKING not in text and "Got your photo" not in text
        found = cards_in(joined)
        assert len(found) == 2
        assert title(found[0]).startswith("List your")
        assert isinstance(found[1].payload, CarouselCardPayload)
        # No stale progress card, no home menu next to the proposal, and one way to approve.
        assert not any(title(c) in ("Pricing your stuff", "Yardsale") for c in found)
        assert len(approve_buttons(found)) == 1
        assert_each_card_once(joined)

    # The photo sent before signing up was priced exactly once.
    assert searches == 1
    (proposal,) = proposals(store)
    assert proposal.title.startswith("Trek FX 2")


def proposals(store: Store) -> list[Listing]:
    """Every proposal, whoever owns it."""
    owners = {row["owner"] for row in store._db.execute("SELECT owner FROM listings")}
    return [found for owner in owners for found in store.owned(owner, PROPOSED)]


async def test_a_photo_still_pricing_at_sign_up_follows_once(
    campus: None, store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(chat, "REPLY_WITHIN", 0.2)
    release = asyncio.Event()

    async def slow(query: str, condition: str, max_results: int) -> list[Comp]:
        await release.wait()
        return await fakes.research(query, condition, max_results)

    async with sim.running_market(store, research=slow) as market:
        seller = market.user("seller")
        welcome = await turn(seller, sim.chat_message("sell this", BOTTLE))
        assert [m.text() for m in welcome] == [onboarding.ARE_YOU_A_STUDENT]  # no "Got your photo"
        await turn(seller, tap({"action": "student"}))

        joined = await turn(seller, tap({"action": "student_email", "email": "sam@ucr.edu"}))
        assert len(joined) == 1
        assert joined[0].text().startswith("You're in, at UCR.")
        assert joined[0].text().endswith(chat.ALREADY_WORKING)
        assert [title(c) for c in cards_in(joined)] == ["Pricing your stuff"]

        release.set()
        proposal = [await seller.next_reply()]
        await asyncio.sleep(SETTLE)
        assert seller.replies.empty()
        assert "suggested listing" in proposal[0].text()
        found = cards_in(proposal)
        assert title(found[0]).startswith("List your")
        assert "Pricing your stuff" not in [title(c) for c in found]
        assert_each_card_once(proposal)
    assert len(proposals(store)) == 1  # priced once


# --- Any seller ----------------------------------------------------------------------------------


async def test_a_priced_photo_is_one_message_with_its_cards_once(market: sim.Market) -> None:
    seller = market.user("seller")
    answer = await turn(seller, sim.chat_message("sell this, at least $300", BOTTLE))
    assert len(answer) == 1
    found = cards_in(answer)
    assert [type(c.payload).__name__ for c in found] == ["CustomCardPayload", "CarouselCardPayload"]
    assert len(approve_buttons(found)) == 1  # the proposal card's, not the carousel's too
    carousel = found[1].payload
    assert isinstance(carousel, CarouselCardPayload)
    assert all(item.id != "main-menu" for item in carousel.items)  # the proposal card has it
    assert_each_card_once(answer)


async def test_slow_pricing_sends_progress_then_the_result_without_it(
    store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(chat, "REPLY_WITHIN", 0.05)
    release = asyncio.Event()

    async def slow(query: str, condition: str, max_results: int) -> list[Comp]:
        await release.wait()
        return await fakes.research(query, condition, max_results)

    async with sim.running_market(store, research=slow) as market:
        seller = market.user("seller")
        await seller.send(sim.chat_message("sell this", BOTTLE))
        working = await seller.next_reply()
        assert working.text() == chat.STILL_WORKING
        assert [title(c) for c in sim.cards_of(working)] == ["Pricing your stuff"]
        release.set()
        done = await seller.next_reply()
        await asyncio.sleep(SETTLE)
        assert seller.replies.empty()
        assert "suggested listing" in done.text()
        assert "Pricing your stuff" not in [title(c) for c in sim.cards_of(done)]
        assert_each_card_once([working, done])


# --- The proposal's words -------------------------------------------------------------------------


def test_sold_and_listed_prices_are_separate_lists() -> None:
    text = chat.proposal_text(shoes_listing())
    parts = text.split("\n\n")
    sold = parts.index(chat.SOLD_HEADING)
    assert parts[sold + 1].startswith("- [Air Jordan 4 Retro sz 10")
    assert all(line.startswith("- ") for line in parts[sold + 1].splitlines())
    assert "sold 2026-10-01" in parts[sold + 1]
    assert " on Craigslist" not in parts[sold + 1] and " on OfferUp" not in parts[sold + 1]
    listed = parts.index(chat.ASKING_HEADING)
    assert listed > sold
    assert all(line.startswith("- ") for line in parts[listed + 1].splitlines())
    assert " on Craigslist" in parts[listed + 1] or " on OfferUp" in parts[listed + 1]
    # The last list ends before the prompt: a blank line, not a lazy continuation of a bullet.
    assert parts[-1] == chat.APPROVE_PROMPT
    assert f"\n\n{chat.APPROVE_PROMPT}" in text


def test_the_sales_line_agrees_with_the_cards_and_the_count() -> None:
    proposal = shoes_listing()  # 20 sales behind the price, 6 in the carousel
    assert chat.comps_text(proposal) == (
        "The cards show the 6 closest of the 20 eBay sales behind that price."
    )
    reply = chat.proposal_reply(proposal)
    (carousel,) = reply.extra
    assert carousel.metadata["card_kind"] == "carousel"
    assert reply.text.endswith(chat.comps_text(proposal))
    assert reply.more == ()  # one message


@pytest.mark.parametrize(
    ("first", "answered"), [("find me a mini fridge under $60", True), ("hey!", False)]
)
async def test_a_new_students_first_words_are_answered_after_sign_up(
    campus: None, store: Store, first: str, answered: bool
) -> None:
    """Searches are same-school, so a first request waits for the school, then gets its answer
    with the sign-up line. A bare hello gets nothing more than the sign-up."""
    async with sim.running_market(store) as market:
        student = market.user("student")
        welcome = await turn(student, sim.chat_message(first, start_session=True))
        assert [m.text() for m in welcome] == [onboarding.ARE_YOU_A_STUDENT]
        await turn(student, tap({"action": "student"}))
        joined = await turn(student, tap({"action": "student_email", "email": "sam@ucr.edu"}))
        text = "\n".join(m.text() for m in joined)
        assert text.startswith("You're in, at UCR.")
        assert ("mini fridge" in text.lower()) is answered
        assert "Hi, I'm Yardsale" not in text
