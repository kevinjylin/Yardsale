"""ASI:One's own model (asi1-mini) reads what people typed in the ASI:One chat.

Photos still go to Claude (identifier.py, appraiser.py): ASI:One's docs don't list image input.
This only turns text like "sell this, at least $40, small scratch on the side" or "find me a
cheap road bike under $300" into fields. Without ASI:One, read_locally() does a rougher job with
keywords; every price either reader finds is shown to the seller before anything is listed.

Approvals, offers, and other commands never come through here (see commands.py).
"""

from __future__ import annotations

import json
import logging
import os
import re
from collections.abc import Awaitable, Callable
from typing import Literal

import aiohttp
from pydantic import BaseModel, ValidationError

from yardsale.commands import (
    AMOUNT,
    BUDGET,
    FLOOR,
    asks_about_buyers,
    asks_about_status,
    budget,
    seller_prices,
)

API_URL = "https://api.asi1.ai/v1/chat/completions"
MODEL = os.getenv("YARDSALE_ASI_MODEL", "asi1-mini")

log = logging.getLogger("yardsale.asione")

Intent = Literal["sell", "buy", "buyers", "status", "other"]


class SellerMessage(BaseModel):
    """What a message asks for. "buy" always means a search: Yardsale's listings and other
    sites, compared, with links. Haggling is never read from prose; it's an explicit command
    (commands.HAGGLE), so no reading of "best deal" or "get me a bargain" starts one.

    "buyers" and "status" are a seller asking after a listing ("someone messaged me, reply to
    them", "did it sell?"). Both only show the seller a view: no reading approves, accepts, or
    changes a price."""

    intent: Intent
    asking_usd: float | None
    floor_usd: float | None
    note: str
    query: str = ""  # buyers: what they're looking for
    budget_usd: float | None = None  # buyers: the most they want to pay


# (text, has_photo) -> what the message asks for
ReadMessage = Callable[[str, bool], Awaitable[SellerMessage]]

SYSTEM = """You read messages sent to Yardsale in the ASI:One chat. Sellers attach a photo of \
something they want to sell; Yardsale prices it from recent eBay sales and lists it. Buyers want \
to find something: Yardsale searches its own listings and other marketplaces (eBay, Craigslist, \
OfferUp, Facebook Marketplace) and compares real listings with prices and links. Messages may \
come straight from the user or from the ASI:One planner describing what the user said ("The \
buyer wants ..."). Messages can be in any language; answer in English.

- intent: "sell" if they want to sell, list, price, or get rid of an item, or they attached a \
photo without saying otherwise; "buy" if they're looking for something to buy, or want the best \
deal, the best price, or a bargain on something; "buyers" if they're a seller asking about people \
answering their listing ("someone messaged me about the bottle", "any buyers?", "did anyone \
reply?", "message them back", "reply to them and haggle"); "status" if they're a seller asking \
how their listing is doing ("did it sell?", "how's my listing?", "is my lamp still up?"); \
"other" for anything else (greetings, questions about Yardsale).
- asking_usd: the price they want to ask, in US dollars, if they gave one ("list it for $120"). \
Otherwise null.
- floor_usd: the lowest price they'd accept, if they gave one ("at least 40", "won't go below \
$90", "minimum 25"). Otherwise null. One price with no other wording ("sell this for $50") is \
asking_usd, not floor_usd.
- note: facts about the item a photo might not show (size, age, flaws, what's included, \
model number), in a few words. Empty if none. Leave out prices and requests.
- query: for "buy", the item they want in a few words, like "road bike", "Jordan 4", or \
"espresso machine". Keep words like "cheap" and sizes. Leave out "best deal", "best price", \
"good deal", and budgets. Empty otherwise.
- budget_usd: for "buy", the most they want to pay, if they said ("under $300", "my max is \
250"). Otherwise null."""

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["intent", "asking_usd", "floor_usd", "note", "query", "budget_usd"],
    "properties": {
        "intent": {"type": "string", "enum": ["sell", "buy", "buyers", "status", "other"]},
        "asking_usd": {"type": ["number", "null"]},
        "floor_usd": {"type": ["number", "null"]},
        "note": {"type": "string"},
        "query": {"type": "string"},
        "budget_usd": {"type": ["number", "null"]},
    },
}


class AsiOneError(RuntimeError):
    pass


_BUY = re.compile(
    r"\b(?:find|looking\s+for|look\s+for|search|buy|shopping\s+for|show\s+me|got\s+any|"
    r"do\s+you\s+have|anything|what'?s\s+for\s+sale|for\s+sale|get\s+me|wants|needs|"
    r"would\s+like\s+(?:a|an|some|to\s+(?:buy|find|get))|deals?\s+on|best\s+(?:deal|price)|good\s+deal|bargain|cheap(?:est)?)\b",
    re.IGNORECASE,
)
# Shopping words that aren't the item: "the best deal on", "get me a good deal on", "the buyer".
_DEAL_WORDS = re.compile(
    r"\b(?:(?:the|a)\s+)?(?:best|good|great|cheapest|lowest)\s+(?:deal|price|offer)s?"
    r"(?:\s+(?:on|for))?|\bdeals?\s+(?:on|for)\b|\bbargains?(?:\s+(?:on|for))?\b|"
    r"\bdeal\s+for\s+me\b|\bget\s+me\b|\b(?:the\s+)?(?:buyer|user|shopper|customer)\b|"
    r"\bwould\s+like\b|\bwants?\b|\bneeds?\b|\bthe\b|"
    r"\b(?:haggle|haggling|negotiate|negotiating)(?:\s+for(?:\s+me)?)?\b",
    re.IGNORECASE,
)
_SELL = re.compile(
    r"\b(?:sell|selling|list|get\s+rid\s+of|price\s+(?:this|it|my)|worth)\b", re.IGNORECASE
)
_FILLER = re.compile(
    r"^(?:hi|hey|hello|please|thanks|thank\s+you|this|it|here|here\s+it\s+is|see\s+(?:the\s+)?"
    r"(?:photo|picture|pic))$",
    re.IGNORECASE,
)
_BUY_WORDS = re.compile(
    r"\b(?:can\s+you|could\s+you|please|find|looking\s+for|look\s+for|search(?:\s+for)?|buy|"
    r"shopping\s+for|show\s+me|got\s+any|do\s+you\s+have|i\s+want|i\s+need|want|need|me|"
    r"a|an|some|any|for\s+sale|sure|ok(?:ay)?|yes|yeah|great|now|also|instead)\b",
    re.IGNORECASE,
)


def _dollars(cents: int | None) -> float | None:
    return None if cents is None else cents / 100


def read_locally(text: str, has_photo: bool) -> SellerMessage:
    """A rough reader for when ASI:One can't be asked: keywords for intent, patterns for prices.

    A photo means sell. Prices it can't read are left out, so the appraisal fills them in.
    """
    if not has_photo and asks_about_buyers(text):
        return SellerMessage(intent="buyers", asking_usd=None, floor_usd=None, note="")
    if not has_photo and asks_about_status(text):
        return SellerMessage(intent="status", asking_usd=None, floor_usd=None, note="")
    if has_photo or (_SELL.search(text) and not _BUY.search(text)):
        asking, floor, _ = seller_prices(text)
        # The note is whatever's left that isn't a price, a request, or filler.
        parts = [p.strip() for p in re.split(r"[,.;!\n]+(?!\d)", text)]
        note = ", ".join(
            p
            for p in parts
            if p
            and not _SELL.search(p)
            and not re.search(AMOUNT, p)
            and not FLOOR.search(p)
            and not _FILLER.match(p)
        )
        return SellerMessage(
            intent="sell",
            asking_usd=_dollars(asking),
            floor_usd=_dollars(floor),
            note=note[:200],
        )
    if _BUY.search(text):
        limit, _ = budget(text)
        # The planner adds sentences ("The buyer wants Jordans. Please proceed."): the item is
        # in the first one that asks for something.
        sentences = re.split(r"(?<=[.!?;])\s+", text)
        wanted = next((s for s in sentences if _BUY.search(s)), text)
        query = _BUY_WORDS.sub(" ", _DEAL_WORDS.sub(" ", BUDGET.sub(" ", wanted.lower())))
        return SellerMessage(
            intent="buy",
            asking_usd=None,
            floor_usd=None,
            note="",
            query=" ".join(re.sub(r"[^\w\s'-]", " ", query).split())[:100],
            budget_usd=_dollars(limit),
        )
    return SellerMessage(intent="other", asking_usd=None, floor_usd=None, note="")


async def ask_asi_one(text: str, has_photo: bool) -> SellerMessage:
    api_key = os.getenv("ASI_ONE_API_KEY", "").strip()
    if not api_key:
        raise AsiOneError("ASI_ONE_API_KEY is not set")
    body = {
        "model": MODEL,
        "temperature": 0,
        "messages": [
            {"role": "system", "content": SYSTEM},
            {
                "role": "user",
                "content": f"Photo attached: {'yes' if has_photo else 'no'}\n\nMessage: {text}",
            },
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "seller_message", "strict": True, "schema": SCHEMA},
        },
    }
    headers = {"Authorization": f"Bearer {api_key}"}
    timeout = aiohttp.ClientTimeout(total=20)
    async with (
        aiohttp.ClientSession(timeout=timeout) as session,
        session.post(API_URL, json=body, headers=headers) as resp,
    ):
        if resp.status != 200:
            raise AsiOneError(f"ASI:One returned {resp.status}: {(await resp.text())[:200]}")
        data = await resp.json()
    try:
        return SellerMessage.model_validate(json.loads(data["choices"][0]["message"]["content"]))
    except (KeyError, IndexError, TypeError, ValueError, ValidationError) as ex:
        raise AsiOneError(f"ASI:One's answer didn't match the schema: {ex}") from ex


async def read_seller_message(text: str, has_photo: bool) -> SellerMessage:
    """Never fails: if ASI:One is unavailable, read_locally() reads the message instead."""
    if not text.strip() or not os.getenv("ASI_ONE_API_KEY", "").strip():
        return read_locally(text, has_photo)
    try:
        message = await ask_asi_one(text, has_photo)
    except (AsiOneError, aiohttp.ClientError, TimeoutError) as ex:
        log.warning(f"Couldn't read the message with ASI:One ({ex}); reading it locally")
        return read_locally(text, has_photo)
    if has_photo and message.intent not in ("sell", "buy"):
        message.intent = "sell"
    return message
