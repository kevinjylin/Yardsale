"""End to end: a photo goes through the Market, Identifier, Researcher, and Appraiser, and a live
item agent comes out. Claude and Apify are replaced with fakes; the agents, messages, and price
math are the real ones, all in one process with no network."""

from __future__ import annotations

import asyncio
import base64
from typing import Any
from uuid import uuid4

from uagents import Agent, Context, Model, Protocol

from tests.fakes import (
    BIKE,
    fake_identify,
    fake_judge,
    fake_research,
    jpeg_bytes,
    judge_nothing_matches,
    research_fails,
    run_agents,
)
from yardsale import bureau_server
from yardsale.appraiser import Judge
from yardsale.identifier import Identify
from yardsale.models import Appraised, Listed, ListingRejected, ListingUpdate, ListPhoto
from yardsale.researcher import Research


async def list_photo(
    identify: Identify = fake_identify,
    research: Research = fake_research,
    judge: Judge = fake_judge,
    **photo_fields: Any,
) -> list[Model]:
    """Run one photo listing from a test seller and return everything the Market sent back."""
    _, market, services = bureau_server.build(True, identify, research, judge)
    received: list[Model] = []
    done = asyncio.Event()
    proto = Protocol(name="TestSeller", version="0.1.0")

    @proto.on_message(ListingUpdate)
    async def on_update(ctx: Context, sender: str, msg: ListingUpdate) -> None:
        received.append(msg)

    @proto.on_message(Appraised)
    async def on_appraised(ctx: Context, sender: str, msg: Appraised) -> None:
        received.append(msg)

    @proto.on_message(Listed)
    async def on_listed(ctx: Context, sender: str, msg: Listed) -> None:
        received.append(msg)
        done.set()

    @proto.on_message(ListingRejected)
    async def on_rejected(ctx: Context, sender: str, msg: ListingRejected) -> None:
        received.append(msg)
        done.set()

    seller = Agent(
        name="test-seller",
        seed=uuid4().hex,
        loop=asyncio.get_running_loop(),
        enable_agent_inspector=False,
        publish_agent_details=False,
    )
    seller.include(proto)

    @seller.on_event("startup")
    async def send(ctx: Context) -> None:
        await ctx.send(
            market.address,
            ListPhoto(
                listing_ref="photo-1",
                seller_id="seller-1",
                image_b64=base64.b64encode(jpeg_bytes()).decode(),
                media_type="image/jpeg",
                **photo_fields,
            ),
        )

    await run_agents([market, *services, seller], done)
    return received


def first(received: list[Model], kind: type[Model]) -> Any:
    return next(m for m in received if isinstance(m, kind))


async def test_photo_becomes_a_priced_item_agent() -> None:
    received = await list_photo()

    stages = [m.stage for m in received if isinstance(m, ListingUpdate)]
    assert stages == ["identify", "research", "appraise"]

    appraisal = first(received, Appraised).appraisal
    assert appraisal.confidence == "high"
    assert len(appraisal.matches) == 5  # the road bike isn't the same product
    assert appraisal.asking_cents == 34000

    listed = first(received, Listed)
    assert listed.title == BIKE.title
    assert listed.asking_cents == 34000
    listing = bureau_server.runtime().listings[listed.item_address]
    assert listing.floor_cents == appraisal.floor_cents
    assert listing.seller_id == "seller-1"


async def test_seller_prices_win_over_the_appraisal() -> None:
    received = await list_photo(asking_cents=50000, floor_cents=40000)
    listed = first(received, Listed)
    assert listed.asking_cents == 50000
    assert bureau_server.runtime().listings[listed.item_address].floor_cents == 40000


async def test_no_close_sales_means_no_listing() -> None:
    received = await list_photo(judge=judge_nothing_matches)
    assert first(received, Appraised).appraisal.confidence == "none"
    assert "close enough" in first(received, ListingRejected).reason
    assert not any(isinstance(m, Listed) for m in received)


async def test_a_failing_service_is_reported_to_the_seller() -> None:
    received = await list_photo(research=research_fails)
    assert "401" in first(received, ListingRejected).reason
