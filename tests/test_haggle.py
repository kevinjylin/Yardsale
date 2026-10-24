"""Buyer agents haggling with an item agent directly, over real agent messages."""

from __future__ import annotations

import asyncio

from uagents import Model

from tests import sim
from tests.helpers import Peer
from yardsale.models import AcceptCounter, Counter, Deal, Listed, ListItem, Offer, OfferRejected
from yardsale.store import SOLD, Store

BUYER_KINDS = (Counter, Deal, OfferRejected)


async def list_bike(market: sim.Market) -> Listed:
    seller = Peer("seller", Listed)
    return await seller.ask(
        market.address,
        ListItem(
            listing_ref="bike",
            title="Trek FX 2",
            asking_cents=36000,
            floor_cents=30000,
            seller_id="s",
        ),
        Listed,
    )


async def test_lowball_counter_accept(market: sim.Market, store: Store) -> None:
    bike = await list_bike(market)
    bob = Peer("bob", *BUYER_KINDS)
    counter = await bob.ask(
        bike.item_address, Offer(item_id=bike.item_id, offer_cents=25000), Counter
    )
    assert counter.counter_cents == 30500
    deal = await bob.ask(
        bike.item_address, AcceptCounter(item_id=bike.item_id, counter_cents=30500), Deal
    )
    assert deal.price_cents == 30500 and deal.deal_id and deal.payment_status == "not_collected"
    assert store.get(bike.item_id).status == SOLD  # type: ignore[union-attr]


async def test_another_buyer_cannot_take_your_counter(market: sim.Market) -> None:
    bike = await list_bike(market)
    bob, mallory = Peer("bob", *BUYER_KINDS), Peer("mallory", *BUYER_KINDS)
    await bob.ask(bike.item_address, Offer(item_id=bike.item_id, offer_cents=25000), Counter)
    # Same amount, and pretending to be a chat buyer the Market speaks for: neither works.
    for buyer_ref in ("", "some-chat"):
        rejected = await mallory.ask(
            bike.item_address,
            AcceptCounter(item_id=bike.item_id, counter_cents=30500, buyer_ref=buyer_ref),
            OfferRejected,
        )
        assert rejected.code == "no_counter"


async def test_competing_buyers_close_once(market: sim.Market, store: Store) -> None:
    bike = await list_bike(market)
    buyers = [Peer(f"buyer{n}", *BUYER_KINDS) for n in range(5)]
    for buyer in buyers:
        await buyer.send(bike.item_address, Offer(item_id=bike.item_id, offer_cents=36000))
    replies = await asyncio.gather(*(buyer.receive(Model) for buyer in buyers))
    assert sum(isinstance(reply, Deal) for reply in replies) == 1
    assert all(r.code == "unavailable" for r in replies if isinstance(r, OfferRejected))
    assert store.deal_for(bike.item_id) is not None


async def test_wrong_item_id_is_refused(market: sim.Market) -> None:
    bike = await list_bike(market)
    bob = Peer("bob", *BUYER_KINDS)
    rejected = await bob.ask(
        bike.item_address, Offer(item_id="something-else", offer_cents=36000), OfferRejected
    )
    assert rejected.code == "unknown_item"
