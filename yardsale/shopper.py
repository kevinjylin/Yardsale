"""The buyer agent ("yardsale-shopper"): haggles with several item agents at once for a shopper.

The Market sends it a HaggleRequest with the shopper's max and the matching items. For each item
it makes offers over the published YardsaleHaggle protocol (models.HAGGLE_SPEC), meeting each
counter halfway and never going above the max (haggle.buyer_move). Every item is haggled at the
same time, and the reply lists every round with every item and the best price found.

It never buys. Agreements and counters stay open for the chat buyer (it sends buyer_ref, which
item agents accept only from Yardsale's own agents), and the shopper picks one in the chat.
Any other agent can take the buyer's seat by speaking the same protocol.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from uuid import uuid4

from uagents import Agent, Context, Protocol

from yardsale import haggle
from yardsale.agentverse import service_agent
from yardsale.models import (
    HAGGLE_SPEC,
    Counter,
    Deal,
    HaggleOutcome,
    HaggleRequest,
    HaggleResult,
    HaggleRound,
    HaggleTarget,
    Offer,
    OfferRejected,
)

ROUND_TIMEOUT = 30.0  # seconds to wait for one item agent's answer (Agentverse mailboxes are slow)
MAX_ITEMS = 5

DESCRIPTION = (
    "Yardsale's buyer agent. Haggles with several secondhand item agents at once over the "
    "YardsaleHaggle protocol, never going above the shopper's max, and reports every round."
)

log = logging.getLogger("yardsale.shopper")


@dataclass
class Shopper:
    agent: Agent
    market: str = ""  # only the Market may send requests
    round_timeout: float = ROUND_TIMEOUT
    pending: dict[str, tuple[str, asyncio.Future[Counter | Deal | OfferRejected]]] = field(
        default_factory=dict
    )

    @property
    def address(self) -> str:
        return self.agent.address


def haggle_timeout(round_timeout: float = ROUND_TIMEOUT) -> float:
    """How long the Market should wait for a whole HaggleResult: items run at the same time,
    rounds with one item one after another."""
    return haggle.MAX_ROUNDS * round_timeout + 10


def best_outcome(outcomes: list[HaggleOutcome]) -> HaggleOutcome | None:
    """The cheapest item the shopper can buy now; agreements before counters at the same
    price."""
    buyable = [
        o for o in outcomes if o.price_cents is not None and o.status in ("agreed", "counter")
    ]
    if not buyable:
        return None
    return min(buyable, key=lambda o: (o.price_cents, o.status != "agreed", len(o.rounds)))


async def _ask(
    shopper: Shopper, ctx: Context, target: HaggleTarget, offer: Offer
) -> Counter | Deal | OfferRejected | None:
    future: asyncio.Future[Counter | Deal | OfferRejected] = (
        asyncio.get_running_loop().create_future()
    )
    shopper.pending[offer.request_id] = (target.address, future)
    try:
        await ctx.send(target.address, offer)
        return await asyncio.wait_for(future, shopper.round_timeout)
    except TimeoutError:
        return None
    finally:
        shopper.pending.pop(offer.request_id, None)


async def haggle_one(
    shopper: Shopper, ctx: Context, request: HaggleRequest, target: HaggleTarget
) -> HaggleOutcome:
    def outcome(status: str, price: int | None, note: str = "") -> HaggleOutcome:
        return HaggleOutcome(
            item_id=target.item_id,
            title=target.title,
            asking_cents=target.asking_cents,
            status=status,
            price_cents=price,
            rounds=rounds,
            note=note,
        )

    rounds: list[HaggleRound] = []
    offer_cents = haggle.opening_offer(target.asking_cents, request.max_cents)
    for n in range(1, haggle.MAX_ROUNDS + 1):
        reply = await _ask(
            shopper,
            ctx,
            target,
            Offer(
                item_id=target.item_id,
                offer_cents=offer_cents,
                buyer_ref=request.buyer_ref,
                request_id=uuid4().hex,
            ),
        )
        if reply is None:
            return outcome("no_answer", None, "the item's agent didn't answer")
        if isinstance(reply, OfferRejected):
            return outcome("unavailable", None, reply.reason)
        if isinstance(reply, Deal):  # offers never close a sale in this protocol version
            return outcome("unavailable", None, "unexpected sale")
        rounds.append(
            HaggleRound(
                offer_cents=offer_cents, reply_cents=reply.counter_cents, agreed=reply.agreed
            )
        )
        if reply.agreed:
            return outcome("agreed", reply.counter_cents)
        move = haggle.buyer_move(
            target.asking_cents, request.max_cents, offer_cents, reply.counter_cents, n
        )
        if move.kind == "take":
            return outcome("counter", move.cents)
        if move.kind == "stop":
            return outcome("over_max", None, f"its best price was {haggle.usd(move.cents)}")
        offer_cents = move.cents
    return outcome("over_max", None)  # not reached: buyer_move stops at MAX_ROUNDS


async def haggle_all(shopper: Shopper, ctx: Context, request: HaggleRequest) -> HaggleResult:
    targets = request.items[:MAX_ITEMS]
    outcomes = list(await asyncio.gather(*(haggle_one(shopper, ctx, request, t) for t in targets)))
    best = best_outcome(outcomes)
    return HaggleResult(
        request_id=request.request_id,
        max_cents=request.max_cents,
        outcomes=outcomes,
        best_item_id=best.item_id if best else "",
    )


def build_shopper(offline: bool, round_timeout: float = ROUND_TIMEOUT) -> Shopper:
    """The buyer agent. Set `market` before it gets requests. On Agentverse in live mode, so
    item agents hosted there can reach it."""
    agent = service_agent("shopper", "yardsale-shopper", "ys-shopper", DESCRIPTION, offline)
    shopper = Shopper(agent=agent, round_timeout=round_timeout)
    buyer = Protocol(spec=HAGGLE_SPEC, role="buyer")

    def resolve(ctx: Context, sender: str, msg: Counter | Deal | OfferRejected) -> None:
        waiting = shopper.pending.get(msg.request_id)
        if waiting is None or waiting[1].done():
            ctx.logger.info(f"Ignoring a late or unknown haggle reply from {sender[:16]}")
        elif waiting[0] != sender:
            ctx.logger.warning(f"Ignoring a haggle reply from {sender[:16]}: wrong item agent")
        else:
            waiting[1].set_result(msg)

    @buyer.on_message(Counter)
    async def on_counter(ctx: Context, sender: str, msg: Counter) -> None:
        resolve(ctx, sender, msg)

    @buyer.on_message(Deal)
    async def on_deal(ctx: Context, sender: str, msg: Deal) -> None:
        resolve(ctx, sender, msg)

    @buyer.on_message(OfferRejected)
    async def on_rejected(ctx: Context, sender: str, msg: OfferRejected) -> None:
        resolve(ctx, sender, msg)

    shop = Protocol(name="YardsaleShopper", version="0.1.0")

    @shop.on_message(HaggleRequest, replies={HaggleResult})
    async def on_request(ctx: Context, sender: str, msg: HaggleRequest) -> None:
        if sender != shopper.market:
            ctx.logger.warning(f"Ignoring a haggle request from {sender[:16]}: not the Market")
            return
        result = await haggle_all(shopper, ctx, msg)
        agreed = sum(o.status == "agreed" for o in result.outcomes)
        # The max stays out of the log, like a seller's floor.
        ctx.logger.info(f"Haggled with {len(result.outcomes)} item(s); {agreed} agreed")
        await ctx.send(sender, result)

    agent.include(buyer, publish_manifest=not offline)
    agent.include(shop)
    return shopper
