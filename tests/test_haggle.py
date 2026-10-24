"""Buyer agents haggling with an item agent directly, over real agent messages."""

from __future__ import annotations

import asyncio

import pytest
from uagents import Model
from uagents_core.contrib.protocols.payment import (
    CancelPayment,
    CommitPayment,
    CompletePayment,
    RequestPayment,
)

from tests import fakes, sim
from tests.helpers import Peer
from yardsale import bureau_server
from yardsale.models import (
    AcceptCounter,
    Counter,
    Deal,
    HaggleRequest,
    HaggleResult,
    Listed,
    ListItem,
    Offer,
    OfferRejected,
)
from yardsale.store import PAID, SOLD, Store

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


async def test_offer_counter_accept(market: sim.Market, store: Store) -> None:
    bike = await list_bike(market)
    bob = Peer("bob", *BUYER_KINDS)
    counter = await bob.ask(
        bike.item_address, Offer(item_id=bike.item_id, offer_cents=25000), Counter
    )
    assert 30000 < counter.counter_cents < 36000 and not counter.agreed
    price = counter.counter_cents
    deal = await bob.ask(
        bike.item_address, AcceptCounter(item_id=bike.item_id, counter_cents=price), Deal
    )
    assert deal.price_cents == price and deal.deal_id and deal.payment_status == "not_collected"
    assert store.get(bike.item_id).status == SOLD  # type: ignore[union-attr]


async def test_an_agent_buyer_pays_through_the_payment_protocol(store: Store) -> None:
    payments = fakes.FakePayments()
    async with sim.running_market(store, payments=payments) as market:
        bike = await list_bike(market)
        bob = Peer("bob", *BUYER_KINDS, RequestPayment, CompletePayment, CancelPayment)
        agreed = await bob.ask(
            bike.item_address, Offer(item_id=bike.item_id, offer_cents=33000), Counter
        )
        assert agreed.agreed and agreed.counter_cents == 33000
        deal = await bob.ask(
            bike.item_address, AcceptCounter(item_id=bike.item_id, counter_cents=33000), Deal
        )
        request = await bob.receive(RequestPayment)
        assert request.reference == deal.deal_id and request.recipient == market.address
        assert request.accepted_funds[0].amount == "330.00"
        assert request.metadata["payment_url"] == f"https://pay.example.test/{deal.deal_id}"

        commit = CommitPayment(
            funds=request.accepted_funds[0],
            recipient=request.recipient,
            transaction_id="tx-1",
            reference=deal.deal_id,
        )
        not_yet = await bob.ask(market.address, commit, CancelPayment)
        assert "not received yet" in (not_yet.reason or "")
        # Someone else can't confirm Bob's deal.
        mallory = Peer("mallory", CompletePayment, CancelPayment)
        assert "no such deal" in (
            (await mallory.ask(market.address, commit, CancelPayment)).reason or ""
        )

        payments.pay(deal.deal_id)
        done = await bob.ask(market.address, commit, CompletePayment)
        assert done.transaction_id == "tx-1"
        record = store.deal(deal.deal_id)
        assert record is not None and record.payment_status == PAID
        assert (record.commission_cents, record.payout_cents) == (1650, 31350)


async def test_another_buyer_cannot_take_your_counter(market: sim.Market) -> None:
    bike = await list_bike(market)
    bob, mallory = Peer("bob", *BUYER_KINDS), Peer("mallory", *BUYER_KINDS)
    counter = await bob.ask(
        bike.item_address, Offer(item_id=bike.item_id, offer_cents=25000), Counter
    )
    # Same amount, and pretending to be a chat buyer the Market speaks for: neither works.
    for buyer_ref in ("", "some-chat"):
        rejected = await mallory.ask(
            bike.item_address,
            AcceptCounter(
                item_id=bike.item_id, counter_cents=counter.counter_cents, buyer_ref=buyer_ref
            ),
            OfferRejected,
        )
        assert rejected.code == "no_counter"


async def test_competing_buyers_close_once(market: sim.Market, store: Store) -> None:
    bike = await list_bike(market)
    buyers = [Peer(f"buyer{n}", *BUYER_KINDS) for n in range(5)]
    for buyer in buyers:
        agreed = await buyer.ask(
            bike.item_address, Offer(item_id=bike.item_id, offer_cents=36000), Counter
        )
        assert agreed.agreed  # agreeing isn't selling: everyone can get an agreement
    for buyer in buyers:
        await buyer.send(
            bike.item_address, AcceptCounter(item_id=bike.item_id, counter_cents=36000)
        )
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


async def test_only_the_market_can_send_the_buyer_agent_shopping(market: sim.Market) -> None:
    await list_bike(market)
    mallory = Peer("mallory", HaggleResult)
    request = HaggleRequest(request_id="x", buyer_ref="someone", max_cents=36000, items=[])
    await mallory.send(bureau_server.runtime().shopper, request)
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(mallory.receive(HaggleResult), 0.5)
