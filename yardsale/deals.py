"""Scoring listings from other sites: is this a good deal, and why?

The buyer says what they want and their max. Sources (sources.py) return listings; this module
decides which are the best deals and explains each in one line. Nothing here calls a model or a
paid service: it's keyword matching and integer-cent arithmetic, so it's cheap, testable, and
the same every run.

The market price
----------------
What the item actually sells for comes from the Researcher's eBay sold-listings search (the same
90-day comps the Appraiser uses), run once per search. `market_price` keeps the sales whose
titles carry every search word (or at least half, when fewer than MARKET_NEEDED do), drops lots
and parts, skips accepted-Best-Offer sales when there are others (eBay shows their list price,
not what was paid), and takes the median with `pricing.median`. The Appraiser does the same math
after Claude has judged each sale; here keyword overlap stands in for Claude, because the buyer
side has no photo to compare and shouldn't pay for a model call per search.

The score
---------
Each listing's score adds up, in this order of weight:

1. Price against the market: up to PRICE_POINTS for being 50% or more under the typical sold
   price (shipping included on our side), negative above it. Without a market price, the
   listings are judged against each other: the median total of the listings that match every
   search word, worth at most RELATIVE_POINTS, since that's weaker evidence.
2. Quality, about QUALITY_POINTS at best: the site's condition words, how many photos, the
   seller's rating when the site gives one, a title with enough words to tell what it is, and
   how recently it was posted.
3. Fit: each search word the title lacks costs MISSING_WORD. A title missing more than half the
   words is dropped before scoring.

"Parts", "for repair", "broken", "lot" and the like cost RED_FLAG, and accessories of the thing
("road bike helmet" for "road bike") cost ACCESSORY, unless the buyer asked for them. Something
more than FAR_UNDER percent under the market is flagged for the buyer to check, since it's as
likely a part or a scam as a bargain.

Repeats of the same item across sites (the same words and the same price, or near enough) are
dropped, keeping the better-scored one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from fractions import Fraction

from yardsale.models import Comp
from yardsale.pricing import median, quartiles, round_cents, usd
from yardsale.search import CHEAP, STOPWORDS, words
from yardsale.sources import ExternalListing, deduplicated

PRICE_POINTS = 50  # at 50% or more under the typical sold price
PRICE_FULL_AT = 50  # percent under the market that earns every price point
RELATIVE_POINTS = 20  # without a market price: against the other matches
QUALITY_POINTS = 20  # about; the signals below add up to a little more
MISSING_WORD = 30  # per search word the title lacks
RED_FLAG = 60  # parts, repair, broken, lot
ACCESSORY = 40  # a part of or add-on for the thing
FAR_UNDER = 70  # percent under the market that's suspicious
ABOUT_MARKET = 5  # within this many percent is "about the typical sold price"
MIN_PRICE_CENTS = 100  # $0 and $1 posts are almost always "contact me" placeholders
LIMIT = 5  # deals shown to the buyer
MARKET_NEEDED = 3  # sales with every search word; fewer, and half-matches count too
SIMILAR_TITLES = 0.8  # Jaccard overlap of title words that makes two listings the same item
SAME_PRICE_PCT = 3  # and totals within this many percent

# The words around an amount ("under $300", "max 300"), which no title would carry.
BUDGET_WORDS = {
    "under", "below", "max", "maximum", "budget", "most", "than", "less", "more", "about",
    "around", "up", "to", "at", "from", "no", "over", "price",
}  # fmt: skip

# Words that mean the listing isn't a whole, working item.
RED_FLAGS = {
    "part", "parts", "repair", "repairs", "broken", "lot", "cracked", "damaged", "faulty",
    "defective", "untested", "salvage", "asis", "wanted", "iso", "spares", "incomplete",
}  # fmt: skip
# Words that make a listing a part of or an add-on for the thing, unless the buyer asked for them.
ACCESSORIES = {
    "case", "cover", "charger", "cable", "manual", "box", "tire", "tyre", "tube", "wheel",
    "wheelset", "pedal", "saddle", "seat", "helmet", "light", "mount", "rack", "bag", "strap",
    "sticker", "decal", "replacement", "adapter", "skin", "protector", "stand", "kit", "only",
}  # fmt: skip
# How the sites word condition, best first, with the points each is worth.
CONDITIONS: tuple[tuple[str, int], ...] = (
    ("like new", 6),
    ("open box", 5),
    ("brand new", 6),
    ("new", 6),
    ("excellent", 6),
    ("mint", 6),
    ("very good", 4),
    ("good", 3),
    ("pre-owned", 2),
    ("preowned", 2),
    ("used", 2),
    ("fair", 1),
    ("acceptable", 1),
    ("for parts", 0),
    ("poor", 0),
)


@dataclass(frozen=True)
class MarketPrice:
    """What the item sells for: the median of matching eBay sales in the last 90 days."""

    typical_cents: int
    low_cents: int  # lower quartile
    high_cents: int  # upper quartile
    sales: int  # how many sales the median is from
    basis: str  # "7 eBay sales of the same thing in the last 90 days"


@dataclass(frozen=True)
class ScoredDeal:
    listing: ExternalListing
    total_cents: int  # price plus known shipping
    score: float  # higher is better
    versus_market_pct: int | None  # positive: under the typical sold price; None: no market
    why: str  # one line, without the price and site (the caller shows those its own way)
    flags: tuple[str, ...] = field(default=())  # parts | accessory | missing_words | far_under


def search_terms(query: str) -> list[str]:
    """The words of a search worth matching titles on: no filler, no amounts, no "under"."""
    kept = []
    for w in words(query):
        if w in STOPWORDS or w in CHEAP or w in BUDGET_WORDS or w.isdigit() or w in kept:
            continue
        kept.append(w)
    return kept


def comp_total(comp: Comp) -> int:
    return comp.price_cents + (comp.shipping_cents or 0)


def market_price(comps: list[Comp], query: str) -> MarketPrice | None:
    """The typical sold price for a search, from eBay sales whose titles carry its words.

    Sales carrying every search word are the reference. With fewer than MARKET_NEEDED of
    those, sales carrying at least half the words count too. Lots, parts, and accessories
    never count, and Best Offer sales are skipped when there are others. None when nothing
    matches.
    """
    wanted = search_terms(query)
    if not wanted or not comps:
        return None
    full: list[Comp] = []
    partial: list[Comp] = []
    for comp in comps:
        have = set(words(comp.title))
        if have & RED_FLAGS or (have & ACCESSORIES) - set(wanted):
            continue
        hits = sum(1 for w in wanted if w in have)
        if hits == len(wanted):
            full.append(comp)
        elif hits * 2 >= len(wanted):
            partial.append(comp)
    if len(full) >= MARKET_NEEDED:
        used, what = full, "of the same thing"
    elif full or partial:
        used, what = full + partial, "of the same or similar things"
    else:
        return None
    prices = [comp_total(c) for c in used if not c.best_offer] or [comp_total(c) for c in used]
    low, high = quartiles(prices)
    plural = "s" if len(prices) != 1 else ""
    return MarketPrice(
        typical_cents=round_cents(median(prices)),
        low_cents=round_cents(low),
        high_cents=round_cents(high),
        sales=len(prices),
        basis=f"{len(prices)} eBay sale{plural} {what} in the last 90 days",
    )


def percent_under(reference_cents: int, total_cents: int) -> int:
    """How far under the reference a total is, in whole percent (negative when above)."""
    return round_cents(Fraction((reference_cents - total_cents) * 100, reference_cents))


def _condition_points(condition: str) -> int:
    text = condition.lower()
    for phrase, points in CONDITIONS:
        if phrase in text:
            return points
    return 0


def _days_old(posted_at: str, today: date) -> int | None:
    try:
        return (today - date.fromisoformat(posted_at[:10])).days
    except ValueError:
        return None


def _age_phrase(days: int) -> str:
    if days <= 0:
        return "posted today"
    if days == 1:
        return "posted yesterday"
    if days < 14:
        return f"posted {days} days ago"
    if days < 60:
        return f"posted {days // 7} weeks ago"
    return f"posted {days // 30} months ago"


def quality(listing: ExternalListing, today: date) -> tuple[int, list[str]]:
    """(points, notes) from what the site says about a listing. About QUALITY_POINTS at best."""
    points = 0
    notes: list[str] = []
    condition = _condition_points(listing.condition)
    points += condition
    if listing.condition:
        notes.append(listing.condition.lower())
    photos = (
        listing.photo_count
        if listing.photo_count is not None
        else (1 if listing.image_url else None)
    )
    if photos:
        points += 5 if photos >= 8 else 4 if photos >= 4 else 2
        if photos >= 2:
            notes.append(f"{photos} photos")
    rating = listing.seller_rating
    if rating is not None:
        points += 4 if rating >= 99 else 3 if rating >= 97 else 2 if rating >= 95 else 0
        notes.append(f"seller rated {rating:g}%")
    title_words = len(words(listing.title))
    points += 3 if title_words >= 6 else 2 if title_words >= 4 else 1 if title_words >= 2 else 0
    age = _days_old(listing.posted_at, today) if listing.posted_at else None
    if age is None:
        points += 1
    else:
        points += 3 if age <= 3 else 2 if age <= 14 else 1 if age <= 45 else 0
        notes.append(_age_phrase(age))
    return points, notes


def _market_phrase(pct: int, typical_cents: int) -> str:
    price = usd(typical_cents)
    if abs(pct) < ABOUT_MARKET:
        return f"about the {price} typical sold price"
    if pct > 0:
        return f"{pct}% under the {price} typical sold price"
    return f"{-pct}% above the {price} typical sold price"


def score(
    listing: ExternalListing,
    wanted: list[str],
    market: MarketPrice | None,
    reference_cents: int | None,
    today: date,
) -> ScoredDeal | None:
    """One listing's score and reason. None when a buyer couldn't use it (no price, no link).

    `reference_cents` is the stand-in for the market when there's no market price: the median
    total of the listings that match every search word.
    """
    total = listing.total_cents
    if listing.price_cents is None or listing.price_cents < MIN_PRICE_CENTS or total is None:
        return None
    if not listing.url:
        return None
    have = set(words(listing.title))
    missing = [w for w in wanted if w not in have]
    if wanted and len(missing) * 2 > len(wanted):
        return None

    points = 0.0
    flags: list[str] = []
    notes: list[str] = []
    versus: int | None = None
    if market is not None:
        versus = percent_under(market.typical_cents, total)
        points += max(-PRICE_POINTS, min(PRICE_POINTS, versus * PRICE_POINTS / PRICE_FULL_AT))
        notes.append(_market_phrase(versus, market.typical_cents))
        if versus > FAR_UNDER:
            flags.append("far_under")
    elif reference_cents:
        relative = percent_under(reference_cents, total)
        points += max(-RELATIVE_POINTS, min(RELATIVE_POINTS, relative * RELATIVE_POINTS / 50))

    quality_points, quality_notes = quality(listing, today)
    points += quality_points
    notes += quality_notes

    if have & (RED_FLAGS - set(wanted)):
        points -= RED_FLAG
        flags.append("parts")
    elif (have & ACCESSORIES) - set(wanted):
        points -= ACCESSORY
        flags.append("accessory")
    if missing:
        points -= MISSING_WORD * len(missing)
        flags.append("missing_words")

    if "far_under" in flags:
        notes.append("far under market, so check it's the whole item")
    if "parts" in flags:
        notes.append("listed as parts or for repair")
    if "accessory" in flags:
        notes.append("looks like an accessory, not the thing itself")
    if missing:
        notes.append(f"title doesn't say {' or '.join(repr(w) for w in missing)}")
    return ScoredDeal(listing, total, round(points, 1), versus, ", ".join(notes), tuple(flags))


def same_item(a: ExternalListing, b: ExternalListing) -> bool:
    """The same item posted twice, on one site or across sites: near-identical title words and
    the same total price, or near enough. Different prices are different deals."""
    wa, wb = set(words(a.title)), set(words(b.title))
    if not wa or not wb or len(wa & wb) < SIMILAR_TITLES * len(wa | wb):
        return False
    ta, tb = a.total_cents, b.total_cents
    if ta is None or tb is None:
        return ta == tb
    return abs(ta - tb) * 100 <= SAME_PRICE_PCT * max(ta, tb)


def without_repeats(deals: list[ScoredDeal]) -> list[ScoredDeal]:
    """Drop repeats of the same item, keeping the first (best-scored) of each."""
    kept: list[ScoredDeal] = []
    for deal in deals:
        if not any(same_item(deal.listing, other.listing) for other in kept):
            kept.append(deal)
    return kept


def rank(
    listings: list[ExternalListing],
    query: str,
    budget_cents: int | None,
    market: MarketPrice | None = None,
    today: date | None = None,
    limit: int = LIMIT,
) -> list[ScoredDeal]:
    """The best deals for a buyer, best first: priced, within budget (shipping included),
    matching at least half the search words, scored as the module docstring says, repeats
    dropped, at most `limit`."""
    today = today or date.today()
    wanted = search_terms(query)
    candidates = [
        listing
        for listing in deduplicated(listings)
        if listing.total_cents is not None
        and (budget_cents is None or listing.total_cents <= budget_cents)
    ]
    reference: int | None = None
    if market is None:
        full = [
            c.total_cents
            for c in candidates
            if c.total_cents is not None
            and c.price_cents is not None
            and c.price_cents >= MIN_PRICE_CENTS
            and set(wanted) <= set(words(c.title))
            and not set(words(c.title)) & ((RED_FLAGS | ACCESSORIES) - set(wanted))
        ]
        totals = full or [
            c.total_cents
            for c in candidates
            if c.total_cents is not None and c.price_cents and c.price_cents >= MIN_PRICE_CENTS
        ]
        reference = round_cents(median(totals)) if totals else None
    scored = [
        deal
        for listing in candidates
        if (deal := score(listing, wanted, market, reference, today)) is not None
    ]
    scored.sort(key=lambda d: (-d.score, d.total_cents, d.listing.url))
    return without_repeats(scored)[:limit]


def reason(deal: ScoredDeal, site: str) -> str:
    """The whole line: "$120 + $20 shipping on Craigslist, 35% under the $185 typical sold
    price, like new, 6 photos"."""
    listing = deal.listing
    price = usd(listing.price_cents or 0)
    if listing.shipping_cents:
        price += f" + {usd(listing.shipping_cents)} shipping"
    line = f"{price} on {site}"
    return f"{line}, {deal.why}" if deal.why else line
