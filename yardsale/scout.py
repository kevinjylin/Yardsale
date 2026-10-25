"""Market Scout agent: a product search in, what similar items are listed for right now out.

The Researcher finds what a product *sold* for on eBay. The Scout finds what competitors are
*asking* for it today on Craigslist, OfferUp, Facebook Marketplace, and eBay, through the same
Apify-backed sources buyers' searches use (sources.py and friends; never direct scraping). The
Market asks both at once after the Identifier names the item, and the Appraiser's price still
comes from sold prices: asking prices only inform the suggested range, the market line item
agents cite while haggling, and the warning a seller gets when their own price is far off
(pricing.market_view).

Every site is searched at the same time, and a site that fails is named in the report instead
of failing it, so a Scout problem never blocks a proposal. Each search is a paid Apify run per
site (a few cents each), so the sources it gets are the server's choice (bureau_server), and
with none it reports nothing.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from typing import Any

from uagents import Agent, Context, Protocol

from yardsale.agentverse import service_agent
from yardsale.deals import ACCESSORIES, RED_FLAGS
from yardsale.elsewhere import search_words
from yardsale.models import ScoutListing, ScoutReport, ScoutRequest, ServiceError, SourcePrices
from yardsale.pricing import price_range, usd
from yardsale.search import words
from yardsale.sources import ExternalListing, Source, SourceError, deduplicated

log = logging.getLogger("yardsale.scout")

DESCRIPTION = (
    "Yardsale's market scout. Finds what similar items are listed for right now on Craigslist, "
    "OfferUp, Facebook Marketplace, and eBay, so a seller's price can be checked against live "
    "competition, not only past sales."
)
CLOSEST = 3  # listings shown per site, nearest the median
MIN_PRICE_CENTS = 100  # $0 and $1 posts are "contact me" placeholders, not prices
MAX_PER_SOURCE = 50
_ZIP = re.compile(r"\d{5}")


def seller_zip() -> str:
    """YARDSALE_ZIP: where the server's sellers are, for the local sites. "" means each
    source's own default."""
    raw = os.getenv("YARDSALE_ZIP", "").strip()
    return raw if _ZIP.fullmatch(raw) else ""


def scope_for(source: str, zip_code: str) -> str:
    """What to pass a source as `where`. OfferUp searches around a ZIP code; the others take a
    city or site name that only their own environment settings choose."""
    if source == "offerup" and _ZIP.fullmatch(zip_code or ""):
        return zip_code
    return ""


def relevant(query: str, listings: list[ExternalListing]) -> list[ExternalListing]:
    """The listings that look like the same kind of item with a real price: at least half of
    the search words in the title, and none of the words that make it a part or an add-on."""
    wanted = set(search_words(query).split())
    kept: list[ExternalListing] = []
    for listing in deduplicated(listings):
        if listing.price_cents is None or listing.price_cents < MIN_PRICE_CENTS:
            continue
        have = set(words(listing.title))
        if (have & (RED_FLAGS | ACCESSORIES)) - wanted:
            continue
        if wanted and len(wanted - have) * 2 > len(wanted):
            continue
        kept.append(listing)
    return kept


def source_prices(source: str, query: str, listings: list[ExternalListing]) -> SourcePrices | None:
    """One site's asking prices for the item, or None when nothing there looks like it."""
    kept = relevant(query, listings)
    if not kept:
        return None
    prices = [listing.price_cents for listing in kept if listing.price_cents is not None]
    low, middle, high = price_range(prices)
    with_links = [listing for listing in kept if listing.url] or kept
    with_links.sort(key=lambda listing: (abs((listing.price_cents or 0) - middle), listing.url))
    return SourcePrices(
        source=source,
        count=len(prices),
        low_cents=low,
        median_cents=middle,
        high_cents=high,
        closest=[
            ScoutListing(
                source=source,
                title=listing.title[:120],
                price_cents=listing.price_cents or 0,
                url=listing.url,
                location=listing.location[:80],
            )
            for listing in with_links[:CLOSEST]
        ],
        prices_cents=sorted(prices),
    )


async def _one(
    name: str, source: Source, query: str, where: str, limit: int
) -> list[ExternalListing] | None:
    try:
        return await source(query, where, limit)
    except SourceError as ex:
        log.warning(f"scout: {name} couldn't be searched ({ex})")
    except Exception:
        log.exception(f"scout: {name} failed")
    return None


async def scout_prices(
    sources: dict[str, Source], request: ScoutRequest, within: float | None = None
) -> tuple[list[SourcePrices], list[str]]:
    """Every site at once. (prices per site that had any, sites that couldn't be searched)

    With `within`, a site still searching after that many seconds counts as failed and the
    rest is returned, so a caller with its own deadline gets what's ready."""
    names = list(sources)
    limit = max(1, min(request.max_results, MAX_PER_SOURCE))
    tasks: list[asyncio.Task[list[ExternalListing] | None]] = [
        asyncio.ensure_future(
            _one(name, sources[name], request.query, scope_for(name, request.zip_code), limit)
        )
        for name in names
    ]
    if tasks:
        await asyncio.wait(tasks, timeout=within)
    found: list[SourcePrices] = []
    failed: list[str] = []
    for name, task in zip(names, tasks, strict=True):
        answer = task.result() if task.done() and not task.cancelled() else None
        if not task.done():
            task.cancel()
            log.warning(f"scout: {name} didn't answer within {within}s")
        if answer is None:
            failed.append(name)
            continue
        prices = source_prices(name, request.query, answer)
        if prices is not None:
            found.append(prices)
    return found, failed


# The sources the server gave its Scout (build_scout), so local_prices can use the same ones.
SOURCES: dict[str, Source] = {}
LOCAL_PRICES_WITHIN = 18.0  # seconds; @yardsale's API gives a price check about 20


async def local_prices(query: str, zip_code: str = "") -> dict[str, Any]:
    """Live asking prices for @yardsale's price check (business_api.py): a JSON-able block with
    the count, low, median, and high asking price across every site, which sites answered, and
    up to 3 example listings. Uses the sources the server's Scout has; with none, the block says
    so (count 0, no sites). Answers within LOCAL_PRICES_WITHIN seconds."""
    request = ScoutRequest(request_id="local", query=query.strip(), zip_code=zip_code or "")
    found: list[SourcePrices] = []
    failed: list[str] = []
    if request.query and SOURCES:
        found, failed = await scout_prices(SOURCES, request, LOCAL_PRICES_WITHIN)
    prices = sorted(price for source in found for price in source.prices_cents)
    middle = 0
    stats: dict[str, Any] = {"count": 0, "low_usd": None, "median_usd": None, "high_usd": None}
    if prices:
        low, middle, high = price_range(prices)
        stats = {
            "count": len(prices),
            "low_usd": low / 100,
            "median_usd": middle / 100,
            "high_usd": high / 100,
            "low": usd(low),
            "median": usd(middle),
            "high": usd(high),
        }
    examples = [listing for source in found for listing in source.closest if listing.url]
    examples.sort(key=lambda listing: (abs(listing.price_cents - middle), listing.url))
    return {
        "query": request.query,
        **stats,
        "sites": [source.source for source in found],
        "failed": failed,
        "examples": [
            {
                "title": listing.title,
                "price_usd": listing.price_cents / 100,
                "price": usd(listing.price_cents),
                "site": listing.source,
                "url": listing.url,
                "location": listing.location,
            }
            for listing in examples[:CLOSEST]
        ],
    }


def build_scout(sources: dict[str, Source], offline: bool) -> Agent:
    SOURCES.clear()
    SOURCES.update(sources)
    agent = service_agent("scout", "yardsale-scout", "ys-scout", DESCRIPTION, offline)
    proto = Protocol(name="YardsaleScout", version="0.1.0")

    @proto.on_message(ScoutRequest, replies={ScoutReport, ServiceError})
    async def handle(ctx: Context, sender: str, msg: ScoutRequest) -> None:
        if not msg.query.strip():
            await ctx.send(
                sender, ServiceError(request_id=msg.request_id, reason="nothing to search for")
            )
            return
        try:
            found, failed = await scout_prices(sources, msg)
        except Exception as ex:  # report every failure to the caller instead of going silent
            ctx.logger.exception("Scouting failed")
            await ctx.send(sender, ServiceError(request_id=msg.request_id, reason=str(ex)[:300]))
            return
        counted = ", ".join(f"{p.source} {p.count}" for p in found) or "none"
        ctx.logger.info(
            f"Live asking prices for {msg.query!r}: {counted}"
            + (f"; failed: {', '.join(failed)}" if failed else "")
        )
        await ctx.send(
            sender,
            ScoutReport(request_id=msg.request_id, query=msg.query, sources=found, failed=failed),
        )

    agent.include(proto, publish_manifest=not offline)
    return agent
