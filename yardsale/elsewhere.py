"""The deal finder: when Yardsale has nothing that fits, search other sites once, right now.

A buyer says what they want and the most they'll pay. Yardsale's own listings come first, since
their agents can haggle. When none fits, `DealFinder.find` asks every source (sources.py:
Craigslist, eBay, OfferUp, ...) at once and returns the best few listings within the budget.
`watches.py` does the same search on a schedule; this is the one-off version, shared by the
chat and @yardsale's API.

Every source call is a paid Apify run (about $0.03 to $0.05 for 20 results), so:

- Results are cached per (search words, budget) for CACHE_FOR seconds. The same buyer asking
  twice, or the chat and the API asking the same thing, costs one run.
- A search already running is joined, not started again. That also lets the API answer within
  its 25 seconds and pick up the same search on the next call.
- Each source is asked for at most PER_SOURCE results, and the answer keeps the best LIMIT.
- With no sources (demo mode, tests), the finder is off and nothing is searched.

"Best" is how well the title matches the search, then the total price (price plus shipping),
cheapest first. Titles that look like parts or accessories ("road bike helmet" for "road bike")
sink below the rest, and a budget filters on the total price, since shipping is part of what
the buyer pays. Nothing found here can be haggled with by an agent (`negotiable` is False).

The buyer's max and search words are never logged; the log says how many listings came back.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass

from yardsale.search import CHEAP, STOPWORDS, words
from yardsale.sources import ExternalListing, Source, SourceError, deduplicated

log = logging.getLogger("yardsale.elsewhere")

CACHE_FOR = 600.0  # seconds a search's results are reused
MAX_CACHED = 64  # searches kept at once
PER_SOURCE = 20  # results asked of each source, which is what each search costs
LIMIT = 5  # listings shown to the buyer
MIN_PRICE_CENTS = 100  # $0 and $1 posts are almost always "contact me" placeholders

# Shown as the site's name.
SITE_NAMES = {
    "craigslist": "Craigslist",
    "ebay": "eBay",
    "offerup": "OfferUp",
    "facebook": "Facebook Marketplace",
}
# Words that make a listing a part of or an add-on for the thing, unless the buyer asked for them.
NOT_THE_THING = {
    "part", "parts", "lot", "case", "cover", "charger", "cable", "manual", "box", "tire",
    "tyre", "tube", "wheel", "wheelset", "pedal", "saddle", "seat", "helmet", "light", "mount",
    "rack", "bag", "strap", "sticker", "decal", "replacement", "adapter", "skin", "protector",
    "stand", "kit", "only", "broken", "repair", "wanted", "iso",
}  # fmt: skip
# The words that name sites or ask to search them, so "search ebay for a road bike" is a search
# for "road bike".
SITE_WORDS = {
    "ebay", "craigslist", "offerup", "offer", "up", "facebook", "fb", "marketplace", "other",
    "site", "sites", "website", "websites", "elsewhere", "online", "check", "try", "look",
    "else", "anywhere", "place", "places", "and", "too", "also", "watch", "watching", "keep",
    "let", "know", "if", "one", "show", "shows", "pop", "pops", "come", "comes", "notify",
    "tell", "when", "it", "that", "this", "out", "on", "at", "from", "max", "under",
    "below", "budget", "most", "than", "less", "more", "no",
}  # fmt: skip


def site_name(source: str) -> str:
    return SITE_NAMES.get(source, source.capitalize())


def search_words(query: str) -> str:
    """The words worth searching other sites for: no filler, no site names, no amounts."""
    kept = [
        w
        for w in words(query)
        if w not in STOPWORDS and w not in CHEAP and w not in SITE_WORDS and not w.isdigit()
    ]
    return " ".join(kept)


@dataclass(frozen=True)
class Found:
    """One search of other sites."""

    query: str
    budget_cents: int | None
    listings: tuple[ExternalListing, ...]  # best first, at most LIMIT
    searched: tuple[str, ...]  # sources that answered
    failed: tuple[str, ...]  # sources that couldn't be searched
    considered: int = 0  # listings the sources returned before filtering


def _fit(wanted: set[str], listing: ExternalListing) -> tuple[int, int]:
    """(sinks, misses): whether it looks like a part, and how many search words it lacks."""
    have = set(words(listing.title))
    sinks = int(bool((have & NOT_THE_THING) - wanted))
    return sinks, len(wanted - have)


def best(
    listings: list[ExternalListing], query: str, budget_cents: int | None, limit: int = LIMIT
) -> list[ExternalListing]:
    """The best listings for a buyer: priced, within budget, matching at least half the search
    words; then fewest missing words, parts and accessories last, cheapest total first."""
    wanted = set(search_words(query).split())
    picked: list[tuple[tuple[int, int, int, str], ExternalListing]] = []
    for listing in deduplicated(listings):
        total = listing.total_cents
        if listing.price_cents is None or listing.price_cents < MIN_PRICE_CENTS or total is None:
            continue
        if budget_cents is not None and total > budget_cents:
            continue
        if not listing.url:  # the buyer has to be able to open it
            continue
        sinks, misses = _fit(wanted, listing)
        if wanted and misses * 2 > len(wanted):
            continue
        picked.append(((sinks, misses, total, listing.url), listing))
    picked.sort(key=lambda pair: pair[0])
    return [listing for _, listing in picked[:limit]]


class DealFinder:
    """Searches every source at once, with a short cache. Shared by the chat and the API."""

    def __init__(
        self,
        sources: dict[str, Source],
        where: dict[str, str] | None = None,
        cache_for: float = CACHE_FOR,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.sources = dict(sources)
        self.where = where or {}  # a source's scope (a city, a ZIP code); "" means its default
        self.cache_for = cache_for
        self.clock = clock
        self._searches: dict[tuple[str, int | None], tuple[float, asyncio.Task[Found]]] = {}

    @property
    def enabled(self) -> bool:
        return bool(self.sources)

    def site_names(self) -> str:
        """ "eBay, Craigslist, and OfferUp", for telling the buyer where we looked."""
        names = [site_name(name) for name in self.sources]
        if len(names) <= 2:
            return " and ".join(names)
        return ", ".join(names[:-1]) + ", and " + names[-1]

    def _key(self, query: str, budget_cents: int | None) -> tuple[str, int | None]:
        return search_words(query), budget_cents

    def _forget_old(self) -> None:
        now = self.clock()
        for key, (started, task) in list(self._searches.items()):
            stale = now - started > self.cache_for
            # A search that failed, or where no site answered, is tried again next time.
            failed = task.done() and (
                task.cancelled() or task.exception() is not None or not task.result().searched
            )
            if (stale and task.done()) or failed:
                del self._searches[key]
        while len(self._searches) > MAX_CACHED:
            oldest = min(self._searches, key=lambda k: self._searches[k][0])
            if not self._searches[oldest][1].done():
                break
            del self._searches[oldest]

    def start(self, query: str, budget_cents: int | None) -> asyncio.Task[Found]:
        """The search for these words and budget: a cached or running one, or a new one."""
        self._forget_old()
        key = self._key(query, budget_cents)
        cached = self._searches.get(key)
        if cached is not None:
            return cached[1]
        task = asyncio.ensure_future(self._search(key[0], budget_cents))
        self._searches[key] = (self.clock(), task)
        return task

    async def find(self, query: str, budget_cents: int | None) -> Found:
        """Search other sites for these words and budget. Raises SourceError if there's nothing
        to search for or no source is set up; a source that fails is reported in `failed`."""
        if not self.enabled:
            raise SourceError("searching other sites is off on this Yardsale server")
        if not search_words(query):
            raise SourceError('tell me what to look for, like "a road bike"')
        # Shielded, so a caller that stops waiting (a timeout) doesn't cancel the shared search.
        return await asyncio.shield(self.start(query, budget_cents))

    async def _one(self, name: str, source: Source, query: str) -> list[ExternalListing] | None:
        try:
            return await source(query, self.where.get(name, ""), PER_SOURCE)
        except SourceError as ex:
            log.warning(f"other sites: {name} couldn't be searched ({ex})")
        except Exception:
            log.exception(f"other sites: {name} failed")
        return None

    async def _search(self, query: str, budget_cents: int | None) -> Found:
        started = time.monotonic()
        names = list(self.sources)
        answers = await asyncio.gather(
            *(self._one(name, self.sources[name], query) for name in names)
        )
        found = [listing for answer in answers if answer for listing in answer]
        searched = tuple(
            name for name, answer in zip(names, answers, strict=True) if answer is not None
        )
        failed = tuple(name for name, answer in zip(names, answers, strict=True) if answer is None)
        chosen = best(found, query, budget_cents)
        log.info(
            f"other sites: {len(chosen)} of {len(found)} listing(s) kept from "
            f"{len(searched)} source(s), {len(failed)} failed, in {time.monotonic() - started:.1f}s"
        )
        return Found(query, budget_cents, tuple(chosen), searched, failed, len(found))
