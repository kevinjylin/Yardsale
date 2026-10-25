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
The chat searches Yardsale's listings and other sites (elsewhere.py) and shows prices and links.
Shopping words ("best deal", "best price", "get me a good deal") are a search, never a haggle.
Haggling is opt-in: after a search, "haggle with the seller on 1, max $300" has Yardsale's buyer
agent (shopper.py) haggle with that item's agent (or every result's, "haggle with the sellers");
the reply shows every round and the best price. Or they pick a result by number and make offers
themselves ("offer $250"). Item agents agree to an offer or counter it. "accept" (or "accept 2")
buys at that price. Buyers never see a floor price, and sellers never see a buyer's max.

Paying: once a deal closes, the Market's checkout (checkout.py) makes a payment link (Visa Pay By
Link or Stripe, test mode) and the chat shows it. "paid" checks with the provider.

commands.py reads commands without a model. Anything else goes to ASI:One (asione.py). A seller
asking about buyers ("someone messaged me, reply to them") gets each listing's Craigslist post and
buyers; a message nobody understood gets a short question about what the conversation has going,
with buttons, and "yes" or "the second one" answers it. Only first contact gets the greeting.
Every reply is a short line of text and an Interactive Card (cards.py): the menu, the sell and
buy screens, proposals, listings, results, offers, haggles, payments, orders, watches, and deals
on other sites, each with a picture and buttons, and a Main menu button on all but the menu.
Tapping a button does exactly what typing the command does, with the same checks; the text
names the commands, so it works on its own wherever cards don't show.

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
    META_CARD_KIND,
    META_CARD_PAYLOAD,
    SelectionValue,
    extract_card_response,
)
from uagents_core.types import DeliveryStatus

from yardsale import (
    advice,
    campus,
    cards,
    commands,
    craigslist_buyer,
    craigslist_mail,
    deals,
    elsewhere,
    maps,
    meetup,
    onboarding,
    schools,
    students,
    watches,
)
from yardsale.asione import ReadMessage
from yardsale.checkout import Checkout
from yardsale.common import PipelineError, UnpricedError
from yardsale.deals import ScoredDeal
from yardsale.ebay_seller import EbayError, EbaySeller
from yardsale.elsewhere import (
    COMPS_LATE,
    DealFinder,
    Found,
    Search,
    names_of,
    search_place,
    search_words,
    site_name,
)
from yardsale.models import (
    AcceptCounter,
    Appraisal,
    Counter,
    Deal,
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

    async def ask_deal_finder(self, query: str, budget_cents: int | None, zip_code: str) -> Found:
        """Have the Deal Finder agent run a search and return its first answer (by
        elsewhere.SOURCE_WITHIN). It searches with `finder`, so finder.search() with the same
        words afterwards returns that running search, for slower sites. Raises SourceError if
        it refuses or doesn't answer."""
        ...

    @property
    def checkout(self) -> Checkout: ...


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif", ".gif"}
_MARKDOWN_LINK = re.compile(r"!?\[[^\]]*\]\((https?://[^\s)]+)\)")
_BARE_LINK = re.compile(r"https?://[^\s)\]<>\"']+")
_MENTION = re.compile(r"^\s*@\S+\s*")
_MENTIONS_PHOTO = re.compile(
    r"\b(photos?|pictures?|pics?|images?|upload(ed)?|attach(ed|ment)?)\b", re.IGNORECASE
)

HOW_IT_WORKS = (
    f"To sell, attach a photo (up to {MAX_PHOTOS} at once) and say your minimum, like "
    '"at least $40". To buy, tell me what you want and your max, like "find me Jordan 4s under '
    "$200\", and I'll search Yardsale and other marketplaces. Or pick from the menu."
)
HELP = "Hi, I'm Yardsale. " + HOW_IT_WORKS  # the greeting, for a conversation's first contact
UNCLEAR = "I didn't catch that. " + HOW_IT_WORKS  # a returning conversation isn't greeted again
AGENT_ANSWERS_EMAIL = (
    "Its agent answers Craigslist buyers' emails by itself, usually within seconds: it haggles "
    "with your prices, never below your lowest, and sets up where to meet once a buyer says deal "
    "(they pay you in person). "
    "You don't need to tell it to reply or haggle."
)
EMAIL_OFF = (
    "Craigslist email isn't connected on this Yardsale server, so its agent can't answer "
    "Craigslist buyers yet; buyers in ASI:One and @yardsale still haggle with it."
)
# A first message that's only a hello: nothing to answer after sign-up.
_GREETING = re.compile(r"(?i)^\W*(hi|hey|hello|yo|sup|hiya|howdy|good (morning|evening))\W*$")
CHAT_NOTICE_WINDOW = 86400.0  # seconds: Craigslist chat notices a seller hears about


def _clip(said: str, limit: int = 140) -> str:
    """One line of an email for the chat, cut at a word."""
    said = " ".join(said.split())
    return said if len(said) <= limit else said[:limit].rsplit(" ", 1)[0] + "…"


CRAIGSLIST_CHAT = (
    "It can't read messages sent in Craigslist's own chat, though. If a buyer messaged you "
    "there, ask them to email the address on the post instead."
)
HAGGLE_FIRST = (
    'Haggling is only for Yardsale listings, so search first, like "find me a road bike under '
    '$300". Then say "haggle with the seller on 1" and my agent will negotiate for you.'
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
SOLD_HEADING = "Recent eBay sales:"  # what the same thing sold for: these set the price
ASKING_HEADING = "Listed for sale now:"  # what similar ones ask today; nothing here sold
APPROVE_PROMPT = (
    'Tap Approve, or reply "approve" to put it up for sale, "ask $X" or "lowest $X" to change a '
    'price, or "cancel".'
)
_MENU = re.compile(
    r"^(?:(?:open\s+|show\s+(?:me\s+)?)?(?:the\s+)?(?:main\s+)?menu|home|start over)\b"
)
_NOT_NOW = re.compile(
    r"(?:not\s+(?:now|yet|today)|later|maybe\s+later|skip(?:\s+it)?|no\s+thanks)\b"
)
MEETUP_NOT_ON_CAMPUS = (
    "Meeting spots are a campus feature: students at one school get a public place on their "
    "campus to hand items over. This chat isn't signed in with a school email, so agree a "
    "place with the other side here in the chat."
)
CRAIGSLIST_NOT_NOW = (
    "OK. It's listed on Yardsale, where buyers in ASI:One and @yardsale can find it, and its agent "
    'answers them for you. Say "craigslist" any time for a ready-to-paste post.'
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
    # Messages sent right after this one, such as each proposal of several photos.
    more: tuple[tuple[str, MetadataContent | None], ...] = ()
    # (search, budget, ZIP code or ""): search other sites, outside the conversation's lock,
    # and add the best deals to this text before it's sent. The ZIP is the place the buyer
    # named ("in Berkeley"); "" searches the server's default place.
    elsewhere: tuple[str, int | None, str] | None = None


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
    # "proposal", "counter", "max", "craigslist" (offered a post), or "choice" (asked what an
    # unclear message meant): what a bare reply answers
    awaiting: str | None = None
    # With awaiting "choice": the card selections offered, in order, so "yes" or "the second
    # one" does what tapping that button does.
    choices: list[dict[str, Any]] = field(default_factory=list)
    introduced: bool = False  # has had the "Hi, I'm Yardsale" greeting
    quotes: dict[str, int] = field(default_factory=dict)  # item ID -> price on offer to this buyer
    budget: int | None = None  # the buyer's max from their last search or haggle
    # Seller's photos by number ("approve 2"): proposal item IDs, "" for photos that aren't one.
    numbered: list[str] = field(default_factory=list)
    needs_price: dict[str, Any] = field(default_factory=dict)  # number -> item without a price
    ebay_zip: str | None = None  # the seller's ZIP code, for their eBay listings
    last_query: str = ""  # the buyer's last search words, for "search other sites" and "watch"
    deals: list[dict[str, Any]] = field(default_factory=list)  # the last deals on other sites

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


def card_key(card: MetadataContent) -> str:
    """What a card shows: its kind and payload, without its random ID. Two cards with the same
    key look the same in the chat."""
    return card.metadata.get(META_CARD_KIND, "") + card.metadata.get(META_CARD_PAYLOAD, "")


def text_message(
    text: str, offer_uploads: bool = False, card: MetadataContent | None = None
) -> ChatMessage:
    """One chat message: the text, then at most one card. ASI:One keeps only the last card of a
    message (seen October 25: a proposal's Approve card vanished behind its sales carousel), so
    a second card goes in a message of its own (`Reply.more`)."""
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


def proposal_lines(listing: Listing) -> tuple[str, str, str]:
    """The proposal's asking line, lowest line, and what the price is based on."""
    appraisal = listing.appraisal
    asking_note = SOURCES[listing.asking_from]
    if listing.asking_from == "seller" and appraisal and appraisal.asking_cents is not None:
        asking_note += f"; recent eBay sales suggest {usd(appraisal.asking_cents)}"
    asking = f"Asking price: {usd(listing.asking_cents)} ({asking_note})"
    lowest = (
        f"Lowest price you'll take: {usd(listing.floor_cents)} "
        f"({SOURCES[listing.floor_from]}; buyers never see it)"
    )
    if appraisal is None or appraisal.asking_cents is None:
        return asking, lowest, "I couldn't find close eBay sales, so this uses your price."
    spread = ""
    if appraisal.low_cents is not None and appraisal.high_cents is not None:
        spread = f", most between {usd(appraisal.low_cents)} and {usd(appraisal.high_cents)}"
    return asking, lowest, f"Based on {appraisal.basis}{spread}."


def proposal_text(listing: Listing, n: int | None = None) -> str:
    """The text above a proposal's card: what it is, the prices, and links to the closest
    sales (card text can't be tapped). `n` is the proposal's number when there are several."""
    condition = f" ({listing.item.condition} condition)" if listing.item else ""
    sections = [
        f"Here's my suggested listing for your {listing.title}{condition}: asking "
        f"{usd(listing.asking_cents)}, lowest {usd(listing.floor_cents)} (only you see it)."
    ]
    appraisal = listing.appraisal
    if appraisal is not None and appraisal.asking_cents is not None and appraisal.matches:
        sold = [
            f"- {_link(match.comp.title, match.comp.url)}: {usd(match.comp.price_cents)}"
            + (f", sold {match.comp.sold_at}" if match.comp.sold_at else "")
            for match in appraisal.matches[:3]
        ]
        sections.append("\n".join([SOLD_HEADING, "", *sold]))
    # Live asking prices are what similar ones are listed for, not what anything sold for, so
    # they get their own list.
    asking = [line for line in advice.market_lines(listing) if line.startswith("- [")]
    if asking:
        sections.append("\n".join([ASKING_HEADING, "", *asking]))
    if n is None:
        sections.append(APPROVE_PROMPT)
    else:
        sections.append(
            f'Tap Approve, or reply "approve {n}", "ask {n} $X", "lowest {n} $X", or "cancel {n}".'
        )
    # Blank lines between the parts, so each list ends before the next line starts.
    return "\n\n".join(sections)


def proposal_card(listing: Listing, n: int | None = None) -> MetadataContent:
    asking, lowest, basis = proposal_lines(listing)
    market = [line for line in advice.market_lines(listing) if not line.startswith("- ")]
    return cards.proposal(
        listing,
        n,
        asking=asking,
        lowest=lowest,
        basis=basis,
        market=market,
        feedback=advice.price_feedback(listing, n),
    )


def numbered_proposal_text(listing: Listing, n: int) -> str:
    """A proposal named by its number, for a seller with several photos."""
    return f"#{n}: " + proposal_text(listing, n)


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
    """The proposal: its text and card (the only Approve button), then the eBay sales behind
    the price as a carousel in a message of its own, since ASI:One shows one card per message."""
    reply = Reply(proposal_text(listing), proposal_card(listing))
    comps = cards.comps(listing)
    if comps is None:
        return reply
    return reply._replace(more=((comps_text(listing), comps),))


def comps_text(listing: Listing) -> str:
    """The line about the sales carousel, counting what it shows against what the price used."""
    count = len(listing.appraisal.matches) if listing.appraisal else 0
    shown = min(count, cards.COMPS_SHOWN)
    sales = f"{count} eBay sale{'s' if count != 1 else ''}"
    if shown < count:
        return f"The cards show the {shown} closest of the {sales} behind that price."
    return f"The cards show the {sales} behind that price."


def unpriced_reply(title: str, n: int | None, market: MarketView | None) -> Reply:
    """A photo identified, but no sales were close enough: ask for the seller's price."""
    if n is None:
        text = (
            f"This looks like {title}, but no recent eBay sales were close enough to suggest a "
            'price. Tell me your price, like "ask $40" or "at least $30", and I\'ll set up the '
            'listing for you to approve. Or say "cancel".' + unpriced_hint(market)
        )
    else:
        text = (
            f"#{n}: This looks like {title}, but no recent eBay sales were close enough to "
            f'suggest a price. Tell me yours, like "ask {n} $40", or say "cancel {n}".'
        )
    hint = (
        " ".join(
            line
            for line in advice.market_lines(_unpriced_listing(market))
            if not line.startswith("-")
        )
        if market is not None and market.asking_median_cents is not None
        else ""
    )
    return Reply(text, cards.price_needed(n, title, hint))


def profile_link(listing: Listing) -> str:
    return f"https://agentverse.ai/agents/details/{listing.address}/profile"


def agent_line(listing: Listing) -> str:
    if listing.host == AGENTVERSE:
        named = f" as @{listing.handle}" if listing.handle else ""
        return (
            f"Its agent is live on Agentverse{named}: [{listing.address}]({profile_link(listing)})"
        )
    return f"Its agent is live at {listing.address}"


def listed_notes(listing: Listing, registered: bool, offline: bool) -> list[str]:
    if listing.host == AGENTVERSE:
        return []
    if offline:
        return [
            "(Offline mode: this address was made on this server and isn't on the public Almanac.)"
        ]
    if not registered:
        return ["(The Almanac hasn't confirmed it yet. The server retries every hour.)"]
    return []


def listed_text(listing: Listing, registered: bool, offline: bool) -> str:
    lines = [
        f"Your {listing.title} is for sale at {usd(listing.asking_cents)}. Its own agent answers "
        f"buyers and never goes below your {usd(listing.floor_cents)} minimum.",
        agent_line(listing),
        'Want it on Craigslist too? Tap "Write my Craigslist post", or reply "yes" or "not now".',
        *listed_notes(listing, registered, offline),
    ]
    return "\n".join(lines)


def results_text(found: list[Listing], query: str, owner: str) -> str:
    heading = f"Here's what's for sale for \"{query}\":" if query else "Here's what's for sale:"
    lines = [heading]
    for n, listing in enumerate(found, 1):
        condition = f" ({listing.item.condition})" if listing.item else ""
        yours = " (yours)" if listing.owner == owner else ""
        lines.append(f"{n}. {listing.title}: {usd(listing.asking_cents)}{condition}{yours}")
    lines.append(
        'Tap one for details, or buy one at its price with "offer $X on 1". (To have my agent '
        'haggle with a Yardsale seller instead, say "haggle with the seller on 1, max $X".)'
    )
    return "\n".join(lines)


def details_text(listing: Listing, mine: bool) -> str:
    lines = [f"{listing.title}: {usd(listing.asking_cents)}"]
    if listing.host == AGENTVERSE:
        lines.append(f"Its agent: [{listing.address}]({profile_link(listing)})")
    else:
        lines.append(f"Its agent: {listing.address}")
    lines.append(
        "This is your own listing."
        if mine
        else f'Buy it at its price with "offer {usd(listing.asking_cents)}", or make a lower '
        'offer. (To have my agent haggle with the seller, say "haggle with the seller, max $X".)'
    )
    return "\n".join(lines)


STATUS_TEXT = cards.STATUS_TEXT


def haggle_text(result: HaggleResult) -> str:
    """The text above the haggle card: one line per seller, and what to say to buy."""
    lines = [
        f"My buyer agent haggled with {len(result.outcomes)} seller agent(s) at once. Your max "
        f"of {usd(result.max_cents)} stayed private, and so did their lowest prices."
    ]
    for n, outcome in enumerate(result.outcomes, 1):
        if outcome.price_cents is not None:
            end = f"Yours for {usd(outcome.price_cents)}"
        else:
            end = STATUS_TEXT.get(outcome.status, outcome.status)
            end += f" ({outcome.note})" if outcome.note else ""
        lines.append(f"{n}. {outcome.title} (asking {usd(outcome.asking_cents)}): {end}.")
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
            + f'. Tap Buy, or reply "accept" to buy it{others}. Prices hold for 24 hours.'
        )
    else:
        lines.append("None of them came down to your max. Try a higher max, or another search.")
    return "\n".join(lines)


def deal_text(deal: DealRecord, title: str, provider_label: str | None) -> str:
    head = f"Deal! The {title} is yours for {usd(deal.price_cents)}. Order {deal.deal_id}."
    if deal.payment_status == PENDING and deal.payment_url:
        return (
            f"{head}\nPay here: [Pay {usd(deal.price_cents)}]({deal.payment_url}) "
            f'({provider_label}). The link is good for 24 hours. Once you\'ve paid, say "paid" '
            "and I'll confirm it."
        )
    if provider_label is None:
        # Payments off (no YARDSALE_PAYMENTS): the buyer pays the seller when they meet.
        return f"{head}\nYou'll pay the seller in person when you pick it up (cash or Venmo)."
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


PAYMENT_STATUS = {
    PAID: ("paid", "success"),
    PENDING: ("waiting for payment", "warning"),
    NOT_COLLECTED: ("agreed, no payment taken", "info"),
}


def payment_status_card(deal: DealRecord, title: str) -> MetadataContent:
    status, _ = PAYMENT_STATUS.get(deal.payment_status, ("lapsed", "warning"))
    return cards.payment_status(
        title, deal.price_cents, deal.deal_id, status.capitalize(), deal.payment_status == PAID
    )


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


def listing_line(
    n: int, listing: ExternalListing, why: str = "", budget_cents: int | None = None
) -> str:
    """One listing on another site: its link, then price, site, shipping, the budget
    difference, condition, location, age, `why` it's a good deal when scored (deals.py; a
    watch's finds aren't), and what the site didn't say (deals.describe)."""
    link = _link(listing.title, listing.url)
    about = deals.describe(listing, site_name(listing.source), why, budget_cents)
    return f"{n}. {link}: {about}"


def deal_line(n: int, deal: ScoredDeal, budget_cents: int | None = None) -> str:
    return listing_line(n, deal.listing, deal.why, budget_cents)


def deals_headline(found: Found) -> str:
    """The sentence the deals open with (deals.headline). Read defensively: `Found` grows."""
    return deals.headline(
        tuple(getattr(found, "deals", ()) or ()),
        getattr(found, "budget_cents", None),
        getattr(found, "market", None),
        site_name,
    )


def market_note(found: Found) -> str:
    """What the deals were judged against: "typical sold price on eBay: $185, from 7 sales"."""
    if found.comps_late:
        return COMPS_LATE
    if found.market is None:
        return "no recent eBay sales to compare prices with"
    sales = f"{found.market.sales} sale{'s' if found.market.sales != 1 else ''}"
    return f"typical sold price on eBay: {usd(found.market.typical_cents)}, from {sales}"


def elsewhere_text(found: Found, sites: str, headline: bool = True) -> str:
    """The deals on other sites, opening with the headline (deals.headline) unless the caller
    puts it first itself, or why there are none. "I didn't find" is only ever said of the sites
    that answered; a site that failed is named with why (timed out, blocked, ...), a site still
    searching is said to be, and a search where every site failed says it couldn't search."""
    about = f'"{found.query}"{_under(found.budget_cents)}'
    answered = [site_name(name) for name in found.searched]
    failed = elsewhere.failed_sentence(found)
    around = f", around {found.place}" if found.place else ""
    if not found.deals and not answered and found.pending:
        lines = [f"I'm still searching {names_of(found.pending)} for {about}."]
        if failed:
            lines.append(f"({failed}.)")
        lines.append(SEARCHING_ELSEWHERE)
    elif not found.deals and not answered:
        lines = [
            f"I couldn't search other sites for {about}: {failed or 'none of them answered'}. "
            "That doesn't mean it isn't out there; ask me again in a minute."
        ]
    elif not found.deals:
        lines = [f"I didn't find {about} on {elsewhere.and_list(answered)}{around} either."]
        if elsewhere.unfit_sentence(found):
            lines.append(elsewhere.unfit_sentence(found))
        if failed:
            lines.append(f"({failed}, so I couldn't check there.)")
    else:
        budget = found.budget_cents
        lines = [deals_headline(found), ""] if headline else []
        lines.append(f"Best deals on other sites for {about} ({market_note(found)}):")
        if found.loose:
            lines.append("Nothing had every word of your search, so these are the closest.")
        fitting = sum(1 for d in found.deals if not {"over_budget", "no_price"} & set(d.flags))
        said: set[str] = set()
        for n, deal in enumerate(found.deals, 1):
            if "over_budget" in deal.flags and "over_budget" not in said and budget:
                only = f"only {fitting}" if fitting else "none"
                lines.append(f"Over your {usd(budget)} budget, since {only} fit:")
                said.add("over_budget")
            elif "no_price" in deal.flags and "no_price" not in said:
                lines.append("No price listed, so ask the seller:")
                said.add("no_price")
            lines.append(deal_line(n, deal, budget))
        lines.append(NOT_YARDSALE)
        if any(listing.source == "craigslist" for listing in found.listings):
            lines.append(CRAIGSLIST_OFFERS)
        if found.place:
            lines.append(f"(Local sites searched around {found.place}.)")
        if failed:
            lines.append(f"({failed}, so these are from {elsewhere.and_list(answered)} only.)")
    if found.pending and (found.deals or answered):
        lines.append(f"(Still searching {names_of(found.pending)}; I'll send anything better.)")
    lines.append(watch_offer(sites))
    return "\n".join(lines)


FLAG_LABELS = {
    "over_budget": "Over budget",
    "no_price": "No price",
    "parts": "Parts or repair",
    "accessory": "Accessory",
    "missing_words": "Partial match",
}


def deal_price(deal: ScoredDeal, budget_cents: int | None = None) -> str:
    """A deal's card subtitle: the price, the site, and the budget difference (or how it
    compares with the typical sold price)."""
    listing = deal.listing
    site = site_name(listing.source)
    if listing.price_cents is None:
        price = f"Price not listed on {site}"
    else:
        price = usd(deal.total_cents)
        if listing.shipping_cents:
            price += " with shipping"
        price += f" on {site}"
    pct = deal.versus_market_pct
    if against := deals.budget_phrase(listing, budget_cents, qualify=False):
        price += f", {against}"
    elif pct is not None and pct >= deals.ABOUT_MARKET:
        price += f", {pct}% under typical"
    elif pct is not None and pct <= -deals.ABOUT_MARKET:
        price += f", {-pct}% above typical"
    return price


def saved_deals(found: Found) -> list[dict[str, Any]]:
    """The deals as the conversation keeps them, for their cards and "Get the link"."""
    budget = getattr(found, "budget_cents", None)
    kept = []
    for deal in found.deals:
        listing = deal.listing
        flag = next((label for f, label in FLAG_LABELS.items() if f in deal.flags), "")
        kept.append(
            {
                "id": f"{listing.source}-{listing.external_id}"[:80],
                "title": listing.title[:120],
                "url": listing.url,
                "price": deal_price(deal, budget),
                "site": site_name(listing.source),
                "source": listing.source,
                "why": deal.why,
                "location": listing.location,
                "condition": listing.condition,
                "image": listing.image_url,
                "flag": flag,
            }
        )
    return kept


def later_text(early: Found, final: Found, sites: str) -> str | None:
    """The follow-up once slower sites (or the eBay sold comps) are in, after `early` went out:
    the whole list again when it changed, a line when it didn't, nothing when only the comps
    were late and changed nothing."""
    late = names_of(early.pending)
    if final.deals != early.deals:
        lead = f"{late} answered too" if early.pending else "Recent eBay sales came in"
        return f"{lead}, so here's the updated list.\n\n{elsewhere_text(final, sites)}"
    if not early.pending:
        return None
    answered = [name for name in early.pending if name in final.searched]
    if answered:
        return f"{names_of(answered)} had nothing better than the deals above."
    return f"({late} couldn't be searched just now.)"


def deals_subtitle(found: Found) -> str:
    if found.market is None:
        return "Scored against each other: no recent eBay sales to compare with"
    return f"Scored against the {market_note(found)}"[:160]


def _form_price(value: SelectionValue | None) -> int | None:
    """A price typed into a card's form: a number of dollars, or empty."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, bool):
        raise PriceError("That isn't a price I can use.")
    return parse_usd(str(value))


def _number(value: SelectionValue | None) -> int | None:
    """A whole number from a button's selection (JSON gives ints, prose may give strings)."""
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(float(value))
    except ValueError:
        return None


# --- The protocol --------------------------------------------------------------------------------


def build_chat_protocol(backend: Backend, read: ReadMessage, fetch: FetchPhoto) -> AgentProtocol:
    proto = AgentProtocol(spec=chat_protocol_spec)
    store = backend.store
    locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
    answered: OrderedDict[str, Reply | None] = OrderedDict()  # message ID -> reply, for resends
    delivered: set[str] = set()  # message IDs whose answer reached ASI:One
    # Conversation key -> (pricing job, photo count): a new student's first photos, answered once
    # they've signed up.
    held: OrderedDict[str, tuple[asyncio.Future[Reply], int]] = OrderedDict()
    # A new student's first words (no photo), answered once they've signed up: searches are
    # same-school, so they can't run before the school is known.
    held_words: OrderedDict[str, ChatMessage] = OrderedDict()
    follow_ups: set[asyncio.Task[None]] = set()  # keeps background replies from being collected
    fields = {f.name for f in dataclasses.fields(Conversation)} - {"key"}
    finder = backend.finder

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

    def waiting_reply(conv: Conversation, waiting: Waiting, head: str) -> Reply:
        """The waiting list with its card: tap one to review it, or approve them all."""
        entries = [(n, waiting_line(conv, n, listing), listing) for n, listing in waiting]
        return Reply(waiting_list(conv, waiting, head), cards.waiting(entries, head))

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
            reply = unpriced_reply(ex.item.title, None, ex.market)
        except (PhotoError, PipelineError, PriceError) as ex:
            # A PriceError's message can include the seller's floor, which stays out of logs.
            if isinstance(ex, PriceError):
                detail = "prices didn't work"
            else:
                detail = f"{ex} ({ex.__cause__})" if ex.__cause__ else str(ex)
            ctx.logger.warning(f"Chat photo for {key[:8]} failed: {detail}")
            failed = f"I couldn't price this. {ex} Send the photo again to retry."
            reply = Reply(failed, cards.priced_photo_failed(failed))
        except Exception:
            ctx.logger.exception(f"Chat photo for {key[:8]} failed")
            reply = Reply(SOMETHING_BROKE, cards.priced_photo_failed(SOMETHING_BROKE))
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
            reply = Reply(numbered_proposal_text(listing, n), proposal_card(listing, n))
        except UnpricedError as ex:
            unpriced = ex
            reply = unpriced_reply(ex.item.title, n, None)
        except (PhotoError, PipelineError, PriceError) as ex:
            detail = f"{ex} ({ex.__cause__})" if ex.__cause__ else str(ex)
            if isinstance(ex, PriceError):
                detail = "prices didn't work"
            ctx.logger.warning(f"Chat photo #{n} for {key[:8]} failed: {detail}")
            failed = f"#{n}: I couldn't price this photo. {ex} Send it again to retry."
            reply = Reply(failed, cards.priced_photo_failed(failed))
        except Exception:
            ctx.logger.exception(f"Chat photo #{n} for {key[:8]} failed")
            reply = Reply(f"#{n}: {SOMETHING_BROKE}", cards.priced_photo_failed(SOMETHING_BROKE))
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

    def batch_summary(key: str, numbers: list[int]) -> Reply:
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
        card = cards.batch_done(lines, waiting)
        if waiting:
            lines.append(BATCH_PROMPT)
        return Reply("\n".join(lines), card)

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
            summary = batch_summary(key, numbers)
            return Reply(notice + head + notes, None, (*ready, (summary.text, summary.card)))
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
                await deliver(batch_summary(key, numbers))
            except Exception:
                ctx.logger.exception(f"Sending priced photos in {key[:8]} failed")

        follow_up = asyncio.ensure_future(send_rest())
        follow_ups.add(follow_up)
        follow_up.add_done_callback(follow_ups.discard)
        return Reply(notice + head + notes, cards.pricing(count), ready)

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

    def review(conv: Conversation, item_id: str) -> str | Reply:
        """A seller's proposal again, by item: from the waiting list or a notice's button."""
        listing = store.get(item_id) if item_id else None
        if listing is None:
            waiting = numbered_waiting(conv)
            if len(waiting) > 1:
                return waiting_reply(conv, waiting, "Waiting for your approval:")
            pending = store.owned(conv.owner, PROPOSED)
            if conv.unpriced is not None:
                title = str(conv.unpriced["item"]["title"])
                return unpriced_reply(title, None, saved_market(conv.unpriced))
            if waiting and waiting[0][1] is None:
                n = waiting[0][0]
                return unpriced_reply(str(conv.needs_price[str(n)]["item"]["title"]), n, None)
            if not pending:
                return Reply(
                    "There's nothing waiting for approval. Attach a photo of something to sell.",
                    cards.sell(),
                )
            listing = pending[0]
        if listing.owner != conv.owner or listing.status != PROPOSED:
            return "That listing isn't waiting for approval anymore."
        conv.awaiting = "proposal"
        numbers = {item.item_id: n for n, item in numbered_waiting(conv) if item is not None}
        number = numbers.get(listing.item_id) if len(numbers) > 1 else None
        if number is not None:
            return Reply(numbered_proposal_text(listing, number), proposal_card(listing, number))
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

    async def approve(ctx: Context, conv: Conversation, proposal: Listing) -> str | Reply:
        live = await go_live(ctx, conv, proposal)
        if isinstance(live, str):
            return live
        listing, registered, on_ebay = live
        conv.awaiting = "craigslist"  # a bare "yes" now means: write the Craigslist post
        text = listed_text(listing, registered, backend.offline) + on_ebay + waiting_too(conv)
        notes = listed_notes(listing, registered, backend.offline)
        if on_ebay:
            notes.append("Also listed on your eBay; the link is in the message above.")
        return Reply(text, cards.listed(listing, notes))

    def craigslist_post(conv: Conversation, item_id: str | None) -> str | Reply:
        """The ready-to-paste Craigslist post for one of the seller's live listings (the newest
        when no item is named)."""
        conv.awaiting = None
        active = store.owned(conv.owner, ACTIVE)
        listing = next((each for each in active if each.item_id == item_id), None)
        if listing is None and item_id is None and active:
            listing = active[0]
        if listing is None:
            return "That item isn't for sale anymore."
        try:
            settings = craigslist_mail.settings_from_env()
        except craigslist_mail.MailConfigError:
            settings = None
        title = craigslist_mail.post(store, listing)
        return Reply(
            craigslist_mail.post_text(listing, title, settings),
            cards.craigslist_post(listing, title, settings.address if settings else None),
        )

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
        return Reply(numbered_proposal_text(listing, n), proposal_card(listing, n))

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
            cards.prices_form(proposal),
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
                return waiting_reply(conv, waiting, head)
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

    def my_listings(conv: Conversation) -> str | Reply:
        listings = store.owned(conv.owner)
        if not listings:
            return Reply(
                "You haven't listed anything yet. Attach a photo of something to sell.",
                cards.sell(),
            )
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
        entries: list[tuple[Listing, str, str, cards.Action]] = [
            (listing, "for sale", "success", ("Manage", {"action": "manage", "item_id": i}))
            for listing in active
            if (i := listing.item_id)
        ]
        for listing in listings:
            ids = {"item_id": listing.item_id}
            if listing.status == PROPOSED:
                number = f" (#{numbers[listing.item_id]})" if len(waiting) > 1 else ""
                lines.append(
                    f"Waiting for your approval{number}: {listing.title} at "
                    f"{usd(listing.asking_cents)}"
                )
                entries.append(
                    (
                        listing,
                        "waiting for approval",
                        "warning",
                        ("Review", {"action": "review", **ids}),
                    )
                )
            elif listing.status == SOLD:
                deal = store.deal_for(listing.item_id)
                lines.append(sold_line(listing, deal))
                paid = deal is not None and deal.payment_status == PAID
                entries.append(
                    (
                        listing,
                        "sold, paid" if paid else "sold",
                        "success" if paid else "info",
                        ("Details", {"action": "manage", **ids}),
                    )
                )
        lines += craigslist_mail.seller_lines(store, listings)
        if active:
            lines.append(
                'Tap one to manage it, or say "unlist 1" to take one down, "craigslist 1" to post '
                "it on Craigslist" + (', or "list 1 on ebay".' if backend.ebay is not None else ".")
            )
        if not entries:
            return "\n".join(lines) or "Nothing's for sale right now. Attach a photo to sell."
        return Reply("\n".join(lines), cards.my_listings(entries))

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

    def owned_listing(conv: Conversation, item_id: str) -> Listing | None:
        listing = store.get(item_id) if item_id else None
        return listing if listing is not None and listing.owner == conv.owner else None

    def manage(conv: Conversation, item_id: str) -> str | Reply:
        """One of the seller's listings, with what they can do with it."""
        listing = owned_listing(conv, item_id)
        if listing is None:
            return "That listing isn't yours, or it's gone."
        if listing.status == PROPOSED:
            return review(conv, item_id)
        ids = {"item_id": listing.item_id}
        pairs: list[tuple[str, str]] = [
            ("Asking price", usd(listing.asking_cents)),
            ("Lowest price (only you see it)", usd(listing.floor_cents)),
            ("Its agent", cards.agent_name(listing)),
        ]
        lines = [f"Your {listing.title}: {usd(listing.asking_cents)}."]
        if listing.host == AGENTVERSE:
            lines.append(f"Its agent on Agentverse: [{listing.address}]({profile_link(listing)})")
        on_craigslist = craigslist_mail.seller_lines(store, [listing])
        if on_craigslist and listing.status == ACTIVE:
            pairs.append(("On Craigslist", on_craigslist[0].split(": ", 1)[-1]))
            mine = [
                exchange
                for exchange in craigslist_mail.recent_exchanges(store, conv.owner, limit=20)
                if exchange.item_id == listing.item_id
            ]
            lines += exchange_lines(mine[:3])
        record = store.ebay_listing(listing.item_id)
        if record is not None and record.status == "active":
            pairs.append(("On eBay", "Yes; the link is in the message above"))
            lines.append(f"On eBay: {record.url}")
        actions: list[cards.Action] = []
        if listing.status == ACTIVE:
            pairs.insert(0, ("Status", "For sale"))
            actions.append(("Craigslist post", {"action": "craigslist", **ids}))
            if (
                backend.ebay is not None
                and backend.ebay.connected(conv.owner)
                and not (record is not None and record.status == "active")
            ):
                actions.append(("List on eBay", {"action": "ebay_list", **ids}))
            actions.append(("Take it down", {"action": "unlist", **ids}))
        else:
            deal = store.deal_for(listing.item_id)
            status = sold_line(listing, deal) if listing.status == SOLD else listing.status
            pairs.insert(0, ("Status", status))
        return Reply("\n".join(lines), cards.manage(listing, pairs, actions))

    def unlist(conv: Conversation, pick: int | None) -> str | Reply:
        active = store.owned(conv.owner, ACTIVE)
        if not active:
            return "You have nothing for sale right now."
        if pick is None and len(active) > 1:
            return 'Which one? Say "unlist 2", using the numbers from "my listings".'
        index = (pick or 1) - 1
        if not 0 <= index < len(active):
            return f"Pick a number from 1 to {len(active)}."
        return take_down(conv, active[index])

    def take_down(conv: Conversation, listing: Listing) -> str | Reply:
        if backend.cancel(listing.item_id, conv.owner) is None:
            return f"The {listing.title} isn't for sale anymore."
        gone = f"Your {listing.title} is no longer for sale."
        if listing.host == AGENTVERSE:
            text = f"{gone} Its agent on Agentverse is being deleted."
        else:
            text = f"{gone} Its agent turns down new offers."
        return Reply(text, cards.notice(text, [cards.LISTINGS, cards.SELL], "Taken down"))

    # --- Buying ----------------------------------------------------------------------------

    def for_sale(conv: Conversation) -> list[Listing]:
        """The listings this conversation may see.

        Everything, normally. In campus mode, only items from classmates at the same school:
        that's the whole promise, so every search path goes through here rather than each
        remembering to filter. Someone who hasn't given a school email sees nothing.
        """
        listings = backend.available()
        if not onboarding.campus_mode():
            return listings
        return students.listings_for(store, listings, conv.owner)

    def find(
        conv: Conversation, query: str, budget_cents: int | None, near: str = ""
    ) -> str | Reply:
        """Yardsale's listings for a search, then other sites'. `near` is the ZIP code of a
        place the buyer's message named, when the query (ASI:One's reading) lost it."""
        near = search_place(query) or near
        found = search(for_sale(conv), query, budget_cents)
        if conv.awaiting in ("max", "counter"):
            # A new search ends the haggle: a bare "$300" is now this search's budget, and
            # prose like "proceed with the purchase" can't take an old price.
            conv.awaiting = None
        conv.budget = budget_cents or conv.budget
        conv.results = [listing.item_id for listing in found]
        conv.selected = found[0].item_id if len(found) == 1 else None
        conv.last_query = search_words(query) or conv.last_query
        if not found and finder.enabled and search_words(query):
            return look_elsewhere(conv, query, budget_cents, "Nothing on Yardsale matches", near)
        if not found:
            under = f" under {usd(budget_cents)}" if budget_cents else ""
            about = f' for "{query}"' if query else ""
            return Reply(
                f"Nothing is for sale{about}{under} right now. Try other words, or ask "
                '"what\'s for sale?"',
                cards.buy(query, budget_cents),
            )
        card = cards.results(found, conv.owner, query)
        reply = Reply(results_text(found, query, conv.owner), card)
        if finder.enabled and search_words(query):
            # Yardsale's listings first, then the best deals on other sites, every time: a
            # buyer should see the whole market, not only what's listed here.
            words = search_words(query)
            conv.last_query = words
            text = f"{reply.text}\n\nI'm also checking {finder.site_names()} for more."
            return reply._replace(text=text, elsewhere=(words, budget_cents, near))
        return reply

    def pick(conv: Conversation, number: int) -> str | Reply:
        if not 1 <= number <= len(conv.results):
            return f"Pick a number from 1 to {len(conv.results)}."
        listing = store.get(conv.results[number - 1])
        if listing is None or listing.status != ACTIVE:
            return "Sorry, that one is no longer for sale."
        conv.selected = listing.item_id
        mine = listing.owner == conv.owner
        return Reply(details_text(listing, mine), cards.item_detail(listing, mine))

    def shown(conv: Conversation, item_id: str) -> Listing | str:
        """An item from this buyer's last results, for a tapped button: still for sale, and one
        this conversation was shown (campus mode shows only classmates' items)."""
        if item_id not in conv.results:
            return "That list is out of date. Search again to see what's for sale."
        listing = store.get(item_id)
        if listing is None or listing.status != ACTIVE:
            return "Sorry, that one is no longer for sale."
        return listing

    def results_again(conv: Conversation) -> str | Reply:
        found = [
            listing
            for item_id in conv.results
            if (listing := store.get(item_id)) is not None and listing.status == ACTIVE
        ]
        if not found:
            return Reply(
                "There are no results to go back to. What are you looking for?", cards.buy()
            )
        conv.results = [listing.item_id for listing in found]
        return Reply(
            results_text(found, conv.last_query, conv.owner),
            cards.results(found, conv.owner, conv.last_query),
        )

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

    def meeting_line(conv: Conversation) -> str:
        """Where to hand the item over, on campus. Empty unless both sides are at one school.

        Only in campus mode, and only when the campus has been mapped. Neither student's own
        location is involved: it's one public spot on the campus they share.
        """
        if not onboarding.campus_mode():
            return ""
        school = students.school_of(store, conv.owner)
        if not school:
            return ""
        try:
            meeting = students.where_to_meet(store, school)
        except students.StudentError:
            return ""
        link = maps.map_url(meeting.spot, campus.school_label_for(school))
        return f"\n\nMeet at [{meetup.describe(meeting)}]({link}) to hand it over."

    def meetup_reply(conv: Conversation) -> str | Reply:
        """Where to meet on campus, on demand: "where should we meet", "meetup spot", or the
        Where to meet button. The best spot first, then two more, each with a map link.

        Works for any conversation that has given a school, whether or not campus mode is on,
        so meetups can be shown without locking the rest of the product to students. Without
        a school: the email form in campus mode, or a plain explanation otherwise.
        """
        school = students.school_of(store, conv.owner)
        if not school:
            if onboarding.campus_mode():
                return Reply(
                    "I can suggest a public spot on your campus once I know your school. "
                    + onboarding.ASK_FOR_EMAIL,
                    onboarding.email_form(),
                )
            return Reply(MEETUP_NOT_ON_CAMPUS, cards.notice(MEETUP_NOT_ON_CAMPUS, [cards.BUY]))
        places = students.other_places(store, school, limit=3)
        if not places:
            return Reply(students.NO_CAMPUS, cards.notice(students.NO_CAMPUS, [cards.BUY]))
        label = schools.school_label(school)
        university = campus.school_label_for(school)
        lines = [f"Meet on campus at {label}. Best spot first:"]
        for n, place in enumerate(places, 1):
            lines.append(f"{n}. [{meetup.describe(place)}]({maps.map_url(place.spot, university)})")
        lines.append(
            "Pick one and agree a time in the chat. Nobody has to say where they live, and the "
            "first one there won't be waiting alone."
        )
        card = cards.meetup(
            label,
            [
                (
                    meetup.describe(place),
                    place.spot.kind.replace("_", " "),
                    maps.map_url(place.spot, university),
                )
                for place in places
            ],
        )
        return Reply("\n".join(lines), card)

    async def settle(conv: Conversation, listing: Listing, deal: Deal) -> str | Reply:
        """A deal closed: make the payment link and show it."""
        conv.quotes.pop(listing.item_id, None)
        where = meeting_line(conv)
        after: list[cards.Action] = [cards.ORDERS, *([cards.MEET] if where else []), cards.BUY]
        record = await backend.checkout.start(deal.deal_id) or store.deal(deal.deal_id)
        if record is None:
            text = f"Deal! The {listing.title} is yours for {usd(deal.price_cents)}.{where}"
            return Reply(text, cards.notice(text, after, "It's yours"))
        label = provider_label()
        text = deal_text(record, listing.title, label) + where
        if record.payment_status == PENDING and label is not None:
            meet = cards.plain(where).removeprefix("Meet at ").removesuffix(" to hand it over.")
            card = cards.payment(
                record.deal_id,
                record.price_cents,
                listing.title,
                label,
                cards.listing_picture(listing),
                meet,
                url=record.payment_url,
            )
            return Reply(text, card)
        return Reply(text, cards.notice(text, after, "It's yours"))

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
                    f'{listing.title}. Tap Accept, or reply "accept" to buy it at that price. The '
                    "agreement is good for 24 hours.",
                    cards.counter(listing, reply.counter_cents, reply.note, agreed=True),
                )
            return Reply(
                f"The seller's agent countered with {usd(reply.counter_cents)} for the "
                f'{listing.title}. Tap Accept, or reply "accept" to buy it at '
                f"{usd(reply.counter_cents)}, or make another offer. The counter-offer is good "
                "for 24 hours.",
                cards.counter(listing, reply.counter_cents, reply.note, agreed=False),
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
        return await offer_on(ctx, conv, cents, item_id)

    async def offer_on(ctx: Context, conv: Conversation, cents: int, item_id: str) -> str | Reply:
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
                    else (
                        "There's nothing to accept yet. Tell me what you're looking for, like "
                        '"find me a road bike under $300".'
                    )
                )
            item_id = conv.results[pick - 1]
            if item_id not in conv.quotes:
                return f"Item {pick} has no price on offer to you. Make an offer first."
            return await accept(ctx, conv, item_id, conv.quotes[item_id])
        assert conv.counter is not None
        return await accept(ctx, conv, str(conv.counter["item_id"]), int(conv.counter["cents"]))

    # --- Haggling for the buyer -------------------------------------------------------------

    # Opt-in only: handle_text calls these for an explicit "haggle with the seller on 1" after a
    # search, never for shopping words like "best deal".

    def haggle_targets(conv: Conversation, max_cents: int, every: bool) -> list[Listing]:
        """The item the buyer named or picked, or every result of the last search."""
        reach = max_cents * LOWEST_SHARE_REACH[0] // LOWEST_SHARE_REACH[1]
        item_ids = [conv.selected] if conv.selected and not every else conv.results
        found = [
            listing
            for item_id in item_ids
            if (listing := store.get(item_id)) is not None and listing.status == ACTIVE
        ]
        return [
            listing
            for listing in found
            if listing.owner != conv.owner and listing.asking_cents <= reach
        ]

    async def haggle(
        ctx: Context,
        conv: Conversation,
        max_cents: int | None,
        pick: int | None = None,
        every: bool = False,
    ) -> str | Reply:
        if not conv.results:
            return Reply(HAGGLE_FIRST, cards.buy())
        if pick is not None:
            if not 1 <= pick <= len(conv.results):
                return f"Pick a number from 1 to {len(conv.results)}."
            conv.selected = conv.results[pick - 1]
        if max_cents is None:
            max_cents = conv.budget
        if max_cents is None:
            conv.awaiting = "max"
            return Reply(ASK_MAX, cards.max_form(len(conv.results)))
        conv.budget = max_cents
        targets = haggle_targets(conv, max_cents, every)
        if not targets:
            return Reply(
                f"Nothing here is close enough to {usd(max_cents)} to haggle over. Try a higher "
                "max, or search for something else.",
                cards.max_form(len(conv.results), budget_cents=max_cents),
            )
        return await haggle_with(ctx, conv, max_cents, targets)

    async def haggle_with(
        ctx: Context, conv: Conversation, max_cents: int, targets: list[Listing]
    ) -> Reply:
        """Send the buyer agent to haggle with these items at once."""
        conv.budget = max_cents
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
        return Reply(haggle_text(result), cards.haggle(result))

    # --- Other sites ------------------------------------------------------------------------

    def look_elsewhere(
        conv: Conversation, query: str, budget: int | None, lead: str, near: str = ""
    ) -> Reply:
        """Search other sites after this reply's text, outside the lock (handle_safely)."""
        words = search_words(query)
        conv.last_query = words
        conv.budget = budget or conv.budget
        text = (
            f'{lead} "{words}"{_under(budget)} right now, so I\'m checking {finder.site_names()}.'
        )
        return Reply(text, elsewhere=(words, budget, near))

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
            elsewhere=(query, budget, search_place(command.text)),
        )

    async def deals_card(key: str, found: Found) -> MetadataContent | None:
        """The deals' carousel. The conversation keeps them, so "Get the link" works later."""
        kept = saved_deals(found)
        if not kept:
            return None
        async with locks[key]:
            conv = load(key)
            conv.deals = kept
            save(conv)
        return cards.deals(kept, deals_subtitle(found))

    async def with_deals(key: str, reply: Reply, found: Found) -> Reply:
        """The reply with the best deals on other sites added to it. With Yardsale's own results
        (and their card), the deals follow as a second message that opens with the headline;
        otherwise the headline ("Found it: ...") leads the whole reply."""
        sites = finder.site_names()
        card = await deals_card(key, found)
        if reply.card is not None and card is not None:
            return reply._replace(more=(*reply.more, (elsewhere_text(found, sites), card)))
        headline = deals_headline(found) if found.deals else ""
        rest = elsewhere_text(found, sites, headline=not headline)
        text = f"{headline}\n\n{reply.text}\n\n{rest}" if headline else f"{reply.text}\n\n{rest}"
        return reply._replace(text=text, card=card or reply.card)

    async def deals_reply(key: str, found: Found) -> Reply:
        text = elsewhere_text(found, finder.site_names())
        card = await deals_card(key, found)
        return Reply(text, card or cards.notice(text, [cards.BUY, cards.WATCHES]))

    async def from_deal_finder(
        ctx: Context, query: str, budget: int | None, near: str
    ) -> tuple[Found, Search | None]:
        """A new search, done by the Deal Finder agent: its first answer (by the finder's
        deadline), and the search it's still running for slower sites, which the Market shares
        (None if it's gone). If the agent refuses or doesn't answer, the Market searches itself,
        so the buyer still gets deals. Raises SourceError only if that can't search either."""
        try:
            found = await backend.ask_deal_finder(query, budget, near)
        except Exception as ex:
            ctx.logger.warning(
                f"The Deal Finder agent didn't answer ({type(ex).__name__}); searching here"
            )
            running = finder.search(query, budget, near)
            return await running.early(), running
        return found, finder.cached(query, budget, near)

    async def send_later(
        ctx: Context,
        key: str,
        running: Search | None,
        early: Found | None,
        deliver: Deliver,
        asked: tuple[str, int | None, str] = ("", None, ""),
    ) -> None:
        """The deals once they're in (by the finder's deadline), then slower sites' deals.
        Without a running search, the search is new and the Deal Finder agent does it
        (`asked`: its words, budget, and ZIP)."""
        try:
            if early is None:
                if running is None:
                    early, running = await from_deal_finder(ctx, *asked)
                else:
                    early = await running.early()
                await deliver(await deals_reply(key, early))
            if running is None or running.done():
                return
            final = await running.complete()
            later = later_text(early, final, finder.site_names())
            if later is not None:
                card = await deals_card(key, final) if final.deals != early.deals else None
                await deliver(Reply(later, card))
        except Exception:
            ctx.logger.exception(f"Sending deals from other sites in {key[:8]} failed")

    def in_background(job: Awaitable[None]) -> None:
        task = asyncio.ensure_future(job)
        follow_ups.add(task)
        task.add_done_callback(follow_ups.discard)

    async def with_deals_elsewhere(
        ctx: Context, key: str, reply: Reply, deliver: Deliver | None
    ) -> Reply:
        """The reply at once, and the best deals on other sites as the next message once the
        sites answer: by the finder's deadline (elsewhere.SOURCE_WITHIN), with slower sites'
        deals after that. A new search is the Deal Finder agent's, and its answer is what the
        buyer sees first; a search that's already answered or running (asked before, here or
        through the API) is picked up from the finder the agent shares, with no message. A
        search that's already answered goes in the reply."""
        assert reply.elsewhere is not None
        query, budget, near = reply.elsewhere
        reply = reply._replace(elsewhere=None)
        try:
            finder.check(query)  # nothing to search for, or sites off: said now, nothing started
            running = finder.cached(query, budget, near)
            if running is None and deliver is None:  # nowhere to send it later: wait for it
                found, running = await from_deal_finder(ctx, query, budget, near)
                return await with_deals(key, reply, found)
        except SourceError as ex:
            return reply._replace(text=f"{reply.text}\n\nI couldn't search other sites: {ex}.")
        if running is not None and (running.ready() or deliver is None):
            found = await running.early()
            if deliver is not None and not running.done():
                in_background(send_later(ctx, key, running, found, deliver))
            return await with_deals(key, reply, found)
        assert deliver is not None
        in_background(send_later(ctx, key, running, None, deliver, (query, budget, near)))
        return reply._replace(
            text=f"{reply.text}\n\n{SEARCHING_ELSEWHERE}",
            card=reply.card or cards.searching(finder.site_names()),
        )

    def watch(conv: Conversation, command: commands.Command | None) -> str | Reply:
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
        text = (
            f'OK. I\'ll check {finder.site_names()} for "{started.query}"{_under(budget)} every '
            f"{hours} hours for a week, and tell you about anything new next time you message me. "
            f'Say "my watches" to see what turned up, or "stop watching {n}".'
        )
        return Reply(text, cards.notice(text, [cards.WATCHES, cards.BUY], "Watching for you"))

    def my_watches(conv: Conversation) -> Reply:
        """The buyer's watches, and anything new they found, which then counts as told."""
        running = store.watches_for(conv.owner)
        lines = ["You're watching:"] if running else ["You aren't watching anything now."]
        entries: list[str] = []
        for n, standing in enumerate(running, 1):
            days = max(1, int(-(-(standing.expires_at - store.clock()) // 86400)))
            entry = f'"{standing.query}"{_under(standing.budget_cents)}, for {days} more day(s)'
            entries.append(entry)
            lines.append(f"{n}. {entry}")
        found = 0
        # Findings of watches that have since ended are shown too, once.
        news: list[str] = []
        for standing in store.watches_for(conv.owner, running_only=False):
            # Unpriced posts stay in (watches.affordable keeps them), after the priced ones.
            hits = store.untold_hits(standing.watch_id)
            fresh = sorted(hits, key=lambda x: (x.total_cents is None, x.total_cents or 0))[:10]
            if fresh:
                found += len(fresh)
                news.append(f'New for "{standing.query}":')
                news += [
                    listing_line(i, listing, budget_cents=standing.budget_cents)
                    for i, listing in enumerate(fresh, 1)
                ]
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
        sites = finder.site_names() if finder.enabled else "other sites"
        return Reply("\n".join(lines), cards.watches(entries, found, sites))

    def unwatch(conv: Conversation, pick: int | None) -> str | Reply:
        running = store.watches_for(conv.owner)
        if not running:
            return "You aren't watching anything."
        if pick is None and len(running) == 1:
            pick = 1
        if pick is None:
            listed = my_watches(conv)
            return listed._replace(text=listed.text + '\nWhich one? Like "stop watching 2".')
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

    async def check_payments(conv: Conversation, deal_id: str = "") -> str | Reply:
        deals = store.deals(buyer=f"chat:{conv.key}")
        if not deals:
            return Reply("You haven't bought anything yet.", cards.buy())
        deal = next((d for d in deals if d.payment_status in (NOT_COLLECTED, PENDING)), deals[0])
        if deal_id:
            named = next((d for d in deals if d.deal_id == deal_id), None)
            if named is None:
                return "That order isn't yours, or it's gone."
            deal = named
        listing = store.get(deal.item_id)
        title = listing.title if listing else "item"
        picture = cards.listing_picture(listing) if listing else None
        if deal.payment_status == NOT_COLLECTED and backend.checkout.enabled:
            started = await backend.checkout.start(deal.deal_id) or deal
            label = provider_label()
            text = deal_text(started, title, label)
            if started.payment_status == PENDING and label is not None:
                card = cards.payment(
                    started.deal_id,
                    started.price_cents,
                    title,
                    label,
                    picture,
                    url=started.payment_url,
                )
                return Reply(text, card)
            return Reply(text, payment_status_card(started, title))
        if deal.payment_status == PENDING:
            try:
                deal = await backend.checkout.refresh(deal.deal_id) or deal
            except PaymentError as ex:
                return f"I couldn't check the payment just now ({ex}). Try again in a minute."
        return Reply(payment_status_text(deal, title), payment_status_card(deal, title))

    def my_orders(conv: Conversation) -> str | Reply:
        deals = store.deals(buyer=f"chat:{conv.key}")
        by_email = craigslist_buyer.buyer_lines(store, conv.owner)
        if not deals:
            text = "\n".join(by_email) if by_email else "You haven't bought anything yet."
            return Reply(text, cards.notice(text, [cards.BUY], "Your orders"))
        lines = ["Your orders:"]
        entries: list[tuple[str, str, int, str, str, str | None]] = []
        for deal in deals:
            listing = store.get(deal.item_id)
            title = listing.title if listing else deal.item_id
            status, variant = PAYMENT_STATUS.get(deal.payment_status, ("lapsed", "warning"))
            lines.append(f"- {title}: {usd(deal.price_cents)}, {status} ({deal.deal_id})")
            picture = cards.listing_picture(listing) if listing else None
            entries.append((deal.deal_id, title, deal.price_cents, status, variant, picture))
        return Reply("\n".join(lines + by_email), cards.orders(entries))

    # --- Routing ---------------------------------------------------------------------------

    def menu_card(conv: Conversation) -> MetadataContent:
        """The home screen, with counts of what this conversation has going."""
        bought = store.deals(buyer=f"chat:{conv.key}")
        found = students.school_of(store, conv.owner)
        school = schools.school_label(found) if found and onboarding.campus_mode() else ""
        return cards.menu(
            live=len(store.owned(conv.owner, ACTIVE)),
            waiting=len(numbered_waiting(conv)) or (1 if conv.unpriced else 0),
            to_pay=sum(deal.payment_status == PENDING for deal in bought),
            orders=len(bought),
            watching=len(store.watches_for(conv.owner)),
            ebay=backend.ebay is not None,
            school=school,
            meet=bool(found),
        )

    def menu_reply(conv: Conversation, text: str | None = None) -> Reply:
        """The menu. Without `text`, the greeting on first contact and plain help after."""
        if text is None:
            text = HELP if not has_history(conv) else HOW_IT_WORKS
            conv.introduced = True
        return Reply(text, menu_card(conv))

    def has_history(conv: Conversation) -> bool:
        """Whether this conversation has met Yardsale: greeted, or anything sold, searched,
        priced, bought, or watched."""
        return bool(
            conv.introduced
            or conv.seen_photos
            or conv.pricing
            or conv.numbered
            or conv.unpriced
            or conv.results
            or conv.last_query
            or conv.deals
            or conv.counter
            or store.owned(conv.owner)
            or store.deals(buyer=f"chat:{conv.key}")
            or store.watches_for(conv.owner)
            or craigslist_buyer.buyer_lines(store, conv.owner)
        )

    def buyers_view(conv: Conversation) -> Reply:
        """A seller asking about buyers ("someone messaged me, reply to them and haggle"): each
        live listing's Craigslist post and who's emailing, and that its agent answers them."""
        listings = store.owned(conv.owner)
        active = [listing for listing in listings if listing.status == ACTIVE]
        if not active:
            if craigslist_buyer.buyer_lines(store, conv.owner):
                # A buyer asking whether a Craigslist seller they had me email has answered.
                text = craigslist_buyer.negotiations_reply(store, conv.owner)
                return Reply(text, cards.notice(text, [cards.ORDERS, cards.BUY], "Negotiations"))
            nothing = "You have nothing for sale right now, so there are no buyers to answer."
            sold = craigslist_mail.seller_lines(store, listings)
            text = "\n".join([nothing, *sold, "Attach a photo to sell something."])
            return Reply(text, cards.notice(text, [cards.LISTINGS, cards.SELL], "Your buyers"))
        lines: list[str] = []
        actions: list[cards.Action] = []
        posted = False
        for n, listing in enumerate(active, 1):
            if craigslist_mail.posted_title(store, listing.item_id) is not None:
                posted = True
                continue
            say = f'"craigslist {n}"' if len(active) > 1 else '"craigslist"'
            lines.append(
                f"Your {listing.title} isn't on Craigslist yet: say {say} for a post to paste."
            )
            if not actions:
                actions.append(("Craigslist post", {"action": "craigslist", **item_ref(listing)}))
        lines += craigslist_mail.seller_lines(store, active)
        # What buyers wrote and what each item's agent answered, newest first.
        lines += exchange_lines(craigslist_mail.recent_exchanges(store, conv.owner, limit=5))
        try:
            email_on = craigslist_mail.settings_from_env() is not None
        except craigslist_mail.MailConfigError:
            email_on = False
        about = [AGENT_ANSWERS_EMAIL if email_on else EMAIL_OFF]
        if posted:
            chats = craigslist_mail.chat_notices_since(store, store.clock() - CHAT_NOTICE_WINDOW)
            if chats:
                about.append(
                    f"Craigslist says {chats} chat message{'s' if chats != 1 else ''} came in "
                    "over the last day."
                )
            about.append(CRAIGSLIST_CHAT)
        head = (
            f"Buyers for your {active[0].title}:"
            if len(active) == 1
            else "Buyers for your listings:"
        )
        text = "\n".join([head, *lines, "", *about])
        return Reply(text, cards.buyers(about[0], lines, actions))

    def exchange_lines(exchanges: list[craigslist_mail.Exchange]) -> list[str]:
        """What Craigslist buyers wrote and what each item's agent answered, newest first."""
        lines = []
        for exchange in exchanges:
            said = (
                f'- {exchange.buyer_label} on your {exchange.title}: "{_clip(exchange.buyer_said)}"'
            )
            if exchange.kind == "holding":
                lines.append(f"{said} Its agent is working on an answer; they've been told so.")
            else:
                lines.append(f'{said} I answered: "{_clip(exchange.we_said)}"')
        return lines

    def post_link(conv: Conversation, text: str) -> str | None:
        """A seller pasting their live Craigslist post's link: save which listing it is, so
        buyers' emails about it reach its agent whatever subject they write. None when the
        message has no post link or the conversation has nothing for sale."""
        links = craigslist_mail.post_links(text)
        active = store.owned(conv.owner, ACTIVE)
        if not links or not active:
            return None
        listing = craigslist_mail.match_slug(store, links[0].slug) if links[0].slug else None
        if listing is None or listing.owner != conv.owner:
            if len(active) > 1:
                return (
                    "I couldn't tell which of your listings that post is for. Use the exact "
                    'title from "craigslist" when you post, then paste the link again.'
                )
            listing = active[0]
        if not craigslist_mail.record_post_link(store, listing.item_id, text):
            return None
        return (
            f"Got it: that Craigslist post is your {listing.title}. Buyers' emails about it go "
            "straight to its agent, whatever subject they write."
        )

    def item_ref(listing: Listing) -> dict[str, str]:
        return {"item_id": listing.item_id}

    def clarify(conv: Conversation) -> Reply:
        """The answer to a message nobody understood: a short question about what this
        conversation has going, with a button for each likely meaning. The greeting only on
        first contact; plain help when there's nothing to ask about."""
        pending = store.owned(conv.owner, PROPOSED)
        if conv.counter is not None and conv.awaiting == "counter":
            listing = store.get(str(conv.counter["item_id"]))
            cents = int(conv.counter["cents"])
            title = listing.title if listing else "item"
            selection = {
                "action": "accept",
                "item_id": str(conv.counter["item_id"]),
                "cents": cents,
            }
            asked = f'Do you want the {title} at {usd(cents)}? Reply "accept", or make an offer.'
            return Reply(asked, cards.question(asked, [(f"Accept {usd(cents)}", selection)]))
        if len(pending) == 1 and conv.unpriced is None and not conv.needs_price:
            proposal = pending[0]
            conv.awaiting = "proposal"  # a "yes" now approves it, as after the proposal itself
            asked = (
                f"Did you mean your {proposal.title}, waiting for your approval at "
                f'{usd(proposal.asking_cents)}? Reply "approve" to put it up for sale, or '
                '"cancel".'
            )
            approve_it = ("Approve", {"action": "approve", **item_ref(proposal)})
            review_it = ("Review it", {"action": "review", **item_ref(proposal)})
            return Reply(asked, cards.question(asked, [approve_it, review_it]))
        # Everything else this conversation has going, most likely first: (what it's called,
        # its buttons). Each button only shows something; none approves, buys, or sets a price.
        options: list[tuple[str, list[cards.Action]]] = []
        active = store.owned(conv.owner, ACTIVE)
        if active:
            posted = next(
                (each for each in active if craigslist_mail.posted_title(store, each.item_id)),
                None,
            )
            listing = posted or active[0]
            more = f" (and {len(active) - 1} more)" if len(active) > 1 else ""
            options.append(
                (
                    f"buyers for your {listing.title}{more}",
                    [("Show buyers", {"action": "buyers"}), cards.LISTINGS],
                )
            )
        if conv.unpriced is not None or conv.needs_price:
            options.append(
                ("the photo that needs your price", [("Review it", {"action": "review"})])
            )
        if conv.results:
            options.append(
                (
                    f'your search for "{conv.last_query}"' if conv.last_query else "your search",
                    [("Back to results", {"action": "results"})],
                )
            )
        if conv.deals:
            options.append(
                (
                    f'the deals I found for "{conv.last_query}"',
                    [("Deals on other sites", {"action": "deals"})],
                )
            )
        if store.deals(buyer=f"chat:{conv.key}"):
            options.append(("your orders", [cards.ORDERS]))
        if craigslist_buyer.buyer_lines(store, conv.owner):
            options.append(
                ("your Craigslist negotiations", [("Negotiations", {"action": "negotiations"})])
            )
        if store.watches_for(conv.owner):
            options.append(("your watches", [cards.WATCHES]))
        if not options:
            if not has_history(conv):
                return menu_reply(conv)  # first contact: the greeting
            return menu_reply(conv, UNCLEAR)
        top = options[:2]
        actions = top[0][1][:2] if len(top) == 1 else [option[1][0] for option in top]
        meant = " or ".join(name for name, _ in top)
        asked = f"Did you mean {meant}? Tap one, or say it another way."
        conv.awaiting = "choice"
        conv.choices = [selection for _, selection in actions]
        return Reply(asked, cards.question(asked, actions))

    def next_steps(conv: Conversation) -> list[cards.Action]:
        """The buttons under a short message: what this conversation can do next."""
        actions: list[cards.Action] = []
        if conv.counter is not None and conv.awaiting == "counter":
            cents = int(conv.counter["cents"])
            selection = {
                "action": "accept",
                "item_id": str(conv.counter["item_id"]),
                "cents": cents,
            }
            actions.append((f"Accept {usd(cents)}", selection))
        if store.owned(conv.owner, PROPOSED) or conv.unpriced or conv.needs_price:
            actions.append(("Review my listing", {"action": "review"}))
        if conv.results and len(actions) < 2:
            actions.append(("Back to results", {"action": "results"}))
        if len(actions) < 2 and store.owned(conv.owner, ACTIVE):
            actions.append(cards.LISTINGS)
        for action in (cards.SELL, cards.BUY):
            if len(actions) < 3:
                actions.append(action)
        return actions

    async def find_from_form(
        ctx: Context, conv: Conversation, choice: dict[str, SelectionValue]
    ) -> str | Reply:
        query = " ".join(str(choice.get("query") or "").split())[:120]
        budget = _form_price(choice.get("max"))
        if not query:
            return Reply("What are you looking for?", cards.buy(budget_cents=budget))
        return find(conv, query, budget)

    async def handle_choice(
        ctx: Context, conv: Conversation, choice: dict[str, SelectionValue]
    ) -> str | Reply:
        """A tapped card button. Checked exactly like the typed command it stands for."""
        action, item_id = str(choice.get("action", "")), str(choice.get("item_id", ""))
        if conv.awaiting == "choice":  # a tap answers the question; a later "yes" doesn't
            conv.awaiting, conv.choices = None, []
        if not choice:
            return menu_reply(conv, "OK.")
        # Signing up. Nothing else can happen until a conversation has a school.
        if action == "student":
            return Reply(onboarding.ASK_FOR_EMAIL, onboarding.email_form())
        if action == "not_student":
            return onboarding.NOT_A_STUDENT_YET
        if action == "student_email":
            text, card = onboarding.join(store, conv.owner, str(choice.get("email", "")))
            student = store.student(conv.owner)
            if student is not None and not store.campus_loaded(student.school):
                # The first student from a school starts its campus lookup. That's a Claude
                # call, so it runs behind the reply rather than inside it.
                warming = asyncio.ensure_future(onboarding.warm(store, student.school))
                follow_ups.add(warming)
                warming.add_done_callback(follow_ups.discard)
            return Reply(text, card or menu_card(conv))
        # Getting around.
        if action == "menu":
            return menu_reply(conv)
        if action == "meet":
            return meetup_reply(conv)
        if action == "sell":
            return Reply(NEEDS_PHOTO, cards.sell(len(numbered_waiting(conv))))
        if action == "buy":
            return Reply(
                "What are you looking for, and what's the most you'd pay?",
                cards.buy(conv.last_query, conv.budget),
            )
        if action == "find":
            return await find_from_form(ctx, conv, choice)
        if action == "status":
            if conv.pricing:
                return Reply(ALREADY_WORKING, cards.pricing(len(conv.pricing)))
            return review(conv, "")
        if action == "listings":
            return my_listings(conv)
        if action == "buyers":
            return buyers_view(conv)
        if action == "orders":
            return my_orders(conv)
        if action == "watches":
            return my_watches(conv)
        if action == "negotiate_help":
            return Reply(craigslist_buyer.HOW_TO, cards.negotiate_help())
        if action == "negotiations":
            return craigslist_buyer.negotiations_reply(store, conv.owner)
        if action == "ebay_connect":
            return await ebay_command(conv, commands.Command("ebay_connect"))
        # Selling.
        if action in ("approve", "cancel"):
            listing = store.get(item_id)
            if listing is None or listing.owner != conv.owner or listing.status != PROPOSED:
                return "That listing isn't waiting for approval anymore."
            if action == "approve":
                return await approve(ctx, conv, listing)
            return cancel_proposal(conv, listing)
        if action == "approve_all":
            waiting = numbered_waiting(conv)
            every = commands.Command("approve", every=True)
            approved = await numbered_command(ctx, conv, every, waiting) if waiting else None
            return approved or review(conv, "")
        if action == "review":
            return review(conv, item_id)
        if action == "edit":
            listing = owned_listing(conv, item_id)
            if listing is None or listing.status != PROPOSED:
                return "That listing isn't waiting for approval anymore."
            return Reply(
                f'Set your prices for the {listing.title}, or type them, like "ask '
                f'{usd(listing.asking_cents)}, lowest {usd(listing.floor_cents)}".',
                cards.prices_form(listing),
            )
        if action == "price_form":
            n = _number(choice.get("n"))
            if n is None or str(n) not in conv.needs_price:
                return review(conv, "")
            title = str(conv.needs_price[str(n)]["item"]["title"])
            return unpriced_reply(title, n, None)
        if action == "set_price":
            try:
                asking = _form_price(choice.get("asking"))
                floor = _form_price(choice.get("lowest"))
            except PriceError as ex:
                return str(ex)
            if asking is None and floor is None:
                return 'Type your price instead, like "ask $40".'
            n = _number(choice.get("n"))
            edit = commands.Command("edit", asking_cents=asking, floor_cents=floor)
            if n is not None:
                return await edit_number(ctx, conv, n, edit, numbered_waiting(conv))
            if conv.unpriced is None:
                return review(conv, "")
            return await change_prices(ctx, conv, None, asking, floor, False)
        if action == "drop":
            n = _number(choice.get("n"))
            if n is not None:
                return cancel_numbers(conv, [n], numbered_waiting(conv))
            if conv.unpriced is not None:
                conv.unpriced = None
                return "OK, I dropped that photo. Nothing was listed."
            return review(conv, "")
        if action == "craigslist":
            return craigslist_post(conv, item_id)
        if action == "no_craigslist":
            conv.awaiting = None
            return Reply(
                CRAIGSLIST_NOT_NOW,
                cards.notice(CRAIGSLIST_NOT_NOW, [cards.LISTINGS, cards.SELL], "Listed"),
            )
        if action == "manage":
            return manage(conv, item_id)
        if action in ("unlist", "unlist_yes", "ebay_list"):
            listing = owned_listing(conv, item_id)
            if listing is None or listing.status != ACTIVE:
                return "That isn't for sale anymore."
            if action == "unlist":
                return Reply(f"Take down your {listing.title}?", cards.confirm_unlist(listing))
            if action == "unlist_yes":
                return take_down(conv, listing)
            if backend.ebay is None or not backend.ebay.connected(conv.owner):
                return 'Connect your eBay account first: say "connect ebay".'
            return await list_on_ebay(conv, listing)
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
        # Buying.
        if action == "accept":
            cents = choice.get("cents")
            counter = conv.counter
            open_price = conv.quotes.get(item_id)
            if counter is not None and counter["item_id"] == item_id:
                open_price = int(counter["cents"])
            if open_price is None or open_price != cents:
                return "That price isn't open anymore. Make a new offer, or haggle again."
            return await accept(ctx, conv, item_id, open_price)
        if action == "decline":
            conv.counter = None
            conv.awaiting = None
            return "OK. Make another offer any time."
        if action == "pick" and item_id in conv.results:
            return pick(conv, conv.results.index(item_id) + 1)
        if action == "pick":
            return "That list is out of date. Search again to see what's for sale."
        if action == "results":
            return results_again(conv)
        if action in ("buy_now", "offer_form", "offer"):
            listing_or_why = shown(conv, item_id)
            if isinstance(listing_or_why, str):
                return listing_or_why
            listing = listing_or_why
            if action == "offer_form":
                if listing.owner == conv.owner:
                    return "That's your own listing."
                return Reply(
                    f"What would you offer for the {listing.title}? It's asking "
                    f'{usd(listing.asking_cents)}. Or type it, like "offer $X".',
                    cards.offer_form(listing),
                )
            if action == "buy_now":
                return await offer_on(ctx, conv, listing.asking_cents, item_id)
            amount = _form_price(choice.get("amount"))
            if amount is None:
                return Reply("How much would you offer?", cards.offer_form(listing))
            return await offer_on(ctx, conv, amount, item_id)
        if action == "haggle_form":
            if item_id:
                listing_or_why = shown(conv, item_id)
                if isinstance(listing_or_why, str):
                    return listing_or_why
                return Reply(ASK_MAX, cards.max_form(1, item_id, conv.budget))
            if not conv.results:
                return Reply("What are you looking for?", cards.buy(conv.last_query, conv.budget))
            conv.awaiting = "max"
            return Reply(ASK_MAX, cards.max_form(len(conv.results), "", conv.budget))
        if action == "haggle_max":
            limit = _form_price(choice.get("max"))
            if limit is None:
                return Reply(ASK_MAX, cards.max_form(max(1, len(conv.results)), item_id))
            if not item_id:
                return await haggle(ctx, conv, limit, every=True)
            listing_or_why = shown(conv, item_id)
            if isinstance(listing_or_why, str):
                return listing_or_why
            if listing_or_why.owner == conv.owner:
                return "That's your own listing."
            return await haggle_with(ctx, conv, limit, [listing_or_why])
        if action == "paid":
            return await check_payments(conv, str(choice.get("deal_id") or ""))
        # Other sites.
        if action == "watch":
            return watch(conv, None)
        if action == "unwatch":
            return unwatch(conv, _number(choice.get("n")))
        if action == "deal":
            n = _number(choice.get("n"))
            if n is None or not 0 <= n < len(conv.deals):
                return "That list is out of date. Search again."
            deal = conv.deals[n]
            if choice.get("id") and str(deal.get("id")) != str(choice.get("id")):
                # A card from an earlier search: find that deal, if it's still in the list.
                found = [d for d in conv.deals if str(d.get("id")) == str(choice.get("id"))]
                if not found:
                    return "That list is out of date. Search again."
                deal = found[0]
            text = (
                f"{_link(str(deal['title']), str(deal['url']))}: {deal['price']} on {deal['site']}."
            )
            if deal.get("why"):
                text += f" {str(deal['why']).capitalize()}."
            return Reply(text, cards.deal_detail(deal))
        if action == "deals":
            card = cards.deals(conv.deals, f'Your last search: "{conv.last_query}"')
            if card is None:
                return Reply("What are you looking for?", cards.buy(conv.last_query, conv.budget))
            lines = ["Your last deals on other sites:"] + [
                f"{n}. {_link(str(d['title']), str(d['url']))}: {d['price']} on {d['site']}"
                for n, d in enumerate(conv.deals, 1)
            ]
            return Reply("\n".join(lines), card)
        return menu_reply(conv)

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

    def seller_question(conv: Conversation, text: str, kind: str | None) -> Reply | None:
        """A seller asking after their listings, read with patterns: buyers ("someone messaged
        me, reply to them and haggle") or how it's doing ("did it sell?"). Haggling words from a
        seller never start the buyer agent. None when the message isn't one of these."""
        mine = store.owned(conv.owner)
        if not mine or kind not in (None, "haggle"):
            return None
        if commands.asks_about_buyers(text):
            return buyers_view(conv)
        if kind is None and commands.asks_about_status(text):
            return as_reply(my_listings(conv))
        if kind != "haggle" or not commands.vague_haggle(text):
            return None
        active = [listing for listing in mine if listing.status == ACTIVE]
        if not active:
            return None
        if not conv.results:  # nothing searched: "haggle with them" means their buyers
            return buyers_view(conv)
        # "haggle with them" with search results and a listing of their own: ask which.
        haggle_sellers = ("Haggle with sellers", {"action": "haggle_form"})
        show_buyers = ("Show buyers", {"action": "buyers"})
        asked = (
            f"Did you mean have my buyer agent haggle with the sellers in your search for "
            f'"{conv.last_query}", or your own buyers for your {active[0].title}?'
        )
        conv.awaiting = "choice"
        conv.choices = [haggle_sellers[1], show_buyers[1]]
        return Reply(asked, cards.question(asked, [haggle_sellers, show_buyers]))

    async def handle_text(ctx: Context, conv: Conversation, text: str) -> str | Reply:
        # A bare answer ("yes", "the second one", "no") to the question an unclear message got
        # does what tapping that button does. Anything else drops the question.
        if conv.awaiting == "choice":
            offered, conv.awaiting, conv.choices = conv.choices, None, []
            picked = commands.choice_answer(text, len(offered))
            if picked == -1:
                return menu_reply(conv, "OK. Tell me in other words, or pick from the menu.")
            if picked is not None:
                return await handle_choice(ctx, conv, offered[picked])
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
        linked = post_link(conv, text)
        if linked is not None:
            return linked
        command = commands.parse(text)
        if command is not None and command.error:
            return command.error
        kind = command.kind if command else None
        pending = store.owned(conv.owner, PROPOSED)
        proposal = pending[0] if pending else None
        waiting = numbered_waiting(conv)
        selling = proposal is not None or conv.unpriced is not None or bool(waiting)
        if (
            command is not None
            and kind in ("approve", "accept", "cancel")
            and command.pick is None
            and not command.picks
            and not command.every
            and not selling
            and commands.shopping(text)
        ):
            # "sure, find me Jordans under $200" or "ok, the best price on a bike" is a search,
            # not a yes to a price on offer.
            command, kind = None, None

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
        seller_view = seller_question(conv, text, kind)
        if seller_view is not None:
            return seller_view
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
        if kind == "meetup":
            return meetup_reply(conv)
        if command is not None and kind == "unwatch":
            return unwatch(conv, command.pick)
        if command is not None and kind == "edit" and selling:
            return await change_prices(
                ctx, conv, proposal, command.asking_cents, command.floor_cents, command.approve
            )
        if command is not None and kind == "offer" and command.amount_cents is not None:
            if selling and not conv.results:
                return f'Did you mean "ask {usd(command.amount_cents)}" for your listing?'
            if not conv.results:
                # "I'd pay $200 for Jordans" with nothing found yet: a search, with that budget.
                wanted = await read(text, False)
                if wanted.intent == "buy" and wanted.query:
                    budget = to_cents(wanted.budget_usd) or command.amount_cents
                    return find(conv, wanted.query, budget)
            return await make_offer(ctx, conv, command.amount_cents, command.pick)
        # Haggling only on an explicit request ("haggle with the seller on 1"), and only over
        # Yardsale results this conversation already has.
        if command is not None and kind == "haggle" and conv.results:
            return await haggle(ctx, conv, command.amount_cents, command.pick, command.every)
        if command is not None and kind == "haggle" and not selling:
            wanted = await read(text, False)
            if wanted.intent == "buy" and wanted.query:
                return find(conv, wanted.query, to_cents(wanted.budget_usd))
            return Reply(HAGGLE_FIRST, cards.buy())
        if command is not None and kind == "accept" and command.pick is not None:
            return await accept_typed(ctx, conv, command.pick)
        if kind == "accept" or (kind == "approve" and conv.awaiting == "counter"):
            if conv.counter is not None:
                return await accept_typed(ctx, conv, None)
            if proposal is not None:
                return await approve(ctx, conv, proposal)
            return (
                "There's no counter-offer to accept. Tell me what you're looking for, like "
                '"find me a road bike under $300".'
            )
        if kind == "approve":
            if proposal is not None:
                return await approve(ctx, conv, proposal)
            if conv.awaiting == "craigslist" and "approve" not in text.lower():
                return craigslist_post(conv, None)  # "yes" to the Craigslist offer
            if conv.unpriced is not None:
                return 'Tell me your price first, like "ask $40".'
            # A "yes" buys only right after a price was offered (awaiting "counter", above); a
            # stale counter-offer from before a new search isn't taken by an "ok".
            return "There's nothing waiting for approval. Attach a photo of something to sell."
        not_now = _NOT_NOW.match(text.strip().lower()) is not None
        if (kind == "cancel" or not_now) and proposal is None and conv.awaiting == "craigslist":
            conv.awaiting = None
            return CRAIGSLIST_NOT_NOW
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
                cards.prices_form(proposal),
            )
        if command is not None and kind == "select" and conv.results and command.pick:
            return pick(conv, command.pick)
        if kind == "help" or _MENU.match(text.strip().lower()):
            return menu_reply(conv)
        if (
            command is not None
            and kind in ("amount", "budget")
            and command.amount_cents is not None
        ):
            amount = usd(command.amount_cents)
            if conv.awaiting == "max" and conv.results:  # the max an explicit haggle asked for
                return await haggle(ctx, conv, command.amount_cents)
            if selling and kind == "amount":
                return f'Did you mean "ask {amount}" or "lowest {amount}"?'
            if conv.awaiting == "counter" and kind == "amount":
                return f'Did you mean "offer {amount}"?'
            if conv.last_query and not selling:
                # "$300" or "max $300" after a search: that search again, under this budget.
                return find(conv, conv.last_query, command.amount_cents)
            if conv.results and kind == "amount":
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

        # Not a command, or not one that fits this conversation: ASI:One reads it. Its reading
        # only ever picks a view or a search: it never approves, accepts, or sets a price alone.
        wanted = await read(text, False)
        if wanted.intent == "buyers":
            return buyers_view(conv)
        if wanted.intent == "status":
            return my_listings(conv) if store.owned(conv.owner) else clarify(conv)
        if wanted.intent == "buy":  # always a search; haggling is only ever an explicit command
            return find(conv, wanted.query, to_cents(wanted.budget_usd), search_place(text))
        if wanted.intent == "sell":
            asking, floor = to_cents(wanted.asking_usd), to_cents(wanted.floor_usd)
            if selling and (asking or floor):
                edit = commands.Command("edit", asking_cents=asking, floor_cents=floor)
                by_number = await numbered_command(ctx, conv, edit, waiting)
                if by_number is not None:
                    return by_number
                return await change_prices(ctx, conv, proposal, asking, floor, False)
            missing = PHOTO_MISSING if _MENTIONS_PHOTO.search(text) else NEEDS_PHOTO
            return Reply(missing, cards.sell(len(waiting)))
        # Neither buying nor selling, as far as the reader can tell. If the words match
        # something for sale ("espresso machine?"), show it anyway.
        if text and search(for_sale(conv), text):
            return find(conv, text, None)
        if len(waiting) > 1:
            return waiting_reply(conv, waiting, "Waiting for your approval:")
        return clarify(conv)

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
                return Reply(notice + ALREADY_WORKING, cards.pricing(len(conv.pricing)))
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
                if reply.card is None and reply.elsewhere is None:
                    # Every reply is a card: a short message gets what to do next.
                    reply = reply._replace(card=cards.notice(reply.text, next_steps(conv)))
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
        return reply._replace(text=notice + reply.text)

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
                # Never waits on other sites: their deals follow as later messages.
                reply = await with_deals_elsewhere(ctx, key, reply, deliver)
            return reply
        except Exception:
            ctx.logger.exception(f"Chat message in {key[:8]} failed")
            return Reply(SOMETHING_BROKE)

    async def with_held_photos(
        ctx: Context, key: str, reply: Reply, deadline: float | None, deliver: Deliver | None
    ) -> Reply:
        """Once a new student has signed up, the photos they sent before it, in this answer:
        the sign-up line, then the proposal and its cards (not the menu). If pricing is still
        running at the deadline, the answer says so and the proposal follows when it's ready."""
        found = held.get(key)
        if found is None or store.student(f"asi1:{key}") is None:
            return reply
        del held[key]
        job, count = found
        if not job.done():
            timeout = None if deadline is None else max(0.0, deadline - loop_time())
            await asyncio.wait({job}, timeout=timeout)
        if job.done():
            if job.cancelled():
                return reply
            priced = job.result()  # handle_safely never raises
            return priced._replace(text=f"{reply.text}\n\n{priced.text}")
        if deliver is not None:
            in_background(send_held(ctx, key, job, deliver))
        return Reply(f"{reply.text}\n\n{ALREADY_WORKING}", cards.pricing(count))

    async def send_held(
        ctx: Context, key: str, job: asyncio.Future[Reply], deliver: Deliver
    ) -> None:
        try:
            await deliver(await job)
        except Exception:
            ctx.logger.exception(f"Sending a held photo's proposal in {key[:8]} failed")

    def hold(ctx: Context, key: str, msg: ChatMessage) -> None:
        """Start pricing a new student's first photos now, and keep the answer until they've
        signed up, so the sign-up cards are the only thing they see first."""
        job = asyncio.ensure_future(handle_safely(ctx, key, msg))
        held[key] = (job, min(len(photos_in(msg)), MAX_PHOTOS))
        while len(held) > MAX_REMEMBERED:
            held.popitem(last=False)

    async def answer_message(
        ctx: Context, key: str, msg: ChatMessage, deadline: float, deliver: Deliver
    ) -> Reply:
        """The answer to one message, with a new student's held photos once they've joined."""
        reply = await handle_safely(ctx, key, msg, deadline, deliver)
        try:
            reply = await with_held_photos(ctx, key, reply, deadline, deliver)
        except Exception:
            ctx.logger.exception(f"Held photos in {key[:8]} failed")
        if key in held_words and store.student(f"asi1:{key}") is not None:
            words = held_words.pop(key)
            answered = await handle_safely(ctx, key, words, deadline, deliver)
            reply = answered._replace(text=f"{reply.text}\n\n{answered.text}")
        return reply

    def loop_time() -> float:
        return asyncio.get_running_loop().time()

    async def send_reply(
        ctx: Context,
        sender: str,
        reply: Reply,
        offer_uploads: bool = False,
        sent: set[str] | None = None,
    ) -> bool:
        """Send an answer: one message with its text and card, then any later messages
        (`more`). A card this turn already sent (`sent`, by card_key) isn't sent again. Returns
        whether the first message was delivered."""
        sent = set() if sent is None else sent
        messages = [(reply.text, reply.card), *reply.more]
        delivered = True
        for n, (text, card) in enumerate(messages):
            # Anything that got here without a card gets a plain one, so every reply is a card.
            card = card or cards.notice(text, [cards.SELL, cards.BUY])
            fresh = card_key(card) not in sent
            sent.add(card_key(card))
            message = text_message(text, offer_uploads and n == 0, card if fresh else None)
            status = await ctx.send(sender, message)
            if n == 0:
                delivered = status.status != DeliveryStatus.FAILED
        return delivered

    def remember(msg_id: str, reply: Reply | None) -> None:
        answered[msg_id] = reply
        while len(answered) > MAX_REMEMBERED:
            forgotten, _ = answered.popitem(last=False)
            delivered.discard(forgotten)

    async def resend(ctx: Context, sender: str, msg_id: str, earlier: Reply) -> None:
        """ASI:One sent a message again. Its cards already went out with the first answer, so
        the same text goes again without them; ASI:One showed resent cards twice."""
        if msg_id in delivered:
            await ctx.send(sender, text_message(earlier.text))
        elif await send_reply(ctx, sender, earlier):
            delivered.add(msg_id)

    @proto.on_message(ChatMessage)
    async def on_chat(ctx: Context, sender: str, msg: ChatMessage) -> None:
        received = loop_time()
        await ctx.send(
            sender, ChatAcknowledgement(timestamp=datetime.now(UTC), acknowledged_msg_id=msg.msg_id)
        )
        msg_id = str(msg.msg_id)
        if msg_id in answered:  # a resend: answer it again rather than act twice
            earlier = answered[msg_id]
            if earlier is not None:
                await resend(ctx, sender, msg_id, earlier)
            return
        remember(msg_id, None)

        key = conversation_key(ctx, sender)
        choice = card_choice(msg)
        async with locks[key]:
            conv = load(key)
            first_message = not conv.greeted
            new_student = (
                first_message
                and onboarding.campus_mode()
                and store.student(conv.owner) is None
                and not onboarding.signing_up(choice)
            )
            if first_message:
                conv.greeted = True
                save(conv)
        # Message text can include a seller's lowest price, so it isn't logged.
        ctx.logger.info(
            f"Chat message in {key[:8]} ({len(photos_in(msg))} photo link(s)), "
            f"attachments: {attachments_summary(msg)}"
        )
        # Every card this message's answers carry, so none goes out twice for it.
        sent: set[str] = set()
        if new_student:
            # Same-school only, so the first thing to settle is which school. The welcome is the
            # whole answer, and it offers uploads. A photo sent with it is priced now, and its
            # proposal comes with the sign-up confirmation.
            if photos_in(msg):
                hold(ctx, key, msg)
            elif clean_text(msg) and not _GREETING.match(clean_text(msg)):
                held_words[key] = msg  # "find me a mini fridge": answered after sign-up
                while len(held_words) > MAX_REMEMBERED:
                    held_words.popitem(last=False)
            welcome = Reply(onboarding.ARE_YOU_A_STUDENT, onboarding.student_welcome())
            remember(msg_id, welcome)
            if await send_reply(ctx, sender, welcome, True, sent):
                delivered.add(msg_id)
            return
        starting = any(isinstance(item, StartSessionContent) for item in msg.content)
        if starting and not clean_text(msg) and not photos_in(msg) and choice is None:
            await ctx.send(sender, uploads_welcome())
            return  # ASI:One opening the session; nothing to answer yet
        # The first answer offers uploads, in the same message, so the turn stays one message.
        offer_uploads = first_message or starting

        answer_sent = asyncio.Event()

        async def deliver(later: Reply) -> None:
            await answer_sent.wait()  # proposals priced later never overtake the answer
            await send_reply(ctx, sender, later, sent=sent)

        deadline = received + REPLY_WITHIN * BATCH_WAIT_SHARE
        job = asyncio.ensure_future(answer_message(ctx, key, msg, deadline, deliver))
        left = REPLY_WITHIN - (loop_time() - received)
        done, _ = await asyncio.wait({job}, timeout=max(0.0, left))
        if done:
            reply = job.result()
        else:
            count = len(photos_in(msg))
            if count:
                reply = Reply(STILL_WORKING, cards.pricing(min(count, MAX_PHOTOS)))
            else:
                reply = Reply(WORKING, cards.working())

            async def send_when_done() -> None:
                result = await job
                remember(msg_id, result)
                if await send_reply(ctx, sender, result, sent=sent):
                    delivered.add(msg_id)
                answer_sent.set()

            follow_up = asyncio.ensure_future(send_when_done())
            follow_ups.add(follow_up)
            follow_up.add_done_callback(follow_ups.discard)
        remember(msg_id, reply)
        if await send_reply(ctx, sender, reply, offer_uploads, sent):
            delivered.add(msg_id)
        if done:
            answer_sent.set()

    @proto.on_message(ChatAcknowledgement)
    async def on_ack(ctx: Context, sender: str, msg: ChatAcknowledgement) -> None:
        pass

    return proto
