"""Parsing listings from other sites. Nothing here touches the network."""

from __future__ import annotations

from typing import Any

import pytest

from yardsale import sources
from yardsale.craigslist import parse_listing, parse_listings, search_craigslist
from yardsale.sources import (
    ExternalListing,
    SourceError,
    cents_or_none,
    coordinate,
    deduplicated,
    first_web_link,
    iso_date,
    photo_count,
    run_actor,
)

# Shape of one result from the logiover/craigslist-scraper actor (from its output schema).
SAMPLE = {
    "id": "7891234567",
    "title": "  Trek FX 2 Disc hybrid bike\n  size M  ",
    "price": "$320",
    "priceValue": 320,
    "category": "bik",
    "location": "sunset / parkside",
    "latitude": 37.7533,
    "longitude": -122.4934,
    "postedDate": "2026-10-19T14:22:00.000Z",
    "slug": "trek-fx-2-disc",
    "images": ["https://images.craigslist.org/00a0a_abc123_600x450.jpg"],
    "imageCount": 4,
    "url": "https://sfbay.craigslist.org/sfc/bik/d/trek-fx-2-disc/7891234567.html",
    "source": "craigslist",
}


def test_parses_an_actor_result() -> None:
    listing = parse_listing(SAMPLE)
    assert listing is not None
    assert listing.source == "craigslist"
    assert listing.external_id == "7891234567"
    assert listing.title == "Trek FX 2 Disc hybrid bike size M"  # whitespace collapsed
    assert listing.price_cents == 32000
    assert listing.location == "sunset / parkside"
    assert listing.latitude == 37.7533
    assert listing.longitude == -122.4934
    assert listing.posted_at == "2026-10-19"
    assert listing.image_url == SAMPLE["images"][0]  # type: ignore[index]


def test_nothing_scraped_is_negotiable() -> None:
    """The seller is a person on Craigslist, so no agent can haggle for this item."""
    listing = parse_listing(SAMPLE)
    assert listing is not None and listing.negotiable is False


def test_price_falls_back_to_the_displayed_string() -> None:
    listing = parse_listing({**SAMPLE, "priceValue": None, "price": "$1,250.50"})
    assert listing is not None and listing.price_cents == 125050


def test_free_items_are_kept_at_zero() -> None:
    listing = parse_listing({**SAMPLE, "priceValue": 0, "price": "free"})
    assert listing is not None and listing.price_cents == 0


def test_a_listing_with_no_usable_price_is_still_browsable() -> None:
    """Unlike an eBay comp, a listing we can't price is still worth showing a buyer."""
    listing = parse_listing({**SAMPLE, "priceValue": None, "price": "contact me"})
    assert listing is not None and listing.price_cents is None


@pytest.mark.parametrize("price", ["NaN", "Infinity", "-5", "1e12", True, [], {"x": 1}])
def test_malformed_prices_become_no_price(price: object) -> None:
    listing = parse_listing({**SAMPLE, "priceValue": price, "price": price})
    assert listing is not None and listing.price_cents is None


# One real result from a live actor run on 2026-10-24 (query "bike", city "sfbay"). Note the
# null url: the actor leaves it out on many listings, so it can't be a requirement.
REAL = {
    "id": "58481048",
    "title": "Paw patrol tricycle",
    "price": "$20",
    "priceValue": 20,
    "category": "sss",
    "location": "sunset / parkside",
    "latitude": 37.7441,
    "longitude": -122.4863,
    "postedDate": "2026-10-24T20:28:14.000Z",
    "slug": "san-francisco-paw-patrol-tricycle",
    "images": [
        "https://images.craigslist.org/00V0V_eI4RFkHleLU_0CI0t2_600x450.jpg",
        "https://images.craigslist.org/00c0c_eqitTGORU9x_0t20CI_600x450.jpg",
    ],
    "imageCount": 3,
    "url": None,
    "source": "sfbay.craigslist.org",
    "scrapedAt": "2026-10-24T20:29:26.581Z",
}


def test_parses_a_real_actor_result() -> None:
    listing = parse_listing(REAL)
    assert listing is not None
    assert listing.external_id == "58481048"
    assert listing.title == "Paw patrol tricycle"
    assert listing.price_cents == 2000
    assert listing.location == "sunset / parkside"
    assert listing.latitude == 37.7441 and listing.longitude == -122.4863
    assert listing.posted_at == "2026-10-24"
    assert listing.image_url == REAL["images"][0]  # type: ignore[index]
    assert listing.photo_count == 3  # imageCount, not the trimmed images list
    assert listing.condition == ""  # only on the detail page, which costs a request each


def test_photo_count_falls_back_to_the_images_list() -> None:
    trimmed = {k: v for k, v in REAL.items() if k != "imageCount"}
    listing = parse_listing(trimmed)
    assert listing is not None and listing.photo_count == 2
    assert photo_count(None) is None and photo_count(True) is None and photo_count("8") == 8
    assert photo_count(-1) is None and photo_count({"url": "https://x.test/a.jpg"}) == 1


def test_a_listing_with_no_link_is_still_kept() -> None:
    """The actor nulls `url` on many results. Requiring one dropped 3 of the first 5 real ones."""
    listing = parse_listing(REAL)
    assert listing is not None and listing.url == ""


def test_title_and_id_are_what_a_listing_needs() -> None:
    assert parse_listing({**SAMPLE, "title": ""}) is None
    assert parse_listing({**SAMPLE, "title": "   \n "}) is None
    assert parse_listing({**SAMPLE, "id": ""}) is None
    assert parse_listing({**SAMPLE, "id": None}) is None


def test_a_junk_link_is_dropped_but_the_listing_survives() -> None:
    for bad in ("javascript:alert(1)", "file:///etc/passwd", ""):
        listing = parse_listing({**SAMPLE, "url": bad})
        assert listing is not None and listing.url == ""


@pytest.mark.parametrize("raw", [None, "a string", 42, ["list"], {}])
def test_results_that_are_not_listings_are_skipped(raw: object) -> None:
    assert parse_listing(raw) is None


def test_only_web_photo_links_are_kept() -> None:
    listing = parse_listing({**SAMPLE, "images": ["file:///etc/passwd", "data:image/png;base64,x"]})
    assert listing is not None and listing.image_url is None


def test_photos_can_be_wrapped_in_objects_or_missing() -> None:
    wrapped = parse_listing({**SAMPLE, "images": [{"url": "https://x.test/a.jpg"}]})
    assert wrapped is not None and wrapped.image_url == "https://x.test/a.jpg"
    for empty in ([], None, "", {}):
        listing = parse_listing({**SAMPLE, "images": empty})
        assert listing is not None and listing.image_url is None


@pytest.mark.parametrize(
    ("latitude", "longitude"),
    [
        (None, None),
        ("", ""),
        (91.0, 0.0),  # out of range, and its valid partner goes with it
        (0.0, 181.0),
        (37.75, None),  # half a position is dropped: distance code must not guess the rest
        (None, -122.49),
        (0.0, 0.0),  # scrapers use zero for "missing"; nothing is really at null island
        ("north", "west"),
        (True, True),
    ],
)
def test_unusable_coordinates_become_none(latitude: object, longitude: object) -> None:
    listing = parse_listing({**SAMPLE, "latitude": latitude, "longitude": longitude})
    assert listing is not None
    assert listing.latitude is None and listing.longitude is None


def test_coordinates_arriving_as_strings_are_read() -> None:
    listing = parse_listing({**SAMPLE, "latitude": "37.75", "longitude": "-122.49"})
    assert listing is not None
    assert listing.latitude == 37.75 and listing.longitude == -122.49


def test_nan_coordinates_are_rejected() -> None:
    assert coordinate("nan", 90.0) is None
    assert coordinate(float("inf"), 90.0) is None


@pytest.mark.parametrize("posted", [None, "", "not a date", "2026/10/19", 20261019])
def test_unreadable_dates_become_empty(posted: object) -> None:
    listing = parse_listing({**SAMPLE, "postedDate": posted})
    assert listing is not None and listing.posted_at == ""


def test_iso_date_keeps_only_the_day() -> None:
    assert iso_date("2026-10-19T14:22:00.000Z") == "2026-10-19"
    assert iso_date("2026-10-19") == "2026-10-19"


def test_repeated_posts_are_dropped_once() -> None:
    listings = parse_listings([SAMPLE, dict(SAMPLE), {**SAMPLE, "url": "https://x.test/other"}])
    assert len(listings) == 2


def test_bad_results_do_not_fail_the_whole_search() -> None:
    listings = parse_listings([SAMPLE, None, "junk", {"title": "no url"}, 7])
    assert len(listings) == 1 and listings[0].external_id == "7891234567"


def test_dedup_falls_back_to_the_source_id_without_a_link() -> None:
    one = ExternalListing(source="craigslist", external_id="a", title="t", url="", price_cents=1)
    assert len(deduplicated([one, one])) == 1


def test_searchable_text_is_the_public_words() -> None:
    listing = parse_listing(SAMPLE)
    assert listing is not None
    assert listing.searchable_text() == "Trek FX 2 Disc hybrid bike size M sunset / parkside"


def test_cents_and_links_reject_junk() -> None:
    assert cents_or_none("$12.34") == 1234
    assert cents_or_none("10.005") == 1001  # half a cent rounds up
    assert cents_or_none(None) is None
    assert first_web_link("https://x.test/a.jpg") == "https://x.test/a.jpg"
    assert first_web_link("ftp://x.test/a.jpg") is None


async def test_run_actor_caps_both_the_charge_and_the_items_returned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Apify's maxItems only caps billing; limit caps the dataset read. Both go on the request,
    because an actor can ignore its own max-results input."""
    seen: dict[str, Any] = {}

    class FakeResponse:
        status = 200

        async def __aenter__(self) -> FakeResponse:
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def json(self) -> list[Any]:
            return []

        async def text(self) -> str:
            return ""

    class FakeSession:
        def __init__(self, **kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> FakeSession:
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        def post(self, url: str, **kwargs: Any) -> FakeResponse:
            seen.update(url=url, **kwargs)
            return FakeResponse()

    monkeypatch.setenv("APIFY_TOKEN", "test-token")
    monkeypatch.setattr(sources.aiohttp, "ClientSession", FakeSession)
    assert await run_actor("some~actor", {"q": 1}, 10, "0.25") == []
    assert seen["params"]["maxItems"] == "10" and seen["params"]["limit"] == "10"
    assert seen["params"]["maxTotalChargeUsd"] == "0.25" and seen["json"] == {"q": 1}
    assert "test-token" in seen["headers"]["Authorization"]


async def test_no_apify_token_is_a_readable_error() -> None:
    """conftest strips the key, so this raises before any request is made."""
    with pytest.raises(SourceError, match="APIFY_TOKEN is not set"):
        await run_actor("some~actor", {}, 10, "0.25")


async def test_an_empty_search_never_reaches_apify() -> None:
    with pytest.raises(SourceError, match="nothing to search"):
        await search_craigslist("   ", "sfbay", 20)
