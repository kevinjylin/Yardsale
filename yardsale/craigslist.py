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

import ast
import os
import re
from typing import Any
from urllib.parse import urlencode

from yardsale import places
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
# Searching around a ZIP code: Craigslist's own "miles from zip" filter, passed as a search URL
# (the actor's city input has no radius). Checked live on October 24: a 5-mile search around
# 94704 returned only Berkeley, Oakland, Emeryville, El Cerrito, and Albany posts.
MILES = int(os.getenv("YARDSALE_CRAIGSLIST_MILES", "10") or "10")
_SITE = re.compile(r"[a-z]{2,30}")
_ZIP = re.compile(r"\d{5}")


def _photos(value: Any) -> Any:
    """The actor sends `images` as a Python-style list in a string ("['https://...']"), seen
    live on October 24, which hid every Craigslist photo. Read that back into a list."""
    if isinstance(value, str) and value.strip().startswith("["):
        try:
            return ast.literal_eval(value.strip())
        except (ValueError, SyntaxError):
            return None
    return value


def _slug_place(raw: dict[str, Any], title: str) -> str:
    """The town at the front of the post's slug ("santa-rosa-pokemon-water-bottle" -> "santa
    rosa"), or "". The actor's `location` field was off by one post on October 24 (a Santa Rosa
    post said "Alameda"); the slug and the coordinates agreed, so the slug wins."""
    slug = [part for part in str(raw.get("slug") or "").lower().split("-") if part]
    plain = re.sub("['\\u2019]", "", title.lower())
    # Craigslist drops some numbers from slugs and keeps others ("2020" stays, "4" goes), so
    # numbers are left out of the comparison on both sides.
    words = [w for w in re.findall(r"[a-z0-9]+", plain) if not w.isdigit()]
    if not slug or not words:
        return ""
    for start in range(1, len(slug)):
        tail = [part for part in slug[start:] if not part.isdigit()]
        if tail and tail == words[: len(tail)]:
            return " ".join(slug[:start]).title()[:MAX_LOCATION]
    return ""


def search_link(listing: ExternalListing, zip_code: str = "") -> str:
    """A link for a post the actor gave none: Craigslist's search for its exact title (around
    the ZIP code when given), whose first result is the post. The actor nulled 6 of 10 links in
    a Jordan search on October 24, and the deal finder drops a listing it can't link to."""
    if _ZIP.fullmatch(zip_code or ""):
        return search_url(listing.title, zip_code)
    query = urlencode({"query": listing.title})
    return f"https://{DEFAULT_CITY}.craigslist.org/search/{CATEGORY}?{query}"


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
        location=_slug_place(raw, title) or one_line(raw.get("location"), MAX_LOCATION),
        image_url=first_web_link(_photos(raw.get("images"))),
        latitude=latitude,
        longitude=longitude,
        posted_at=iso_date(raw.get("postedDate")),
        # The actor trims `images` in search results; `imageCount` is the real number.
        photo_count=photo_count(raw.get("imageCount")) or photo_count(_photos(raw.get("images"))),
    )


def parse_listings(items: list[Any]) -> list[ExternalListing]:
    """Every result the buyer could act on, repeats dropped."""
    parsed = [parse_listing(raw) for raw in items]
    return deduplicated([listing for listing in parsed if listing is not None])


def search_url(query: str, zip_code: str, site: str = "") -> str:
    """Craigslist's for-sale search within MILES of a ZIP code, on that ZIP code's site."""
    site = site or places.site_for_zip(zip_code) or DEFAULT_CITY
    params = {"query": query.strip(), "postal": zip_code, "search_distance": str(MILES)}
    return f"https://{site}.craigslist.org/search/{CATEGORY}?{urlencode(params)}"


async def search_craigslist(query: str, where: str, max_results: int) -> list[ExternalListing]:
    """Search one Craigslist city's for-sale section. `where` is a city subdomain, e.g. "sfbay",
    or a 5-digit ZIP code to search within MILES of it, on that ZIP code's site."""
    if not query.strip():
        raise SourceError("there's nothing to search Craigslist for", "setup")
    where = (where or "").strip().lower()
    body: dict[str, Any] = {
        "city": where if _SITE.fullmatch(where) else DEFAULT_CITY,
        "category": CATEGORY,
        "query": query.strip(),
        "maxResults": max_results,
        # Details cost extra per listing and add a request each; search results already carry
        # the price, location, coordinates, and a photo.
        "includeDetails": False,
    }
    if _ZIP.fullmatch(where):
        # The actor reads searchUrls in place of city, category, and query.
        body = {
            "searchUrls": [search_url(query, where)],
            "maxResults": max_results,
            "includeDetails": False,
        }
    items = await run_actor(ACTOR, body, max_results, MAX_CHARGE_USD)
    return parse_listings(items)
