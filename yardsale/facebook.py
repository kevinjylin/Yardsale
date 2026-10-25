"""Facebook Marketplace source: a buyer's search in, local for-sale listings out (Apify).

Uses Apify's own `apify/facebook-marketplace-scraper`, the one maintained by Apify itself (about
11,700 users, 980 a month, checked October 24). It takes Marketplace URLs, not keywords, so a
search becomes `facebook.com/marketplace/<city>/search/?query=...`. It's pay-per-result: "from
$2.60 per 1,000" on the Store page, and about $6.20 per 1,000 on Apify's free plan by its README,
so a 20-result search costs about $0.05 to $0.12. That's the dearest source here, so it's off
unless YARDSALE_FACEBOOK=1.

Marketplace sellers are people, so nothing here is agent-negotiable. The actor also returns the
seller's name; it's dropped here, because a buyer doesn't need it to decide and Yardsale
shouldn't keep it.

Parsing follows the actor's documented sample output (October 24); it hasn't been run with a
real key yet.
"""

from __future__ import annotations

import os
import re
from typing import Any
from urllib.parse import quote

from yardsale.sources import (
    ExternalListing,
    SourceError,
    cents_or_none,
    deduplicated,
    one_line,
    run_actor,
    web_link,
)

SOURCE = "facebook"
ACTOR = os.getenv("YARDSALE_FACEBOOK_ACTOR", "apify~facebook-marketplace-scraper")
DEFAULT_CITY = os.getenv("YARDSALE_FACEBOOK_CITY", "sanfrancisco")  # Marketplace's city slug
MAX_CHARGE_USD = "0.25"  # hard cap per search, enforced by Apify
MAX_TITLE = 200
MAX_LOCATION = 80
_CITY = re.compile(r"[a-z0-9-]{2,40}")
_ID = re.compile(r"\d{5,25}")


def enabled() -> bool:
    return os.getenv("YARDSALE_FACEBOOK", "").strip().lower() in ("1", "true", "yes", "on")


def _price(raw: dict[str, Any]) -> int | None:
    price = raw.get("listing_price")
    if not isinstance(price, dict):
        return None
    cents = cents_or_none(price.get("amount"))
    return cents if cents is not None else cents_or_none(price.get("formatted_amount"))


def _location(raw: dict[str, Any]) -> str:
    location = raw.get("location")
    place = location.get("reverse_geocode") if isinstance(location, dict) else None
    if not isinstance(place, dict):
        return ""
    parts = [one_line(place.get("city"), 40), one_line(place.get("state"), 20)]
    return one_line(", ".join(p for p in parts if p), MAX_LOCATION)


def _photo(raw: dict[str, Any]) -> str | None:
    photo = raw.get("primary_listing_photo")
    image = photo.get("image") if isinstance(photo, dict) else None
    if not isinstance(image, dict):
        return None
    return web_link(image.get("uri")) or None


def parse_listing(raw: Any) -> ExternalListing | None:
    """One actor result -> ExternalListing. None for anything a buyer couldn't act on."""
    if not isinstance(raw, dict):
        return None
    title = one_line(raw.get("marketplace_listing_title") or raw.get("custom_title"), MAX_TITLE)
    external_id = one_line(raw.get("id"), 64)
    if not title or not external_id:
        return None
    if raw.get("is_sold") is True or raw.get("is_pending") is True or raw.get("is_live") is False:
        return None
    url = web_link(raw.get("listingUrl"))
    if not url and _ID.fullmatch(external_id):
        url = f"https://www.facebook.com/marketplace/item/{external_id}"
    return ExternalListing(
        source=SOURCE,
        external_id=external_id,
        title=title,
        price_cents=_price(raw),
        url=url,
        location=_location(raw),
        image_url=_photo(raw),
    )


def parse_listings(items: list[Any]) -> list[ExternalListing]:
    """Every result the buyer could act on, repeats dropped."""
    parsed = [parse_listing(raw) for raw in items]
    return deduplicated([listing for listing in parsed if listing is not None])


def search_url(query: str, city: str) -> str:
    slug = city.strip().lower() if _CITY.fullmatch(city.strip().lower()) else DEFAULT_CITY
    return f"https://www.facebook.com/marketplace/{slug}/search/?query={quote(query.strip())}"


async def search_facebook(query: str, where: str, max_results: int) -> list[ExternalListing]:
    """Search one city's Marketplace. `where` is Marketplace's city slug, e.g. "sanfrancisco"."""
    if not query.strip():
        raise SourceError("there's nothing to search Facebook Marketplace for")
    body = {
        "startUrls": [{"url": search_url(query, where or DEFAULT_CITY)}],
        "resultsLimit": max_results,
        "includeListingDetails": False,  # details cost a page load each
    }
    items = await run_actor(ACTOR, body, max_results, MAX_CHARGE_USD)
    return parse_listings(items)
