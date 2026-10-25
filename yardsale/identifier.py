"""Identifier agent: photo in, product details and an eBay search query out (Claude vision)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Literal

from pydantic import BaseModel
from uagents import Agent, Context, Protocol

from yardsale import llm, relay
from yardsale.agentverse import service_agent
from yardsale.models import Identified, IdentifyRequest, ItemDetails, ServiceError

# (image_b64, media_type, seller_note) -> details
Identify = Callable[[str, str, str], Awaitable[ItemDetails]]

SYSTEM = """You identify secondhand items from a seller's photo so they can be priced against \
recent eBay sold listings. Be specific when the photo supports it: read logos, labels, model \
numbers, and distinctive design features. Don't guess a brand or model you can't see evidence \
for; leave it empty instead."""

INSTRUCTIONS = """Identify the item for sale in this photo.

- title: a short eBay-style listing title, at most 80 characters.
- brand, model: empty strings when you can't tell.
- condition: judge only from what's visible.
- details: visible features that change the price, such as size, color, material, variant, \
accessories included, or damage.
- search_query: 3 to 8 words an eBay buyer would type to find this exact product. Put the most \
specific identifiers first (brand, model, key variant). Leave out condition words.
- generic: true when there's no identifiable brand or model, like a plain wooden bookshelf.
- confidence: how sure you are of the brand and model, or of the item type if generic."""


class _Guess(BaseModel):
    title: str
    brand: str
    model: str
    category: str
    condition: Literal["new", "like new", "good", "fair", "poor"]
    details: list[str]
    search_query: str
    generic: bool
    confidence: Literal["high", "medium", "low"]


async def identify_with_claude(image_b64: str, media_type: str, note: str) -> ItemDetails:
    text = INSTRUCTIONS + (f"\n\nThe seller added: {note}" if note.strip() else "")
    guess = await llm.ask(
        SYSTEM, [llm.image_block(image_b64, media_type), {"type": "text", "text": text}], _Guess
    )
    return ItemDetails(**guess.model_dump())


def tidy(item: ItemDetails) -> ItemDetails:
    """Short single-line title and query. An empty query falls back to the title."""
    item.title = " ".join(item.title.split())[:80] or "Unidentified item"
    item.search_query = " ".join(item.search_query.split())[:120] or item.title
    return item


def build_identifier(identify: Identify, offline: bool) -> Agent:
    agent = service_agent(
        "identifier",
        "yardsale-identifier",
        "ys-identifier",
        "Identifies a secondhand item from a photo and writes an eBay search query for it.",
        offline,
    )
    proto = Protocol(name="YardsaleIdentify", version="0.1.0")

    @proto.on_message(IdentifyRequest, replies={Identified, ServiceError})
    async def handle(ctx: Context, sender: str, msg: IdentifyRequest) -> None:
        if not relay.claim(msg.request_id):
            return  # another copy of this request arrived first (relay.py)
        try:
            item = tidy(await identify(msg.image_b64, msg.media_type, msg.note))
        except llm.LLMError as ex:  # no key, or Claude declined
            ctx.logger.warning(f"Identification failed: {ex}")
            await ctx.send(sender, ServiceError(request_id=msg.request_id, reason=str(ex)[:300]))
            return
        except Exception as ex:  # report every failure to the caller instead of going silent
            ctx.logger.exception("Identification failed")
            await ctx.send(sender, ServiceError(request_id=msg.request_id, reason=str(ex)[:300]))
            return
        ctx.logger.info(f"Identified: {item.title} (query: {item.search_query!r})")
        await ctx.send(sender, Identified(request_id=msg.request_id, item=item))

    agent.include(proto, publish_manifest=not offline)
    return agent
