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
   words is dropped before scoring, unless nothing fits better (then the closest are kept,
   flagged "loose_match"). A title "says" a word through a synonym ("flask" for "water
   bottle"), a compound ("minifridge"), or "4's" for "4" (has_term). Sizes and measurements
   ("size 10", "32 oz") are soft: each one a title doesn't say costs SOFT_MISSING, never a drop.

"Parts", "for repair", "broken", "lot", "frame only" and the like cost RED_FLAG, and parts of or
add-ons for the thing ("road bike helmet" or "road bike brake levers" for "road bike") cost
ACCESSORY, unless the buyer asked for them. Something more than FAR_UNDER percent under the
market is flagged for the buyer to check, since it's as likely a part or a scam as a bargain, and
when it also carries a part word it's dropped: that's the part, priced as a part.

Repeats of the same item across sites (the same words and the same price, or near enough) are
dropped, keeping the better-scored one.

The budget
----------
A listing's total is its price plus known shipping. Craigslist, OfferUp, and Facebook listings
without a shipping price are pickup only, so their total is known; an eBay listing that doesn't
say what shipping costs is compared on its price alone, and says so ("before shipping"). Deals
that fit the budget come first. When fewer than FILL_TO fit, the best ones up to OVER_BUDGET_PCT
over it fill in after them, flagged "over_budget", so the buyer sees how close the market is.
A listing with no price ("contact me") isn't hidden: it comes last, flagged "no_price".

How a deal is shown
-------------------
`headline` is the one sentence a reply opens with: the best deal, its link, its site, its price,
and what it saves against the budget, or without a budget against the typical sold price
("$40 under the typical $90 sold price"). It claims a saving only when the numbers show one.
`describe` is each listing's line: price, site, shipping ("+ $12 shipping", "free shipping",
"pickup only", "shipping unknown"), the budget difference, condition, location, age, the
score's reason, and a short suffix naming what the site didn't say ("condition not listed, no
photo").
"""

from __future__ import annotations

import dataclasses
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date
from fractions import Fraction

from yardsale.models import Comp
from yardsale.pricing import LOCAL_SOURCES, median, quartiles, round_cents, usd
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
FILL_TO = 3  # fewer deals than this within budget, and the closest over it fill in, flagged
OVER_BUDGET_PCT = 50  # the most over budget a filler can be, in percent
UNPRICED_MAX = 2  # listings with no price shown after the priced ones, at most
HEADLINE_TITLE = 60  # characters of a title in the headline's link
# Flags that make a deal a near miss, so the headline doesn't say "Found it".
NEAR_MISS = frozenset({"parts", "accessory", "missing_words"})

# The words around an amount ("under $300", "max 300"), which no title would carry.
BUDGET_WORDS = {
    "under", "below", "max", "maximum", "budget", "most", "than", "less", "more", "about",
    "around", "up", "to", "at", "from", "no", "over", "price",
}  # fmt: skip

# Words that mean the listing isn't a whole, working item. Matched on search.words(), which
# drops a trailing plural s, so "brakes" is "brake" and "levers" is "lever".
RED_FLAGS = {
    "part", "parts", "repair", "repairs", "broken", "lot", "cracked", "damaged", "faulty",
    "defective", "untested", "salvage", "asis", "wanted", "iso", "spares", "incomplete",
}  # fmt: skip
# Phrases that mean the same, which single words miss ("frame only", "box only").
RED_FLAG_PHRASES = ("frame only", "box only", "for parts", "parts only", "not working", "as is")
# Words that make a listing a part of or an add-on for the thing, unless the buyer asked for them.
# A road bike search on October 24 ranked "Road Bike Brake Levers" third, so bike parts are
# spelled out.
#
# "box", "only", "light", and "seat" used to be here too, and they sank whole items: "Jordan 4
# Retro ... new in box", "worn only once", "light blue water bottle", "3 seat sofa".
# The bike words are now accessories only in a bike search, and "box only" and the like are
# RED_FLAG phrases. An accessory word right after "with", "w/", "includes", "+" ... names
# something that comes with the thing ("iPhone 13 with case"), so it doesn't count either.
ACCESSORIES = {
    # anything
    "case", "cover", "charger", "cable", "cord", "remote", "manual", "mount", "bracket",
    "rack", "bag", "strap", "sticker", "decal", "replacement", "adapter", "skin", "protector",
    "stand", "kit", "cushion", "leg", "helmet", "lid", "cap", "straw", "sleeve", "holder", "pouch",
    "carrier", "insert", "cage", "lace", "insole",
}  # fmt: skip
# Parts of a bike, which are accessories only when the buyer is shopping for a bike. A road bike
# search on October 24 ranked "Road Bike Brake Levers" third.
BIKE_PARTS = {
    "tire", "tyre", "tube", "wheel", "wheelset", "pedal", "saddle", "seat", "stem", "handlebar",
    "fork", "derailleur", "crank", "crankset", "cassette", "shifter", "lever", "brake",
    "groupset", "frameset", "light",
}  # fmt: skip
BIKE_WORDS = {"bike", "bicycle", "ebike", "cycle", "cycling"}
# Words just before an accessory word that make it an extra, not the thing for sale.
WITH_WORDS = {
    "with", "w", "includes", "including", "incl", "plus", "comes", "extra", "bonus", "free",
    "original", "og", "and",
}  # fmt: skip
# "<part> only" means that's all there is.
_ONLY = re.compile(
    r"\b(?:box|case|lid|cap|legs?|shell|top|base|frame|cover|stand|charger|remote|controller|"
    r"straps?|parts?|boots?|sleeve|cushions?|mattress|tank)\s+only\b"
)
# Search words that mean the same thing in a listing's title. Both sides go through words(),
# which drops a plural s. A water bottle search on October 24 had nothing to say about "Hydro
# Flask Standard Mouth" or "ThermoFlask Blue Insulated", which are water bottles.
SYNONYMS: dict[str, frozenset[str]] = {
    "fridge": frozenset({"refrigerator", "minifridge", "fridgerator"}),
    "refrigerator": frozenset({"fridge", "minifridge"}),
    "mini": frozenset({"compact", "minifridge", "dorm"}),
    "bottle": frozenset({"flask", "hydroflask", "thermoflask", "tumbler", "canteen", "nalgene"}),
    "bike": frozenset({"bicycle"}),
    "bicycle": frozenset({"bike"}),
    "couch": frozenset({"sofa", "loveseat", "sectional"}),
    "sofa": frozenset({"couch", "loveseat", "sectional"}),
    "tv": frozenset({"television"}),
    "television": frozenset({"tv"}),
    "sneaker": frozenset({"shoe", "trainer"}),
    "shoe": frozenset({"sneaker", "trainer"}),
    "headphone": frozenset({"headset", "earbud", "earphone"}),
    "laptop": frozenset({"macbook", "chromebook", "thinkpad"}),
    "jordan": frozenset({"aj", "jordans"}),
    "4": frozenset({"iv"}),
    "3": frozenset({"iii"}),
    "11": frozenset({"xi"}),
    "12": frozenset({"xii"}),
}
# A search word that a synonym of another word already says: a flask is a water bottle.
IMPLIED: dict[str, frozenset[str]] = {
    "water": frozenset({"flask", "hydroflask", "thermoflask", "nalgene", "canteen"}),
}
# The sizes and measurements in a search ("size 10", "32 oz", "128gb"). The sites are searched
# without them, since titles word them every way ("Sz 10", "32oz", "10M"), and a title that
# doesn't say them costs SOFT_MISSING instead of being dropped.
_SIZE = re.compile(
    r"\b(?:size|sz|us|mens|men|womens|women|youth|kids|gs)\s*(\d{1,2}(?:\.5)?)\b(?!\s*(?:oz|gb))"
)
_UNITS = (
    "oz", "ounce", "gb", "tb", "inch", "in", "ft", "feet", "l", "liter", "litre", "ml", "cm",
    "mm", "lb", "lbs", "qt", "quart", "gal", "gallon", "cu", "w", "mah",
)  # fmt: skip
_MEASURE = re.compile(r"\b(\d+(?:\.\d+)?)\s*(" + "|".join(_UNITS) + r")s?\b")
# Style codes and part numbers ("FV5029-141", "308497-603") and years: right for eBay's exact
# search, but a classified ad rarely carries them, and Craigslist found nothing for "Air Jordan
# 4 Military Blue 2024 FV5029-141" on October 24.
_CODE = re.compile(r"\b(?:[a-z]{1,3}\d{4,}-\d{1,4}|\d{6}-\d{3}|\d{6,})\b")
_YEAR = re.compile(r"\b(?:19[5-9]\d|20[0-4]\d)\b")
SOFT_MISSING = 8  # per size or measurement the title doesn't say
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
    # The score's reason: the market comparison, photos, seller rating, and cautions. Price,
    # site, shipping, condition, location, and age come from the listing (`describe`).
    why: str
    # parts | accessory | missing_words | far_under | over_budget | no_price
    flags: tuple[str, ...] = field(default=())


_AMOUNTS = re.compile(
    r"\b(?:under|below|max(?:imum)?|budget(?:\s+of)?|up\s+to|at\s+most|less\s+than|"
    r"no\s+more\s+than)\s*\$?\s?\d[\d,]*(?:\.\d+)?\b|\$\s?\d[\d,]*(?:\.\d+)?|"
    r"\b\d[\d,]*(?:\.\d+)?\s*(?:dollars|bucks|usd)\b"
)
_UNIT_NAMES = {
    "ounce": "oz", "inch": "in", "feet": "ft", "liter": "l", "litre": "l", "lbs": "lb",
    "quart": "qt", "gallon": "gal",
}  # fmt: skip


def norm(word: str) -> str:
    """One title or search word, compared form: "4s" (as in "Jordan 4's") is "4"."""
    return word[:-1] if len(word) > 1 and word[:-1].isdigit() and word.endswith("s") else word


def _measures(text: str) -> list[str]:
    """ "32 oz" and "32oz" -> "32oz"; every measurement in the text, in one spelling."""
    return [
        f"{found.group(1)}{_UNIT_NAMES.get(found.group(2), found.group(2))}"
        for found in _MEASURE.finditer(text)
    ]


def split_terms(query: str) -> tuple[list[str], list[str]]:
    """(core, soft) words of a search. Core words are what a title has to say to be the thing:
    no filler, no amounts, no "under", but model numbers stay ("jordan 4", "trek fx 2"). Soft
    words are sizes, measurements, and years ("10", "32oz", "2024"): worth a little when a
    title says them, never a reason to drop one. Style codes are dropped from both."""
    text = _AMOUNTS.sub(" ", _plain(query))
    soft = [f"size{found.group(1)}" for found in _SIZE.finditer(text)]
    text = _SIZE.sub(" ", text)
    soft += _measures(text)
    text = _MEASURE.sub(" ", text)
    soft += _YEAR.findall(text)
    text = _CODE.sub(" ", _YEAR.sub(" ", text))
    core: list[str] = []
    for w in words(text):
        w = norm(w)
        if w in STOPWORDS or w in CHEAP or w in BUDGET_WORDS or w in core:
            continue
        core.append(w)
    return core, list(dict.fromkeys(soft))


def search_terms(query: str) -> list[str]:
    """The words of a search a title has to match: split_terms' core words."""
    return split_terms(query)[0]


def site_query(query: str) -> str:
    """What to type into another site's search box for a search: its core words, in order,
    without sizes, measurements, years, or style codes, which titles word every way."""
    return " ".join(search_terms(query))


def _plain(text: str) -> str:
    """Lowercase, without apostrophes, so "Men's" reads as "mens"."""
    return re.sub("['\\u2019]", "", text.lower())


def title_words(title: str) -> set[str]:
    """A title's words in compared form, plus its measurements ("32oz") and sizes ("size10").
    A size's number isn't a word of its own, so "Air Jordan Retro Size 4.5" doesn't say "4"."""
    lowered = _plain(title)
    sizes = {f"size{found.group(1)}" for found in _SIZE.finditer(lowered)}
    return {norm(w) for w in words(_SIZE.sub(" ", lowered))} | set(_measures(lowered)) | sizes


def has_term(term: str, have: set[str]) -> bool:
    """Whether a title (its title_words) says a search word: the word itself, a synonym, a word
    the synonym implies ("flask" says "water"), or a compound starting with it ("minifridge")
    or, for longer words, ending with it ("hydroflask")."""
    if term in have or have & SYNONYMS.get(term, frozenset()):
        return True
    if have & IMPLIED.get(term, frozenset()):
        return True
    if term.isalpha() and len(term) >= 3:
        for word in have:
            if len(word) < len(term) + 3 or not word.isalpha():
                continue
            if word.startswith(term) or (len(term) >= 5 and word.endswith(term)):
                return True
    return False


def missing_terms(wanted: list[str], title: str) -> list[str]:
    """The search words a title doesn't say (has_term)."""
    have = title_words(title)
    return [w for w in wanted if not has_term(w, have)]


def fits(wanted: list[str], title: str) -> bool:
    """A title says at least half the search words (has_term). The buyer's fit rule."""
    return not wanted or len(missing_terms(wanted, title)) * 2 <= len(wanted)


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
        have = title_words(comp.title)
        if not_the_thing(comp.title, have, set(wanted)):
            continue
        hits = sum(1 for w in wanted if has_term(w, have))
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


def not_the_thing(title: str, have: set[str], wanted: set[str]) -> str | None:
    """ "parts" when a title says the item is broken, incomplete, or a part; "accessory" when it
    names a part of or add-on for the thing; None otherwise. Words the buyer asked for don't
    count against a listing ("bike helmet" for "bike helmet"), and neither does an accessory
    word right after "with" or "+" ("Jordan 4 with box", "DS + laces"). Bike parts count only
    in a bike search. `have` is kept for callers; the title is read again for word order."""
    lowered = title.lower()
    ordered = words(lowered.replace("+", " plus ").replace("&", " and "))
    tokens = set(ordered) | have
    if tokens & (RED_FLAGS - wanted) or any(phrase in lowered for phrase in RED_FLAG_PHRASES):
        return "parts"
    if _ONLY.search(lowered) and not wanted & {"box", "case", "lid", "frame", "part", "parts"}:
        return "parts"
    parts = ACCESSORIES | (BIKE_PARTS if wanted & BIKE_WORDS else set())
    for n, word in enumerate(ordered):
        if word not in parts or word in wanted:
            continue
        if any(before in WITH_WORDS for before in ordered[max(0, n - 3) : n]):
            continue
        return "accessory"
    return None


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
    photos = _photos(listing)
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


def _photos(listing: ExternalListing) -> int | None:
    if listing.photo_count is not None:
        return listing.photo_count
    return 1 if listing.image_url else None


def signal_notes(listing: ExternalListing) -> list[str]:
    """The quality notes that aren't on a listing's line already: photos and seller rating."""
    notes: list[str] = []
    photos = _photos(listing)
    if photos and photos >= 2:
        notes.append(f"{photos} photos")
    if listing.seller_rating is not None:
        notes.append(f"seller rated {listing.seller_rating:g}%")
    return notes


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
    soft: list[str] | tuple[str, ...] = (),
    loose: bool = False,
) -> ScoredDeal | None:
    """One listing's score and reason. None when a buyer couldn't use it (no price, no link),
    or when its title says fewer than half the search words (has_term). With `loose`, one word
    is enough, and the deal is flagged "loose_match": rank uses that when nothing fits better.

    `reference_cents` is the stand-in for the market when there's no market price: the median
    total of the listings that match every search word. `soft` are the search's sizes and
    measurements (split_terms); each one the title doesn't say costs SOFT_MISSING.
    """
    total = listing.total_cents
    if listing.price_cents is None or listing.price_cents < MIN_PRICE_CENTS or total is None:
        return None
    if not listing.url:
        return None
    have = title_words(listing.title)
    missing = [w for w in wanted if not has_term(w, have)]
    loosely = bool(wanted) and len(missing) * 2 > len(wanted)
    if loosely and (not loose or len(missing) == len(wanted)):
        return None

    points = 0.0
    flags: list[str] = []
    notes: list[str] = []
    versus: int | None = None
    part = not_the_thing(listing.title, have, set(wanted))
    if market is not None:
        versus = percent_under(market.typical_cents, total)
        if versus > FAR_UNDER and part is not None:
            # A "road bike" at 84% under market that says "brake levers" is the levers, not a
            # bargain bike (seen live on October 24). Not worth showing at all.
            return None
        points += max(-PRICE_POINTS, min(PRICE_POINTS, versus * PRICE_POINTS / PRICE_FULL_AT))
        notes.append(_market_phrase(versus, market.typical_cents))
        if versus > FAR_UNDER:
            flags.append("far_under")
    elif reference_cents:
        relative = percent_under(reference_cents, total)
        points += max(-RELATIVE_POINTS, min(RELATIVE_POINTS, relative * RELATIVE_POINTS / 50))

    quality_points, _ = quality(listing, today)
    points += quality_points
    notes += signal_notes(listing)  # condition and age are on the listing's line already

    if part == "parts":
        points -= RED_FLAG
        flags.append("parts")
    elif part == "accessory":
        points -= ACCESSORY
        flags.append("accessory")
    if missing:
        points -= MISSING_WORD * len(missing)
        flags.append("missing_words")
    if loosely:
        flags.append("loose_match")
    unsaid = [term for term in soft if term not in have]
    points -= SOFT_MISSING * len(unsaid)

    if "far_under" in flags:
        notes.append("far under market, so check it's the whole item")
    if "parts" in flags:
        notes.append("listed as parts or for repair")
    if "accessory" in flags:
        notes.append("looks like an accessory, not the thing itself")
    if missing:
        notes.append(f"title doesn't say {' or '.join(repr(w) for w in missing)}")
    return ScoredDeal(listing, total, round(points, 1), versus, ", ".join(notes), tuple(flags))


def _places(location: str) -> set[str]:
    return set(words(location)) - {"ca", "california", "usa", "us", "united", "state"}


def same_item(a: ExternalListing, b: ExternalListing) -> bool:
    """The same item posted twice, on one site or across sites: near-identical title words and
    the same total price, or near enough. Different prices are different deals, and so are two
    posts in different towns: on October 24, OfferUp had "Mini Fridge" for $60 in Redwood City
    and in Richmond, and four "Air Jordan 4 Retro" posts at the same prices around the bay, and
    all but one of each were dropped as repeats."""
    wa, wb = set(words(a.title)), set(words(b.title))
    if not wa or not wb or len(wa & wb) < SIMILAR_TITLES * len(wa | wb):
        return False
    pa, pb = _places(a.location), _places(b.location)
    if pa and pb and not pa & pb:
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


def unpriced(listing: ExternalListing, wanted: list[str], today: date) -> ScoredDeal | None:
    """A listing with no price ("contact me"), scored on quality and fit only and flagged
    "no_price", so it can be shown last instead of hidden. None when a buyer couldn't use it:
    no link, too few search words, or a part or accessory (with no price to judge it by)."""
    if listing.price_cents is not None or not listing.url:
        return None
    have = title_words(listing.title)
    missing = [w for w in wanted if not has_term(w, have)]
    if wanted and len(missing) * 2 > len(wanted):
        return None
    if not_the_thing(listing.title, have, set(wanted)) is not None:
        return None
    points, _ = quality(listing, today)
    flags = ["no_price"]
    notes = signal_notes(listing)
    if missing:
        points -= MISSING_WORD * len(missing)
        flags.append("missing_words")
        notes.append(f"title doesn't say {' or '.join(repr(w) for w in missing)}")
    return ScoredDeal(listing, 0, float(points), None, ", ".join(notes), tuple(flags))


def _best_first(scored: list[ScoredDeal]) -> list[ScoredDeal]:
    return sorted(scored, key=lambda d: (-d.score, d.total_cents, d.listing.url))


def _more(kept: list[ScoredDeal], extra: list[ScoredDeal], room: int) -> list[ScoredDeal]:
    """Up to `room` of `extra` (best first) that aren't repeats of what's kept."""
    return without_repeats(kept + _best_first(extra))[len(kept) :][: max(room, 0)]


def rank(
    listings: list[ExternalListing],
    query: str,
    budget_cents: int | None,
    market: MarketPrice | None = None,
    today: date | None = None,
    limit: int = LIMIT,
    fill_to: int = FILL_TO,
    unpriced_too: bool = True,
) -> list[ScoredDeal]:
    """The best deals for a buyer, best first: priced, within budget (shipping included; an
    unknown shipping cost never drops a listing), matching at least half the search words,
    scored as the module docstring says, repeats dropped, at most `limit`. When nothing
    matches half the words, the listings matching any of them are kept instead, flagged
    "loose_match", so the buyer sees the closest things rather than nothing. When fewer than
    `fill_to` fit the budget, the best up to OVER_BUDGET_PCT over it follow, flagged
    "over_budget"; then up to UNPRICED_MAX listings with no price, flagged "no_price"."""
    today = today or date.today()
    wanted, soft = split_terms(query)
    unique = deduplicated(listings)
    candidates = [
        listing
        for listing in unique
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
            and not missing_terms(wanted, c.title)
            and not_the_thing(c.title, title_words(c.title), set(wanted)) is None
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
        if (deal := score(listing, wanted, market, reference, today, soft)) is not None
    ]
    if not scored:
        scored = [
            deal
            for listing in candidates
            if (deal := score(listing, wanted, market, reference, today, soft, loose=True))
            is not None
        ]
    chosen = without_repeats(_best_first(scored))[:limit]
    room = min(fill_to, limit) - len(chosen)
    if budget_cents is not None and room > 0:
        most = budget_cents * (100 + OVER_BUDGET_PCT) // 100
        over = [
            dataclasses.replace(deal, flags=(*deal.flags, "over_budget"))
            for listing in unique
            if listing.total_cents is not None and budget_cents < listing.total_cents <= most
            if (deal := score(listing, wanted, market, reference, today, soft)) is not None
        ]
        chosen += _more(chosen, over, room)
    if unpriced_too:
        no_price = [
            deal for listing in unique if (deal := unpriced(listing, wanted, today)) is not None
        ]
        chosen += _more(chosen, no_price, min(UNPRICED_MAX, limit - len(chosen)))
    return chosen


def fitting(
    listings: list[ExternalListing],
    query: str,
    budget_cents: int | None,
    market: MarketPrice | None = None,
    limit: int = LIMIT,
) -> list[ScoredDeal]:
    """`rank` with only priced listings within the budget: no fillers, no unpriced posts."""
    return rank(listings, query, budget_cents, market, limit=limit, fill_to=0, unpriced_too=False)


# --- How a deal is shown ------------------------------------------------------------------------


def markdown_link(title: str, url: str, limit: int = 70) -> str:
    """[title](url), with brackets taken out of the title and a long one cut at a word."""
    title = re.sub(r"[\[\]()]", "", title).strip()
    if len(title) > limit:
        title = (title[:limit].rsplit(" ", 1)[0] or title[:limit]).rstrip(" ,;:-")
    return f"[{title}]({url})" if url else title


def shipping_known(listing: ExternalListing) -> bool:
    """Whether the total is what the buyer pays: shipping is given, or it's a pickup site."""
    return listing.shipping_cents is not None or listing.source in LOCAL_SOURCES


def shipping_phrase(listing: ExternalListing) -> str:
    """ "+ $12 shipping", "free shipping", "pickup only", or "shipping unknown"."""
    if listing.shipping_cents:
        return f"+ {usd(listing.shipping_cents)} shipping"
    if listing.shipping_cents == 0:
        return "free shipping"
    if listing.source in LOCAL_SOURCES:
        return "pickup only"
    return "shipping unknown"


def under_budget_cents(listing: ExternalListing, budget_cents: int | None) -> int | None:
    """How far under the budget the total is (negative when over); None without a budget or a
    price. With shipping unknown, it's the price alone (see shipping_known)."""
    total = listing.total_cents
    if budget_cents is None or total is None:
        return None
    return budget_cents - total


def budget_phrase(listing: ExternalListing, budget_cents: int | None, qualify: bool = True) -> str:
    """ "$45 under budget", "$5 over budget with shipping", "$20 under budget before shipping",
    or "" without a budget or a price. `qualify` False leaves out "with shipping" (for a price
    already shown with shipping); "before shipping" always stays."""
    diff = under_budget_cents(listing, budget_cents)
    if diff is None:
        return ""
    if not shipping_known(listing):
        tail = " before shipping"
    elif listing.shipping_cents and qualify:
        tail = " with shipping"
    else:
        tail = ""
    if diff > 0:
        return f"{usd(diff)} under budget{tail}"
    if diff == 0:
        return f"right at your budget{tail}"
    return f"{usd(-diff)} over budget{tail}"


def missing_fields(listing: ExternalListing) -> list[str]:
    """What the site didn't say: any of price, photo, condition, location."""
    missing = []
    if listing.price_cents is None:
        missing.append("price")
    if not _photos(listing):
        missing.append("photo")
    if not listing.condition.strip():
        missing.append("condition")
    if not listing.location.strip():
        missing.append("location")
    return missing


MISSING_WORDS = {
    "photo": "no photo",
    "condition": "condition not listed",
    "location": "location not listed",
}


def missing_note(listing: ExternalListing) -> str:
    """ "condition not listed, no photo": the suffix a line ends with. A missing price is said
    where the price goes, so it isn't repeated here."""
    return ", ".join(MISSING_WORDS[f] for f in missing_fields(listing) if f in MISSING_WORDS)


def details(listing: ExternalListing, today: date | None = None) -> list[str]:
    """Condition, location, how long ago it was posted, and whether the site takes offers."""
    found = [part for part in (listing.condition.strip(), listing.location.strip()) if part]
    if listing.posted_at:
        age = _days_old(listing.posted_at, today or date.today())
        if age is not None:
            found.append(_age_phrase(age))
    if listing.accepts_offers:
        found.append("takes offers")
    return found


def _sentence(text: str) -> str:
    return text[:1].upper() + text[1:]


def describe(
    listing: ExternalListing,
    site: str,
    why: str = "",
    budget_cents: int | None = None,
    today: date | None = None,
) -> str:
    """One listing, without its link: "$180 on Craigslist, pickup only, $120 under budget.
    Used, Oakland, CA, posted 2 days ago. 42% under the $310 typical sold price, 6 photos.
    (no photo)"."""
    if listing.price_cents is None:
        first = f"price not listed on {site}"
    else:
        first = usd(listing.price_cents)
        if listing.shipping_cents:
            first += f" + {usd(listing.shipping_cents)} shipping"
        first += f" on {site}"
    head = [first]
    if not listing.shipping_cents:
        head.append(shipping_phrase(listing))
    if against := budget_phrase(listing, budget_cents):
        head.append(against)
    sentences = [", ".join(head)]
    if found := details(listing, today):
        sentences.append(_sentence(", ".join(found)))
    if why:
        sentences.append(_sentence(why))
    text = ". ".join(sentences) + "."
    note = missing_note(listing)
    return f"{text} ({note})" if note else text


def reason(
    deal: ScoredDeal, site: str, budget_cents: int | None = None, today: date | None = None
) -> str:
    """A deal's whole line without its link (describe)."""
    return describe(deal.listing, site, deal.why, budget_cents, today)


def headline(
    found: Sequence[ScoredDeal],
    budget_cents: int | None,
    market: MarketPrice | None,
    site: Callable[[str], str],
) -> str:
    """The sentence a reply opens with: the best deal, its link, and what it saves.

    "Found it: [Hydro Flask 32 oz](...) on Craigslist for $18, $12 under your $30 budget
    (Berkeley, pickup)." Without a budget it's compared with the typical sold price, and
    without either it says where and how much. A saving is claimed only from the numbers:
    shipping that isn't known is said ("before shipping"), and when nothing fits the budget it
    says so and names the closest. "" when there are no deals.
    """
    if not found:
        return ""
    priced = [d for d in found if d.listing.price_cents is not None]
    if not priced:
        first = found[0].listing
        link = markdown_link(first.title, first.url, HEADLINE_TITLE)
        return f"Found {link} on {site(first.source)}, but the price isn't listed: ask the seller."
    fits = [d for d in priced if (under_budget_cents(d.listing, budget_cents) or 0) >= 0]
    best = fits[0] if fits else priced[0]
    listing = best.listing
    price = listing.price_cents or 0
    total = listing.total_cents or price
    link = markdown_link(listing.title, listing.url, HEADLINE_TITLE)
    if listing.shipping_cents:
        cost = f"for {usd(total)} with shipping"
    elif listing.shipping_cents == 0:
        cost = f"for {usd(price)} with free shipping"
    else:
        cost = f"for {usd(price)}"
    before = "" if shipping_known(listing) else " before shipping"
    compare = ""
    if budget_cents is not None:
        diff = budget_cents - total
        if diff > 0:
            compare = f"{usd(diff)} under your {usd(budget_cents)} budget{before}"
        elif diff == 0:
            compare = f"right at your {usd(budget_cents)} budget{before}"
        else:
            compare = f"{usd(-diff)} over it{before}"
    elif market is not None and market.typical_cents > 0:
        typical = market.typical_cents
        if abs(percent_under(typical, total)) < ABOUT_MARKET:
            compare = f"about the typical {usd(typical)} sold price{before}"
        elif typical > total:
            compare = f"{usd(typical - total)} under the typical {usd(typical)} sold price{before}"
        else:
            compare = f"{usd(total - typical)} above the typical {usd(typical)} sold price{before}"
    tags = [listing.location.strip()] if listing.location.strip() else []
    if listing.shipping_cents is None:
        if listing.source in LOCAL_SOURCES:
            tags.append("pickup")
        elif not compare:
            tags.append("shipping unknown")
    sentence = f"{link} on {site(listing.source)} {cost}"
    if compare:
        sentence += f", {compare}"
    if tags:
        sentence += f" ({', '.join(tags)})"
    if not fits:
        return f"Nothing fits your {usd(budget_cents or 0)} budget yet. Closest: {sentence}."
    lead = "Closest match" if NEAR_MISS & set(best.flags) else "Found it"
    text = f"{lead}: {sentence}."
    if "far_under" in best.flags:
        text += " It's far under the usual price, so check it's the whole item."
    return text
