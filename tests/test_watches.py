"""Standing searches: polling, "is this new?", budgets, caps, and expiry.

Sources are fakes, so nothing here reaches Apify or spends anything.
"""

from __future__ import annotations

import pytest

from yardsale import watches
from yardsale.sources import ExternalListing, Source, SourceError
from yardsale.store import Store, Watch

OWNER = "asi1:abc123"


class Clock:
    def __init__(self, now: float = 1_000_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def listing(external_id: str, price_cents: int | None = 20000, source: str = "craigslist"):
    return ExternalListing(
        source=source,
        external_id=external_id,
        title=f"bike {external_id}",
        price_cents=price_cents,
        url=f"https://example.test/{external_id}",
    )


def fake_source(*batches: list[ExternalListing]) -> tuple[Source, list[int]]:
    """A source that returns each batch in turn, then repeats the last. Records call count."""
    calls = [0]

    async def source(query: str, scope: str, limit: int) -> list[ExternalListing]:
        index = min(calls[0], len(batches) - 1)
        calls[0] += 1
        return batches[index]

    return source, calls


async def failing_source(query: str, scope: str, limit: int) -> list[ExternalListing]:
    raise SourceError("the site is down")


async def exploding_source(query: str, scope: str, limit: int) -> list[ExternalListing]:
    raise RuntimeError("unexpected")


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def db(clock: Clock):
    store = Store(clock=clock)
    yield store
    store.close()


def make(db: Store, clock: Clock, **kwargs) -> Watch:
    return watches.start(db, watches.new_watch(OWNER, "road bike", clock(), **kwargs))


# --- Creating -------------------------------------------------------------------------------


def test_a_new_watch_runs_for_a_week_at_six_hours(db: Store, clock: Clock) -> None:
    watch = make(db, clock)
    assert watch.every_seconds == 6 * 3600.0
    assert watch.expires_at == clock() + 7 * 86400.0
    assert db.watches_for(OWNER) == [watch]


def test_a_watch_needs_something_to_search_for(clock: Clock) -> None:
    for blank in ("", "   ", "\n"):
        with pytest.raises(watches.WatchError, match="what to watch for"):
            watches.new_watch(OWNER, blank, clock())


def test_the_query_is_tidied(clock: Clock) -> None:
    watch = watches.new_watch(OWNER, "  road   bike\n ", clock())
    assert watch.query == "road bike"


def test_one_conversation_cannot_run_unlimited_watches(db: Store, clock: Clock) -> None:
    for _ in range(watches.MAX_PER_OWNER):
        make(db, clock)
    with pytest.raises(watches.WatchError, match="most I can keep going"):
        make(db, clock)


def test_stopping_a_watch_frees_a_slot(db: Store, clock: Clock) -> None:
    kept = [make(db, clock) for _ in range(watches.MAX_PER_OWNER)]
    assert db.stop_watch(kept[0].watch_id, OWNER) is not None
    assert len(db.watches_for(OWNER)) == watches.MAX_PER_OWNER - 1
    make(db, clock)  # no longer refused


def test_only_the_owner_can_stop_a_watch(db: Store, clock: Clock) -> None:
    watch = make(db, clock)
    assert db.stop_watch(watch.watch_id, "asi1:someone-else") is None
    assert db.watches_for(OWNER) == [watch]


# --- Polling and "is this new?" -------------------------------------------------------------


async def test_the_first_poll_reports_everything(db: Store, clock: Clock) -> None:
    watch = make(db, clock)
    source, _ = fake_source([listing("a"), listing("b")])
    fresh = await watches.poll(db, watch, {"craigslist": source})
    assert [x.external_id for x in fresh] == ["a", "b"]


async def test_a_listing_that_stays_up_is_reported_once(db: Store, clock: Clock) -> None:
    """The point of the engine: Craigslist posts sit for days, and the buyer wants one alert."""
    watch = make(db, clock)
    source, calls = fake_source([listing("a"), listing("b")])
    first = await watches.poll(db, watch, {"craigslist": source})
    second = await watches.poll(db, watch, {"craigslist": source})
    third = await watches.poll(db, watch, {"craigslist": source})
    assert len(first) == 2
    assert second == [] and third == []
    assert calls[0] == 3  # it really did search all three times


async def test_only_the_new_listing_is_reported(db: Store, clock: Clock) -> None:
    watch = make(db, clock)
    source, _ = fake_source([listing("a")], [listing("a"), listing("b")])
    await watches.poll(db, watch, {"craigslist": source})
    fresh = await watches.poll(db, watch, {"craigslist": source})
    assert [x.external_id for x in fresh] == ["b"]


async def test_the_same_id_on_two_sites_is_two_listings(db: Store, clock: Clock) -> None:
    """eBay and Craigslist ids are unrelated, so the source is part of a hit's identity."""
    watch = make(db, clock)
    source, _ = fake_source([listing("1", source="craigslist"), listing("1", source="ebay")])
    fresh = await watches.poll(db, watch, {"craigslist": source})
    assert len(fresh) == 2


async def test_findings_queue_until_the_buyer_is_told(db: Store, clock: Clock) -> None:
    watch = make(db, clock)
    source, _ = fake_source([listing("a"), listing("b")])
    await watches.poll(db, watch, {"craigslist": source})
    assert len(db.untold_hits(watch.watch_id)) == 2
    assert db.count_untold(OWNER) == 2
    db.mark_told(watch.watch_id)
    assert db.untold_hits(watch.watch_id) == []
    assert db.count_untold(OWNER) == 0


async def test_a_told_listing_is_not_reported_again(db: Store, clock: Clock) -> None:
    watch = make(db, clock)
    source, _ = fake_source([listing("a")])
    await watches.poll(db, watch, {"craigslist": source})
    db.mark_told(watch.watch_id)
    assert await watches.poll(db, watch, {"craigslist": source}) == []


async def test_a_queued_listing_survives_a_restart(db: Store, clock: Clock, tmp_path) -> None:
    path = tmp_path / "watch.sqlite3"
    first = Store(path, clock=clock)
    watch = watches.start(first, watches.new_watch(OWNER, "road bike", clock()))
    source, _ = fake_source([listing("a")])
    await watches.poll(first, watch, {"craigslist": source})
    first.close()

    again = Store(path, clock=clock)
    try:
        assert [x.external_id for x in again.untold_hits(watch.watch_id)] == ["a"]
        assert await watches.poll(again, again.watch(watch.watch_id), {"craigslist": source}) == []
    finally:
        again.close()


# --- Budgets --------------------------------------------------------------------------------


async def test_a_budget_filters_on_the_asking_price(db: Store, clock: Clock) -> None:
    watch = make(db, clock, budget_cents=30000)
    source, _ = fake_source([listing("cheap", 25000), listing("dear", 40000)])
    fresh = await watches.poll(db, watch, {"craigslist": source})
    assert [x.external_id for x in fresh] == ["cheap"]


async def test_an_unpriced_listing_is_kept_under_a_budget(db: Store, clock: Clock) -> None:
    """Craigslist posts often say "contact me". Dropping them would hide them all week."""
    watch = make(db, clock, budget_cents=10000)
    source, _ = fake_source([listing("ask", None)])
    assert len(await watches.poll(db, watch, {"craigslist": source})) == 1


def test_no_budget_keeps_everything() -> None:
    everything = [listing("a", 1), listing("b", 10**9), listing("c", None)]
    assert watches.affordable(everything, None) == everything


# --- Several sources, and failures ----------------------------------------------------------


async def test_every_source_is_searched(db: Store, clock: Clock) -> None:
    watch = make(db, clock)
    one, one_calls = fake_source([listing("a")])
    two, two_calls = fake_source([listing("b", source="ebay")])
    fresh = await watches.poll(db, watch, {"craigslist": one, "ebay": two})
    assert sorted(x.external_id for x in fresh) == ["a", "b"]
    assert one_calls[0] == 1 and two_calls[0] == 1


async def test_a_watch_can_name_the_sources_it_wants(db: Store, clock: Clock) -> None:
    watch = make(db, clock, sources=("ebay",))
    one, one_calls = fake_source([listing("a")])
    two, two_calls = fake_source([listing("b", source="ebay")])
    fresh = await watches.poll(db, watch, {"craigslist": one, "ebay": two})
    assert [x.external_id for x in fresh] == ["b"]
    assert one_calls[0] == 0  # the unwanted source is never paid for
    assert two_calls[0] == 1  # the named one is searched exactly once


async def test_named_sources_survive_a_round_trip(db: Store, clock: Clock) -> None:
    watch = make(db, clock, sources=("ebay", "craigslist"))
    assert db.watch(watch.watch_id).sources == ("ebay", "craigslist")  # type: ignore[union-attr]


async def test_one_site_being_down_does_not_lose_the_others(db: Store, clock: Clock) -> None:
    watch = make(db, clock)
    working, _ = fake_source([listing("a")])
    fresh = await watches.poll(
        db, watch, {"craigslist": failing_source, "ebay": working, "other": exploding_source}
    )
    assert [x.external_id for x in fresh] == ["a"]


async def test_a_poll_that_finds_nothing_still_counts_as_a_poll(db: Store, clock: Clock) -> None:
    """Otherwise an empty watch would be due again immediately and poll in a loop."""
    watch = make(db, clock)
    await watches.poll(db, watch, {"craigslist": failing_source})
    assert db.watch(watch.watch_id).last_run_at == clock()  # type: ignore[union-attr]
    assert db.due_watches() == []


# --- Scheduling -----------------------------------------------------------------------------


async def test_a_new_watch_is_due_at_once(db: Store, clock: Clock) -> None:
    watch = make(db, clock)
    assert [w.watch_id for w in db.due_watches()] == [watch.watch_id]


async def test_a_polled_watch_waits_six_hours(db: Store, clock: Clock) -> None:
    watch = make(db, clock)
    source, _ = fake_source([listing("a")])
    await watches.poll(db, watch, {"craigslist": source})
    assert db.due_watches() == []
    clock.advance(watches.WATCH_EVERY - 1)
    assert db.due_watches() == []
    clock.advance(1)
    assert [w.watch_id for w in db.due_watches()] == [watch.watch_id]


async def test_run_due_polls_what_is_due_and_nothing_else(db: Store, clock: Clock) -> None:
    make(db, clock)
    source, calls = fake_source([listing("a")])
    found = await watches.run_due(db, {"craigslist": source})
    assert found == 1 and calls[0] == 1
    assert await watches.run_due(db, {"craigslist": source}) == 0
    assert calls[0] == 1  # not polled again, so not paid for again


async def test_run_due_is_bounded_so_a_backlog_cannot_spend_everything(
    db: Store, clock: Clock
) -> None:
    for i in range(watches.MAX_PER_OWNER):
        watches.start(db, watches.new_watch(f"owner-{i}", "bike", clock()))
    for i in range(watches.MAX_DUE_PER_RUN + 3):
        watches.start(db, watches.new_watch(f"other-{i}", "bike", clock()))
    source, calls = fake_source([])
    await watches.run_due(db, {"craigslist": source})
    assert calls[0] == watches.MAX_DUE_PER_RUN


async def test_a_watch_stops_after_its_week(db: Store, clock: Clock) -> None:
    watch = make(db, clock)
    clock.advance(watches.WATCH_FOR + 1)
    assert await watches.run_due(db, {"craigslist": failing_source}) == 0
    assert db.due_watches() == []
    assert db.watches_for(OWNER) == []  # no longer running
    assert db.watch(watch.watch_id).stopped is True  # type: ignore[union-attr]


async def test_a_stopped_watch_is_never_polled(db: Store, clock: Clock) -> None:
    watch = make(db, clock)
    db.stop_watch(watch.watch_id, OWNER)
    source, calls = fake_source([listing("a")])
    assert await watches.run_due(db, {"craigslist": source}) == 0
    assert calls[0] == 0


def test_watches_are_listed_oldest_first_so_numbering_is_stable(db: Store, clock: Clock) -> None:
    first = make(db, clock)
    clock.advance(10)
    second = make(db, clock)
    assert [w.watch_id for w in db.watches_for(OWNER)] == [first.watch_id, second.watch_id]
