"""Chat commands read without a model: approve, change prices, cancel, pick a result, offer,
haggle, accept, and check a payment.

A seller with several proposals waiting (one per photo) names them by number: "approve 2",
"approve 1, 3", "approve all", "lowest 2 $40", "ask 3 $60", "cancel 2", "rules 2".

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
    "haggle",
    "accept",
    "paid",
    "orders",
    "rules",
    "approve",
    "cancel",
    "unlist",
    "listings",
    "select",
    "help",
    "amount",
    "ebay_connect",
    "ebay_disconnect",
    "ebay_zip",
    "ebay_list",
    "ebay_end",
    "craigslist_end",
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
# "take it off ebay" isn't taking the price.
_NOT_TAKING = r"(?!\s+(?:off|down|away|from|back)\b)"
_ACCEPT = re.compile(
    r"^(?:i\s+)?(?:accept(?:ed)?|deal|it'?s\s+a\s+deal|i'?ll\s+take\s+it|take\s+it|agreed?)\b"
    + _NOT_TAKING
)
_ACCEPT_PICK = re.compile(
    r"^(?:i\s+)?(?:accept|buy|take)\s+(?:the\s+)?(?:#|item\s*|number\s*|no\.?\s*|option\s*)?"
    r"(\d{1,2})$"
)
HAGGLE = re.compile(
    r"\b(?:haggle|haggling|negotiate|negotiating|bargain|best\s+(?:deal|price)|"
    r"get\s+me\s+a\s+(?:good\s+)?deal|deal\s+for\s+me|talk\s+(?:them|it)\s+down)\b"
)
_PAID = re.compile(
    r"^(?:i\s+)?(?:(?:have\s+|just\s+|already\s+)?paid|done\s+paying|pay|payment|"
    r"payment\s+(?:link|status)|check\s+(?:my\s+)?payment|did\s+(?:my\s+)?payment\s+go\s+through|"
    r"how\s+do\s+i\s+pay)\b"
)
_ORDERS = re.compile(r"\bmy\s+(?:orders?|purchases?|deals?)\b|\bwhat\s+did\s+i\s+buy\b")
_RULES = re.compile(
    r"^(?:rules|my\s+rules|set\s+(?:my\s+)?(?:rules|prices)|change\s+(?:the\s+)?prices|"
    r"edit(?:\s+(?:it|prices|the\s+listing))?|prices?\s+form)$"
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
# ASI:One's planner doesn't pass a buyer's "accept" (or a tapped Buy button) on word for word. It
# writes prose, like "The buyer accepts the negotiated deal ... Please proceed to complete the
# purchase". These read such prose; the chat only uses them while a price is waiting to be taken.
_ACCEPTS = re.compile(
    r"\b(?:accept(?:s|ed|ing)?|buy\s+it|purchase\s+it|take\s+(?:it|the\s+deal)" + _NOT_TAKING + r"|"
    r"complete\s+the\s+(?:purchase|deal|order)|"
    r"proceed\s+(?:with\s+)?(?:the\s+)?(?:purchase|deal|order|checkout))\b"
)
_ZIP = r"(\d{5})(?:-\d{4})?"
_EBAY_DISCONNECT = re.compile(
    r"\b(?:disconnect|unlink|remove|forget)\s+(?:my\s+)?ebay(?:\s+account)?\b"
)
_EBAY_CONNECT = re.compile(
    r"^(?:please\s+)?(?:connect|link|hook\s+up|sign\s+in\s+(?:to|with)|log\s+in\s+(?:to|with))\s+"
    r"(?:my\s+|to\s+)?ebay(?:\s+account)?\b(?:\D{0,20}?\b" + _ZIP + r"\b)?"
)
_EBAY_ZIP = re.compile(
    r"^(?:my\s+)?(?:ebay\s+)?(?:zip|postal)(?:\s*code)?\s*(?:is\s+|:\s*|=\s*)?" + _ZIP + r"$"
)
_EBAY_LIST = re.compile(
    r"^(?:(?:also\s+)?(?:list|post|put|sell|add)\s+"
    r"(?:it\s+|them\s+|(?:my\s+)?(?:listings?|items?|stuff)\s+|" + _PICK + r"\s+)?"
    r"(?:on|to)\s+ebay(?:\s+too)?(?:\s+" + _PICK + r")?|ebay\s+" + _PICK + r")$"
)
# Ends only the eBay copy; "unlist" takes the item down everywhere.
_EBAY_END = re.compile(
    r"^(?:please\s+)?(?:stop\s+selling|take|remove|end|unlist|delist|pull)\b(?:\s+(?:it|this|that|"
    r"(?:my\s+)?listing|" + _PICK + r"))?\s+(?:on|off|from)\s+(?:of\s+)?ebay(?:\s+" + _PICK + r")?$"
)
_CRAIGSLIST_END = re.compile(
    r"\b(?:stop\s+selling|take|remove|end|unlist|delist|pull|delete)\b.*\b(?:on|off|from)\s+"
    r"craigslist\b"
)
# Several proposals at once, by number: "approve 1, 3 and 4", "approve all", "cancel 2".
_NUMBER = r"(?:#|item\s*|number\s*|no\.?\s*|photo\s*)?\d{1,2}"
_NUMBERS = _NUMBER + r"(?:\s*(?:,|&|\+|\band\b)\s*" + _NUMBER + r"|\s+" + _NUMBER + r")*"
_EVERY = (
    r"(?:them\s+all|all(?:\s+of\s+them|\s+(?:items?|listings?|photos?))?|everything|every\s*one)"
)
_APPROVE_SOME = re.compile(
    r"^(?:yes\s+to|approve|confirm|publish|list)\s+(?:(" + _EVERY + r")|(" + _NUMBERS + r"))$"
)
_CANCEL_SOME = re.compile(
    r"^(?:cancel|drop|discard|skip|don'?t\s+list)\s+(?:(" + _EVERY + r")|(" + _NUMBERS + r"))$"
)
_RULES_PICK = re.compile(
    r"^(?:rules|edit|prices?\s+form|set\s+(?:my\s+)?(?:rules|prices))\s+(?:for\s+)?" + _PICK + r"$"
)
# A price for one of several proposals: "lowest 2 $40", "ask #3 to 60", "approve 2 at $50".
_PRICE_WORD = (
    r"(?:ask(?:ing)?(?:\s+price)?|lowest(?:\s+price)?|floor(?:\s+price)?|minimum(?:\s+price)?|"
    r"min|at\s+least|approve|publish)"
)
_PRICE_PICK = re.compile(
    r"\b(" + _PRICE_WORD + r")\s+(?:for\s+|on\s+|of\s+)?(?:#|item\s*|number\s*|no\.?\s*|photo\s*)?"
    r"(\d{1,2})\s+(?=(?:(?:to|at|of|is|for|be)\s+|[=:]\s*)?\$?\s?\d)"
)
# "#2: ask $60", "item 2, lowest 40", "for 3 lowest $25"
_LEADING_PICK = re.compile(
    r"^(?:(?:for\s+)?(?:#|item\s*|number\s*|no\.?\s*|photo\s*)(\d{1,2})|(\d{1,2})\s*[:)])\s*[:,-]?\s*"
)
# "lowest $40 on 2", "ask 60 for #3"
_TRAILING_PICK = re.compile(
    r"\s+(?:on|for)\s+(?:the\s+)?(?:#|item\s*|number\s*|no\.?\s*|photo\s*)?(\d{1,2})$"
)
_NOT_ACCEPTING = re.compile(
    r"\b(?:not|don'?t|doesn'?t|won'?t|never|decline[sd]?|reject(?:s|ed)?|cancel(?:s|led)?|"
    r"find|search|look(?:ing)?\s+for|show\s+me|another|instead|other)\b|n't\b"
)


@dataclass
class Command:
    kind: Kind
    asking_cents: int | None = None
    floor_cents: int | None = None
    amount_cents: int | None = None
    pick: int | None = None  # 1-based number from a list the chat showed
    approve: bool = False  # "approve at $350": change the price, then approve
    error: str = ""  # a price that couldn't be used
    text: str = ""  # the message, for commands that also search ("haggle for a road bike")
    zip_code: str = ""  # a seller's ZIP code, for listing on eBay
    picks: tuple[int, ...] = ()  # "approve 1, 3": proposals by number
    every: bool = False  # "approve all", "cancel all"


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


def accepts_deal(text: str) -> bool:
    """True for a buyer taking the price on offer, written as prose ("The buyer accepts the deal,
    please proceed to complete the purchase"). False if it also says no, or asks for something
    else, so "don't accept" or "find me another one I'd accept" never buys."""
    lowered = " ".join(text.lower().split())
    return bool(_ACCEPTS.search(lowered)) and not _NOT_ACCEPTING.search(lowered)


def numbered_prices(text: str) -> Command | None:
    """A price for one of several waiting proposals, by number: "lowest 2 $40", "ask 3 $60",
    "#2: ask $60, lowest $45", "lowest $40 on 2", "approve 2 at $50". None if no number is named
    next to a price."""
    picks: set[int] = set()

    def drop_pick(found: re.Match[str]) -> str:
        picks.add(int(found.group(2)))
        return found.group(1) + " "

    rest = _PRICE_PICK.sub(drop_pick, text)
    if not picks:
        leading = _LEADING_PICK.search(rest)
        trailing = _TRAILING_PICK.search(rest)
        if leading is not None:
            picks.add(int(leading.group(1) or leading.group(2)))
            rest = rest[leading.end() :]
        elif trailing is not None:
            picks.add(int(trailing.group(1)))
            rest = rest[: trailing.start()]
    if not picks:
        return None
    asking, floor, error = seller_prices(rest)
    if asking is None and floor is None and not error:
        return None
    if len(picks) > 1:
        error = 'One item at a time, please, like "lowest 2 $40", then "lowest 3 $25".'
    pick = min(picks)
    if pick < 1:
        return Command("edit", error="Item numbers start at 1.")
    approve = bool(_APPROVE_TOO.search(text))
    return Command(
        "edit", asking_cents=asking, floor_cents=floor, pick=pick, approve=approve, error=error
    )


def _some(found: re.Match[str], kind: Kind) -> Command:
    if found.group(1):
        return Command(kind, every=True)
    numbers = tuple(dict.fromkeys(int(n) for n in re.findall(r"\d{1,2}", found.group(2))))
    return Command(kind, picks=numbers)


def parse(text: str) -> Command | None:
    """The command in a chat message, or None for anything else (ASI:One reads that)."""
    lowered = " ".join(text.lower().split()).rstrip(".!?")
    if not lowered:
        return None
    if _EBAY_DISCONNECT.search(lowered):
        return Command("ebay_disconnect")
    ebay = _EBAY_CONNECT.search(lowered)
    if ebay is not None:
        return Command("ebay_connect", zip_code=ebay.group(1) or "")
    ebay = _EBAY_ZIP.search(lowered)
    if ebay is not None:
        return Command("ebay_zip", zip_code=ebay.group(1))
    ebay = _EBAY_END.search(lowered)
    if ebay is not None:
        pick = next((g for g in ebay.groups() if g), None)
        return Command("ebay_end", pick=int(pick) if pick else None)
    if _CRAIGSLIST_END.search(lowered):
        return Command("craigslist_end")
    ebay = _EBAY_LIST.search(lowered)
    if ebay is not None:
        pick = next((g for g in ebay.groups() if g), None)
        return Command("ebay_list", pick=int(pick) if pick else None)
    offer = _OFFER.search(lowered)
    if offer is not None:
        amount, error = _cents(offer)
        pick = int(offer.group(2)) if offer.group(2) else None
        return Command("offer", amount_cents=amount, pick=pick, error=error)
    numbered = numbered_prices(lowered)
    if numbered is not None:
        return numbered
    asking, floor, error = seller_prices(lowered)
    if asking is not None or floor is not None or error:
        approve = bool(_APPROVE_TOO.search(lowered))
        return Command("edit", asking_cents=asking, floor_cents=floor, approve=approve, error=error)
    some = _APPROVE_SOME.search(lowered)
    if some is not None:
        return _some(some, "approve")
    some = _CANCEL_SOME.search(lowered)
    if some is not None:
        return _some(some, "cancel")
    rules = _RULES_PICK.search(lowered)
    if rules is not None:
        return Command("rules", pick=int(rules.group(1)))
    if HAGGLE.search(lowered):
        limit, error = budget(lowered)
        if limit is None and not error:
            bare = _BARE_AMOUNT.search(HAGGLE.sub("", lowered).strip(" ,.:"))
            limit, error = _cents(bare)
        return Command("haggle", amount_cents=limit, error=error, text=text)
    accept_pick = _ACCEPT_PICK.search(lowered)
    if accept_pick is not None:
        return Command("accept", pick=int(accept_pick.group(1)))
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
    if _ORDERS.search(lowered):
        return Command("orders")
    if _PAID.search(lowered):
        return Command("paid")
    if _RULES.search(lowered):
        return Command("rules")
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
