"""What the seller is told about the market, in words: the live-price lines in a proposal, the
plain warning when their own price is far off, and the listing description used for Craigslist
posts and the proposal. All of it is built from the saved market view (pricing.market_view) and
Claude's identification; no model is called here.

The seller's price always wins. This is advice, with the numbers behind it, so a seller who
types "at least $40" for shoes that sell for $185 hears so before anything is listed.
"""

from __future__ import annotations

from yardsale.models import MarketView, ScoutListing
from yardsale.pricing import LOCAL_SOURCES, price_verdict, usd
from yardsale.store import Listing

SITE_NAMES = {
    "craigslist": "Craigslist",
    "ebay": "eBay",
    "offerup": "OfferUp",
    "facebook": "Facebook Marketplace",
}
SHOWN = 3  # live listings linked in a proposal


def _span(low: int | None, high: int | None, middle: int) -> str:
    low = middle if low is None else low
    high = middle if high is None else high
    return usd(low) if low == high else f"{usd(low)} to {usd(high)}"


def _sites(view: MarketView) -> str:
    names = [SITE_NAMES.get(s.source, s.source.capitalize()) for s in view.sources]
    if len(names) <= 2:
        return " and ".join(names)
    return ", ".join(names[:-1]) + ", and " + names[-1]


def evidence(view: MarketView) -> str:
    """ "the last 20 sales were around $185, and locals are asking $150 to $200": the numbers a
    warning or a reassurance rests on."""
    parts: list[str] = []
    if view.sold_median_cents is not None:
        count = f"the last {view.sold_count} sales" if view.sold_count > 1 else "the last sale"
        parts.append(f"{count} were around {usd(view.sold_median_cents)}")
    if view.asking_median_cents is not None:
        who = "locals" if any(s.source in LOCAL_SOURCES for s in view.sources) else "other sellers"
        span = _span(view.asking_low_cents, view.asking_high_cents, view.asking_median_cents)
        parts.append(f"{who} are asking {span}")
    return ", and ".join(parts)


def closest_listings(view: MarketView, limit: int = SHOWN) -> list[ScoutListing]:
    """The live listings nearest the pooled asking median, across sites, with links."""
    middle = view.asking_median_cents or 0
    found = [listing for source in view.sources for listing in source.closest if listing.url]
    found.sort(key=lambda listing: (abs(listing.price_cents - middle), listing.url))
    return found[:limit]


def _link(title: str, url: str) -> str:
    title = title.replace("[", "").replace("]", "").replace("(", "").replace(")", "")[:60]
    return f"[{title}]({url})" if url else title


def market_lines(listing: Listing) -> list[str]:
    """Lines for the proposal about live asking prices and the fair range. Empty without a
    market view, and without live listings when only sold prices are known (the proposal
    already shows those)."""
    view = listing.market
    if view is None:
        return []
    lines: list[str] = []
    if view.asking_median_cents is not None:
        span = _span(view.asking_low_cents, view.asking_high_cents, view.asking_median_cents)
        count = f"{view.asking_count} listing{'s' if view.asking_count != 1 else ''}"
        lines.append(
            f"Listed right now: similar ones are asking {span} ({count} on {_sites(view)})."
        )
        lines += [
            f"- {_link(found.title, found.url)}: {usd(found.price_cents)} on "
            f"{SITE_NAMES.get(found.source, found.source)}"
            for found in closest_listings(view)
        ]
    if view.range_low_cents is not None and view.range_high_cents is not None:
        lines.append(
            f"A fair asking price is {_span(view.range_low_cents, view.range_high_cents, 0)}."
        )
    return lines


def _command(word: str, cents: int, n: int | None) -> str:
    return f'"{word} {n} {usd(cents)}"' if n is not None else f'"{word} {usd(cents)}"'


def _suggested_asking(listing: Listing) -> int | None:
    view = listing.market
    if listing.suggested_asking_cents is not None:
        return listing.suggested_asking_cents
    return view.range_high_cents if view is not None else None


def _suggested_floor(listing: Listing) -> int | None:
    view = listing.market
    if listing.suggested_floor_cents is not None:
        return listing.suggested_floor_cents
    return view.range_low_cents if view is not None else None


IN_LINE = "in line with the market"
"""The words of the one feedback that isn't a warning, so a card can tell them apart."""


def price_feedback(listing: Listing, n: int | None = None) -> str:
    """A personal word about the prices the seller gave, with the numbers: far under what it
    sells for, far over, or in line. "" when the seller gave no price or nothing is known.

    `n` is the proposal's number when the seller has several, so the suggested command names
    it ("lowest 2 $150")."""
    view = listing.market
    if view is None:
        return ""
    given = [
        (word, label, cents, suggestion)
        for word, label, cents, suggestion in (
            ("ask", "asking price", listing.seller_asking_cents, _suggested_asking(listing)),
            ("lowest", "minimum", listing.seller_floor_cents, _suggested_floor(listing)),
        )
        if cents is not None
    ]
    if not given:
        return ""
    facts = evidence(view)
    market = "what these sell for" if view.sold_median_cents is not None else "what others ask"
    notes: list[str] = []
    fine: list[str] = []
    for word, label, cents, suggestion in given:
        verdict = price_verdict(cents, view)
        if verdict == "under":
            outcome = (
                f"You'll likely give it away at {usd(cents)}."
                if word == "lowest"
                else f"You'd likely get more at {usd(cents)} than you're asking."
            )
        elif verdict == "over":
            outcome = (
                f"Buyers will likely pass at {usd(cents)}."
                if word == "ask"
                else f"At {usd(cents)} your agent would turn down offers others would take."
            )
        else:
            if verdict == "fine":
                fine.append(f"{usd(cents)} {label}")
            continue
        change = f" Keep it, or say {_command(word, suggestion, n)}." if suggestion else ""
        notes.append(
            f"Your {usd(cents)} {label} is far {verdict} {market}: {facts}. {outcome}{change}"
        )
    if notes:
        return "\n".join(notes)
    if fine and facts:
        prices = " and ".join(fine)
        return f"Your {prices} {'are' if len(fine) > 1 else 'is'} {IN_LINE}: {facts}."
    return ""


def listing_description(listing: Listing) -> str:
    """A short, honest description for a post or a proposal: what Claude saw in the photo and
    what the market says, never the floor."""
    lines = [f"{listing.title}."]
    item = listing.item
    if item is not None:
        named = [f"{k}: {v}" for k, v in (("Brand", item.brand), ("Model", item.model)) if v]
        if named:
            lines.append(". ".join(named) + ".")
        lines.append(f"Condition: {item.condition}.")
        if item.details:
            lines.append("Details: " + "; ".join(item.details) + ".")
    line = listing.market.line if listing.market is not None else ""
    if line:
        lines.append(f"Priced from the market: {line}.")
    elif listing.appraisal is not None and listing.appraisal.matches:
        lines.append("Priced from recent sales of the same item.")
    return "\n".join(lines)
