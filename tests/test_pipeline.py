"""Agents talking to the Market directly: a photo goes through the Identifier, Researcher, and
Appraiser, comes back as a proposal, and becomes an item agent once approved. Claude and Apify
are fakes; the agents, messages, price math, and store are the real ones, in one process."""

from __future__ import annotations

import asyncio
import base64

import pytest

from tests import fakes, sim
from tests.helpers import Peer
from yardsale import bureau_server
from yardsale.models import (
    Appraised,
    ApproveListing,
    CancelListing,
    Comp,
    ItemDetails,
    Listed,
    ListingRejected,
    ListingUpdate,
    ListItem,
    ListPhoto,
    Proposed,
)
from yardsale.researcher import ResearchError
from yardsale.store import ACTIVE, CANCELED, PROPOSED, Store

SELLER_KINDS = (ListingUpdate, Appraised, Proposed, Listed, ListingRejected)


def photo(key: str = "bike", ref: str = "photo-1", **fields: object) -> ListPhoto:
    return ListPhoto(
        listing_ref=ref,
        seller_id="payout-1",
        image_b64=base64.b64encode(fakes.photo_bytes(key)).decode(),
        media_type="image/jpeg",
        **fields,
    )


async def test_a_photo_becomes_a_proposal_then_an_item_agent(
    market: sim.Market, store: Store
) -> None:
    seller = Peer("seller", *SELLER_KINDS)
    await seller.send(market.address, photo())

    stages = [(await seller.receive(ListingUpdate)).stage for _ in range(3)]
    assert stages == ["identify", "research", "appraise"]
    appraisal = (await seller.receive(Appraised)).appraisal
    assert appraisal.confidence == "high"
    assert len(appraisal.matches) == 6  # not the lot of wheels or the road bike
    proposed = await seller.receive(Proposed)
    assert (proposed.asking_cents, proposed.floor_cents) == (34000, 31000)
    assert proposed.asking_from == "appraisal"
    assert store.active() == []  # nothing is for sale before approval

    listed = await seller.ask(market.address, ApproveListing(listing_ref="photo-1"), Listed)
    assert listed.item_id == proposed.item_id and listed.asking_cents == 34000
    assert not listed.almanac_registered  # offline: made here, registered nowhere
    item = store.get(listed.item_id)
    assert item is not None and item.status == ACTIVE and item.address == listed.item_address
    assert item.owner == seller.address and item.seller_id == "payout-1"
    assert bureau_server.runtime().agents[listed.item_id].address == listed.item_address

    # Approving again, or resending the photo, returns the same agent.
    again = await seller.ask(market.address, ApproveListing(listing_ref="photo-1"), Listed)
    resent = await seller.ask(market.address, photo(), Listed)
    assert again.item_address == resent.item_address == listed.item_address
    assert len(bureau_server.runtime().agents) == 1


async def test_seller_prices_win_and_can_change_at_approval(
    market: sim.Market, store: Store
) -> None:
    seller = Peer("seller", *SELLER_KINDS)
    await seller.send(market.address, photo(asking_cents=50000, floor_cents=40000))
    proposed = await seller.receive(Proposed)
    assert (proposed.asking_cents, proposed.floor_cents) == (50000, 40000)
    assert (proposed.asking_from, proposed.floor_from) == ("seller", "seller")
    listed = await seller.ask(
        market.address, ApproveListing(listing_ref="photo-1", asking_cents=45000), Listed
    )
    assert listed.asking_cents == 45000
    assert store.get(listed.item_id).floor_cents == 40000  # type: ignore[union-attr]


async def test_only_the_seller_can_approve_or_cancel(market: sim.Market, store: Store) -> None:
    seller = Peer("seller", *SELLER_KINDS)
    mallory = Peer("mallory", *SELLER_KINDS)
    await seller.send(market.address, photo())
    proposed = await seller.receive(Proposed)

    for request in (ApproveListing(listing_ref="photo-1"), CancelListing(listing_ref="photo-1")):
        rejected = await mallory.ask(market.address, request, ListingRejected)
        assert "no listing" in rejected.reason.lower() or "nothing" in rejected.reason.lower()
    assert store.get(proposed.item_id).status == PROPOSED  # type: ignore[union-attr]

    canceled = await seller.ask(
        market.address, CancelListing(listing_ref="photo-1"), ListingRejected
    )
    assert canceled.reason == "Canceled by the seller."
    assert store.get(proposed.item_id).status == CANCELED  # type: ignore[union-attr]
    late = await seller.ask(market.address, ApproveListing(listing_ref="photo-1"), ListingRejected)
    assert "canceled" in late.reason
    assert store.active() == []


async def test_no_close_sales_and_no_seller_price_means_no_proposal(market: sim.Market) -> None:
    seller = Peer("seller", *SELLER_KINDS)
    await seller.send(market.address, photo("mug"))
    assert (await seller.receive(Appraised)).appraisal.confidence == "none"
    assert "close enough" in (await seller.receive(ListingRejected)).reason


async def test_a_failing_service_is_reported_to_the_seller(market: sim.Market) -> None:
    seller = Peer("seller", *SELLER_KINDS)
    await seller.send(market.address, photo("camera"))
    assert "503" in (await seller.receive(ListingRejected)).reason


async def test_bad_photos_and_prices_are_refused_before_any_paid_call(store: Store) -> None:
    calls = 0

    async def counting(image_b64: str, media_type: str, note: str) -> ItemDetails:
        nonlocal calls
        calls += 1
        return await fakes.identify(image_b64, media_type, note)

    async with sim.running_market(store, identify=counting) as market:
        seller = Peer("seller", *SELLER_KINDS)
        for bad in (
            photo(asking_cents=-5),
            photo(ref="r2", floor_cents=10**12),
            ListPhoto(listing_ref="r3", seller_id="s", image_b64="!!", media_type="image/jpeg"),
            ListPhoto(listing_ref="r4", seller_id="s", image_b64="aGk=", media_type="text/html"),
        ):
            await seller.send(market.address, bad)
            await seller.receive(ListingRejected)
    assert calls == 0


async def test_slow_research_times_out_cleanly(
    store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def never(query: str, condition: str, max_results: int) -> list[Comp]:
        await asyncio.Event().wait()
        return []

    monkeypatch.setattr(bureau_server, "RESEARCH_TIMEOUT", 0.2)
    async with sim.running_market(store, research=never) as market:
        seller = Peer("seller", *SELLER_KINDS)
        await seller.send(market.address, photo())
        assert "didn't answer" in (await seller.receive(ListingRejected)).reason
        assert bureau_server.runtime().pending == {}


async def test_list_item_goes_live_at_once_and_only_once(market: sim.Market, store: Store) -> None:
    seller = Peer("seller", Listed, ListingRejected)
    request = ListItem(
        listing_ref="t-1", title="Oak bookshelf", asking_cents=8000, floor_cents=4800, seller_id="s"
    )
    first, second = await asyncio.gather(
        seller.ask(market.address, request, Listed), seller.ask(market.address, request, Listed)
    )
    assert first.item_address == second.item_address
    assert len(store.active()) == 1
    bad = request.copy(update={"listing_ref": "t-2", "floor_cents": 9000})
    assert "floor" in (await seller.ask(market.address, bad, ListingRejected)).reason


async def test_research_errors_from_the_real_parser_reach_the_seller(store: Store) -> None:
    async def broken(query: str, condition: str, max_results: int) -> list[Comp]:
        raise ResearchError("Apify returned dict, not a list of sales")

    async with sim.running_market(store, research=broken) as market:
        seller = Peer("seller", *SELLER_KINDS)
        await seller.send(market.address, photo())
        assert "not a list" in (await seller.receive(ListingRejected)).reason
