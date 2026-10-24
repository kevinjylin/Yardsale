"""Chat commands read without a model: approve, change prices, cancel, pick a result, offer,
accept.

The chat matches these before asking ASI:One anything, so selling and buying work the same with
or without ASI_ONE_API_KEY, and no model's misreading can approve a listing or close a deal.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from yardsale.pricing import PriceError, parse_usd

Kind = Literal[
    "offer",
    "edit",
    "accept",
    "approve",
    "cancel",
    "unlist",
    "listings",
    "select",
    "help",
    "amount",
]

# $1,250.50 or 1250 or 40.5, not part of a longer number such as 40.999
AMOUNT = (
    r"\$?\s?(?<![\d.,])((?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d{1,2})?)(?![\d.,]*\d)"
    r"(?:\s*(?:dollars|bucks|usd))?"
)
_PICK = r"(?:#|item\s*|number\s*|no\.?\s*|option\s*)?(\d{1,2})"

FLOOR = re.compile(
    r"\b(?:lowest(?:\s+price)?|floor(?:\s+price)?|minimum(?:\s+price)?|min|at\s+least|"
    r"no\s+less\s+than|bottom(?:\s+price)?|won'?t\s+(?:go|take)\s+(?:below|under|less\s+than)|"
    r"not\s+(?:below|under|less\s+than)|reserve(?:\s+price)?)\s*(?:to|at|of|is|=|:|be)?\s*"
    + AMOUNT,
    re.IGNORECASE,
)
ASKING = re.compile(
    r"\b(?:ask(?:ing)?(?:\s+price)?|(?:list|sell)(?:\s+[a-z'-]+){0,4}?\s+(?:for|at)|"
    r"price(?:\s+it)?|make\s+it|"
    r"(?:approve|publish)(?:\s+(?:it|this|that))?\s+(?:for|at)|"
    r"change\s+(?:the\s+)?price\s+to)\s*(?:to|at|of|is|=|:)?\s*" + AMOUNT,
    re.IGNORECASE,
)
BUDGET = re.compile(
    r"\b(?:under|below|less\s+than|max(?:imum)?|up\s+to|at\s+most|budget(?:\s+of)?(?:\s+is)?|"
    r"no\s+more\s+than)\s*" + AMOUNT,
    re.IGNORECASE,
)
_OFFER = re.compile(
    r"\b(?:offer|bid|i'?ll\s+pay|i\s+can\s+pay|i'?d\s+pay|pay|how\s+about|would\s+you\s+take|"
    r"will\s+you\s+take|can\s+you\s+do|give\s+you)\b\D{0,12}?"
    + AMOUNT
    + r"(?:\s*(?:on|for)\s*(?:the\s+)?"
    + _PICK
    + r"\b)?",
    re.IGNORECASE,
)
_ACCEPT = re.compile(
    r"^(?:i\s+)?(?:accept(?:ed)?|deal|it'?s\s+a\s+deal|i'?ll\s+take\s+it|take\s+it|agreed?)\b"
)
_APPROVE = re.compile(
    r"^(?:yes|yep|yeah|y|ok(?:ay)?|sure|approve[ds]?|confirm(?:ed)?|list\s+it|publish(?:\s+it)?|"
    r"go\s+ahead|looks\s+good|sounds\s+good|do\s+it|lgtm|go\s+live)\b|\bapprove\b"
)
_APPROVE_TOO = re.compile(r"\b(?:approve[ds]?|confirm(?:ed)?|publish|go\s+live)\b")
_CANCEL = re.compile(
    r"^(?:no|nope|cancel|never\s*mind|don'?t\s+(?:list|sell)\s+it|forget\s+it|stop)\b|"
    r"\bcancel\b"
)
_UNLIST = re.compile(
    r"\b(?:unlist|take\s+(?:it\s+|that\s+|this\s+)?down|remove\s+(?:my\s+|the\s+)?listing|"
    r"stop\s+selling)\b(?:\s*" + _PICK + r")?"
)
_LISTINGS = re.compile(r"\b(?:my\s+(?:listings?|items|stuff|sales)|what\s+am\s+i\s+selling)\b")
_SELECT = re.compile(
    r"^(?:(?:tell\s+me\s+(?:more\s+)?about|show(?:\s+me)?|details\s+(?:for|on|of)|"
    r"more\s+(?:on|about)|pick|choose|select|open)\s+)?(?:the\s+)?" + _PICK + r"$"
)
_HELP = re.compile(r"^(?:help|\?|what\s+can\s+you\s+do|commands|how\s+does\s+this\s+work)\b")
_BARE_AMOUNT = re.compile(r"^" + AMOUNT + r"$", re.IGNORECASE)


@dataclass
class Command:
    kind: Kind
    asking_cents: int | None = None
    floor_cents: int | None = None
    amount_cents: int | None = None
    pick: int | None = None  # 1-based number from a list the chat showed
    approve: bool = False  # "approve at $350": change the price, then approve
    error: str = ""  # a price that couldn't be used


def _cents(match: re.Match[str] | None) -> tuple[int | None, str]:
    if match is None:
        return None, ""
    try:
        return parse_usd(match.group(1)), ""
    except PriceError as ex:
        return None, str(ex)


def seller_prices(text: str) -> tuple[int | None, int | None, str]:
    """(asking, floor, error) from text like "list it for $60, at least 45"."""
    floor_match = FLOOR.search(text)
    floor, floor_error = _cents(floor_match)
    # Look for the asking price outside the floor phrase, so "lowest price 40" isn't an ask.
    rest = text if floor_match is None else text[: floor_match.start()] + text[floor_match.end() :]
    asking, asking_error = _cents(ASKING.search(rest))
    return asking, floor, floor_error or asking_error


def budget(text: str) -> tuple[int | None, str]:
    return _cents(BUDGET.search(text))


def parse(text: str) -> Command | None:
    """The command in a chat message, or None for anything else (ASI:One reads that)."""
    lowered = " ".join(text.lower().split()).rstrip(".!?")
    if not lowered:
        return None
    offer = _OFFER.search(lowered)
    if offer is not None:
        amount, error = _cents(offer)
        pick = int(offer.group(2)) if offer.group(2) else None
        return Command("offer", amount_cents=amount, pick=pick, error=error)
    asking, floor, error = seller_prices(lowered)
    if asking is not None or floor is not None or error:
        approve = bool(_APPROVE_TOO.search(lowered))
        return Command("edit", asking_cents=asking, floor_cents=floor, approve=approve, error=error)
    unlist = _UNLIST.search(lowered)
    if unlist is not None:
        return Command("unlist", pick=int(unlist.group(1)) if unlist.group(1) else None)
    if _ACCEPT.search(lowered):
        return Command("accept")
    if _APPROVE.search(lowered):
        return Command("approve")
    if _CANCEL.search(lowered):
        return Command("cancel")
    if _LISTINGS.search(lowered):
        return Command("listings")
    select = _SELECT.search(lowered)
    if select is not None:
        return Command("select", pick=int(select.group(1)))
    if _HELP.search(lowered):
        return Command("help")
    bare = _BARE_AMOUNT.search(lowered)
    if bare is not None:
        amount, error = _cents(bare)
        return Command("amount", amount_cents=amount, error=error)
    return None
