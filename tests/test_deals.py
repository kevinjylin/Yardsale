"""Scoring other sites' listings: the market price from eBay comps, the score and its reason,
ranking, quality signals, and dropping repeats. Pure functions; nothing here is paid."""

from __future__ import annotations

from datetime import date

import pytest

from tests import fakes
from yardsale import deals
from yardsale.deals import (
    ScoredDeal,
    market_price,
    quality,
    rank,
    reason,
    same_item,
    score,
    search_terms,
)
from yardsale.models import Comp
from yardsale.sources import ExternalListing

TODAY = date(2026, 10, 24)


def listing(
    title: str,
    price_cents: int | None,
    source: str = "craigslist",
    shipping_cents: int | None = None,
    url: str | None = None,
    **extra: object,
) -> ExternalListing:
    return ExternalListing(
        source=source,
        external_id=title,
        title=title,
        price_cents=price_cents,
        url=url if url is not None else f"https://{source}.example.test/{abs(hash(title))}",
        shipping_cents=shipping_cents,
        **extra,  # type: ignore[arg-type]
    )


def comp(title: str, dollars: int, best_offer: bool = False, shipping: int | None = None) -> Comp:
    return Comp(
        title=title,
        price_cents=dollars * 100,
        shipping_cents=shipping,
        condition="Pre-Owned",
        sold_at="2026-10-01",
        url=f"https://www.ebay.com/itm/{abs(hash(title))}",
        image_url=None,
        best_offer=best_offer,
    )


# --- The market price ---------------------------------------------------------------------------


def test_market_price_is_the_median_of_sales_of_the_same_thing() -> None:
    market = market_price(fakes.road_bike_comps(), "find me a road bike under $300")
    assert market is not None
    # The four whole bikes ($280, $300, $320, $340); the wheelset, parts, and repair don't count.
    assert market.typical_cents == 31000 and market.sales == 4
    assert market.basis == "4 eBay sales of the same thing in the last 90 days"
    assert market.low_cents <= market.typical_cents <= market.high_cents


def test_market_price_counts_shipping_and_skips_best_offer_sales_when_it_can() -> None:
    comps = [
        comp("Trek road bike", 300, shipping=20),
        comp("Giant road bike", 280),
        comp("Fuji road bike", 290),
        comp("Cannondale road bike", 900, best_offer=True),  # the list price, not what was paid
    ]
    market = market_price(comps, "road bike")
    assert market is not None
    assert market.sales == 3 and market.typical_cents == 29000  # 320, 280, 290 -> 290

    only_offers = [comp("Trek road bike", 300, best_offer=True)] * 3
    market = market_price(only_offers, "road bike")
    assert market is not None and market.sales == 3  # nothing else to use


def test_market_price_falls_back_to_similar_things_and_can_be_none() -> None:
    comps = [comp("Trek road bike 54cm", 300), comp("Trek hybrid bike", 400), comp("Bike", 100)]
    market = market_price(comps, "road bike")
    assert market is not None
    # One full match: the half-matches ("hybrid bike", "Bike") count, and the basis says so.
    assert market.sales == 3 and "similar" in market.basis
    assert market_price([comp("Kitchen table", 50)], "road bike") is None
    assert market_price([], "road bike") is None
    assert market_price(comps, "") is None


def test_search_terms_drop_filler_and_repeats() -> None:
    assert search_terms("find me a cheap road bike, road bikes under 300") == ["road", "bike"]


# --- One listing's score and reason -------------------------------------------------------------


def market_of(cents: int) -> deals.MarketPrice:
    return deals.MarketPrice(cents, cents, cents, 5, "5 eBay sales of the same thing")


def test_score_says_how_far_under_the_typical_sold_price() -> None:
    deal = score(
        listing("Trek road bike", 10000, shipping_cents=2000, condition="Like new"),
        ["road", "bike"],
        market_of(18500),
        None,
        TODAY,
    )
    assert deal is not None
    assert deal.total_cents == 12000 and deal.versus_market_pct == 35
    assert deal.why.startswith("35% under the $185 typical sold price, like new")
    assert reason(deal, "Craigslist") == (
        "$100 + $20 shipping on Craigslist, 35% under the $185 typical sold price, like new"
    )
    assert deal.flags == ()


def test_score_wording_about_and_above_the_market() -> None:
    about = score(listing("Road bike", 18200), ["road", "bike"], market_of(18500), None, TODAY)
    above = score(listing("Road bike", 24000), ["road", "bike"], market_of(18500), None, TODAY)
    assert about is not None and above is not None
    assert about.why.startswith("about the $185 typical sold price")
    assert above.why.startswith("30% above the $185 typical sold price")
    assert above.versus_market_pct == -30 and above.score < about.score


def test_far_under_is_flagged_for_the_buyer_to_check() -> None:
    deal = score(listing("Road bike", 2000), ["road", "bike"], market_of(18500), None, TODAY)
    assert deal is not None
    assert "far_under" in deal.flags
    assert "check it's the whole item" in deal.why


def test_parts_and_accessories_sink_unless_asked_for() -> None:
    whole = score(listing("Road bike", 15000), ["road", "bike"], market_of(18500), None, TODAY)
    parts = score(
        listing("Road bike for parts", 15000), ["road", "bike"], market_of(18500), None, TODAY
    )
    helmet = score(
        listing("Road bike helmet", 15000), ["road", "bike"], market_of(18500), None, TODAY
    )
    assert whole is not None and parts is not None and helmet is not None
    assert parts.score < helmet.score < whole.score
    assert "parts" in parts.flags and "listed as parts or for repair" in parts.why
    assert "accessory" in helmet.flags and "looks like an accessory" in helmet.why
    asked = score(listing("Road bike helmet", 3000), ["bike", "helmet"], None, None, TODAY)
    assert asked is not None and asked.flags == ()


@pytest.mark.parametrize(
    "title",
    [
        "Vintage Modolo Pro Gold Drilled Road Bike Brake Levers",  # ranked 3rd on October 24
        "Shimano 105 road bike shifters",
        "Carbon road bike fork",
        "Road bike frame only",
        "Road bike wheelset",
    ],
)
def test_a_part_priced_far_under_the_market_is_dropped_not_shown(title: str) -> None:
    # 84% under the $600 typical sold price: that's a part priced as a part, not a bargain bike.
    assert score(listing(title, 9500), ["road", "bike"], market_of(60000), None, TODAY) is None
    # Priced like a whole bike, it's still shown, sunk and flagged, for the buyer to judge.
    kept = score(listing(title, 40000), ["road", "bike"], market_of(60000), None, TODAY)
    assert kept is not None and set(kept.flags) & {"parts", "accessory"}
    # Without a market price there's no "far under", so it's only sunk.
    sunk = score(listing(title, 9500), ["road", "bike"], None, None, TODAY)
    assert sunk is not None and set(sunk.flags) & {"parts", "accessory"}


@pytest.mark.parametrize(
    ("title", "wanted", "kind"),
    [
        ("Road bike brake levers", ["road", "bike"], "accessory"),
        ("Trek road bike, carbon frame", ["road", "bike"], None),  # a whole bike with a frame
        ("Road bike frame only", ["road", "bike"], "parts"),
        ("Dyson vacuum charger and cord", ["dyson", "vacuum"], "accessory"),
        ("Sofa cushion covers", ["sofa"], "accessory"),
        ("Standing desk legs", ["standing", "desk"], "accessory"),
        ("Dyson vacuum charger", ["dyson", "charger"], None),  # asked for
        ("Nintendo Switch, box only", ["nintendo", "switch"], "parts"),
    ],
)
def test_part_words_and_phrases(title: str, wanted: list[str], kind: str | None) -> None:
    assert deals.not_the_thing(title, set(deals.words(title)), set(wanted)) == kind


def test_part_sales_never_set_the_market_price() -> None:
    comps = [
        comp("Trek road bike", 600),
        comp("Giant road bike", 580),
        comp("Fuji road bike", 620),
        comp("Road bike brake levers", 40),
        comp("Road bike frame only", 150),
    ]
    market = market_price(comps, "road bike")
    assert market is not None and market.sales == 3 and market.typical_cents == 60000


def test_missing_search_words_cost_points_and_are_named() -> None:
    full = score(listing("Trek road bike", 15000), ["road", "bike"], market_of(18500), None, TODAY)
    partial = score(listing("Trek bike", 15000), ["road", "bike"], market_of(18500), None, TODAY)
    assert full is not None and partial is not None
    assert full.score - partial.score == pytest.approx(deals.MISSING_WORD, abs=1)
    assert "missing_words" in partial.flags and "title doesn't say 'road'" in partial.why
    # More than half the words missing: not a candidate at all.
    assert score(listing("Trek", 15000), ["road", "bike"], None, None, TODAY) is None


def test_unusable_listings_score_nothing() -> None:
    assert score(listing("Road bike", None), ["road", "bike"], None, None, TODAY) is None
    assert score(listing("Road bike", 50), ["road", "bike"], None, None, TODAY) is None
    assert score(listing("Road bike", 15000, url=""), ["road", "bike"], None, None, TODAY) is None


# --- Quality signals ----------------------------------------------------------------------------


def test_quality_reads_condition_photos_rating_title_and_age() -> None:
    plain = listing("Bike", 10000)
    good = listing(
        "Trek FX 2 Disc hybrid bike, medium, matte black",
        10000,
        condition="Like New",
        photo_count=6,
        seller_rating=99.6,
        posted_at="2026-10-22",
    )
    low, low_notes = quality(plain, TODAY)
    high, high_notes = quality(good, TODAY)
    assert high > low
    assert high_notes == ["like new", "6 photos", "seller rated 99.6%", "posted 2 days ago"]
    assert low_notes == []


@pytest.mark.parametrize(
    ("posted", "phrase"),
    [
        ("2026-10-24", "posted today"),
        ("2026-10-23", "posted yesterday"),
        ("2026-10-10", "posted 2 weeks ago"),
        ("2026-07-01", "posted 3 months ago"),
        ("not a date", None),
    ],
)
def test_age_phrases(posted: str, phrase: str | None) -> None:
    _, notes = quality(listing("Road bike", 10000, posted_at=posted), TODAY)
    assert notes == ([phrase] if phrase else [])


def test_condition_words_are_worth_more_when_better() -> None:
    points = [
        quality(listing("Road bike", 10000, condition=c), TODAY)[0]
        for c in ("Like New", "Good", "Used", "For parts or not working", "")
    ]
    assert points == sorted(points, reverse=True) and points[0] > points[-1]


# --- Ranking ------------------------------------------------------------------------------------


def test_rank_puts_price_against_the_market_first_then_quality() -> None:
    found = rank(
        [
            listing("Giant road bike", 21000, "ebay", shipping_cents=3000),  # $240: 23% under
            listing("Trek road bike 54cm", 18000),  # $180: 42% under
            listing("Specialized road bike", 18000, "offerup", condition="Like new"),  # same
            listing("Road bike", 40000),  # over the max
            listing("Road bike helmet", 2500),  # an accessory, far under market: dropped
            listing("Road bike helmet, large", 15000),  # an accessory priced like a bike: sunk
        ],
        "find me a road bike",
        30000,
        market_of(31000),
        today=TODAY,
    )
    assert [d.listing.title for d in found] == [
        "Specialized road bike",  # 42% under, and in better condition than the Trek
        "Trek road bike 54cm",
        "Giant road bike",
        "Road bike helmet, large",
    ]
    assert [d.versus_market_pct for d in found] == [42, 42, 23, 52]
    assert found[-1].total_cents == 15000 and "accessory" in found[-1].flags


def test_rank_without_a_market_judges_matches_against_each_other() -> None:
    found = rank(
        [
            listing("Road bike helmet", 3000),
            listing("Trek road bike", 22000),
            listing("Bike", 9000),
            listing("Specialized road bike", 18000),
            listing("Kitchen table", 5000),
        ],
        "road bike",
        30000,
        today=TODAY,
    )
    # Against the median of the full matches ($200): the cheaper full match first, then the
    # dearer one, then the partial match, then the accessory.
    assert [d.listing.title for d in found] == [
        "Specialized road bike",
        "Trek road bike",
        "Bike",
        "Road bike helmet",
    ]
    assert all(d.versus_market_pct is None for d in found)
    assert found[0].why == ""  # nothing to say without a market or site details


def test_rank_shows_at_most_the_limit() -> None:
    found = rank([listing(f"road bike {n}", 10000 + n * 100) for n in range(9)], "road bike", None)
    assert len(found) == deals.LIMIT and found[0].total_cents == 10000


# --- Repeats ------------------------------------------------------------------------------------


def test_the_same_item_on_two_sites_is_shown_once() -> None:
    cross_posted = [
        listing("Trek FX 2 Disc hybrid bike medium", 25000, "craigslist"),
        listing("Trek FX 2 Disc Hybrid Bike - Medium", 25000, "offerup"),
        listing("Trek FX 2 Disc hybrid bike medium", 25500, "facebook"),  # within 3%
        listing("Trek FX 2 Disc hybrid bike medium", 20000, "ebay"),  # a different price: a deal
    ]
    found = rank(cross_posted, "trek fx hybrid bike", None, today=TODAY)
    assert [d.listing.source for d in found] == ["ebay", "craigslist"]


def test_near_duplicate_titles_need_near_the_same_price() -> None:
    a = listing("Specialized Allez road bike 56cm", 30000, "craigslist")
    b = listing("Specialized Allez road bike 56cm, red", 30000, "offerup")
    c = listing("Specialized Allez road bike 56cm", 20000, "ebay")
    assert same_item(a, b) and not same_item(a, c)
    assert not same_item(a, listing("Giant Contend road bike", 30000))


def test_repeats_keep_the_better_scored_one() -> None:
    worse = ScoredDeal(listing("Road bike", 20000, "craigslist"), 20000, 10.0, None, "")
    better = ScoredDeal(listing("Road bike", 20000, "ebay"), 20000, 20.0, None, "")
    assert deals.without_repeats([better, worse]) == [better]
