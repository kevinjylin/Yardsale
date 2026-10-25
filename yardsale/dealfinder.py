"""The Deal Finder agent ("yardsale-dealfinder"): the best deals on other sites, scored.

The Market sends it a FindDeals (what the buyer wants, their max, and a ZIP code) and gets a
DealsFound back: the top listings across eBay, Craigslist, OfferUp, and Facebook Marketplace,
each with a score and a one-line reason, plus the market price they were judged against. The
work is elsewhere.DealFinder (the searches, the eBay sold comps, the cache) and deals.py (the
scoring); this module is the agent around it and the Market's client for it.

The Market talks to it the way it talks to the Identifier, Researcher, and Appraiser: a request
with a request_id, a reply with the same ID, or a ServiceError. `DealsClient` keeps the
DealFinder interface the chat and @yardsale's API already use (`enabled`, `site_names`, `start`,
`find`), so they don't know whether the search runs in this process or on another machine.

The buyer's max and search words are never logged, here or in the finder.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from uuid import uuid4

from uagents import Agent, Context, Protocol

from yardsale.agentverse import service_agent
from yardsale.common import PipelineError
from yardsale.deals import MarketPrice, ScoredDeal
from yardsale.elsewhere import CACHE_FOR, DealFinder, Found
from yardsale.models import DealsFound, FindDeals, FoundDeal, ServiceError
from yardsale.sources import ExternalListing, Source, SourceError

log = logging.getLogger("yardsale.dealfinder")

DEALS_TIMEOUT = 240.0  # seconds the Market waits: every source's actor can take a minute or two

DESCRIPTION = (
    "Finds the best-priced, best-quality secondhand deals for a search across eBay, Craigslist, "
    "OfferUp, and Facebook Marketplace, under a buyer's max. Prices each against what the item "
    "actually sold for on eBay in the last 90 days and explains why it's a good deal."
)

# Market -> Deal Finder -> Market, with the Market's context; bureau_server wires it.
Ask = Callable[[FindDeals], Awaitable[DealsFound]]


# --- Between the finder's result and the message ------------------------------------------------


def to_message(deal: ScoredDeal) -> FoundDeal:
    listing = deal.listing
    return FoundDeal(
        source=listing.source,
        external_id=listing.external_id,
        title=listing.title,
        url=listing.url,
        price_cents=listing.price_cents or 0,
        shipping_cents=listing.shipping_cents,
        total_cents=deal.total_cents,
        location=listing.location,
        condition=listing.condition,
        image_url=listing.image_url,
        posted_at=listing.posted_at,
        accepts_offers=listing.accepts_offers,
        photo_count=listing.photo_count,
        seller_rating=listing.seller_rating,
        score=deal.score,
        versus_market_pct=deal.versus_market_pct,
        why=deal.why,
        flags=list(deal.flags),
    )


def from_message(deal: FoundDeal) -> ScoredDeal:
    listing = ExternalListing(
        source=deal.source,
        external_id=deal.external_id,
        title=deal.title,
        price_cents=deal.price_cents,
        url=deal.url,
        location=deal.location,
        image_url=deal.image_url,
        posted_at=deal.posted_at,
        condition=deal.condition,
        shipping_cents=deal.shipping_cents,
        accepts_offers=deal.accepts_offers,
        photo_count=deal.photo_count,
        seller_rating=deal.seller_rating,
    )
    return ScoredDeal(
        listing, deal.total_cents, deal.score, deal.versus_market_pct, deal.why, tuple(deal.flags)
    )


def deals_found(request_id: str, found: Found) -> DealsFound:
    market = found.market
    return DealsFound(
        request_id=request_id,
        query=found.query,
        max_cents=found.budget_cents,
        deals=[to_message(deal) for deal in found.deals],
        searched=list(found.searched),
        failed=list(found.failed),
        considered=found.considered,
        typical_cents=market.typical_cents if market else None,
        comps_used=market.sales if market else 0,
        comps_note=found.comps_note,
    )


def found_from(msg: DealsFound) -> Found:
    market = None
    if msg.typical_cents is not None:
        plural = "s" if msg.comps_used != 1 else ""
        market = MarketPrice(
            typical_cents=msg.typical_cents,
            low_cents=msg.typical_cents,
            high_cents=msg.typical_cents,
            sales=msg.comps_used,
            basis=f"{msg.comps_used} eBay sale{plural} in the last 90 days",
        )
    return Found(
        query=msg.query,
        budget_cents=msg.max_cents,
        deals=tuple(from_message(deal) for deal in msg.deals),
        searched=tuple(msg.searched),
        failed=tuple(msg.failed),
        considered=msg.considered,
        market=market,
        comps_note=msg.comps_note,
    )


# --- The agent ---------------------------------------------------------------------------------


def build_dealfinder(finder: DealFinder, offline: bool) -> Agent:
    agent = service_agent(
        "dealfinder", "yardsale-dealfinder", "ys-dealfinder", DESCRIPTION, offline
    )
    proto = Protocol(name="YardsaleDeals", version="0.1.0")

    @proto.on_message(FindDeals, replies={DealsFound, ServiceError})
    async def handle(ctx: Context, sender: str, msg: FindDeals) -> None:
        try:
            found = await finder.find(msg.query, msg.max_cents, msg.zip[:10])
        except SourceError as ex:  # nothing to search, or no sources
            ctx.logger.warning(f"Deal search refused: {ex}")
            await ctx.send(sender, ServiceError(request_id=msg.request_id, reason=str(ex)[:300]))
            return
        except Exception as ex:  # report every failure to the caller instead of going silent
            ctx.logger.exception("Deal search failed")
            await ctx.send(sender, ServiceError(request_id=msg.request_id, reason=str(ex)[:300]))
            return
        ctx.logger.info(
            f"{len(found.deals)} deal(s) from {len(found.searched)} source(s), "
            f"{'with' if found.market else 'without'} a market price"
        )
        await ctx.send(sender, deals_found(msg.request_id, found))

    agent.include(proto, publish_manifest=not offline)
    return agent


# --- The Market's side -------------------------------------------------------------------------


class DealsClient(DealFinder):
    """The Market's deal finder: the same interface, with the search done by the Deal Finder
    agent. Keeps its own short cache, so one chat asking twice sends one request. A request
    the agent refuses or doesn't answer is a SourceError, like a source that's off."""

    def __init__(
        self,
        sources: dict[str, Source],
        ask: Ask,
        cache_for: float = CACHE_FOR,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        super().__init__(sources, cache_for=cache_for, clock=clock)
        self.ask = ask

    async def _search(self, query: str, budget_cents: int | None, zip_code: str) -> Found:
        request = FindDeals(
            request_id=uuid4().hex, query=query, max_cents=budget_cents, zip=zip_code
        )
        try:
            return found_from(await self.ask(request))
        except PipelineError as ex:
            raise SourceError(str(ex)) from ex
