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
    # Condition and age are on the line from the listing, so the reason doesn't repeat them.
    assert deal.why == "35% under the $185 typical sold price"
    assert reason(deal, "Craigslist") == (
        "$100 + $20 shipping on Craigslist. Like new. 35% under the $185 typical sold price. "
        "(no photo, location not listed)"
    )
    assert deal.flags == ()


def test_score_wording_about_and_above_the_market() -> None:
    about = score(listing("Road bike", 18200), ["road", "bike"], market_of(18500), None, TODAY)
    above = score(listing("Road bike", 24000), ["road", "bike"], market_of(18500), None, TODAY)
    assert about is not None and above is not None
    assert about.why.startswith("about the $185 typical sold price")
    assert above.why.startswith("30% above the $185 typical sold price")
    assert above.versus_market_pct == -30 and above.score < about.score


def test_far_under_the_market_is_dropped_not_shown() -> None:
    # 89% under what it sells for is a placeholder price or a scam, not a deal to show.
    assert (
        score(listing("Road bike", 2000), ["road", "bike"], market_of(18500), None, TODAY) is None
    )
    # 70% under is the edge: still shown.
    kept = score(listing("Road bike", 5550), ["road", "bike"], market_of(18500), None, TODAY)
    assert kept is not None and "far_under" not in kept.flags


def test_placeholder_prices_under_five_dollars_are_dropped() -> None:
    for cents in (0, 100, 499):
        assert score(listing("Road bike", cents), ["road", "bike"], None, None, TODAY) is None
    assert score(listing("Road bike", 500), ["road", "bike"], None, None, TODAY) is not None


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


# --- The budget: shipping, over-budget fillers, unpriced posts -----------------------------------


@pytest.mark.parametrize(
    ("source", "shipping", "phrase", "known"),
    [
        ("ebay", 1200, "+ $12 shipping", True),
        ("ebay", 0, "free shipping", True),
        ("ebay", None, "shipping unknown", False),
        ("craigslist", None, "pickup only", True),
        ("offerup", None, "pickup only", True),
        ("facebook", None, "pickup only", True),
        ("offerup", 800, "+ $8 shipping", True),
    ],
)
def test_shipping_is_said_known_free_pickup_or_unknown(
    source: str, shipping: int | None, phrase: str, known: bool
) -> None:
    item = listing("Road bike", 10000, source, shipping_cents=shipping)
    assert deals.shipping_phrase(item) == phrase
    assert deals.shipping_known(item) is known


def test_the_budget_counts_known_shipping_and_flags_unknown() -> None:
    budget = 10000
    pickup = listing("Road bike", 5500)
    shipped = listing("Road bike", 9800, "ebay", shipping_cents=700)
    unknown = listing("Road bike", 8000, "ebay")
    exact = listing("Road bike", 10000, "ebay", shipping_cents=0)
    assert deals.under_budget_cents(pickup, budget) == 4500
    assert deals.budget_phrase(pickup, budget) == "$45 under budget"
    assert deals.under_budget_cents(shipped, budget) == -500
    assert deals.budget_phrase(shipped, budget) == "$5 over budget with shipping"
    assert deals.budget_phrase(shipped, budget, qualify=False) == "$5 over budget"
    assert deals.under_budget_cents(unknown, budget) == 2000  # the price alone
    assert deals.budget_phrase(unknown, budget) == "$20 under budget before shipping"
    assert deals.budget_phrase(unknown, budget, qualify=False) == "$20 under budget before shipping"
    assert deals.budget_phrase(exact, budget) == "right at your budget"
    assert deals.budget_phrase(pickup, None) == ""
    assert deals.under_budget_cents(listing("Road bike", None), budget) is None


def test_over_budget_listings_fill_in_only_when_fewer_than_three_fit() -> None:
    over = [
        listing("Trek road bike", 33000),  # 10% over
        listing("Giant road bike", 36000),  # 20% over
        listing("Cannondale road bike", 44000),  # 47% over
        listing("Pinarello road bike", 46000),  # 53% over: too far to show
    ]
    one_fits = rank([listing("Fuji road bike", 25000), *over], "road bike", 30000, today=TODAY)
    assert one_fits[0].listing.title == "Fuji road bike"
    assert len(one_fits) == deals.FILL_TO
    assert "over_budget" not in one_fits[0].flags
    assert all("over_budget" in d.flags for d in one_fits[1:])
    assert "Pinarello road bike" not in {d.listing.title for d in one_fits}

    fits = [listing(f"Road bike {n}", 20000 + n * 1000) for n in range(3)]
    enough = rank([*fits, *over], "road bike", 30000, today=TODAY)
    assert len(enough) == 3 and not any("over_budget" in d.flags for d in enough)

    none_fit = rank(over, "road bike", 30000, today=TODAY)
    assert len(none_fit) == 3 and all("over_budget" in d.flags for d in none_fit)

    # Without a budget nothing is over it.
    assert not any("over_budget" in d.flags for d in rank(over, "road bike", None, today=TODAY))


def test_an_unpriced_post_is_kept_but_always_last() -> None:
    found = rank(
        [
            listing(
                "Trek road bike, like new, 54cm, ready to ride",
                None,
                condition="Like new",
                photo_count=12,
                posted_at="2026-10-24",
            ),
            listing("Road bike", 28000),
            listing("Road bike helmet", None),  # an accessory with no price: left out
            listing("Specialized road bike", None),
            listing("Giant road bike", None),  # a third unpriced one: past UNPRICED_MAX
        ],
        "road bike",
        30000,
        today=TODAY,
    )
    titles = [d.listing.title for d in found]
    assert titles[0] == "Road bike"  # the priced one first, though the unpriced one is nicer
    assert len(titles) == 1 + deals.UNPRICED_MAX
    assert titles[1] == "Trek road bike, like new, 54cm, ready to ride"
    assert all("no_price" in d.flags and d.listing.price_cents is None for d in found[1:])
    assert "Road bike helmet" not in titles


def test_rank_can_keep_only_priced_listings_under_the_budget() -> None:
    found = rank(
        [listing("Road bike", 33000), listing("Trek road bike", None)],
        "road bike",
        30000,
        fill_to=0,
        unpriced_too=False,
    )
    assert found == []


# --- How a deal is shown ------------------------------------------------------------------------


def test_missing_fields_and_their_suffix() -> None:
    bare = listing("Road bike", None)
    assert deals.missing_fields(bare) == ["price", "photo", "condition", "location"]
    assert deals.missing_note(bare) == "no photo, condition not listed, location not listed"
    full = listing(
        "Road bike", 10000, image_url="https://img.test/1.jpg", condition="Used", location="SF"
    )
    assert deals.missing_fields(full) == [] and deals.missing_note(full) == ""


def test_a_line_says_price_shipping_budget_condition_place_age_and_reason() -> None:
    item = listing(
        "Trek FX 2",
        18000,
        condition="Used",
        location="Berkeley",
        posted_at="2026-10-22",
        photo_count=6,
    )
    deal = score(item, ["trek"], market_of(31000), None, TODAY)
    assert deal is not None
    assert reason(deal, "Craigslist", 30000, TODAY) == (
        "$180 on Craigslist, pickup only, $120 under budget. Used, Berkeley, posted 2 days ago. "
        "42% under the $310 typical sold price, 6 photos."
    )
    ebay = listing("Trek FX 2", 9000, "ebay", accepts_offers=True)
    assert deals.describe(ebay, "eBay", "", 10000, TODAY) == (
        "$90 on eBay, shipping unknown, $10 under budget before shipping. Takes offers. "
        "(no photo, condition not listed, location not listed)"
    )
    shipped = listing("Trek FX 2", 9000, "ebay", shipping_cents=1500, location="Reno, NV")
    assert deals.describe(shipped, "eBay", "", 10000, TODAY).startswith(
        "$90 + $15 shipping on eBay, $5 over budget with shipping. Reno, NV."
    )
    free = listing("Trek FX 2", 9000, "ebay", shipping_cents=0)
    assert deals.describe(free, "eBay").startswith("$90 on eBay, free shipping.")
    assert deals.describe(listing("Trek FX 2", None), "Craigslist", "", 10000).startswith(
        "price not listed on Craigslist, pickup only."
    )


def site(source: str) -> str:
    return {"craigslist": "Craigslist", "ebay": "eBay", "offerup": "OfferUp"}[source]


def deal_of(item: ExternalListing, *flags: str) -> ScoredDeal:
    total = item.total_cents if item.total_cents is not None else 0
    return ScoredDeal(item, total, 10.0, None, "", tuple(flags))


FLASK = listing(
    "Hydro Flask 32 oz", 1800, "craigslist", url="https://cl.test/flask", location="Berkeley"
)


def test_the_headline_with_a_budget() -> None:
    assert deals.headline([deal_of(FLASK)], 3000, None, site) == (
        "Found it: [Hydro Flask 32 oz](https://cl.test/flask) on Craigslist for $18, $12 under "
        "your $30 budget (Berkeley, pickup)."
    )
    shipped = listing("Hydro Flask", 1800, "ebay", 700, url="https://e.test/1")
    assert deals.headline([deal_of(shipped)], 3000, None, site) == (
        "Found it: [Hydro Flask](https://e.test/1) on eBay for $25 with shipping, $5 under your "
        "$30 budget."
    )
    free = listing("Hydro Flask", 1800, "ebay", 0, url="https://e.test/2")
    assert deals.headline([deal_of(free)], 1800, None, site) == (
        "Found it: [Hydro Flask](https://e.test/2) on eBay for $18 with free shipping, right at "
        "your $18 budget."
    )
    unknown = listing("Hydro Flask", 1800, "ebay", url="https://e.test/3")
    assert deals.headline([deal_of(unknown)], 3000, None, site) == (
        "Found it: [Hydro Flask](https://e.test/3) on eBay for $18, $12 under your $30 budget "
        "before shipping."
    )


def test_the_headline_without_a_budget_compares_with_the_typical_sold_price() -> None:
    cheap = listing("Hydro Flask", 5000, url="https://cl.test/2", location="Oakland")
    assert deals.headline([deal_of(cheap)], None, market_of(9000), site) == (
        "Found it: [Hydro Flask](https://cl.test/2) on Craigslist for $50, $40 under the typical "
        "$90 sold price (Oakland, pickup)."
    )
    dear = listing("Hydro Flask", 10000, url="https://cl.test/3")
    assert "$10 above the typical $90 sold price" in deals.headline(
        [deal_of(dear)], None, market_of(9000), site
    )
    about = listing("Hydro Flask", 8800, url="https://cl.test/4")
    assert "about the typical $90 sold price" in deals.headline(
        [deal_of(about)], None, market_of(9000), site
    )
    # Nothing to compare with: where and how much, no saving claimed.
    item = listing("Hydro Flask", 5000, "ebay", url="u")
    plain = deals.headline([deal_of(item)], None, None, site)
    assert plain == "Found it: [Hydro Flask](u) on eBay for $50 (shipping unknown)."


def test_the_headline_when_nothing_fits_or_nothing_is_priced() -> None:
    over = listing("Hydro Flask", 3500, "ebay", url="https://e.test/4")
    text = deals.headline([deal_of(over, "over_budget")], 3000, None, site)
    assert text == (
        "Nothing fits your $30 budget yet. Closest: [Hydro Flask](https://e.test/4) on eBay for "
        "$35, $5 over it before shipping."
    )
    unpriced = listing("Hydro Flask", None, url="https://cl.test/5")
    assert deals.headline([deal_of(unpriced, "no_price")], 3000, None, site) == (
        "Found [Hydro Flask](https://cl.test/5) on Craigslist, but the price isn't listed: ask "
        "the seller."
    )
    # A priced deal is the headline even when an unpriced one came first.
    both = [deal_of(unpriced, "no_price"), deal_of(FLASK)]
    assert deals.headline(both, 3000, None, site).startswith("Found it: [Hydro Flask 32 oz]")
    assert deals.headline([], 3000, None, site) == ""


def test_the_headline_is_careful_with_near_misses() -> None:
    near = deals.headline([deal_of(FLASK, "missing_words")], 3000, None, site)
    assert near.startswith("Closest match: [Hydro Flask 32 oz]")
    long = listing("Hydro Flask " + "very " * 20 + "big", 1800, url="u")
    title = deals.headline([deal_of(long)], 3000, None, site).split("[")[1].split("]")[0]
    assert len(title) <= deals.HEADLINE_TITLE and title.endswith("very")
