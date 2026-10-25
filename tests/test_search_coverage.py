"""Searching other sites, the October 24 fixes: what the sites are asked for, where, which
listings fit, and telling a failed search from an empty one, per site, in the chat and the API.

Everything is offline: the actor calls are faked, and the live samples are recorded search
results in tests/fixtures (listing data only, no seller details).
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from tests import sim
from tests.test_business_api_elsewhere import api_over
from tests.test_chat_elsewhere import Site, deal
from yardsale import chat, craigslist, deals, ebay, elsewhere, offerup, places, sources
from yardsale.elsewhere import DealFinder, Found, SiteResult, search_words
from yardsale.sources import ExternalListing, SourceError
from yardsale.store import Store

LIVE = json.loads((Path(__file__).parent / "fixtures" / "live_2026_10_24.json").read_text())
TODAY = date(2026, 10, 24)


def listing(
    title: str, cents: int, source: str = "craigslist", location: str = ""
) -> ExternalListing:
    return ExternalListing(
        source=source,
        external_id=f"{title}-{location}",
        title=title,
        price_cents=cents,
        url=f"https://{source}.example.test/{abs(hash((title, location)))}",
        location=location,
    )


# --- What the sites are asked for -------------------------------------------------------------


@pytest.mark.parametrize(
    ("message", "words", "site_query"),
    [
        ("find me a water bottle under $30", "water bottle", "water bottle"),
        ("hydro flask 32 oz", "hydro flask 32 oz", "hydro flask"),
        ("Jordan 4 retro bred size 10", "jordan 4 retro bred size 10", "jordan 4 retro bred"),
        ("air jordan 4 retro under $250", "air jordan 4 retro", "air jordan 4 retro"),
        ("mini fridge under $60", "mini fridge", "mini fridge"),
        ("jordan 4's near 94704", "jordan 4s", "jordan 4"),
        ("water bottle in Berkeley max $30", "water bottle", "water bottle"),
        ("Air Jordan 4 Military Blue 2024 FV5029-141", None, "air jordan 4 military blue"),
    ],
)
def test_searches_keep_model_numbers_and_lose_amounts_places_and_sizes(
    message: str, words: str | None, site_query: str
) -> None:
    if words is not None:
        assert search_words(message) == words
    assert deals.site_query(search_words(message)) == site_query


def test_sizes_and_measurements_are_soft_terms() -> None:
    assert deals.split_terms("jordan 4 retro size 10") == (["jordan", "4", "retro"], ["size10"])
    assert deals.split_terms("hydro flask 32 oz") == (["hydro", "flask"], ["32oz"])
    # A size in a title isn't a model number: "Size 4.5" doesn't say "4".
    assert "4" not in deals.title_words("Air Jordan Retro Size 4.5")
    assert "size4.5" in deals.title_words("Air Jordan Retro Size 4.5")


# --- Where ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("message", "zip_code", "name"),
    [
        ("water bottle in Berkeley", "94704", "Berkeley"),
        ("jordans near 94704", "94704", "Berkeley"),
        ("mini fridge sf", "94103", "San Francisco"),
        ("desk lamp at ucr", "92521", "Riverside"),
        ("couch within 5 miles of 94110", "94110", "94110"),
    ],
)
def test_a_place_in_the_message_becomes_a_zip_code(message: str, zip_code: str, name: str) -> None:
    place, _ = places.split(message)
    assert place is not None and (place.zip_code, place.name) == (zip_code, name)


def test_words_that_only_look_like_places_stay_in_the_search() -> None:
    assert places.split("sf giants hat") == (None, "sf giants hat")
    assert places.split("lamp la") == (None, "lamp la")
    assert places.describe("") == "the SF Bay Area"  # each site's default
    assert places.describe("94704") == "Berkeley (94704)"
    assert places.site_for_zip("92507") == "inlandempire"


async def test_craigslist_searches_around_a_zip_with_a_search_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[dict[str, Any]] = []

    async def fake(actor: str, body: dict[str, Any], limit: int, charge: str) -> list[Any]:
        seen.append(body)
        return []

    monkeypatch.setattr(craigslist, "run_actor", fake)
    await craigslist.search_craigslist("water bottle", "94704", 20)
    await craigslist.search_craigslist("water bottle", "", 20)
    (url,) = seen[0]["searchUrls"]
    assert url.startswith("https://sfbay.craigslist.org/search/sss?")
    assert "query=water+bottle" in url and "postal=94704" in url and "search_distance=10" in url
    assert seen[1]["city"] == "sfbay" and seen[1]["query"] == "water bottle"


async def test_the_place_named_reaches_the_local_sites_only() -> None:
    local, national = Site([deal("water bottle", 1500)]), Site([deal("water bottle", 1200)])
    where: dict[str, str] = {}

    def recording(name: str, site: Site) -> sources.Source:
        async def search(query: str, scope: str, limit: int) -> list[ExternalListing]:
            where[name] = scope
            return await site(query, scope, limit)

        return search

    finder = DealFinder(
        {"craigslist": recording("craigslist", local), "ebay": recording("ebay", national)}
    )
    found = await finder.find("water bottle in Berkeley", 3000)
    assert where == {"craigslist": "94704", "ebay": ""}
    assert found.place == "Berkeley (94704)" and local.calls == ["water bottle"]
    plain = await finder.find("water bottle", 3000)
    assert plain.place == "the SF Bay Area"


# --- Which listings fit -----------------------------------------------------------------------


def test_a_hydro_flask_is_a_water_bottle_and_a_minifridge_a_mini_fridge() -> None:
    assert deals.fits(["water", "bottle"], "Hydro Flask Standard Mouth 21 oz")
    assert deals.fits(["water", "bottle"], "ThermoFlask Blue Insulated")
    assert deals.fits(["mini", "fridge"], "Compact refrigerator, dorm size")
    assert deals.fits(["mini", "fridge"], "Danby minifridge")
    assert not deals.fits(["air", "jordan", "4", "retro"], "Kids chair repair kit")


def test_jordan_4s_fit_a_jordan_4_search() -> None:
    """OfferUp's "Jordan 4's" and "Military black jordan 4's" were dropped on October 24."""
    wanted = deals.search_terms("air jordan 4 retro")
    titles = [x["title"] for x in LIVE["offerup_air_jordan_4_retro"]]
    assert all(deals.fits(wanted, title) for title in titles), titles


@pytest.mark.parametrize(
    "title",
    [
        "Air Jordan 4 Retro Bred, new in box",
        "Jordan 4 worn only once",
        "Light blue water bottle",
        "3 seat sofa",
        "32oz Hydro Flask Water Bottle Wide Mouth W/Straw Lid",
        "iPhone 13 with case and charger",
    ],
)
def test_ordinary_words_no_longer_make_a_whole_item_an_accessory(title: str) -> None:
    wanted = set(deals.search_terms(title.split(",")[0]))
    assert deals.not_the_thing(title, deals.title_words(title), wanted) is None


@pytest.mark.parametrize(
    ("title", "wanted", "kind"),
    [
        ("Hydro Flask Flex Cap", ["hydro", "flask"], "accessory"),
        ("Road bike seat", ["road", "bike"], "accessory"),  # bike parts, in a bike search
        ("Jordan 4 box only", ["jordan", "4"], "parts"),
        ("Laptop stand", ["laptop"], "accessory"),
    ],
)
def test_real_accessories_still_sink(title: str, wanted: list[str], kind: str) -> None:
    assert deals.not_the_thing(title, deals.title_words(title), set(wanted)) == kind


def test_the_same_title_and_price_in_two_towns_is_two_items() -> None:
    """OfferUp had "Mini Fridge" for $60 in Redwood City and in Richmond on October 24."""
    one = listing("Mini Fridge", 6000, "offerup", "Redwood City, CA")
    two = listing("Mini Fridge", 6000, "offerup", "Richmond, CA")
    again = listing("Mini fridge", 6000, "craigslist", "redwood city")
    assert not deals.same_item(one, two)
    assert deals.same_item(one, again)
    ranked = deals.rank([one, two, again], "mini fridge", 8000, today=TODAY)
    assert len(ranked) == 2


def test_when_nothing_fits_the_closest_are_shown_and_flagged() -> None:
    near = [listing("Jordan style sneakers", 9000), listing("Pyrex bowl", 900)]
    ranked = deals.rank(near, "air jordan 4 retro", None, today=TODAY)
    assert [d.listing.title for d in ranked] == ["Jordan style sneakers"]
    assert "loose_match" in ranked[0].flags
    strict = deals.rank([*near, listing("Air Jordan 4 Retro", 15000)], "air jordan 4 retro", None)
    assert [d.listing.title for d in strict] == ["Air Jordan 4 Retro"]


def test_unknown_shipping_never_drops_a_listing() -> None:
    posted = listing("Hydro Flask 32 oz", 2500, "ebay")
    assert posted.shipping_cents is None
    assert deals.rank([posted], "hydro flask", 3000, today=TODAY)


# --- Recorded live samples parse ---------------------------------------------------------------


def test_live_craigslist_results_parse_with_photos_and_the_right_town() -> None:
    parsed = craigslist.parse_listings(LIVE["craigslist_water_bottle"])
    assert len(parsed) == 5 and all(x.url and x.price_cents for x in parsed)
    # `images` came as a string; every post with a photo has one now.
    assert all(x.image_url for x in parsed if x.photo_count)
    # The actor said "Alameda" for a Santa Rosa post; the slug and coordinates say Santa Rosa.
    pokemon = next(x for x in parsed if "Pokemon" in x.title)
    assert pokemon.location == "Santa Rosa" and pokemon.latitude and pokemon.latitude > 38.3


def test_live_offerup_and_ebay_results_rank_for_their_search() -> None:
    found = offerup.parse_listings(LIVE["offerup_air_jordan_4_retro"]) + ebay.parse_listings(
        LIVE["ebay_air_jordan_4_retro"]
    )
    ranked = deals.rank(found, "air jordan 4 retro", 25000, today=TODAY, limit=20)
    titles = [d.listing.title for d in ranked]
    assert "Jordan 4's" in titles
    # Two "Air Jordan 4 retro" posts at $100 in different towns are two deals.
    assert sum(1 for d in ranked if d.listing.price_cents == 10000) == 2


# --- Failures are never "nothing found" --------------------------------------------------------


class FakeResponse:
    def __init__(self, status: int, body: Any) -> None:
        self.status = status
        self.body = body

    async def __aenter__(self) -> FakeResponse:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def json(self) -> Any:
        return self.body

    async def text(self) -> str:
        return self.body if isinstance(self.body, str) else json.dumps(self.body)


class FakeApify:
    """Answers run-sync calls from a list, and the last-run and log lookups from fixed bodies."""

    def __init__(self, runs: list[FakeResponse], last_run: dict[str, Any] | None = None) -> None:
        self.runs = runs
        self.last_run = last_run
        self.log = ""
        self.posts = 0

    def session(self, **kwargs: Any) -> FakeApify:
        return self

    async def __aenter__(self) -> FakeApify:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    def post(self, url: str, **kwargs: Any) -> FakeResponse:
        self.posts += 1
        return self.runs.pop(0)

    def get(self, url: str, **kwargs: Any) -> FakeResponse:
        if url.endswith("/log"):
            return FakeResponse(200, self.log)
        if self.last_run is None:
            return FakeResponse(404, {})
        return FakeResponse(200, {"data": self.last_run})


@pytest.fixture
def apify(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("APIFY_TOKEN", "test-token")
    monkeypatch.setattr(sources, "BUSY_RETRY_SECONDS", (0.0, 0.0))

    def install(fake: FakeApify) -> FakeApify:
        monkeypatch.setattr(sources.aiohttp, "ClientSession", fake.session)
        return fake

    return install


def apify_error(status: int, kind: str, message: str) -> FakeResponse:
    return FakeResponse(status, {"error": {"type": kind, "message": message}})


@pytest.mark.parametrize(
    ("response", "kind"),
    [
        (apify_error(408, "run-timeout-exceeded", "Actor run exceeded the timeout"), "timeout"),
        (
            apify_error(400, "run-failed", "Actor run did not succeed (status: TIMED-OUT)"),
            "timeout",
        ),
        (apify_error(400, "run-failed", "Actor run did not succeed (status: FAILED)"), "error"),
        (apify_error(402, "not-enough-usage-to-run-paid-actor", "no credit"), "credit"),
        (apify_error(400, "invalid-input", "Input is not valid"), "setup"),
        (FakeResponse(500, "Internal Server Error"), "error"),
    ],
)
async def test_each_apify_failure_has_a_kind(apify: Any, response: FakeResponse, kind: str) -> None:
    apify(FakeApify([response]))
    with pytest.raises(SourceError) as failed:
        await sources.run_actor("some~actor", {}, 10, "0.25")
    assert failed.value.kind == kind


async def test_too_many_runs_at_once_is_retried_then_busy(apify: Any) -> None:
    busy = apify_error(402, "concurrent-runs-limit-exceeded", "limit of 5 concurrent runs")
    fake = apify(FakeApify([busy, FakeResponse(201, [{"title": "x"}])]))
    assert await sources.run_actor("some~actor", {}, 10, "0.25") == [{"title": "x"}]
    assert fake.posts == 2
    apify(FakeApify([busy, busy, busy]))
    with pytest.raises(SourceError) as failed:
        await sources.run_actor("some~actor", {}, 10, "0.25")
    assert failed.value.kind == "busy"


async def test_a_dataset_of_only_errors_is_a_blocked_search(apify: Any) -> None:
    apify(FakeApify([FakeResponse(201, [{"error": "Request blocked (403)", "url": "x"}])]))
    with pytest.raises(SourceError) as failed:
        await sources.run_actor("some~actor", {}, 10, "0.25")
    assert failed.value.kind == "blocked"


async def test_an_empty_dataset_is_blocked_only_when_the_run_says_so(apify: Any) -> None:
    failed_requests = {"id": "r1", "status": "SUCCEEDED", "statusMessage": "Total 3 requests: "
                       "0 succeeded, 3 failed."}  # fmt: skip
    apify(FakeApify([FakeResponse(201, [])], failed_requests))
    with pytest.raises(SourceError) as failed:
        await sources.run_actor("some~actor", {}, 10, "0.25")
    assert failed.value.kind == "blocked"

    fine = {"id": "r2", "status": "SUCCEEDED", "statusMessage": "Saved 0 items from 1 search."}
    apify(FakeApify([FakeResponse(201, [])], fine))
    assert await sources.run_actor("some~actor", {}, 10, "0.25") == []

    logged = FakeApify([FakeResponse(201, [])], {"id": "r3", "status": "SUCCEEDED"})
    logged.log = "ERROR Request failed: 403 Forbidden from the site"
    apify(logged)
    with pytest.raises(SourceError) as failed:
        await sources.run_actor("some~actor", {}, 10, "0.25")
    assert failed.value.kind == "blocked"


async def timed_out(query: str, where: str, limit: int) -> list[ExternalListing]:
    raise SourceError("Apify didn't answer within 180s", "timeout")


async def nothing(query: str, where: str, limit: int) -> list[ExternalListing]:
    return []


async def test_each_site_says_whether_it_answered_and_why_not() -> None:
    finder = DealFinder(
        {
            "ebay": Site([deal("water bottle", 1500, "ebay")]),
            "craigslist": timed_out,
            "offerup": nothing,
        }
    )
    found = await finder.find("water bottle", 3000)
    assert found.sites == (
        SiteResult("ebay", "ok", raw=1, kept=1),
        SiteResult("craigslist", "failed", reason="timeout"),
        SiteResult("offerup", "empty"),
    )
    assert found.searched == ("ebay", "offerup") and found.failed == ("craigslist",)


def found_with(sites: tuple[SiteResult, ...], deals_found: tuple[Any, ...] = ()) -> Found:
    return Found(
        query="water bottle",
        budget_cents=3000,
        deals=deals_found,
        searched=tuple(s.source for s in sites if s.answered),
        failed=tuple(s.source for s in sites if not s.answered),
        sites=sites,
        place="the SF Bay Area",
    )


def test_the_chat_never_says_nothing_was_found_when_every_site_failed() -> None:
    found = found_with(
        (
            SiteResult("craigslist", "failed", reason="timeout"),
            SiteResult("ebay", "failed", reason="busy"),
        )
    )
    text = chat.elsewhere_text(found, "eBay and Craigslist")
    assert "didn't find" not in text
    assert "I couldn't search other sites" in text
    assert "Craigslist: timed out" in text and "eBay: too many searches at once" in text


def test_the_chat_says_nothing_only_of_the_sites_that_answered() -> None:
    found = found_with(
        (
            SiteResult("craigslist", "ok", raw=14, kept=0),
            SiteResult("ebay", "empty"),
            SiteResult("offerup", "failed", reason="blocked"),
        )
    )
    text = chat.elsewhere_text(found, "eBay, Craigslist, and OfferUp")
    assert 'I didn\'t find "water bottle" under $30 on Craigslist and eBay' in text
    assert "Craigslist had 14 listings, none that fit; eBay had none." in text
    assert "OfferUp couldn't be searched just now (blocked)" in text


async def test_the_api_reports_a_failed_search_as_failed(store: Store) -> None:
    async for client in api_over(store, {"craigslist": timed_out}):
        status, body = await client.call("GET", "/other-sites", q="water bottle", max_usd=30)
    assert status == 200 and body["status"] == "failed" and body["searched"] == []
    assert body["sites"] == [
        {"site": "Craigslist", "status": "failed", "listings": 0, "fit": 0, "reason": "timed out"}
    ]
    assert "Don't tell the buyer nothing is out there" in body["next_step"]


async def test_the_api_takes_a_zip_and_says_where_it_searched(store: Store) -> None:
    site = Site([deal("water bottle", 1500)])
    async for client in api_over(store, {"craigslist": site}):
        status, body = await client.call(
            "GET", "/other-sites", q="water bottle", max_usd=30, zip="94704"
        )
    assert status == 200 and body["place"] == "Berkeley (94704)"
    assert body["sites"][0]["status"] == "ok" and "around Berkeley (94704)" in body["summary"]


@pytest.fixture
async def berkeley_shop(store: Store) -> AsyncIterator[tuple[sim.Market, dict[str, str]]]:
    where: dict[str, str] = {}

    async def local(query: str, scope: str, limit: int) -> list[ExternalListing]:
        where["craigslist"] = scope
        return [deal("Sigg water bottle", 1500)]

    async with sim.running_market(store, sources={"craigslist": local}) as running:
        yield running, where


async def test_the_chat_searches_where_the_buyer_said(
    berkeley_shop: tuple[sim.Market, dict[str, str]],
) -> None:
    shop, where = berkeley_shop
    text = await shop.user("buyer").say("search other sites for a water bottle in Berkeley")
    assert where == {"craigslist": "94704"}
    assert "Sigg water bottle" in text and "searched around Berkeley (94704)" in text


def test_failure_words_cover_every_kind() -> None:
    for kind in ("timeout", "blocked", "busy", "credit", "setup", "error"):
        assert elsewhere.failure_words(kind)
    assert elsewhere.failure_words("something new") == "an error"


async def test_a_craigslist_post_without_a_link_gets_a_search_link_not_dropped() -> None:
    """The actor nulled 6 of 10 Craigslist links in a Jordan search on October 24."""
    linkless = ExternalListing(
        source="craigslist",
        external_id="1",
        title="Air Jordan 4 Retro Bred",
        price_cents=15000,
        url="",
    )
    found = await DealFinder({"craigslist": Site([linkless])}).find("jordan 4 near 94704", None)
    (shown,) = found.listings
    assert shown.url.startswith("https://sfbay.craigslist.org/search/sss?")
    assert "query=Air+Jordan+4+Retro+Bred" in shown.url and "postal=94704" in shown.url


def test_the_town_is_read_from_a_slug_that_kept_a_year() -> None:
    raw = {
        "id": "1",
        "title": "air jordan 4 retro 2020 new in box sz10-1/2",
        "slug": "richmond-air-jordan-retro-2020-new-in",
        "location": "Oakland",
        "priceValue": 150,
    }
    parsed = craigslist.parse_listing(raw)
    assert parsed is not None and parsed.location == "Richmond"
