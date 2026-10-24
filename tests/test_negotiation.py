"""The haggling rules, applied straight to the store (the item agents call the same functions)."""

from __future__ import annotations

from yardsale.models import Counter, Deal, OfferRejected
from yardsale.negotiation import answer_accept, answer_offer, buyer_key, gap_cents
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


def test_an_offer_at_the_floor_is_agreed_and_accepting_closes_it() -> None:
    store = listed()
    reply = answer_offer(store, "bike", "agent:bob", 30000, "req-1")
    assert isinstance(reply, Counter) and reply.agreed
    assert (reply.counter_cents, reply.request_id) == (30000, "req-1")
    assert store.get("bike").status != SOLD  # type: ignore[union-attr]  # not until accepted
    deal = answer_accept(store, "bike", "agent:bob", 30000)
    assert isinstance(deal, Deal)
    assert (deal.price_cents, deal.payment_status) == (30000, "not_collected")
    assert store.get("bike").status == SOLD  # type: ignore[union-attr]


def test_counters_come_down_but_never_reach_the_floor() -> None:
    store = listed()
    listing = store.get("bike")
    assert listing is not None
    gap = gap_cents(listing)
    assert 900 <= gap <= 2100  # 15-35% of the $60 spread
    prices = []
    for offer in (25000, 26000, 27000, 28000, 29000, 29500, 29900, 29900, 29900, 29900):
        reply = answer_offer(store, "bike", "agent:bob", offer)
        assert isinstance(reply, Counter) and not reply.agreed
        assert "300" not in reply.note and reply.expires_at
        prices.append(reply.counter_cents)
    assert prices == sorted(prices, reverse=True)
    assert prices[0] < 36000
    assert all(price >= 30000 + gap for price in prices)


def test_a_lowball_gets_no_concession() -> None:
    store = listed()
    reply = answer_offer(store, "bike", "agent:carol", 1000)
    assert isinstance(reply, Counter) and reply.counter_cents == 36000 and not reply.agreed
    again = answer_offer(store, "bike", "agent:carol", 1000)
    assert isinstance(again, Counter) and again.counter_cents == 36000


def test_an_agreement_cant_be_used_to_probe_lower() -> None:
    store = listed()
    agreed = answer_offer(store, "bike", "agent:bob", 31000)
    assert isinstance(agreed, Counter) and agreed.agreed and agreed.counter_cents == 31000
    lower = answer_offer(store, "bike", "agent:bob", 30000)
    assert isinstance(lower, Counter) and lower.agreed and lower.counter_cents == 31000


def test_an_offer_above_the_last_counter_agrees_at_the_counter() -> None:
    store = listed()
    counter = answer_offer(store, "bike", "agent:bob", 25000)
    assert isinstance(counter, Counter)
    reply = answer_offer(store, "bike", "agent:bob", 36000)
    assert isinstance(reply, Counter) and reply.agreed
    assert reply.counter_cents == counter.counter_cents


def test_only_the_exact_latest_counter_can_be_accepted() -> None:
    store = listed()
    first = answer_offer(store, "bike", "agent:bob", 25000)
    second = answer_offer(store, "bike", "agent:bob", 27000)
    assert isinstance(first, Counter) and isinstance(second, Counter)
    assert second.counter_cents < first.counter_cents
    stale = answer_accept(store, "bike", "agent:bob", first.counter_cents)
    assert isinstance(stale, OfferRejected) and stale.code == "counter_changed"
    deal = answer_accept(store, "bike", "agent:bob", second.counter_cents)
    assert isinstance(deal, Deal) and deal.price_cents == second.counter_cents


def test_nobody_can_accept_another_buyers_counter() -> None:
    store = listed()
    counter = answer_offer(store, "bike", "agent:bob", 25000)
    assert isinstance(counter, Counter)
    reply = answer_accept(store, "bike", "agent:mallory", counter.counter_cents)
    assert isinstance(reply, OfferRejected) and reply.code == "no_counter"
    assert store.get("bike").status != SOLD  # type: ignore[union-attr]


def test_an_expired_counter_cannot_be_accepted() -> None:
    clock = Clock()
    store = listed(clock)
    counter = answer_offer(store, "bike", "agent:bob", 25000)
    assert isinstance(counter, Counter)
    clock.now += COUNTER_TTL
    reply = answer_accept(store, "bike", "agent:bob", counter.counter_cents)
    assert isinstance(reply, OfferRejected) and reply.code == "counter_expired"
    # An expired haggle starts over: the next counter is a first-round counter again.
    fresh = answer_offer(store, "bike", "agent:bob", 25000)
    assert isinstance(fresh, Counter) and fresh.counter_cents == counter.counter_cents


def test_competing_buyers_cannot_both_buy() -> None:
    store = listed()
    bobs = answer_offer(store, "bike", "agent:bob", 25000)
    carols = answer_offer(store, "bike", "agent:carol", 36000)
    assert isinstance(bobs, Counter) and isinstance(carols, Counter) and carols.agreed
    first = answer_accept(store, "bike", "agent:carol", 36000)
    second = answer_accept(store, "bike", "agent:bob", bobs.counter_cents)
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
    # The buyer agent haggles for chat buyers too.
    assert buyer_key("agent1qshopper", "chat-123", {MARKET, "agent1qshopper"}) == "chat:chat-123"
