"""Standing searches: keep looking on other sites, and remember what's new.

A buyer asks for something that isn't listed yet ("watch for a road bike under $300"). Yardsale
saves the search and polls every source every WATCH_EVERY seconds until the week is up. Each
poll keeps only listings it hasn't seen before, so an item that sits on Craigslist for five days
is reported once, not twenty times.

Findings are stored, not pushed. Whether an agent can message an ASI:One conversation days
later is unproven: the planner relays a whole session from one address, and that session may be
long gone. So `untold_hits` queues what was found and the buyer is told next time they say
anything. If proactive delivery does turn out to work, it reads the same queue.

Cost matters, because every poll is a paid scrape. At 6 hours, a week of one watch over both
sources is about $2.50:

    eBay        20 listings x $0.0025 = $0.05
    Craigslist  20 listings x $0.0020 = $0.04
    4 polls a day x 7 days x $0.09    = $2.52

So watches are capped per owner, expire after a week, and poll a bounded number of sources.
A source that fails is skipped and tried again next poll; one site being down doesn't lose a
whole watch.
"""

from __future__ import annotations

import asyncio
import logging
from uuid import uuid4

from yardsale.sources import ExternalListing, Source, SourceError
from yardsale.store import Store, Watch

log = logging.getLogger("yardsale.watches")

WATCH_EVERY = 6 * 3600.0  # seconds between polls
WATCH_FOR = 7 * 86400.0  # a watch stops after a week
MAX_PER_OWNER = 5  # standing searches one conversation may run at once
MAX_PER_POLL = 20  # listings asked of each source, which is what each poll costs
MAX_DUE_PER_RUN = 10  # watches polled in one interval, so a backlog can't spend everything


class WatchError(ValueError):
    """A watch couldn't be made. The message is shown to the buyer."""


def new_watch(
    owner: str,
    query: str,
    now: float,
    scope: str = "",
    budget_cents: int | None = None,
    sources: tuple[str, ...] = (),
    every_seconds: float = WATCH_EVERY,
    lasting: float = WATCH_FOR,
) -> Watch:
    """Build a watch. Raises WatchError if the buyer gave nothing to search for."""
    if not query.strip():
        raise WatchError('Tell me what to watch for, like "watch for a road bike under $300".')
    return Watch(
        watch_id=uuid4().hex[:12],
        owner=owner,
        query=" ".join(query.split()),
        scope=scope.strip(),
        budget_cents=budget_cents,
        sources=sources,
        every_seconds=every_seconds,
        created_at=now,
        expires_at=now + lasting,
    )


def start(store: Store, watch: Watch) -> Watch:
    """Save a watch, refusing one over the per-owner cap."""
    running = store.watches_for(watch.owner)
    if len(running) >= MAX_PER_OWNER:
        raise WatchError(
            f"You're already watching {len(running)} searches, which is the most I can keep "
            "going at once. Stop one first."
        )
    return store.add_watch(watch)


def affordable(listings: list[ExternalListing], budget_cents: int | None) -> list[ExternalListing]:
    """Within budget on the asking price. Listings with no readable price are kept: an unpriced
    post is often a real item, and dropping it would hide it for the whole week."""
    if budget_cents is None:
        return listings
    return [x for x in listings if x.price_cents is None or x.price_cents <= budget_cents]


async def _from_source(name: str, source: Source, watch: Watch) -> list[ExternalListing]:
    """One source's answer for one watch. A failure is logged and treated as "nothing found"."""
    try:
        return await source(watch.query, watch.scope, MAX_PER_POLL)
    except SourceError as ex:
        log.warning(f"watch {watch.watch_id}: {name} couldn't be searched ({ex})")
    except Exception:
        log.exception(f"watch {watch.watch_id}: {name} failed")
    return []


def chosen_sources(watch: Watch, sources: dict[str, Source]) -> dict[str, Source]:
    """The sources this watch uses. No names means every source there is."""
    if not watch.sources:
        return sources
    return {name: source for name, source in sources.items() if name in watch.sources}


async def poll(store: Store, watch: Watch, sources: dict[str, Source]) -> list[ExternalListing]:
    """Search every source for one watch and return only what hasn't been seen before.

    `last_run_at` is stamped even when nothing is found, so a watch that keeps coming up empty
    doesn't get polled again immediately.
    """
    using = chosen_sources(watch, sources)
    results = await asyncio.gather(
        *(_from_source(name, source, watch) for name, source in using.items())
    )
    found = [listing for batch in results for listing in batch]
    store.mark_polled(watch.watch_id)
    fresh = store.record_hits(watch.watch_id, affordable(found, watch.budget_cents))
    if fresh:
        log.info(
            f"watch {watch.watch_id} ({watch.query!r}): {len(fresh)} new of {len(found)} "
            f"across {len(using)} source(s)"
        )
    return fresh


async def run_due(store: Store, sources: dict[str, Source]) -> int:
    """Poll every watch that's due and stop the expired ones. Returns how many new listings
    were found. This is what the Market's interval calls."""
    stopped = store.expire_watches()
    if stopped:
        log.info(f"{stopped} watch(es) reached the end of their week")
    due = store.due_watches(MAX_DUE_PER_RUN)
    if not due:
        return 0
    found = 0
    for watch in due:  # one at a time: each poll costs money, and nothing here is urgent
        found += len(await poll(store, watch, sources))
    return found
