"""Buying in the chat: the buyer agent haggling with several items at once, paying through a
payment link, and the seller's cards (the sales behind a price, the prices form)."""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator

import pytest
from uagents_core.contrib.protocols.chat import ChatMessage, MetadataContent
from uagents_core.contrib.protocols.chat.cards import (
    Card,
    CarouselCardPayload,
    CustomCardPayload,
    FormCardPayload,
    ReviewCardPayload,
    extract_card,
)

from tests import fakes, sim
from yardsale import chat
from yardsale.store import PAID, PENDING, PROPOSED, Store

BIKE = fakes.photo_url("bike")
ADDRESS = re.compile(r"agent1q[0-9a-z]{50,}")


def card_in(msg: ChatMessage) -> Card | None:
    for block in msg.content:
        if isinstance(block, MetadataContent) and (card := extract_card(block)) is not None:
            return card
    return None


async def list_bike(market: sim.Market, name: str, asking: int, lowest: int) -> None:
    seller = market.user(name)
    await seller.say("sell this", BIKE)
    await seller.say(f"ask ${asking}")
    await seller.say(f"lowest ${lowest}")
    assert ADDRESS.search(await seller.say("approve"))


@pytest.fixture
async def paying_market(store: Store) -> AsyncIterator[tuple[sim.Market, fakes.FakePayments]]:
    payments = fakes.FakePayments()
    async with sim.running_market(store, payments=payments) as running:
        yield running, payments


# --- The buyer agent haggles --------------------------------------------------------------------


async def test_haggle_for_me_with_several_sellers_at_once(market: sim.Market, store: Store) -> None:
    await list_bike(market, "ann", 360, 300)
    await list_bike(market, "ben", 340, 290)
    buyer = market.user("buyer")
    await buyer.say("find me a hybrid bike")
    reply = await buyer.exchange(sim.chat_message("haggle with the sellers, max $320"))
    text = reply.text()
    assert "haggled with 2 seller agent(s) at once" in text
    assert "Best deal: #" in text and "$320" in text  # the buyer's own max is shown back
    for floor in ("$300", "$290"):
        assert f"→ {floor}" not in text  # no counter lands on a floor
    card = card_in(reply)
    assert card is not None and isinstance(card.payload, CustomCardPayload)
    assert "$300" not in card.model_dump_json() or "you $300" in text  # only as the buyer's offer

    deal = await buyer.say("accept")
    assert "Deal!" in deal
    (bought,) = store.deals()
    assert bought.price_cents <= 32000


async def test_an_acceptance_relayed_by_the_planner_buys(market: sim.Market, store: Store) -> None:
    await list_bike(market, "ann", 360, 300)
    buyer = market.user("buyer")
    await buyer.say("find me a hybrid bike")
    reply = await buyer.say("haggle with the seller, max $320")
    assert "Yours for" in reply
    # Not a yes: nothing is bought.
    assert "Deal!" not in await buyer.say("The buyer does not accept the deal yet.")
    assert store.deals() == []
    deal = await buyer.say(
        "The buyer accepts the negotiated deal for the used hybrid bike at the agreed price "
        "(within the $320 max budget). Please proceed to complete the purchase."
    )
    assert "Deal!" in deal
    (bought,) = store.deals()
    assert bought.price_cents <= 32000


async def test_a_relayed_acceptance_with_nothing_on_offer_buys_nothing(
    market: sim.Market, store: Store
) -> None:
    await list_bike(market, "ann", 360, 300)
    buyer = market.user("buyer")
    await buyer.say("The buyer accepts the deal. Please proceed to complete the purchase.")
    assert store.deals() == []


async def test_haggle_asks_for_a_max_first(market: sim.Market) -> None:
    await list_bike(market, "ann", 360, 300)
    buyer = market.user("buyer")
    await buyer.say("find me a hybrid bike")
    assert await buyer.say("haggle with the seller on 1") == chat.ASK_MAX
    reply = await buyer.say("$330")
    assert "haggled with 1 seller agent(s)" in reply and "Yours for" in reply


async def test_haggle_with_nothing_near_the_max(market: sim.Market) -> None:
    await list_bike(market, "ann", 360, 300)
    buyer = market.user("buyer")
    await buyer.say("find me a hybrid bike")
    assert "close enough" in await buyer.say("haggle with the seller, max $100")


async def test_haggle_below_every_floor_buys_nothing(market: sim.Market, store: Store) -> None:
    await list_bike(market, "ann", 360, 300)
    buyer = market.user("buyer")
    await buyer.say("find me a hybrid bike")
    reply = await buyer.say("haggle with the seller, max $260")
    assert "come down to your max" in reply and "Best deal" not in reply
    assert "no counter-offer" in await buyer.say("accept")
    assert all(store.deal_for(listing.item_id) is None for listing in store.active())


async def test_the_haggle_card_buys_the_item(market: sim.Market, store: Store) -> None:
    await list_bike(market, "ann", 360, 300)
    buyer = market.user("buyer")
    await buyer.say("find me a hybrid bike")
    reply = await buyer.exchange(sim.chat_message("haggle with the seller, max $340"))
    card = card_in(reply)
    assert card is not None and isinstance(card.payload, CustomCardPayload)
    selection = re.search(r'"selection":\s*(\{[^}]*\})', card.model_dump_json())
    assert selection is not None
    buy = json.loads(selection.group(1))
    assert buy["action"] == "accept"
    assert "isn't open anymore" in await market.user("mallory").tap(buy)
    assert "Deal!" in await buyer.tap(buy)
    (deal,) = store.deals()
    assert deal.price_cents == buy["cents"]


# --- Paying -------------------------------------------------------------------------------------


async def test_a_deal_gets_a_payment_link_and_is_paid(
    paying_market: tuple[sim.Market, fakes.FakePayments], store: Store
) -> None:
    market, payments = paying_market
    await list_bike(market, "ann", 360, 300)
    buyer = market.user("buyer")
    await buyer.say("bike")
    assert "agreed to $360" in await buyer.say("offer $360")
    reply = await buyer.exchange(sim.chat_message("accept"))
    (deal_id,) = payments.links
    assert f"[Pay $360](https://pay.example.test/{deal_id})" in reply.text()
    card = card_in(reply)
    assert card is not None and isinstance(card.payload, ReviewCardPayload)
    paid_button = card.payload.approve_cta.selection
    assert paid_button == {"action": "paid", "deal_id": deal_id}
    deal = store.deal(deal_id)
    assert deal is not None and deal.payment_status == PENDING

    assert "I don't see a payment" in await buyer.say("paid")
    assert "waiting for the buyer to pay" in await market.users[0].say("my listings")
    payments.pay(deal_id)
    assert "Payment received: $360" in await buyer.tap(paid_button)
    deal = store.deal(deal_id)
    assert deal is not None and deal.payment_status == PAID
    assert (deal.commission_cents, deal.payout_cents) == (1800, 34200)
    assert "You get $342 after Yardsale's 5% fee" in await market.users[0].say("my listings")
    assert "paid" in await buyer.say("my orders")


async def test_a_failed_payment_link_can_be_retried(
    paying_market: tuple[sim.Market, fakes.FakePayments], store: Store
) -> None:
    market, payments = paying_market
    await list_bike(market, "ann", 360, 300)
    buyer = market.user("buyer")
    await buyer.say("bike")
    await buyer.say("offer $360")
    payments.fail = True
    assert "couldn't make the payment link" in await buyer.say("accept")
    payments.fail = False
    assert "Pay here: [Pay $360]" in await buyer.say("pay")


async def test_other_conversations_cannot_see_your_payment(
    paying_market: tuple[sim.Market, fakes.FakePayments],
) -> None:
    market, _ = paying_market
    await list_bike(market, "ann", 360, 300)
    buyer = market.user("buyer")
    await buyer.say("bike")
    await buyer.say("offer $360")
    await buyer.say("accept")
    assert "haven't bought anything" in await market.user("mallory").say("paid")


# --- Seller cards -------------------------------------------------------------------------------


async def test_the_sales_behind_a_price_follow_the_proposal(market: sim.Market) -> None:
    seller = market.user("seller")
    await seller.say("sell this", BIKE)
    follow_up = seller.follow_ups[0] if seller.follow_ups else await seller.next_reply()
    assert "closest eBay sales" in follow_up.text()
    card = card_in(follow_up)
    assert card is not None and isinstance(card.payload, CarouselCardPayload)
    first = card.payload.items[0]
    assert first.primary_cta is not None
    why = await seller.tap(first.primary_cta.selection)
    assert "sold for $" in why and "Claude judged it" in why
    assert "aren't available" in await market.user("mallory").tap(first.primary_cta.selection)


async def test_the_prices_form(market: sim.Market, store: Store) -> None:
    seller = market.user("seller")
    await seller.say("sell this", BIKE)
    reply = await seller.exchange(sim.chat_message("rules"))
    card = card_in(reply)
    assert card is not None and isinstance(card.payload, FormCardPayload)
    submit = dict(card.payload.submit_cta.selection)
    assert "isn't waiting" in await market.user("mallory").tap({**submit, "asking": "380"})
    assert "$380" in await seller.tap({**submit, "asking": "380", "lowest": "320"})
    row = store._db.execute("SELECT item_id FROM listings").fetchone()
    proposal = store.get(row["item_id"])
    assert proposal is not None
    assert (proposal.status, proposal.asking_cents, proposal.floor_cents) == (
        PROPOSED,
        38000,
        32000,
    )
    assert "Type your prices" in await seller.tap({**submit, "asking": "", "lowest": ""})


async def test_haggle_after_a_search_uses_its_results(market: sim.Market) -> None:
    await list_bike(market, "ann", 360, 300)
    buyer = market.user("buyer")
    await buyer.say("find me a hybrid bike")
    reply = await buyer.say("haggle $340")
    assert "haggled with 1 seller agent(s)" in reply
