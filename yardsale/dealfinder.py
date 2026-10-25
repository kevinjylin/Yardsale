"""The Deal Finder agent ("yardsale-dealfinder"): the best deals on other sites, scored.

An agent sends it a FindDeals (what the buyer wants, their max, and a ZIP code) and gets a
DealsFound back: the top listings across eBay, Craigslist, OfferUp, and Facebook Marketplace,
each with a score and a one-line reason, plus the market price they were judged against. The
work is elsewhere.DealFinder (the searches, the eBay sold comps, the cache) and deals.py (the
scoring); this module is the agent around it.

Other agents talk to it the way the Market talks to the Identifier, Researcher, and Appraiser: a
request with a request_id, a reply with the same ID, or a ServiceError. The reply waits for
every site (each gives up after elsewhere.SOURCE_LIMIT seconds).

The chat and @yardsale's API don't go through this agent. Messages between agents in one Bureau
are delivered in-process (uAgents' dispatcher, live or offline), so the hop cost under a
millisecond, but its one reply can only come once every site has answered: a buyer would wait
for the slowest site, with a 240-second timeout as the only limit. They use the Market's
DealFinder directly instead, which answers by its deadline and sends slower sites' deals later.
This agent shares that DealFinder, so its searches and cache are the same ones.

The buyer's max and search words are never logged, here or in the finder.
"""

from __future__ import annotations

import logging

from uagents import Agent, Context, Protocol

from yardsale.agentverse import service_agent
from yardsale.deals import MarketPrice, ScoredDeal
from yardsale.elsewhere import DealFinder, Found
from yardsale.models import DealsFound, FindDeals, FoundDeal, ServiceError
from yardsale.sources import ExternalListing, SourceError

log = logging.getLogger("yardsale.dealfinder")

DESCRIPTION = (
    "Finds the best-priced, best-quality secondhand deals for a search across eBay, Craigslist, "
    "OfferUp, and Facebook Marketplace, under a buyer's max. Prices each against what the item "
    "actually sold for on eBay in the last 90 days and explains why it's a good deal."
)

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
        # A listing with no price travels as 0, flagged; it's unpriced again here.
        price_cents=None if "no_price" in deal.flags else deal.price_cents,
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
