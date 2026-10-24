"""The haggling rules, applied straight to the store (the item agents call the same functions)."""

from __future__ import annotations

from yardsale.models import Counter, Deal, OfferRejected
from yardsale.negotiation import answer_accept, answer_offer, buyer_key
from yardsale.store import COUNTER_TTL, PROPOSED, SOLD, Listing, Store

MARKET = "agent1qmarket"


class Clock:
    def __init__(self) -> None:
        self.now = 1_000_000.0

    def __call__(self) -> float:
        return self.now


def listed(clock: Clock | None = None) -> Store:
    store = Store(clock=clock or Clock())
    store.add_listing(
        Listing(
            item_id="bike",
            owner="seller",
            seller_id="payout",
            listing_ref="r",
            status=PROPOSED,
            title="Trek FX 2",
            asking_cents=36000,
            floor_cents=30000,
        )
    )
    store.activate("bike", "seller", "agent1qbike")
    return store


def test_an_offer_at_the_floor_closes_at_the_offer() -> None:
    store = listed()
    reply = answer_offer(store, "bike", "agent:bob", 30000, "req-1")
    assert isinstance(reply, Deal)
    assert (reply.price_cents, reply.request_id, reply.payment_status) == (
        30000,
        "req-1",
        "not_collected",
    )
    assert store.get("bike").status == SOLD  # type: ignore[union-attr]


def test_a_lowball_gets_a_midpoint_counter_never_below_the_floor() -> None:
    store = listed()
    reply = answer_offer(store, "bike", "agent:bob", 25000)
    assert isinstance(reply, Counter) and reply.counter_cents == 30500
    reply = answer_offer(store, "bike", "agent:carol", 1000)
    assert isinstance(reply, Counter) and reply.counter_cents == 30000
    assert "30000" not in reply.note and reply.expires_at


def test_only_the_exact_latest_counter_can_be_accepted() -> None:
    store = listed()
    answer_offer(store, "bike", "agent:bob", 25000)  # counter $305
    answer_offer(store, "bike", "agent:bob", 27000)  # counter $315 replaces it
    stale = answer_accept(store, "bike", "agent:bob", 30500)
    assert isinstance(stale, OfferRejected) and stale.code == "counter_changed"
    deal = answer_accept(store, "bike", "agent:bob", 31500)
    assert isinstance(deal, Deal) and deal.price_cents == 31500


def test_nobody_can_accept_another_buyers_counter() -> None:
    store = listed()
    answer_offer(store, "bike", "agent:bob", 25000)
    reply = answer_accept(store, "bike", "agent:mallory", 30500)
    assert isinstance(reply, OfferRejected) and reply.code == "no_counter"
    assert store.get("bike").status != SOLD  # type: ignore[union-attr]


def test_an_expired_counter_cannot_be_accepted() -> None:
    clock = Clock()
    store = listed(clock)
    answer_offer(store, "bike", "agent:bob", 25000)
    clock.now += COUNTER_TTL
    reply = answer_accept(store, "bike", "agent:bob", 30500)
    assert isinstance(reply, OfferRejected) and reply.code == "counter_expired"


def test_competing_buyers_cannot_both_buy() -> None:
    store = listed()
    answer_offer(store, "bike", "agent:bob", 25000)
    first = answer_offer(store, "bike", "agent:carol", 36000)
    second = answer_accept(store, "bike", "agent:bob", 30500)
    third = answer_offer(store, "bike", "agent:dave", 36000)
    assert isinstance(first, Deal)
    assert isinstance(second, OfferRejected) and second.code == "unavailable"
    assert isinstance(third, OfferRejected) and third.code == "unavailable"


def test_bad_offers_and_unknown_items() -> None:
    store = listed()
    for amount in (0, -1, 10**12):
        reply = answer_offer(store, "bike", "agent:bob", amount)
        assert isinstance(reply, OfferRejected) and reply.code == "invalid_amount"
    reply = answer_offer(store, "nope", "agent:bob", 100)
    assert isinstance(reply, OfferRejected) and reply.code == "unknown_item"


def test_a_canceled_listing_takes_no_offers() -> None:
    store = listed()
    store.cancel("bike", "seller")
    reply = answer_offer(store, "bike", "agent:bob", 36000)
    assert isinstance(reply, OfferRejected) and reply.code == "unavailable"


def test_only_the_market_speaks_for_chat_buyers() -> None:
    assert buyer_key(MARKET, "chat-123", MARKET) == "chat:chat-123"
    assert buyer_key("agent1qbob", "", MARKET) == "agent:agent1qbob"
    # Another agent can't pose as a chat buyer to take their counter-offer.
    assert buyer_key("agent1qmallory", "chat-123", MARKET) == "agent:agent1qmallory"
