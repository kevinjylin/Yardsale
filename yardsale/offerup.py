"""OfferUp source: a buyer's search in, local for-sale listings out (Apify).

Uses the `igolaizola/offerup-scraper` actor, picked over the other OfferUp actors because it's
the most used (about 190 users, 50 a month, 99.6% of runs succeeding, rated 5.0 when checked on
October 24) and the cheapest: pay-per-result from about $1.40 per 1,000, so a 20-result search
costs about $0.03. It searches around a ZIP code, so `where` is a ZIP code here.

Like Craigslist, OfferUp is local and in person, and the seller is a person, so nothing found
here is agent-negotiable. OfferUp has its own "make offer" button for the buyer to use.

Parsing follows the actor's documented sample output (October 24); it hasn't been run with a
real key yet.
"""

from __future__ import annotations

import os
import re
from typing import Any

from yardsale.sources import (
    ExternalListing,
    SourceError,
    cents_or_none,
    deduplicated,
    first_web_link,
    one_line,
    photo_count,
    run_actor,
    web_link,
)

SOURCE = "offerup"
ACTOR = os.getenv("YARDSALE_OFFERUP_ACTOR", "igolaizola~offerup-scraper")
DEFAULT_ZIP = os.getenv("YARDSALE_OFFERUP_ZIP", "94103")  # San Francisco, near the venue
MAX_CHARGE_USD = "0.10"  # hard cap per search, enforced by Apify: about 70 results
MAX_TITLE = 200
MAX_LOCATION = 80
_ID = re.compile(r"[0-9a-f-]{8,64}")  # OfferUp's listing IDs are UUIDs


def _item_link(external_id: str) -> str:
    """OfferUp's page for a listing, for results whose `url` is missing."""
    if _ID.fullmatch(external_id):
        return f"https://offerup.com/item/detail/{external_id}"
    return ""


def parse_listing(raw: Any) -> ExternalListing | None:
    """One actor result -> ExternalListing. None for anything a buyer couldn't act on."""
    if not isinstance(raw, dict):
        return None
    title = one_line(raw.get("title"), MAX_TITLE)
    external_id = one_line(raw.get("listingId"), 64)
    if not title or not external_id:
        return None
    found = raw.get("_details")
    details: dict[str, Any] = found if isinstance(found, dict) else {}
    if details.get("isSold") is True or details.get("isUnlisted") is True:
        return None
    # `price` is the number as a string ("60"); `formattedPrice` is what OfferUp shows ("$60").
    price = cents_or_none(raw.get("price"))
    if price is None:
        price = cents_or_none(raw.get("formattedPrice"))
    image = raw.get("image")
    return ExternalListing(
        source=SOURCE,
        external_id=external_id,
        title=title,
        price_cents=price,
        url=web_link(raw.get("url")) or _item_link(external_id),
        location=one_line(raw.get("locationName"), MAX_LOCATION),
        image_url=first_web_link(image if isinstance(image, dict) else raw.get("imageUrl")),
        condition=one_line(raw.get("conditionText") or details.get("conditionDisplayText"), 60),
        # Firm-priced posts say so; the rest take offers through OfferUp's own button.
        accepts_offers=raw.get("isFirmPrice") is not True,
        photo_count=photo_count(details.get("photos") or image or raw.get("imageUrl")),
    )


def parse_listings(items: list[Any]) -> list[ExternalListing]:
    """Every result the buyer could act on, repeats dropped."""
    parsed = [parse_listing(raw) for raw in items]
    return deduplicated([listing for listing in parsed if listing is not None])


async def search_offerup(query: str, where: str, max_results: int) -> list[ExternalListing]:
    """Search OfferUp around a ZIP code. `where` is a 5-digit ZIP; otherwise DEFAULT_ZIP."""
    if not query.strip():
        raise SourceError("there's nothing to search OfferUp for")
    zip_code = where.strip() if where.strip().isdigit() and len(where.strip()) == 5 else ""
    body = {
        "query": query.strip(),
        "zipCode": zip_code or DEFAULT_ZIP,
        "maxItems": max_results,
        "sortBy": "bestMatch",
        "fetchDetails": False,  # details cost a request each; results carry price and place
    }
    items = await run_actor(ACTOR, body, max_results, MAX_CHARGE_USD)
    return parse_listings(items)
