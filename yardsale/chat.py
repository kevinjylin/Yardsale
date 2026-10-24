"""The Market's ASI:One front door: Agent Chat Protocol messages from sellers and buyers.

Selling: the seller attaches a photo and can type anything else ("at least $40, small dent").
The Identifier, Researcher, and Appraiser price it, and the reply is a proposal: the item, the
suggested prices, and the eBay sales behind them. Nothing is listed until the seller replies
"approve". Before that they can change a price ("ask $360", "lowest $280") or reply "cancel".
Approving starts the item agent, and the reply gives its address.

Buying: the buyer says what they want ("find me a cheap road bike"), picks a result by number,
and makes offers ("offer $250"). The Market passes each offer to the item agent, which closes,
counters, or refuses. "accept" takes the latest counter. Buyers never see a floor price.

commands.py reads commands without a model. Anything else goes to ASI:One (asione.py).
Proposals, search results, and counter-offers also carry Interactive Cards (Approve, Details,
Accept buttons) from uagents_core's chat cards module. Tapping one does exactly what typing the
command does, with the same checks; the text works on its own wherever cards don't show.

ASI:One sends an upload as a ResourceContent: either a web link, or an `agent-storage://` file in
Agentverse storage that the Market downloads with its own identity. When its planner relays the
message, the link can also be in the text. The planner repeats earlier links in later messages, so
each conversation remembers which photos it has already priced.

The planner stops waiting for a reply after about 60 seconds, and pricing can take longer (the
eBay search alone can take a minute). So the Market answers within REPLY_WITHIN seconds of
receiving a message: with the result if it's ready, or with "still working" and then the result
as a second message.

One conversation (one ASI:One session) is one seller and one buyer. Its proposals, listings, and
counter-offers belong to it: no other conversation can see their floor prices, approve, change,
or cancel them, or accept its counter-offers.
"""

from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import json
import logging
import os
import re
from collections import OrderedDict, defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, NamedTuple, Protocol
from urllib.parse import urlparse
from uuid import uuid4

from uagents import Context
from uagents import Protocol as AgentProtocol
from uagents_core.contrib.protocols.chat import (
    ChatAcknowledgement,
    ChatMessage,
    MetadataContent,
    ResourceContent,
    TextContent,
    chat_protocol_spec,
)
from uagents_core.contrib.protocols.chat.cards import (
    CarouselCardPayload,
    CarouselItem,
    CtaAction,
    ReviewCardPayload,
    ReviewSummaryRow,
    SelectionValue,
    create_card_content,
    extract_card_response,
)

from yardsale import commands
from yardsale.asione import ReadMessage
from yardsale.common import PipelineError, UnpricedError
from yardsale.models import (
    AcceptCounter,
    Appraisal,
    Counter,
    Deal,
    ItemDetails,
    ListPhoto,
    Offer,
    OfferRejected,
)
from yardsale.photos import PhotoError, download_asset, prepare_photo
from yardsale.pricing import PriceError, to_cents, usd
from yardsale.search import search
from yardsale.store import ACTIVE, PROPOSED, SOLD, Listing, Store

REPLY_WITHIN = 40.0  # seconds; ASI:One's planner gives up on a reply after about 60
MAX_REMEMBERED = 200  # photo links per conversation, and message IDs overall

log = logging.getLogger("yardsale.chat")

FetchPhoto = Callable[[str], Awaitable[bytes]]
# Downloads an Agentverse storage asset shared with the agent handling the message.
FetchAsset = Callable[[Context, str], Awaitable[bytes]]


async def download_attachment(ctx: Context, asset_id: str) -> bytes:
    return await download_asset(ctx.agent.identity, asset_id)


class Backend(Protocol):
    """What the chat needs from the Market (bureau_server.MarketBackend)."""

    store: Store
    offline: bool
    boot_id: str

    async def propose(self, ctx: Context, owner: str, photo: ListPhoto) -> Listing:
        """Price a photo and save the proposal. Raises PipelineError or UnpricedError."""
        ...

    def save_proposal(
        self,
        owner: str,
        seller_id: str,
        listing_ref: str,
        item: ItemDetails,
        appraisal: Appraisal,
        asking_cents: int | None,
        floor_cents: int | None,
    ) -> Listing: ...

    async def activate(self, ctx: Context, listing: Listing) -> tuple[Listing, bool]:
        """Start the item agent. Returns the live listing and whether the Almanac has it."""
        ...

    async def negotiate(
        self, ctx: Context, item_id: str, request: Offer | AcceptCounter
    ) -> Counter | Deal | OfferRejected: ...

    def available(self) -> list[Listing]: ...


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif", ".gif"}
_MARKDOWN_LINK = re.compile(r"!?\[[^\]]*\]\((https?://[^\s)]+)\)")
_BARE_LINK = re.compile(r"https?://[^\s)\]<>\"']+")
_MENTION = re.compile(r"^\s*@\S+\s*")
_MENTIONS_PHOTO = re.compile(
    r"\b(photos?|pictures?|pics?|images?|upload(ed)?|attach(ed|ment)?)\b", re.IGNORECASE
)

HELP = (
    "Hi, I'm Yardsale.\n"
    "To sell: attach a photo of the item. I'll work out what it is, price it from recent eBay "
    'sales, and suggest a listing for you to approve. You can add your own price, like "at '
    'least $40".\n'
    'To buy: tell me what you\'re looking for, like "find me a cheap road bike", then make an '
    "offer. Each item has its own agent that haggles for the seller."
)
NEEDS_PHOTO = "Attach a photo of the item and I'll price it from recent eBay sales."
PHOTO_MISSING = (
    "Your photo didn't come through with that message. Please attach it again, as a file or a "
    "direct image link."
)
STILL_WORKING = (
    "Got your photo. I'm identifying it and checking recent eBay sales now, which can take a "
    "minute or two. I'll send the suggested price here for you to approve."
)
WORKING = "Working on it. I'll reply here in a moment."
SOMETHING_BROKE = "Sorry, something went wrong on my side. Please send that again."
APPROVE_PROMPT = (
    'Reply "approve" to put it up for sale, "ask $X" or "lowest $X" to change a price, or "cancel".'
)
RESTARTED = (
    "A photo you sent earlier didn't finish pricing because Yardsale restarted. Please send it "
    "again.\n\n"
)
SOURCES = {
    "seller": "your price",
    "appraisal": "from recent eBay sales",
    "default": "70% of the asking price",
}


class Reply(NamedTuple):
    text: str
    card: MetadataContent | None = None  # an Interactive Card shown with the text


def as_reply(answer: str | Reply) -> Reply:
    return answer if isinstance(answer, Reply) else Reply(answer)


@dataclass(frozen=True)
class Photo:
    url: str  # a web link, or the agent-storage:// URI of an Agentverse file
    mime_type: str
    asset_id: str | None = None  # set for Agentverse files, which are downloaded by ID


@dataclass
class Conversation:
    """What the chat remembers about one conversation. Saved in the store after each message."""

    key: str
    greeted: bool = False
    seen_photos: list[str] = field(default_factory=list)  # links already priced
    pricing: dict[str, str] = field(default_factory=dict)  # link -> boot ID of the run pricing it
    unpriced: dict[str, Any] | None = None  # identified, but waiting for the seller's price
    results: list[str] = field(default_factory=list)  # item IDs from the last search, as numbered
    selected: str | None = None  # the item an offer without a number is for
    counter: dict[str, Any] | None = None  # {"item_id", "cents"}: the open counter-offer
    awaiting: str | None = None  # "proposal" or "counter": what a bare "yes" answers

    @property
    def owner(self) -> str:
        return f"asi1:{self.key}"


def conversation_key(ctx: Context, sender: str) -> str:
    """A stable, opaque ID for one conversation. The raw session ID is never stored or shown.

    ASI:One's planner relays many users from one address, so the session, not the sender,
    tells conversations apart.
    """
    source = f"session:{ctx.session}" if ctx.session else f"sender:{sender}"
    return hashlib.sha256(source.encode()).hexdigest()[:24]


def _trim(link: str) -> str:
    """Sentence punctuation right after a link isn't part of it."""
    return link.rstrip(".,;:!?")


def _is_image_link(url: str) -> bool:
    parsed = urlparse(url)
    extension = os.path.splitext(parsed.path.lower())[1]
    is_cloudinary_image = parsed.hostname == "res.cloudinary.com" and "/image/upload/" in url
    return extension in IMAGE_EXTENSIONS or is_cloudinary_image


def _resource_url(uri: str, metadata: dict[str, str]) -> str | None:
    for candidate in (
        uri,
        *(metadata.get(key, "") for key in ("url", "uri", "source", "image_url")),
    ):
        if urlparse(candidate).scheme in ("http", "https"):
            return candidate
    return None


def photos_in(msg: ChatMessage) -> list[Photo]:
    """Every attached photo, from ResourceContent or from image links in the text."""
    found: list[Photo] = []
    for item in msg.content:
        if not isinstance(item, ResourceContent):
            continue
        for resource in item.resource if isinstance(item.resource, list) else [item.resource]:
            # ASI:One's mime_type isn't always a full "image/jpeg", so an image link is trusted
            # whatever it says. Anything that isn't a photo is refused later, when it's opened.
            mime_type = resource.metadata.get("mime_type", "")
            image_type = not mime_type or mime_type.lower().startswith("image")
            url = _resource_url(resource.uri, resource.metadata)
            if url and (image_type or _is_image_link(url)):
                found.append(Photo(url=url, mime_type=mime_type or "image/*"))
            elif not url and image_type and resource.uri.startswith("agent-storage://"):
                found.append(
                    Photo(
                        url=resource.uri,
                        mime_type=mime_type or "image/*",
                        asset_id=str(item.resource_id),
                    )
                )
    text = msg.text()
    links = _MARKDOWN_LINK.findall(text) + [_trim(link) for link in _BARE_LINK.findall(text)]
    for link in links:
        if _is_image_link(link) and all(link != photo.url for photo in found):
            found.append(Photo(url=link, mime_type="image/*"))
    return found


def clean_text(msg: ChatMessage) -> str:
    """The person's words, without photo links or the @agent mention direct chats start with."""
    text = _MENTION.sub("", msg.text())
    text = _MARKDOWN_LINK.sub(" ", text)
    text = _BARE_LINK.sub(lambda m: " " if _is_image_link(_trim(m.group(0))) else m.group(0), text)
    return " ".join(text.split())


def text_message(
    text: str, offer_uploads: bool = False, card: MetadataContent | None = None
) -> ChatMessage:
    content: list[TextContent | MetadataContent] = [TextContent(type="text", text=text)]
    if card is not None:
        content.append(card)
    if offer_uploads:
        # Tells ASI:One this agent accepts file uploads.
        content.append(MetadataContent(type="metadata", metadata={"attachments": "true"}))
    return ChatMessage(timestamp=datetime.now(UTC), msg_id=uuid4(), content=content)


def attachments_summary(msg: ChatMessage) -> str:
    """What each attachment looks like, for the log, so a photo that's missed can be diagnosed."""
    parts = []
    for item in msg.content:
        if isinstance(item, ResourceContent):
            for resource in item.resource if isinstance(item.resource, list) else [item.resource]:
                mime_type = resource.metadata.get("mime_type")
                parts.append(
                    f"{resource.uri[:120]} mime_type={mime_type!r} keys={sorted(resource.metadata)}"
                )
    return "; ".join(parts) or "none"


def card_choice(msg: ChatMessage) -> dict[str, SelectionValue] | None:
    """What a tapped card button chose, or None if this message isn't a card response.

    The cards module's README says some clients still send the selection as JSON text, so that
    works too. A dismissed card is an empty choice.
    """
    for block in msg.content:
        if isinstance(block, MetadataContent):
            response = extract_card_response(block)
            if response is not None:
                return {} if response.cancelled else dict(response.selection or {})
    text = msg.text().strip()
    if text.startswith("{") and text.endswith("}"):
        try:
            data = json.loads(text)
        except ValueError:
            return None
        if isinstance(data, dict) and isinstance(data.get("action"), str):
            return {k: v for k, v in data.items() if isinstance(v, str | int | float | bool)}
    return None


# --- Replies -------------------------------------------------------------------------------------


def _link(title: str, url: str) -> str:
    title = re.sub(r"[\[\]()]", "", title)[:70]
    return f"[{title}]({url})" if url else title


def proposal_text(listing: Listing) -> str:
    appraisal = listing.appraisal
    condition = f" ({listing.item.condition} condition)" if listing.item else ""
    asking_note = SOURCES[listing.asking_from]
    if listing.asking_from == "seller" and appraisal and appraisal.asking_cents is not None:
        asking_note += f"; recent eBay sales suggest {usd(appraisal.asking_cents)}"
    lines = [
        f"Here's my suggested listing for your {listing.title}{condition}:",
        f"- Asking price: {usd(listing.asking_cents)} ({asking_note})",
        f"- Lowest price you'll take: {usd(listing.floor_cents)} "
        f"({SOURCES[listing.floor_from]}; buyers never see it)",
    ]
    if appraisal is None or appraisal.asking_cents is None:
        lines.append("I couldn't find close eBay sales, so this uses your price.")
    else:
        spread = ""
        if appraisal.low_cents is not None and appraisal.high_cents is not None:
            spread = f", most between {usd(appraisal.low_cents)} and {usd(appraisal.high_cents)}"
        lines.append(f"Based on {appraisal.basis}{spread}. Closest sales:")
        lines += [
            f"- {_link(match.comp.title, match.comp.url)}: {usd(match.comp.price_cents)}"
            for match in appraisal.matches[:3]
        ]
    lines.append(APPROVE_PROMPT)
    return "\n".join(lines)


def proposal_card(listing: Listing) -> MetadataContent:
    rows = [
        ReviewSummaryRow(label="Asking price", value=usd(listing.asking_cents)),
        ReviewSummaryRow(
            label="Lowest price (buyers never see it)", value=usd(listing.floor_cents)
        ),
    ]
    appraisal = listing.appraisal
    if appraisal is not None and appraisal.asking_cents is not None:
        used = f"{len(appraisal.matches)} ({appraisal.confidence} confidence)"
        rows.append(ReviewSummaryRow(label="eBay sales used", value=used))
    choice: dict[str, Any] = {"item_id": listing.item_id}
    return create_card_content(
        ReviewCardPayload(
            title=f"List your {listing.title}?",
            summary_rows=rows,
            approve_cta=CtaAction(
                label="Approve", selection={"action": "approve", **choice}, primary=True
            ),
            reject_cta=CtaAction(label="Cancel", selection={"action": "cancel", **choice}),
        ),
        card_id=uuid4(),
    )


def results_card(found: list[Listing]) -> MetadataContent:
    return create_card_content(
        CarouselCardPayload(
            title="For sale",
            items=[
                CarouselItem(
                    id=listing.item_id,
                    title=listing.title,
                    subtitle=usd(listing.asking_cents),
                    secondary_text=listing.item.condition if listing.item else None,
                    primary_cta=CtaAction(
                        label="Details", selection={"action": "pick", "item_id": listing.item_id}
                    ),
                )
                for listing in found
            ],
        ),
        card_id=uuid4(),
    )


def counter_card(listing: Listing, counter_cents: int) -> MetadataContent:
    return create_card_content(
        ReviewCardPayload(
            title=f"Buy the {listing.title} for {usd(counter_cents)}?",
            summary_rows=[
                ReviewSummaryRow(label="Asking price", value=usd(listing.asking_cents)),
                ReviewSummaryRow(label="Counter-offer", value=usd(counter_cents)),
                ReviewSummaryRow(label="Good for", value="24 hours"),
            ],
            approve_cta=CtaAction(
                label=f"Accept {usd(counter_cents)}",
                selection={"action": "accept", "item_id": listing.item_id, "cents": counter_cents},
                primary=True,
            ),
            reject_cta=CtaAction(
                label="No thanks", selection={"action": "decline", "item_id": listing.item_id}
            ),
        ),
        card_id=uuid4(),
    )


def listed_text(listing: Listing, registered: bool, offline: bool) -> str:
    lines = [
        f"Your {listing.title} is for sale at {usd(listing.asking_cents)}.",
        f"Its agent is live at {listing.address}",
        f"It counters offers below {usd(listing.floor_cents)} and accepts anything at or above "
        'it. Say "my listings" any time to check on it.',
    ]
    if offline:
        lines.append(
            "(Offline mode: this address was made on this server and isn't on the public Almanac.)"
        )
    elif not registered:
        lines.append("(The Almanac hasn't confirmed it yet. The server retries every hour.)")
    return "\n".join(lines)


def results_text(found: list[Listing], query: str, owner: str) -> str:
    heading = f"Here's what's for sale for \"{query}\":" if query else "Here's what's for sale:"
    lines = [heading]
    for n, listing in enumerate(found, 1):
        condition = f" ({listing.item.condition})" if listing.item else ""
        yours = " (yours)" if listing.owner == owner else ""
        lines.append(f"{n}. {listing.title}: {usd(listing.asking_cents)}{condition}{yours}")
    lines.append('Reply with a number for details, or make an offer like "offer $X on 1".')
    return "\n".join(lines)


def details_text(listing: Listing, mine: bool) -> str:
    lines = [f"{listing.title}: {usd(listing.asking_cents)}"]
    if listing.item is not None:
        lines.append(f"Condition: {listing.item.condition}")
        if listing.item.details:
            lines.append("Details: " + "; ".join(listing.item.details))
    lines.append(f"Its agent: {listing.address}")
    lines.append(
        "This is your own listing."
        if mine
        else 'Make an offer like "offer $X", or buy it now '
        f'with "offer {usd(listing.asking_cents)}".'
    )
    return "\n".join(lines)


def deal_text(deal: Deal) -> str:
    return (
        f"Deal! You agreed to buy the {deal.title} for {usd(deal.price_cents)}. Yardsale "
        f"recorded it as {deal.deal_id}. No payment has been taken: payment and pickup aren't "
        "automated yet, so this is an agreed price only."
    )


# --- The protocol --------------------------------------------------------------------------------


def build_chat_protocol(
    backend: Backend,
    read: ReadMessage,
    fetch: FetchPhoto,
    fetch_asset: FetchAsset = download_attachment,
) -> AgentProtocol:
    proto = AgentProtocol(spec=chat_protocol_spec)
    store = backend.store
    locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
    answered: OrderedDict[str, Reply | None] = OrderedDict()  # message ID -> reply, for resends
    follow_ups: set[asyncio.Task[None]] = set()  # keeps background replies from being collected
    fields = {f.name for f in dataclasses.fields(Conversation)} - {"key"}

    def load(key: str) -> Conversation:
        saved = store.load_conversation(key)
        return Conversation(key=key, **{k: v for k, v in saved.items() if k in fields})

    def save(conv: Conversation) -> None:
        state = dataclasses.asdict(conv)
        del state["key"]
        state["seen_photos"] = conv.seen_photos[-MAX_REMEMBERED:]
        store.save_conversation(conv.key, state)

    def waiting_too(conv: Conversation) -> str:
        """A reminder of other proposals still waiting for this seller."""
        pending = store.owned(conv.owner, PROPOSED)
        if not pending:
            return ""
        conv.awaiting = "proposal"
        return (
            f"\n\nYour {pending[0].title} is still waiting at {usd(pending[0].asking_cents)}. "
            'Reply "approve" or "cancel" for it.'
        )

    # --- Selling ---------------------------------------------------------------------------

    async def price_photo(ctx: Context, key: str, photo: Photo, text: str) -> Reply:
        owner = f"asi1:{key}"
        listing: Listing | None = None
        unpriced: UnpricedError | None = None
        try:
            wanted = await read(text, True)
            asking, floor = to_cents(wanted.asking_usd), to_cents(wanted.floor_usd)
            if photo.asset_id:
                data = await fetch_asset(ctx, photo.asset_id)
            else:
                data = await fetch(photo.url)
            image_b64, media_type = await asyncio.to_thread(prepare_photo, data)
            listing = await backend.propose(
                ctx,
                owner,
                ListPhoto(
                    listing_ref=uuid4().hex,
                    seller_id=owner,
                    image_b64=image_b64,
                    media_type=media_type,
                    note=wanted.note,
                    asking_cents=asking,
                    floor_cents=floor,
                ),
            )
            reply = Reply(proposal_text(listing), proposal_card(listing))
        except UnpricedError as ex:
            unpriced = ex
            reply = Reply(
                f"This looks like {ex.item.title}, but no recent eBay sales were close enough to "
                'suggest a price. Tell me your price, like "ask $40" or "at least $30", and I\'ll '
                'set up the listing for you to approve. Or say "cancel".'
            )
        except (PhotoError, PipelineError, PriceError) as ex:
            # A PriceError's message can include the seller's floor, which stays out of logs.
            if isinstance(ex, PriceError):
                detail = "prices didn't work"
            else:
                detail = f"{ex} ({ex.__cause__})" if ex.__cause__ else str(ex)
            ctx.logger.warning(f"Chat photo for {key[:8]} failed: {detail}")
            reply = Reply(f"I couldn't price this. {ex} Send the photo again to retry.")
        except Exception:
            ctx.logger.exception(f"Chat photo for {key[:8]} failed")
            reply = Reply(SOMETHING_BROKE)
        async with locks[key]:
            conv = load(key)
            conv.pricing.pop(photo.url, None)
            # Only a photo that got through pricing counts as seen; after a failure the same
            # link can be sent again.
            if listing is not None or unpriced is not None:
                conv.seen_photos.append(photo.url)
            if listing is not None:
                conv.unpriced = None
                conv.awaiting = "proposal"
            if unpriced is not None:
                conv.unpriced = {
                    "item": unpriced.item.model_dump(),
                    "appraisal": unpriced.appraisal.model_dump(),
                }
            save(conv)
        return reply

    async def change_prices(
        ctx: Context,
        conv: Conversation,
        proposal: Listing | None,
        asking: int | None,
        floor: int | None,
        approve_after: bool,
    ) -> str | Reply:
        if conv.unpriced is not None:
            listing = backend.save_proposal(
                conv.owner,
                conv.owner,
                uuid4().hex,
                ItemDetails.model_validate(conv.unpriced["item"]),
                Appraisal.model_validate(conv.unpriced["appraisal"]),
                asking,
                floor,
            )
            conv.unpriced = None
        elif proposal is not None:
            changed = store.reprice(proposal.item_id, conv.owner, asking, floor)
            if changed is None:
                return "That listing isn't waiting for approval anymore."
            listing = changed
        else:
            return NEEDS_PHOTO
        conv.awaiting = "proposal"
        if approve_after:
            return await approve(ctx, conv, listing)
        return Reply(proposal_text(listing), proposal_card(listing))

    async def approve(ctx: Context, conv: Conversation, proposal: Listing) -> str:
        try:
            listing, registered = await backend.activate(ctx, proposal)
        except PipelineError as ex:
            return f"I couldn't start the item agent. {ex}"
        conv.awaiting = None
        return listed_text(listing, registered, backend.offline) + waiting_too(conv)

    def cancel_proposal(conv: Conversation, proposal: Listing) -> str:
        conv.awaiting = None
        if store.cancel(proposal.item_id, conv.owner) is None:
            return "That listing isn't waiting for approval anymore."
        return f"OK, I canceled the {proposal.title}. Nothing was listed." + waiting_too(conv)

    def my_listings(conv: Conversation) -> str:
        listings = store.owned(conv.owner)
        if not listings:
            return "You haven't listed anything yet. Attach a photo of something to sell."
        lines: list[str] = []
        active = [listing for listing in listings if listing.status == ACTIVE]
        if active:
            lines.append("For sale:")
            lines += [
                f"{n}. {listing.title}: {usd(listing.asking_cents)}, lowest "
                f"{usd(listing.floor_cents)}. Agent: {listing.address}"
                for n, listing in enumerate(active, 1)
            ]
        for listing in listings:
            if listing.status == PROPOSED:
                lines.append(
                    f"Waiting for your approval: {listing.title} at {usd(listing.asking_cents)}"
                )
            elif listing.status == SOLD:
                deal = store.deal_for(listing.item_id)
                price = f" for {usd(deal.price_cents)}" if deal else ""
                lines.append(f"Sold: {listing.title}{price} (payment not collected)")
        if active:
            lines.append('Say "unlist 1" to take one down.')
        return "\n".join(lines)

    def unlist(conv: Conversation, pick: int | None) -> str:
        active = store.owned(conv.owner, ACTIVE)
        if not active:
            return "You have nothing for sale right now."
        if pick is None and len(active) > 1:
            return 'Which one? Say "unlist 2", using the numbers from "my listings".'
        index = (pick or 1) - 1
        if not 0 <= index < len(active):
            return f"Pick a number from 1 to {len(active)}."
        listing = active[index]
        if store.cancel(listing.item_id, conv.owner) is None:
            return f"The {listing.title} isn't for sale anymore."
        return f"Your {listing.title} is no longer for sale. Its agent turns down new offers."

    # --- Buying ----------------------------------------------------------------------------

    def find(conv: Conversation, query: str, budget_cents: int | None) -> str | Reply:
        found = search(backend.available(), query, budget_cents)
        conv.results = [listing.item_id for listing in found]
        conv.selected = found[0].item_id if len(found) == 1 else None
        if not found:
            under = f" under {usd(budget_cents)}" if budget_cents else ""
            about = f' for "{query}"' if query else ""
            return (
                f"Nothing is for sale{about}{under} right now. Try other words, or ask "
                '"what\'s for sale?"'
            )
        return Reply(results_text(found, query, conv.owner), results_card(found))

    def pick(conv: Conversation, number: int) -> str:
        if not 1 <= number <= len(conv.results):
            return f"Pick a number from 1 to {len(conv.results)}."
        listing = store.get(conv.results[number - 1])
        if listing is None or listing.status != ACTIVE:
            return "Sorry, that one is no longer for sale."
        conv.selected = listing.item_id
        return details_text(listing, mine=listing.owner == conv.owner)

    def offer_target(conv: Conversation, number: int | None) -> str | None:
        if number is not None:
            if 1 <= number <= len(conv.results):
                return conv.results[number - 1]
            return None
        if conv.selected:
            return conv.selected
        return conv.results[0] if len(conv.results) == 1 else None

    def answer(
        conv: Conversation, listing: Listing, reply: Counter | Deal | OfferRejected
    ) -> str | Reply:
        if isinstance(reply, Counter):
            conv.counter = {"item_id": listing.item_id, "cents": reply.counter_cents}
            conv.awaiting = "counter"
            return Reply(
                f"The seller's agent countered with {usd(reply.counter_cents)} for the "
                f'{listing.title}. Reply "accept" to buy it at {usd(reply.counter_cents)}, or '
                "make another offer. The counter-offer is good for 24 hours.",
                counter_card(listing, reply.counter_cents),
            )
        conv.counter = None
        conv.awaiting = None
        if isinstance(reply, Deal):
            return deal_text(reply)
        if reply.code in ("unavailable", "unknown_item"):
            return f"Sorry, the {listing.title} is no longer for sale."
        if reply.code == "counter_expired":
            return "That counter-offer expired. Make a new offer."
        if reply.code in ("no_counter", "counter_changed"):
            return "That counter-offer isn't open anymore. Make a new offer."
        return f"The seller's agent turned that down: {reply.reason}."

    async def make_offer(
        ctx: Context, conv: Conversation, cents: int, number: int | None
    ) -> str | Reply:
        item_id = offer_target(conv, number)
        if item_id is None:
            if not conv.results:
                return 'Tell me what you\'re looking for first, like "find me a road bike".'
            return f'Which one? Say "offer {usd(cents)} on 2", or pick a number first.'
        listing = store.get(item_id)
        if listing is None or listing.status != ACTIVE:
            return "Sorry, that one is no longer for sale."
        if listing.owner == conv.owner:
            return "That's your own listing."
        if cents > listing.asking_cents:
            return (
                f"That's more than the asking price of {usd(listing.asking_cents)}. Offer "
                f"{usd(listing.asking_cents)} or less."
            )
        conv.selected = item_id
        reply = await backend.negotiate(
            ctx, item_id, Offer(item_id=item_id, offer_cents=cents, buyer_ref=conv.key)
        )
        return answer(conv, listing, reply)

    async def accept(ctx: Context, conv: Conversation) -> str | Reply:
        assert conv.counter is not None
        item_id, cents = str(conv.counter["item_id"]), int(conv.counter["cents"])
        listing = store.get(item_id)
        if listing is None:
            conv.counter = None
            return "That counter-offer isn't open anymore."
        reply = await backend.negotiate(
            ctx, item_id, AcceptCounter(item_id=item_id, counter_cents=cents, buyer_ref=conv.key)
        )
        return answer(conv, listing, reply)

    # --- Routing ---------------------------------------------------------------------------

    async def handle_choice(
        ctx: Context, conv: Conversation, choice: dict[str, SelectionValue]
    ) -> str | Reply:
        """A tapped card button. Checked exactly like the typed command it stands for."""
        action, item_id = str(choice.get("action", "")), str(choice.get("item_id", ""))
        if not choice:
            return "OK."
        if action in ("approve", "cancel"):
            listing = store.get(item_id)
            if listing is None or listing.owner != conv.owner or listing.status != PROPOSED:
                return "That listing isn't waiting for approval anymore."
            if action == "approve":
                return await approve(ctx, conv, listing)
            return cancel_proposal(conv, listing)
        if action == "accept":
            counter = conv.counter
            if counter is None or (counter["item_id"], counter["cents"]) != (
                item_id,
                choice.get("cents"),
            ):
                return "That counter-offer isn't open anymore. Make a new offer."
            return await accept(ctx, conv)
        if action == "decline":
            conv.counter = None
            conv.awaiting = None
            return "OK. Make another offer any time."
        if action == "pick" and item_id in conv.results:
            return pick(conv, conv.results.index(item_id) + 1)
        if action == "pick":
            return "That list is out of date. Search again to see what's for sale."
        return HELP

    async def handle_text(ctx: Context, conv: Conversation, text: str) -> str | Reply:
        command = commands.parse(text)
        if command is not None and command.error:
            return command.error
        kind = command.kind if command else None
        pending = store.owned(conv.owner, PROPOSED)
        proposal = pending[0] if pending else None
        selling = proposal is not None or conv.unpriced is not None

        if command is not None and kind == "edit" and selling:
            return await change_prices(
                ctx, conv, proposal, command.asking_cents, command.floor_cents, command.approve
            )
        if command is not None and kind == "offer" and command.amount_cents is not None:
            if selling and not conv.results:
                return f'Did you mean "ask {usd(command.amount_cents)}" for your listing?'
            return await make_offer(ctx, conv, command.amount_cents, command.pick)
        if kind == "accept" or (kind == "approve" and conv.awaiting == "counter"):
            if conv.counter is not None:
                return await accept(ctx, conv)
            if proposal is not None:
                return await approve(ctx, conv, proposal)
            return "There's no counter-offer to accept. Make an offer first."
        if kind == "approve":
            if proposal is not None:
                return await approve(ctx, conv, proposal)
            if conv.unpriced is not None:
                return 'Tell me your price first, like "ask $40".'
            if conv.counter is not None:
                return await accept(ctx, conv)
            return "There's nothing waiting for approval. Attach a photo of something to sell."
        if kind == "cancel":
            if conv.unpriced is not None:
                conv.unpriced = None
                return "OK, I dropped that photo. Nothing was listed."
            if proposal is not None:
                return cancel_proposal(conv, proposal)
            if conv.counter is not None:
                conv.counter = None
                conv.awaiting = None
                return "OK. Make another offer any time."
            return 'There\'s nothing waiting to cancel. Say "my listings" to see your listings.'
        if command is not None and kind == "unlist":
            return unlist(conv, command.pick)
        if kind == "listings":
            return my_listings(conv)
        if command is not None and kind == "select" and conv.results and command.pick:
            return pick(conv, command.pick)
        if kind == "help":
            return HELP
        if command is not None and kind == "amount" and command.amount_cents is not None:
            amount = usd(command.amount_cents)
            if selling:
                return f'Did you mean "ask {amount}" or "lowest {amount}"?'
            if conv.results:
                return f'Did you mean "offer {amount}"?'

        # Not a command, or not one that fits this conversation: ASI:One reads it.
        wanted = await read(text, False)
        if wanted.intent == "buy":
            return find(conv, wanted.query, to_cents(wanted.budget_usd))
        if wanted.intent == "sell":
            asking, floor = to_cents(wanted.asking_usd), to_cents(wanted.floor_usd)
            if selling and (asking or floor):
                return await change_prices(ctx, conv, proposal, asking, floor, False)
            return PHOTO_MISSING if _MENTIONS_PHOTO.search(text) else NEEDS_PHOTO
        # Neither buying nor selling, as far as the reader can tell. If the words match
        # something for sale ("espresso machine?"), show it anyway.
        if text and search(backend.available(), text):
            return find(conv, text, None)
        if proposal is not None:
            return f"Your {proposal.title} is waiting at {usd(proposal.asking_cents)}. " + (
                APPROVE_PROMPT
            )
        return HELP

    async def handle(ctx: Context, key: str, msg: ChatMessage) -> Reply:
        text = clean_text(msg)
        choice = card_choice(msg)
        async with locks[key]:
            conv = load(key)
            notice = ""
            lost = [link for link, boot in conv.pricing.items() if boot != backend.boot_id]
            if lost:
                for link in lost:
                    del conv.pricing[link]
                notice = RESTARTED
            photos = [
                photo
                for photo in photos_in(msg)
                if photo.url not in conv.seen_photos and photo.url not in conv.pricing
            ]
            if not photos:
                try:
                    if choice is not None:
                        reply = as_reply(await handle_choice(ctx, conv, choice))
                    else:
                        reply = as_reply(await handle_text(ctx, conv, text))
                except PriceError as ex:
                    reply = Reply(str(ex))
                except PipelineError as ex:
                    reply = Reply(f"Sorry, that didn't go through. {ex}")
                save(conv)
                return Reply(notice + reply.text, reply.card)
            conv.pricing[photos[0].url] = backend.boot_id
            save(conv)
        reply = await price_photo(ctx, key, photos[0], text)
        more = ""
        if len(photos) > 1:
            more = "\n\n(I used the first photo. Send other items one message at a time.)"
        return Reply(notice + reply.text + more, reply.card)

    async def handle_safely(ctx: Context, key: str, msg: ChatMessage) -> Reply:
        try:
            return await handle(ctx, key, msg)
        except Exception:
            ctx.logger.exception(f"Chat message in {key[:8]} failed")
            return Reply(SOMETHING_BROKE)

    @proto.on_message(ChatMessage)
    async def on_chat(ctx: Context, sender: str, msg: ChatMessage) -> None:
        received = asyncio.get_running_loop().time()
        await ctx.send(
            sender, ChatAcknowledgement(timestamp=datetime.now(UTC), acknowledged_msg_id=msg.msg_id)
        )
        msg_id = str(msg.msg_id)
        if msg_id in answered:  # a resend: answer it again rather than act twice
            earlier = answered[msg_id]
            if earlier is not None:
                await ctx.send(sender, text_message(earlier.text, card=earlier.card))
            return
        answered[msg_id] = None
        while len(answered) > MAX_REMEMBERED:
            answered.popitem(last=False)

        key = conversation_key(ctx, sender)
        async with locks[key]:
            conv = load(key)
            first_message = not conv.greeted
            if first_message:
                conv.greeted = True
                save(conv)
        # Message text can include a seller's lowest price, so it isn't logged.
        ctx.logger.info(
            f"Chat message in {key[:8]} ({len(photos_in(msg))} photo link(s)), "
            f"attachments: {attachments_summary(msg)}"
        )

        job = asyncio.ensure_future(handle_safely(ctx, key, msg))
        left = REPLY_WITHIN - (asyncio.get_running_loop().time() - received)
        done, _ = await asyncio.wait({job}, timeout=max(0.0, left))
        if done:
            reply = job.result()
        else:
            reply = Reply(STILL_WORKING if photos_in(msg) else WORKING)

            async def send_when_done() -> None:
                result = await job
                answered[msg_id] = result
                await ctx.send(sender, text_message(result.text, card=result.card))

            follow_up = asyncio.ensure_future(send_when_done())
            follow_ups.add(follow_up)
            follow_up.add_done_callback(follow_ups.discard)
        answered[msg_id] = reply
        await ctx.send(
            sender, text_message(reply.text, offer_uploads=first_message, card=reply.card)
        )

    @proto.on_message(ChatAcknowledgement)
    async def on_ack(ctx: Context, sender: str, msg: ChatAcknowledgement) -> None:
        pass

    return proto
