"""Money rules: integer cents, rounding, seller prices versus suggestions, and counter-offers.

Prices are integer cents everywhere. Amounts people type are parsed with Decimal, and the
appraisal statistics use Fraction, so no float ever decides a price.
"""

from __future__ import annotations

import math
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from fractions import Fraction

MAX_CENTS = 100_000_000  # $1,000,000. Anything above is a typo.
FLOOR_SHARE = Fraction(7, 10)  # the lowest price is never below 70% of the typical price


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
