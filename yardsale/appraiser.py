"""Appraiser agent: which eBay sales are the same product as the seller's item, and what's it worth.

Claude compares the seller's photo and details with each sold listing's title, condition, and
photo. The price math is plain code: the median of same-product sales sets the asking price.
"""

from __future__ import annotations

import math
from collections.abc import Awaitable, Callable
from typing import Any

import anthropic
from pydantic import BaseModel
from uagents import Agent, Context, Protocol

from yardsale import llm, relay
from yardsale.agentverse import service_agent
from yardsale.common import dollars
from yardsale.models import (
    Appraisal,
    AppraiseRequest,
    Comp,
    ItemDetails,
    MatchedComp,
    ServiceError,
)
from yardsale.pricing import suggested_prices

MAX_COMP_PHOTOS = 10
SAME_PRODUCT_NEEDED = 3  # fewer same-product sales than this, and similar items count too
SIMILAR_MIN = 0.6  # inclusive: a similarity of exactly 0.6 counts


class Judgement(BaseModel):
    index: int
    same_product: bool
    similarity: float
    reason: str


class _Judgements(BaseModel):
    judgements: list[Judgement]


# (item, seller image_b64, media_type, comps) -> one judgement per comp
Judge = Callable[[ItemDetails, str, str, list[Comp]], Awaitable[list[Judgement]]]

SYSTEM = """You decide which recent eBay sales are fair price references for a seller's \
secondhand item. You'll see the seller's photo and details, then numbered sold listings with \
titles, conditions, prices, and some listing photos."""

INSTRUCTIONS = """Return one judgement for every numbered listing, using its number as index.

- same_product: true only if it's the same product as the seller's item: same brand, model, and \
the variant or size where that changes the price. Lots, bundles, parts-only, broken, or \
accessory-only listings are not the same product.
- similarity: 0 to 1, how good a price reference the sale is for the seller's item, counting \
product match and condition. A similar item from another brand can still be a weak reference.
- reason: under 12 words."""


def _item_text(item: ItemDetails) -> str:
    lines = [
        f"Title: {item.title}",
        f"Brand: {item.brand or 'unknown'}",
        f"Model: {item.model or 'unknown'}",
        f"Category: {item.category}",
        f"Condition: {item.condition}",
    ]
    if item.details:
        lines.append("Details: " + "; ".join(item.details))
    return "\n".join(lines)


def _comp_line(index: int, comp: Comp) -> str:
    condition = comp.condition or "condition unknown"
    return f"[{index}] {comp.title} | {condition} | {dollars(comp.price_cents)}"


async def judge_with_claude(
    item: ItemDetails, image_b64: str, media_type: str, comps: list[Comp]
) -> list[Judgement]:
    if not comps:
        return []
    content: list[dict[str, Any]] = [
        {"type": "text", "text": "The seller's item:\n" + _item_text(item)},
        llm.image_block(image_b64, media_type),
        {
            "type": "text",
            "text": "eBay sold listings:\n"
            + "\n".join(_comp_line(i, c) for i, c in enumerate(comps)),
        },
    ]
    photos: list[dict[str, Any]] = []
    for i, comp in enumerate(comps):
        if comp.image_url and len(photos) < 2 * MAX_COMP_PHOTOS:
            photos += [
                {"type": "text", "text": f"Photo of listing [{i}]:"},
                llm.url_image_block(comp.image_url),
            ]
    ask = {"type": "text", "text": INSTRUCTIONS}
    try:
        result = await llm.ask(SYSTEM, [*content, *photos, ask], _Judgements)
    except anthropic.BadRequestError:
        if not photos:
            raise
        # One listing photo that can't be fetched fails the whole request, so retry on text alone.
        result = await llm.ask(SYSTEM, [*content, ask], _Judgements)
    return result.judgements


def appraise(request_id: str, comps: list[Comp], judgements: list[Judgement]) -> Appraisal:
    """The price math, in plain code. Claude's judgements only say which sales match.

    - 3 or more same-product sales: price from those only. Confidence is high with 5 or more.
    - Fewer: add similar items (similarity 0.6 or more), and confidence is low.
    - Neither: no price.
    - Accepted Best Offer sales show the list price, not what was paid, so they're left out
      whenever the chosen sales include any others.
    """
    matches: dict[int, MatchedComp] = {}
    for j in judgements:
        if 0 <= j.index < len(comps) and j.index not in matches:
            similarity = j.similarity if math.isfinite(j.similarity) else 0.0
            matches[j.index] = MatchedComp(
                comp=comps[j.index],
                same_product=j.same_product,
                similarity=min(1.0, max(0.0, similarity)),
                reason=j.reason,
            )
    same = [m for m in matches.values() if m.same_product]
    similar = [m for m in matches.values() if not m.same_product and m.similarity >= SIMILAR_MIN]

    if len(same) >= SAME_PRODUCT_NEEDED:
        used = same
        basis = f"{len(same)} eBay sales of the same product in the last 90 days"
        confidence = "high" if len(same) >= 5 else "medium"
    elif same or similar:
        used = same + similar
        basis = (
            f"{len(same)} eBay sales of the same product and {len(similar)} of similar items, "
            "so treat this as a rough estimate"
        )
        confidence = "low"
    else:
        return Appraisal(
            request_id=request_id,
            comps_considered=len(comps),
            matches=[],
            basis="No eBay sales in the last 90 days were close enough to price this.",
            confidence="none",
            asking_cents=None,
            floor_cents=None,
            low_cents=None,
            high_cents=None,
        )

    used.sort(key=lambda m: (m.same_product, m.similarity), reverse=True)
    # A Best Offer sale shows the list price, not what the buyer paid, so skip those when we can.
    prices = [m.comp.price_cents for m in used if not m.comp.best_offer]
    if prices and len(prices) < len(used):
        skipped = len(used) - len(prices)
        basis += f" (left out {skipped} Best Offer sale{'s' if skipped != 1 else ''})"
    prices = prices or [m.comp.price_cents for m in used]
    asking, floor, low, high = suggested_prices(prices)
    return Appraisal(
        request_id=request_id,
        comps_considered=len(comps),
        matches=used,
        basis=basis,
        confidence=confidence,
        asking_cents=asking,
        floor_cents=floor,
        low_cents=low,
        high_cents=high,
    )


def build_appraiser(judge: Judge, offline: bool) -> Agent:
    agent = service_agent(
        "appraiser",
        "yardsale-appraiser",
        "ys-appraiser",
        "Compares an item's photo with eBay sold listings and sets an asking price and a floor.",
        offline,
    )
    proto = Protocol(name="YardsaleAppraise", version="0.1.0")

    @proto.on_message(AppraiseRequest, replies={Appraisal, ServiceError})
    async def handle(ctx: Context, sender: str, msg: AppraiseRequest) -> None:
        if not relay.claim(msg.request_id):
            return  # another copy of this request arrived first (relay.py)
        try:
            judgements = await judge(msg.item, msg.image_b64, msg.media_type, msg.comps)
        except llm.LLMError as ex:  # no key, or Claude declined
            ctx.logger.warning(f"Appraisal failed: {ex}")
            await ctx.send(sender, ServiceError(request_id=msg.request_id, reason=str(ex)[:300]))
            return
        except Exception as ex:  # report every failure to the caller instead of going silent
            ctx.logger.exception("Appraisal failed")
            await ctx.send(sender, ServiceError(request_id=msg.request_id, reason=str(ex)[:300]))
            return
        appraisal = appraise(msg.request_id, msg.comps, judgements)
        ctx.logger.info(f"Appraised {msg.item.title!r}: {appraisal.basis}")
        await ctx.send(sender, appraisal)

    agent.include(proto, publish_manifest=not offline)
    return agent
