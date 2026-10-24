"""Stand-ins for Claude, Apify, ASI:One, and photo downloads, and a way to run agents in-process."""

from __future__ import annotations

import asyncio
import io
from uuid import uuid4

from PIL import Image
from uagents import Agent

from yardsale.appraiser import Judgement
from yardsale.asione import SellerMessage
from yardsale.models import Comp, ItemDetails
from yardsale.photos import PhotoError
from yardsale.researcher import ResearchError

BIKE = ItemDetails(
    title="Trek FX 2 Disc hybrid bike, medium",
    brand="Trek",
    model="FX 2 Disc",
    category="Bicycles",
    condition="good",
    details=["medium frame", "matte black"],
    search_query="Trek FX 2 Disc hybrid bike medium",
    generic=False,
    confidence="high",
)


def _comp(dollars: int, title: str) -> Comp:
    return Comp(
        title=title,
        price_cents=dollars * 100,
        shipping_cents=None,
        condition="Pre-Owned",
        sold_at="2026-10-01",
        url=f"https://www.ebay.com/itm/{uuid4().hex[:10]}",
        image_url=None,
        best_offer=False,
    )


COMPS = [
    *(_comp(d, "Trek FX 2 Disc Hybrid Bike M") for d in (300, 320, 340, 360, 380)),
    _comp(900, "Trek Domane AL 2 road bike"),
]


def jpeg_bytes() -> bytes:
    image = io.BytesIO()
    Image.new("RGB", (8, 8), "red").save(image, "JPEG")
    return image.getvalue()


async def fake_identify(image_b64: str, media_type: str, note: str) -> ItemDetails:
    assert media_type == "image/jpeg" and image_b64
    return BIKE


async def fake_research(query: str, condition: str, max_results: int) -> list[Comp]:
    assert query == BIKE.search_query and condition == "used"
    return COMPS


async def slow_research(query: str, condition: str, max_results: int) -> list[Comp]:
    await asyncio.sleep(1)
    return COMPS


async def research_fails(query: str, condition: str, max_results: int) -> list[Comp]:
    raise ResearchError("Apify returned 401: invalid token")


async def fake_judge(
    item: ItemDetails, image_b64: str, media_type: str, comps: list[Comp]
) -> list[Judgement]:
    return [
        Judgement(index=i, same_product="FX 2" in c.title, similarity=0.9, reason="test")
        for i, c in enumerate(comps)
    ]


async def judge_nothing_matches(
    item: ItemDetails, image_b64: str, media_type: str, comps: list[Comp]
) -> list[Judgement]:
    return [Judgement(index=i, same_product=False, similarity=0.1, reason="no") for i in range(6)]


async def fake_read(text: str, has_photo: bool) -> SellerMessage:
    """A tiny ASI:One: 'at least $N' is the floor, 'find me' is a buyer."""
    lowered = text.lower()
    floor = float(lowered.split("at least $")[1].split()[0]) if "at least $" in lowered else None
    intent = (
        "sell" if has_photo or "sell" in lowered else "buy" if "find me" in lowered else "other"
    )
    return SellerMessage(intent=intent, asking_usd=None, floor_usd=floor, note="")


async def fake_fetch(url: str) -> bytes:
    if "broken" in url:
        raise PhotoError("I couldn't download the photo (the link returned 404).")
    return jpeg_bytes()


async def run_agents(agents: list[Agent], done: asyncio.Event) -> None:
    """Start the agents in this loop (no servers; the dispatcher delivers locally) until done."""
    for agent in agents:
        agent.setup()
    try:
        await asyncio.wait_for(done.wait(), 10)
    finally:
        for task in asyncio.all_tasks():
            if task is not asyncio.current_task():
                task.cancel()
