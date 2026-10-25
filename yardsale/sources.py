"""Listings Yardsale finds on other sites: Craigslist now, eBay and the rest later.

A source turns a buyer's search into `ExternalListing`s. Every source is read-only, because the
other side is a person on someone else's site, not an agent. Nothing found here can be haggled
with, so `negotiable` is False on all of it, and the buyer flow reads that field to decide
between making an offer and handing the buyer a link.

Parsing is deliberately forgiving: a scraper's fields drift, and one odd result should drop out
instead of failing the whole search. `researcher.py` has near-identical private helpers for the
eBay actor; they could move here later, once more than one source is in use.
"""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

import aiohttp

APIFY_RUN_URL = "https://api.apify.com/v2/acts/{actor}/run-sync-get-dataset-items"
MAX_PRICE_USD = 1_000_000  # above this is a parsing error, not a price
ACTOR_TIMEOUT = 180.0  # seconds; a scrape of one city takes tens of seconds


class SourceError(RuntimeError):
    """A source couldn't be searched. The message is shown to the buyer."""


@dataclass(frozen=True)
class ExternalListing:
    """One listing on another site, normalised. Prices are integer cents, like everywhere else."""

    source: str  # "craigslist", "ebay", ...
    external_id: str
    title: str
    price_cents: int | None  # None when there's no usable price ("contact me"); 0 means free
    url: str
    location: str = ""  # as the site words it: "sunset / parkside", "San Jose, CA"
    image_url: str | None = None
    latitude: float | None = None  # Craigslist gives these; eBay gives a place name only
    longitude: float | None = None
    posted_at: str = ""  # YYYY-MM-DD when the site gives one
    condition: str = ""  # as the site words it: "Pre-Owned", "New". Classifieds rarely say.
    shipping_cents: int | None = None  # None when unknown; 0 means free shipping
    accepts_offers: bool = False  # the site lets a buyer send an offer, e.g. eBay Best Offer
    negotiable: bool = False  # nothing scraped is: there's no agent on the other end
    # Quality signals for the deal finder (deals.py). None when the site doesn't say.
    photo_count: int | None = None
    seller_rating: float | None = None  # percent positive feedback, 0 to 100

    @property
    def total_cents(self) -> int | None:
        """Price plus shipping, for comparing a posted item with one you'd drive to."""
        if self.price_cents is None:
            return None
        return self.price_cents + (self.shipping_cents or 0)

    def searchable_text(self) -> str:
        """Public words a buyer's search can match, like `store.Listing.searchable_text`."""
        return f"{self.title} {self.location}".strip()


# (query, where, max_results) -> listings. `where` is whatever a source needs to scope a search:
# a Craigslist city subdomain, a postal code, a metro name. Mirrors `researcher.Research`.
Source = Callable[[str, str, int], Awaitable[list[ExternalListing]]]


def cents_or_none(value: Any) -> int | None:
    """A price from a scraper -> cents. None when it isn't a number we can use. 0 means free."""
    if value in (None, "") or isinstance(value, bool):
        return None
    try:
        amount = Decimal(str(value).replace(",", "").replace("$", "").strip())
    except InvalidOperation:
        return None
    if not amount.is_finite() or not 0 <= amount <= MAX_PRICE_USD:
        return None
    return int((amount * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def web_link(value: Any) -> str:
    """The value when it's an http(s) link, otherwise "". Keeps javascript: and file: out."""
    link = str(value or "").strip()
    return link if link.startswith(("https://", "http://")) else ""


def first_web_link(value: Any) -> str | None:
    """The first usable link in a scraper's photo list, or from a bare string."""
    candidates = value if isinstance(value, list) else [value]
    for candidate in candidates:
        if isinstance(candidate, dict):  # some actors wrap photos as {"url": ...}
            candidate = candidate.get("url") or candidate.get("src")
        link = web_link(candidate)
        if link:
            return link
    return None


def photo_count(value: Any) -> int | None:
    """How many photos a scraper's photo field holds: a list's length, 1 for a bare link or a
    {"url": ...} photo, None when there's no field to count."""
    if value in (None, "", [], {}) or isinstance(value, bool):
        return None
    if isinstance(value, list):
        return sum(1 for item in value if item not in (None, ""))
    return 1


def percent(value: Any) -> float | None:
    """A percentage such as a seller's positive feedback ("99.6%", 99.6), or None."""
    if value in (None, "") or isinstance(value, bool):
        return None
    try:
        number = float(str(value).replace("%", "").strip())
    except ValueError:
        return None
    if number != number or not 0 <= number <= 100:  # NaN fails its own equality check
        return None
    return number


def coordinate(value: Any, limit: float) -> float | None:
    """A latitude or longitude, or None when it's missing or out of range."""
    if value in (None, "") or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or abs(number) > limit:  # NaN fails its own equality check
        return None
    return number


def coordinates(latitude: Any, longitude: Any) -> tuple[float | None, float | None]:
    """Both coordinates, or neither.

    Half a position is worse than none: distance code given a latitude and no longitude would
    quietly measure from the wrong place. Exactly (0, 0) is rejected too, because scrapers use
    zero for "missing" and no listing is really in the Gulf of Guinea.
    """
    north = coordinate(latitude, 90.0)
    east = coordinate(longitude, 180.0)
    if north is None or east is None or (north == 0.0 and east == 0.0):
        return None, None
    return north, east


def one_line(value: Any, limit: int) -> str:
    """Collapsed whitespace, cut to `limit`. Scraped titles arrive with newlines and padding."""
    return " ".join(str(value or "").split())[:limit]


def iso_date(value: Any) -> str:
    """The YYYY-MM-DD part of a timestamp, or "" when there isn't one."""
    text = one_line(value, 40)[:10]
    if len(text) == 10 and text[4] == "-" and text[7] == "-":
        return text
    return ""


async def run_actor(
    actor: str, body: dict[str, Any], max_results: int, max_charge_usd: str
) -> list[Any]:
    """Run an Apify actor and return its dataset. Raises SourceError with a readable reason.

    `maxTotalChargeUsd` is a cap Apify enforces, so a runaway scrape can't empty the account.
    """
    token = os.getenv("APIFY_TOKEN", "").strip()
    if not token:
        raise SourceError("APIFY_TOKEN is not set on the server (see .env.example)")
    params = {
        "timeout": "120",
        # maxItems caps what a pay-per-result actor may charge for; limit caps what the dataset
        # returns. An actor can ignore its own max-results input (OfferUp's returned 85 for 10
        # on October 24), so both are set.
        "maxItems": str(max_results),
        "limit": str(max_results),
        "maxTotalChargeUsd": max_charge_usd,
    }
    headers = {"Authorization": f"Bearer {token}"}
    timeout = aiohttp.ClientTimeout(total=ACTOR_TIMEOUT)
    url = APIFY_RUN_URL.format(actor=actor)
    try:
        async with (
            aiohttp.ClientSession(timeout=timeout) as session,
            session.post(url, params=params, json=body, headers=headers) as resp,
        ):
            if resp.status not in (200, 201):
                detail = (await resp.text())[:200]
                raise SourceError(f"Apify returned {resp.status}: {detail}")
            items = await resp.json()
    except (aiohttp.ClientError, TimeoutError) as ex:
        raise SourceError(f"couldn't reach Apify ({ex})") from ex
    if not isinstance(items, list):
        raise SourceError(f"Apify returned {type(items).__name__}, not a list of listings")
    return items


def deduplicated(listings: list[ExternalListing]) -> list[ExternalListing]:
    """Drop repeats. The same post can appear twice in one scrape, and across nearby cities."""
    seen: set[str] = set()
    unique: list[ExternalListing] = []
    for listing in listings:
        key = listing.url or f"{listing.source}|{listing.external_id}"
        if key not in seen:
            seen.add(key)
            unique.append(listing)
    return unique
