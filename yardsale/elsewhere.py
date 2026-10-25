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
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace

from yardsale import craigslist, deals, places
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
# Sources that search around a ZIP code when the buyer names a place (craigslist.py reads one
# as "within MILES of it", facebook.py as its city's Marketplace).
ZIP_SOURCES = frozenset({"offerup", "craigslist", "facebook"})
# What each kind of failure (SourceError.kind) is called in a reply.
FAILURE_WORDS = {
    "timeout": "timed out",
    "blocked": "blocked",
    "busy": "too many searches at once",
    "credit": "out of search credit",
    "setup": "not set up here",
    "error": "an error",
}


def failure_words(reason: str) -> str:
    return FAILURE_WORDS.get(reason, FAILURE_WORDS["error"])


def and_list(names: list[str]) -> str:
    """ "eBay", "eBay and Craigslist", "eBay, Craigslist, and OfferUp"."""
    if len(names) <= 2:
        return " and ".join(names)
    return ", ".join(names[:-1]) + ", and " + names[-1]


def failed_sentence(found: Found) -> str:
    """ "OfferUp couldn't be searched just now (timed out)", "OfferUp and Craigslist couldn't
    be searched just now (OfferUp: timed out; Craigslist: blocked)", or ""."""
    failures = found.failures()
    if not failures:
        return ""
    names = and_list([site_name(name) for name, _ in failures])
    if len(failures) == 1:
        why = failure_words(failures[0][1])
    else:
        why = "; ".join(f"{site_name(name)}: {failure_words(reason)}" for name, reason in failures)
    return f"{names} couldn't be searched just now ({why})"


def unfit_sentence(found: Found) -> str:
    """For a search with no deals: what each site that answered had. "Craigslist had 14
    listings, none that fit; eBay had none." """
    parts = []
    for site in found.sites:
        if site.status == "empty":
            parts.append(f"{site_name(site.source)} had none")
        elif site.status == "ok":
            plural = "s" if site.raw != 1 else ""
            parts.append(f"{site_name(site.source)} had {site.raw} listing{plural}, none that fit")
    return "; ".join(parts) + "." if parts else ""


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
    "ebay", "craigslist", "offerup", "offer", "facebook", "fb", "marketplace", "other",
    "site", "sites", "website", "websites", "elsewhere", "online", "check", "try", "look",
    "else", "anywhere", "place", "places", "and", "too", "also", "watch", "watching", "keep",
    "let", "know", "if", "show", "shows", "pop", "pops", "come", "comes", "notify",
    "tell", "when", "it", "that", "this", "out", "on", "at", "from", "max", "under",
    "below", "budget", "most", "than", "less", "more", "no", "near", "around", "nearby",
    "local", "locally",
}  # fmt: skip
# Phrases removed before the words are read: amounts with their "under" ("under $30", "max
# 300", "$30", "30 bucks"), "offer up", "if one shows up", and the Bay Area (each site's default
# region already). "up" and "one" used to be site words, which made "xbox one" "xbox".
_NOT_SEARCHED = re.compile(
    r"\b(?:under|below|max(?:imum)?|budget(?:\s+of)?(?:\s+is)?|up\s+to|at\s+most|less\s+than|"
    r"no\s+more\s+than|for)\s*\$\s?\d[\d,]*(?:\.\d+)?|"
    r"\b(?:under|below|max(?:imum)?|budget(?:\s+of)?(?:\s+is)?|up\s+to|at\s+most|less\s+than|"
    r"no\s+more\s+than)\s*\d[\d,]*(?:\.\d+)?(?:\s*(?:dollars|bucks|usd))?\b|"
    r"\$\s?\d[\d,]*(?:\.\d+)?|\b\d[\d,]*(?:\.\d+)?\s*(?:dollars|bucks|usd)\b|"
    r"\boffer\s*up\b|\b(?:if\s+)?(?:one|any|it|something)?\s*(?:shows?|pops?|comes?)\s+up\b|"
    r"\b(?:in|around|near)?\s*(?:the\s+)?(?:sf\s+)?bay\s+area\b",
    re.IGNORECASE,
)


_TOKEN = re.compile(r"[a-z0-9]+(?:[.-][a-z0-9]+)*")


def site_name(source: str) -> str:
    return SITE_NAMES.get(source, source.capitalize())


def search_words(query: str) -> str:
    """The words worth searching other sites for: no filler, no site names, no amounts, no
    place ("in Berkeley", "near 94704": places.split reads that). Model numbers and sizes
    stay ("jordan 4", "trek fx 2", "size 10"): dropping every number made "air jordan 4 retro"
    a search for "air jordan retro", which found Jordan 11s and 12s on October 24."""
    _, text = places.split(query)
    text = _NOT_SEARCHED.sub(" ", text).lower().replace("'", "").replace("\u2019", "")
    kept: list[str] = []
    # "10.5" and "FV5029-141" stay whole, so a size or style code isn't read as two numbers.
    for token in _TOKEN.findall(text):
        word = token if any(c in token for c in ".-") else "".join(words(token))
        if word and word not in STOPWORDS and word not in CHEAP and word not in SITE_WORDS:
            kept.append(word)
    return " ".join(kept)


def search_place(query: str) -> str:
    """The ZIP code of the place a buyer's message names, or ""."""
    return places.zip_for(query)


@dataclass(frozen=True)
class SiteResult:
    """How one site's search went. "ok": it answered with listings (`raw` of them, `kept` of
    those fit the search and budget). "empty": it answered with none, a real "nothing there".
    "failed": it couldn't be searched, and `reason` says why (SourceError.kind: timeout,
    blocked, busy, credit, setup, error). Only "ok" and "empty" sites may be said to have
    nothing."""

    source: str
    status: str  # ok | empty | failed
    raw: int = 0
    kept: int = 0
    reason: str = ""

    @property
    def answered(self) -> bool:
        return self.status != "failed"


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
    sites: tuple[SiteResult, ...] = ()  # per site, in the order searched; () from older peers
    place: str = ""  # where the local sites searched: "Berkeley (94704)", "the SF Bay Area"

    @property
    def listings(self) -> tuple[ExternalListing, ...]:
        return tuple(deal.listing for deal in self.deals)

    @property
    def typical_cents(self) -> int | None:
        return self.market.typical_cents if self.market else None

    @property
    def loose(self) -> bool:
        """The deals are only close matches: nothing said half the search words."""
        return bool(self.deals) and all("loose_match" in deal.flags for deal in self.deals)

    def site(self, source: str) -> SiteResult:
        for result in self.sites:
            if result.source == source:
                return result
        status = "failed" if source in self.failed else "ok"
        return SiteResult(source, status, reason="error" if status == "failed" else "")

    def failures(self) -> list[tuple[str, str]]:
        """(source, reason) for each site that couldn't be searched."""
        return [(name, self.site(name).reason or "error") for name in self.failed]


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
        self.per_source = PER_SOURCE
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
        """(search words, budget, ZIP). The ZIP is the caller's, else the place the words name
        ("in Berkeley"), else the server's YARDSALE_ZIP, else "" (each site's default)."""
        zip_code = zip_code.strip() or search_place(query) or places.server_zip()
        return search_words(query), budget_cents, zip_code

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
    ) -> tuple[list[ExternalListing] | None, str]:
        """(the site's listings, "") or (None, why it failed: a SourceError kind)."""
        where = self.where.get(name, "")
        if zip_code and name in ZIP_SOURCES:
            where = zip_code
        try:
            return await source(deals.site_query(query) or query, where, self.per_source), ""
        except SourceError as ex:
            # The reason never has the search words: they're only in the request body.
            log.warning(f"other sites: {name} couldn't be searched ({ex.kind}: {ex})")
            return None, ex.kind
        except Exception:
            log.exception(f"other sites: {name} failed")
            return None, "error"

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
        found = [linked(listing, zip_code) for answer, _ in answers if answer for listing in answer]
        searched = tuple(
            name for name, (answer, _) in zip(names, answers, strict=True) if answer is not None
        )
        failed = tuple(
            name for name, (answer, _) in zip(names, answers, strict=True) if answer is None
        )
        # Every listing that fits, so each site's count is known; the buyer sees the best LIMIT.
        fitting = deals.rank(found, query, budget_cents, market, limit=len(found) or 1)
        chosen = fitting[: deals.LIMIT]
        sites = tuple(
            site_result(name, answer, reason, fitting)
            for name, (answer, reason) in zip(names, answers, strict=True)
        )
        each = ", ".join(
            f"{s.source} {s.reason}" if s.reason else f"{s.source} {s.kept}/{s.raw}" for s in sites
        )
        log.info(
            f"other sites: {len(chosen)} of {len(found)} listing(s) kept from "
            f"{len(searched)} source(s), {len(failed)} failed ({each}), "
            f"{'with' if market else 'without'} a market price, "
            f"in {time.monotonic() - started:.1f}s"
        )
        return Found(
            query,
            budget_cents,
            tuple(chosen),
            searched,
            failed,
            len(found),
            market,
            note,
            sites,
            places.describe(zip_code),
        )


def linked(listing: ExternalListing, zip_code: str) -> ExternalListing:
    """A Craigslist post the actor gave no link gets a search link for its title, instead of
    being dropped by the deal finder (deals.score needs a link)."""
    if listing.url or listing.source != craigslist.SOURCE:
        return listing
    return replace(listing, url=craigslist.search_link(listing, zip_code))


def site_result(
    name: str, answer: list[ExternalListing] | None, reason: str, fitting: list[ScoredDeal]
) -> SiteResult:
    if answer is None:
        return SiteResult(name, "failed", reason=reason or "error")
    if not answer:
        return SiteResult(name, "empty")
    kept = sum(1 for deal in fitting if deal.listing.source == name)
    return SiteResult(name, "ok", raw=len(answer), kept=kept)


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


async def probe(
    query: str,
    budget_cents: int | None,
    zip_code: str,
    only: list[str] | None = None,
    with_comps: bool = True,
) -> int:
    """Run every source once, small, against the live actors, through the same DealFinder the
    server uses, and print what came back: each site's outcome (listings, kept, or why it
    failed), what each parser could read, the market price, the place searched, and the top
    deals with their scores. Paid."""
    from yardsale import bureau_server  # pulls in the whole server; fine by hand
    from yardsale.researcher import search_ebay_sold

    sources = {
        name: source
        for name, source in bureau_server.other_sites().items()
        if not only or name in only
    }
    names = list(sources)
    words = search_words(query)
    print(
        f"Probing {', '.join(site_name(n) for n in names)} for {words!r} "
        f"(the sites get {deals.site_query(words)!r})"
        f"{f' under ${budget_cents // 100}' if budget_cents else ''}, "
        f"{PROBE_PER_SOURCE} results each{' plus eBay sold comps' if with_comps else ''}: "
        f"about {probe_cost_usd(names)} of Apify credit."
    )
    seen: dict[str, list[ExternalListing]] = {}

    def recording(name: str) -> Source:
        async def search(words: str, where: str, limit: int) -> list[ExternalListing]:
            seen[name] = await sources[name](words, where, limit)
            return seen[name]

        return search

    finder = DealFinder(
        {name: recording(name) for name in names},
        comps=search_ebay_sold if with_comps else None,
    )
    finder.per_source = PROBE_PER_SOURCE
    started = time.monotonic()
    found = await finder.find(query, budget_cents, zip_code)
    print(f"Searched {found.place} in {time.monotonic() - started:.1f}s:")
    for site in found.sites:
        if site.status == "failed":
            print(f"  {site_name(site.source)}: FAILED ({site.reason})")
            continue
        answer = seen.get(site.source, [])
        print(
            f"  {site_name(site.source)}: {site.status}, {site.raw} listing(s), {site.kept} fit; "
            f"{sum(1 for x in answer if x.price_cents is None)} without a price, "
            f"{sum(1 for x in answer if not x.url)} without a link, "
            f"{sum(1 for x in answer if x.image_url)} with a photo, "
            f"{sum(1 for x in answer if x.condition)} with a condition, "
            f"{sum(1 for x in answer if x.posted_at)} with a date"
        )
    market = found.market
    price = f"${market.typical_cents / 100:.2f} from {market.basis}" if market else "none"
    print(f"  eBay sold comps: market price {price} {found.comps_note}")
    closest = " (close matches only)" if found.loose else ""
    print(f"Top {len(found.deals)} of {found.considered}{closest}:")
    for n, deal in enumerate(found.deals, 1):
        print(f"  {n}. [{deal.score:+.0f}] {deal.listing.title}")
        print(f"     {deals.reason(deal, site_name(deal.listing.source))}; {deal.listing.location}")
        print(f"     {deal.listing.url}")
    return 0 if found.deals else 1


if __name__ == "__main__":
    import argparse
    import sys

    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--probe", metavar="QUERY", help="run one small paid search per source")
    parser.add_argument("--max", type=int, help="the buyer's max, in whole dollars")
    parser.add_argument("--zip", default="", help="a ZIP code for sources that search around one")
    parser.add_argument("--sites", default="", help="only these sources, comma separated")
    parser.add_argument("--no-comps", action="store_true", help="skip the eBay sold comps")
    args = parser.parse_args()
    if not args.probe:
        parser.error("--probe QUERY is the only command")
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s [%(name)s] %(message)s")
    budget = args.max * 100 if args.max else None
    only = [name.strip() for name in args.sites.split(",") if name.strip()]
    sys.exit(asyncio.run(probe(args.probe, budget, args.zip, only, not args.no_comps)))
