"""The deal finder: the best-priced, best-quality listings on other sites for a buyer's search.

A buyer says what they want and the most they'll pay. `DealFinder.find` asks every source
(sources.py: Craigslist, eBay, OfferUp, ...) at once, looks up what the item actually sells for
(the Researcher's eBay sold-listings search, `comps`), scores every listing against that market
price and its own quality signals (deals.py), and returns the best few within the budget, each
with a one-line reason. `watches.py` does the same search on a schedule; this is the one-off
version. The Deal Finder agent (dealfinder.py) runs one of these; the chat and @yardsale's API
reach it through the Market.

Every source call is a paid Apify run (about $0.03 to $0.05 for 20 results), and the comps
search is another (about $0.08 for 20 sales), so:

- Results are cached per (search words, budget, ZIP) for CACHE_FOR seconds, and comps per
  search words for COMPS_CACHE_FOR. The same buyer asking twice, or the chat and the API asking
  the same thing, costs one run of each.
- A search already running is joined, not started again. That also lets the API answer within
  its 25 seconds and pick up the same search on the next call.
- Each source is asked for at most PER_SOURCE results, and the answer keeps the best LIMIT.
- With no sources (demo mode, tests), the finder is off and nothing is searched.

Nothing found here can be haggled with by an agent (`negotiable` is False). The buyer's max and
search words are never logged; the log says how many listings came back.

    uv run python -m yardsale.elsewhere --probe "road bike" --max 300

runs one small paid search per source (about 10 results each) plus one small comps search, and
prints what each parser made of the live actor output. Set APIFY_TOKEN first; the output says
roughly what it cost.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from yardsale import deals
from yardsale.deals import LIMIT, MarketPrice, ScoredDeal
from yardsale.models import Comp
from yardsale.search import CHEAP, STOPWORDS, words
from yardsale.sources import ExternalListing, Source, SourceError

log = logging.getLogger("yardsale.elsewhere")

CACHE_FOR = 600.0  # seconds a search's results are reused
COMPS_CACHE_FOR = 600.0  # seconds a search's eBay sold comps are reused
MAX_CACHED = 64  # searches kept at once
PER_SOURCE = 20  # results asked of each source, which is what each search costs
COMPS = 20  # eBay sold listings asked for per search, about $0.004 each
MIN_PRICE_CENTS = deals.MIN_PRICE_CENTS
ZIP_SOURCES = frozenset({"offerup"})  # sources whose `where` is a ZIP code

# (query, condition, max_results) -> eBay sold listings; researcher.search_ebay_sold
Comps = Callable[[str, str, int], Awaitable[list[Comp]]]

# Shown as the site's name.
SITE_NAMES = {
    "craigslist": "Craigslist",
    "ebay": "eBay",
    "offerup": "OfferUp",
    "facebook": "Facebook Marketplace",
}
# Words that name sites or ask to search them, so "search ebay for a road bike" is a search
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
    """One search of other sites: the best deals, scored, and the market price behind them."""

    query: str
    budget_cents: int | None
    deals: tuple[ScoredDeal, ...]  # best first, at most LIMIT
    searched: tuple[str, ...]  # sources that answered
    failed: tuple[str, ...]  # sources that couldn't be searched
    considered: int = 0  # listings the sources returned before filtering
    market: MarketPrice | None = None  # None when no recent eBay sales matched
    comps_note: str = ""  # why there's no market price, when there isn't

    @property
    def listings(self) -> tuple[ExternalListing, ...]:
        return tuple(deal.listing for deal in self.deals)

    @property
    def typical_cents(self) -> int | None:
        return self.market.typical_cents if self.market else None


def best(
    listings: list[ExternalListing],
    query: str,
    budget_cents: int | None,
    limit: int = LIMIT,
    market: MarketPrice | None = None,
) -> list[ExternalListing]:
    """The best listings for a buyer, best first (deals.rank without the reasons)."""
    return [deal.listing for deal in deals.rank(listings, query, budget_cents, market, limit=limit)]


class DealFinder:
    """Searches every source at once, prices the market from eBay comps, and scores the result,
    with a short cache. The Deal Finder agent runs one; tests use it directly."""

    def __init__(
        self,
        sources: dict[str, Source],
        where: dict[str, str] | None = None,
        comps: Comps | None = None,
        cache_for: float = CACHE_FOR,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.sources = dict(sources)
        self.where = where or {}  # a source's scope (a city, a ZIP code); "" means its default
        self.comps = comps  # None: no market prices, listings are judged against each other
        self.cache_for = cache_for
        self.clock = clock
        self._searches: dict[tuple[str, int | None, str], tuple[float, asyncio.Task[Found]]] = {}
        self._comps: dict[str, tuple[float, asyncio.Task[MarketPrice | None]]] = {}

    @property
    def enabled(self) -> bool:
        return bool(self.sources)

    def site_names(self) -> str:
        """ "eBay, Craigslist, and OfferUp", for telling the buyer where we looked."""
        names = [site_name(name) for name in self.sources]
        if len(names) <= 2:
            return " and ".join(names)
        return ", ".join(names[:-1]) + ", and " + names[-1]

    def _key(
        self, query: str, budget_cents: int | None, zip_code: str
    ) -> tuple[str, int | None, str]:
        return search_words(query), budget_cents, zip_code.strip()

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
        for query, (started, comps_task) in list(self._comps.items()):
            stale = now - started > COMPS_CACHE_FOR
            # A comps search that failed is tried again next time; no sales is a real answer.
            failed = comps_task.done() and (
                comps_task.cancelled() or comps_task.exception() is not None
            )
            if (stale and comps_task.done()) or failed:
                del self._comps[query]

    def start(
        self, query: str, budget_cents: int | None, zip_code: str = ""
    ) -> asyncio.Task[Found]:
        """The search for these words, budget, and ZIP: a cached or running one, or a new one."""
        self._forget_old()
        key = self._key(query, budget_cents, zip_code)
        cached = self._searches.get(key)
        if cached is not None:
            return cached[1]
        task = asyncio.ensure_future(self._search(key[0], budget_cents, key[2]))
        self._searches[key] = (self.clock(), task)
        return task

    async def find(self, query: str, budget_cents: int | None, zip_code: str = "") -> Found:
        """Search other sites for these words and budget. Raises SourceError if there's nothing
        to search for or no source is set up; a source that fails is reported in `failed`."""
        if not self.enabled:
            raise SourceError("searching other sites is off on this Yardsale server")
        if not search_words(query):
            raise SourceError('tell me what to look for, like "a road bike"')
        # Shielded, so a caller that stops waiting (a timeout) doesn't cancel the shared search.
        return await asyncio.shield(self.start(query, budget_cents, zip_code))

    def market_for(self, query: str) -> asyncio.Task[MarketPrice | None]:
        """The market price for these search words: cached, running, or started now."""
        key = search_words(query)
        cached = self._comps.get(key)
        if cached is not None:
            return cached[1]
        task = asyncio.ensure_future(self._market(key))
        self._comps[key] = (self.clock(), task)
        return task

    async def _market(self, query: str) -> MarketPrice | None:
        if self.comps is None:
            return None
        started = time.monotonic()
        comps = await self.comps(query, "any", COMPS)
        market = deals.market_price(comps, query)
        used = market.sales if market else 0
        log.info(
            f"other sites: market price from {used} of {len(comps)} eBay sales in "
            f"{time.monotonic() - started:.1f}s"
        )
        return market

    async def _one(
        self, name: str, source: Source, query: str, zip_code: str
    ) -> list[ExternalListing] | None:
        where = self.where.get(name, "")
        if zip_code and name in ZIP_SOURCES:
            where = zip_code
        try:
            return await source(query, where, PER_SOURCE)
        except SourceError as ex:
            log.warning(f"other sites: {name} couldn't be searched ({ex})")
        except Exception:
            log.exception(f"other sites: {name} failed")
        return None

    async def _market_or_none(self, query: str) -> tuple[MarketPrice | None, str]:
        """The market price, or None and why. A comps failure never fails the search."""
        if self.comps is None:
            return None, "no eBay sold-listings search on this server"
        try:
            market = await asyncio.shield(self.market_for(query))
        except Exception as ex:
            log.warning(f"other sites: the eBay sold-listings search failed ({type(ex).__name__})")
            return None, "the eBay sold-listings search failed"
        if market is None:
            return None, "no recent eBay sales matched the search"
        return market, ""

    async def _search(self, query: str, budget_cents: int | None, zip_code: str) -> Found:
        started = time.monotonic()
        names = list(self.sources)
        answers, (market, note) = await asyncio.gather(
            asyncio.gather(
                *(self._one(name, self.sources[name], query, zip_code) for name in names)
            ),
            self._market_or_none(query),
        )
        found = [listing for answer in answers if answer for listing in answer]
        searched = tuple(
            name for name, answer in zip(names, answers, strict=True) if answer is not None
        )
        failed = tuple(name for name, answer in zip(names, answers, strict=True) if answer is None)
        chosen = deals.rank(found, query, budget_cents, market)
        log.info(
            f"other sites: {len(chosen)} of {len(found)} listing(s) kept from "
            f"{len(searched)} source(s), {len(failed)} failed, "
            f"{'with' if market else 'without'} a market price, "
            f"in {time.monotonic() - started:.1f}s"
        )
        return Found(query, budget_cents, tuple(chosen), searched, failed, len(found), market, note)


# --- Probe: one small live run per source, by hand ----------------------------------------------

PROBE_PER_SOURCE = 10  # results asked of each source and of the comps search
# Rough cost per result, in US cents, from each actor's Store page (see each module's docstring).
PROBE_COST_CENTS = {"ebay": 0.25, "craigslist": 0.2, "offerup": 0.14, "facebook": 0.62}
PROBE_COMPS_COST_CENTS = 0.4


def probe_cost_usd(sources: list[str]) -> str:
    """About what a probe costs: PROBE_PER_SOURCE results per source plus the comps."""
    cents = sum(PROBE_COST_CENTS.get(name, 0.5) for name in sources) * PROBE_PER_SOURCE
    cents += PROBE_COMPS_COST_CENTS * PROBE_PER_SOURCE
    return f"${cents / 100:.2f}"


async def probe(query: str, budget_cents: int | None, zip_code: str) -> int:
    """Run every source once, small, against the live actors, and print what came back: counts,
    parse failures, the market price, and the top deals with their scores. Paid."""
    from yardsale import bureau_server  # pulls in the whole server; fine by hand
    from yardsale.researcher import search_ebay_sold

    sources = bureau_server.other_sites()
    names = list(sources)
    print(
        f"Probing {', '.join(site_name(n) for n in names)} for {query!r}"
        f"{f' under ${budget_cents // 100}' if budget_cents else ''}, "
        f"{PROBE_PER_SOURCE} results each plus {PROBE_PER_SOURCE} eBay sold comps: "
        f"about {probe_cost_usd(names)} of Apify credit."
    )

    async def small(name: str) -> tuple[str, list[ExternalListing] | str]:
        where = zip_code if zip_code and name in ZIP_SOURCES else ""
        started = time.monotonic()
        try:
            listings = await sources[name](search_words(query), where, PROBE_PER_SOURCE)
        except Exception as ex:
            return name, f"FAILED after {time.monotonic() - started:.1f}s: {ex}"
        print(f"  {site_name(name)}: {len(listings)} parsed in {time.monotonic() - started:.1f}s")
        return name, listings

    async def small_comps() -> list[Comp] | str:
        try:
            return await search_ebay_sold(search_words(query), "any", PROBE_PER_SOURCE)
        except Exception as ex:
            return f"FAILED: {ex}"

    answers, comps = await asyncio.gather(
        asyncio.gather(*(small(name) for name in names)), small_comps()
    )
    found: list[ExternalListing] = []
    for name, answer in answers:
        if isinstance(answer, str):
            print(f"  {site_name(name)}: {answer}")
            continue
        found += answer
        unpriced = sum(1 for x in answer if x.price_cents is None)
        linkless = sum(1 for x in answer if not x.url)
        photos = sum(1 for x in answer if x.photo_count)
        print(
            f"    {unpriced} without a price, {linkless} without a link, {photos} with photo "
            f"counts, {sum(1 for x in answer if x.condition)} with a condition, "
            f"{sum(1 for x in answer if x.posted_at)} with a date"
        )
    if isinstance(comps, str):
        print(f"  eBay sold comps: {comps}")
        market = None
    else:
        market = deals.market_price(comps, query)
        price = f"${market.typical_cents / 100:.2f} from {market.basis}" if market else "none"
        print(f"  eBay sold comps: {len(comps)} parsed; market price: {price}")
    top = deals.rank(found, query, budget_cents, market, limit=3)
    print(f"Top {len(top)} of {len(found)}:")
    for n, deal in enumerate(top, 1):
        print(f"  {n}. [{deal.score:+.0f}] {deal.listing.title}")
        print(f"     {deals.reason(deal, site_name(deal.listing.source))}")
        print(f"     {deal.listing.url}")
    return 0 if found else 1


if __name__ == "__main__":
    import argparse
    import sys

    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--probe", metavar="QUERY", help="run one small paid search per source")
    parser.add_argument("--max", type=int, help="the buyer's max, in whole dollars")
    parser.add_argument("--zip", default="", help="a ZIP code for sources that search around one")
    args = parser.parse_args()
    if not args.probe:
        parser.error("--probe QUERY is the only command")
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s [%(name)s] %(message)s")
    budget = args.max * 100 if args.max else None
    sys.exit(asyncio.run(probe(args.probe, budget, args.zip)))
