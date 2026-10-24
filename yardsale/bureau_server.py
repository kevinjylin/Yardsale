"""Yardsale Bureau prototype: one process, one port, a new uAgent for every listed item.

    uv run python -m yardsale.bureau_server            # live: Almanac API
    uv run python -m yardsale.bureau_server --offline  # no Fetch servers

The Market agent receives ListItem messages and spins up an item agent for each one inside the
already-running Bureau. Item agents hold a private floor price and haggle with buyers. In live
mode every agent registers on the Almanac API (free, no chain transaction) with the Bureau's
endpoint. Nothing here connects to Agentverse, so none of these agents use Agentverse slots.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from dataclasses import dataclass, field
from uuid import uuid4

from uagents import Agent, Bureau, Context, Protocol
from uagents.registration import BatchAlmanacApiRegistrationPolicy
from uagents_core.registration import BatchRegistrationPolicy

from yardsale.common import (
    BUREAU_PORT,
    MARKET_FILE,
    derive_seed,
    dollars,
    endpoint,
    write_json,
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
    NoBatchRegistration,
    publish_locally,
)

log = logging.getLogger("yardsale.bureau")


@dataclass
class Listing:
    item_id: str
    title: str
    asking_cents: int
    floor_cents: int
    seller_id: str
    almanac_registered: bool = False
    open_counters: dict[str, int] = field(default_factory=dict)  # buyer address -> counter
    sold_to: str | None = None


@dataclass
class Runtime:
    bureau: Bureau
    registration: BatchRegistrationPolicy
    offline: bool
    listings: dict[str, Listing] = field(default_factory=dict)  # item agent address -> listing
    by_ref: dict[tuple[str, str], str] = field(default_factory=dict)  # (seller, ref) -> address


RUNTIME: Runtime | None = None

market_proto = Protocol(name="YardsaleMarket", version="0.1.0")
item_proto = Protocol(name="YardsaleHaggle", version="0.1.0")


def runtime() -> Runtime:
    if RUNTIME is None:
        raise RuntimeError("Bureau is not running")
    return RUNTIME


def new_agent(name: str, seed_label: str, offline: bool) -> Agent:
    return Agent(
        name=name,
        seed=derive_seed(seed_label),
        resolve=LocalDirectoryResolver() if offline else None,
        loop=asyncio.get_running_loop(),
        publish_agent_details=False,
        enable_agent_inspector=False,
    )


async def spawn_item_agent(rt: Runtime, listing: Listing) -> Agent:
    agent = new_agent(f"yardsale-item-{listing.item_id}", f"item:{listing.item_id}", rt.offline)
    agent.include(item_proto)
    rt.listings[agent.address] = listing

    # Joining a running Bureau: add() points the agent at the Bureau's loop, endpoint, and port,
    # and the global dispatcher already routes /submit envelopes by address. But the Bureau only
    # calls setup() and registers agents when run_async() starts, so late joiners do both here.
    rt.bureau.add(agent)
    agent.setup()

    if rt.offline:
        publish_locally(agent.address, endpoint(BUREAU_PORT))
        return agent

    # Register now instead of waiting for the Bureau's hourly batch, then join that batch so
    # the hourly refresh keeps this agent's Almanac entry alive. Bureau uses _identity too.
    one_off = BatchAlmanacApiRegistrationPolicy(logger=log)
    one_off.add_agent(agent.info, agent._identity)
    await one_off.register()
    listing.almanac_registered = one_off.last_successful_registration is not None
    rt.registration.add_agent(agent.info, agent._identity)
    return agent


@market_proto.on_message(ListItem, replies={Listed, ListingRejected})
async def handle_list_item(ctx: Context, sender: str, msg: ListItem) -> None:
    rt = runtime()
    if not msg.title.strip() or not 0 < msg.floor_cents <= msg.asking_cents:
        await ctx.send(
            sender,
            ListingRejected(
                listing_ref=msg.listing_ref, reason="need a title and 0 < floor <= ask"
            ),
        )
        return

    key = (sender, msg.listing_ref)
    address = rt.by_ref.get(key)
    if address is None:
        listing = Listing(
            item_id=uuid4().hex[:12],
            title=msg.title.strip(),
            asking_cents=msg.asking_cents,
            floor_cents=msg.floor_cents,
            seller_id=msg.seller_id,
        )
        agent = await spawn_item_agent(rt, listing)
        address = agent.address
        rt.by_ref[key] = address
        ctx.logger.info(
            f"Spawned item agent #{len(rt.listings)} for '{listing.title}': {address} "
            f"(almanac_registered={listing.almanac_registered})"
        )
    listing = rt.listings[address]
    await ctx.send(
        sender,
        Listed(
            listing_ref=msg.listing_ref,
            item_id=listing.item_id,
            title=listing.title,
            asking_cents=listing.asking_cents,
            item_address=address,
            almanac_registered=listing.almanac_registered,
        ),
    )


def _my_listing(ctx: Context, item_id: str) -> Listing | None:
    listing = runtime().listings.get(ctx.agent.address)
    return listing if listing is not None and listing.item_id == item_id else None


async def _close_deal(ctx: Context, buyer: str, listing: Listing, price_cents: int) -> None:
    listing.sold_to = buyer
    listing.open_counters.clear()
    ctx.logger.info(f"SOLD '{listing.title}' for {dollars(price_cents)} (no payment is made)")
    await ctx.send(
        buyer, Deal(item_id=listing.item_id, price_cents=price_cents, seller_id=listing.seller_id)
    )


@item_proto.on_message(Offer, replies={Counter, Deal, OfferRejected})
async def handle_offer(ctx: Context, sender: str, msg: Offer) -> None:
    listing = _my_listing(ctx, msg.item_id)
    if listing is None or listing.sold_to is not None or msg.offer_cents <= 0:
        reason = "unknown item" if listing is None else "already sold or invalid offer"
        await ctx.send(sender, OfferRejected(item_id=msg.item_id, reason=reason))
        return
    if msg.offer_cents >= listing.floor_cents:
        await _close_deal(ctx, sender, listing, msg.offer_cents)
        return
    counter = max(listing.floor_cents, (msg.offer_cents + listing.asking_cents) // 2)
    listing.open_counters[sender] = counter
    ctx.logger.info(
        f"'{listing.title}': {dollars(msg.offer_cents)} is below my floor, "
        f"countering at {dollars(counter)}"
    )
    await ctx.send(
        sender,
        Counter(item_id=listing.item_id, counter_cents=counter, note="That's too low for me."),
    )


@item_proto.on_message(AcceptCounter, replies={Deal, OfferRejected})
async def handle_accept(ctx: Context, sender: str, msg: AcceptCounter) -> None:
    listing = _my_listing(ctx, msg.item_id)
    if listing is None or listing.sold_to is not None:
        reason = "unknown item" if listing is None else "already sold"
        await ctx.send(sender, OfferRejected(item_id=msg.item_id, reason=reason))
        return
    if listing.open_counters.get(sender) != msg.counter_cents:
        await ctx.send(
            sender, OfferRejected(item_id=msg.item_id, reason="no matching counter-offer")
        )
        return
    await _close_deal(ctx, sender, listing, msg.counter_cents)


async def main(offline: bool) -> None:
    global RUNTIME
    registration: BatchRegistrationPolicy = (
        NoBatchRegistration() if offline else BatchAlmanacApiRegistrationPolicy(logger=log)
    )
    bureau = Bureau(
        port=BUREAU_PORT,
        endpoint=[endpoint(BUREAU_PORT)],
        registration_policy=registration,
        loop=asyncio.get_running_loop(),
    )
    market = new_agent("yardsale-market", "market", offline)
    market.include(market_proto)
    bureau.add(market)
    RUNTIME = Runtime(bureau=bureau, registration=registration, offline=offline)

    if offline:
        publish_locally(market.address, endpoint(BUREAU_PORT))
    write_json(MARKET_FILE, {"address": market.address, "offline": offline})
    mode = "OFFLINE (local directory, nothing registered)" if offline else "LIVE (Almanac API)"
    log.info(f"Mode: {mode}. Market agent: {market.address} on {endpoint(BUREAU_PORT)}")
    await bureau.run_async()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--offline", action="store_true", help="skip the Almanac entirely")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s [%(name)s] %(message)s")
    try:
        asyncio.run(main(args.offline))
    except KeyboardInterrupt:
        pass
