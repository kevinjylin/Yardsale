"""Yardsale's checkout: one payment link per deal, sent through the Agent Payment Protocol.

Yardsale is the merchant for every sale, so the Market runs checkout for all item agents, both
those in its own Bureau and those hosted on Agentverse (which hold no payment keys):

1. A deal closes. The checkout makes a payment link for it (payments.py) and marks the payment
   pending.
2. An agent buyer gets a RequestPayment from the Market (role "seller" in Fetch's
   AgentPaymentProtocol) with the link in its metadata. A chat buyer gets the link in the chat.
3. The buyer pays. An agent buyer answers with CommitPayment; the checkout asks the provider and
   replies CompletePayment once it's paid, or CancelPayment if it isn't yet. A chat buyer says
   "paid" or taps the card. The Market also checks pending payments every minute.
4. Paid: the deal records Yardsale's commission and the seller's payout, and the order is
   completed (orders.py).

The provider decides whether a payment happened. A buyer's word, or a transaction ID it sends,
never marks a deal paid.
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict

from uagents import Agent, Context, Protocol
from uagents_core.contrib.protocols.payment import (
    CancelPayment,
    CommitPayment,
    CompletePayment,
    Funds,
    RejectPayment,
    RequestPayment,
    payment_protocol_spec,
)

from yardsale import orders
from yardsale.payments import PaymentError, PaymentLink, PaymentProvider, split
from yardsale.store import EXPIRED, NOT_COLLECTED, PAID, PENDING, REJECTED, DealRecord, Store

PAYMENT_DEADLINE = 24 * 3600  # seconds a buyer has to pay
CHECK_EVERY = 60.0  # seconds between checks of pending payments

log = logging.getLogger("yardsale.checkout")


def dollars_text(cents: int) -> str:
    return f"{cents // 100}.{cents % 100:02d}"


class Checkout:
    def __init__(self, store: Store, provider: PaymentProvider | None) -> None:
        self.store = store
        self.provider = provider
        self.market: Agent | None = None  # sends RequestPayment to agent buyers
        self._locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    @property
    def enabled(self) -> bool:
        return self.provider is not None

    def link(self, deal: DealRecord) -> PaymentLink | None:
        if not (deal.payment_provider and deal.payment_ref and deal.payment_url):
            return None
        return PaymentLink(deal.payment_provider, deal.payment_ref, deal.payment_url)

    def request_for(self, deal: DealRecord) -> RequestPayment:
        listing = self.store.get(deal.item_id)
        title = listing.title if listing else "a Yardsale item"
        return RequestPayment(
            accepted_funds=[
                Funds(
                    amount=dollars_text(deal.price_cents),
                    currency="USD",
                    payment_method=deal.payment_provider or "card",
                )
            ],
            recipient=self.market.address if self.market else "yardsale",
            deadline_seconds=PAYMENT_DEADLINE,
            reference=deal.deal_id,
            description=f"{title}, bought from Yardsale",
            metadata={
                "payment_url": deal.payment_url or "",
                "provider": deal.payment_provider or "",
                "item_id": deal.item_id,
                "item_agent": (listing.address or "") if listing else "",
            },
        )

    async def start(self, deal_id: str) -> DealRecord | None:
        """Make the deal's payment link if it has none, and send an agent buyer its
        RequestPayment. Safe to call more than once; the link is only made once."""
        async with self._locks[deal_id]:
            deal = self.store.deal(deal_id)
            if deal is None or self.provider is None or deal.payment_status != NOT_COLLECTED:
                return deal
            listing = self.store.get(deal.item_id)
            title = listing.title if listing else "Yardsale item"
            try:
                link = await self.provider.create(deal.deal_id, title, deal.price_cents)
            except PaymentError as ex:
                log.warning(f"No payment link for {deal_id}: {ex}")
                return deal
            started = self.store.start_payment(deal_id, link.provider, link.ref, link.url)
            if started is None:
                return self.store.deal(deal_id)
        log.info(f"Payment link for {deal_id} sent ({link.provider})")
        await orders.publish(started, listing)
        if started.buyer.startswith("agent:") and self.market is not None:
            await self.market._build_context().send(started.buyer[6:], self.request_for(started))
        return started

    async def refresh(self, deal_id: str) -> DealRecord | None:
        """Ask the provider about a pending payment and record the answer."""
        deal = self.store.deal(deal_id)
        link = self.link(deal) if deal else None
        if deal is None or deal.payment_status != PENDING or self.provider is None or link is None:
            return deal
        state = await self.provider.state(link)
        if state == "paid":
            commission, payout = split(deal.price_cents)
            updated = self.store.mark_paid(deal_id, commission, payout)
            if updated is not None:
                log.info(f"Payment received for {deal_id}")
                await orders.publish(updated, self.store.get(updated.item_id))
                return updated
        elif state == "expired":
            updated = self.store.end_payment(deal_id, EXPIRED)
            if updated is not None:
                await orders.publish(updated, self.store.get(updated.item_id))
                return updated
        return self.store.deal(deal_id)

    async def refresh_all(self) -> None:
        for deal in self.store.deals(payment_status=PENDING):
            try:
                await self.refresh(deal.deal_id)
            except PaymentError as ex:
                log.warning(f"Couldn't check payment for {deal.deal_id}: {ex}")


def build_payment_protocol(checkout: Checkout) -> Protocol:
    """The Market's side of Fetch's AgentPaymentProtocol (role "seller")."""
    proto = Protocol(spec=payment_protocol_spec, role="seller")

    @proto.on_message(CommitPayment)
    async def on_commit(ctx: Context, sender: str, msg: CommitPayment) -> None:
        deal = checkout.store.deal(msg.reference or "")
        if deal is None or deal.buyer != f"agent:{sender}":
            await ctx.send(
                sender,
                CancelPayment(transaction_id=msg.transaction_id, reason="no such deal for you"),
            )
            return
        try:
            deal = await checkout.refresh(deal.deal_id) or deal
        except PaymentError as ex:
            ctx.logger.warning(f"Couldn't check payment for {deal.deal_id}: {ex}")
        if deal.payment_status == PAID:
            await ctx.send(sender, CompletePayment(transaction_id=msg.transaction_id))
            return
        reason = "payment not received yet"
        if deal.payment_url and deal.payment_status == PENDING:
            reason += f"; pay at {deal.payment_url}, then send CommitPayment again"
        await ctx.send(sender, CancelPayment(transaction_id=msg.transaction_id, reason=reason))

    @proto.on_message(RejectPayment)
    async def on_reject(ctx: Context, sender: str, msg: RejectPayment) -> None:
        for deal in checkout.store.deals(buyer=f"agent:{sender}"):
            if deal.payment_status in (NOT_COLLECTED, PENDING):
                ended = checkout.store.end_payment(deal.deal_id, REJECTED)
                if ended is not None:
                    ctx.logger.info(f"Buyer declined to pay for {deal.deal_id}")
                    await orders.publish(ended, checkout.store.get(ended.item_id))
                return

    return proto
