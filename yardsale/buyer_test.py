"""End-to-end check for the Yardsale Bureau prototype. Start bureau_server first, then:

    uv run python -m yardsale.buyer_test --count 12            # live
    uv run python -m yardsale.buyer_test --count 12 --offline

This separate buyer agent lists N items through the Market, offers every item agent 55% of its
asking price (below the seller's floor, so the agent counters), accepts each counter-offer, and
checks that every deal closes. In live mode the buyer finds the agents
only through the Almanac, then looks every address up on the Almanac API to confirm it.
Exits 0 when every check passes.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from dataclasses import dataclass, field
from uuid import uuid4

from uagents import Agent, Context, Protocol
from uagents.registration import AlmanacApiRegistrationPolicy
from uagents.resolver import Resolver
from uagents_core.registration import AgentRegistrationPolicy
from uagents_core.types import DeliveryStatus

from yardsale.common import (
    BUREAU_PORT,
    BUYER_PORT,
    MARKET_FILE,
    almanac_endpoints,
    derive_seed,
    dollars,
    endpoint,
    read_json,
    wait_until,
)
from yardsale.models import (
    AcceptCounter,
    Counter,
    Deal,
    Listed,
    ListingRejected,
    ListItem,
    Offer,
    OfferRejected,
)
from yardsale.offline import (
    LocalDirectoryResolver,
    NoRegistration,
    publish_locally,
    stay_offline,
)

log = logging.getLogger("yardsale.buyer")

SAMPLE_ITEMS = [
    ("Oak bookshelf", 8000),
    ("Road bike", 22000),
    ("Cast iron skillet", 3500),
    ("Mid-century lamp", 6000),
    ("Board game bundle", 4500),
    ("Acoustic guitar", 15000),
    ("Espresso machine", 12000),
    ("Kids' scooter", 2500),
    ("Vinyl record crate", 5000),
    ("Camping tent", 9000),
    ("Standing desk", 25000),
    ("Polaroid camera", 7000),
]
OFFER_PERCENT = 55  # below the floor, but not a lowball, so the item agent comes down
FLOOR_PERCENT = 60


@dataclass
class Tracker:
    count: int
    done: asyncio.Event = field(default_factory=asyncio.Event)
    listed: dict[str, Listed] = field(default_factory=dict)  # item_id -> Listed
    offers: dict[str, int] = field(default_factory=dict)
    counters: dict[str, int] = field(default_factory=dict)
    deals: dict[str, Deal] = field(default_factory=dict)
    failures: list[str] = field(default_factory=list)

    def check_done(self) -> None:
        if len(self.deals) + len(self.failures) >= self.count:
            self.done.set()


TRACKER: Tracker | None = None
buyer_proto = Protocol(name="YardsaleTestBuyer", version="0.1.0")


def tracker() -> Tracker:
    if TRACKER is None:
        raise RuntimeError("tracker not initialised")
    return TRACKER


@buyer_proto.on_message(Listed)
async def on_listed(ctx: Context, sender: str, msg: Listed) -> None:
    t = tracker()
    t.listed[msg.item_id] = msg
    offer = msg.asking_cents * OFFER_PERCENT // 100
    t.offers[msg.item_id] = offer
    status = await ctx.send(msg.item_address, Offer(item_id=msg.item_id, offer_cents=offer))
    if status.status == DeliveryStatus.FAILED:
        t.failures.append(f"{msg.title}: could not reach item agent ({status.detail})")
        t.check_done()


@buyer_proto.on_message(ListingRejected)
async def on_listing_rejected(ctx: Context, sender: str, msg: ListingRejected) -> None:
    tracker().failures.append(f"listing {msg.listing_ref} rejected: {msg.reason}")
    tracker().check_done()


@buyer_proto.on_message(Counter)
async def on_counter(ctx: Context, sender: str, msg: Counter) -> None:
    t = tracker()
    t.counters[msg.item_id] = msg.counter_cents
    listed = t.listed.get(msg.item_id)
    floor = listed.asking_cents * FLOOR_PERCENT // 100 if listed else 0
    if not msg.agreed and msg.counter_cents <= floor:
        t.failures.append(f"{msg.item_id}: a counter-offer showed the seller's floor")
    await ctx.send(sender, AcceptCounter(item_id=msg.item_id, counter_cents=msg.counter_cents))


@buyer_proto.on_message(Deal)
async def on_deal(ctx: Context, sender: str, msg: Deal) -> None:
    t = tracker()
    listed = t.listed.get(msg.item_id)
    if listed is None or listed.item_address != sender:
        t.failures.append(f"deal for {msg.item_id} came from an unexpected agent {sender}")
    else:
        t.deals[msg.item_id] = msg
    t.check_done()


@buyer_proto.on_message(OfferRejected)
async def on_offer_rejected(ctx: Context, sender: str, msg: OfferRejected) -> None:
    tracker().failures.append(f"offer on {msg.item_id} rejected: {msg.reason}")
    tracker().check_done()


def report(t: Tracker, market: str, almanac: dict[str, list[str]] | None) -> int:
    addresses = [item.item_address for item in t.listed.values()]
    countered = [i for i in t.listed if i in t.counters]
    print("\n=== Yardsale Bureau test ===")
    print(f"Mode: {'offline (local directory)' if almanac is None else 'live (Almanac API)'}")
    print(f"Market agent: {market}")
    print(f"{'item':<20} {'ask':>9} {'offer':>9} {'counter':>9} {'deal':>9}  item agent address")
    for item_id, item in t.listed.items():
        deal = t.deals.get(item_id)
        print(
            f"{item.title[:20]:<20} {dollars(item.asking_cents):>9} "
            f"{dollars(t.offers.get(item_id, 0)):>9} {dollars(t.counters.get(item_id, 0)):>9} "
            f"{dollars(deal.price_cents) if deal else '-':>9}  {item.item_address}"
        )

    checks = [
        (f"item agents spawned: {len(t.listed)}/{t.count}", len(t.listed) == t.count),
        (
            f"unique item agent addresses: {len(set(addresses))}",
            len(set(addresses)) == t.count,
        ),
        (f"offers countered: {len(countered)}/{t.count}", len(countered) == t.count),
        (f"deals closed: {len(t.deals)}/{t.count}", len(t.deals) == t.count),
    ]
    if almanac is not None:
        expected = endpoint(BUREAU_PORT)
        found = [a for a, eps in almanac.items() if expected in eps]
        checks.append(
            (
                f"Almanac API lookups pointing at {expected}: {len(found)}/{len(almanac)} "
                "(market + every item agent)",
                len(found) == len(almanac),
            )
        )
        registered = sum(item.almanac_registered for item in t.listed.values())
        checks.append(
            (
                f"Market reported Almanac registration OK: {registered}/{t.count}",
                registered == t.count,
            )
        )
    print()
    for label, ok in checks:
        print(f"[{'PASS' if ok else 'FAIL'}] {label}")
    for failure in t.failures:
        print(f"[FAIL] {failure}")
    passed = all(ok for _, ok in checks) and not t.failures
    print(f"\nResult: {'PASS' if passed else 'FAIL'}")
    return 0 if passed else 1


async def main(count: int, offline: bool, wait_seconds: float) -> int:
    global TRACKER
    if not MARKET_FILE.exists():
        print("Start bureau_server first; it writes the Market address.", file=sys.stderr)
        return 2
    market_info = read_json(MARKET_FILE)
    if bool(market_info["offline"]) != offline:
        print("bureau_server and buyer_test must both use --offline, or neither.", file=sys.stderr)
        return 2
    market: str = market_info["address"]
    TRACKER = Tracker(count=count)
    if offline:
        stay_offline()

    almanac_policy = None if offline else AlmanacApiRegistrationPolicy(logger=log)
    policy: AgentRegistrationPolicy = almanac_policy or NoRegistration()
    resolver: Resolver | None = LocalDirectoryResolver() if offline else None
    agent = Agent(
        name="yardsale-test-buyer",
        seed=derive_seed("test-buyer"),
        port=BUYER_PORT,
        endpoint=[endpoint(BUYER_PORT)],
        resolve=resolver,
        registration_policy=policy,
        loop=asyncio.get_running_loop(),
        publish_agent_details=False,
        enable_agent_inspector=False,
    )
    agent.include(buyer_proto)
    if offline:
        publish_locally(agent.address, endpoint(BUYER_PORT))

    @agent.on_event("startup")
    async def list_items(ctx: Context) -> None:
        if not offline:
            # Replies come back through the Almanac, so the buyer must be registered first,
            # and the Market must be findable there.
            async def buyer_registered() -> bool:
                assert almanac_policy is not None
                return almanac_policy.last_successful_registration is not None

            async def market_resolvable() -> bool:
                return bool(await almanac_endpoints(market))

            await wait_until(buyer_registered, "the buyer's own Almanac registration")
            await wait_until(market_resolvable, "the Market on the Almanac")
        run_id = uuid4().hex[:6]  # fresh refs, so repeat runs list new items
        for n in range(count):
            title, asking = SAMPLE_ITEMS[n % len(SAMPLE_ITEMS)]
            if n >= len(SAMPLE_ITEMS):
                title = f"{title} #{n // len(SAMPLE_ITEMS) + 1}"
            await ctx.send(
                market,
                ListItem(
                    listing_ref=f"test-{run_id}-{n}",
                    title=title,
                    asking_cents=asking,
                    floor_cents=asking * FLOOR_PERCENT // 100,
                    seller_id=f"seller-{n % 3}",
                ),
            )

    # setup() + start_server() instead of run_async(): run_async() cancels every other task in
    # the loop when it stops, which would cancel this function before it can report.
    agent.setup()
    server = asyncio.get_running_loop().create_task(agent.start_server())
    try:
        await asyncio.wait_for(TRACKER.done.wait(), wait_seconds)
    except TimeoutError:
        TRACKER.failures.append(f"timed out after {wait_seconds:.0f}s")

    almanac = None
    if not offline:
        addresses = [market, *(item.item_address for item in TRACKER.listed.values())]
        results = await asyncio.gather(*(almanac_endpoints(a) for a in addresses))
        almanac = dict(zip(addresses, results, strict=True))
    code = report(TRACKER, market, almanac)
    server.cancel()
    return code


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--count", type=int, default=12)
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--timeout", type=float, default=180.0)
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s [%(name)s] %(message)s")
    sys.exit(asyncio.run(main(args.count, args.offline, args.timeout)))
