"""Test stand-ins for Claude, Apify, ASI:One, and photo downloads: no keys, no network, no cost.

Only the tests use these; the server always calls the real services. Each test item is a photo
of one solid color, so any photo is "identified" by its average color, and the answers are the
same every run:

- bike (red): a Trek hybrid with 5 same-model sales, plus a lot and a road bike that don't count.
- espresso (blue): a Breville espresso machine with 4 same-model sales.
- mug (green): a handmade mug with no close sales, so it can't be priced without the seller's.
- camera (yellow): eBay research fails, as if Apify were down.
"""

from __future__ import annotations

import base64
import io
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from PIL import Image, ImageDraw
from uagents import Context

from yardsale.appraiser import Judgement
from yardsale.asione import SellerMessage, read_locally
from yardsale.chat import Photo
from yardsale.models import Comp, ItemDetails
from yardsale.photos import PhotoError
from yardsale.researcher import ResearchError


@dataclass(frozen=True)
class FakeItem:
    key: str
    color: tuple[int, int, int]
    item: ItemDetails
    sales: tuple[tuple[str, int, bool], ...]  # (title, dollars, accepted best offer)
    research_fails: bool = False


def _comp(key: str, n: int, title: str, dollars: int, best_offer: bool) -> Comp:
    return Comp(
        title=title,
        price_cents=dollars * 100,
        shipping_cents=None,
        condition="Pre-Owned",
        sold_at=f"2026-10-{n + 1:02d}",
        url=f"https://www.ebay.com/itm/fake-{key}-{n}",
        image_url=None,
        best_offer=best_offer,
    )


CATALOG = {
    item.key: item
    for item in (
        FakeItem(
            "bike",
            (200, 30, 30),
            ItemDetails(
                title="Trek FX 2 Disc hybrid bike, medium",
                brand="Trek",
                model="FX 2 Disc",
                category="Bicycles",
                condition="good",
                details=["medium frame", "matte black", "hybrid bike"],
                search_query="Trek FX 2 Disc hybrid bike medium",
                generic=False,
                confidence="high",
            ),
            (
                ("Trek FX 2 Disc Hybrid Bike M", 300, False),
                ("Trek FX 2 Disc Hybrid Bike Medium Black", 320, False),
                ("Trek FX 2 Disc 2022 hybrid, M", 340, False),
                ("Trek FX 2 Disc Hybrid Bike M", 360, False),
                ("Trek FX 2 Disc Hybrid Bike M, upgraded", 380, False),
                ("Trek FX 2 Disc Hybrid Bike M", 520, True),
                ("Lot of 3 Trek FX 2 Disc wheels, parts", 90, False),
                ("Trek Domane AL 2 road bike", 900, False),
            ),
        ),
        FakeItem(
            "espresso",
            (30, 60, 200),
            ItemDetails(
                title="Breville Bambino Plus espresso machine",
                brand="Breville",
                model="Bambino Plus",
                category="Coffee makers",
                condition="like new",
                details=["brushed stainless", "steam wand", "espresso machine"],
                search_query="Breville Bambino Plus espresso machine",
                generic=False,
                confidence="high",
            ),
            (
                ("Breville Bambino Plus BES500 espresso machine", 260, False),
                ("Breville Bambino Plus espresso maker stainless", 280, False),
                ("Breville Bambino Plus BES500BSS", 300, False),
                ("Breville Bambino Plus espresso machine, used", 240, False),
                ("Breville Barista Express espresso machine", 450, False),
            ),
        ),
        FakeItem(
            "mug",
            (40, 170, 60),
            ItemDetails(
                title="Handmade speckled ceramic mug",
                brand="",
                model="",
                category="Mugs",
                condition="like new",
                details=["speckled glaze", "about 12 oz"],
                search_query="handmade speckled ceramic mug",
                generic=True,
                confidence="medium",
            ),
            (
                ("Set of 6 Starbucks city mugs", 75, False),
                ("Vintage Pyrex mixing bowl", 40, False),
            ),
        ),
        FakeItem(
            "camera",
            (230, 200, 40),
            ItemDetails(
                title="Polaroid Now instant camera",
                brand="Polaroid",
                model="Now",
                category="Cameras",
                condition="good",
                details=["white", "autofocus"],
                search_query="Polaroid Now instant camera",
                generic=False,
                confidence="high",
            ),
            (),
            research_fails=True,
        ),
    )
}


def photo_bytes(key: str, size: tuple[int, int] = (480, 360)) -> bytes:
    """A test photo: the item's color with its name written on it."""
    fake = CATALOG[key]
    image = Image.new("RGB", size, fake.color)
    ImageDraw.Draw(image).text((16, 16), fake.item.title, fill=(255, 255, 255))
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", quality=90)
    return buffer.getvalue()


def _which(image_b64: str) -> FakeItem:
    with Image.open(io.BytesIO(base64.b64decode(image_b64))) as image:
        pixel = image.convert("RGB").resize((1, 1)).getpixel((0, 0))
    assert isinstance(pixel, tuple)
    return min(
        CATALOG.values(),
        key=lambda fake: sum((a - b) ** 2 for a, b in zip(fake.color, pixel, strict=False)),
    )


async def identify(image_b64: str, media_type: str, note: str) -> ItemDetails:
    item = _which(image_b64).item.copy(deep=True)
    if note:
        item.details.append(note)
    return item


async def research(query: str, condition: str, max_results: int) -> list[Comp]:
    fake = next((d for d in CATALOG.values() if d.item.search_query == query), None)
    if fake is None:
        return []
    if fake.research_fails:
        raise ResearchError("Apify returned 503: the actor run failed (fake outage)")
    return [_comp(fake.key, n, *sale) for n, sale in enumerate(fake.sales)][:max_results]


async def judge(
    item: ItemDetails, image_b64: str, media_type: str, comps: list[Comp]
) -> list[Judgement]:
    """Same product when the title names the model and isn't a lot or parts. Same brand is a
    weak reference; anything else isn't one."""
    judgements = []
    for i, comp in enumerate(comps):
        title = comp.title.lower()
        bundle = any(word in title for word in ("lot of", "set of", "parts", "bundle"))
        same = bool(item.model) and item.model.lower() in title and not bundle
        similar = 0.9 if same else 0.5 if item.brand and item.brand.lower() in title else 0.1
        judgements.append(
            Judgement(
                index=i,
                same_product=same,
                similarity=similar,
                reason="same model" if same else "different product",
            )
        )
    return judgements


async def read(text: str, has_photo: bool) -> SellerMessage:
    return read_locally(text, has_photo)


def photo_url(key: str) -> str:
    return f"https://res.cloudinary.com/demo/image/upload/v1/{key}.jpg"


def storage_uri(key: str) -> str:
    """How the ASI:One app sends an upload: a file in Agentverse storage, not a web link."""
    return f"agent-storage://agentverse.ai/v1/storage/assets/{key}"


async def fetch(ctx: Context, photo: Photo) -> bytes:
    """A download of a test photo link or upload; the file name picks the item. "broken" fails."""
    name = Path(urlparse(photo.url).path).stem
    if "broken" in name:
        raise PhotoError("I couldn't download the photo (the link returned 404).")
    return photo_bytes(name if name in CATALOG else "bike")
