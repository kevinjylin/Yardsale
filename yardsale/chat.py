"""The Market's ASI:One front door: Agent Chat Protocol messages from sellers and buyers.

Selling: the seller attaches a photo and can type anything else ("at least $40, small dent").
The Identifier, Researcher, and Appraiser price it, and the reply is a proposal: the item, the
suggested prices, and the eBay sales behind them. Nothing is listed until the seller replies
"approve". Before that they can change a price ("ask $360", "lowest $280") or reply "cancel".
Approving starts the item agent, and the reply gives its address.

Several photos in one message (up to MAX_PHOTOS, one item each) become one proposal per photo,
numbered in the order they were sent and priced PHOTOS_AT_ONCE at a time. Each photo is its own
paid pricing run. A price typed with several photos isn't used, since it can't say which item it
means; the seller names items by number instead: "lowest 2 $40", "ask 3 $60", "approve 2",
"approve all", "cancel 2". With several proposals waiting, a plain "approve" asks which.

Buying: the buyer says what they want ("find me a cheap road bike"). They can say their max and
"haggle", and Yardsale's buyer agent (shopper.py) haggles with every matching item agent at once;
the reply shows every round and the best price. Or they pick a result by number and make offers
themselves ("offer $250"). Item agents agree to an offer or counter it. "accept" (or "accept 2")
buys at that price. Buyers never see a floor price, and sellers never see a buyer's max.

Paying: once a deal closes, the Market's checkout (checkout.py) makes a payment link (Visa Pay By
Link or Stripe, test mode) and the chat shows it. "paid" checks with the provider.

commands.py reads commands without a model. Anything else goes to ASI:One (asione.py).
Proposals, search results, haggles, counter-offers, and payments also carry Interactive Cards
from uagents_core's chat cards module: Approve, the eBay sales behind a price, a form for the
seller's prices, Details, Buy, and "I've paid". Tapping one does exactly what typing the command
does, with the same checks; the text works on its own wherever cards don't show.

ASI:One sends an upload as a ResourceContent whose file sits in Agentverse storage, readable only
by the agent it was sent to, or as a web link in the resource or in the text when its planner
relays the message. The planner also repeats earlier links in later messages, so each conversation
remembers which photos it has already priced. A session opens with a StartSessionContent message,
which gets the attachments metadata and no text, as in Fetch's own ASI:One examples.

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
import base64
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
    StartSessionContent,
    TextContent,
    chat_protocol_spec,
)
from uagents_core.contrib.protocols.chat.cards import (
    BadgeNode,
    ButtonAction,
    ButtonNode,
    CarouselBadge,
    CarouselCardPayload,
    CarouselItem,
    CtaAction,
    CustomCardPayload,
    DividerNode,
    FormCardPayload,
    FormField,
    GroupNode,
    HeadingNode,
    ListNode,
    ReviewCardPayload,
    ReviewSummaryRow,
    SectionNode,
    SelectionValue,
    TextNode,
    create_card_content,
    extract_card_response,
)
from uagents_core.contrib.protocols.chat.cards import (
    ListItem as CardListItem,
)

from yardsale import advice, commands, craigslist_buyer, craigslist_mail, deals, watches
from yardsale.asione import ReadMessage
from yardsale.checkout import Checkout
from yardsale.common import PipelineError, UnpricedError
from yardsale.deals import ScoredDeal
from yardsale.ebay_seller import EbayError, EbaySeller
from yardsale.elsewhere import DealFinder, Found, search_words, site_name
from yardsale.models import (
    AcceptCounter,
    Appraisal,
    Counter,
    Deal,
    HaggleOutcome,
    HaggleResult,
    ItemDetails,
    ListPhoto,
    MarketView,
    Offer,
    OfferRejected,
)
from yardsale.payments import PaymentError, commission_text
from yardsale.photos import PhotoError, download_from_storage, download_photo, prepare_photo
from yardsale.pricing import PriceError, parse_usd, to_cents, usd
from yardsale.search import search
from yardsale.sources import ExternalListing, SourceError
from yardsale.store import (
    ACTIVE,
    AGENTVERSE,
    EXPIRED,
    NOT_COLLECTED,
    PAID,
    PENDING,
    PROPOSED,
    REJECTED,
    SOLD,
    DealRecord,
    Listing,
    Store,
)

REPLY_WITHIN = 40.0  # seconds; ASI:One's planner gives up on a reply after about 60
MAX_PHOTOS = 8  # photos priced from one message; each is a paid Apify search and two Claude calls
PHOTOS_AT_ONCE = 4  # photos from one message priced at the same time
BATCH_WAIT_SHARE = 0.9  # of REPLY_WITHIN: how long several photos wait to fill the first reply
MAX_REMEMBERED = 200  # photo links per conversation, and message IDs overall

log = logging.getLogger("yardsale.chat")

# Returns the photo's bytes. Raises PhotoError with a message for the seller.
FetchPhoto = Callable[[Context, "Photo"], Awaitable[bytes]]
# Sends a later message in the same conversation, after the first reply.
Deliver = Callable[["Reply"], Awaitable[None]]
# A seller's waiting photos by number: a proposal, or None for one that needs the seller's price.
Waiting = list[tuple[int, Listing | None]]


class Backend(Protocol):
    """What the chat needs from the Market (bureau_server.MarketBackend)."""

    store: Store
    offline: bool
    boot_id: str
    ebay: EbaySeller | None  # sellers' own eBay accounts; None when eBay isn't set up
    finder: DealFinder  # searches other sites when Yardsale has nothing; off without sources

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
        market: MarketView | None = None,
    ) -> Listing: ...

    async def activate(self, ctx: Context, listing: Listing) -> tuple[Listing, bool]:
        """Start the item agent. Returns the live listing and whether the Almanac has it."""
        ...

    def cancel(self, item_id: str, owner: str) -> Listing | None:
        """Take down the owner's proposal or listing, deleting a hosted agent. None if it
        wasn't proposed or active."""
        ...

    async def negotiate(
        self, ctx: Context, item_id: str, request: Offer | AcceptCounter
    ) -> Counter | Deal | OfferRejected: ...

    async def haggle(
        self, ctx: Context, buyer_ref: str, max_cents: int, listings: list[Listing]
    ) -> HaggleResult:
        """Have the buyer agent haggle with these items at once, never above max_cents."""
        ...

    def available(self) -> list[Listing]: ...

    @property
    def checkout(self) -> Checkout: ...


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
    f'least $40". Moving out? Attach up to {MAX_PHOTOS} photos at once, one item each. Say '
    '"connect ebay" to also list on your own eBay account.\n'
    "To buy: tell me what you're looking for and your max, like \"find me a road bike, haggle "
    "for me, max $250\". My buyer agent haggles with every matching seller's agent at once and "
    "shows you the best price. You can also make your own offers."
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
ALREADY_WORKING = (
    "I'm still pricing that photo. I'll send the suggested price here for you to approve as soon "
    "as it's ready."
)
SOMETHING_BROKE = "Sorry, something went wrong on my side. Please send that again."
APPROVE_PROMPT = (
    'Reply "approve" to put it up for sale, "ask $X" or "lowest $X" to change a price ("rules" '
    'for a form), or "cancel".'
)
BATCH_PROMPT = (
    'Reply "approve all" to put them all up for sale, or name one by number: "approve 2", '
    '"ask 2 $60", "lowest 2 $40", or "cancel 2".'
)
ASK_MAX = (
    "What's the most you'd pay? My buyer agent never offers more than that, and never tells the "
    'sellers. Reply with an amount, like "$250".'
)
LOWEST_SHARE_REACH = (10, 7)  # haggle with items asking up to max * 10/7: floors are often 70%
EBAY_LIST_WITHIN = 30.0  # seconds approval waits for eBay before replying without it
EBAY_CONNECTED = (
    "Your eBay account is connected. Listings you approve also go up on eBay, and my agent "
    "answers their Best Offers with your lowest price, which eBay never sees.\n\n"
)
CRAIGSLIST_END = (
    "Craigslist posts are yours to delete: open the post from Craigslist's email and delete it. "
    'Your Yardsale listing stays up; say "unlist" to take it down everywhere.'
)
EBAY_OFF = "eBay isn't set up on this Yardsale server, so listings stay on Yardsale."
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
    # Messages sent right after this one, such as the eBay sales behind a proposal's price.
    more: tuple[tuple[str, MetadataContent | None], ...] = ()
    # (search, budget): search other sites, outside the conversation's lock, and add the best
    # deals to this text before it's sent.
    elsewhere: tuple[str, int | None] | None = None


def as_reply(answer: str | Reply) -> Reply:
    return answer if isinstance(answer, Reply) else Reply(answer)


@dataclass(frozen=True)
class Photo:
    url: str  # a web link, or the resource URI (agent-storage://...) of a chat upload
    mime_type: str
    resource_id: str | None = None  # the Agentverse storage asset of a chat upload


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
    awaiting: str | None = None  # "proposal", "counter", or "max": what a bare reply answers
    quotes: dict[str, int] = field(default_factory=dict)  # item ID -> price on offer to this buyer
    budget: int | None = None  # the buyer's max from their last search or haggle
    # Seller's photos by number ("approve 2"): proposal item IDs, "" for photos that aren't one.
    numbered: list[str] = field(default_factory=list)
    needs_price: dict[str, Any] = field(default_factory=dict)  # number -> item without a price
    ebay_zip: str | None = None  # the seller's ZIP code, for their eBay listings
    last_query: str = ""  # the buyer's last search words, for "search other sites" and "watch"

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
            web_url = _resource_url(resource.uri, resource.metadata)
            if web_url:
                url = web_url if image_type or _is_image_link(web_url) else ""
            else:
                # An upload's URI is usually agent-storage://..., which only the storage API opens.
                url = resource.uri.strip() if image_type else ""
            if url and all(url != photo.url for photo in found):
                found.append(
                    Photo(
                        url=url, mime_type=mime_type or "image/*", resource_id=str(item.resource_id)
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


UPLOADS_WELCOME = MetadataContent(type="metadata", metadata={"attachments": "true"})


def text_message(
    text: str, offer_uploads: bool = False, card: MetadataContent | None = None
) -> ChatMessage:
    content: list[TextContent | MetadataContent] = [TextContent(type="text", text=text)]
    if card is not None:
        content.append(card)
    if offer_uploads:
        content.append(UPLOADS_WELCOME)  # tells ASI:One this agent accepts file uploads
    return ChatMessage(timestamp=datetime.now(UTC), msg_id=uuid4(), content=content)


def uploads_welcome() -> ChatMessage:
    """Fetch's ASI:One examples answer a session start with this, before any text."""
    return ChatMessage(timestamp=datetime.now(UTC), msg_id=uuid4(), content=[UPLOADS_WELCOME])


async def fetch_photo(ctx: Context, photo: Photo) -> bytes:
    """Download a chat photo: from its web link if it has one, else from Agentverse storage."""
    if urlparse(photo.url).scheme in ("http", "https"):
        try:
            return await download_photo(photo.url)
        except PhotoError:
            if photo.resource_id is None:
                raise
            ctx.logger.info("The photo's link didn't work; trying Agentverse storage")
    if photo.resource_id is None:
        raise PhotoError("I can only open photo links that start with http:// or https://.")
    return await asyncio.to_thread(download_from_storage, ctx.agent.identity, photo.resource_id)


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


def proposal_text(listing: Listing, n: int | None = None) -> str:
    """The proposal, with what the market says and a plain word when the seller's own price is
    far off it. `n` is the proposal's number when the seller has several."""
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
    lines += advice.market_lines(listing)
    feedback = advice.price_feedback(listing, n)
    if feedback:
        lines.append(feedback)
    lines.append(APPROVE_PROMPT)
    return "\n".join(lines)


def numbered_proposal_text(listing: Listing, n: int) -> str:
    """A proposal named by its number, for a seller with several photos."""
    prompt = (
        f'Reply "approve {n}" to put it up for sale, "ask {n} $X" or "lowest {n} $X" to change a '
        f'price, or "cancel {n}".'
    )
    return f"#{n}: " + proposal_text(listing, n).removesuffix(APPROVE_PROMPT) + prompt


def unpriced_hint(market: MarketView | None) -> str:
    """What the Scout found when no sold listing was close enough: live asking prices, if any,
    so the seller's own price has something to go on."""
    if market is None or market.asking_median_cents is None:
        return ""
    lines = advice.market_lines(_unpriced_listing(market))
    return ("\n" + "\n".join(lines)) if lines else ""


def _unpriced_listing(market: MarketView) -> Listing:
    return Listing(
        item_id="", owner="", seller_id="", listing_ref="", status=PROPOSED, title="",
        asking_cents=1, floor_cents=1, market=market,
    )  # fmt: skip


def saved_market(saved: dict[str, Any]) -> MarketView | None:
    """The market view kept with an unpriced photo, if the Scout found anything."""
    data = saved.get("market")
    return MarketView.model_validate(data) if data else None


def pending_photo_key(owner: str, n: int) -> str:
    """Where photo #n's prepared image waits for the seller's price, for listing it on eBay."""
    return f"pending:{owner}#{n}"


def batch_notes(count: int, text: str) -> str:
    """What the seller should know about a message with several photos."""
    notes = ""
    if count > MAX_PHOTOS:
        notes += (
            f"\n\nI price up to {MAX_PHOTOS} photos from one message, so I took the first "
            f"{MAX_PHOTOS}. Attach the other {count - MAX_PHOTOS} in another message."
        )
    asking, floor, _ = commands.seller_prices(text.lower())
    if asking is not None or floor is not None:
        notes += (
            "\n\nYou sent several photos, so I didn't use the price in your message: it could "
            'mean any of them. Set each one\'s by number, like "lowest 2 $40" or "ask 2 $60".'
        )
    return notes


def proposal_reply(listing: Listing) -> Reply:
    """The proposal with its Approve card, then the eBay sales behind the price."""
    comps = comps_card(listing)
    more = ((comps_text(listing), comps),) if comps is not None else ()
    return Reply(proposal_text(listing), proposal_card(listing), more)


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


def comps_card(listing: Listing) -> MetadataContent | None:
    """The eBay sales behind a proposal's price, with their photos."""
    appraisal = listing.appraisal
    if appraisal is None or not appraisal.matches:
        return None
    items = []
    for n, match in enumerate(appraisal.matches[:6]):
        comp = match.comp
        badge = (
            CarouselBadge(label="Same product", variant="success")
            if match.same_product
            else CarouselBadge(label="Similar", variant="info")
        )
        items.append(
            CarouselItem(
                id=f"{listing.item_id}-{n}",
                image=comp.image_url,
                title=comp.title[:80],
                subtitle=f"{usd(comp.price_cents)}, sold {comp.sold_at}",
                badges=[badge],
                secondary_text=match.reason[:120] or None,
                primary_cta=CtaAction(
                    label="Why this sale?",
                    selection={"action": "comp", "item_id": listing.item_id, "n": n},
                ),
            )
        )
    return create_card_content(
        CarouselCardPayload(
            title="The eBay sales behind this price",
            subtitle=appraisal.basis[:120] or None,
            items=items,
        ),
        card_id=uuid4(),
    )


def comps_text(listing: Listing) -> str:
    count = len(listing.appraisal.matches) if listing.appraisal else 0
    return f"Here are the {min(count, 6)} closest eBay sales behind that price."


def rules_card(listing: Listing) -> MetadataContent:
    """A form for the seller's own prices: asking and lowest."""
    return create_card_content(
        FormCardPayload(
            title=f"Your prices for the {listing.title}",
            fields=[
                FormField(
                    name="asking",
                    kind="number",
                    label="Asking price ($)",
                    placeholder=f"{listing.asking_cents / 100:.2f}",
                ),
                FormField(
                    name="lowest",
                    kind="number",
                    label="Lowest price you'll take ($). Buyers never see it.",
                    placeholder=f"{listing.floor_cents / 100:.2f}",
                ),
            ],
            submit_cta=CtaAction(
                label="Save prices",
                selection={"action": "rules", "item_id": listing.item_id},
                primary=True,
            ),
        ),
        card_id=uuid4(),
    )


def counter_card(listing: Listing, counter_cents: int, agreed: bool = False) -> MetadataContent:
    price_label = "Agreed price" if agreed else "Counter-offer"
    return create_card_content(
        ReviewCardPayload(
            title=f"Buy the {listing.title} for {usd(counter_cents)}?",
            summary_rows=[
                ReviewSummaryRow(label="Asking price", value=usd(listing.asking_cents)),
                ReviewSummaryRow(label=price_label, value=usd(counter_cents)),
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


def profile_link(listing: Listing) -> str:
    return f"https://agentverse.ai/agents/details/{listing.address}/profile"


def agent_line(listing: Listing) -> str:
    if listing.host == AGENTVERSE:
        named = f" as @{listing.handle}" if listing.handle else ""
        return (
            f"Its agent is live on Agentverse{named}: [{listing.address}]({profile_link(listing)})"
        )
    return f"Its agent is live at {listing.address}"


def listed_text(listing: Listing, registered: bool, offline: bool) -> str:
    lines = [
        f"Your {listing.title} is for sale at {usd(listing.asking_cents)}.",
        agent_line(listing),
        f"It agrees to any offer of {usd(listing.floor_cents)} or more. Below that it counters, "
        "coming down a little each round without ever revealing your lowest price. Say "
        '"my listings" any time to check on it.',
    ]
    if listing.host == AGENTVERSE:
        return "\n".join(lines)
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
    lines.append(
        'Say "haggle" with your max (like "haggle $250") and my buyer agent will negotiate with '
        'all of them at once. Or reply with a number for details, or offer yourself: "offer $X on '
        '1".'
    )
    return "\n".join(lines)


def details_text(listing: Listing, mine: bool) -> str:
    lines = [f"{listing.title}: {usd(listing.asking_cents)}"]
    if listing.item is not None:
        lines.append(f"Condition: {listing.item.condition}")
        if listing.item.details:
            lines.append("Details: " + "; ".join(listing.item.details))
    lines.append(
        f"Its agent: [{listing.address}]({profile_link(listing)})"
        if listing.host == AGENTVERSE
        else f"Its agent: {listing.address}"
    )
    lines.append(
        "This is your own listing."
        if mine
        else 'Make an offer like "offer $X", say "haggle" with your max, or buy it now with '
        f'"offer {usd(listing.asking_cents)}".'
    )
    return "\n".join(lines)


def _round_text(offer_cents: int, reply_cents: int, agreed: bool) -> str:
    return f"you {usd(offer_cents)} → " + ("agreed" if agreed else usd(reply_cents))


STATUS_TEXT = {
    "agreed": "agreed",
    "counter": "their price is within your max",
    "over_max": "wouldn't come down to your max",
    "unavailable": "no longer for sale",
    "no_answer": "its agent didn't answer",
}


def haggle_text(result: HaggleResult) -> str:
    lines = [
        f"My buyer agent haggled with {len(result.outcomes)} seller agent(s) at once. Your max "
        f"of {usd(result.max_cents)} stayed private, and so did their lowest prices."
    ]
    for n, outcome in enumerate(result.outcomes, 1):
        rounds = ", ".join(
            _round_text(r.offer_cents, r.reply_cents, r.agreed) for r in outcome.rounds
        )
        if outcome.price_cents is not None:
            end = f"Yours for {usd(outcome.price_cents)}."
        else:
            end = STATUS_TEXT.get(outcome.status, outcome.status).capitalize()
            end += f" ({outcome.note})." if outcome.note else "."
        lines.append(
            f"{n}. {outcome.title} (asking {usd(outcome.asking_cents)}): "
            + (f"{rounds}. " if rounds else "")
            + end
        )
    best = next((o for o in result.outcomes if o.item_id == result.best_item_id), None)
    if best is not None and best.price_cents is not None:
        number = result.outcomes.index(best) + 1
        others = (
            ' or "accept N" for another'
            if sum(o.price_cents is not None for o in result.outcomes) > 1
            else ""
        )
        saved = best.asking_cents - best.price_cents
        lines.append(
            f"Best deal: #{number}, the {best.title}, for {usd(best.price_cents)}"
            + (f" ({usd(saved)} under asking)" if saved > 0 else "")
            + f'. Reply "accept" to buy it{others}. Prices hold for 24 hours.'
        )
    else:
        lines.append("None of them came down to your max. Try a higher max, or another search.")
    return "\n".join(lines)


def _outcome_node(n: int, outcome: HaggleOutcome, best: bool) -> GroupNode:
    children: list[Any] = [
        HeadingNode(type="heading", value=f"{n}. {outcome.title}"[:90], level=3),
        TextNode(type="text", value=f"Asking {usd(outcome.asking_cents)}", style="muted"),
    ]
    if outcome.rounds:
        children.append(
            ListNode(
                type="list",
                items=[
                    CardListItem(
                        children=[
                            TextNode(
                                type="text",
                                value=f"Round {i}: "
                                + _round_text(r.offer_cents, r.reply_cents, r.agreed),
                            )
                        ]
                    )
                    for i, r in enumerate(outcome.rounds, 1)
                ],
            )
        )
    if outcome.price_cents is not None:
        label = "Agreed" if outcome.status == "agreed" else "Within your max"
        children.append(
            BadgeNode(type="badge", label=f"{label}: {usd(outcome.price_cents)}", variant="success")
        )
        children.append(
            ButtonNode(
                type="button",
                label=f"Buy for {usd(outcome.price_cents)}",
                primary=best,
                action=ButtonAction(
                    selection={
                        "action": "accept",
                        "item_id": outcome.item_id,
                        "cents": outcome.price_cents,
                    }
                ),
            )
        )
    else:
        children.append(
            BadgeNode(
                type="badge",
                label=STATUS_TEXT.get(outcome.status, outcome.status),
                variant="warning",
            )
        )
    return GroupNode(type="group", direction="column", gap=4, children=children)


def haggle_card(result: HaggleResult) -> MetadataContent | None:
    if not result.outcomes:
        return None
    children: list[Any] = []
    for n, outcome in enumerate(result.outcomes, 1):
        if children:
            children.append(DividerNode(type="divider"))
        children.append(_outcome_node(n, outcome, outcome.item_id == result.best_item_id))
    return create_card_content(
        CustomCardPayload(
            root=SectionNode(
                type="section",
                title=f"Haggled with {len(result.outcomes)} sellers at once",
                subtitle=f"Your max ({usd(result.max_cents)}) stayed private.",
                children=children,
            )
        ),
        card_id=uuid4(),
    )


def payment_card(deal: DealRecord, title: str, provider_label: str) -> MetadataContent:
    return create_card_content(
        ReviewCardPayload(
            title=f"Pay {usd(deal.price_cents)} for the {title}",
            summary_rows=[
                ReviewSummaryRow(label="Order", value=deal.deal_id),
                ReviewSummaryRow(label="Pay with", value=provider_label),
                ReviewSummaryRow(label="Pay within", value="24 hours"),
            ],
            approve_cta=CtaAction(
                label="I've paid",
                selection={"action": "paid", "deal_id": deal.deal_id},
                primary=True,
            ),
        ),
        card_id=uuid4(),
    )


def deal_text(deal: DealRecord, title: str, provider_label: str | None) -> str:
    head = f"Deal! The {title} is yours for {usd(deal.price_cents)}. Order {deal.deal_id}."
    if deal.payment_status == PENDING and deal.payment_url:
        return (
            f"{head}\nPay here: [Pay {usd(deal.price_cents)}]({deal.payment_url}) "
            f'({provider_label}). The link is good for 24 hours. Once you\'ve paid, say "paid" '
            "and I'll confirm it."
        )
    if provider_label is None:
        return (
            f"{head}\nNo payment was taken: payments aren't set up on this Yardsale server, so "
            "this is an agreed price only."
        )
    return f'{head}\nI couldn\'t make the payment link just now. Say "pay" to try again.'


def payment_status_text(deal: DealRecord, title: str) -> str:
    if deal.payment_status == PAID:
        return (
            f"Payment received: {usd(deal.price_cents)} for the {title}. Order {deal.deal_id} "
            "is complete."
        )
    if deal.payment_status == PENDING:
        return (
            f"I don't see a payment for the {title} yet. Pay here: [Pay "
            f'{usd(deal.price_cents)}]({deal.payment_url}), then say "paid" again.'
        )
    if deal.payment_status in (EXPIRED, REJECTED):
        return f"The payment for the {title} wasn't made in time, so order {deal.deal_id} lapsed."
    return f"Order {deal.deal_id} for the {title}: no payment is needed on this server."


# --- Other sites -------------------------------------------------------------------------------

NOT_YARDSALE = (
    "These aren't Yardsale listings, so my agents can't haggle for them. Open a link to buy from "
    "the seller."
)
CRAIGSLIST_OFFERS = "Craigslist sellers usually take offers by email: reply to the post."
SEARCHING_ELSEWHERE = "I'll send the best deals I find here in a moment."
SITES_OFF = "Searching other sites is off on this Yardsale server."


def watch_offer(sites: str) -> str:
    hours = int(watches.WATCH_EVERY // 3600)
    return f'Say "watch for it" and I\'ll keep checking {sites} every {hours} hours for a week.'


def _under(budget_cents: int | None) -> str:
    return f" under {usd(budget_cents)}" if budget_cents else ""


def listing_line(n: int, listing: ExternalListing, why: str = "") -> str:
    """One listing on another site: its link, price, site, `why` it's a good deal when scored
    (deals.py; a watch's finds aren't), and where it is."""
    price = usd(listing.price_cents) if listing.price_cents is not None else "no price given"
    if listing.shipping_cents:
        price += f" + {usd(listing.shipping_cents)} shipping"
    notes = [listing.location] if listing.location else []
    if not why and listing.condition:
        notes.insert(0, listing.condition)
    if listing.accepts_offers:
        notes.append("takes offers")
    extra = f" ({'; '.join(notes)})" if notes else ""
    link = _link(listing.title, listing.url)
    about = f", {why}" if why else ""
    return f"{n}. {link}: {price} on {site_name(listing.source)}{about}{extra}"


def deal_line(n: int, deal: ScoredDeal) -> str:
    return listing_line(n, deal.listing, deal.why)


def market_note(found: Found) -> str:
    """What the deals were judged against: "typical sold price on eBay: $185, from 7 sales"."""
    if found.market is None:
        return "no recent eBay sales to compare prices with"
    sales = f"{found.market.sales} sale{'s' if found.market.sales != 1 else ''}"
    return f"typical sold price on eBay: {usd(found.market.typical_cents)}, from {sales}"


def elsewhere_text(found: Found, sites: str) -> str:
    about = f'"{found.query}"{_under(found.budget_cents)}'
    if not found.deals:
        lines = [f"I didn't find {about} on {sites} either."]
    else:
        lines = [f"Best deals on other sites for {about} ({market_note(found)}):"]
        lines += [deal_line(n, deal) for n, deal in enumerate(found.deals, 1)]
        lines.append(NOT_YARDSALE)
        if any(listing.source == "craigslist" for listing in found.listings):
            lines.append(CRAIGSLIST_OFFERS)
    if found.failed:
        names = " and ".join(site_name(name) for name in found.failed)
        lines.append(f"({names} couldn't be searched just now.)")
    lines.append(watch_offer(sites))
    return "\n".join(lines)


def elsewhere_card(found: Found) -> MetadataContent | None:
    """The deals as a carousel, with their photos. The links are in the text."""
    if not found.deals:
        return None
    items = []
    for deal in found.deals:
        listing = deal.listing
        price = usd(deal.total_cents)
        if listing.shipping_cents:
            price += " with shipping"
        pct = deal.versus_market_pct
        if pct is not None and pct >= deals.ABOUT_MARKET:
            price += f", {pct}% under typical"
        elif pct is not None and pct <= -deals.ABOUT_MARKET:
            price += f", {-pct}% above typical"
        items.append(
            CarouselItem(
                id=f"{listing.source}-{listing.external_id}"[:80],
                image=listing.image_url,
                title=listing.title[:80],
                subtitle=price,
                badges=[CarouselBadge(label=site_name(listing.source), variant="info")],
                secondary_text=listing.location or None,
                primary_cta=CtaAction(label="Watch for more", selection={"action": "watch"}),
            )
        )
    return create_card_content(
        CarouselCardPayload(
            title="On other sites",
            subtitle="Not Yardsale listings: open the links in the message to buy",
            items=items,
        ),
        card_id=uuid4(),
    )


def _form_price(value: SelectionValue | None) -> int | None:
    """A price typed into the rules form: a number of dollars, or empty."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, bool):
        raise PriceError("That isn't a price I can use.")
    return parse_usd(str(value))


# --- The protocol --------------------------------------------------------------------------------


def build_chat_protocol(backend: Backend, read: ReadMessage, fetch: FetchPhoto) -> AgentProtocol:
    proto = AgentProtocol(spec=chat_protocol_spec)
    store = backend.store
    locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
    answered: OrderedDict[str, Reply | None] = OrderedDict()  # message ID -> reply, for resends
    follow_ups: set[asyncio.Task[None]] = set()  # keeps background replies from being collected
    fields = {f.name for f in dataclasses.fields(Conversation)} - {"key"}
    finder = backend.finder
    searching: dict[str, str] = {}  # conversation -> what to say while other sites are searched

    def load(key: str) -> Conversation:
        saved = store.load_conversation(key)
        return Conversation(key=key, **{k: v for k, v in saved.items() if k in fields})

    def save(conv: Conversation) -> None:
        state = dataclasses.asdict(conv)
        del state["key"]
        state["seen_photos"] = conv.seen_photos[-MAX_REMEMBERED:]
        store.save_conversation(conv.key, state)

    def numbered_waiting(conv: Conversation) -> list[tuple[int, Listing | None]]:
        """Everything this seller has waiting, by number: proposals, and photos from a batch
        that need the seller's price (None). Proposals without a number get the next ones."""
        pending = store.owned(conv.owner, PROPOSED)
        known = set(conv.numbered)
        conv.numbered += [p.item_id for p in reversed(pending) if p.item_id not in known]
        by_id = {listing.item_id: listing for listing in pending}
        waiting: list[tuple[int, Listing | None]] = []
        for n, item_id in enumerate(conv.numbered, 1):
            if item_id in by_id:
                waiting.append((n, by_id[item_id]))
            elif str(n) in conv.needs_price:
                waiting.append((n, None))
        return waiting

    def waiting_line(conv: Conversation, n: int, listing: Listing | None) -> str:
        if listing is None:
            return f"{n}. {conv.needs_price[str(n)]['item']['title']}: needs your price"
        return (
            f"{n}. {listing.title}: asking {usd(listing.asking_cents)}, lowest "
            f"{usd(listing.floor_cents)}"
        )

    def waiting_list(
        conv: Conversation, waiting: list[tuple[int, Listing | None]], head: str
    ) -> str:
        conv.awaiting = "proposal"
        lines = [head, *(waiting_line(conv, n, listing) for n, listing in waiting)]
        return "\n".join([*lines, BATCH_PROMPT])

    def not_waiting(conv: Conversation, n: int) -> str:
        if not 1 <= n <= len(conv.numbered):
            return f"Pick a number from 1 to {len(conv.numbered)}."
        return f"#{n} isn't waiting for approval."

    def waiting_too(conv: Conversation) -> str:
        """A reminder of other proposals still waiting for this seller."""
        waiting = numbered_waiting(conv)
        if len(waiting) > 1:
            return "\n\n" + waiting_list(conv, waiting, "Still waiting for you:")
        if waiting and waiting[0][1] is None:
            conv.awaiting = "proposal"
            n = waiting[0][0]
            return f'\n\n#{n} still needs your price, like "ask {n} $40".'
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
        prepared: tuple[str, str] | None = None
        try:
            wanted = await read(text, True)
            asking, floor = to_cents(wanted.asking_usd), to_cents(wanted.floor_usd)
            data = await fetch(ctx, photo)
            image_b64, media_type = prepared = await asyncio.to_thread(prepare_photo, data)
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
            reply = proposal_reply(listing)
        except UnpricedError as ex:
            unpriced = ex
            reply = Reply(
                f"This looks like {ex.item.title}, but no recent eBay sales were close enough to "
                'suggest a price. Tell me your price, like "ask $40" or "at least $30", and I\'ll '
                'set up the listing for you to approve. Or say "cancel".' + unpriced_hint(ex.market)
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
        if backend.ebay is not None and prepared is not None:
            # Kept for listing on eBay, which needs the photo itself.
            photo_key = listing.item_id if listing is not None else f"pending:{owner}"
            if listing is not None or unpriced is not None:
                store.save_photo(photo_key, prepared[1], base64.b64decode(prepared[0]))
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
                    "market": unpriced.market.model_dump() if unpriced.market else None,
                }
            save(conv)
        return reply

    async def price_numbered(ctx: Context, key: str, n: int, photo: Photo) -> Reply:
        """Price photo #n of several. Never raises: a failure is a reply about that photo."""
        owner = f"asi1:{key}"
        listing: Listing | None = None
        unpriced: UnpricedError | None = None
        prepared: tuple[str, str] | None = None
        try:
            data = await fetch(ctx, photo)
            image_b64, media_type = prepared = await asyncio.to_thread(prepare_photo, data)
            listing = await backend.propose(
                ctx,
                owner,
                ListPhoto(
                    listing_ref=uuid4().hex,
                    seller_id=owner,
                    image_b64=image_b64,
                    media_type=media_type,
                ),
            )
            reply = Reply(numbered_proposal_text(listing, n), proposal_card(listing))
        except UnpricedError as ex:
            unpriced = ex
            reply = Reply(
                f"#{n}: This looks like {ex.item.title}, but no recent eBay sales were close "
                f'enough to suggest a price. Tell me yours, like "ask {n} $40", or say "cancel '
                f'{n}".'
            )
        except (PhotoError, PipelineError, PriceError) as ex:
            detail = f"{ex} ({ex.__cause__})" if ex.__cause__ else str(ex)
            if isinstance(ex, PriceError):
                detail = "prices didn't work"
            ctx.logger.warning(f"Chat photo #{n} for {key[:8]} failed: {detail}")
            reply = Reply(f"#{n}: I couldn't price this photo. {ex} Send it again to retry.")
        except Exception:
            ctx.logger.exception(f"Chat photo #{n} for {key[:8]} failed")
            reply = Reply(f"#{n}: {SOMETHING_BROKE}")
        if backend.ebay is not None and prepared is not None:
            # Kept for listing on eBay, which needs the photo itself.
            if listing is not None:
                store.save_photo(listing.item_id, prepared[1], base64.b64decode(prepared[0]))
            elif unpriced is not None:
                photo_key = pending_photo_key(owner, n)
                store.save_photo(photo_key, prepared[1], base64.b64decode(prepared[0]))
        async with locks[key]:
            conv = load(key)
            conv.pricing.pop(photo.url, None)
            conv.numbered += [""] * (n - len(conv.numbered))
            if listing is not None or unpriced is not None:
                conv.seen_photos.append(photo.url)  # a failed photo can be sent again
                conv.awaiting = "proposal"
            if listing is not None:
                # Another message may already have numbered it; it keeps its photo's number.
                conv.numbered = ["" if i == listing.item_id else i for i in conv.numbered]
                conv.numbered[n - 1] = listing.item_id
            if unpriced is not None:
                conv.needs_price[str(n)] = {
                    "item": unpriced.item.model_dump(),
                    "appraisal": unpriced.appraisal.model_dump(),
                    "market": unpriced.market.model_dump() if unpriced.market else None,
                }
            save(conv)
        return reply

    def batch_summary(key: str, numbers: list[int]) -> str:
        """Where each photo of a batch stands, once all of them are priced."""
        conv = load(key)
        lines = [f"All {len(numbers)} photos are done:"]
        waiting = False
        for n in numbers:
            item_id = conv.numbered[n - 1] if n <= len(conv.numbered) else ""
            listing = store.get(item_id) if item_id else None
            if listing is not None and listing.status == PROPOSED:
                waiting = True
                lines.append(
                    f"{n}. {listing.title}: asking {usd(listing.asking_cents)}, lowest "
                    f"{usd(listing.floor_cents)}"
                )
            elif listing is not None:
                state = "for sale" if listing.status == ACTIVE else listing.status
                lines.append(f"{n}. {listing.title}: {state}")
            elif str(n) in conv.needs_price:
                waiting = True
                title = conv.needs_price[str(n)]["item"]["title"]
                lines.append(f'{n}. {title}: needs your price, like "ask {n} $40"')
            else:
                lines.append(f"{n}. Couldn't be priced. Send that photo again to retry.")
        if waiting:
            lines.append(BATCH_PROMPT)
        return "\n".join(lines)

    async def price_batch(
        ctx: Context,
        key: str,
        numbered: list[tuple[int, Photo]],
        notice: str,
        notes: str,
        deadline: float | None,
        deliver: Deliver | None,
    ) -> Reply:
        """Price several photos, PHOTOS_AT_ONCE at a time. The reply carries the proposals ready
        by the deadline; the rest are delivered one by one as they're ready, then a summary."""
        limit = asyncio.Semaphore(PHOTOS_AT_ONCE)

        async def one(n: int, photo: Photo) -> Reply:
            async with limit:
                return await price_numbered(ctx, key, n, photo)

        tasks = [asyncio.ensure_future(one(n, photo)) for n, photo in numbered]
        numbers = [n for n, _ in numbered]
        timeout = None
        if deliver is not None and deadline is not None:
            timeout = max(0.0, deadline - asyncio.get_running_loop().time())
        done, rest = await asyncio.wait(tasks, timeout=timeout)
        ready = tuple((task.result().text, task.result().card) for task in tasks if task in done)
        count = len(numbered)
        if not rest:
            head = f"Here are suggested listings for your {count} photos, numbered in the order "
            head += "you sent them."
            summary = ((batch_summary(key, numbers), None),)
            return Reply(notice + head + notes, None, ready + summary)
        assert deliver is not None
        if ready:
            head = (
                f"Got your {count} photos. Here's what's priced so far ({len(ready)} of {count}); "
                "I'll send the rest here as each one is ready."
            )
        else:
            head = (
                f"Got your {count} photos. I'm identifying each one and checking recent eBay "
                f"sales, {PHOTOS_AT_ONCE} at a time, which can take a few minutes. Each suggested "
                "listing comes here as it's ready, numbered in the order you sent them."
            )

        async def send_rest() -> None:
            try:
                for next_ready in asyncio.as_completed(rest):
                    await deliver(await next_ready)
                await deliver(Reply(batch_summary(key, numbers)))
            except Exception:
                ctx.logger.exception(f"Sending priced photos in {key[:8]} failed")

        follow_up = asyncio.ensure_future(send_rest())
        follow_ups.add(follow_up)
        follow_up.add_done_callback(follow_ups.discard)
        return Reply(notice + head + notes, None, ready)

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
                saved_market(conv.unpriced),
            )
            store.move_photo(f"pending:{conv.owner}", listing.item_id)
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

    async def go_live(
        ctx: Context, conv: Conversation, proposal: Listing
    ) -> tuple[Listing, bool, str] | str:
        """Start a proposal's item agent and its eBay copy: (listing, registered, eBay line), or
        why it couldn't start."""
        try:
            listing, registered = await backend.activate(ctx, proposal)
        except PipelineError as ex:
            return f"I couldn't start the item agent. {ex}"
        on_ebay = ""
        if backend.ebay is not None and backend.ebay.connected(conv.owner):
            on_ebay = "\n" + await list_on_ebay(conv, listing)
        return listing, registered, on_ebay

    async def approve(ctx: Context, conv: Conversation, proposal: Listing) -> str:
        live = await go_live(ctx, conv, proposal)
        if isinstance(live, str):
            return live
        listing, registered, on_ebay = live
        conv.awaiting = None
        return listed_text(listing, registered, backend.offline) + on_ebay + waiting_too(conv)

    # --- Several proposals, by number ------------------------------------------------------

    async def approve_numbers(
        ctx: Context, conv: Conversation, numbers: list[int], waiting: Waiting
    ) -> str:
        entries = dict(waiting)
        lines: list[str] = []
        listed = unconfirmed = in_server = False
        for n in numbers:
            if n not in entries:
                lines.append(not_waiting(conv, n))
                continue
            proposal = entries[n]
            if proposal is None:
                lines.append(f'#{n} needs your price first, like "ask {n} $40".')
                continue
            live = await go_live(ctx, conv, proposal)
            if isinstance(live, str):
                lines.append(f"#{n}: {live}")
                continue
            listing, registered, on_ebay = live
            listed = True
            in_server |= listing.host != AGENTVERSE
            unconfirmed |= listing.host != AGENTVERSE and not registered
            lines.append(
                f"#{n} {listing.title}: for sale at {usd(listing.asking_cents)}. "
                f"{agent_line(listing)}{on_ebay}"
            )
        if listed:
            conv.awaiting = None
            lines.append(
                "Each agent agrees to any offer at or above your lowest price, and below it "
                'counters without ever revealing it. Say "my listings" any time to check on them.'
            )
            if backend.offline and in_server:
                lines.append(
                    "(Offline mode: these addresses were made on this server and aren't on the "
                    "public Almanac.)"
                )
            elif unconfirmed:
                lines.append(
                    "(The Almanac hasn't confirmed some yet. The server retries every hour.)"
                )
        return "\n".join(lines) + waiting_too(conv)

    def cancel_numbers(conv: Conversation, numbers: list[int], waiting: Waiting) -> str:
        entries = dict(waiting)
        lines: list[str] = []
        for n in numbers:
            if n not in entries:
                lines.append(not_waiting(conv, n))
                continue
            proposal = entries[n]
            if proposal is None:
                title = conv.needs_price.pop(str(n))["item"]["title"]
            elif backend.cancel(proposal.item_id, conv.owner) is None:
                lines.append(not_waiting(conv, n))
                continue
            else:
                title = proposal.title
            lines.append(f"Canceled #{n}, the {title}. Nothing was listed.")
        conv.awaiting = None
        return "\n".join(lines) + waiting_too(conv)

    async def edit_number(
        ctx: Context, conv: Conversation, n: int, command: commands.Command, waiting: Waiting
    ) -> str | Reply:
        entries = dict(waiting)
        if n not in entries:
            return not_waiting(conv, n)
        proposal = entries[n]
        if proposal is None:
            saved = conv.needs_price[str(n)]
            try:
                listing = backend.save_proposal(
                    conv.owner,
                    conv.owner,
                    uuid4().hex,
                    ItemDetails.model_validate(saved["item"]),
                    Appraisal.model_validate(saved["appraisal"]),
                    command.asking_cents,
                    command.floor_cents,
                    saved_market(saved),
                )
            except UnpricedError:
                return f'I need an asking price for #{n} too, like "ask {n} $40".'
            store.move_photo(pending_photo_key(conv.owner, n), listing.item_id)
            del conv.needs_price[str(n)]
            conv.numbered[n - 1] = listing.item_id
        else:
            changed = store.reprice(
                proposal.item_id, conv.owner, command.asking_cents, command.floor_cents
            )
            if changed is None:
                return not_waiting(conv, n)
            listing = changed
        conv.awaiting = "proposal"
        if command.approve:
            return await approve(ctx, conv, listing)
        return Reply(numbered_proposal_text(listing, n), proposal_card(listing))

    def rules_number(conv: Conversation, n: int, waiting: Waiting) -> str | Reply:
        entries = dict(waiting)
        if n not in entries:
            return not_waiting(conv, n)
        proposal = entries[n]
        if proposal is None:
            return f'Tell me #{n}\'s price first, like "ask {n} $40".'
        return Reply(
            f'Set your prices for #{n}, the {proposal.title}, below, or type them, like "ask {n} '
            f'{usd(proposal.asking_cents)}, lowest {n} {usd(proposal.floor_cents)}".',
            rules_card(proposal),
        )

    async def numbered_command(
        ctx: Context, conv: Conversation, command: commands.Command, waiting: Waiting
    ) -> str | Reply | None:
        """Approve, cancel, or price proposals by number, or ask which one when several are
        waiting. None when the usual one-proposal handling applies."""
        kind = command.kind
        if kind not in ("approve", "cancel", "edit", "rules", "accept"):
            return None
        if conv.counter is not None and (kind == "accept" or conv.awaiting == "counter"):
            return None  # a buyer answering a counter-offer
        if kind == "accept" and command.pick is not None:
            return None
        if not waiting and not conv.numbered:
            return None
        numbers = list(command.picks)
        if command.pick is not None and kind in ("edit", "rules"):
            numbers = [command.pick]
        if command.every:
            if not waiting:
                return None
            numbers = [n for n, _ in waiting]
        if not numbers:
            if conv.unpriced is not None or not waiting:
                return None
            if len(waiting) == 1:
                if waiting[0][1] is not None:
                    return None  # the one proposal, as always
                numbers = [waiting[0][0]]
            else:
                example = waiting[-1][0]
                head = {
                    "edit": f'Which one? Put its number with the price, like "lowest {example} '
                    '$40":',
                    "rules": f'Which one? Say "rules {example}":',
                    "cancel": f'Which one? Say "cancel {example}", or "cancel all":',
                }.get(kind, f'Which one? Say "approve {example}", or "approve all":')
                return waiting_list(conv, waiting, head)
        if kind == "edit":
            return await edit_number(ctx, conv, numbers[0], command, waiting)
        if kind == "rules":
            return rules_number(conv, numbers[0], waiting)
        if kind == "cancel":
            return cancel_numbers(conv, numbers, waiting)
        return await approve_numbers(ctx, conv, numbers, waiting)

    async def list_on_ebay(conv: Conversation, listing: Listing) -> str:
        """List one live listing on the seller's eBay too. Never raises: eBay failing doesn't
        undo anything on Yardsale."""
        assert backend.ebay is not None
        try:
            record = await asyncio.wait_for(
                backend.ebay.list_item(listing, conv.ebay_zip), EBAY_LIST_WITHIN
            )
        except EbayError as ex:
            return (
                f"I couldn't list the {listing.title} on eBay: {ex} It's still for sale on "
                'Yardsale; say "list on ebay" to try again.'
            )
        except TimeoutError:
            return (
                f"eBay didn't answer in time, so the {listing.title} isn't on eBay yet. It's "
                'still for sale on Yardsale; say "list on ebay" to try again.'
            )
        return (
            f"Also on eBay: {record.url} . My agent answers its Best Offers with the same rules, "
            "and eBay never sees your lowest price."
        )

    async def ebay_command(conv: Conversation, command: commands.Command) -> str:
        ebay = backend.ebay
        if command.zip_code:
            conv.ebay_zip = command.zip_code
        if ebay is None:
            return EBAY_OFF
        if command.kind == "ebay_disconnect":
            if not ebay.connected(conv.owner):
                return "Your eBay account isn't connected."
            ended, failed = await ebay.disconnect(conv.owner)
            text = "Disconnected your eBay account; Yardsale deleted its access."
            if ended:
                text += f" I ended {ended} eBay listing(s) first."
            if failed:
                text += f" {failed} eBay listing(s) couldn't be ended; end them on eBay yourself."
            return text
        if command.kind == "ebay_connect":
            if ebay.connected(conv.owner):
                return 'Your eBay account is already connected. Say "disconnect ebay" to remove it.'
            link = ebay.start_connect(conv.owner)
            zip_line = (
                f" I'll use ZIP code {conv.ebay_zip} for pickup."
                if conv.ebay_zip
                else ' Also tell me your ZIP code for eBay listings, like "ebay zip 94110".'
            )
            return (
                f"[Connect your eBay account]({link}). You sign in on eBay's own page; Yardsale "
                "never sees your password. The link works for 15 minutes. Then come back here, "
                "and listings you approve also go up on eBay." + zip_line
            )
        if command.kind == "ebay_zip":
            if ebay.connected(conv.owner):
                return f"Got it: eBay listings use ZIP code {conv.ebay_zip}."
            return f'Got it: ZIP code {conv.ebay_zip}. Say "connect ebay" to connect your account.'
        if command.kind == "ebay_end":
            on_ebay = [
                listing
                for listing in store.owned(conv.owner, ACTIVE)
                if (record := store.ebay_listing(listing.item_id)) is not None
                and record.status == "active"
            ]
            if command.pick is not None:
                active = store.owned(conv.owner, ACTIVE)
                if not 1 <= command.pick <= len(active):
                    return (
                        f"Pick a number from 1 to {len(active)}." if active else "Nothing's live."
                    )
                on_ebay = [active[command.pick - 1]] if active[command.pick - 1] in on_ebay else []
            elif len(on_ebay) > 1:
                return 'Which one? Say "take 2 off ebay", using the numbers from "my listings".'
            if not on_ebay:
                return "That isn't on eBay."
            try:
                await ebay.end_listing(on_ebay[0].item_id)
            except EbayError as ex:
                return f"eBay wouldn't end the listing: {ex}. End it on eBay yourself."
            return (
                f"Took the {on_ebay[0].title} off eBay. It's still for sale on Yardsale; "
                'say "unlist" to take it down everywhere.'
            )
        # ebay_list: put live listings that aren't on eBay yet on it
        if not ebay.connected(conv.owner):
            return 'Connect your eBay account first: say "connect ebay".'
        active = store.owned(conv.owner, ACTIVE)
        if command.pick is not None:
            if not 1 <= command.pick <= len(active):
                return f"Pick a number from 1 to {len(active)}." if active else "Nothing's live."
            active = [active[command.pick - 1]]
        waiting = [
            listing
            for listing in active
            if (record := store.ebay_listing(listing.item_id)) is None or record.status != "active"
        ]
        if not waiting:
            return (
                "Everything you have for sale is already on eBay."
                if active
                else "You have nothing for sale right now. Attach a photo to sell something."
            )
        return "\n".join([await list_on_ebay(conv, listing) for listing in waiting])

    def cancel_proposal(conv: Conversation, proposal: Listing) -> str:
        conv.awaiting = None
        if backend.cancel(proposal.item_id, conv.owner) is None:
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
                f"{usd(listing.floor_cents)}. "
                + (
                    f"Agent on Agentverse"
                    f"{f' (@{listing.handle})' if listing.handle else ''}: "
                    f"{profile_link(listing)}"
                    if listing.host == AGENTVERSE
                    else f"Agent: {listing.address}"
                )
                + ebay_line(listing)
                for n, listing in enumerate(active, 1)
            ]
        waiting = numbered_waiting(conv)
        numbers = {item.item_id: n for n, item in waiting if item is not None}
        for listing in listings:
            if listing.status == PROPOSED:
                number = f" (#{numbers[listing.item_id]})" if len(waiting) > 1 else ""
                lines.append(
                    f"Waiting for your approval{number}: {listing.title} at "
                    f"{usd(listing.asking_cents)}"
                )
            elif listing.status == SOLD:
                lines.append(sold_line(listing, store.deal_for(listing.item_id)))
        lines += craigslist_mail.seller_lines(store, listings)
        if active:
            lines.append(
                'Say "unlist 1" to take one down, "craigslist 1" to post it on Craigslist'
                + (', or "list 1 on ebay".' if backend.ebay is not None else ".")
            )
        return "\n".join(lines)

    def ebay_line(listing: Listing) -> str:
        record = store.ebay_listing(listing.item_id)
        if record is None or record.status != "active":
            return ""
        return f". On eBay: {record.url}"

    def sold_line(listing: Listing, deal: DealRecord | None) -> str:
        if deal is None:
            return f"Sold: {listing.title}"
        if deal.buyer.startswith("ebay:"):
            return (
                f"Sold on eBay: {listing.title} for {usd(deal.price_cents)}. The buyer pays "
                "through eBay."
            )
        line = f"Sold: {listing.title} for {usd(deal.price_cents)}"
        if deal.payment_status == PAID and deal.payout_cents is not None:
            return (
                f"{line}, paid. You get {usd(deal.payout_cents)} after Yardsale's "
                f"{commission_text()}% fee."
            )
        if deal.payment_status == PENDING:
            return f"{line}, waiting for the buyer to pay."
        if deal.payment_status in (EXPIRED, REJECTED):
            return f"{line}, but the buyer didn't pay."
        return f"{line} (payment not collected)"

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
        if backend.cancel(listing.item_id, conv.owner) is None:
            return f"The {listing.title} isn't for sale anymore."
        gone = f"Your {listing.title} is no longer for sale."
        if listing.host == AGENTVERSE:
            return f"{gone} Its agent on Agentverse is being deleted."
        return f"{gone} Its agent turns down new offers."

    # --- Buying ----------------------------------------------------------------------------

    def find(conv: Conversation, query: str, budget_cents: int | None) -> str | Reply:
        found = search(backend.available(), query, budget_cents)
        conv.budget = budget_cents or conv.budget
        conv.results = [listing.item_id for listing in found]
        conv.selected = found[0].item_id if len(found) == 1 else None
        conv.last_query = search_words(query) or conv.last_query
        if not found and finder.enabled and search_words(query):
            return look_elsewhere(conv, query, budget_cents, "Nothing on Yardsale matches")
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

    def provider_label() -> str | None:
        provider = backend.checkout.provider
        return provider.label if provider is not None else None

    async def settle(conv: Conversation, listing: Listing, deal: Deal) -> str | Reply:
        """A deal closed: make the payment link and show it."""
        conv.quotes.pop(listing.item_id, None)
        record = await backend.checkout.start(deal.deal_id) or store.deal(deal.deal_id)
        if record is None:
            return f"Deal! The {listing.title} is yours for {usd(deal.price_cents)}."
        label = provider_label()
        text = deal_text(record, listing.title, label)
        if record.payment_status == PENDING and label is not None:
            return Reply(text, payment_card(record, listing.title, label))
        return text

    async def answer(
        conv: Conversation, listing: Listing, reply: Counter | Deal | OfferRejected
    ) -> str | Reply:
        if isinstance(reply, Counter):
            conv.counter = {"item_id": listing.item_id, "cents": reply.counter_cents}
            conv.quotes[listing.item_id] = reply.counter_cents
            conv.awaiting = "counter"
            if reply.agreed:
                return Reply(
                    f"The seller's agent agreed to {usd(reply.counter_cents)} for the "
                    f'{listing.title}. Reply "accept" to buy it at that price. The agreement is '
                    "good for 24 hours.",
                    counter_card(listing, reply.counter_cents, agreed=True),
                )
            return Reply(
                f"The seller's agent countered with {usd(reply.counter_cents)} for the "
                f'{listing.title}. Reply "accept" to buy it at {usd(reply.counter_cents)}, or '
                "make another offer. The counter-offer is good for 24 hours.",
                counter_card(listing, reply.counter_cents),
            )
        conv.counter = None
        conv.awaiting = None
        if isinstance(reply, Deal):
            return await settle(conv, listing, reply)
        conv.quotes.pop(listing.item_id, None)
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
        return await answer(conv, listing, reply)

    async def accept(ctx: Context, conv: Conversation, item_id: str, cents: int) -> str | Reply:
        listing = store.get(item_id)
        if listing is None:
            conv.counter = None
            conv.quotes.pop(item_id, None)
            return "That counter-offer isn't open anymore."
        reply = await backend.negotiate(
            ctx, item_id, AcceptCounter(item_id=item_id, counter_cents=cents, buyer_ref=conv.key)
        )
        return await answer(conv, listing, reply)

    async def accept_typed(ctx: Context, conv: Conversation, pick: int | None) -> str | Reply:
        """ "accept": the open counter or best haggled price. "accept 2": item 2's price."""
        if pick is not None:
            if not 1 <= pick <= len(conv.results):
                return (
                    f"Pick a number from 1 to {len(conv.results)}."
                    if conv.results
                    else ('There\'s nothing to accept yet. Make an offer, or say "haggle".')
                )
            item_id = conv.results[pick - 1]
            if item_id not in conv.quotes:
                return f"Item {pick} has no price on offer to you. Make an offer first."
            return await accept(ctx, conv, item_id, conv.quotes[item_id])
        assert conv.counter is not None
        return await accept(ctx, conv, str(conv.counter["item_id"]), int(conv.counter["cents"]))

    # --- Haggling for the buyer -------------------------------------------------------------

    def haggle_targets(conv: Conversation, query: str, max_cents: int) -> list[Listing]:
        reach = max_cents * LOWEST_SHARE_REACH[0] // LOWEST_SHARE_REACH[1]
        if query:
            found = search(backend.available(), query, reach)
        else:
            found = [
                listing
                for item_id in conv.results
                if (listing := store.get(item_id)) is not None and listing.status == ACTIVE
            ]
        return [
            listing
            for listing in found
            if listing.owner != conv.owner and listing.asking_cents <= reach
        ]

    async def haggle(
        ctx: Context, conv: Conversation, max_cents: int | None, query: str
    ) -> str | Reply:
        if max_cents is None:
            max_cents = conv.budget
        if query:
            found = [listing.item_id for listing in search(backend.available(), query)]
            if found or not conv.results:
                conv.results = found
            else:
                query = ""  # the words matched nothing new: haggle over the last results

        if max_cents is None:
            if not conv.results:
                return (
                    'What are you looking for? Like "find me a road bike, haggle for me, max $300".'
                )
            conv.awaiting = "max"
            return ASK_MAX
        conv.budget = max_cents
        conv.last_query = search_words(query) or conv.last_query
        targets = haggle_targets(conv, query, max_cents)
        if not targets and finder.enabled and search_words(query):
            return look_elsewhere(
                conv, query, max_cents, "Nothing on Yardsale is close enough to haggle over"
            )
        if not targets:
            about = f' for "{query}"' if query else ""
            return (
                f"Nothing for sale{about} is close enough to {usd(max_cents)} to haggle over. "
                "Try a higher max, or other words."
            )
        result = await backend.haggle(ctx, conv.key, max_cents, targets)
        conv.results = [outcome.item_id for outcome in result.outcomes]
        conv.quotes = {
            o.item_id: o.price_cents for o in result.outcomes if o.price_cents is not None
        }
        conv.selected = result.best_item_id or None
        conv.counter = None
        conv.awaiting = None
        best = conv.quotes.get(result.best_item_id)
        if best is not None:
            conv.counter = {"item_id": result.best_item_id, "cents": best}
            conv.awaiting = "counter"
        return Reply(haggle_text(result), haggle_card(result))

    # --- Other sites ------------------------------------------------------------------------

    def look_elsewhere(conv: Conversation, query: str, budget: int | None, lead: str) -> Reply:
        """Search other sites after this reply's text, outside the lock (handle_safely)."""
        words = search_words(query)
        conv.last_query = words
        conv.budget = budget or conv.budget
        text = f'{lead} "{words}"{_under(budget)} right now, so I checked {finder.site_names()}.'
        return Reply(text, elsewhere=(words, budget))

    def asked_for(conv: Conversation, command: commands.Command) -> tuple[str, int | None]:
        """The search and budget a "search other sites" or "watch" message is about: its own
        words, or the buyer's last search when it has none ("watch for it")."""
        words = search_words(command.text)
        if words:
            return words, command.amount_cents
        return conv.last_query, command.amount_cents or conv.budget

    def search_other_sites(conv: Conversation, command: commands.Command) -> str | Reply:
        if not finder.enabled:
            return SITES_OFF
        query, budget = asked_for(conv, command)
        if not query:
            return 'What should I look for? Like "search other sites for a road bike under $300".'
        conv.last_query = query
        conv.budget = budget or conv.budget
        return Reply(
            f'Checking {finder.site_names()} for "{query}"{_under(budget)}.',
            elsewhere=(query, budget),
        )

    async def with_deals_elsewhere(key: str, reply: Reply) -> Reply:
        """The reply with the best deals on other sites added. While the sites are searched,
        on_chat can say so if the answer is slow."""
        assert reply.elsewhere is not None
        query, budget = reply.elsewhere
        searching[key] = f"{reply.text}\n\n{SEARCHING_ELSEWHERE}"
        try:
            found = await finder.find(query, budget)
        except SourceError as ex:
            return reply._replace(text=f"{reply.text}\n\nI couldn't search other sites: {ex}.")
        finally:
            searching.pop(key, None)
        text = f"{reply.text}\n\n{elsewhere_text(found, finder.site_names())}"
        return Reply(text, elsewhere_card(found) or reply.card, reply.more)

    def watch(conv: Conversation, command: commands.Command | None) -> str:
        """A standing search over other sites (watches.py), from the message or the last search."""
        if not finder.enabled:
            return SITES_OFF
        if command is None:  # the card's "Watch for more"
            query, budget = conv.last_query, conv.budget
        else:
            query, budget = asked_for(conv, command)
        try:
            started = watches.start(
                store, watches.new_watch(conv.owner, query, store.clock(), budget_cents=budget)
            )
        except watches.WatchError as ex:
            return str(ex)
        n = len(store.watches_for(conv.owner))
        hours = int(started.every_seconds // 3600)
        return (
            f'OK. I\'ll check {finder.site_names()} for "{started.query}"{_under(budget)} every '
            f"{hours} hours for a week, and tell you about anything new next time you message me. "
            f'Say "my watches" to see what turned up, or "stop watching {n}".'
        )

    def my_watches(conv: Conversation) -> str:
        """The buyer's watches, and anything new they found, which then counts as told."""
        running = store.watches_for(conv.owner)
        lines = ["You're watching:"] if running else ["You aren't watching anything now."]
        for n, standing in enumerate(running, 1):
            days = max(1, int(-(-(standing.expires_at - store.clock()) // 86400)))
            lines.append(
                f'{n}. "{standing.query}"{_under(standing.budget_cents)}, for {days} more day(s)'
            )
        # Findings of watches that have since ended are shown too, once.
        news: list[str] = []
        for standing in store.watches_for(conv.owner, running_only=False):
            # Unpriced posts stay in (watches.affordable keeps them), after the priced ones.
            hits = store.untold_hits(standing.watch_id)
            fresh = sorted(hits, key=lambda x: (x.total_cents is None, x.total_cents or 0))[:10]
            if fresh:
                news.append(f'New for "{standing.query}":')
                news += [listing_line(i, listing) for i, listing in enumerate(fresh, 1)]
            store.mark_told(standing.watch_id)
        if news:
            lines += ["", *news, NOT_YARDSALE]
        elif running:
            lines.append("Nothing new has turned up yet.")
        if running:
            lines.append('Say "stop watching 1" to stop one.')
        else:
            lines.append(
                'Say something like "watch for a road bike under $300" and I\'ll keep checking '
                "other sites for a week."
            )
        return "\n".join(lines)

    def unwatch(conv: Conversation, pick: int | None) -> str:
        running = store.watches_for(conv.owner)
        if not running:
            return "You aren't watching anything."
        if pick is None and len(running) == 1:
            pick = 1
        if pick is None:
            return my_watches(conv) + '\nWhich one? Like "stop watching 2".'
        if not 1 <= pick <= len(running):
            return f"Pick a number from 1 to {len(running)}."
        stopped = store.stop_watch(running[pick - 1].watch_id, conv.owner)
        if stopped is None:
            return "That one had already stopped."
        store.mark_told(stopped.watch_id)  # so "found N new" stops mentioning it
        return f'OK, I stopped watching for "{stopped.query}".'

    def untold_note(conv: Conversation) -> str:
        count = store.count_untold(conv.owner)
        if not count:
            return ""
        return (
            f'\n\n(Your watches found {count} new listing(s) on other sites. Say "my watches" to '
            "see them.)"
        )

    # --- Paying -----------------------------------------------------------------------------

    async def check_payments(conv: Conversation) -> str | Reply:
        deals = store.deals(buyer=f"chat:{conv.key}")
        if not deals:
            return "You haven't bought anything yet."
        deal = next((d for d in deals if d.payment_status in (NOT_COLLECTED, PENDING)), deals[0])
        listing = store.get(deal.item_id)
        title = listing.title if listing else "item"
        if deal.payment_status == NOT_COLLECTED and backend.checkout.enabled:
            started = await backend.checkout.start(deal.deal_id) or deal
            label = provider_label()
            text = deal_text(started, title, label)
            if started.payment_status == PENDING and label is not None:
                return Reply(text, payment_card(started, title, label))
            return text
        if deal.payment_status == PENDING:
            try:
                deal = await backend.checkout.refresh(deal.deal_id) or deal
            except PaymentError as ex:
                return f"I couldn't check the payment just now ({ex}). Try again in a minute."
        return payment_status_text(deal, title)

    def my_orders(conv: Conversation) -> str:
        deals = store.deals(buyer=f"chat:{conv.key}")
        by_email = craigslist_buyer.buyer_lines(store, conv.owner)
        if not deals:
            return "\n".join(by_email) if by_email else "You haven't bought anything yet."
        lines = ["Your orders:"]
        for deal in deals:
            listing = store.get(deal.item_id)
            title = listing.title if listing else deal.item_id
            status = {
                PAID: "paid",
                PENDING: "waiting for payment",
                NOT_COLLECTED: "agreed, no payment taken",
            }.get(deal.payment_status, "lapsed")
            lines.append(f"- {title}: {usd(deal.price_cents)}, {status} ({deal.deal_id})")
        return "\n".join(lines + by_email)

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
            cents = choice.get("cents")
            counter = conv.counter
            open_price = conv.quotes.get(item_id)
            if counter is not None and counter["item_id"] == item_id:
                open_price = int(counter["cents"])
            if open_price is None or open_price != cents:
                return "That price isn't open anymore. Make a new offer, or haggle again."
            return await accept(ctx, conv, item_id, open_price)
        if action == "paid":
            return await check_payments(conv)
        if action == "watch":
            return watch(conv, None)
        if action == "comp":
            return comp_details(conv, item_id, choice.get("n"))
        if action == "rules":
            listing = store.get(item_id)
            if listing is None or listing.owner != conv.owner or listing.status != PROPOSED:
                return "That listing isn't waiting for approval anymore."
            try:
                asking = _form_price(choice.get("asking"))
                floor = _form_price(choice.get("lowest"))
            except PriceError as ex:
                return str(ex)
            if asking is None and floor is None:
                return 'Type your prices instead, like "ask $360, lowest $300".'
            return await change_prices(ctx, conv, listing, asking, floor, False)
        if action == "decline":
            conv.counter = None
            conv.awaiting = None
            return "OK. Make another offer any time."
        if action == "pick" and item_id in conv.results:
            return pick(conv, conv.results.index(item_id) + 1)
        if action == "pick":
            return "That list is out of date. Search again to see what's for sale."
        return HELP

    def comp_details(conv: Conversation, item_id: str, n: SelectionValue | None) -> str:
        listing = store.get(item_id)
        if listing is None or listing.owner != conv.owner or listing.appraisal is None:
            return "Those sales aren't available anymore."
        matches = listing.appraisal.matches
        if not isinstance(n, int | float) or not 0 <= int(n) < len(matches):
            return "That sale isn't in the list anymore."
        match = matches[int(n)]
        kind = "the same product" if match.same_product else "a similar product"
        return (
            f"{_link(match.comp.title, match.comp.url)} sold for {usd(match.comp.price_cents)} on "
            f"{match.comp.sold_at} ({match.comp.condition}). Claude judged it {kind}: "
            f"{match.reason or 'no reason given'}."
        )

    async def handle_text(ctx: Context, conv: Conversation, text: str) -> str | Reply:
        # A pasted Craigslist reply address: email that seller and haggle for this buyer.
        negotiating = craigslist_buyer.command(text)
        if negotiating is not None:
            return craigslist_buyer.chat_reply(store, conv.owner, negotiating)
        if craigslist_buyer.asks_for_negotiations(text):
            return craigslist_buyer.negotiations_reply(store, conv.owner)
        posting = craigslist_mail.post_command(text)
        if posting is not None:
            answer = craigslist_mail.post_reply(store, conv.owner, posting)
            if answer is not None:
                return answer
        command = commands.parse(text)
        if command is not None and command.error:
            return command.error
        kind = command.kind if command else None
        pending = store.owned(conv.owner, PROPOSED)
        proposal = pending[0] if pending else None
        waiting = numbered_waiting(conv)
        selling = proposal is not None or conv.unpriced is not None or bool(waiting)

        if command is not None and kind in (
            "ebay_connect",
            "ebay_disconnect",
            "ebay_zip",
            "ebay_list",
            "ebay_end",
        ):
            return await ebay_command(conv, command)
        if kind == "craigslist_end":
            return CRAIGSLIST_END
        if command is not None:
            by_number = await numbered_command(ctx, conv, command, waiting)
            if by_number is not None:
                return by_number
        if command is not None and kind == "elsewhere":
            return search_other_sites(conv, command)
        if command is not None and kind == "watch":
            return watch(conv, command)
        if kind == "watches":
            return my_watches(conv)
        if command is not None and kind == "unwatch":
            return unwatch(conv, command.pick)
        if command is not None and kind == "edit" and selling:
            return await change_prices(
                ctx, conv, proposal, command.asking_cents, command.floor_cents, command.approve
            )
        if command is not None and kind == "offer" and command.amount_cents is not None:
            if selling and not conv.results:
                return f'Did you mean "ask {usd(command.amount_cents)}" for your listing?'
            return await make_offer(ctx, conv, command.amount_cents, command.pick)
        if command is not None and kind == "haggle" and not (selling and not conv.results):
            wanted = await read(command.text or text, False)
            limit = command.amount_cents or to_cents(wanted.budget_usd)
            return await haggle(ctx, conv, limit, wanted.query)
        if command is not None and kind == "accept" and command.pick is not None:
            return await accept_typed(ctx, conv, command.pick)
        if kind == "accept" or (kind == "approve" and conv.awaiting == "counter"):
            if conv.counter is not None:
                return await accept_typed(ctx, conv, None)
            if proposal is not None:
                return await approve(ctx, conv, proposal)
            return "There's no counter-offer to accept. Make an offer first."
        if kind == "approve":
            if proposal is not None:
                return await approve(ctx, conv, proposal)
            if conv.unpriced is not None:
                return 'Tell me your price first, like "ask $40".'
            if conv.counter is not None:
                return await accept_typed(ctx, conv, None)
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
        if kind == "orders":
            return my_orders(conv)
        if kind == "paid":
            return await check_payments(conv)
        if kind == "rules":
            if proposal is None:
                return "There's no listing waiting for your prices. Attach a photo to sell."
            return Reply(
                f'Set your prices for the {proposal.title} below, or type them, like "ask '
                f'{usd(proposal.asking_cents)}, lowest {usd(proposal.floor_cents)}".',
                rules_card(proposal),
            )
        if command is not None and kind == "select" and conv.results and command.pick:
            return pick(conv, command.pick)
        if kind == "help":
            return HELP
        if command is not None and kind == "amount" and command.amount_cents is not None:
            amount = usd(command.amount_cents)
            if conv.awaiting == "max" and conv.results:
                return await haggle(ctx, conv, command.amount_cents, "")
            if selling:
                return f'Did you mean "ask {amount}" or "lowest {amount}"?'
            if conv.results:
                return f'Did you mean "offer {amount}"?'

        # ASI:One's planner relays "accept" as prose. Take it only while a price is waiting, and
        # only at that saved price: the words never set it.
        if (
            command is None
            and conv.counter is not None
            and conv.awaiting == "counter"
            and commands.accepts_deal(text)
        ):
            return await accept_typed(ctx, conv, None)

        # Not a command, or not one that fits this conversation: ASI:One reads it.
        wanted = await read(text, False)
        if wanted.intent == "buy" and wanted.haggle:
            return await haggle(ctx, conv, to_cents(wanted.budget_usd), wanted.query)
        if wanted.intent == "buy":
            return find(conv, wanted.query, to_cents(wanted.budget_usd))
        if wanted.intent == "sell":
            asking, floor = to_cents(wanted.asking_usd), to_cents(wanted.floor_usd)
            if selling and (asking or floor):
                edit = commands.Command("edit", asking_cents=asking, floor_cents=floor)
                by_number = await numbered_command(ctx, conv, edit, waiting)
                if by_number is not None:
                    return by_number
                return await change_prices(ctx, conv, proposal, asking, floor, False)
            return PHOTO_MISSING if _MENTIONS_PHOTO.search(text) else NEEDS_PHOTO
        # Neither buying nor selling, as far as the reader can tell. If the words match
        # something for sale ("espresso machine?"), show it anyway.
        if text and search(backend.available(), text):
            return find(conv, text, None)
        if len(waiting) > 1:
            return waiting_list(conv, waiting, "Waiting for your approval:")
        if proposal is not None:
            return f"Your {proposal.title} is waiting at {usd(proposal.asking_cents)}. " + (
                APPROVE_PROMPT
            )
        return HELP

    async def handle(
        ctx: Context,
        key: str,
        msg: ChatMessage,
        deadline: float | None = None,
        deliver: Deliver | None = None,
    ) -> Reply:
        """The answer to one message. With several photos, the answer holds the proposals ready
        by `deadline` (loop time), and `deliver` sends the others as they're ready."""
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
            if backend.ebay is not None and store.announce_ebay(conv.owner):
                notice = EBAY_CONNECTED + notice  # the seller just came back from eBay
            attached = photos_in(msg)
            photos = [
                photo
                for photo in attached
                if photo.url not in conv.seen_photos and photo.url not in conv.pricing
            ]
            # The planner repeats earlier links, and sellers ask "is it done?", so a message that
            # only repeats a photo still being priced is about that photo.
            if not photos and choice is None and any(p.url in conv.pricing for p in attached):
                save(conv)
                return Reply(notice + ALREADY_WORKING)
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
                news = "" if reply.elsewhere else untold_note(conv)
                return reply._replace(text=notice + reply.text + news)
            numbered: list[tuple[int, Photo]] = []
            if len(photos) > 1:
                # Each photo gets a number for this conversation, after any earlier proposals.
                numbered_waiting(conv)
                numbered = list(enumerate(photos[:MAX_PHOTOS], len(conv.numbered) + 1))
                conv.numbered += [""] * len(numbered)
                # Photos over the limit count as seen so a repeated link doesn't price them
                # later; attaching one again makes a new upload.
                conv.seen_photos += [photo.url for photo in photos[MAX_PHOTOS:]]
            for _, photo in numbered or [(1, photos[0])]:
                conv.pricing[photo.url] = backend.boot_id
            save(conv)
        if numbered:
            notes = batch_notes(len(photos), text)
            return await price_batch(ctx, key, numbered, notice, notes, deadline, deliver)
        reply = await price_photo(ctx, key, photos[0], text)
        return Reply(notice + reply.text, reply.card, reply.more)

    async def handle_safely(
        ctx: Context,
        key: str,
        msg: ChatMessage,
        deadline: float | None = None,
        deliver: Deliver | None = None,
    ) -> Reply:
        try:
            reply = await handle(ctx, key, msg, deadline, deliver)
            if reply.elsewhere is not None:
                reply = await with_deals_elsewhere(key, reply)
            return reply
        except Exception:
            ctx.logger.exception(f"Chat message in {key[:8]} failed")
            return Reply(SOMETHING_BROKE)

    async def send_reply(
        ctx: Context, sender: str, reply: Reply, offer_uploads: bool = False
    ) -> None:
        await ctx.send(
            sender, text_message(reply.text, offer_uploads=offer_uploads, card=reply.card)
        )
        for text, card in reply.more:
            await ctx.send(sender, text_message(text, card=card))

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
                await send_reply(ctx, sender, earlier)
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
        if any(isinstance(item, StartSessionContent) for item in msg.content):
            await ctx.send(sender, uploads_welcome())
            first_message = False  # the welcome already offered uploads
            if not clean_text(msg) and not photos_in(msg) and card_choice(msg) is None:
                answered[msg_id] = None
                return  # ASI:One opening the session; nothing to answer yet
        # Message text can include a seller's lowest price, so it isn't logged.
        ctx.logger.info(
            f"Chat message in {key[:8]} ({len(photos_in(msg))} photo link(s)), "
            f"attachments: {attachments_summary(msg)}"
        )

        answer_sent = asyncio.Event()

        async def deliver(later: Reply) -> None:
            await answer_sent.wait()  # proposals priced later never overtake the answer
            await send_reply(ctx, sender, later)

        deadline = received + REPLY_WITHIN * BATCH_WAIT_SHARE
        job = asyncio.ensure_future(handle_safely(ctx, key, msg, deadline, deliver))
        left = REPLY_WITHIN - (asyncio.get_running_loop().time() - received)
        done, _ = await asyncio.wait({job}, timeout=max(0.0, left))
        if done:
            reply = job.result()
        else:
            reply = Reply(STILL_WORKING if photos_in(msg) else searching.get(key, WORKING))

            async def send_when_done() -> None:
                result = await job
                answered[msg_id] = result
                await send_reply(ctx, sender, result)
                answer_sent.set()

            follow_up = asyncio.ensure_future(send_when_done())
            follow_ups.add(follow_up)
            follow_up.add_done_callback(follow_ups.discard)
        answered[msg_id] = reply
        await send_reply(ctx, sender, reply, first_message)
        if done:
            answer_sent.set()

    @proto.on_message(ChatAcknowledgement)
    async def on_ack(ctx: Context, sender: str, msg: ChatAcknowledgement) -> None:
        pass

    return proto
