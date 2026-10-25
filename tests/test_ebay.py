"""Parsing eBay's live listings. Nothing here touches the network.

SAMPLE is a real result from a live actor run on 2026-10-24, kept verbatim so the parser is
pinned to what the actor really sends rather than what its docs say.
"""

from __future__ import annotations

import pytest

from yardsale.ebay import parse_listing, parse_listings, search_ebay
from yardsale.sources import SourceError

SAMPLE = {
    "itemId": "178500842055",
    "title": "Trek FX 2 700C Hybrid Bike size M Blue Hydraulic Disc",
    "url": "https://www.ebay.com/itm/178500842055",
    "price": 800,  # the actor sends dollars, not cents
    "priceMax": None,
    "priceText": "$800.00",
    "currency": "USD",
    "buyItNowPrice": None,
    "originalPriceText": None,
    "shippingText": "+$99.00 delivery",
    "shippingCost": 99,
    "condition": "New (Other)",
    "listingType": "buy_it_now",
    "bestOffer": True,
    "bids": None,
    "timeLeft": None,
    "endsText": None,
    "locationText": "United States",
    "soldCount": None,
    "watchers": None,
    "sellerFeedbackPercent": 75,
    "sellerFeedbackCount": 38,
    "isSponsored": False,
    "fewerWordsMatch": False,
    "imageUrl": "https://i.ebayimg.com/images/g/KM8AAeSwTJBqtdYt/s-l500.webp",
    "site": "ebay.com",
    "search": "trek fx 2 disc bike (ebay.com)",
    "page": 1,
    "position": 1,
    "scrapedAt": "2026-10-24T20:39:01.178Z",
}


def test_parses_a_real_actor_result() -> None:
    listing = parse_listing(SAMPLE)
    assert listing is not None
    assert listing.source == "ebay"
    assert listing.external_id == "178500842055"
    assert listing.price_cents == 80000  # $800 in dollars -> cents
    assert listing.shipping_cents == 9900
    assert listing.condition == "New (Other)"
    assert listing.location == "United States"
    assert listing.accepts_offers is True
    assert listing.url == SAMPLE["url"]
    assert listing.seller_rating == 75.0  # sellerFeedbackPercent
    assert listing.photo_count == 1  # one imageUrl
    assert listing.posted_at == ""  # the actor gives no listing date


def test_total_includes_shipping() -> None:
    """$800 plus $99 delivery is what the buyer pays, and what a local pickup competes with."""
    listing = parse_listing(SAMPLE)
    assert listing is not None and listing.total_cents == 89900


def test_an_ebay_listing_is_not_agent_negotiable() -> None:
    """eBay has no public buyer-side Best Offer API, so an agent still can't haggle here."""
    listing = parse_listing(SAMPLE)
    assert listing is not None
    assert listing.negotiable is False and listing.accepts_offers is True


def test_sponsored_results_are_ads_not_answers() -> None:
    """A third of the first real search was sponsored, for other items entirely."""
    assert parse_listing({**SAMPLE, "isSponsored": True}) is None


def test_buy_it_now_price_wins_over_the_current_bid() -> None:
    listing = parse_listing({**SAMPLE, "price": 50, "buyItNowPrice": 275})
    assert listing is not None and listing.price_cents == 27500


def test_price_falls_back_to_the_displayed_text() -> None:
    listing = parse_listing({**SAMPLE, "price": None, "priceText": "$1,250.50"})
    assert listing is not None and listing.price_cents == 125050


def test_other_currencies_are_left_unpriced() -> None:
    """A listing in yen is real but not comparable, so it keeps its place with no price."""
    listing = parse_listing({**SAMPLE, "currency": "JPY"})
    assert listing is not None and listing.price_cents is None


def test_a_missing_currency_is_assumed_usd() -> None:
    listing = parse_listing({**SAMPLE, "currency": None})
    assert listing is not None and listing.price_cents == 80000


def test_title_and_item_id_are_what_a_listing_needs() -> None:
    assert parse_listing({**SAMPLE, "title": ""}) is None
    assert parse_listing({**SAMPLE, "title": "  \n "}) is None
    assert parse_listing({**SAMPLE, "itemId": ""}) is None
    assert parse_listing({**SAMPLE, "itemId": None}) is None


@pytest.mark.parametrize("raw", [None, "a string", 42, ["list"], {}])
def test_results_that_are_not_listings_are_skipped(raw: object) -> None:
    assert parse_listing(raw) is None


@pytest.mark.parametrize("price", ["NaN", "Infinity", "-5", "1e12", True, [], {"x": 1}])
def test_malformed_prices_become_no_price(price: object) -> None:
    listing = parse_listing({**SAMPLE, "price": price, "priceText": price, "buyItNowPrice": price})
    assert listing is not None and listing.price_cents is None


def test_unknown_shipping_is_none_but_free_is_zero() -> None:
    unknown = parse_listing({**SAMPLE, "shippingCost": None})
    assert unknown is not None and unknown.shipping_cents is None
    assert unknown.total_cents == 80000  # unknown shipping doesn't inflate the total
    free = parse_listing({**SAMPLE, "shippingCost": 0, "shippingText": "Free delivery"})
    assert free is not None and free.shipping_cents == 0


def test_a_junk_link_is_dropped_but_the_listing_survives() -> None:
    listing = parse_listing({**SAMPLE, "url": "javascript:alert(1)"})
    assert listing is not None and listing.url == ""


def test_only_web_photo_links_are_kept() -> None:
    listing = parse_listing({**SAMPLE, "imageUrl": "file:///etc/passwd"})
    assert listing is not None and listing.image_url is None


def test_the_same_item_twice_is_dropped_once() -> None:
    listings = parse_listings([SAMPLE, dict(SAMPLE), {**SAMPLE, "itemId": "2", "url": ""}])
    assert len(listings) == 2


def test_ads_and_junk_do_not_fail_the_whole_search() -> None:
    listings = parse_listings([SAMPLE, {**SAMPLE, "isSponsored": True}, None, "junk", 7, {}])
    assert len(listings) == 1 and listings[0].external_id == "178500842055"


async def test_an_empty_search_never_reaches_apify() -> None:
    with pytest.raises(SourceError, match="nothing to search"):
        await search_ebay("   ", "ebay.com", 20)


async def test_no_apify_token_is_a_readable_error() -> None:
    """conftest strips the key, so this raises before any request is made."""
    with pytest.raises(SourceError, match="APIFY_TOKEN is not set"):
        await search_ebay("road bike", "ebay.com", 5)
