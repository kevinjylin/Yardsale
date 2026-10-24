import statistics
from decimal import Decimal
from fractions import Fraction

import pytest

from yardsale.pricing import (
    MAX_CENTS,
    NoSuggestedPrice,
    PriceError,
    default_floor,
    listing_prices,
    median,
    parse_usd,
    quartiles,
    round_price,
    suggested_prices,
    to_cents,
    usd,
)


@pytest.mark.parametrize(
    ("cents", "rounded"),
    [
        (1249, 1200),  # $1 steps under $20
        (1250, 1300),  # halves round up, not to even
        (1999, 2000),  # the step comes from the unrounded amount
        (2000, 2000),
        (2249, 2000),  # $5 steps from $20
        (2250, 2500),
        (4730, 4500),
        (9990, 10000),  # $99.90 is under $100, so $5 steps, which reaches $100
        (10000, 10000),  # $10 steps from $100
        (23800, 24000),
        (30, 100),  # never below one step
        (Fraction(24499, 2), 12000),  # $122.495
    ],
)
def test_round_price(cents: int | Fraction, rounded: int) -> None:
    assert round_price(cents) == rounded


@pytest.mark.parametrize(
    "prices",
    [[100], [100, 200], [100, 110, 120], [100, 110, 120, 130], [5, 7, 9, 13, 21, 34, 55, 89]],
)
def test_statistics_are_exact_and_match_python(prices: list[int]) -> None:
    assert median(prices) == Fraction(statistics.median(prices))
    low, high = quartiles(prices)
    if len(prices) >= 4:
        q1, _, q3 = statistics.quantiles(prices, n=4)
        assert (float(low), float(high)) == pytest.approx((q1, q3))
    else:
        assert (low, high) == (min(prices), max(prices))


def test_suggested_prices() -> None:
    # Median $120, lower quartile $105, 70% of the median $84: the quartile wins, and $105 rounds
    # half up to $110 (Python's round() would give $100).
    assert suggested_prices([10000, 11000, 12000, 13000, 14000]) == (12000, 11000, 10500, 13500)
    # A wide spread: 70% of the median ($70) beats the lower quartile ($30).
    asking, floor, _, _ = suggested_prices([2000, 4000, 10000, 10000, 10000, 12000])
    assert (asking, floor) == (10000, 7000)


def test_floor_is_never_above_asking() -> None:
    for prices in ([150], [99, 101], [1999, 2001], [1, 1, 1, 1]):
        asking, floor, _, _ = suggested_prices(prices)
        assert 0 < floor <= asking


class TestListingPrices:
    def test_suggestion_when_the_seller_gives_nothing(self) -> None:
        assert listing_prices(34000, 31000, None, None) == (34000, 31000)

    def test_no_suggestion_and_no_seller_price(self) -> None:
        with pytest.raises(NoSuggestedPrice):
            listing_prices(None, None, None, None)

    def test_seller_prices_win(self) -> None:
        assert listing_prices(34000, 31000, 50000, 40000) == (50000, 40000)

    def test_seller_floor_above_seller_asking_is_refused(self) -> None:
        with pytest.raises(PriceError, match="above your asking"):
            listing_prices(None, None, 10000, 20000)

    def test_seller_asking_keeps_a_suggested_floor_below_it(self) -> None:
        assert listing_prices(34000, 31000, 36000, None) == (36000, 31000)

    def test_seller_asking_below_the_suggested_floor_never_raises_the_asking(self) -> None:
        # The old code took max(asking, floor) and listed at $310 when the seller said $100.
        assert listing_prices(34000, 31000, 10000, None) == (10000, 7000)

    def test_seller_floor_keeps_a_suggested_asking_above_it(self) -> None:
        assert listing_prices(34000, 31000, None, 30000) == (34000, 30000)

    def test_seller_floor_above_the_suggestion_becomes_the_asking_price(self) -> None:
        assert listing_prices(34000, 31000, None, 40000) == (40000, 40000)

    def test_seller_price_with_no_suggestion(self) -> None:
        assert listing_prices(None, None, 1800, None) == (1800, 1300)
        assert listing_prices(None, None, None, 1500) == (1500, 1500)

    @pytest.mark.parametrize("bad", [0, -100, MAX_CENTS + 1])
    def test_out_of_range_prices_are_refused(self, bad: int) -> None:
        with pytest.raises(PriceError):
            listing_prices(None, None, bad, None)


def test_default_floor() -> None:
    assert default_floor(36000) == 25000  # $252 in $10 steps
    assert default_floor(50) == 50  # never above the asking price


@pytest.mark.parametrize(
    ("text", "cents"),
    [("$1,250.50", 125050), ("40", 4000), ("40.5", 4050), ("0.01", 1), (" $7 ", 700)],
)
def test_parse_usd(text: str, cents: int) -> None:
    assert parse_usd(text) == cents


@pytest.mark.parametrize("text", ["0", "-5", "abc", "nan", "inf", "1000000.01", "1e9"])
def test_parse_usd_refuses_bad_amounts(text: str) -> None:
    with pytest.raises(PriceError):
        parse_usd(text)


def test_model_dollars_become_exact_cents() -> None:
    assert to_cents(39.99) == 3999
    assert to_cents(0.1 + 0.2) == 30  # 0.30000000000000004 dollars
    assert to_cents(None) is None
    assert to_cents(0) is None
    assert to_cents(-5) is None
    assert to_cents(float("nan")) is None
    with pytest.raises(PriceError):
        to_cents(5_000_000)
    assert Decimal("1.005").quantize(Decimal("0.01")) == Decimal("1.00")  # banker's: we don't
    assert to_cents(1.005) == 101


def test_usd() -> None:
    assert usd(34000) == "$340"
    assert usd(1250) == "$12.50"
    assert usd(5) == "$0.05"
    assert usd(123456789) == "$1,234,567.89"
