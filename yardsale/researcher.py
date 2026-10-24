"""Researcher agent: search query in, recent eBay sold listings out (Apify).

Uses the `caffein.dev/ebay-sold-listings` Apify actor, the most used eBay sold-listings actor on
Apify. It's pay-per-result, about $0.004 per listing, so a 20-result search costs about $0.08.
"""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

import aiohttp
from uagents import Agent, Context, Protocol

from yardsale.agentverse import service_agent
from yardsale.models import Comp, ResearchRequest, ResearchResult, ServiceError

# (query, condition, max_results) -> sold listings
Research = Callable[[str, str, int], Awaitable[list[Comp]]]

ACTOR = os.getenv("YARDSALE_APIFY_ACTOR", "caffein.dev~ebay-sold-listings")
RUN_URL = f"https://api.apify.com/v2/acts/{ACTOR}/run-sync-get-dataset-items"
MAX_CHARGE_USD = "0.50"  # hard cap per search, enforced by Apify
MAX_PRICE_USD = 1_000_000  # anything above is a parsing error, not a sale


class ResearchError(RuntimeError):
    pass


def _cents(value: Any) -> int | None:
    if value in (None, "") or isinstance(value, bool):
        return None
    try:
        amount = Decimal(str(value).replace(",", "").replace("$", "").strip())
    except InvalidOperation:
        return None
    if not amount.is_finite() or not 0 <= amount <= MAX_PRICE_USD:
        return None
    return int((amount * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def _web_link(value: Any) -> str:
    link = str(value or "").strip()
    return link if link.startswith(("https://", "http://")) else ""


def parse_comp(raw: Any) -> Comp | None:
    """One actor result -> Comp. Returns None for results we can't price in US dollars."""
    if not isinstance(raw, dict):
        return None
    price = _cents(raw.get("soldPrice"))
    if price is None or price <= 0 or (raw.get("soldCurrency") or "USD") != "USD":
        return None
    title = " ".join(str(raw.get("title") or "").split())[:200]
    if not title:
        return None
    return Comp(
        title=title,
        price_cents=price,
        shipping_cents=_cents(raw.get("shippingPrice")),
        condition=str(raw.get("condition") or "")[:60],
        sold_at=str(raw.get("endedAt") or "")[:10],
        url=_web_link(raw.get("url")),
        image_url=_web_link(raw.get("thumbnailUrl")) or None,
        best_offer=bool(raw.get("isBestOfferAccepted")),
    )


async def search_ebay_sold(query: str, condition: str, max_results: int) -> list[Comp]:
    token = os.getenv("APIFY_TOKEN", "").strip()
    if not token:
        raise ResearchError("APIFY_TOKEN is not set on the server (see .env.example)")
    if not query.strip():
        raise ResearchError("there's nothing to search eBay for")
    body = {
        "keywords": [query],
        "count": max_results,
        "daysToScrape": 90,
        "itemCondition": condition if condition in ("new", "used") else "any",
        "ebaySite": "ebay.com",
    }
    params = {"timeout": "120", "maxItems": str(max_results), "maxTotalChargeUsd": MAX_CHARGE_USD}
    headers = {"Authorization": f"Bearer {token}"}
    timeout = aiohttp.ClientTimeout(total=180)
    async with (
        aiohttp.ClientSession(timeout=timeout) as session,
        session.post(RUN_URL, params=params, json=body, headers=headers) as resp,
    ):
        if resp.status not in (200, 201):
            raise ResearchError(f"Apify returned {resp.status}: {(await resp.text())[:200]}")
        items = await resp.json()

    if not isinstance(items, list):
        raise ResearchError(f"Apify returned {type(items).__name__}, not a list of sales")
    comps: list[Comp] = []
    seen: set[str] = set()
    for raw in items:
        comp = parse_comp(raw)
        if comp is None:
            continue
        # Same listing twice in the results. Listings without a link are told apart by content.
        key = comp.url or f"{comp.title}|{comp.price_cents}|{comp.sold_at}"
        if key not in seen:
            seen.add(key)
            comps.append(comp)
    return comps


def build_researcher(research: Research, offline: bool) -> Agent:
    agent = service_agent(
        "researcher",
        "yardsale-researcher",
        "Finds recent eBay sold listings for a product search, with sold prices and photos.",
        offline,
    )
    proto = Protocol(name="YardsaleResearch", version="0.1.0")

    @proto.on_message(ResearchRequest, replies={ResearchResult, ServiceError})
    async def handle(ctx: Context, sender: str, msg: ResearchRequest) -> None:
        try:
            comps = await research(msg.query, msg.condition, max(1, min(msg.max_results, 50)))
        except ResearchError as ex:
            ctx.logger.warning(f"eBay research failed: {ex}")
            await ctx.send(sender, ServiceError(request_id=msg.request_id, reason=str(ex)[:300]))
            return
        except Exception as ex:  # report every failure to the caller instead of going silent
            ctx.logger.exception("eBay research failed")
            await ctx.send(sender, ServiceError(request_id=msg.request_id, reason=str(ex)[:300]))
            return
        ctx.logger.info(f"{len(comps)} eBay sold listings for {msg.query!r}")
        await ctx.send(
            sender, ResearchResult(request_id=msg.request_id, query=msg.query, comps=comps)
        )

    agent.include(proto, publish_manifest=not offline)
    return agent
