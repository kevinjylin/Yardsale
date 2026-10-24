"""Yardsale Bureau: the Market, its helper agents, and a new uAgent for every listed item.

    uv run python -m yardsale.bureau_server                    # live: Almanac + Agentverse
    uv run python -m yardsale.bureau_server --offline          # no Fetch servers; real Claude/Apify
    uv run python -m yardsale.bureau_server --check            # what live mode still needs

The Market takes listings three ways:

- ChatMessage: a seller in the ASI:One chat attaches a photo (see chat.py). Buyers search and
  haggle in the same chat.
- ListPhoto: a photo from another agent, such as the sell command. The Market answers with
  Proposed. ApproveListing makes it live; CancelListing drops it.
- ListItem: a title and the seller's own prices. Nothing is suggested, so it goes live at once.

For a photo, the Market asks the Identifier what it is, the Researcher for recent eBay sold
listings, and the Appraiser which of those match and what to charge. It saves the proposal
(store.py), and spawns the item agent only once the seller approves.

Item agents hold a private floor price and haggle with buyers over the published YardsaleHaggle
protocol (haggle.py, negotiation.py). Most run in this Bureau and register on the Almanac API
with its endpoint. With hosting on (hosting.py), approved items get an agent hosted on
Agentverse instead, up to YARDSALE_HOST_ITEMS of them, each with a handle made from its title
and, with YARDSALE_ITEM_AVATARS=1, a picture ASI:One draws (avatars.py). The Market, the buyer
agent (shopper.py), the Identifier, Researcher, and Appraiser use Agentverse mailboxes in live
mode.

When a buyer accepts, the deal is recorded and the Market's checkout (checkout.py) sends a payment
link through the Agent Payment Protocol, or in the chat for chat buyers.

Listings, proposals, counter-offers, deals, and payments are saved in SQLite. On start the
Bureau re-creates the agent of every active listing it runs. Seeds come from the master seed and
the item ID, so the addresses are the same as before. Hosted agents keep running on Agentverse
until their listing is sold or canceled. Then the Market deletes them, and tries again every
minute (and on start) if Agentverse can't be reached.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import time
from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import dataclass, field
from typing import Any, TypeVar
from uuid import uuid4

from aiohttp import web
from uagents import Agent, Bureau, Context, Model, Protocol
from uagents.registration import BatchAlmanacApiRegistrationPolicy
from uagents_core.registration import BatchRegistrationPolicy
from uagents_core.types import DeliveryStatus

from yardsale import business_api, config
from yardsale.agentverse import connect_mailbox, inspector_url, service_agent
from yardsale.appraiser import Judge, build_appraiser, judge_with_claude
from yardsale.asione import ReadMessage, read_seller_message
from yardsale.avatars import ItemPictures, item_pictures_from_env, picture_item_quietly
from yardsale.chat import FetchPhoto, build_chat_protocol, fetch_photo
from yardsale.checkout import CHECK_EVERY, Checkout, build_payment_protocol
from yardsale.common import (
    BUREAU_PORT,
    MARKET_FILE,
    PipelineError,
    UnpricedError,
    derive_seed,
    dollars,
    write_json,
)
from yardsale.hosting import HostingClient, HostingError, claim_handle, host_item, hosted_limit
from yardsale.identifier import Identify, build_identifier, identify_with_claude
from yardsale.models import (
    HAGGLE_SPEC,
    AcceptCounter,
    Appraisal,
    Appraised,
    AppraiseRequest,
    ApproveListing,
    CancelListing,
    Counter,
    Deal,
    HaggleRequest,
    HaggleResult,
    Identified,
    IdentifyRequest,
    ItemDetails,
    ItemSold,
    Listed,
    ListingRejected,
    ListingUpdate,
    ListItem,
    ListPhoto,
    Offer,
    OfferRejected,
    Proposed,
    ResearchRequest,
    ResearchResult,
    ServiceError,
)
from yardsale.negotiation import answer_accept, answer_offer, buyer_key, gap_cents
from yardsale.offline import (
    LocalDirectoryResolver,
    NoBatchRegistration,
    publish_locally,
    stay_offline,
)
from yardsale.payments import PaymentConfigError, PaymentProvider, provider_from_env
from yardsale.photos import PhotoError, check_prepared_photo
from yardsale.pricing import NoSuggestedPrice, PriceError, listing_prices, valid_cents
from yardsale.researcher import Research, build_researcher, search_ebay_sold
from yardsale.shopper import Shopper, build_shopper, haggle_timeout
from yardsale.store import ACTIVE, AGENTVERSE, BUREAU, PROPOSED, DealRecord, Listing, Store

log = logging.getLogger("yardsale.bureau")

R = TypeVar("R", bound=Model)
Progress = Callable[[str, str], Awaitable[None]]  # (stage, text) -> tell the seller

# How long the Market waits for each service. Apify searches often take 20-60 seconds.
IDENTIFY_TIMEOUT = 180.0
RESEARCH_TIMEOUT = 240.0
APPRAISE_TIMEOUT = 240.0
NEGOTIATE_TIMEOUT = 15.0  # item agents in this Bureau answer in milliseconds
HOSTED_NEGOTIATE_TIMEOUT = 60.0  # hosted ones answer through Agentverse mailboxes
RETRY_DELETES_EVERY = 60.0  # seconds between tries to delete closed listings' hosted agents


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
    endpoint: str
    services: Services
    store: Store
    checkout: Checkout
    market: str = ""
    shopper: str = ""  # the buyer agent
    # Agentverse's Hosting API, with an API key in live mode: hosts up to host_limit item agents
    # and deletes the agents of sold or canceled listings.
    hosting: HostingClient | None = None
    host_limit: int = 0  # how many item agents may be on Agentverse at once (0 = host none)
    pictures: ItemPictures | None = None  # draws and publishes hosted item agents' pictures
    background: set[asyncio.Task[None]] = field(default_factory=set)  # kept until they finish
    deleting: asyncio.Lock = field(default_factory=asyncio.Lock)  # one delete sweep at a time
    round_timeout: float = 30.0  # the buyer agent's wait for one item agent's answer
    boot_id: str = field(default_factory=lambda: uuid4().hex)
    started: bool = False  # the Bureau is running, so new agents start themselves
    agents: dict[str, Agent] = field(default_factory=dict)  # item_id -> its agent
    items: dict[str, str] = field(default_factory=dict)  # item agent address -> item_id
    registered: dict[str, bool] = field(default_factory=dict)  # item_id -> Almanac confirmed
    in_progress: set[tuple[str, str]] = field(default_factory=set)  # (seller, ref) being priced
    pending: dict[str, PendingReply] = field(default_factory=dict)  # request_id -> reply slot


RUNTIME: Runtime | None = None

market_proto = Protocol(name="YardsaleMarket", version="0.4.0")
item_proto = Protocol(spec=HAGGLE_SPEC, role="item")  # every item agent in this Bureau
market_buyer = Protocol(spec=HAGGLE_SPEC, role="buyer")  # the Market, haggling for chat buyers


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


# --- Item agents ---------------------------------------------------------------------------------


def _add_item_agent(rt: Runtime, item_id: str) -> tuple[Agent, bool]:
    """The item's agent, created and added to the Bureau if it isn't already. (agent, is_new)"""
    existing = rt.agents.get(item_id)
    if existing is not None:
        return existing, False
    agent = new_agent(f"yardsale-item-{item_id}", f"item:{item_id}", rt.offline)
    agent.include(item_proto)
    rt.agents[item_id] = agent
    rt.items[agent.address] = item_id
    # add() points the agent at the Bureau's loop, endpoint, and port, and the global
    # dispatcher already routes /submit envelopes by address.
    rt.bureau.add(agent)
    if rt.offline:
        publish_locally(agent.address, rt.endpoint)
    return agent, True


async def start_item_agent(rt: Runtime, item_id: str) -> tuple[Agent, bool]:
    """Start the item's agent in the running Bureau. (agent, registered on the Almanac)

    The Bureau only calls setup() and registers agents when run_async() starts, so an agent
    added later does both here. Calling this again for the same item returns the same agent.
    """
    agent, is_new = _add_item_agent(rt, item_id)
    if not is_new:
        return agent, rt.registered.get(item_id, False)
    if not rt.started:
        return agent, False  # the Bureau starts and registers it when it boots
    agent.setup()
    if rt.offline:
        return agent, False
    # Register now instead of waiting for the Bureau's hourly batch, then join that batch so
    # the hourly refresh keeps this agent's Almanac entry alive. Bureau uses _identity too.
    one_off = BatchAlmanacApiRegistrationPolicy(logger=log)
    one_off.add_agent(agent.info, agent._identity)
    try:
        await one_off.register()
    except Exception:
        log.exception(f"Almanac registration for item {item_id} failed; the hourly batch retries")
    rt.registration.add_agent(agent.info, agent._identity)
    rt.registered[item_id] = one_off.last_successful_registration is not None
    return agent, rt.registered[item_id]


def _hosted_count(rt: Runtime) -> int:
    """Item agents on Agentverse, counting closed listings' agents that aren't deleted yet."""
    return len(rt.store.on_agentverse())


def _in_background(rt: Runtime, work: Coroutine[Any, Any, None]) -> None:
    """Run `work` without making anyone wait for it."""
    task = asyncio.create_task(work)
    rt.background.add(task)
    task.add_done_callback(rt.background.discard)


async def delete_closed_hosted(rt: Runtime) -> None:
    """Delete the Agentverse agents of hosted listings that were sold or canceled. A delete that
    fails is tried again on the next sweep: right after the next sale or cancel, every
    RETRY_DELETES_EVERY seconds, and when the server starts."""
    if rt.hosting is None:
        return
    async with rt.deleting:
        for listing in rt.store.on_agentverse():
            if listing.status == ACTIVE or listing.address is None:
                continue
            try:
                await rt.hosting.delete(listing.address)
            except HostingError as ex:
                log.warning(f"Couldn't delete the hosted agent of '{listing.title}' yet: {ex}")
                if ex.status is None:
                    break  # Agentverse can't be reached, so the others wait too
                continue
            rt.store.agent_deleted(listing.item_id)
            log.info(
                f"Deleted the hosted agent of '{listing.title}' ({listing.status}): "
                f"{listing.address}"
            )


def delete_closed_hosted_soon(rt: Runtime) -> None:
    if rt.hosting is not None:
        _in_background(rt, delete_closed_hosted(rt))


def cancel_listing(rt: Runtime, item_id: str, owner: str) -> Listing | None:
    """The owner takes a proposal or listing down. A hosted agent is deleted from Agentverse."""
    canceled = rt.store.cancel(item_id, owner)
    if canceled is not None and canceled.host == AGENTVERSE:
        delete_closed_hosted_soon(rt)
    return canceled


async def _host_on_agentverse(ctx: Context, rt: Runtime, listing: Listing) -> Listing | None:
    """Host the item agent on Agentverse and mark the listing active. None when hosting is off,
    full, or failed; the caller then runs the agent in this Bureau."""
    if rt.hosting is None or _hosted_count(rt) >= rt.host_limit:
        return None
    try:
        address = await host_item(rt.hosting, listing, gap_cents(listing), rt.market, rt.shopper)
    except HostingError as ex:
        ctx.logger.warning(f"Hosting {listing.item_id} on Agentverse failed, so it runs here: {ex}")
        return None
    handle = await claim_handle(rt.hosting, address, listing.title)
    live = rt.store.activate(
        listing.item_id, listing.owner, address, host=AGENTVERSE, handle=handle
    )
    if live is None:
        try:
            await rt.hosting.delete(address)
        except HostingError:
            ctx.logger.warning(f"Couldn't delete the unused hosted agent {address}")
        raise PipelineError("That listing was canceled or sold, so it can't go live.")
    ctx.logger.info(
        f"Item agent for '{live.title}' at {dollars(live.asking_cents)} is hosted on "
        f"Agentverse: {address}" + (f" (@{handle})" if handle else "")
    )
    if rt.pictures is not None:
        # Drawing takes longer than the seller should wait, so the reply doesn't wait for it.
        _in_background(rt, picture_item_quietly(rt.hosting, live, rt.pictures))
    return live


async def activate(ctx: Context, listing: Listing) -> tuple[Listing, bool]:
    """Approved: start the item agent, then mark the listing active. Safe to repeat.

    The agent starts first and turns down offers until the listing is active, so a crash in
    between leaves a proposal to approve again, never a live listing without an agent. With
    hosting on, the agent is created on Agentverse; otherwise, or if that fails, here.
    """
    rt = runtime()
    if listing.status == PROPOSED and listing.item_id not in rt.agents:
        hosted = await _host_on_agentverse(ctx, rt, listing)
        if hosted is not None:
            return hosted, True
    elif listing.status == ACTIVE and listing.host == AGENTVERSE:
        return listing, True
    try:
        agent, registered = await start_item_agent(rt, listing.item_id)
    except Exception as ex:
        ctx.logger.exception(f"Item agent for {listing.item_id} didn't start")
        raise PipelineError("The item agent didn't start. Try approving again.") from ex
    live = rt.store.activate(listing.item_id, listing.owner, agent.address)
    if live is None:
        raise PipelineError("That listing was canceled or sold, so it can't go live.")
    ctx.logger.info(
        f"Item agent #{len(rt.agents)} for '{live.title}' at {dollars(live.asking_cents)} is "
        f"live: {agent.address} (almanac_registered={registered})"
    )
    return live, registered


def _listed(listing: Listing, registered: bool) -> Listed:
    return Listed(
        listing_ref=listing.listing_ref,
        item_id=listing.item_id,
        title=listing.title,
        asking_cents=listing.asking_cents,
        item_address=listing.address or "",
        almanac_registered=registered,
    )


def _proposed(listing: Listing) -> Proposed:
    return Proposed(
        listing_ref=listing.listing_ref,
        item_id=listing.item_id,
        title=listing.title,
        asking_cents=listing.asking_cents,
        floor_cents=listing.floor_cents,
        asking_from=listing.asking_from,
        floor_from=listing.floor_from,
    )


async def _send_state(ctx: Context, seller: str, listing: Listing) -> None:
    """Answer a repeated request with where the listing stands now."""
    if listing.status == PROPOSED:
        await ctx.send(seller, _proposed(listing))
    elif listing.status == ACTIVE:
        registered = listing.host == AGENTVERSE or runtime().registered.get(listing.item_id, False)
        await ctx.send(seller, _listed(listing, registered))
    else:
        await ctx.send(
            seller,
            ListingRejected(
                listing_ref=listing.listing_ref, reason=f"This listing is {listing.status}."
            ),
        )


@market_proto.on_message(ListItem, replies={Listed, ListingRejected})
async def handle_list_item(ctx: Context, sender: str, msg: ListItem) -> None:
    rt = runtime()
    if (
        not msg.title.strip()
        or not valid_cents(msg.asking_cents)
        or not 0 < msg.floor_cents <= msg.asking_cents
    ):
        await ctx.send(
            sender,
            ListingRejected(
                listing_ref=msg.listing_ref,
                reason="need a title and 0 < floor <= ask <= $1,000,000",
            ),
        )
        return
    key = (sender, msg.listing_ref)
    if key in rt.in_progress:
        return
    rt.in_progress.add(key)
    try:
        listing = rt.store.find_ref(sender, msg.listing_ref) or rt.store.add_listing(
            Listing(
                item_id=uuid4().hex[:12],
                owner=sender,
                seller_id=msg.seller_id,
                listing_ref=msg.listing_ref,
                status=PROPOSED,
                title=msg.title.strip()[:80],
                asking_cents=msg.asking_cents,
                floor_cents=msg.floor_cents,
                seller_asking_cents=msg.asking_cents,
                seller_floor_cents=msg.floor_cents,
            )
        )
        if listing.status != PROPOSED:
            await _send_state(ctx, sender, listing)
            return
        # The seller gave every field, so there's nothing to approve.
        live, registered = await activate(ctx, listing)
        await ctx.send(sender, _listed(live, registered))
    except PipelineError as ex:
        await ctx.send(sender, ListingRejected(listing_ref=msg.listing_ref, reason=str(ex)))
    finally:
        rt.in_progress.discard(key)


# --- Talking to other agents ---------------------------------------------------------------------


async def request_reply(
    ctx: Context, address: str, request: Model, request_id: str, wait_seconds: float
) -> Model:
    """Send a request and wait for the reply with the same request_id from that address."""
    rt = runtime()
    future: asyncio.Future[Model] = asyncio.get_running_loop().create_future()
    rt.pending[request_id] = PendingReply(service=address, future=future)
    try:
        status = await ctx.send(address, request)
        if status.status == DeliveryStatus.FAILED:
            raise PipelineError(f"Couldn't reach a Yardsale agent ({status.detail}).")
        try:
            return await asyncio.wait_for(future, wait_seconds)
        except TimeoutError:
            waited = f"{wait_seconds:.0f}s"
            raise PipelineError(f"A Yardsale agent didn't answer within {waited}.") from None
    finally:
        rt.pending.pop(request_id, None)


async def call_service(
    ctx: Context,
    service: str,
    request: IdentifyRequest | ResearchRequest | AppraiseRequest,
    reply_type: type[R],
    wait_seconds: float,
) -> R:
    reply = await request_reply(ctx, service, request, request.request_id, wait_seconds)
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


# Item agents answering offers the Market made for chat buyers.
@market_buyer.on_message(Counter)
async def on_counter(ctx: Context, sender: str, msg: Counter) -> None:
    _resolve(ctx, sender, msg.request_id, msg)


@market_buyer.on_message(Deal)
async def on_deal(ctx: Context, sender: str, msg: Deal) -> None:
    _resolve(ctx, sender, msg.request_id, msg)


@market_buyer.on_message(OfferRejected)
async def on_offer_rejected(ctx: Context, sender: str, msg: OfferRejected) -> None:
    _resolve(ctx, sender, msg.request_id, msg)


@market_proto.on_message(HaggleResult)
async def on_haggle_result(ctx: Context, sender: str, msg: HaggleResult) -> None:
    _resolve(ctx, sender, msg.request_id, msg)


def _item_address(rt: Runtime, listing: Listing) -> str | None:
    if listing.host == AGENTVERSE:
        return listing.address
    agent = rt.agents.get(listing.item_id)
    return agent.address if agent is not None else None


def record_hosted_deal(rt: Runtime, listing: Listing, buyer: str, deal: Deal) -> DealRecord | None:
    """A hosted item agent sold its item: record the deal here, once, and delete the agent."""
    record = rt.store.close_deal(listing.item_id, buyer, deal.price_cents, deal.deal_id)
    if record is not None:
        delete_closed_hosted_soon(rt)
    return record


async def negotiate(
    ctx: Context, item_id: str, request: Offer | AcceptCounter
) -> Counter | Deal | OfferRejected:
    """Pass a chat buyer's offer or acceptance to the item agent and return its answer."""
    rt = runtime()
    listing = rt.store.get(item_id)
    address = _item_address(rt, listing) if listing and listing.status == ACTIVE else None
    if listing is None or address is None:
        return OfferRejected(item_id=item_id, reason="not for sale", code="unavailable")
    request = request.copy(update={"request_id": uuid4().hex})
    wait = HOSTED_NEGOTIATE_TIMEOUT if listing.host == AGENTVERSE else NEGOTIATE_TIMEOUT
    reply = await request_reply(ctx, address, request, request.request_id, wait)
    if not isinstance(reply, Counter | Deal | OfferRejected):
        raise PipelineError(f"The item agent sent {type(reply).__name__}.")
    if isinstance(reply, Deal) and listing.host == AGENTVERSE:
        buyer = buyer_key(rt.market, request.buyer_ref, rt.market)
        if record_hosted_deal(rt, listing, buyer, reply) is None:
            ctx.logger.warning(f"Hosted item {item_id} sold, but the store had it as unavailable")
    return reply


@market_proto.on_message(ItemSold)
async def handle_item_sold(ctx: Context, sender: str, msg: ItemSold) -> None:
    """A hosted item agent reports a sale: record it and start checkout."""
    rt = runtime()
    listing = rt.store.get(msg.item_id)
    if listing is None or listing.host != AGENTVERSE or listing.address != sender:
        ctx.logger.warning(f"Ignoring a sale report for {msg.item_id} from {sender[:16]}")
        return
    if not valid_cents(msg.price_cents) or not msg.buyer.startswith(("agent:", "chat:")):
        ctx.logger.warning(f"Ignoring a malformed sale report for {msg.item_id}")
        return
    deal = Deal(item_id=msg.item_id, price_cents=msg.price_cents, deal_id=msg.deal_id)
    record = record_hosted_deal(rt, listing, msg.buyer, deal)
    if record is None:
        ctx.logger.warning(f"Hosted item {msg.item_id} reported a sale it can't record")
        return
    ctx.logger.info(f"Hosted item '{listing.title}' sold for {dollars(record.price_cents)}")
    await rt.checkout.start(record.deal_id)


async def haggle_for(
    ctx: Context, buyer_ref: str, max_cents: int, listings: list[Listing]
) -> HaggleResult:
    """Ask the buyer agent to haggle with these items at once for a chat buyer."""
    rt = runtime()
    request = HaggleRequest(
        request_id=uuid4().hex,
        buyer_ref=buyer_ref,
        max_cents=max_cents,
        items=[
            {
                "item_id": listing.item_id,
                "address": address,
                "title": listing.title,
                "asking_cents": listing.asking_cents,
            }
            for listing in listings
            if (address := _item_address(rt, listing)) is not None
        ],
    )
    reply = await request_reply(
        ctx, rt.shopper, request, request.request_id, haggle_timeout(rt.round_timeout)
    )
    if not isinstance(reply, HaggleResult):
        raise PipelineError(f"The buyer agent sent {type(reply).__name__}.")
    return reply


# --- Photo listings ------------------------------------------------------------------------------


async def price_photo(
    ctx: Context, msg: ListPhoto, progress: Progress
) -> tuple[ItemDetails, Appraisal]:
    """Identifier -> Researcher -> Appraiser. Raises PipelineError if any of them fails."""
    rt = runtime()
    run = uuid4().hex[:12]
    started = time.monotonic()
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
    identified_at = time.monotonic()

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
    researched_at = time.monotonic()

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
    done = time.monotonic()
    ctx.logger.info(
        f"Priced photo {run} in {done - started:.1f}s: identify {identified_at - started:.1f}s, "
        f"eBay research {researched_at - identified_at:.1f}s, appraise {done - researched_at:.1f}s"
    )
    return item, appraisal


def save_proposal(
    owner: str,
    seller_id: str,
    listing_ref: str,
    item: ItemDetails,
    appraisal: Appraisal,
    asking_cents: int | None,
    floor_cents: int | None,
) -> Listing:
    """Save a listing waiting for approval. Prices the seller gave win over the appraisal's.

    Raises UnpricedError when neither has a price, and PriceError for prices that can't work.
    """
    try:
        asking, floor = listing_prices(
            appraisal.asking_cents, appraisal.floor_cents, asking_cents, floor_cents
        )
    except NoSuggestedPrice:
        raise UnpricedError(
            f"This looks like {item.title}, but no recent eBay sales were close enough to price "
            "it. Send it again with your own asking price.",
            item,
            appraisal,
        ) from None
    return runtime().store.add_listing(
        Listing(
            item_id=uuid4().hex[:12],
            owner=owner,
            seller_id=seller_id,
            listing_ref=listing_ref,
            status=PROPOSED,
            title=item.title[:80],
            asking_cents=asking,
            floor_cents=floor,
            seller_asking_cents=asking_cents,
            seller_floor_cents=floor_cents,
            suggested_asking_cents=appraisal.asking_cents,
            suggested_floor_cents=appraisal.floor_cents,
            item=item,
            appraisal=appraisal,
        )
    )


def _photo_problem(msg: ListPhoto) -> str:
    for price in (msg.asking_cents, msg.floor_cents):
        if price is not None and not valid_cents(price):
            return "prices must be between $0.01 and $1,000,000"
    try:
        check_prepared_photo(msg.image_b64, msg.media_type)
    except PhotoError as ex:
        return str(ex)
    return ""


@market_proto.on_message(
    ListPhoto, replies={ListingUpdate, Appraised, Proposed, Listed, ListingRejected}
)
async def handle_list_photo(ctx: Context, sender: str, msg: ListPhoto) -> None:
    rt = runtime()
    existing = rt.store.find_ref(sender, msg.listing_ref)
    if existing is not None:
        await _send_state(ctx, sender, existing)
        return
    key = (sender, msg.listing_ref)
    if key in rt.in_progress:
        return  # still being priced; that run will answer
    problem = _photo_problem(msg)
    if problem:
        await ctx.send(sender, ListingRejected(listing_ref=msg.listing_ref, reason=problem))
        return

    async def update(stage: str, text: str) -> None:
        await ctx.send(sender, ListingUpdate(listing_ref=msg.listing_ref, stage=stage, text=text))

    rt.in_progress.add(key)
    try:
        item, appraisal = await price_photo(ctx, msg, update)
        await ctx.send(
            sender, Appraised(listing_ref=msg.listing_ref, item=item, appraisal=appraisal)
        )
        # The seller is this agent address, whatever seller_id says, so only it can approve.
        listing = save_proposal(
            sender,
            msg.seller_id,
            msg.listing_ref,
            item,
            appraisal,
            msg.asking_cents,
            msg.floor_cents,
        )
        await ctx.send(sender, _proposed(listing))
    except (PipelineError, PriceError) as ex:
        # A PriceError's message can include the seller's floor, which stays out of logs.
        detail = "prices didn't work" if isinstance(ex, PriceError) else str(ex)
        ctx.logger.warning(f"Photo listing {msg.listing_ref} failed: {detail}")
        await ctx.send(sender, ListingRejected(listing_ref=msg.listing_ref, reason=str(ex)))
    finally:
        rt.in_progress.discard(key)


@market_proto.on_message(ApproveListing, replies={Listed, ListingRejected})
async def handle_approve(ctx: Context, sender: str, msg: ApproveListing) -> None:
    rt = runtime()
    listing = rt.store.find_ref(sender, msg.listing_ref)
    if listing is None:
        await ctx.send(
            sender,
            ListingRejected(
                listing_ref=msg.listing_ref, reason="You have no listing with that ref."
            ),
        )
        return
    try:
        if msg.asking_cents is not None or msg.floor_cents is not None:
            listing = (
                rt.store.reprice(listing.item_id, sender, msg.asking_cents, msg.floor_cents)
                or listing
            )
        live, registered = await activate(ctx, listing)
    except (PipelineError, PriceError) as ex:
        await ctx.send(sender, ListingRejected(listing_ref=msg.listing_ref, reason=str(ex)))
        return
    await ctx.send(sender, _listed(live, registered))


@market_proto.on_message(CancelListing, replies={ListingRejected})
async def handle_cancel(ctx: Context, sender: str, msg: CancelListing) -> None:
    rt = runtime()
    listing = rt.store.find_ref(sender, msg.listing_ref)
    canceled = listing and cancel_listing(rt, listing.item_id, sender)
    reason = "Canceled by the seller." if canceled else "Nothing to cancel."
    await ctx.send(sender, ListingRejected(listing_ref=msg.listing_ref, reason=reason))


class MarketBackend:
    """The Market's side of the ASI:One chat (chat.Backend)."""

    def __init__(self, rt: Runtime) -> None:
        self.rt = rt
        self.store = rt.store
        self.offline = rt.offline
        self.boot_id = rt.boot_id

    async def propose(self, ctx: Context, owner: str, photo: ListPhoto) -> Listing:
        async def log_progress(stage: str, text: str) -> None:
            ctx.logger.info(f"Chat listing {photo.listing_ref[:8]}: {stage}")

        item, appraisal = await price_photo(ctx, photo, log_progress)
        return self.save_proposal(
            owner,
            photo.seller_id,
            photo.listing_ref,
            item,
            appraisal,
            photo.asking_cents,
            photo.floor_cents,
        )

    def save_proposal(
        self,
        owner: str,
        seller_id: str,
        listing_ref: str,
        item: ItemDetails,
        appraisal: Appraisal,
        asking_cents: int | None,
        floor_cents: int | None,
    ) -> Listing:
        return save_proposal(
            owner, seller_id, listing_ref, item, appraisal, asking_cents, floor_cents
        )

    async def activate(self, ctx: Context, listing: Listing) -> tuple[Listing, bool]:
        return await activate(ctx, listing)

    def cancel(self, item_id: str, owner: str) -> Listing | None:
        return cancel_listing(self.rt, item_id, owner)

    async def negotiate(
        self, ctx: Context, item_id: str, request: Offer | AcceptCounter
    ) -> Counter | Deal | OfferRejected:
        return await negotiate(ctx, item_id, request)

    async def haggle(
        self, ctx: Context, buyer_ref: str, max_cents: int, listings: list[Listing]
    ) -> HaggleResult:
        return await haggle_for(ctx, buyer_ref, max_cents, listings)

    def available(self) -> list[Listing]:
        """Active listings whose agent is running, here or on Agentverse."""
        return [
            listing
            for listing in self.store.active()
            if listing.host == AGENTVERSE or listing.item_id in self.rt.agents
        ]

    @property
    def checkout(self) -> Checkout:
        return self.rt.checkout


# --- Haggling ------------------------------------------------------------------------------------


def _log_answer(ctx: Context, reply: Counter | Deal | OfferRejected) -> None:
    # Floors stay out of the logs; a counter is already something the buyer sees.
    if isinstance(reply, Deal):
        ctx.logger.info(f"SOLD '{reply.title}' for {dollars(reply.price_cents)}")
    elif isinstance(reply, Counter) and reply.agreed:
        ctx.logger.info(f"Agreed to an offer of {dollars(reply.counter_cents)}")
    elif isinstance(reply, Counter):
        ctx.logger.info(f"Countered an offer at {dollars(reply.counter_cents)}")
    else:
        ctx.logger.info(f"Turned down a request: {reply.code or reply.reason}")


def _speaks_for_chats(rt: Runtime) -> set[str]:
    return {address for address in (rt.market, rt.shopper) if address}


@item_proto.on_message(Offer)
async def handle_offer(ctx: Context, sender: str, msg: Offer) -> None:
    rt = runtime()
    item_id = rt.items.get(ctx.agent.address)
    reply: Counter | OfferRejected
    if item_id != msg.item_id:
        reply = OfferRejected(
            item_id=msg.item_id,
            reason="unknown item",
            code="unknown_item",
            request_id=msg.request_id,
        )
    else:
        buyer = buyer_key(sender, msg.buyer_ref, _speaks_for_chats(rt))
        reply = answer_offer(rt.store, item_id, buyer, msg.offer_cents, msg.request_id)
    _log_answer(ctx, reply)
    await ctx.send(sender, reply)


@item_proto.on_message(AcceptCounter)
async def handle_accept(ctx: Context, sender: str, msg: AcceptCounter) -> None:
    rt = runtime()
    item_id = rt.items.get(ctx.agent.address)
    reply: Deal | OfferRejected
    if item_id != msg.item_id:
        reply = OfferRejected(
            item_id=msg.item_id,
            reason="unknown item",
            code="unknown_item",
            request_id=msg.request_id,
        )
    else:
        buyer = buyer_key(sender, msg.buyer_ref, _speaks_for_chats(rt))
        reply = answer_accept(rt.store, item_id, buyer, msg.counter_cents, msg.request_id)
    _log_answer(ctx, reply)
    await ctx.send(sender, reply)
    if isinstance(reply, Deal):
        await rt.checkout.start(reply.deal_id)  # payment link; RequestPayment for agent buyers


# --- Startup -------------------------------------------------------------------------------------


MARKET_DESCRIPTION = (  # Agentverse shows up to 300 characters
    "Sell your used stuff from a photo, or buy secondhand for less. Send a picture: Yardsale "
    "prices it from real eBay sales and gives it its own agent that haggles for you. Shopping? "
    "Say what you want and your max; an agent haggles with every seller at once. Pay by link."
)


def build(
    offline: bool,
    identify: Identify,
    research: Research,
    judge: Judge,
    read: ReadMessage = read_seller_message,
    fetch: FetchPhoto = fetch_photo,
    store: Store | None = None,
    payments: PaymentProvider | None = None,
    hosting: HostingClient | None = None,
    host_limit: int = 0,
    round_timeout: float = 30.0,
    pictures: ItemPictures | None = None,
) -> tuple[Bureau, Agent, list[Agent]]:
    """Create the Bureau, the Market, the buyer agent and three service agents, and the agents
    of the store's active listings that run here, inside a running loop. Without a store, state
    lives in memory. Without `payments`, deals get no payment link; without `hosting`, every
    item agent runs here; without `pictures`, hosted item agents get no picture. The returned
    services include the buyer agent."""
    global RUNTIME
    registration: BatchRegistrationPolicy = (
        NoBatchRegistration() if offline else BatchAlmanacApiRegistrationPolicy(logger=log)
    )
    bureau_endpoint = config.bureau_endpoint("offline" if offline else "live")
    bureau = Bureau(
        port=BUREAU_PORT,
        endpoint=[bureau_endpoint],
        registration_policy=registration,
        loop=asyncio.get_running_loop(),
    )
    services = [
        build_identifier(identify, offline),
        build_researcher(research, offline),
        build_appraiser(judge, offline),
    ]
    shopper: Shopper = build_shopper(offline, round_timeout)
    store = store or Store()
    rt = RUNTIME = Runtime(
        bureau=bureau,
        registration=registration,
        offline=offline,
        endpoint=bureau_endpoint,
        services=Services(*(agent.address for agent in services)),
        store=store,
        checkout=Checkout(store, payments),
        shopper=shopper.address,
        hosting=hosting,
        host_limit=host_limit,
        round_timeout=round_timeout,
        pictures=pictures,
    )
    # On Agentverse in live mode, so ASI:One can reach it. Handles messages concurrently, so a
    # photo listing waiting on a service doesn't block other messages.
    market = service_agent(
        "market", "Yardsale Orchestrator", "ys-orchestrator", MARKET_DESCRIPTION, offline
    )
    market.include(market_proto)
    market.include(market_buyer)
    market.include(build_payment_protocol(rt.checkout), publish_manifest=not offline)
    market.include(
        build_chat_protocol(MarketBackend(rt), read, fetch), publish_manifest=not offline
    )
    rt.market = shopper.market = market.address
    rt.checkout.market = market

    @market.on_interval(period=CHECK_EVERY)
    async def check_payments(ctx: Context) -> None:
        if rt.checkout.enabled:
            await rt.checkout.refresh_all()

    # Also runs on start, for agents whose listing closed while the server was down.
    @market.on_interval(period=RETRY_DELETES_EVERY)
    async def delete_closed_hosted_agents(ctx: Context) -> None:
        await delete_closed_hosted(rt)

    services.append(shopper.agent)
    for agent in (market, *services):
        bureau.add(agent)
        if offline:
            publish_locally(agent.address, bureau_endpoint)
    # Same seeds, same addresses. The Bureau starts and registers these with everything else.
    for listing in rt.store.active():
        if listing.host == BUREAU:
            _add_item_agent(rt, listing.item_id)
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


async def start_business_api(market: Agent) -> web.AppRunner | None:
    """@yardsale's HTTP API (business_api.py), when YARDSALE_API_KEY is set."""
    try:
        key = business_api.api_key()
        port = business_api.api_port()
    except business_api.ApiConfigError as error:
        log.error(f"The Fetch Business API is off: {error}")
        return None
    if key is None:
        return None
    api = business_api.BusinessApi(
        MarketBackend(runtime()),
        market._build_context,
        key,
        os.getenv("YARDSALE_API_URL", "").strip() or None,
    )
    runner = await business_api.serve(api, port)
    log.info(f"Fetch Business API on http://127.0.0.1:{port} (its description: /openapi.json)")
    return runner


async def main(mode: str) -> None:
    offline = mode != "live"
    if offline:
        stay_offline()
    checklist, _ = config.report(mode)
    for line in checklist.splitlines():
        log.info(line)

    store = Store(config.db_path(mode))
    try:
        payments = provider_from_env()
    except PaymentConfigError as error:
        log.error(f"Payments are off: {error}")
        payments = None
    api_key = os.getenv("AGENTVERSE_API_KEY", "").strip()
    hosting = HostingClient(api_key) if api_key and not offline else None
    bureau, market, services = build(
        offline,
        identify_with_claude,
        search_ebay_sold,
        judge_with_claude,
        store=store,
        payments=payments,
        hosting=hosting,
        host_limit=hosted_limit() if hosting else 0,
        pictures=item_pictures_from_env() if hosting else None,
    )
    if not offline:
        await connect_to_agentverse([market, *services])
    await start_business_api(market)

    write_json(MARKET_FILE, {"address": market.address, "offline": offline, "mode": mode})
    restored = len(runtime().agents)
    rt = runtime()
    pictures = ", with pictures drawn by ASI:One" if rt.pictures else ""
    log.info(
        f"Payments: {payments.label if payments else 'off (no payment keys)'}. Hosting item "
        f"agents on Agentverse: {f'up to {rt.host_limit}{pictures}' if rt.host_limit else 'off'}."
    )
    if offline:
        log.info(
            f"Mode: {mode.upper()} (local directory, nothing registered). Market: "
            f"{market.address}. Item agents restored from {store.path}: {restored}"
        )
    else:
        log.info(
            f"Mode: LIVE (Almanac API). Market: {market.address}, reached through its Agentverse "
            "mailbox. Sellers chat with it in ASI:One or from its Agentverse profile. Item agents "
            f"restored: {restored}, advertising {runtime().endpoint}"
        )
    for agent in services:
        log.info(f"{agent.name}: {agent.address}")
    # run_async() sets up every agent added so far, before any message can arrive. Item agents
    # added after this start themselves (start_item_agent).
    runtime().started = True
    await bureau.run_async()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--offline", action="store_true", help="skip the Almanac and Agentverse")
    parser.add_argument("--check", action="store_true", help="print what live mode still needs")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s [%(name)s] %(message)s")
    run_mode = "offline" if args.offline else "live"
    if args.check:
        text, ok = config.report(run_mode)
        print(text)
        sys.exit(0 if ok else 1)
    try:
        config.public_url()
    except config.ConfigError as error:
        parser.error(str(error))
    try:
        asyncio.run(main(run_mode))
    except KeyboardInterrupt:
        pass
