"""Craigslist source: a buyer's search in, local for-sale listings out (Apify).

Uses the `logiover/craigslist-scraper` actor, which reads Craigslist's own search endpoint and
returns coordinates for each post. Coordinates are why this actor was picked over the others:
a buyer's "within 10 miles" and any future meetup arranging both need them. It's pay-per-result
at about $0.002 a listing, so a 20-result search costs about $0.04.

Craigslist is the local, in-person end of the market, so these listings are the ones a buyer
would drive to. None of them can be negotiated with by an agent: the seller is a person with a
Craigslist relay email, which is why `ExternalListing.negotiable` stays False.

Note for the first paid run: the actor's per-result price is separate from Apify's own charge
for the US residential proxy it needs, so check the real cost on a small `maxResults` first.
"""

from __future__ import annotations

import os
from typing import Any

from yardsale.sources import (
    ExternalListing,
    SourceError,
    cents_or_none,
    coordinates,
    deduplicated,
    first_web_link,
    iso_date,
    one_line,
    photo_count,
    run_actor,
    web_link,
)

SOURCE = "craigslist"
ACTOR = os.getenv("YARDSALE_CRAIGSLIST_ACTOR", "logiover~craigslist-scraper")
DEFAULT_CITY = os.getenv("YARDSALE_CRAIGSLIST_CITY", "sfbay")
CATEGORY = "sss"  # Craigslist's code for "for sale", all categories
MAX_CHARGE_USD = "0.25"  # hard cap per search, enforced by Apify: about 125 results
MAX_TITLE = 200
MAX_LOCATION = 80


def parse_listing(raw: Any) -> ExternalListing | None:
    """One actor result -> ExternalListing. None for anything a buyer couldn't act on."""
    if not isinstance(raw, dict):
        return None
    title = one_line(raw.get("title"), MAX_TITLE)
    external_id = one_line(raw.get("id"), 64)
    if not title or not external_id:
        return None
    # The actor leaves `url` null on many results, so a missing link can't disqualify a listing:
    # price, location, coordinates, and photos are still worth showing. Requiring one dropped
    # 3 of the first 5 real results.
    url = web_link(raw.get("url"))
    # priceValue is the actor's parsed number; price is the displayed string ("$250").
    price = cents_or_none(raw.get("priceValue"))
    if price is None:
        price = cents_or_none(raw.get("price"))
    latitude, longitude = coordinates(raw.get("latitude"), raw.get("longitude"))
    return ExternalListing(
        source=SOURCE,
        external_id=external_id,
        title=title,
        price_cents=price,
        url=url,
        location=one_line(raw.get("location"), MAX_LOCATION),
        image_url=first_web_link(raw.get("images")),
        latitude=latitude,
        longitude=longitude,
        posted_at=iso_date(raw.get("postedDate")),
        photo_count=photo_count(raw.get("images")),
    )


def parse_listings(items: list[Any]) -> list[ExternalListing]:
    """Every result the buyer could act on, repeats dropped."""
    parsed = [parse_listing(raw) for raw in items]
    return deduplicated([listing for listing in parsed if listing is not None])


async def search_craigslist(query: str, where: str, max_results: int) -> list[ExternalListing]:
    """Search one Craigslist city's for-sale section. `where` is a city subdomain, e.g. "sfbay"."""
    if not query.strip():
        raise SourceError("there's nothing to search Craigslist for")
    body = {
        "city": (where or DEFAULT_CITY).strip(),
        "category": CATEGORY,
        "query": query.strip(),
        "maxResults": max_results,
        # Details cost extra per listing and add a request each; search results already carry
        # the price, location, coordinates, and a photo.
        "includeDetails": False,
    }
    items = await run_actor(ACTOR, body, max_results, MAX_CHARGE_USD)
    return parse_listings(items)
