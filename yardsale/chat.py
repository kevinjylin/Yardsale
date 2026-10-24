"""The Market's ASI:One front door: Agent Chat Protocol messages from sellers.

A seller attaches a photo in ASI:One and can type anything else ("at least $40, small dent").
ASI:One's model reads the text (asione.py). The photo goes to the Identifier, Researcher, and
Appraiser, and the reply says what the item is, its price, and its new item agent's address.

ASI:One sends an upload as a ResourceContent whose file sits in Agentverse storage, readable only
by the agent it was sent to, or as a web link in the resource or in the text when its planner
relays the message. The planner also repeats earlier links in later messages, so each conversation
remembers which photos it has already handled and what happened to them.

The planner stops waiting for a reply after about 60 seconds, and pricing can take longer (the
eBay search alone can take a minute). So the Market always answers within REPLY_WITHIN seconds:
with the result if it's ready, or with "still working" and then the result as a second message.
"""

from __future__ import annotations

import asyncio
import os
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urlparse
from uuid import uuid4

from uagents import Context, Protocol
from uagents_core.contrib.protocols.chat import (
    ChatAcknowledgement,
    ChatMessage,
    MetadataContent,
    ResourceContent,
    StartSessionContent,
    TextContent,
    chat_protocol_spec,
)

from yardsale.asione import ReadMessage, SellerMessage
from yardsale.common import PipelineError
from yardsale.models import Appraisal, ItemDetails, ListPhoto
from yardsale.photos import PhotoError, download_from_storage, download_photo, prepare_photo

REPLY_WITHIN = 40.0  # seconds; ASI:One's planner gives up on a reply after about 60


@dataclass
class Sold:
    item: ItemDetails
    appraisal: Appraisal
    address: str
    asking_cents: int
    floor_cents: int


@dataclass(frozen=True)
class Photo:
    url: str  # a web link, or the resource URI (agent-storage://...) of a chat upload
    mime_type: str
    resource_id: str | None = None  # the Agentverse storage asset of a chat upload


# Prices the photo and starts its item agent. Raises PipelineError with a message for the seller.
Sell = Callable[[Context, ListPhoto], Awaitable[Sold]]
# Returns the photo's bytes. Raises PhotoError with a message for the seller.
FetchPhoto = Callable[[Context, Photo], Awaitable[bytes]]

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif", ".gif"}
_MARKDOWN_LINK = re.compile(r"!?\[[^\]]*\]\((https?://[^\s)]+)\)")
_BARE_LINK = re.compile(r"https?://[^\s)\]<>\"']+")
_MENTION = re.compile(r"^\s*@\S+\s*")
_MENTIONS_PHOTO = re.compile(
    r"\b(photos?|pictures?|pics?|images?|upload(ed)?|attach(ed|ment)?)\b", re.IGNORECASE
)

HELP = (
    "Hi, I'm Yardsale. Attach a photo of something you want to sell, and I'll work out what it "
    "is, price it from recent eBay sales, and start an agent that haggles with buyers for you. "
    'You can add your own price too, like "at least $40".'
)
NEEDS_PHOTO = "Attach a photo of the item and I'll price it from recent eBay sales and list it."
PHOTO_MISSING = (
    "Your photo didn't come through with that message. Please attach it again, as a file or a "
    "direct image link."
)
BUYING_NOT_READY = (
    "Buying through this chat isn't ready yet. Right now I help people sell: attach a photo of "
    "something you want to sell and I'll list it."
)
STILL_WORKING = (
    "Got your photo. I'm identifying it and checking recent eBay sales now, which can take a "
    "minute or two. I'll send the price and your item agent's address here when it's live."
)
ALREADY_WORKING = (
    "I'm still pricing that photo. I'll send the price and your item agent's address here as "
    "soon as it's live."
)
SKIPPED_PHOTO = (
    "I take one photo per message, so I skipped that one. Attach it again on its own to list it."
)
SOMETHING_BROKE = "Sorry, something went wrong on my side. Please send that again."


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
            mime_type = resource.metadata.get("mime_type", "")
            if mime_type and not mime_type.startswith("image/"):
                continue
            # An upload's URI is usually agent-storage://..., which only the storage API can open.
            url = _resource_url(resource.uri, resource.metadata) or resource.uri.strip()
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
    """The seller's words, without photo links or the @agent mention direct chats start with."""
    text = _MENTION.sub("", msg.text())
    text = _MARKDOWN_LINK.sub(" ", text)
    text = _BARE_LINK.sub(lambda m: " " if _is_image_link(_trim(m.group(0))) else m.group(0), text)
    return " ".join(text.split())


UPLOADS_WELCOME = MetadataContent(type="metadata", metadata={"attachments": "true"})


def text_message(text: str, offer_uploads: bool = False) -> ChatMessage:
    content: list[TextContent | MetadataContent] = [TextContent(type="text", text=text)]
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


def usd(cents: int) -> str:
    return f"${cents // 100:,}" if cents % 100 == 0 else f"${cents / 100:,.2f}"


def to_cents(dollars: float | None) -> int | None:
    return round(dollars * 100) if dollars is not None and dollars > 0 else None


def listed_text(sold: Sold, wanted: SellerMessage) -> str:
    appraisal = sold.appraisal
    lines = [f"Your {sold.item.title} is for sale."]
    ask = f"Asking price: {usd(sold.asking_cents)}"
    if wanted.asking_usd and appraisal.asking_cents:
        ask += f" (your price; recent eBay sales suggest {usd(appraisal.asking_cents)})"
    lines.append(f"{ask}. Lowest price you'll take: {usd(sold.floor_cents)} (buyers never see it).")
    if appraisal.asking_cents is None:
        lines.append("I couldn't find close eBay sales, so I used your price.")
    else:
        spread = ""
        if appraisal.low_cents is not None and appraisal.high_cents is not None:
            spread = f", most between {usd(appraisal.low_cents)} and {usd(appraisal.high_cents)}"
        lines.append(f"Based on {appraisal.basis}{spread}.")
        lines.append("Closest sales:")
        lines += [
            f"- [{match.comp.title[:70]}]({match.comp.url}): {usd(match.comp.price_cents)}"
            for match in appraisal.matches[:3]
        ]
    lines.append(
        f"Your item agent is live at {sold.address}. It counters low offers and accepts anything "
        "at or above your lowest price."
    )
    return "\n".join(lines)


def build_chat_protocol(sell: Sell, read: ReadMessage, fetch: FetchPhoto) -> Protocol:
    proto = Protocol(spec=chat_protocol_spec)
    # conversation -> photo link -> its listing job, so a repeated link reports instead of relists
    handled: dict[str, dict[str, asyncio.Future[str]]] = {}
    follow_ups: set[asyncio.Task[None]] = set()  # keeps background replies from being collected

    async def list_photo(ctx: Context, conversation: str, photo: Photo, text: str) -> str:
        try:
            wanted = await read(text, True)
            data = await fetch(ctx, photo)
            image_b64, media_type = await asyncio.to_thread(prepare_photo, data)
            sold = await sell(
                ctx,
                ListPhoto(
                    listing_ref=uuid4().hex,
                    seller_id=f"asi1:{conversation}",
                    image_b64=image_b64,
                    media_type=media_type,
                    note=wanted.note,
                    asking_cents=to_cents(wanted.asking_usd),
                    floor_cents=to_cents(wanted.floor_usd),
                ),
            )
        except (PhotoError, PipelineError) as ex:
            ctx.logger.warning(f"Chat listing for {conversation} failed: {ex}")
            return f"I couldn't list this. {ex}"
        except Exception:
            ctx.logger.exception(f"Chat listing for {conversation} failed")
            return SOMETHING_BROKE
        return listed_text(sold, wanted)

    async def answer_without_photo(
        text: str, repeated: list[asyncio.Future[str]], wanted: SellerMessage
    ) -> str:
        # The planner repeats earlier links, and sellers ask "is it done?", so a message that
        # only repeats a photo is about that photo, unless they've moved on to buying.
        if repeated and wanted.intent != "buy":
            job = repeated[0]
            return job.result() if job.done() else ALREADY_WORKING
        if wanted.intent == "sell":
            return PHOTO_MISSING if _MENTIONS_PHOTO.search(text) else NEEDS_PHOTO
        return BUYING_NOT_READY if wanted.intent == "buy" else HELP

    async def respond(
        ctx: Context,
        sender: str,
        job: asyncio.Future[str] | None,
        extra: str,
        text: str,
        repeated: list[asyncio.Future[str]],
        deadline: float,
    ) -> str:
        if job is None:
            return await answer_without_photo(text, repeated, await read(text, False))
        # The deadline counts from when the message arrived, so reading the text with ASI:One
        # (up to 20 s) can't push the reply past the planner's patience.
        timeout = max(0.0, deadline - asyncio.get_running_loop().time())
        done, _ = await asyncio.wait({job}, timeout=timeout)
        if done:
            return job.result() + extra

        async def send_when_done() -> None:
            await ctx.send(sender, text_message(await job))

        follow_up = asyncio.ensure_future(send_when_done())
        follow_ups.add(follow_up)
        follow_up.add_done_callback(follow_ups.discard)
        return STILL_WORKING + extra

    @proto.on_message(ChatMessage)
    async def on_chat(ctx: Context, sender: str, msg: ChatMessage) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + REPLY_WITHIN
        await ctx.send(
            sender, ChatAcknowledgement(timestamp=datetime.now(UTC), acknowledged_msg_id=msg.msg_id)
        )
        # One ASI:One conversation is one session. The planner relays many users from one
        # address, so the sender alone doesn't identify a conversation.
        conversation = f"session:{ctx.session}" if ctx.session else f"sender:{sender}"
        first_message = conversation not in handled
        jobs = handled.setdefault(conversation, {})
        new: list[Photo] = []
        repeated: list[asyncio.Future[str]] = []
        for photo in photos_in(msg):
            if photo.url in jobs:
                repeated.append(jobs[photo.url])
            elif all(photo.url != other.url for other in new):
                new.append(photo)
        text = clean_text(msg)
        if any(isinstance(item, StartSessionContent) for item in msg.content):
            await ctx.send(sender, uploads_welcome())
            if not text and not new and not repeated:
                return  # ASI:One opening the session; nothing to answer yet
        ctx.logger.info(
            f"Chat from {conversation}: {len(new)} new photo(s), {len(repeated)} repeated, "
            f"text {text[:120]!r}"
        )
        # Start the job before anything slow so a redelivered message can't list the photo twice.
        job: asyncio.Future[str] | None = None
        extra = ""
        if new:
            job = asyncio.ensure_future(list_photo(ctx, conversation, new[0], text))
            jobs[new[0].url] = job
            for photo in new[1:]:
                skipped: asyncio.Future[str] = loop.create_future()
                skipped.set_result(SKIPPED_PHOTO)
                jobs[photo.url] = skipped
            if len(new) > 1:
                extra = "\n\n(I used the first photo. Send other items one message at a time.)"
        try:
            reply = await respond(ctx, sender, job, extra, text, repeated, deadline)
        except Exception:
            ctx.logger.exception(f"Chat message from {conversation} failed")
            reply = SOMETHING_BROKE
        await ctx.send(sender, text_message(reply, offer_uploads=first_message))

    @proto.on_message(ChatAcknowledgement)
    async def on_ack(ctx: Context, sender: str, msg: ChatAcknowledgement) -> None:
        pass

    return proto
