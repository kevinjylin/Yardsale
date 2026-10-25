"""eBay source: a buyer's search in, listings that are on sale right now out (Apify).

This is the buying side, and it is not the same job as `researcher.py`. The Researcher looks up
eBay sales that have already *ended*, to price a seller's photo. This looks up listings a buyer
could go and buy today.

Uses the `charitable_trousseau/ebay-search-scraper` actor, picked over the other eBay actors
because it returns `locationText` for each listing, so a buyer can tell a local pickup from
something two states away. About $0.0025 a listing, so a 20-result search costs about $0.05.

eBay's own Browse API would be free, but production access to it is a partner application with
an eligibility review, and the Finding API it replaced was shut off in February 2025. Scraping
needs no approval. If a Browse API keyset ever arrives, only this module has to change.

Nothing here is agent-negotiable. eBay has no public API for a buyer to send a Best Offer, and
its Order API is a limited release, so `negotiable` stays False. `accepts_offers` records that
eBay would let a *person* send an offer, which is worth telling the buyer.
"""

from __future__ import annotations

import os
from typing import Any

from yardsale.sources import (
    ExternalListing,
    SourceError,
    cents_or_none,
    deduplicated,
    first_web_link,
    one_line,
    percent,
    photo_count,
    run_actor,
    web_link,
)

SOURCE = "ebay"
ACTOR = os.getenv("YARDSALE_EBAY_ACTOR", "charitable_trousseau~ebay-search-scraper")
DEFAULT_SITE = os.getenv("YARDSALE_EBAY_SITE", "ebay.com")
MAX_CHARGE_USD = "0.25"  # hard cap per search, enforced by Apify: about 100 listings
MAX_TITLE = 200
MAX_LOCATION = 80
MAX_CONDITION = 60


def _price(raw: dict[str, Any]) -> int | None:
    """The asking price. Auctions put the current bid in `price`; `buyItNowPrice` wins when set.

    A listing priced in anything but US dollars is skipped: a buyer can't compare it with the
    rest, and we'd have to guess a rate.
    """
    currency = one_line(raw.get("currency"), 8).upper()
    if currency and currency != "USD":
        return None
    for key in ("buyItNowPrice", "price", "priceText"):
        cents = cents_or_none(raw.get(key))
        if cents is not None:
            return cents
    return None


def parse_listing(raw: Any) -> ExternalListing | None:
    """One actor result -> ExternalListing. None for anything a buyer couldn't use."""
    if not isinstance(raw, dict):
        return None
    title = one_line(raw.get("title"), MAX_TITLE)
    external_id = one_line(raw.get("itemId"), 64)
    if not title or not external_id:
        return None
    # Sponsored results are ads for other items, not answers to the search.
    if raw.get("isSponsored") is True:
        return None
    return ExternalListing(
        source=SOURCE,
        external_id=external_id,
        title=title,
        price_cents=_price(raw),
        url=web_link(raw.get("url")),
        location=one_line(raw.get("locationText"), MAX_LOCATION),
        image_url=first_web_link(raw.get("imageUrl")),
        condition=one_line(raw.get("condition"), MAX_CONDITION),
        shipping_cents=cents_or_none(raw.get("shippingCost")),
        accepts_offers=raw.get("bestOffer") is True,
        # Quality signals for the deal finder, under the names eBay scrapers tend to use; a
        # field this actor doesn't return is simply unknown.
        photo_count=photo_count(raw.get("images") or raw.get("imageUrls") or raw.get("imageUrl")),
        seller_rating=percent(raw.get("sellerFeedbackPercentage") or raw.get("feedbackPercent")),
    )


def parse_listings(items: list[Any]) -> list[ExternalListing]:
    """Every listing a buyer could use, repeats and ads dropped."""
    parsed = [parse_listing(raw) for raw in items]
    return deduplicated([listing for listing in parsed if listing is not None])


async def search_ebay(query: str, where: str, max_results: int) -> list[ExternalListing]:
    """Search eBay's live listings. `where` is an eBay site, such as "ebay.com"."""
    if not query.strip():
        raise SourceError("there's nothing to search eBay for")
    body = {
        "keywords": [query.strip()],
        "site": (where or DEFAULT_SITE).strip(),
        "maxItemsPerSearch": max_results,
    }
    items = await run_actor(ACTOR, body, max_results, MAX_CHARGE_USD)
    return parse_listings(items)
