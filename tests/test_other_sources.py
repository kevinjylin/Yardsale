"""Parsing OfferUp and Facebook Marketplace listings. Nothing here touches the network: the
samples are the shapes each actor documents, and run_actor is replaced."""

from __future__ import annotations

from typing import Any

import pytest

from yardsale import facebook, offerup
from yardsale.sources import SourceError

# One result from igolaizola/offerup-scraper, as its README's sample shows it (October 24).
OFFERUP_SAMPLE = {
    "__typename": "ModularFeedListing",
    "listingId": "ac5f8f61-a479-3a3b-bd0b-6dbcea95ea9d",
    "title": "Trek FX 2 hybrid bike ",
    "price": "260",
    "formattedPrice": "$260",
    "locationName": "Newark, CA",
    "flags": ["LOCAL_PICKUP"],
    "image": {
        "url": "https://images.offerup.com/abc=250x250/1234.jpg",
        "width": 250,
        "height": 250,
    },
    "url": "https://offerup.com/item/detail/ac5f8f61-a479-3a3b-bd0b-6dbcea95ea9d",
    "conditionText": "Used",
    "isFirmPrice": False,
}

# One result from apify/facebook-marketplace-scraper, as its README's sample shows it.
FACEBOOK_SAMPLE = {
    "facebookUrl": "https://www.facebook.com/marketplace/sanfrancisco/search/?query=road%20bike",
    "listingUrl": "https://www.facebook.com/marketplace/item/1189200412268517",
    "id": "1189200412268517",
    "primary_listing_photo": {
        "__typename": "ProductImage",
        "image": {"uri": "https://scontent.xx.fbcdn.net/v/t45/abc.jpg"},
        "id": "987",
    },
    "listing_price": {
        "formatted_amount": "$65",
        "amount_with_offset_in_currency": "6500",
        "amount": "65.00",
    },
    "strikethrough_price": None,
    "location": {
        "reverse_geocode": {
            "city": "San Francisco",
            "state": "CA",
            "city_page": {"display_name": "San Francisco, California", "id": "1"},
        }
    },
    "is_hidden": False,
    "is_live": True,
    "is_pending": False,
    "is_sold": False,
    "marketplace_listing_title": "Road bike, 54cm",
    "marketplace_listing_seller": {"__typename": "User", "name": "A Person", "id": "pfbid0abc"},
    "delivery_types": ["IN_PERSON"],
}


def recording_actor(monkeypatch: pytest.MonkeyPatch, module: Any, items: list[Any]) -> list[Any]:
    """Replace the module's run_actor; returns the list its calls are recorded in."""
    calls: list[Any] = []

    async def fake(actor: str, body: dict[str, Any], max_results: int, cap: str) -> list[Any]:
        calls.append((actor, body, max_results, cap))
        return items

    monkeypatch.setattr(module, "run_actor", fake)
    return calls


# --- OfferUp -------------------------------------------------------------------------------------


def test_offerup_result_is_parsed() -> None:
    listing = offerup.parse_listing(OFFERUP_SAMPLE)
    assert listing is not None
    assert listing.source == "offerup"
    assert listing.external_id == OFFERUP_SAMPLE["listingId"]
    assert listing.title == "Trek FX 2 hybrid bike"
    assert listing.price_cents == 26000
    assert listing.location == "Newark, CA"
    assert listing.url == OFFERUP_SAMPLE["url"]
    assert listing.image_url == "https://images.offerup.com/abc=250x250/1234.jpg"
    assert listing.condition == "Used"
    assert listing.accepts_offers is True
    assert listing.negotiable is False  # a person on OfferUp, not an agent


def test_offerup_price_falls_back_to_the_shown_price_and_firm_means_no_offers() -> None:
    listing = offerup.parse_listing({**OFFERUP_SAMPLE, "price": None, "isFirmPrice": True})
    assert listing is not None
    assert listing.price_cents == 26000
    assert listing.accepts_offers is False


def test_offerup_missing_link_is_rebuilt_only_from_a_real_id() -> None:
    listing = offerup.parse_listing({**OFFERUP_SAMPLE, "url": None})
    assert listing is not None and listing.url.endswith(str(OFFERUP_SAMPLE["listingId"]))
    odd = offerup.parse_listing({**OFFERUP_SAMPLE, "url": None, "listingId": "../evil?x=1"})
    assert odd is not None and odd.url == ""


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "text",
        {**OFFERUP_SAMPLE, "title": "  "},
        {**OFFERUP_SAMPLE, "listingId": None},
        {**OFFERUP_SAMPLE, "_details": {"isSold": True}},
    ],
)
def test_offerup_drops_what_a_buyer_cant_use(raw: Any) -> None:
    assert offerup.parse_listing(raw) is None


async def test_offerup_search_asks_around_a_zip(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = recording_actor(monkeypatch, offerup, [OFFERUP_SAMPLE, OFFERUP_SAMPLE])
    found = await offerup.search_offerup(" road bike ", "94110", 20)
    assert len(found) == 1  # the repeat is dropped
    actor, body, max_results, cap = calls[0]
    assert actor == offerup.ACTOR
    assert body["query"] == "road bike" and body["zipCode"] == "94110"
    assert body["maxItems"] == 20 and body["fetchDetails"] is False
    assert max_results == 20 and cap == offerup.MAX_CHARGE_USD

    await offerup.search_offerup("road bike", "sfbay", 20)  # not a ZIP: the default
    assert calls[1][1]["zipCode"] == offerup.DEFAULT_ZIP


async def test_offerup_needs_words() -> None:
    with pytest.raises(SourceError):
        await offerup.search_offerup("  ", "", 20)


# --- Facebook Marketplace ------------------------------------------------------------------------


def test_facebook_result_is_parsed_without_the_seller() -> None:
    listing = facebook.parse_listing(FACEBOOK_SAMPLE)
    assert listing is not None
    assert listing.source == "facebook"
    assert listing.external_id == "1189200412268517"
    assert listing.title == "Road bike, 54cm"
    assert listing.price_cents == 6500
    assert listing.location == "San Francisco, CA"
    assert listing.url == FACEBOOK_SAMPLE["listingUrl"]
    assert listing.image_url == "https://scontent.xx.fbcdn.net/v/t45/abc.jpg"
    assert "A Person" not in repr(listing)  # the seller's name isn't kept


def test_facebook_price_falls_back_to_the_shown_price() -> None:
    raw = {**FACEBOOK_SAMPLE, "listing_price": {"formatted_amount": "$1,200", "amount": None}}
    listing = facebook.parse_listing(raw)
    assert listing is not None and listing.price_cents == 120000


@pytest.mark.parametrize(
    "change",
    [{"is_sold": True}, {"is_pending": True}, {"is_live": False}, {"id": ""}],
)
def test_facebook_drops_sold_pending_and_broken_listings(change: dict[str, Any]) -> None:
    assert facebook.parse_listing({**FACEBOOK_SAMPLE, **change}) is None


def test_facebook_search_url_escapes_the_words_and_checks_the_city() -> None:
    url = facebook.search_url("road bike & lock", "oakland")
    assert (
        url == "https://www.facebook.com/marketplace/oakland/search/?query=road%20bike%20%26%20lock"
    )
    assert f"/marketplace/{facebook.DEFAULT_CITY}/" in facebook.search_url("bike", "../../x")


async def test_facebook_search_sends_a_marketplace_url(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = recording_actor(monkeypatch, facebook, [FACEBOOK_SAMPLE])
    found = await facebook.search_facebook("road bike", "", 20)
    assert [listing.price_cents for listing in found] == [6500]
    body = calls[0][1]
    assert body["startUrls"] == [{"url": facebook.search_url("road bike", facebook.DEFAULT_CITY)}]
    assert body["resultsLimit"] == 20 and body["includeListingDetails"] is False


def test_facebook_is_off_unless_turned_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("YARDSALE_FACEBOOK", raising=False)
    assert facebook.enabled() is False
    monkeypatch.setenv("YARDSALE_FACEBOOK", "1")
    assert facebook.enabled() is True
