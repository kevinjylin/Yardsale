"""Money rules: integer cents, rounding, seller prices versus suggestions, and counter-offers.

Prices are integer cents everywhere. Amounts people type are parsed with Decimal, and the
appraisal statistics use Fraction, so no float ever decides a price.

The market view (market_view) puts two things side by side: what the item sold for (the
Appraiser's matched eBay sales) and what competitors are asking for it right now (the Market
Scout's live listings). Sold prices set the asking price; asking prices only inform the range,
the market line buyers hear while haggling, and the warning a seller gets when their own price
is far off (price_verdict).
"""

from __future__ import annotations

import math
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from fractions import Fraction

from yardsale.models import Appraisal, MarketView, ScoutReport

MAX_CENTS = 100_000_000  # $1,000,000. Anything above is a typo.
FLOOR_SHARE = Fraction(7, 10)  # the lowest price is never below 70% of the typical price
FAR_SHARE = Fraction(7, 20)  # a seller's price 35% or more off the market gets a plain warning
LOCAL_SOURCES = frozenset({"craigslist", "offerup", "facebook"})  # in-person sites


class PriceError(ValueError):
    """A price that can't be used. The message is shown to the seller or buyer."""


class NoSuggestedPrice(PriceError):
    """The appraisal found nothing to price from, and the seller gave no price."""


def valid_cents(cents: int) -> bool:
    return 0 < cents <= MAX_CENTS


def usd(cents: int) -> str:
    """$340 for whole dollars, $12.50 otherwise."""
    return f"${cents // 100:,}" if cents % 100 == 0 else f"${cents // 100:,}.{cents % 100:02d}"


def cents_from_decimal(amount: Decimal) -> int:
    """Dollars -> cents, rounding half a cent up. Raises PriceError outside $0.01-$1,000,000."""
    if not amount.is_finite():
        raise PriceError("That isn't a price I can use.")
    cents = int((amount * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))
    if not valid_cents(cents):
        raise PriceError(f"Prices must be between $0.01 and {usd(MAX_CENTS)}.")
    return cents


def parse_usd(text: str) -> int:
    """'$1,250.50' -> 125050. Raises PriceError."""
    try:
        amount = Decimal(text.replace("$", "").replace(",", "").strip())
    except InvalidOperation:
        raise PriceError(f"{text!r} isn't a price I can read.") from None
    return cents_from_decimal(amount)


def to_cents(dollars: float | None) -> int | None:
    """A dollar amount from a model's JSON -> cents. Zero, negative, or missing means no price."""
    if dollars is None or not math.isfinite(dollars) or dollars <= 0:
        return None
    return cents_from_decimal(Decimal(str(dollars)))


def round_price(cents: Fraction | int) -> int:
    """Round to a price a person would ask: $1 steps under $20, $5 under $100, $10 above.

    The step comes from the unrounded amount, halves round up, and the result is never below one
    step. So $19.99 becomes $20, $22.50 becomes $25, and $99.90 becomes $100.
    """
    step = 100 if cents < 2000 else 500 if cents < 10000 else 1000
    return max(step, math.floor(Fraction(cents) / step + Fraction(1, 2)) * step)


def round_cents(amount: Fraction) -> int:
    return math.floor(amount + Fraction(1, 2))


def median(prices: list[int]) -> Fraction:
    data = sorted(prices)
    mid = len(data) // 2
    return Fraction(data[mid]) if len(data) % 2 else Fraction(data[mid - 1] + data[mid], 2)


def quartiles(prices: list[int]) -> tuple[Fraction, Fraction]:
    """Lower and upper quartiles by the "exclusive" method, the default of Python's
    statistics.quantiles. With fewer than 4 prices: the lowest and the highest."""
    data = sorted(prices)
    n = len(data)
    if n < 4:
        return Fraction(data[0]), Fraction(data[-1])

    def cut(i: int) -> Fraction:
        j = min(max(i * (n + 1) // 4, 1), n - 1)
        delta = i * (n + 1) - j * 4
        return Fraction(data[j - 1] * (4 - delta) + data[j] * delta, 4)

    return cut(1), cut(3)


def suggested_prices(prices: list[int]) -> tuple[int, int, int, int]:
    """(asking, floor, low, high) from sold prices.

    Asking is the median. The floor is the higher of the lower quartile and 70% of the median,
    and never above asking. Low and high are the quartiles, for showing the usual range.
    """
    middle = median(prices)
    low, high = quartiles(prices)
    asking = round_price(middle)
    floor = min(asking, round_price(max(low, middle * FLOOR_SHARE)))
    return asking, floor, round_cents(low), round_cents(high)


def default_floor(asking_cents: int) -> int:
    """The lowest price when only the asking price is known: 70% of it, rounded."""
    return min(asking_cents, round_price(asking_cents * FLOOR_SHARE))


def listing_prices(
    suggested_asking: int | None,
    suggested_floor: int | None,
    seller_asking: int | None,
    seller_floor: int | None,
) -> tuple[int, int]:
    """(asking, floor) for a listing. A price the seller gave always wins over a suggestion.

    - Both from the seller: used as given; the floor can't be above the asking price.
    - Only the seller's asking price: the suggested floor if it's at or below it, else 70%.
    - Only the seller's floor: the suggested asking price if it's at or above it, else the floor.
    - Neither: the suggestion. No suggestion either raises NoSuggestedPrice.
    """
    for given in (seller_asking, seller_floor):
        if given is not None and not valid_cents(given):
            raise PriceError(f"Prices must be between $0.01 and {usd(MAX_CENTS)}.")
    if seller_asking is not None and seller_floor is not None:
        if seller_floor > seller_asking:
            raise PriceError(
                f"Your lowest price ({usd(seller_floor)}) is above your asking price "
                f"({usd(seller_asking)})."
            )
        return seller_asking, seller_floor
    if seller_asking is not None:
        if suggested_floor is not None and suggested_floor <= seller_asking:
            return seller_asking, suggested_floor
        return seller_asking, default_floor(seller_asking)
    if seller_floor is not None:
        if suggested_asking is not None and suggested_asking >= seller_floor:
            return suggested_asking, seller_floor
        return seller_floor, seller_floor
    if suggested_asking is None or suggested_floor is None:
        raise NoSuggestedPrice("No recent sales were close enough to suggest a price.")
    return suggested_asking, suggested_floor


# --- The market view: sold prices beside live asking prices ------------------------------------


def price_range(prices: list[int]) -> tuple[int, int, int]:
    """(low, median, high) in whole cents: the quartiles around the median. With fewer than 4
    prices the quartiles are the lowest and highest."""
    low, high = quartiles(prices)
    return round_cents(low), round_cents(median(prices)), round_cents(high)


def _span(low: int, high: int) -> str:
    return usd(low) if low == high else f"{usd(low)} to {usd(high)}"


def market_line(view: MarketView) -> str:
    """One sentence of evidence an item agent can give while haggling, built only from the
    sold prices and the live asking prices: it never mentions a floor or the seller's prices.
    Empty when there's nothing to cite."""
    parts: list[str] = []
    if view.sold_median_cents is not None:
        parts.append(f"similar ones sold for about {usd(view.sold_median_cents)} on eBay recently")
    if view.asking_median_cents is not None:
        low = (
            view.asking_low_cents if view.asking_low_cents is not None else view.asking_median_cents
        )
        high = (
            view.asking_high_cents
            if view.asking_high_cents is not None
            else view.asking_median_cents
        )
        who = "locals" if any(s.source in LOCAL_SOURCES for s in view.sources) else "other sellers"
        parts.append(f"{who} are asking {_span(low, high)} right now")
    return ", and ".join(parts)


def market_view(appraisal: Appraisal | None, report: ScoutReport | None) -> MarketView | None:
    """Both sides of the market, or None when neither side has a price.

    - The sold side is the appraisal: its rounded median is what the item sells for, and its
      quartiles are the usual spread.
    - The asking side pools every live listing the Scout kept, across sites, and takes the
      median and quartiles of their asking prices.
    - The suggested range runs from the lower of the two medians to the higher: list between
      what it sells for and what competitors ask. With only sold prices it's their quartiles;
      with only asking prices it's from their lower quartile to their median, since an item
      priced at or under the typical ask is the one that sells.

    Everything is rounded the way a person would price (round_price).
    """
    sold_count = len(appraisal.matches) if appraisal is not None else 0
    sold_median = appraisal.asking_cents if appraisal is not None else None
    sold_low = appraisal.low_cents if appraisal is not None else None
    sold_high = appraisal.high_cents if appraisal is not None else None
    sources = list(report.sources) if report is not None else []
    asks = [price for source in sources for price in source.prices_cents if price > 0]
    asking_low = asking_median = asking_high = None
    if asks:
        asking_low, asking_median, asking_high = price_range(asks)
    if sold_median is None and asking_median is None:
        return None
    if sold_median is not None and asking_median is not None:
        range_low = round_price(min(sold_median, asking_median))
        range_high = round_price(max(sold_median, asking_median))
    elif sold_median is not None:
        range_low = round_price(sold_low if sold_low is not None else sold_median)
        range_high = round_price(sold_high if sold_high is not None else sold_median)
    else:
        assert asking_median is not None and asking_low is not None
        range_low, range_high = round_price(asking_low), round_price(asking_median)
    view = MarketView(
        sold_count=sold_count,
        sold_median_cents=sold_median,
        sold_low_cents=sold_low,
        sold_high_cents=sold_high,
        asking_count=len(asks),
        asking_median_cents=asking_median,
        asking_low_cents=asking_low,
        asking_high_cents=asking_high,
        range_low_cents=range_low,
        range_high_cents=min(range_high, MAX_CENTS),
        sources=sources,
    )
    view.line = market_line(view)
    return view


def reference_cents(view: MarketView | None) -> int | None:
    """The price a seller's own price is judged against: what it sells for, or failing that
    what others ask."""
    if view is None:
        return None
    return (
        view.sold_median_cents if view.sold_median_cents is not None else view.asking_median_cents
    )


def price_verdict(price_cents: int, view: MarketView | None) -> str:
    """ "under" when the price is more than FAR_SHARE below the reference, "over" when it's more
    than FAR_SHARE above, "fine" in between, and "" when there's nothing to compare with."""
    reference = reference_cents(view)
    if reference is None:
        return ""
    if price_cents < reference * (1 - FAR_SHARE):
        return "under"
    if price_cents > reference * (1 + FAR_SHARE):
        return "over"
    return "fine"
