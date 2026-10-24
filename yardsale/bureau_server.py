"""Yardsale Bureau: the Market, three helper agents, and a new uAgent for every listed item.

    uv run python -m yardsale.bureau_server            # live: Almanac + Agentverse
    uv run python -m yardsale.bureau_server --offline  # no Fetch servers (still calls Claude/Apify)

The Market takes listings three ways:

- ChatMessage: a seller in the ASI:One chat attaches a photo (see chat.py).
- ListPhoto: a photo from another agent, such as the sell command.
- ListItem: a title and prices. The Market spawns an item agent right away.

For a photo, the Market asks the Identifier what it is, the Researcher for recent eBay sold
listings, and the Appraiser which of those match and what to charge. Then it spawns the item agent
with that price.

Item agents hold a private floor price and haggle with buyers. They register on the Almanac API
with the Bureau's endpoint. The Market, Identifier, Researcher, and Appraiser use Agentverse
mailboxes in live mode, so only those four use Agentverse slots.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TypeVar
from uuid import uuid4

from uagents import Agent, Bureau, Context, Model, Protocol
from uagents.registration import BatchAlmanacApiRegistrationPolicy
from uagents_core.registration import BatchRegistrationPolicy
from uagents_core.types import DeliveryStatus

from yardsale.agentverse import connect_mailbox, inspector_url, service_agent
from yardsale.appraiser import Judge, build_appraiser, judge_with_claude
from yardsale.asione import ReadMessage, read_seller_message
from yardsale.chat import FetchPhoto, Sold, build_chat_protocol
from yardsale.common import (
    BUREAU_PORT,
    MARKET_FILE,
    PipelineError,
    derive_seed,
    dollars,
    endpoint,
    write_json,
)
from yardsale.identifier import Identify, build_identifier, identify_with_claude
from yardsale.models import (
    AcceptCounter,
    Appraisal,
    Appraised,
    AppraiseRequest,
    Counter,
    Deal,
    Identified,
    IdentifyRequest,
    ItemDetails,
    Listed,
    ListingRejected,
    ListingUpdate,
    ListItem,
    ListPhoto,
    Offer,
    OfferRejected,
    ResearchRequest,
    ResearchResult,
    ServiceError,
)
from yardsale.offline import (
    LocalDirectoryResolver,
    NoBatchRegistration,
    publish_locally,
)
from yardsale.photos import download_photo
from yardsale.researcher import Research, build_researcher, search_ebay_sold

log = logging.getLogger("yardsale.bureau")

R = TypeVar("R", bound=Model)
Progress = Callable[[str, str], Awaitable[None]]  # (stage, text) -> tell the seller

# How long the Market waits for each service. Apify searches often take 20-60 seconds.
IDENTIFY_TIMEOUT = 180.0
RESEARCH_TIMEOUT = 240.0
APPRAISE_TIMEOUT = 240.0


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
class Services:
    identifier: str
    researcher: str
    appraiser: str


@dataclass
class PendingReply:
    service: str  # only this agent may answer
    future: asyncio.Future[Model]


@dataclass
class Runtime:
    bureau: Bureau
    registration: BatchRegistrationPolicy
    offline: bool
    services: Services
    listings: dict[str, Listing] = field(default_factory=dict)  # item agent address -> listing
    by_ref: dict[tuple[str, str], str] = field(default_factory=dict)  # (seller, ref) -> address
    in_progress: set[tuple[str, str]] = field(default_factory=set)  # (seller, ref) being listed
    pending: dict[str, PendingReply] = field(default_factory=dict)  # request_id -> reply slot


RUNTIME: Runtime | None = None

market_proto = Protocol(name="YardsaleMarket", version="0.2.0")
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


async def _send_listed(ctx: Context, seller: str, listing_ref: str, address: str) -> None:
    listing = runtime().listings[address]
    await ctx.send(
        seller,
        Listed(
            listing_ref=listing_ref,
            item_id=listing.item_id,
            title=listing.title,
            asking_cents=listing.asking_cents,
            item_address=address,
            almanac_registered=listing.almanac_registered,
        ),
    )


async def open_listing(ctx: Context, listing: Listing) -> str:
    """Spawn the item agent for a listing and return its address."""
    rt = runtime()
    agent = await spawn_item_agent(rt, listing)
    ctx.logger.info(
        f"Spawned item agent #{len(rt.listings)} for '{listing.title}' at "
        f"{dollars(listing.asking_cents)} (floor {dollars(listing.floor_cents)}): {agent.address} "
        f"(almanac_registered={listing.almanac_registered})"
    )
    return agent.address


async def _open_listing(ctx: Context, seller: str, listing_ref: str, listing: Listing) -> None:
    address = await open_listing(ctx, listing)
    runtime().by_ref[(seller, listing_ref)] = address
    await _send_listed(ctx, seller, listing_ref, address)


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
    if key in rt.by_ref:
        await _send_listed(ctx, sender, msg.listing_ref, rt.by_ref[key])
        return
    if key in rt.in_progress:
        return
    rt.in_progress.add(key)
    try:
        listing = Listing(
            item_id=uuid4().hex[:12],
            title=msg.title.strip(),
            asking_cents=msg.asking_cents,
            floor_cents=msg.floor_cents,
            seller_id=msg.seller_id,
        )
        await _open_listing(ctx, sender, msg.listing_ref, listing)
    finally:
        rt.in_progress.discard(key)


# --- Photo listings ------------------------------------------------------------------------------


async def call_service(
    ctx: Context,
    service: str,
    request: IdentifyRequest | ResearchRequest | AppraiseRequest,
    reply_type: type[R],
    wait_seconds: float,
) -> R:
    """Send a request to a service agent and wait for the reply with the same request_id."""
    rt = runtime()
    future: asyncio.Future[Model] = asyncio.get_running_loop().create_future()
    rt.pending[request.request_id] = PendingReply(service=service, future=future)
    try:
        status = await ctx.send(service, request)
        if status.status == DeliveryStatus.FAILED:
            raise PipelineError(f"Couldn't reach a Yardsale agent ({status.detail}).")
        try:
            reply = await asyncio.wait_for(future, wait_seconds)
        except TimeoutError:
            waited = f"{wait_seconds:.0f}s"
            raise PipelineError(f"A Yardsale agent didn't answer within {waited}.") from None
    finally:
        rt.pending.pop(request.request_id, None)
    if isinstance(reply, ServiceError):
        raise PipelineError(reply.reason)
    if not isinstance(reply, reply_type):
        raise PipelineError(f"Expected {reply_type.__name__}, got {type(reply).__name__}.")
    return reply


def _resolve(ctx: Context, sender: str, request_id: str, reply: Model) -> None:
    pending = runtime().pending.get(request_id)
    if pending is None or pending.future.done():
        ctx.logger.warning(f"Ignoring a late or unknown reply for request {request_id}")
    elif sender != pending.service:
        ctx.logger.warning(f"Ignoring a reply for request {request_id} from {sender}")
    else:
        pending.future.set_result(reply)


@market_proto.on_message(Identified)
async def on_identified(ctx: Context, sender: str, msg: Identified) -> None:
    _resolve(ctx, sender, msg.request_id, msg)


@market_proto.on_message(ResearchResult)
async def on_research(ctx: Context, sender: str, msg: ResearchResult) -> None:
    _resolve(ctx, sender, msg.request_id, msg)


@market_proto.on_message(Appraisal)
async def on_appraisal(ctx: Context, sender: str, msg: Appraisal) -> None:
    _resolve(ctx, sender, msg.request_id, msg)


@market_proto.on_message(ServiceError)
async def on_service_error(ctx: Context, sender: str, msg: ServiceError) -> None:
    _resolve(ctx, sender, msg.request_id, msg)


async def price_photo(
    ctx: Context, msg: ListPhoto, progress: Progress
) -> tuple[ItemDetails, Appraisal]:
    """Identifier -> Researcher -> Appraiser. Raises PipelineError if any of them fails."""
    rt = runtime()
    run = uuid4().hex[:12]
    await progress("identify", "Looking at the photo.")
    identified = await call_service(
        ctx,
        rt.services.identifier,
        IdentifyRequest(
            request_id=f"{run}-identify",
            image_b64=msg.image_b64,
            media_type=msg.media_type,
            note=msg.note,
        ),
        Identified,
        IDENTIFY_TIMEOUT,
    )
    item = identified.item

    await progress(
        "research",
        f"This looks like: {item.title} ({item.condition}). "
        f'Searching recent eBay sales for "{item.search_query}".',
    )
    research = await call_service(
        ctx,
        rt.services.researcher,
        ResearchRequest(
            request_id=f"{run}-research",
            query=item.search_query,
            condition="new" if item.condition == "new" else "used",
        ),
        ResearchResult,
        RESEARCH_TIMEOUT,
    )

    await progress("appraise", f"Found {len(research.comps)} sales. Comparing them with the photo.")
    appraisal = await call_service(
        ctx,
        rt.services.appraiser,
        AppraiseRequest(
            request_id=f"{run}-appraise",
            item=item,
            image_b64=msg.image_b64,
            media_type=msg.media_type,
            comps=research.comps,
        ),
        Appraisal,
        APPRAISE_TIMEOUT,
    )
    return item, appraisal


def listing_for(msg: ListPhoto, item: ItemDetails, appraisal: Appraisal) -> Listing:
    """Prices the seller gave win; otherwise the appraisal's. No price at all is an error."""
    asking = msg.asking_cents or appraisal.asking_cents
    if asking is None:
        raise PipelineError(
            f"This looks like {item.title}, but no recent eBay sales were close enough to price "
            "it. Send it again with your own asking price."
        )
    floor = msg.floor_cents or appraisal.floor_cents or asking * 7 // 10
    return Listing(
        item_id=uuid4().hex[:12],
        title=item.title[:80],
        asking_cents=max(asking, floor),
        floor_cents=floor,
        seller_id=msg.seller_id,
    )


async def _list_from_photo(ctx: Context, seller: str, msg: ListPhoto) -> None:
    async def update(stage: str, text: str) -> None:
        await ctx.send(seller, ListingUpdate(listing_ref=msg.listing_ref, stage=stage, text=text))

    item, appraisal = await price_photo(ctx, msg, update)
    await ctx.send(seller, Appraised(listing_ref=msg.listing_ref, item=item, appraisal=appraisal))
    await _open_listing(ctx, seller, msg.listing_ref, listing_for(msg, item, appraisal))


async def sell_from_chat(ctx: Context, msg: ListPhoto) -> Sold:
    """The same pipeline for a photo sent in the ASI:One chat (see chat.py)."""

    async def log_progress(stage: str, text: str) -> None:
        ctx.logger.info(f"Chat listing {msg.listing_ref[:8]}: {text}")

    item, appraisal = await price_photo(ctx, msg, log_progress)
    listing = listing_for(msg, item, appraisal)
    address = await open_listing(ctx, listing)
    return Sold(
        item=item,
        appraisal=appraisal,
        address=address,
        asking_cents=listing.asking_cents,
        floor_cents=listing.floor_cents,
    )


@market_proto.on_message(ListPhoto, replies={ListingUpdate, Appraised, Listed, ListingRejected})
async def handle_list_photo(ctx: Context, sender: str, msg: ListPhoto) -> None:
    rt = runtime()
    key = (sender, msg.listing_ref)
    if key in rt.by_ref:
        await _send_listed(ctx, sender, msg.listing_ref, rt.by_ref[key])
        return
    if key in rt.in_progress:
        return  # still being priced; that run will answer
    if any(p is not None and p <= 0 for p in (msg.asking_cents, msg.floor_cents)):
        await ctx.send(
            sender, ListingRejected(listing_ref=msg.listing_ref, reason="prices must be positive")
        )
        return
    rt.in_progress.add(key)
    try:
        await _list_from_photo(ctx, sender, msg)
    except PipelineError as ex:
        ctx.logger.warning(f"Photo listing {msg.listing_ref} failed: {ex}")
        await ctx.send(sender, ListingRejected(listing_ref=msg.listing_ref, reason=str(ex)))
    finally:
        rt.in_progress.discard(key)


# --- Haggling ------------------------------------------------------------------------------------


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


# --- Startup -------------------------------------------------------------------------------------


MARKET_DESCRIPTION = (  # Agentverse shows up to 300 characters
    "Sell anything from a photo. Attach a picture of an item and Yardsale identifies it, prices it "
    "from recent eBay sold listings, and starts an agent that haggles with buyers for you. Add "
    'your own price if you like, such as "at least $40".'
)


def build(
    offline: bool,
    identify: Identify,
    research: Research,
    judge: Judge,
    read: ReadMessage = read_seller_message,
    fetch: FetchPhoto = download_photo,
) -> tuple[Bureau, Agent, list[Agent]]:
    """Create the Bureau, the Market, and the three service agents, inside a running loop."""
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
    # On Agentverse in live mode, so ASI:One can reach it. Handles messages concurrently, so a
    # photo listing waiting on a service doesn't block other messages.
    market = service_agent("market", "Yardsale", MARKET_DESCRIPTION, offline)
    market.include(market_proto)
    market.include(build_chat_protocol(sell_from_chat, read, fetch), publish_manifest=not offline)
    services = [
        build_identifier(identify, offline),
        build_researcher(research, offline),
        build_appraiser(judge, offline),
    ]
    for agent in (market, *services):
        bureau.add(agent)
        if offline:
            publish_locally(agent.address, endpoint(BUREAU_PORT))
    RUNTIME = Runtime(
        bureau=bureau,
        registration=registration,
        offline=offline,
        services=Services(*(agent.address for agent in services)),
    )
    return bureau, market, services


async def connect_to_agentverse(agents: list[Agent]) -> None:
    api_key = os.getenv("AGENTVERSE_API_KEY", "").strip()
    for agent in agents:
        if api_key:
            ok, detail = await connect_mailbox(agent, api_key)
            if ok:
                log.info(f"{agent.name}: connected to Agentverse ({agent.address})")
                continue
            log.warning(f"{agent.name}: Agentverse connect failed: {detail}")
        log.info(
            f"{agent.name}: to connect it to Agentverse, open this link while signed in, click "
            f"Connect, and choose Mailbox: {inspector_url(agent, BUREAU_PORT)}"
        )


async def main(offline: bool) -> None:
    for name in ("ANTHROPIC_API_KEY", "APIFY_TOKEN"):
        if not os.getenv(name, "").strip():
            log.warning(f"{name} is not set (see .env.example), so photo listings will fail.")
    if not os.getenv("ASI_ONE_API_KEY", "").strip():
        log.warning(
            "ASI_ONE_API_KEY is not set, so chat messages are read without ASI:One: photos still "
            "list at the appraised price, but typed prices and notes are ignored."
        )
    bureau, market, services = build(
        offline, identify_with_claude, search_ebay_sold, judge_with_claude
    )
    if not offline:
        await connect_to_agentverse([market, *services])

    write_json(MARKET_FILE, {"address": market.address, "offline": offline})
    if offline:
        log.info(f"Mode: OFFLINE (local directory, nothing registered). Market: {market.address}")
    else:
        log.info(
            f"Mode: LIVE (Almanac API). Market: {market.address}, reached through its Agentverse "
            "mailbox. Sellers chat with it in ASI:One or from its Agentverse profile."
        )
    for agent in services:
        log.info(f"{agent.name}: {agent.address}")
    await bureau.run_async()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--offline", action="store_true", help="skip the Almanac and Agentverse")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s [%(name)s] %(message)s")
    try:
        asyncio.run(main(args.offline))
    except KeyboardInterrupt:
        pass
