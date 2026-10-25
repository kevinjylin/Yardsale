"""The Market Scout (scout.py): live asking prices from fake sources, and how the Market uses
them when it prices a photo. Nothing reaches Apify."""

from __future__ import annotations

import asyncio
import base64

import pytest

from tests import fakes, sim
from tests.helpers import Peer
from yardsale import bureau_server, scout
from yardsale.models import (
    Appraised,
    ListingRejected,
    ListingUpdate,
    ListPhoto,
    Proposed,
    ScoutReport,
    ScoutRequest,
    ServiceError,
)
from yardsale.sources import ExternalListing, SourceError
from yardsale.store import Store

BIKE = fakes.photo_url("bike")
SELLER_KINDS = (ListingUpdate, Appraised, Proposed, ListingRejected)


def post(
    title: str,
    cents: int | None,
    source: str = "craigslist",
    url: str | None = None,
    location: str = "Oakland",
) -> ExternalListing:
    slug = title.replace(" ", "-").lower()
    return ExternalListing(
        source=source,
        external_id=slug,
        title=title,
        price_cents=cents,
        url=f"https://{source}.example.test/{slug}" if url is None else url,
        location=location,
    )


BIKES = [
    post("Trek FX 2 Disc hybrid bike medium", 30000),
    post("Trek FX 2 Disc hybrid bike", 32000),
    post("Trek FX 2 Disc hybrid bike, like new", 36000),
    post("Trek FX 2 Disc hybrid bike M black", 40000),
    post("Trek FX 2 Disc hybrid, no link", 34000, url=""),
    post("Trek FX 2 Disc wheelset", 9000),  # a part, not the bike
    post("Trek FX 2 Disc hybrid bike", None),  # "contact me"
    post("Trek FX 2 Disc hybrid bike", 100),  # a $1 placeholder
    post("Vintage Pyrex bowl", 2000),  # not the item
]


class Site:
    """A fake source: fixed listings, a call log, and an optional delay."""

    def __init__(self, listings: list[ExternalListing], delay: float = 0.0) -> None:
        self.listings = listings
        self.delay = delay
        self.calls: list[tuple[str, str, int]] = []

    async def __call__(self, query: str, where: str, limit: int) -> list[ExternalListing]:
        self.calls.append((query, where, limit))
        await asyncio.sleep(self.delay)
        return self.listings


async def down(query: str, where: str, limit: int) -> list[ExternalListing]:
    raise SourceError("OfferUp is down")


async def explodes(query: str, where: str, limit: int) -> list[ExternalListing]:
    raise RuntimeError("a parser bug")


def photo(key: str = "bike", ref: str = "photo-1", **fields: object) -> ListPhoto:
    return ListPhoto(
        listing_ref=ref,
        seller_id="payout-1",
        image_b64=base64.b64encode(fakes.photo_bytes(key)).decode(),
        media_type="image/jpeg",
        **fields,
    )


# --- The math ---------------------------------------------------------------------------------


def test_relevant_keeps_priced_listings_of_the_same_kind() -> None:
    kept = scout.relevant("Trek FX 2 Disc hybrid bike medium", BIKES)
    assert [listing.price_cents for listing in kept] == [30000, 32000, 36000, 40000, 34000]


def test_source_prices_are_quartiles_and_the_closest_have_links() -> None:
    prices = scout.source_prices("craigslist", "Trek FX 2 Disc hybrid bike medium", BIKES)
    assert prices is not None
    assert (prices.count, prices.low_cents, prices.median_cents, prices.high_cents) == (
        5,
        31000,
        34000,
        38000,
    )
    assert prices.prices_cents == [30000, 32000, 34000, 36000, 40000]
    # The $340 post has no link, so the three shown are the linked ones nearest the median.
    assert [c.price_cents for c in prices.closest] == [32000, 36000, 30000]
    assert all(c.url and c.source == "craigslist" for c in prices.closest)
    assert scout.source_prices("ebay", "Trek FX 2 Disc hybrid bike", [BIKES[-1]]) is None


def test_zip_codes_scope_offerup_only() -> None:
    assert scout.scope_for("offerup", "94110") == "94110"
    assert scout.scope_for("offerup", "9411") == ""
    assert scout.scope_for("craigslist", "94110") == ""


def test_seller_zip_comes_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("YARDSALE_ZIP", raising=False)
    assert scout.seller_zip() == ""
    monkeypatch.setenv("YARDSALE_ZIP", " 94103 ")
    assert scout.seller_zip() == "94103"
    monkeypatch.setenv("YARDSALE_ZIP", "SF")
    assert scout.seller_zip() == ""


# --- The agent --------------------------------------------------------------------------------


async def test_the_scout_answers_with_every_site_at_once(store: Store) -> None:
    craigslist, ebay = Site(BIKES, delay=0.2), Site(BIKES[:2], delay=0.2)
    sources = {"craigslist": craigslist, "ebay": ebay, "offerup": down, "facebook": explodes}
    async with sim.running_market(store, scout_sources=sources):
        rt = bureau_server.runtime()
        peer = Peer("market2", ScoutReport, ServiceError)
        started = asyncio.get_running_loop().time()
        report = await peer.ask(
            rt.services.scout,
            ScoutRequest(request_id="r1", query="Trek FX 2 Disc hybrid bike", zip_code="94110"),
            ScoutReport,
        )
        elapsed = asyncio.get_running_loop().time() - started
    assert elapsed < 0.4  # both delays overlap
    assert report.request_id == "r1" and sorted(report.failed) == ["facebook", "offerup"]
    assert [(p.source, p.count) for p in report.sources] == [("craigslist", 5), ("ebay", 2)]
    assert craigslist.calls == [("Trek FX 2 Disc hybrid bike", "", 20)]
    assert ebay.calls == [("Trek FX 2 Disc hybrid bike", "", 20)]


async def test_an_empty_query_is_an_error_not_a_search(store: Store) -> None:
    site = Site(BIKES)
    async with sim.running_market(store, scout_sources={"craigslist": site}):
        rt = bureau_server.runtime()
        peer = Peer("market2", ScoutReport, ServiceError)
        error = await peer.ask(
            rt.services.scout, ScoutRequest(request_id="r2", query="  "), ServiceError
        )
    assert "nothing to search" in error.reason and site.calls == []


# --- In the pipeline --------------------------------------------------------------------------


async def test_a_priced_photo_carries_live_asking_prices(store: Store) -> None:
    site = Site(BIKES)
    async with sim.running_market(store, scout_sources={"craigslist": site, "offerup": down}) as m:
        seller = Peer("seller", *SELLER_KINDS)
        await seller.send(m.address, photo())
        proposed = await seller.receive(Proposed)
    assert (proposed.asking_cents, proposed.floor_cents) == (34000, 31000)  # sold prices decide
    listing = store.get(proposed.item_id)
    assert listing is not None and listing.market is not None
    view = listing.market
    assert (view.sold_count, view.sold_median_cents) == (6, 34000)
    assert (view.asking_count, view.asking_median_cents) == (5, 34000)
    assert (view.asking_low_cents, view.asking_high_cents) == (31000, 38000)
    assert (view.range_low_cents, view.range_high_cents) == (34000, 34000)
    assert view.line == (
        "similar ones sold for about $340 on eBay recently, and locals are asking $310 to $380 "
        "right now"
    )
    assert [p.source for p in view.sources] == ["craigslist"]
    assert site.calls == [("Trek FX 2 Disc hybrid bike medium", "", 20)]
    # A restart reads the same view back.
    again = store.get(proposed.item_id)
    assert again is not None and again.market == view


async def test_the_scout_runs_beside_the_researcher(store: Store) -> None:
    scout_started = asyncio.Event()

    async def research(query: str, condition: str, max_results: int) -> list:
        # Serial pricing would never see the Scout start before the research is done.
        await asyncio.wait_for(scout_started.wait(), 2.0)
        return await fakes.research(query, condition, max_results)

    async def source(query: str, where: str, limit: int) -> list[ExternalListing]:
        scout_started.set()
        return BIKES

    async with sim.running_market(
        store, research=research, scout_sources={"craigslist": source}
    ) as m:
        seller = Peer("seller", *SELLER_KINDS)
        await seller.send(m.address, photo())
        proposed = await seller.receive(Proposed)
    listing = store.get(proposed.item_id)
    assert listing is not None and listing.market is not None
    assert listing.market.asking_count == 5


async def test_a_failing_or_slow_scout_never_blocks_a_proposal(
    store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def never(query: str, where: str, limit: int) -> list[ExternalListing]:
        await asyncio.Event().wait()
        return []

    monkeypatch.setattr(bureau_server, "SCOUT_TIMEOUT", 0.2)
    for sources in ({"offerup": down, "facebook": explodes}, {"craigslist": never}):
        async with sim.running_market(store, scout_sources=sources) as m:
            seller = Peer("seller", *SELLER_KINDS)
            await seller.send(m.address, photo(ref=f"photo-{len(sources)}"))
            proposed = await seller.receive(Proposed)
            assert bureau_server.runtime().pending == {}
        listing = store.get(proposed.item_id)
        assert listing is not None and listing.market is not None
        view = listing.market
        assert (view.sold_median_cents, view.asking_median_cents) == (34000, None)
        assert view.line == "similar ones sold for about $340 on eBay recently"
        assert (view.range_low_cents, view.range_high_cents) == (31000, 37000)


async def test_without_sources_nothing_is_searched_and_the_sold_side_still_counts(
    market: sim.Market, store: Store
) -> None:
    seller = Peer("seller", *SELLER_KINDS)
    await seller.send(market.address, photo())
    proposed = await seller.receive(Proposed)
    listing = store.get(proposed.item_id)
    assert listing is not None and listing.market is not None
    assert listing.market.asking_count == 0 and listing.market.sources == []
    assert listing.market_line == "similar ones sold for about $340 on eBay recently"


async def test_no_close_sales_but_live_listings_help_the_seller_price_it(store: Store) -> None:
    mugs = [post(f"Handmade ceramic mug {n}", cents) for n, cents in enumerate((1800, 2200, 2500))]
    async with sim.running_market(store, scout_sources={"offerup": Site(mugs)}) as m:
        seller = m.user("seller")
        reply = await seller.say("sell this", fakes.photo_url("mug"))
        assert "no recent eBay sales were close enough" in reply
        assert (
            "Listed right now: similar ones are asking $18 to $25 (3 listings on OfferUp)." in reply
        )
        assert "A fair asking price is $18 to $20." in reply
        reply = await seller.say("sell it for $18")
        assert "Asking price: $18 (your price)" in reply
        assert (
            "Your $18 asking price is in line with the market: locals are asking $18 to $25."
            in reply
        )
        (listing,) = store.owned(f"asi1:{seller_key(store)}")
        assert listing.market is not None and listing.market.sold_median_cents is None
        assert listing.market_line == "locals are asking $18 to $25 right now"


def seller_key(store: Store) -> str:
    row = store._db.execute("SELECT owner FROM listings").fetchone()
    return str(row["owner"]).removeprefix("asi1:")


# --- @yardsale's price check --------------------------------------------------------------------


async def test_local_prices_is_a_json_block_over_the_servers_sources(store: Store) -> None:
    import json

    sources = {"craigslist": Site(BIKES), "offerup": down}
    async with sim.running_market(store, scout_sources=sources):
        block = await scout.local_prices("Trek FX 2 Disc hybrid bike", "94110")
    json.dumps(block)  # JSON-able
    assert block["query"] == "Trek FX 2 Disc hybrid bike"
    assert (block["count"], block["low_usd"], block["median_usd"], block["high_usd"]) == (
        5,
        310.0,
        340.0,
        380.0,
    )
    assert (block["low"], block["median"], block["high"]) == ("$310", "$340", "$380")
    assert (block["sites"], block["failed"]) == (["craigslist"], ["offerup"])
    assert [(e["title"], e["price_usd"], e["site"]) for e in block["examples"]] == [
        ("Trek FX 2 Disc hybrid bike", 320.0, "craigslist"),
        ("Trek FX 2 Disc hybrid bike, like new", 360.0, "craigslist"),
        ("Trek FX 2 Disc hybrid bike medium", 300.0, "craigslist"),
    ]
    assert all(e["url"].startswith("https://craigslist.example.test/") for e in block["examples"])


async def test_local_prices_answers_in_time_and_without_sources(
    store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(scout, "LOCAL_PRICES_WITHIN", 0.2)
    sources = {"craigslist": Site(BIKES), "offerup": Site(BIKES[:2], delay=5.0)}
    async with sim.running_market(store, scout_sources=sources):
        block = await scout.local_prices("Trek FX 2 Disc hybrid bike")
    assert (block["count"], block["sites"], block["failed"]) == (5, ["craigslist"], ["offerup"])
    async with sim.running_market(store):
        empty = await scout.local_prices("Trek FX 2 Disc hybrid bike")
    assert (empty["count"], empty["sites"], empty["examples"]) == (0, [], [])
    assert empty["median_usd"] is None
