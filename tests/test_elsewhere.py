"""The deal finder: ranking other sites' listings, the cache, and joining a running search.

Sources are fakes, so nothing here reaches Apify or spends anything.
"""

from __future__ import annotations

import asyncio
import logging
import time

import pytest

from tests import fakes
from yardsale.elsewhere import COMPS_LATE, FAILED_CACHE_FOR, DealFinder, best, search_words
from yardsale.models import Comp
from yardsale.researcher import ResearchError
from yardsale.sources import ExternalListing, Source, SourceError


def listing(
    title: str,
    price_cents: int | None,
    source: str = "craigslist",
    shipping_cents: int | None = None,
    url: str | None = None,
) -> ExternalListing:
    return ExternalListing(
        source=source,
        external_id=title,
        title=title,
        price_cents=price_cents,
        url=url if url is not None else f"https://example.test/{abs(hash(title))}",
        shipping_cents=shipping_cents,
    )


class FakeSite:
    """A source that returns fixed listings, counts calls, and can be made to wait."""

    def __init__(self, listings: list[ExternalListing], fail: bool = False) -> None:
        self.listings = listings
        self.fail = fail
        self.calls: list[tuple[str, str, int]] = []
        self.gate: asyncio.Event | None = None

    async def __call__(self, query: str, where: str, limit: int) -> list[ExternalListing]:
        self.calls.append((query, where, limit))
        if self.gate is not None:
            await self.gate.wait()
        if self.fail:
            raise SourceError("the site is down")
        return self.listings


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


# --- Ranking ------------------------------------------------------------------------------------


def test_search_words_drop_filler_sites_and_amounts() -> None:
    assert search_words("search ebay and craigslist for a road bike under $300") == "road bike"
    assert search_words("watch for it") == ""
    assert search_words("find me a cheap Trek FX 2") == "trek fx"  # a lone digit isn't a word


def test_best_keeps_the_budget_including_shipping() -> None:
    found = best(
        [
            listing("road bike a", 25000),
            listing("road bike b", 28000, "ebay", shipping_cents=4000),  # $320 with shipping
            listing("road bike c", 31000),
        ],
        "road bike",
        30000,
    )
    assert [x.title for x in found] == ["road bike a"]


def test_best_ranks_matches_then_cheapest_and_sinks_parts() -> None:
    found = best(
        [
            listing("Road bike helmet", 3000),  # a part, unless asked for
            listing("Trek road bike", 22000),
            listing("Bike", 9000),  # half the words: kept, after the full matches
            listing("Specialized road bike", 18000),
            listing("Kitchen table", 5000),  # no words: dropped
        ],
        "road bike",
        30000,
    )
    # Full matches cheapest first, then a partial match, then the part.
    assert [x.title for x in found] == [
        "Specialized road bike",
        "Trek road bike",
        "Bike",
        "Road bike helmet",
    ]
    helmet = best([listing("Road bike helmet", 3000)], "bike helmet", None)
    assert helmet and helmet[0].title == "Road bike helmet"  # asked for: not sunk or dropped


def test_best_drops_unpriced_placeholders_and_linkless_listings() -> None:
    found = best(
        [
            listing("road bike 1", None),
            listing("road bike 2", 0),
            listing("road bike 3", 100 - 1),
            listing("road bike 4", 20000, url=""),
            listing("road bike 5", 20000),
        ],
        "road bike",
        None,
    )
    assert [x.title for x in found] == ["road bike 5"]


def test_best_shows_at_most_five() -> None:
    found = best([listing(f"road bike {n}", 10000 + n) for n in range(9)], "road bike", None)
    assert len(found) == 5 and found[0].price_cents == 10000


# --- Searching ----------------------------------------------------------------------------------


async def test_every_site_is_searched_once_and_cached() -> None:
    ebay = FakeSite([listing("road bike on ebay", 20000, "ebay")])
    craigslist = FakeSite([listing("road bike on craigslist", 15000)])
    clock = Clock()
    finder = DealFinder({"ebay": ebay, "craigslist": craigslist}, clock=clock)

    found = await finder.find("find me a road bike", 30000)
    assert [x.title for x in found.listings] == ["road bike on craigslist", "road bike on ebay"]
    assert found.searched == ("ebay", "craigslist") and found.failed == ()
    assert ebay.calls == [("road bike", "", 20)]

    again = await finder.find("road bike please", 30000)  # the same words and budget
    assert again is found and len(ebay.calls) == 1
    await finder.find("road bike", 25000)  # another budget is another search
    assert len(ebay.calls) == 2

    clock.now += 601  # ten minutes later the cache is stale
    await finder.find("road bike", 30000)
    assert len(ebay.calls) == 3


async def test_a_running_search_is_joined_not_repeated() -> None:
    site = FakeSite([listing("road bike", 20000)])
    site.gate = asyncio.Event()
    finder = DealFinder({"craigslist": site})
    first = asyncio.ensure_future(finder.find("road bike", None))
    second = asyncio.ensure_future(finder.find("road bike", None))
    await asyncio.sleep(0)
    with pytest.raises(TimeoutError):  # a caller giving up doesn't stop the search
        await asyncio.wait_for(finder.find("road bike", None), 0.01)
    site.gate.set()
    assert (await first) is (await second)
    assert len(site.calls) == 1


async def test_a_failing_site_is_reported_and_retried_after_a_minute() -> None:
    good = FakeSite([listing("road bike", 20000)])
    down = FakeSite([], fail=True)
    finder = DealFinder({"ebay": good, "offerup": down})
    found = await finder.find("road bike", None)
    assert found.failed == ("offerup",) and len(found.listings) == 1
    assert (await finder.find("road bike", None)) is found  # failed once, not asked again
    assert len(down.calls) == 1

    clock = Clock()
    alone = DealFinder({"offerup": down}, clock=clock)
    assert (await alone.find("road bike", None)).searched == ()
    await alone.find("road bike", None)  # asked again at once: the same failed search
    assert len(down.calls) == 2
    clock.now += FAILED_CACHE_FOR + 1  # nothing answered, so it's tried again a minute later
    await alone.find("road bike", None)
    assert len(down.calls) == 3


async def test_off_without_sources_and_needs_words() -> None:
    with pytest.raises(SourceError):
        await DealFinder({}).find("road bike", None)
    site = FakeSite([])
    with pytest.raises(SourceError):
        await DealFinder({"ebay": site}).find("search other sites", None)
    assert site.calls == []


async def test_the_log_never_has_the_search_or_the_max(caplog: pytest.LogCaptureFixture) -> None:
    sources: dict[str, Source] = {"ebay": FakeSite([listing("zebra road bike", 20000)])}
    with caplog.at_level(logging.INFO):
        await DealFinder(sources, comps=fakes.research_with_comps).find("zebra road bike", 31337)
    assert caplog.records
    assert "zebra" not in caplog.text and "313" not in caplog.text


# --- The market price ---------------------------------------------------------------------------


class FakeComps:
    def __init__(self, comps: list[Comp], fail: bool = False) -> None:
        self.comps = comps
        self.fail = fail
        self.calls: list[tuple[str, str, int]] = []

    async def __call__(self, query: str, condition: str, max_results: int) -> list[Comp]:
        self.calls.append((query, condition, max_results))
        if self.fail:
            raise ResearchError("Apify returned 503 (fake outage)")
        return self.comps


async def test_comps_price_the_market_once_per_search_words() -> None:
    comps = FakeComps(fakes.road_bike_comps())
    site = FakeSite([listing("Trek road bike", 18000), listing("Road bike", 29000)])
    clock = Clock()
    finder = DealFinder({"craigslist": site}, comps=comps, clock=clock)

    found = await finder.find("find me a road bike", 30000)
    assert found.market is not None and found.typical_cents == 31000
    assert [d.versus_market_pct for d in found.deals] == [42, 6]
    assert found.deals[0].why.startswith("42% under the $310 typical sold price")
    assert comps.calls == [("road bike", "any", 20)]

    await finder.find("road bike", 25000)  # another budget: another site search, same comps
    assert len(site.calls) == 2 and len(comps.calls) == 1
    await finder.find("hybrid bike", None)  # other words: other comps
    assert len(comps.calls) == 2

    clock.now += 601
    await finder.find("road bike", 30000)
    assert len(comps.calls) == 3


async def test_without_comps_or_when_they_fail_the_search_still_answers() -> None:
    site = FakeSite([listing("Trek road bike", 18000), listing("Giant road bike", 22000)])
    plain = await DealFinder({"craigslist": site}).find("road bike", None)
    assert plain.market is None and "no eBay sold-listings search" in plain.comps_note
    assert [x.title for x in plain.listings] == ["Trek road bike", "Giant road bike"]

    down = FakeComps([], fail=True)
    clock = Clock()
    finder = DealFinder({"craigslist": site}, comps=down, clock=clock)
    failed = await finder.find("road bike", None)
    assert failed.market is None and failed.comps_note == "the eBay sold-listings search failed"
    assert len(failed.deals) == 2
    await finder.find("road bike", 25000)  # another search at once: not a second paid try
    assert len(down.calls) == 1
    clock.now += FAILED_CACHE_FOR + 1  # a failed comps search is tried again a minute later
    await finder.find("road bike", 20000)
    assert len(down.calls) == 2

    nothing = FakeComps([])
    none = await DealFinder({"craigslist": site}, comps=nothing).find("road bike", None)
    assert none.market is None and none.comps_note == "no recent eBay sales matched the search"


# --- Deadlines: slow sites never hold up the answer ---------------------------------------------


class SlowSite(FakeSite):
    """A source that takes `seconds` to answer, as a live Apify run does."""

    def __init__(self, listings: list[ExternalListing], seconds: float) -> None:
        super().__init__(listings)
        self.seconds = seconds

    async def __call__(self, query: str, where: str, limit: int) -> list[ExternalListing]:
        self.calls.append((query, where, limit))
        await asyncio.sleep(self.seconds)
        return self.listings


async def test_a_fast_site_answers_by_the_deadline_and_a_slow_one_is_pending() -> None:
    fast = SlowSite([listing("road bike on offerup", 15000, "offerup")], 2.0)
    slow = SlowSite([listing("road bike on facebook", 12000, "facebook")], 30.0)
    finder = DealFinder({"offerup": fast, "facebook": slow}, within=2.5, limit=4.0)
    started = time.monotonic()
    running = finder.search("road bike", 30000)
    early = await running.early()
    took = time.monotonic() - started
    assert 2.0 <= took < 3.0  # the 2 s site's results, not the 30 s site's
    assert [x.title for x in early.listings] == ["road bike on offerup"]
    assert early.searched == ("offerup",) and early.pending == ("facebook",)
    assert early.failed == () and not running.done()
    assert (await running.early()) is early  # asking again: the same answer, at once

    final = await running.complete()  # the slow site gives up at the 4 s limit
    assert final.pending == () and final.failed == ("facebook",)
    assert time.monotonic() - started < 5.0
    assert len(slow.calls) == 1


async def test_everything_in_before_the_deadline_answers_at_once() -> None:
    fast = SlowSite([listing("road bike", 15000)], 0.05)
    finder = DealFinder({"craigslist": fast, "ebay": FakeSite([])}, within=5.0)
    started = time.monotonic()
    running = finder.search("road bike", None)
    early = await running.early()
    assert time.monotonic() - started < 1.0  # no waiting for the deadline
    assert early is await running.complete() and early.pending == ()


async def test_late_comps_score_without_them_then_with_them() -> None:
    async def slow_comps(query: str, condition: str, max_results: int) -> list[Comp]:
        await asyncio.sleep(0.5)
        return fakes.road_bike_comps()

    site = FakeSite([listing("Trek road bike", 18000)])
    finder = DealFinder({"craigslist": site}, comps=slow_comps, within=0.1)
    running = finder.search("road bike", 30000)
    early = await running.early()
    assert early.market is None and early.comps_late and early.comps_note == COMPS_LATE
    assert early.pending == () and len(early.deals) == 1
    assert early.deals[0].versus_market_pct is None  # not compared with recent sales
    final = await running.complete()
    assert final.market is not None and not final.comps_late
    assert final.deals[0].versus_market_pct == 42


async def test_a_failed_site_is_asked_once_per_search_even_while_others_run() -> None:
    down = FakeSite([], fail=True)
    slow = SlowSite([listing("road bike", 15000)], 0.3)
    finder = DealFinder({"offerup": down, "ebay": slow}, within=0.05)
    running = finder.search("road bike", None)
    early = await running.early()
    assert early.failed == ("offerup",) and early.pending == ("ebay",)
    again = finder.search("bike road", None)  # the same words: joined, not started again
    assert again is running
    final = await again.complete()
    assert final.failed == ("offerup",) and final.searched == ("ebay",)
    assert len(down.calls) == 1 and len(slow.calls) == 1


async def test_a_cache_hit_skips_every_source_and_answers_at_once() -> None:
    site = SlowSite([listing("road bike", 15000)], 0.2)
    comps = FakeComps(fakes.road_bike_comps())
    finder = DealFinder({"craigslist": site}, comps=comps)
    first = await finder.find("find me a Road Bikes", 30000)
    started = time.monotonic()
    again = finder.search("bike road", 30000)  # normalized: case, plurals, order, filler
    assert again.ready()
    assert (await again.early()) is first
    assert time.monotonic() - started < 0.05
    assert len(site.calls) == 1 and len(comps.calls) == 1
