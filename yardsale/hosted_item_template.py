"""Template for an item agent hosted on Agentverse. Never imported: hosting.py builds each hosted
agent's code from the part below the "hosted code" line, pasting in haggle.py, the message
classes from models.py, and the item's settings (CONFIG) in place of the imports above it.

The hosted agent haggles exactly like an item agent in the Market's Bureau (negotiation.py),
keeping each buyer's haggle in its Agentverse storage. Its floor and private gap come from the
YARDSALE_LIMITS secret, or from its storage if the secret isn't readable. When a buyer accepts,
it marks itself sold, answers with a Deal, and tells the Market (ItemSold), which records the
deal and asks the buyer for payment.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any
from uuid import uuid4

from uagents import Agent, Context, Protocol
from uagents_core.protocol import ProtocolSpecification

from yardsale.haggle import SellerState, seller_move
from yardsale.models import AcceptCounter, Counter, Deal, ItemSold, Offer, OfferRejected

CONFIG: dict[str, Any] = {}

# --- hosted code ---

COUNTER_TTL = 24 * 3600

HAGGLE_SPEC = ProtocolSpecification(
    name="YardsaleHaggle",
    version="1.0.0",
    interactions={
        Offer: {Counter, OfferRejected},
        AcceptCounter: {Deal, OfferRejected},
        Counter: set(),
        Deal: set(),
        OfferRejected: set(),
    },
    roles={"item": {Offer, AcceptCounter}, "buyer": {Counter, Deal, OfferRejected}},
)

item = Protocol(spec=HAGGLE_SPEC, role="item")


def _limits(ctx: Context) -> tuple[int, int] | None:
    """(floor, gap) in cents, from the secret or, failing that, the agent's storage."""
    raw: Any = os.environ.get("YARDSALE_LIMITS") or ctx.storage.get("limits")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return None
    if not isinstance(raw, dict):
        return None
    try:
        return int(raw["floor_cents"]), int(raw["gap_cents"])
    except (KeyError, TypeError, ValueError):
        return None


def _buyer(sender: str, buyer_ref: str) -> str:
    """Only Yardsale's Market and buyer agent may haggle for a chat buyer."""
    if buyer_ref and sender in (CONFIG["market"], CONFIG["shopper"]):
        return f"chat:{buyer_ref}"
    return f"agent:{sender}"


def _iso(seconds: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(seconds))


def _rejected(msg: Offer | AcceptCounter, code: str, reason: str) -> OfferRejected:
    return OfferRejected(item_id=msg.item_id, reason=reason, code=code, request_id=msg.request_id)


def _unavailable(ctx: Context, msg: Offer | AcceptCounter) -> OfferRejected | None:
    if msg.item_id != CONFIG["item_id"]:
        return _rejected(msg, "unknown_item", "unknown item")
    if ctx.storage.get("sold"):
        return _rejected(msg, "unavailable", "this item is no longer for sale")
    return None


@item.on_message(Offer)
async def on_offer(ctx: Context, sender: str, msg: Offer) -> None:
    problem = _unavailable(ctx, msg)
    limits = _limits(ctx)
    if problem is None and limits is None:
        problem = _rejected(msg, "unavailable", "this item isn't ready for offers yet")
    if problem is None and not 0 < msg.offer_cents <= 100_000_000:
        problem = _rejected(msg, "invalid_amount", "offers must be a positive price")
    if problem is not None or limits is None:
        await ctx.send(sender, problem or _rejected(msg, "unavailable", "not for sale"))
        return
    floor_cents, gap_cents = limits
    buyer = _buyer(sender, msg.buyer_ref)
    saved = ctx.storage.get(f"buyer:{buyer}") or {}
    state = SellerState()
    if saved and saved.get("expires", 0) > time.time():
        state = SellerState(saved["rounds"], saved["price"], saved["agreed"])
    move = seller_move(CONFIG["asking_cents"], floor_cents, gap_cents, msg.offer_cents, state)
    expires = time.time() + COUNTER_TTL
    ctx.storage.set(
        f"buyer:{buyer}",
        {
            "rounds": move.state.rounds,
            "price": move.price_cents,
            "agreed": move.agreed,
            "expires": expires,
        },
    )
    await ctx.send(
        sender,
        Counter(
            item_id=msg.item_id,
            counter_cents=move.price_cents,
            note=move.note,
            expires_at=_iso(expires),
            request_id=msg.request_id,
            agreed=move.agreed,
        ),
    )


@item.on_message(AcceptCounter)
async def on_accept(ctx: Context, sender: str, msg: AcceptCounter) -> None:
    problem = _unavailable(ctx, msg)
    buyer = _buyer(sender, msg.buyer_ref)
    saved = ctx.storage.get(f"buyer:{buyer}")
    if problem is None and not saved:
        problem = _rejected(msg, "no_counter", "no counter-offer was made to you")
    if problem is None and saved["price"] != msg.counter_cents:
        problem = _rejected(
            msg, "counter_changed", "that isn't your latest counter-offer; accept the newest one"
        )
    if problem is None and saved["expires"] <= time.time():
        problem = _rejected(msg, "counter_expired", "that counter-offer expired; make a new offer")
    if problem is not None:
        await ctx.send(sender, problem)
        return
    deal_id = f"deal-{uuid4().hex[:12]}"
    ctx.storage.set("sold", {"deal_id": deal_id, "buyer": buyer, "price": msg.counter_cents})
    await ctx.send(
        sender,
        Deal(
            item_id=msg.item_id,
            price_cents=msg.counter_cents,
            deal_id=deal_id,
            title=CONFIG["title"],
            request_id=msg.request_id,
        ),
    )
    await ctx.send(
        CONFIG["market"],
        ItemSold(item_id=msg.item_id, deal_id=deal_id, buyer=buyer, price_cents=msg.counter_cents),
    )
    ctx.logger.info(f"Sold for {msg.counter_cents} cents ({deal_id})")


agent = Agent()
agent.include(item, publish_manifest=True)

if __name__ == "__main__":
    agent.run()
