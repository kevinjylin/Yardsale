"""The deal finder: the best-priced, best-quality listings on other sites for a buyer's search.

A buyer says what they want and the most they'll pay. `DealFinder.find` asks every source
(sources.py: Craigslist, eBay, OfferUp, ...) at once, looks up what the item actually sells for
(the Researcher's eBay sold-listings search, `comps`), scores every listing against that market
price and its own quality signals (deals.py), and returns the best few within the budget, each
with a one-line reason. `watches.py` does the same search on a schedule; this is the one-off
version. The chat and @yardsale's API use the Market's finder in this process; the Deal Finder
agent (dealfinder.py) runs the same one for other agents.

Sites answer at very different speeds (live on October 24: OfferUp 3 s, eBay 7 s, Craigslist
16 s, Facebook 20 s, the comps 5 s), so a buyer never waits for the slowest. Every source and
the comps start at once (`Search`). `Search.early()` answers once all of them are in, or
SOURCE_WITHIN seconds after the search started, whichever comes first: sites still searching
are `pending`, and listings are scored without the comps if those are late. `Search.complete()`
answers once every site has answered, failed, or run past SOURCE_LIMIT. Nothing is cancelled at
the deadline, since a cancelled request doesn't stop a paid Apify run.

Each answer says, per site (`Found.sites`), whether it answered with listings ("ok", with how
many fit), answered with none ("empty"), couldn't be searched ("failed", with why: timed out,
blocked, busy, out of credit, ...), or is still searching ("pending"). Only sites that answered
may be said to have nothing.

A place the buyer names ("in Berkeley", "near 94704") is a ZIP code (places.py), and the local
sites (ZIP_SOURCES) search around it; without one, YARDSALE_ZIP, else each site's default.

Every source call is a paid Apify run (about $0.03 to $0.05 for 20 results), and the comps
search is another (about $0.08 for 20 sales), so:

- Results are cached per (search words in any order, budget, ZIP) for CACHE_FOR seconds, and
  comps per search words for COMPS_CACHE_FOR. The same buyer asking twice, or the chat and the
  API asking the same thing, costs one run of each.
- A search already running is joined, not started again. That also lets the API answer within
  its 25 seconds and pick up the same search on the next call.
- Each source is asked once per search; a site that fails stays failed for that search. A
  search where no site answered, and a comps search that failed, are kept for FAILED_CACHE_FOR
  seconds, so a caller asking again right away doesn't start the same paid runs again.
- Each source is asked for at most PER_SOURCE results, and the answer keeps the best LIMIT.
- With no sources (demo mode, tests), the finder is off and nothing is searched.

Nothing found here can be haggled with by an agent (`negotiable` is False). The buyer's max and
search words are never logged; the log says how many listings came back.

    uv run python -m yardsale.elsewhere --probe "road bike" --max 300

runs one small paid search per source (about 10 results each) plus one small comps search
through the same DealFinder, and prints each site's outcome, what each parser made of the live
actor output, and the top deals. Set APIFY_TOKEN first; the output says roughly what it cost.
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
FAILED_CACHE_FOR = 60.0  # seconds a search nothing answered, or failed comps, are reused
SOURCE_WITHIN = 20.0  # seconds after a search starts that its first answer goes out
SOURCE_LIMIT = 150.0  # seconds after which a site still searching counts as failed
COMPS_LATE = "not compared with recent sales"  # comps_note when the comps missed the deadline
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
    blocked, busy, credit, setup, error). "pending": still searching when the answer went out.
    Only "ok" and "empty" sites may be said to have nothing."""

    source: str
    status: str  # ok | empty | failed | pending
    raw: int = 0
    kept: int = 0
    reason: str = ""

    @property
    def answered(self) -> bool:
        return self.status in ("ok", "empty")


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
    pending: tuple[str, ...] = ()  # sources still searching when this answer went out
    sites: tuple[SiteResult, ...] = ()  # per site, in the order searched; () from older peers
    place: str = ""  # where the local sites searched: "Berkeley (94704)", "the SF Bay Area"

    @property
    def comps_late(self) -> bool:
        return self.comps_note == COMPS_LATE

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
        if source in self.failed:
            return SiteResult(source, "failed", reason="error")
        return SiteResult(source, "pending" if source in self.pending else "ok")

    def failures(self) -> list[tuple[str, str]]:
        """(source, reason) for each site that couldn't be searched."""
        return [(name, self.site(name).reason or "error") for name in self.failed]


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


def best(
    listings: list[ExternalListing],
    query: str,
    budget_cents: int | None,
    limit: int = LIMIT,
    market: MarketPrice | None = None,
) -> list[ExternalListing]:
    """The best priced listings within the budget, best first (deals.fitting, no reasons)."""
    return [d.listing for d in deals.fitting(listings, query, budget_cents, market, limit=limit)]


def linked(listing: ExternalListing, zip_code: str) -> ExternalListing:
    """A Craigslist post the actor gave no link gets a search link for its title, instead of
    being dropped by the deal finder (deals.score needs a link)."""
    if listing.url or listing.source != craigslist.SOURCE:
        return listing
    return replace(listing, url=craigslist.search_link(listing, zip_code))


# A site's answer: (its listings, "") or (None, why it failed: a SourceError kind).
Answer = tuple[list[ExternalListing] | None, str]


def site_result(name: str, answer: Answer | None, fitting: list[ScoredDeal]) -> SiteResult:
    if answer is None:
        return SiteResult(name, "pending")
    listings, reason = answer
    if listings is None:
        return SiteResult(name, "failed", reason=reason or "error")
    if not listings:
        return SiteResult(name, "empty")
    kept = sum(1 for deal in fitting if deal.listing.source == name)
    return SiteResult(name, "ok", raw=len(listings), kept=kept)


class Search:
    """One search of every source and the comps, all started at once. `early()` is the answer by
    the deadline, `complete()` the answer once every site is in. Both can be awaited by any
    number of callers; a caller that stops waiting never stops the search."""

    def __init__(
        self,
        finder: DealFinder,
        query: str,
        budget_cents: int | None,
        zip_code: str,
    ) -> None:
        loop = asyncio.get_running_loop()
        self.query = query
        self.budget_cents = budget_cents
        self.zip_code = zip_code
        self.place = places.describe(zip_code)
        self.started = loop.time()
        self.deadline = self.started + finder.within
        self.names = list(finder.sources)
        self._sources = {
            name: asyncio.ensure_future(finder._one(name, finder.sources[name], query, zip_code))
            for name in self.names
        }
        self._market = asyncio.ensure_future(finder._market_or_none(query))
        self._done = asyncio.ensure_future(self._finish())
        self._partial: tuple[tuple[bool, ...], Found] | None = None
        self._told_early = False

    def done(self) -> bool:
        """Every site has answered, failed, or given up, and the comps are in."""
        return self._done.done()

    def ready(self) -> bool:
        """An answer is ready now: the search is done or past its deadline."""
        return self.done() or asyncio.get_running_loop().time() >= self.deadline

    def nothing_answered(self) -> bool:
        """Done, and no site could be searched (or the search itself broke)."""
        if not self._done.done():
            return False
        if self._done.cancelled() or self._done.exception() is not None:
            return True
        return not self._done.result().searched

    def broke(self) -> bool:
        return self._done.done() and (self._done.cancelled() or self._done.exception() is not None)

    async def early(self) -> Found:
        """What's in by the deadline: everything if every site answered sooner. Sites still
        searching are `pending`; without the comps, listings are scored against each other."""
        left = self.deadline - asyncio.get_running_loop().time()
        if left > 0 and not self._done.done():
            await asyncio.wait([self._done], timeout=left)  # wait() never cancels the search
        if self._done.done():
            return self._done.result()
        found = self.so_far()
        if not self._told_early:
            self._told_early = True
            log.info(
                f"other sites: first answer from {len(found.searched)} of {len(self.names)} "
                f"source(s), {len(found.pending)} still searching, "
                f"{'without' if found.comps_late else 'with'} the comps, "
                f"in {asyncio.get_running_loop().time() - self.started:.1f}s"
            )
        return found

    async def complete(self) -> Found:
        """The answer once every site is in (each gives up after SOURCE_LIMIT seconds)."""
        return await asyncio.shield(self._done)

    def so_far(self) -> Found:
        """Scored from what's in right now. The same object until something else comes in."""
        state = (*(task.done() for task in self._sources.values()), self._market.done())
        if self._partial is None or self._partial[0] != state:
            self._partial = (state, self._found())
        return self._partial[1]

    def _found(self) -> Found:
        answers: dict[str, Answer] = {
            name: task.result()
            for name, task in self._sources.items()
            if task.done() and not task.cancelled()
        }
        searched = tuple(n for n in self.names if n in answers and answers[n][0] is not None)
        failed = tuple(n for n in self.names if n in answers and answers[n][0] is None)
        pending = tuple(name for name in self.names if name not in answers)
        listings = [
            linked(listing, self.zip_code)
            for name in searched
            for listing in answers[name][0] or []
        ]
        if self._market.done() and not self._market.cancelled():
            market, note = self._market.result()
        else:
            market, note = None, COMPS_LATE
        chosen = deals.rank(listings, self.query, self.budget_cents, market)
        # Every listing that fits, so each site's count is known; the buyer sees `chosen`.
        fitting = deals.fitting(
            listings, self.query, self.budget_cents, market, limit=len(listings) or 1
        )
        sites = tuple(site_result(name, answers.get(name), fitting) for name in self.names)
        return Found(
            self.query,
            self.budget_cents,
            tuple(chosen),
            searched,
            failed,
            len(listings),
            market,
            note,
            pending,
            sites,
            self.place,
        )

    async def _finish(self) -> Found:
        await asyncio.gather(*self._sources.values(), self._market)
        found = self.so_far()
        each = ", ".join(
            f"{s.source} {s.reason}" if s.reason else f"{s.source} {s.kept}/{s.raw}"
            for s in found.sites
        )
        log.info(
            f"other sites: {len(found.deals)} of {found.considered} listing(s) kept from "
            f"{len(found.searched)} source(s), {len(found.failed)} failed ({each}), "
            f"{'with' if found.market else 'without'} a market price, "
            f"in {asyncio.get_running_loop().time() - self.started:.1f}s"
        )
        return found

    def cancel(self) -> None:
        """Stop every site still searching (shutting down; tests)."""
        for task in (*self._sources.values(), self._market, self._done):
            task.cancel()


SearchKey = tuple[str, int | None, str]


class DealFinder:
    """Searches every source at once, prices the market from eBay comps, and scores the result,
    with a short cache. The Market and the Deal Finder agent share one; tests use it directly."""

    def __init__(
        self,
        sources: dict[str, Source],
        where: dict[str, str] | None = None,
        comps: Comps | None = None,
        cache_for: float = CACHE_FOR,
        clock: Callable[[], float] = time.monotonic,
        within: float = SOURCE_WITHIN,
        limit: float = SOURCE_LIMIT,
    ) -> None:
        self.sources = dict(sources)
        self.where = where or {}  # a source's scope (a city, a ZIP code); "" means its default
        self.comps = comps  # None: no market prices, listings are judged against each other
        self.cache_for = cache_for
        self.clock = clock
        self.within = within  # seconds until a search's first answer
        self.limit = limit  # seconds until a site still searching counts as failed
        self.per_source = PER_SOURCE
        self._searches: dict[SearchKey, tuple[float, Search]] = {}
        self._comps: dict[str, tuple[float, asyncio.Task[MarketPrice | None]]] = {}

    @property
    def enabled(self) -> bool:
        return bool(self.sources)

    def site_names(self) -> str:
        """ "eBay, Craigslist, and OfferUp", for telling the buyer where we looked."""
        return names_of(list(self.sources))

    def _key(self, query: str, budget_cents: int | None, zip_code: str) -> SearchKey:
        """The same words in any order, the same budget, and the same ZIP are the same search.
        The ZIP is the caller's, else the place the words name ("in Berkeley"), else the
        server's YARDSALE_ZIP, else "" (each site's default)."""
        zip_code = zip_code.strip() or search_place(query) or places.server_zip()
        return " ".join(sorted(set(search_words(query).split()))), budget_cents, zip_code

    def _forget_old(self) -> None:
        now = self.clock()
        for key, (started, running) in list(self._searches.items()):
            if not running.done():
                continue
            # A search nothing answered is kept briefly, so asking again at once (an API caller
            # polling, a buyer repeating themselves) doesn't start the same paid runs again.
            keep = FAILED_CACHE_FOR if running.nothing_answered() else self.cache_for
            if running.broke() or now - started > keep:
                del self._searches[key]
        while len(self._searches) > MAX_CACHED:
            oldest = min(self._searches, key=lambda k: self._searches[k][0])
            if not self._searches[oldest][1].done():
                break
            del self._searches[oldest]
        for query, (started, comps_task) in list(self._comps.items()):
            if not comps_task.done():
                continue
            # No sales is a real answer; a failed comps search is tried again after a minute.
            failed = comps_task.cancelled() or comps_task.exception() is not None
            if now - started > (FAILED_CACHE_FOR if failed else COMPS_CACHE_FOR):
                del self._comps[query]

    def search(self, query: str, budget_cents: int | None, zip_code: str = "") -> Search:
        """The search for these words, budget, and ZIP: a cached or running one, or a new one.
        Raises SourceError if there's nothing to search for or no source is set up."""
        if not self.enabled:
            raise SourceError("searching other sites is off on this Yardsale server")
        words = search_words(query)
        if not words:
            raise SourceError('tell me what to look for, like "a road bike"')
        self._forget_old()
        key = self._key(query, budget_cents, zip_code)
        cached = self._searches.get(key)
        if cached is not None:
            return cached[1]
        running = Search(self, words, budget_cents, key[2])
        self._searches[key] = (self.clock(), running)
        return running

    async def find(self, query: str, budget_cents: int | None, zip_code: str = "") -> Found:
        """Search other sites for these words and budget, and wait for every site. Raises
        SourceError if there's nothing to search for or no source is set up; a source that fails
        is reported in `failed`."""
        return await self.search(query, budget_cents, zip_code).complete()

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

    async def _one(self, name: str, source: Source, query: str, zip_code: str) -> Answer:
        """(one site's listings, "") or (None, why it failed or that it ran past the limit: a
        SourceError kind). Asked once, with the search's core words (deals.site_query)."""
        where = self.where.get(name, "")
        if zip_code and name in ZIP_SOURCES:
            where = zip_code
        started = time.monotonic()
        asked = deals.site_query(query) or query
        try:
            listings = await asyncio.wait_for(source(asked, where, self.per_source), self.limit)
        except SourceError as ex:
            # The reason never has the search words: they're only in the request body.
            log.warning(f"other sites: {name} couldn't be searched ({ex.kind}: {ex})")
            return None, ex.kind
        except TimeoutError:
            log.warning(f"other sites: {name} didn't answer within {self.limit:.0f}s")
            return None, "timeout"
        except Exception:
            log.exception(f"other sites: {name} failed")
            return None, "error"
        log.info(f"other sites: {name} answered in {time.monotonic() - started:.1f}s")
        return listings, ""

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


def names_of(sources: list[str] | tuple[str, ...]) -> str:
    """ "eBay, Craigslist, and OfferUp": sites by name, for a sentence."""
    return and_list([site_name(name) for name in sources])


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
    print(deals.headline(found.deals, budget_cents, market, site_name))
    closest = " (close matches only)" if found.loose else ""
    print(f"Top {len(found.deals)} of {found.considered}{closest}:")
    for n, deal in enumerate(found.deals, 1):
        print(f"  {n}. [{deal.score:+.0f}] {deal.listing.title} {list(deal.flags)}")
        print(f"     {deals.reason(deal, site_name(deal.listing.source), budget_cents)}")
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
