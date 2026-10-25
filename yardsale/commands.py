"""Chat commands read without a model: approve, change prices, cancel, pick a result, offer,
haggle, accept, and check a payment.

Shopping words ("best deal", "best price", "get me a good deal", "bargain") are a search, never
a haggle: they reach ASI:One's reader, and the chat searches Yardsale and other sites. Haggling
is opt-in. It takes an explicit request aimed at Yardsale's sellers ("haggle with the seller on
1", "negotiate with item 2", "haggle $300"), and the chat only acts on it once the buyer has
Yardsale results.

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
    "budget",
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
    "elsewhere",
    "watch",
    "watches",
    "unwatch",
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
# "deal" only on its own: "deal on jordans under $200" is a search.
_ACCEPT = re.compile(
    r"^(?:i\s+)?(?:accept(?:ed)?|deal$|it'?s\s+a\s+deal|i'?ll\s+take\s+it|take\s+it|agreed?)\b"
    + _NOT_TAKING
)
_ACCEPT_PICK = re.compile(
    r"^(?:i\s+)?(?:accept|buy|take)\s+(?:the\s+)?(?:#|item\s*|number\s*|no\.?\s*|option\s*)?"
    r"(\d{1,2})$"
)
# Haggling, asked for in so many words. "best deal" or "bargain" alone is shopping (a search).
_HAGGLE_VERB = r"(?:haggle|haggling|negotiate|negotiating)"
_HAGGLE_PICK = r"(?:#|item\s*|number\s*|no\.?\s*|option\s*|listing\s*)?\d{1,2}\b"
_HAGGLE_TARGET = (
    r"(?:(?:the|that|this|these|those|their|its|my|your)\s+)?(?:yardsale(?:'?s)?\s+)?"
    r"(?:sellers?(?:'?s?\s+agents?)?|agents?|listings?|items?|them|it|all(?:\s+of\s+them)?|"
    r"yardsale)\b|" + _HAGGLE_PICK
)
HAGGLE = re.compile(
    # "haggle", "haggle $340", "haggle for me, max $320": the whole message, after a search.
    r"^" + _HAGGLE_VERB + r"(?:\s+for\s+me)?(?:\s*[,:]?\s*(?:(?:my\s+)?max(?:imum)?(?:\s+is)?|"
    r"up\s+to|at\s+most|under|budget(?:\s+of)?)?\s*" + AMOUNT + r")?$|"
    # "haggle with the seller", "negotiate with item 2", "haggle for me on 1"
    r"\b" + _HAGGLE_VERB + r"(?:\s+(?:for\s+me|down|the\s+price))?\s+(?:with|on|over|for)\s+"
    r"(?:" + _HAGGLE_TARGET + r")|"
    # "talk the seller down", "have your agent haggle"
    r"\btalk\s+(?:the\s+sellers?|them|it)\s+down\b|\b(?:your|yardsale'?s?)\s+(?:buyer\s+)?"
    r"agent\s+(?:to\s+)?" + _HAGGLE_VERB,
    re.IGNORECASE,
)
_HAGGLE_NUMBER = re.compile(
    r"(?:\b(?:on|for|with|over)\s+(?:the\s+)?(?:#|item\s*|number\s*|no\.?\s*|option\s*|listing\s*)?"
    r"|(?:#|\bitem\s*|\bnumber\s*|\bno\.?\s*|\boption\s*|\blisting\s*))"
    r"(?<!\$)(\d{1,2})\b(?!\s*(?:dollars|bucks|usd)|\.\d|,\d{3})"
)
_HAGGLE_ALL = re.compile(r"\b(?:sellers|agents|listings|items|them|all)\b")
# A buyer still shopping: these never accept a price or make an offer, whatever else they say.
_SHOPPING = re.compile(
    r"\b(?:find|looking\s+for|look\s+for|search(?:ing)?|shopping|show\s+me|need|needs|"
    r"wants?\s+(?!to\b)|wants?\s+to\s+(?:find|search|look|shop|get\s+(?:a|an|some))|"
    r"deals?\s+(?:on|for)|best\s+(?:deal|price)|good\s+deal|cheap(?:er|est)?|bargain|for\s+sale)\b|"
    r"\b(?:under|below|less\s+than|max(?:imum)?|up\s+to|at\s+most|no\s+more\s+than|budget)\s*"
    r"\$?\s?\d"
)
# "max $300", "under 250": a budget for the last search, never an offer.
_BARE_BUDGET = re.compile(
    r"^(?:(?:my\s+)?(?:max(?:imum)?|budget)(?:\s+is)?|under|below|less\s+than|up\s+to|"
    r"at\s+most|no\s+more\s+than)\s*" + AMOUNT + r"$",
    re.IGNORECASE,
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
# Other sites: search them once now (elsewhere.py), or keep watching them (watches.py).
_SITES = r"(?:ebay|craigslist|offer\s?up|facebook(?:\s+marketplace)?|fb\s+marketplace)"
_OTHER_SITES = re.compile(
    r"\b(?:other|more|different)\s+(?:sites?|websites?|places|marketplaces?|apps?)\b|"
    r"\belsewhere\b|\banywhere\s+else\b"
)
_SEARCH_SITES = re.compile(
    r"\b(?:search|check|look|try|browse|scan|find|shop)\b.{0,60}?\b" + _SITES + r"\b"
)
# "list it on other sites" and "check my ebay listings" are about selling, not searching.
_NOT_SEARCHING = re.compile(
    r"\b(?:list|listings?|post|sell|selling|put|unlist|delist|remove|end|connect)\b|"
    r"\bmy\s+" + _SITES
)
_WATCH = re.compile(
    r"^(?:please\s+)?(?:(?:can\s+you\s+|could\s+you\s+)?(?:watch|look)\s+out\s+for|"
    r"(?:can\s+you\s+|could\s+you\s+)?watch\s+for|"
    r"keep\s+(?:looking|watching|checking|searching|an\s+eye\s+out)|"
    r"set\s+up\s+a\s+(?:watch|alert|saved\s+search))\b|^watch(?:\s+(?:it|that|this|them))?$"
)
_WATCHES = re.compile(
    r"\b(?:my\s+(?:watch(?:es|list)?|alerts|saved\s+searches|standing\s+searches)|"
    r"what\s+am\s+i\s+watching)\b"
)
_UNWATCH = re.compile(
    r"^(?:please\s+)?(?:(?:stop|cancel|end|delete|remove)\s+(?:watching|(?:the\s+|my\s+)?"
    r"(?:watch(?:es)?|alerts?|saved\s+search(?:es)?))|unwatch)\b(?:\s+(?:for\s+)?(?:the\s+)?"
    + _PICK
    + r"\b)?"
)
_NOT_ACCEPTING = re.compile(
    r"\b(?:not|don'?t|doesn'?t|won'?t|never|decline[sd]?|reject(?:s|ed)?|cancel(?:s|led)?|"
    r"find|search|look(?:ing)?\s+for|show\s+me|another|instead|other|wants?\s+(?!to\b)|"
    r"wants?\s+to\s+(?:find|search|look|shop|get\s+(?:a|an|some))|deals?\s+on|for\s+sale|"
    r"cheap(?:er|est)?|best\s+(?:deal|price)\s+(?:on|for))\b|n't\b|"
    r"\b(?:under|below|less\s+than)\s*\$?\s?\d"
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


def shopping(text: str) -> bool:
    """True when a message is still shopping: looking for something, a deal on it, or a budget
    ("find me", "best price on", "the buyer wants Jordans under $200"). The chat searches for
    these, so they never accept a price, approve, or make an offer."""
    return bool(_SHOPPING.search(" ".join(text.lower().split())))


def accepts_deal(text: str) -> bool:
    """True for a buyer taking the price on offer, written as prose ("The buyer accepts the deal,
    please proceed to complete the purchase"). False if it also says no, or asks for something
    else, so "don't accept", "find me another one I'd accept", or "the buyer wants Jordans under
    $200" never buys."""
    lowered = " ".join(text.lower().split())
    return bool(_ACCEPTS.search(lowered)) and not _NOT_ACCEPTING.search(lowered)


# A seller asking about the people answering a listing: "someone messaged me", "any buyers?",
# "did anyone reply", "message them back", "reply to them and haggle". These only ever show the
# seller a view; the item's agent answers Craigslist emails by itself.
_SOMEONE = r"(?:someone|somebody|anyone|anybody|a\s+buyer|buyers?|people|a\s+(?:guy|girl|person))"
_BUYER_PERSON = (
    r"(?:them|him|her|the\s+buyers?|my\s+buyers?|buyers|the\s+(?:guy|girl|person)|"
    r"whoever\s+(?:messaged|emailed|wrote))"
)
_BUYERS = re.compile(
    r"\b" + _SOMEONE + r"\s+(?:has\s+|have\s+|just\s+|already\s+)?"
    r"(?:messaged|emailed|texted|replied|responded|wrote|written|contacted|reached\s+out|dm'?d|"
    r"offered|made\s+an\s+offer|is\s+interested|wants\s+(?:it|to\s+buy))\b|"
    r"\b(?:any|got\s+any|have\s+i\s+got\s+any|do\s+i\s+have\s+any|new)\s+"
    r"(?:buyers?|offers?|messages?|replies|responses|emails?|interest|bites)\b|"
    r"\bdid\s+(?:anyone|anybody|someone|somebody|a\s+buyer|any\s+buyers?)\s+"
    r"(?:message|email|reply|respond|write|offer|text|contact|bite)\b|"
    r"\b(?:message|text|email|reply\s+to|respond\s+to|answer|write\s+(?:to|back\s+to)|"
    r"get\s+back\s+to)\s+" + _BUYER_PERSON + r"\b|"
    r"\b(?:haggle|negotiate|bargain)\s+with\s+(?:the\s+buyers?|my\s+buyers?|buyers|"
    r"the\s+(?:guy|girl|person)|whoever)\b|"
    r"\bwho(?:'s|\s+is|\s+has)?\s+(?:interested|messaged|emailed|offered)\b|"
    r"\b(?:my|the)\s+buyers\b"
)
# "haggle with them" is a buyer's haggle after a search, or a seller's buyers: the chat decides.
_VAGUE_HAGGLE = re.compile(
    r"^(?:(?:can\s+you|please|and)\s+)?(?:haggle|negotiate|bargain)\s+with\s+(?:them|him|her)$"
)
_STATUS = re.compile(
    r"\b(?:did|has|have)\s+(?:it|they|anything|(?:my|the)\s+[\w'-]+(?:\s+[\w'-]+)?)\s+"
    r"(?:sold|sell)\b|"
    r"\b(?:is|are)\s+(?:it|they|(?:my|the)\s+[\w'-]+(?:\s+[\w'-]+)?)\s+"
    r"(?:sold|still\s+(?:up|listed|for\s+sale|on\s+(?:sale|craigslist|yardsale)))\b|"
    r"\bhow(?:'s|\s+is|\s+are)\s+my\s+(?:listings?|items?|stuff|sales?|[\w'-]+)\b|"
    r"\b(?:any\s+(?:luck|news|updates?)|status\s+of\s+my|(?:listing|my)\s+status|"
    r"what'?s\s+the\s+status)\b"
)


def _flat(text: str) -> str:
    return " ".join(text.lower().split()).rstrip(".!?")


def asks_about_buyers(text: str) -> bool:
    """True for a seller asking about buyers answering a listing ("someone messaged me about the
    bottle, reply to them and haggle")."""
    return bool(_BUYERS.search(_flat(text)))


def vague_haggle(text: str) -> bool:
    """True for "haggle with them", which can mean a buyer's search results or a seller's
    buyers."""
    return bool(_VAGUE_HAGGLE.search(_flat(text)))


def asks_about_status(text: str) -> bool:
    """True for a seller asking how a listing is doing ("did it sell?", "how's my listing")."""
    return bool(_STATUS.search(_flat(text)))


_YES_ANSWER = re.compile(
    r"^(?:yes|yep|yeah|yup|ya|y|sure|ok(?:ay)?|correct|right|exactly|that'?s\s+(?:it|right)|"
    r"that\s+one)(?:\s*,?\s*(?:please|thanks?|thank\s+you))?$"
)
_NO_ANSWER = re.compile(
    r"^(?:no|nope|nah|neither|none|neither\s+one|none\s+of\s+(?:them|those)|something\s+else|"
    r"not\s+that|no\s+thanks?)$"
)
_ORDINALS = {"first": 1, "1st": 1, "1": 1, "second": 2, "2nd": 2, "2": 2, "third": 3, "3rd": 3}
_ORDINAL_ANSWER = re.compile(
    r"^(?:the\s+)?(?:#|option\s*|number\s*)?(first|second|third|1st|2nd|3rd|[123]|last)"
    r"(?:\s+(?:one|option|button))?(?:\s*,?\s*please)?$"
)


def choice_answer(text: str, count: int) -> int | None:
    """A bare answer to a question with `count` choices: the 0-based choice ("yes" is the first,
    "the second one" the second), -1 for "no" or "something else", or None when the message
    says something else."""
    lowered = _flat(text)
    if _YES_ANSWER.match(lowered):
        return 0
    if _NO_ANSWER.match(lowered):
        return -1
    found = _ORDINAL_ANSWER.match(lowered)
    if found is None:
        return None
    word = found.group(1)
    index = count - 1 if word == "last" else _ORDINALS[word] - 1
    return index if 0 <= index < count else None


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


def _other_sites(text: str, lowered: str) -> Command | None:
    """Searching or watching other sites, and the watches themselves. The search words are read
    from the message later (elsewhere.search_words); the budget is read here."""
    unwatch = _UNWATCH.search(lowered)
    if unwatch is not None:
        return Command("unwatch", pick=int(unwatch.group(1)) if unwatch.group(1) else None)
    if _WATCHES.search(lowered):
        return Command("watches")
    kind: Kind | None = None
    if _WATCH.search(lowered):
        kind = "watch"
    elif (_OTHER_SITES.search(lowered) or _SEARCH_SITES.search(lowered)) and not (
        _NOT_SEARCHING.search(lowered)
    ):
        kind = "elsewhere"
    if kind is None:
        return None
    limit, error = budget(lowered)
    return Command(kind, amount_cents=limit, error=error, text=text)


def _haggle(text: str, lowered: str) -> Command:
    """An explicit request to haggle with Yardsale's sellers: which item (a number), or all of
    them, and the buyer's max if it's given."""
    number = _HAGGLE_NUMBER.search(lowered)
    pick = int(number.group(1)) if number is not None else None
    rest = lowered if number is None else lowered[: number.start()] + lowered[number.end() :]
    limit, error = budget(rest)
    if limit is None and not error:
        amount = re.search(AMOUNT, re.sub(_HAGGLE_VERB, " ", rest))
        if amount is not None and ("$" in amount.group(0) or len(amount.group(1)) > 2):
            limit, error = _cents(amount)
    every = pick is None and bool(_HAGGLE_ALL.search(rest))
    return Command("haggle", amount_cents=limit, error=error, text=text, pick=pick, every=every)


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
    other_sites = _other_sites(text, lowered)
    if other_sites is not None:
        return other_sites
    offer = _OFFER.search(lowered)
    if offer is not None and not _SHOPPING.search(lowered):  # "I'd pay $200 for jordans": a search
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
        return _haggle(text, lowered)
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
    bare = _BARE_BUDGET.search(lowered)
    if bare is not None:
        amount, error = _cents(bare)
        return Command("budget", amount_cents=amount, error=error)
    return None
