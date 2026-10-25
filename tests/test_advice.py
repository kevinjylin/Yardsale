"""The market view (pricing.market_view) and what the seller is told about it (advice.py): the
fair range, the plain warning when a seller's price is far off, and the listing description."""

from __future__ import annotations

import pytest

from yardsale import advice, haggle
from yardsale.models import (
    Appraisal,
    Comp,
    ItemDetails,
    MarketView,
    MatchedComp,
    ScoutListing,
    ScoutReport,
    SourcePrices,
)
from yardsale.negotiation import answer_offer
from yardsale.pricing import market_line, market_view, price_verdict
from yardsale.store import PROPOSED, Listing, Store

SHOES = ItemDetails(
    title="Nike Air Jordan 4 Retro, size 10",
    brand="Nike",
    model="Air Jordan 4 Retro",
    category="Sneakers",
    condition="good",
    details=["size 10", "white and black", "light creasing"],
    search_query="Nike Air Jordan 4 Retro size 10",
    generic=False,
    confidence="high",
)


def sold(prices: list[int]) -> Appraisal:
    """An appraisal of same-product sales at these prices (dollars)."""
    matches = [
        MatchedComp(
            comp=Comp(
                title=f"Air Jordan 4 Retro sz 10 #{n}",
                price_cents=p * 100,
                shipping_cents=None,
                condition="Pre-Owned",
                sold_at="2026-10-01",
                url=f"https://www.ebay.com/itm/{n}",
                image_url=None,
                best_offer=False,
            ),
            same_product=True,
            similarity=0.9,
            reason="same model",
        )
        for n, p in enumerate(prices)
    ]
    from yardsale.pricing import suggested_prices

    asking, floor, low, high = suggested_prices([p * 100 for p in prices])
    return Appraisal(
        request_id="a",
        comps_considered=len(prices),
        matches=matches,
        basis=f"{len(prices)} eBay sales of the same product in the last 90 days",
        confidence="high",
        asking_cents=asking,
        floor_cents=floor,
        low_cents=low,
        high_cents=high,
    )


def asked(source: str, prices: list[int]) -> SourcePrices:
    """Live listings on one site at these prices (dollars)."""
    from yardsale.pricing import price_range

    cents = sorted(p * 100 for p in prices)
    low, middle, high = price_range(cents)
    return SourcePrices(
        source=source,
        count=len(cents),
        low_cents=low,
        median_cents=middle,
        high_cents=high,
        closest=[
            ScoutListing(
                source=source,
                title=f"Jordan 4 on {source} #{n}",
                price_cents=c,
                url=f"https://{source}.example.test/{n}",
            )
            for n, c in enumerate(cents[:3])
        ],
        prices_cents=cents,
    )


SOLD = sold([160, 170, 180, 190, 200] * 4)  # 20 sales, median $180
LOCAL = ScoutReport(
    request_id="s",
    query="Nike Air Jordan 4 Retro size 10",
    sources=[asked("craigslist", [150, 160, 170, 200]), asked("offerup", [150, 190, 200])],
    failed=[],
)
VIEW = market_view(SOLD, LOCAL)
assert VIEW is not None


def listing(
    seller_asking: int | None = None,
    seller_floor: int | None = None,
    view: MarketView | None = VIEW,
) -> Listing:
    from yardsale.pricing import listing_prices

    asking, floor = listing_prices(SOLD.asking_cents, SOLD.floor_cents, seller_asking, seller_floor)
    return Listing(
        item_id="shoes",
        owner="seller",
        seller_id="payout",
        listing_ref="r",
        status=PROPOSED,
        title=SHOES.title,
        asking_cents=asking,
        floor_cents=floor,
        seller_asking_cents=seller_asking,
        seller_floor_cents=seller_floor,
        suggested_asking_cents=SOLD.asking_cents,
        suggested_floor_cents=SOLD.floor_cents,
        item=SHOES,
        appraisal=SOLD,
        market=view,
    )


# --- The math ---------------------------------------------------------------------------------


def test_the_market_view_pools_live_asking_prices_and_keeps_the_sold_median() -> None:
    assert (VIEW.sold_count, VIEW.sold_median_cents) == (20, 18000)
    assert (VIEW.sold_low_cents, VIEW.sold_high_cents) == (17000, 19000)
    assert VIEW.asking_count == 7  # both sites together
    assert (VIEW.asking_low_cents, VIEW.asking_median_cents, VIEW.asking_high_cents) == (
        15000,
        17000,
        20000,
    )
    # Between what it sells for and what competitors ask, rounded the way a person prices.
    assert (VIEW.range_low_cents, VIEW.range_high_cents) == (17000, 18000)
    assert VIEW.line == (
        "similar ones sold for about $180 on eBay recently, and locals are asking $150 to $200 "
        "right now"
    )


def test_the_range_with_one_side_only() -> None:
    only_sold = market_view(SOLD, None)
    assert only_sold is not None
    assert (only_sold.range_low_cents, only_sold.range_high_cents) == (17000, 19000)
    assert only_sold.line == "similar ones sold for about $180 on eBay recently"
    only_asked = market_view(None, LOCAL)
    assert only_asked is not None
    assert (only_asked.range_low_cents, only_asked.range_high_cents) == (15000, 17000)
    assert only_asked.line == "locals are asking $150 to $200 right now"
    ebay_only = market_view(
        None, ScoutReport(request_id="s", query="q", sources=[asked("ebay", [200, 210])], failed=[])
    )
    assert (
        ebay_only is not None
        and ebay_only.line == "other sellers are asking $200 to $210 right now"
    )
    unpriced = Appraisal(
        request_id="a", comps_considered=0, matches=[], basis="", confidence="none",
        asking_cents=None, floor_cents=None, low_cents=None, high_cents=None,
    )  # fmt: skip
    assert market_view(unpriced, None) is None
    assert (
        market_view(None, ScoutReport(request_id="s", query="q", sources=[], failed=["ebay"]))
        is None
    )


@pytest.mark.parametrize(
    ("cents", "verdict"),
    [
        (4000, "under"),
        (11699, "under"),
        (11700, "fine"),
        (18000, "fine"),
        (24300, "fine"),
        (24301, "over"),
    ],
)
def test_the_verdict_is_35_percent_either_side_of_the_sold_median(cents: int, verdict: str) -> None:
    assert price_verdict(cents, VIEW) == verdict  # $180: under $117 or over $243
    assert price_verdict(cents, None) == ""


def test_the_market_line_never_carries_the_floor() -> None:
    # Floors that share no digits with any market number: the line can't be built from them.
    for floor in (4000, 7700, 13300):
        view = market_view(SOLD, LOCAL)
        assert view is not None
        item = listing(None, floor)
        assert item.floor_cents == floor
        line = market_line(view)
        assert line == item.market_line
        assert str(floor // 100) not in line
        assert "floor" not in line.lower() and "lowest" not in line.lower()


# --- What the seller hears --------------------------------------------------------------------


def test_a_minimum_far_under_the_market_gets_a_plain_warning() -> None:
    text = advice.price_feedback(listing(None, 4000))
    assert text == (
        "Your $40 minimum is far under what these sell for: the last 20 sales were around $180, "
        "and locals are asking $150 to $200. You'll likely give it away at $40. Keep it, or say "
        '"lowest $170".'
    )
    assert advice.price_feedback(listing(None, 4000), n=2).endswith('say "lowest 2 $170".')


def test_an_asking_price_far_over_the_market_gets_a_plain_warning() -> None:
    text = advice.price_feedback(listing(50000, None))
    assert text == (
        "Your $500 asking price is far over what these sell for: the last 20 sales were around "
        "$180, and locals are asking $150 to $200. Buyers will likely pass at $500. Keep it, or "
        'say "ask $180".'
    )


def test_prices_in_line_with_the_market_are_told_so() -> None:
    assert advice.price_feedback(listing(19000, 17000)) == (
        "Your $190 asking price and $170 minimum are in line with the market: the last 20 sales "
        "were around $180, and locals are asking $150 to $200."
    )
    assert advice.price_feedback(listing(None, 17000)) == (
        "Your $170 minimum is in line with the market: the last 20 sales were around $180, and "
        "locals are asking $150 to $200."
    )


def test_both_prices_off_get_both_warnings() -> None:
    text = advice.price_feedback(listing(50000, 4000))
    assert text.count("\n") == 1
    assert "far over" in text.splitlines()[0] and "far under" in text.splitlines()[1]


def test_nothing_to_say_without_a_seller_price_or_a_market() -> None:
    assert advice.price_feedback(listing()) == ""
    assert advice.price_feedback(listing(None, 4000, view=None)) == ""


def test_the_proposal_lines_show_live_listings_and_the_fair_range() -> None:
    lines = advice.market_lines(listing())
    assert lines[0] == (
        "Listed right now: similar ones are asking $150 to $200 (7 listings on Craigslist and "
        "OfferUp)."
    )
    assert lines[1:4] == [
        "- [Jordan 4 on craigslist #2](https://craigslist.example.test/2): $170 on Craigslist",
        "- [Jordan 4 on craigslist #1](https://craigslist.example.test/1): $160 on Craigslist",
        "- [Jordan 4 on craigslist #0](https://craigslist.example.test/0): $150 on Craigslist",
    ]
    assert lines[4] == "A fair asking price is $170 to $180."
    assert advice.market_lines(listing(view=None)) == []
    sold_only = listing(view=market_view(SOLD, None))
    assert advice.market_lines(sold_only) == ["A fair asking price is $170 to $190."]


def test_the_listing_description_is_what_claude_saw_and_what_the_market_says() -> None:
    text = advice.listing_description(listing(None, 4000))
    assert text == (
        "Nike Air Jordan 4 Retro, size 10.\n"
        "Brand: Nike. Model: Air Jordan 4 Retro.\n"
        "Condition: good.\n"
        "Details: size 10; white and black; light creasing.\n"
        "Priced from the market: similar ones sold for about $180 on eBay recently, and locals "
        "are asking $150 to $200 right now."
    )
    assert "$40" not in text
    plain = listing(view=None)
    assert advice.listing_description(plain).endswith("Priced from recent sales of the same item.")


# --- What the buyer hears ---------------------------------------------------------------------


def test_counters_cite_the_market_and_never_the_floor() -> None:
    low = haggle.seller_move(18000, 12300, 500, 5000, haggle.SellerState(), VIEW.line)
    assert low.note == (
        "That's well under what it's worth. I'm at $180. Similar ones sold for about $180 on "
        "eBay recently, and locals are asking $150 to $200 right now."
    )
    counter = haggle.seller_move(18000, 12300, 500, 10000, haggle.SellerState(), VIEW.line)
    assert counter.note == (
        "I can come down to $160. Similar ones sold for about $180 on eBay recently, and locals "
        "are asking $150 to $200 right now."
    )
    for move in (low, counter):
        assert "123" not in move.note and not move.agreed
    agreed = haggle.seller_move(18000, 12300, 500, 13000, haggle.SellerState(), VIEW.line)
    assert agreed.note == "Deal at that price. Accept it to buy."
    assert haggle.seller_move(18000, 12300, 500, 5000, haggle.SellerState()).note == (
        "That's well under what it's worth. I'm at $180."
    )


def test_item_agents_in_the_bureau_pass_the_line_from_the_listing() -> None:
    store = Store()
    item = listing(None, 7700)
    store.add_listing(item)
    store.activate("shoes", "seller", "agent1qshoes")
    reply = answer_offer(store, "shoes", "agent:bob", 6000)
    assert "locals are asking $150 to $200" in reply.note  # type: ignore[union-attr]
    assert "77" not in reply.note  # type: ignore[union-attr]
    store.close()
