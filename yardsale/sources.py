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

import asyncio
import contextlib
import json
import os
import re
import weakref
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

import aiohttp

APIFY_RUN_URL = "https://api.apify.com/v2/acts/{actor}/run-sync-get-dataset-items"
MAX_PRICE_USD = 1_000_000  # above this is a parsing error, not a price
ACTOR_TIMEOUT = 180.0  # seconds; a scrape of one city takes tens of seconds
APIFY_LAST_RUN_URL = "https://api.apify.com/v2/acts/{actor}/runs/last"
APIFY_RUN_LOG_URL = "https://api.apify.com/v2/actor-runs/{run}/log"
# Apify's free plan runs at most 5 actors at once across the whole account. A buyer's search
# runs 5 (four sites plus the eBay comps), and so does a photo (the Researcher's eBay search plus
# the Scout's four sites). On October 25 a photo's eBay search got a 402
# "concurrent-runs-limit-exceeded" while another chat's deal search was running, and the photo
# failed. So every run in this server waits for a slot (`apify_slot`), with one slot kept for
# eBay sold-price searches. YARDSALE_APIFY_RUNS changes the limit, for a paid plan.
APIFY_RUNS_AT_ONCE = 5
# Runs from another machine on the same Apify account aren't counted by the slots. A refused run
# costs nothing, so it's retried after each of these waits before it counts as failed.
BUSY_RETRY_SECONDS: tuple[float, ...] = (3.0, 6.0, 12.0)


def apify_runs_at_once() -> int:
    """How many Apify runs this server starts at once (YARDSALE_APIFY_RUNS, default 5)."""
    raw = os.getenv("YARDSALE_APIFY_RUNS", "").strip()
    try:
        return max(1, int(raw)) if raw else APIFY_RUNS_AT_ONCE
    except ValueError:
        return APIFY_RUNS_AT_ONCE


# One pair of slot counters per event loop: (every run, listing runs). The server has one loop;
# the tests start a new one per test, and an asyncio.Semaphore can't be shared between loops.
_slots: weakref.WeakKeyDictionary[
    asyncio.AbstractEventLoop, tuple[asyncio.Semaphore, asyncio.Semaphore]
] = weakref.WeakKeyDictionary()


@contextlib.asynccontextmanager
async def apify_slot(sold_comps: bool = False) -> AsyncIterator[None]:
    """Wait for a free Apify run slot in this server, and hold it for one run.

    Listing searches (every site, for buyers and the Scout) share all slots but one. eBay
    sold-price searches (`sold_comps=True`) may also take the last one, so a photo's price never
    waits behind a queue of listing searches: without sold prices there's no price at all.
    """
    loop = asyncio.get_running_loop()
    slots = _slots.get(loop)
    if slots is None:
        runs = apify_runs_at_once()
        slots = (asyncio.Semaphore(runs), asyncio.Semaphore(max(1, runs - 1)))
        _slots[loop] = slots
    every_run, listing_runs = slots
    if sold_comps:
        async with every_run:
            yield
    else:
        async with listing_runs, every_run:
            yield


class SourceError(RuntimeError):
    """A source couldn't be searched. The message is shown to the buyer.

    `kind` says why, so a failure is never reported as "nothing found": "timeout" (the run ran
    out of time), "blocked" (the site turned the scraper away, or it returned only errors),
    "busy" (Apify's limit on runs at once, still hit after retrying), "credit" (the Apify
    account is out of credit), "setup" (no token, a bad input, nothing to search), or "error"
    (anything else).
    """

    def __init__(self, message: str, kind: str = "error") -> None:
        super().__init__(message)
        self.kind = kind


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
    """How many photos a scraper's photo field holds: a count the actor gives (Craigslist's
    `imageCount`), a list's length, 1 for a bare link or a {"url": ...} photo, None when there's
    no field to count."""
    if value in (None, "", [], {}) or isinstance(value, bool):
        return None
    if isinstance(value, int | float) or (isinstance(value, str) and value.strip().isdigit()):
        count = int(value)
        return count if 0 <= count <= 1000 else None
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

    Every way a run can fail raises, with `SourceError.kind` saying why, so a caller never
    mistakes a failed search for an empty one: an error status (a timed-out or failed run, a bad
    input, no credit), a run Apify refused because too many are running (retried first, see
    BUSY_RETRY_SECONDS), a dataset holding only error reports, and an empty dataset whose run
    says its page requests failed (a blocked proxy). An empty dataset from a run that worked is
    a real "nothing found" and comes back as [].
    """
    for wait in (*BUSY_RETRY_SECONDS, None):
        try:
            async with apify_slot():
                return await _run_actor_once(actor, body, max_results, max_charge_usd)
        except SourceError as ex:
            if ex.kind != "busy" or wait is None:
                raise
        await asyncio.sleep(wait)
    raise AssertionError("unreachable")  # pragma: no cover


def failure(status: int, detail: str) -> SourceError:
    """An Apify error response -> a SourceError whose kind says why (see SourceError)."""
    error_type, message = "", detail
    try:
        error = json.loads(detail).get("error") or {}
        error_type = str(error.get("type") or "")
        message = str(error.get("message") or detail)
    except (ValueError, AttributeError):
        pass
    text = f"{error_type} {message}".lower()
    if status == 408 or "timed-out" in text or "timeout" in text:
        kind = "timeout"
    elif status == 429 or "concurrent" in text or "memory-limit" in text or "rate-limit" in text:
        kind = "busy"
    elif status == 402 or "usage" in text or "credit" in text:
        kind = "credit"
    elif status in (400, 401, 403, 404) and "run-failed" not in text:
        kind = "setup"
    else:
        kind = "error"
    return SourceError(f"Apify returned {status}: {one_line(message, 200)}", kind)


_ERROR_KEYS = ("error", "errorMessage", "errorDescription", "#error")


def error_report(items: list[Any]) -> str | None:
    """When every item is an error report rather than a result (actors push {"error": ...} for
    a page they couldn't load), the first error; otherwise None."""
    if not items:
        return None
    reasons: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            return None
        found = next((item[key] for key in _ERROR_KEYS if item.get(key)), None)
        if found is None:
            return None
        reasons.append("" if found is True else one_line(found, 120))
    return next((reason for reason in reasons if reason), "the scraper reported only errors")


# "Total 3 requests: 0 succeeded, 3 failed." is how Crawlee-based actors end their status.
_REQUESTS = re.compile(r"(\d+)\s+succeeded,\s*(\d+)\s+failed")
_BLOCK_WORDS = re.compile(
    r"\b(?:403|429|blocked|captcha|access denied|forbidden|too many requests)\b", re.IGNORECASE
)


async def why_empty(
    session: aiohttp.ClientSession, actor: str, headers: dict[str, str]
) -> str | None:
    """For an empty dataset: why, when the actor's latest run says its page loads failed (a
    blocked proxy), or None for a real empty result. Reads the run's status message and the end
    of its log, both free. The latest run is the one that just finished unless another search
    ran the same actor at the same moment; then the reason may be that one's, which is still a
    fair warning. Never raises."""
    try:
        async with session.get(APIFY_LAST_RUN_URL.format(actor=actor), headers=headers) as resp:
            if resp.status != 200:
                return None
            run = (await resp.json()).get("data") or {}
        status = str(run.get("status") or "")
        note = one_line(run.get("statusMessage"), 200)
        if status and status != "SUCCEEDED":
            return f"the run ended {status.lower()}" + (f" ({note})" if note else "")
        counts = _REQUESTS.search(note)
        if counts is not None and int(counts.group(1)) == 0 and int(counts.group(2)) > 0:
            return f"every page request failed ({note})"
        run_id = str(run.get("id") or "")
        if not run_id:
            return None
        async with session.get(APIFY_RUN_LOG_URL.format(run=run_id), headers=headers) as resp:
            if resp.status != 200:
                return None
            tail = (await resp.text())[-3000:]
        blocked = _BLOCK_WORDS.search(tail)
        return f"the site turned the scraper away ({blocked.group(0)})" if blocked else None
    except (aiohttp.ClientError, TimeoutError, ValueError, AttributeError):
        return None


async def _run_actor_once(
    actor: str, body: dict[str, Any], max_results: int, max_charge_usd: str
) -> list[Any]:
    token = os.getenv("APIFY_TOKEN", "").strip()
    if not token:
        raise SourceError("APIFY_TOKEN is not set on the server (see .env.example)", "setup")
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
                detail = (await resp.text())[:600]
                raise failure(resp.status, detail)
            items = await resp.json()
            if isinstance(items, list) and not items:
                reason = await why_empty(session, actor, headers)
                if reason:
                    raise SourceError(f"the search came back empty: {reason}", "blocked")
    except TimeoutError as ex:
        raise SourceError(f"Apify didn't answer within {ACTOR_TIMEOUT:.0f}s", "timeout") from ex
    except aiohttp.ClientError as ex:
        raise SourceError(f"couldn't reach Apify ({ex})") from ex
    if not isinstance(items, list):
        raise SourceError(f"Apify returned {type(items).__name__}, not a list of listings")
    reported = error_report(items)
    if reported is not None:
        raise SourceError(f"the scraper returned only errors: {reported}", "blocked")
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
