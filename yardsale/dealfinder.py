"""The Deal Finder agent ("yardsale-dealfinder"): the best deals on other sites, scored.

An agent sends it a FindDeals (what the buyer wants, their max, and a ZIP code) and gets a
DealsFound back: the top listings across eBay, Craigslist, OfferUp, and Facebook Marketplace,
each with a score and a one-line reason, plus the market price they were judged against. The
work is elsewhere.DealFinder (the searches, the eBay sold comps, the cache) and deals.py (the
scoring); this module is the agent around it.

Other agents talk to it the way the Market talks to the Identifier, Researcher, and Appraiser: a
request with a request_id, a reply with the same ID, or a ServiceError. The reply is the
search's first answer (elsewhere.Search.early): every site that answered within
elsewhere.SOURCE_WITHIN seconds (about 20), with sites still searching marked "pending" in
`sites`. The search keeps going after the reply, so asking again with the same words, budget,
and ZIP gets the slower sites too, from the cache.

Every new search from the chat and from @yardsale's API comes here: the Market sends this agent
a FindDeals (through its Agentverse mailbox when relay.py is on, so Agentverse sees it) and shows
the buyer this agent's answer. The agent and the Market share one DealFinder, so the Market then
follows the same running search for slower sites' deals, and repeats are answered from its cache
without a message. If this agent refuses or doesn't answer, the Market searches in-process.

The buyer's max and search words are never logged, here or in the finder.
"""

from __future__ import annotations

import logging

from uagents import Agent, Context, Protocol

from yardsale import relay
from yardsale.agentverse import service_agent
from yardsale.deals import MarketPrice, ScoredDeal
from yardsale.elsewhere import DealFinder, Found, SiteResult
from yardsale.models import DealsFound, FindDeals, FoundDeal, ServiceError, SiteOutcome
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
        latitude=listing.latitude,
        longitude=listing.longitude,
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
        latitude=deal.latitude,
        longitude=deal.longitude,
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
        comps_low_cents=market.low_cents if market else None,
        comps_high_cents=market.high_cents if market else None,
        comps_basis=market.basis if market else "",
        sites=[
            SiteOutcome(source=s.source, status=s.status, raw=s.raw, kept=s.kept, reason=s.reason)
            for s in found.sites
        ],
        place=found.place,
    )


def found_from(msg: DealsFound) -> Found:
    market = None
    if msg.typical_cents is not None:
        plural = "s" if msg.comps_used != 1 else ""
        market = MarketPrice(
            typical_cents=msg.typical_cents,
            low_cents=msg.comps_low_cents if msg.comps_low_cents is not None else msg.typical_cents,
            high_cents=(
                msg.comps_high_cents if msg.comps_high_cents is not None else msg.typical_cents
            ),
            sales=msg.comps_used,
            basis=msg.comps_basis or f"{msg.comps_used} eBay sale{plural} in the last 90 days",
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
        pending=tuple(s.source for s in msg.sites if s.status == "pending"),
        sites=tuple(SiteResult(s.source, s.status, s.raw, s.kept, s.reason) for s in msg.sites),
        place=msg.place,
    )


# --- The agent ---------------------------------------------------------------------------------


def build_dealfinder(finder: DealFinder, offline: bool) -> Agent:
    agent = service_agent(
        "dealfinder", "yardsale-dealfinder", "ys-dealfinder", DESCRIPTION, offline
    )
    proto = Protocol(name="YardsaleDeals", version="0.1.0")

    @proto.on_message(FindDeals, replies={DealsFound, ServiceError})
    async def handle(ctx: Context, sender: str, msg: FindDeals) -> None:
        if not relay.claim(msg.request_id):  # the copy that came the other way; already handled
            return
        try:
            # Start the search, or join the same one if it's cached or running, and answer by
            # its deadline: slower sites keep searching and are "pending" in the reply.
            running = finder.search(msg.query, msg.max_cents, msg.zip[:10])
            found = await running.early()
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
            f"{len(found.pending)} still searching, "
            f"{'with' if found.market else 'without'} a market price"
        )
        await ctx.send(sender, deals_found(msg.request_id, found))

    agent.include(proto, publish_manifest=not offline)
    return agent
