"""Test stand-ins for Claude, Apify, ASI:One, and photo downloads: no keys, no network, no cost.

Only the tests use these; the server always calls the real services. Each test item is a photo
of one solid color, so any photo is "identified" by its average color, and the answers are the
same every run:

- bike (red): a Trek hybrid with 5 same-model sales, plus a lot and a road bike that don't count.
- espresso (blue): a Breville espresso machine with 4 same-model sales.
- mug (green): a handmade mug with no close sales, so it can't be priced without the seller's.
- camera (yellow): eBay research fails, as if Apify were down.

FakeEbay is a local stand-in for eBay's OAuth token endpoint and Trading API (ebay_seller.py).
"""

from __future__ import annotations

import base64
import io
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from aiohttp import web
from PIL import Image, ImageDraw
from uagents import Context

from yardsale.appraiser import Judgement
from yardsale.asione import SellerMessage, read_locally
from yardsale.chat import Photo
from yardsale.config import EbaySettings
from yardsale.models import Comp, ItemDetails
from yardsale.payments import PaymentError, PaymentLink, PaymentState
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


# What road bikes sold for, for the deal finder's market price: four whole bikes around $310,
# plus an accessory, a parts listing, and a repair job that must not count.
ROAD_BIKE_SALES: tuple[tuple[str, int, bool], ...] = (
    ("Trek Domane AL 2 road bike 54cm", 320, False),
    ("Giant Contend road bike", 280, False),
    ("Specialized Allez road bike 56cm", 300, False),
    ("Cannondale CAAD road bike", 340, False),
    ("Road bike wheelset only", 90, False),
    ("Road bike frame for parts", 60, False),
    ("Fuji road bike, needs repair", 50, False),
)


def road_bike_comps() -> list[Comp]:
    return [_comp("road-bike", n, *sale) for n, sale in enumerate(ROAD_BIKE_SALES)]


async def research_with_comps(query: str, condition: str, max_results: int) -> list[Comp]:
    """`research`, plus sold road bikes for any search that says "road bike", so the deal
    finder has a market price to judge other sites' listings against."""
    if "road bike" in query.lower():
        return road_bike_comps()[:max_results]
    return await research(query, condition, max_results)


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


class FakePayments:
    """A payment provider that makes links on a made-up site; tests decide what's paid."""

    name = "fake"
    label = "Test pay link"

    def __init__(self) -> None:
        self.links: dict[str, PaymentLink] = {}
        self.paid: set[str] = set()
        self.expired: set[str] = set()
        self.fail = False

    async def create(self, deal_id: str, title: str, price_cents: int) -> PaymentLink:
        if self.fail:
            raise PaymentError("the test provider is down")
        link = PaymentLink(self.name, f"pay-{deal_id}", f"https://pay.example.test/{deal_id}")
        self.links[deal_id] = link
        return link

    async def state(self, link: PaymentLink) -> PaymentState:
        if link.ref in self.paid:
            return "paid"
        return "expired" if link.ref in self.expired else "pending"

    def pay(self, deal_id: str) -> None:
        self.paid.add(self.links[deal_id].ref)


# --- eBay ----------------------------------------------------------------------------------------

EBAY_NS = "{urn:ebay:apis:eBLBaseComponents}"
EBAY_SETTINGS = EbaySettings(
    client_id="Yardsale-Test-SBX-0123456789",
    client_secret="fake-ebay-client-secret",
    runame="Yardsale-Test-RuName",
)


@dataclass
class FakeOffer:
    offer_id: str
    buyer: str
    cents: int
    status: str = "Active"


@dataclass
class FakeEbayItem:
    request: str  # the AddFixedPriceItem XML
    ended: bool = False
    offers: list[FakeOffer] = field(default_factory=list)
    sales: list[tuple[str, str, int]] = field(default_factory=list)  # (transaction, buyer, cents)


def _usd(cents: int) -> str:
    return f"{cents // 100}.{cents % 100:02d}"


class FakeEbay:
    """eBay's OAuth token endpoint and Trading API (the calls Yardsale makes), on this machine.

    It checks the client credentials, the RuName, one-time codes, and user tokens, and records
    every Trading request so tests can read exactly what eBay would have been sent."""

    def __init__(self) -> None:
        self.codes: set[str] = set()
        self.access_tokens: set[str] = set()
        self.refresh_tokens: set[str] = set()
        self.minted: list[str] = []  # every token handed out, for log checks
        self.refreshes = 0
        self.calls: list[tuple[str, str]] = []  # (call name, request XML)
        self.pictures: list[bytes] = []
        self.items: dict[str, FakeEbayItem] = {}
        self.responses: list[dict[str, str]] = []  # RespondToBestOffer requests
        self.fail: set[str] = set()  # call names that fail
        self._n = 0

    def _next(self) -> int:
        self._n += 1
        return self._n

    def issue_code(self) -> str:
        """The code eBay puts on the redirect after the seller consents."""
        code = f"v^1.1#i^1#code-{self._next()}"
        self.codes.add(code)
        return code

    def offer(self, ebay_item_id: str, buyer: str, cents: int) -> str:
        offer_id = f"50{self._next():08d}"
        self.items[ebay_item_id].offers.append(FakeOffer(offer_id, buyer, cents))
        return offer_id

    def app(self) -> web.Application:
        app = web.Application()
        app.router.add_post("/identity/v1/oauth2/token", self._token)
        app.router.add_post("/ws/api.dll", self._trading)
        return app

    def _mint(self, kind: str) -> str:
        token = f"v^1.1#i^1#{kind}-{self._next()}-secret"
        self.minted.append(token)
        return token

    async def _token(self, request: web.Request) -> web.Response:
        expected = base64.b64encode(
            f"{EBAY_SETTINGS.client_id}:{EBAY_SETTINGS.client_secret}".encode()
        ).decode()
        if request.headers.get("Authorization") != f"Basic {expected}":
            return web.json_response({"error": "invalid_client"}, status=401)
        form = await request.post()
        if form.get("grant_type") == "authorization_code":
            code = str(form.get("code", ""))
            if code not in self.codes or form.get("redirect_uri") != EBAY_SETTINGS.runame:
                return web.json_response({"error": "invalid_grant"}, status=400)
            self.codes.discard(code)
            access, refresh = self._mint("access"), self._mint("refresh")
            self.access_tokens.add(access)
            self.refresh_tokens.add(refresh)
            return web.json_response(
                {
                    "access_token": access,
                    "expires_in": 7200,
                    "refresh_token": refresh,
                    "refresh_token_expires_in": 47304000,
                    "token_type": "User Access Token",
                }
            )
        if form.get("grant_type") == "refresh_token":
            if form.get("refresh_token") not in self.refresh_tokens:
                return web.json_response({"error": "invalid_grant"}, status=400)
            self.refreshes += 1
            access = self._mint("access")
            self.access_tokens.add(access)
            return web.json_response({"access_token": access, "expires_in": 7200})
        return web.json_response({"error": "unsupported_grant_type"}, status=400)

    @staticmethod
    def _reply(call: str, inner: str = "", ack: str = "Success") -> web.Response:
        return web.Response(
            text=f'<?xml version="1.0" encoding="UTF-8"?><{call}Response '
            f'xmlns="urn:ebay:apis:eBLBaseComponents"><Ack>{ack}</Ack>{inner}</{call}Response>',
            content_type="text/xml",
        )

    def _error(self, call: str, code: str, message: str) -> web.Response:
        inner = (
            f"<Errors><ShortMessage>{message}</ShortMessage><LongMessage>{message}</LongMessage>"
            f"<ErrorCode>{code}</ErrorCode><SeverityCode>Error</SeverityCode></Errors>"
        )
        return self._reply(call, inner, "Failure")

    async def _trading(self, request: web.Request) -> web.Response:
        call = request.headers.get("X-EBAY-API-CALL-NAME", "")
        picture: Any = None
        if request.content_type.startswith("multipart/"):
            form = await request.post()
            text, picture = str(form["XML Payload"]), form.get("image")
        else:
            text = await request.text()
        self.calls.append((call, text))
        if request.headers.get("X-EBAY-API-IAF-TOKEN") not in self.access_tokens:
            return self._error(call, "21917053", "Invalid IAF token.")
        if call in self.fail:
            return self._error(call, "10007", "Internal error to the application.")
        body = ET.fromstring(text)

        def get(path: str) -> str:
            found = body.find("/".join(EBAY_NS + part for part in path.split("/")))
            return (found.text or "") if found is not None else ""

        if call == "UploadSiteHostedPictures":
            assert picture is not None, "the photo goes in a multipart part"
            self.pictures.append(picture.file.read())
            return self._reply(
                call,
                "<SiteHostedPictureDetails><FullURL>https://i.ebayimg.sandbox.ebay.com/images/g/"
                "fake/s-l1600.jpg</FullURL></SiteHostedPictureDetails>",
            )
        if call == "GetSuggestedCategories":
            return self._reply(
                call,
                "<SuggestedCategoryArray><SuggestedCategory><Category><CategoryID>177831"
                "</CategoryID><CategoryName>Bikes</CategoryName></Category></SuggestedCategory>"
                "</SuggestedCategoryArray>",
            )
        if call == "AddFixedPriceItem":
            ebay_item_id = f"1100{self._next():08d}"
            self.items[ebay_item_id] = FakeEbayItem(text)
            return self._reply(call, f"<ItemID>{ebay_item_id}</ItemID>")
        item = self.items.get(get("ItemID"))
        if item is None:
            return self._error(call, "17", "This item cannot be accessed.")
        if call == "GetItemTransactions":
            sales = "".join(
                f"<Transaction><TransactionID>{txn}</TransactionID><TransactionPrice "
                f'currencyID="USD">{_usd(cents)}</TransactionPrice><Buyer><UserID>{buyer}'
                "</UserID></Buyer></Transaction>"
                for txn, buyer, cents in item.sales
            )
            return self._reply(call, f"<TransactionArray>{sales}</TransactionArray>")
        if call == "GetBestOffers":
            offers = "".join(
                f'<BestOffer><BestOfferID>{o.offer_id}</BestOfferID><Price currencyID="USD">'
                f"{_usd(o.cents)}</Price><Status>{o.status}</Status><Buyer><UserID>{o.buyer}"
                "</UserID></Buyer><BestOfferCodeType>BuyerBestOffer</BestOfferCodeType>"
                "</BestOffer>"
                for o in item.offers
                if o.status == "Active"
            )
            return self._reply(call, f"<BestOfferArray>{offers}</BestOfferArray>")
        if call == "RespondToBestOffer":
            answer = {
                "offer_id": get("BestOfferID"),
                "action": get("Action"),
                "counter": get("CounterOfferPrice"),
                "message": get("SellerResponse"),
            }
            self.responses.append(answer)
            for offer in item.offers:
                if offer.offer_id == answer["offer_id"]:
                    offer.status = {"Accept": "Accepted", "Counter": "Countered"}.get(
                        answer["action"], "Declined"
                    )
            return self._reply(call)
        if call == "EndFixedPriceItem":
            if item.ended:
                return self._error(call, "1047", "Auction already closed.")
            item.ended = True
            return self._reply(call, "<EndTime>2026-10-25T00:00:00.000Z</EndTime>")
        return self._error(call, "2", "Unsupported API call.")
