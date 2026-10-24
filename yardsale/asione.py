"""ASI:One's own model (asi1-mini) reads what a seller typed in the ASI:One chat.

Photos still go to Claude (identifier.py, appraiser.py): ASI:One's docs don't list image input.
This only turns text like "sell this, at least $40, small scratch on the side" into fields.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Awaitable, Callable
from typing import Literal

import aiohttp
from pydantic import BaseModel, ValidationError

API_URL = "https://api.asi1.ai/v1/chat/completions"
MODEL = os.getenv("YARDSALE_ASI_MODEL", "asi1-mini")

log = logging.getLogger("yardsale.asione")

Intent = Literal["sell", "buy", "other"]


class SellerMessage(BaseModel):
    intent: Intent
    asking_usd: float | None
    floor_usd: float | None
    note: str


# (text, has_photo) -> what the message asks for
ReadMessage = Callable[[str, bool], Awaitable[SellerMessage]]

SYSTEM = """You read messages sent to Yardsale in the ASI:One chat. Sellers attach a photo of \
something they want to sell; Yardsale prices it from recent eBay sales and starts an agent that \
haggles with buyers for them. Messages may come straight from the user or from the ASI:One \
planner describing what the user said. Messages can be in any language; answer in English.

- intent: "sell" if they want to sell, list, price, or get rid of an item, or they attached a \
photo without saying otherwise; "buy" if they're looking for something to buy; "other" for \
anything else (greetings, questions about Yardsale).
- asking_usd: the price they want to ask, in US dollars, if they gave one ("list it for $120"). \
Otherwise null.
- floor_usd: the lowest price they'd accept, if they gave one ("at least 40", "won't go below \
$90", "minimum 25"). Otherwise null. One price with no other wording ("sell this for $50") is \
asking_usd, not floor_usd.
- note: facts about the item a photo might not show (size, age, flaws, what's included, \
model number), in a few words. Empty if none. Leave out prices and requests."""

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["intent", "asking_usd", "floor_usd", "note"],
    "properties": {
        "intent": {"type": "string", "enum": ["sell", "buy", "other"]},
        "asking_usd": {"type": ["number", "null"]},
        "floor_usd": {"type": ["number", "null"]},
        "note": {"type": "string"},
    },
}


class AsiOneError(RuntimeError):
    pass


def fallback(has_photo: bool) -> SellerMessage:
    """What we assume when ASI:One can't be asked: a photo means sell, and no prices or notes."""
    return SellerMessage(
        intent="sell" if has_photo else "other", asking_usd=None, floor_usd=None, note=""
    )


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
    """Never fails: if ASI:One is unavailable, a photo still gets listed at the appraised price."""
    if not text.strip():
        return fallback(has_photo)
    try:
        message = await ask_asi_one(text, has_photo)
    except (AsiOneError, aiohttp.ClientError, TimeoutError) as ex:
        log.warning(f"Couldn't read the message with ASI:One ({ex}); using defaults")
        return fallback(has_photo)
    if has_photo and message.intent == "other":
        message.intent = "sell"
    return message
