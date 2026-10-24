"""Researcher agent: search query in, recent eBay sold listings out (Apify).

Uses the `caffein.dev/ebay-sold-listings` Apify actor, the most used eBay sold-listings actor on
Apify. It's pay-per-result, about $0.004 per listing, so a 20-result search costs about $0.08.
"""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable
from decimal import Decimal, InvalidOperation
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


class ResearchError(RuntimeError):
    pass


def _cents(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(Decimal(str(value).replace(",", "").replace("$", "").strip()) * 100)
    except InvalidOperation:
        return None


def parse_comp(raw: dict[str, Any]) -> Comp | None:
    """One actor result -> Comp. Returns None for results we can't price in US dollars."""
    price = _cents(raw.get("soldPrice"))
    if price is None or price <= 0 or (raw.get("soldCurrency") or "USD") != "USD":
        return None
    return Comp(
        title=str(raw.get("title") or "").strip()[:200],
        price_cents=price,
        shipping_cents=_cents(raw.get("shippingPrice")),
        condition=str(raw.get("condition") or ""),
        sold_at=str(raw.get("endedAt") or "")[:10],
        url=str(raw.get("url") or ""),
        image_url=raw.get("thumbnailUrl") or None,
        best_offer=bool(raw.get("isBestOfferAccepted")),
    )


async def search_ebay_sold(query: str, condition: str, max_results: int) -> list[Comp]:
    token = os.getenv("APIFY_TOKEN", "").strip()
    if not token:
        raise ResearchError("APIFY_TOKEN is not set")
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

    comps: list[Comp] = []
    seen: set[str] = set()
    for raw in items if isinstance(items, list) else []:
        comp = parse_comp(raw)
        if comp is not None and comp.url not in seen:
            seen.add(comp.url)
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
