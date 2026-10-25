"""Agent pictures (avatars.py) and the Agentverse calls behind them (hosting.py).

ASI:One and Agentverse are stand-ins served on this machine, shaped like the real answers.
"""

from __future__ import annotations

import base64
import io
import json
import re
from pathlib import Path
from typing import Any

import pytest
from aiohttp import web
from PIL import Image
from uagents_core.identity import Identity

from tests.helpers import local_server
from yardsale import avatars
from yardsale.agentverse import service_agent
from yardsale.avatars import AvatarError, draw_with_asi_one, item_prompt, to_avatar
from yardsale.common import derive_seed
from yardsale.hosting import HostingClient
from yardsale.models import ItemDetails
from yardsale.store import Listing


def png(width: int = 64, height: int = 64) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (40, 160, 150)).save(buffer, "PNG")
    return buffer.getvalue()


@pytest.fixture
def folder(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Service pictures and their URLs go to a temporary folder."""
    monkeypatch.setattr(avatars, "FOLDER", tmp_path)
    monkeypatch.setattr(avatars, "URLS", tmp_path / "urls.json")
    monkeypatch.setattr(avatars, "ROOT", tmp_path)
    return tmp_path


# --- Drawing with ASI:One ----------------------------------------------------------------------


async def test_asi_one_draws_from_the_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[str | None, dict[str, Any]]] = []
    encoded = base64.b64encode(png()).decode()
    answers = [
        # What ASI:One sent on October 24, like OpenAI's image API.
        {"created": 1, "data": [{"b64_json": encoded, "url": None, "revised_prompt": "{}"}]},
        # What its docs show.
        {
            "status": 1,
            "message": "Success",
            "images": [{"url": f"data:image/png;base64,{encoded}"}],
        },
    ]

    async def generate(request: web.Request) -> web.Response:
        seen.append((request.headers.get("Authorization"), await request.json()))
        return web.json_response(answers.pop(0))

    app = web.Application()
    app.router.add_post("/v1/image/generate", generate)
    monkeypatch.setenv("ASI_ONE_API_KEY", "asi-test-key")
    async with local_server(app) as base:
        monkeypatch.setattr(avatars, "IMAGE_API", f"{base}/v1/image/generate")
        assert await draw_with_asi_one("a bike") == png()
        assert await draw_with_asi_one("a bike") == png()
    auth, body = seen[0]
    assert auth == "Bearer asi-test-key"
    assert body == {"prompt": "a bike", "size": "1024x1024", "model": "asi1-mini"}


async def test_asi_one_errors_are_avatar_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(AvatarError, match="ASI_ONE_API_KEY"):
        await draw_with_asi_one("a bike")

    answers = [
        web.json_response({"error": {"message": "bad"}}, status=400),
        web.json_response({"data": [{"b64_json": None, "url": None}]}),
        web.json_response({"images": [{"url": "data:image/png;base64,???"}]}),
    ]

    async def generate(request: web.Request) -> web.Response:
        return answers.pop(0)

    app = web.Application()
    app.router.add_post("/v1/image/generate", generate)
    monkeypatch.setenv("ASI_ONE_API_KEY", "asi-test-key")
    async with local_server(app) as base:
        monkeypatch.setattr(avatars, "IMAGE_API", f"{base}/v1/image/generate")
        for message in ("returned 400", "no image", "base64"):
            with pytest.raises(AvatarError, match=message):
                await draw_with_asi_one("a bike")


def test_pictures_become_square_jpegs() -> None:
    with Image.open(io.BytesIO(to_avatar(png(1792, 1024)))) as picture:
        assert (picture.format, picture.size) == ("JPEG", (avatars.SIZE, avatars.SIZE))
    with pytest.raises(AvatarError):
        to_avatar(b"not a picture")


def test_an_item_is_drawn_from_its_title_never_its_prices() -> None:
    listing = Listing(
        item_id="lamp",
        owner="seller",
        seller_id="payout",
        listing_ref="lamp",
        status="active",
        title="IKEA Tertial desk lamp",
        asking_cents=2500,
        floor_cents=1800,
        item=ItemDetails(
            title="IKEA Tertial desk lamp",
            brand="IKEA",
            model="Tertial",
            category="lamps",
            condition="good",
            details=["dark gray", "", "clamp mount", "scratch on the arm"],
            search_query="ikea tertial",
            generic=False,
            confidence="high",
        ),
    )
    prompt = item_prompt(listing)
    assert "a secondhand desk lamp (lamps). Details: dark gray; clamp mount." in prompt
    assert "IKEA" not in prompt and "Tertial" not in prompt  # it would be written on the lamp
    assert "scratch" not in prompt  # only the first three details
    assert "18" not in prompt and "25" not in prompt
    assert avatars.STYLE in prompt


def test_item_pictures_are_off_unless_turned_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ASI_ONE_API_KEY", "asi-test-key")
    monkeypatch.setenv("APIFY_TOKEN", "apify-test-token")
    monkeypatch.delenv("YARDSALE_ITEM_AVATARS", raising=False)
    assert avatars.item_pictures_from_env() is None
    monkeypatch.setenv("YARDSALE_ITEM_AVATARS", "1")
    assert avatars.item_pictures_from_env() is not None
    monkeypatch.delenv("APIFY_TOKEN")
    assert avatars.item_pictures_from_env() is None  # nowhere public to keep them


def test_an_item_is_drawn_as_a_kind_of_thing_not_a_named_product() -> None:
    assert avatars.item_kind("Specialized Sirrus 2.0 hybrid bike, size M") == "hybrid bike"
    assert avatars.item_kind("Mid-century wooden chair") == "wooden chair"
    assert avatars.item_kind("Nintendo Switch") == ""
    listing = Listing(
        item_id="switch",
        owner="seller",
        seller_id="payout",
        listing_ref="switch",
        status="active",
        title="Nintendo Switch OLED",
        asking_cents=20000,
        floor_cents=15000,
        item=ItemDetails(
            title="Nintendo Switch OLED",
            brand="Nintendo",
            model="Switch OLED",
            category="game consoles",
            condition="good",
            details=["white Joy-Cons", "64 GB", "Nintendo dock included", "matte white"],
            search_query="nintendo switch oled",
            generic=False,
            confidence="high",
        ),
    )
    prompt = item_prompt(listing)
    assert "a secondhand game consoles." in prompt  # the category when the title is all name
    assert "Nintendo" not in prompt and "64" not in prompt and "Joy" not in prompt
    assert "matte white" not in prompt  # only the first three details are read


def test_picture_names_use_only_characters_apify_allows() -> None:
    listing = Listing(
        item_id="a b/c:d",
        owner="seller",
        seller_id="payout",
        listing_ref="x",
        status="active",
        title="Lamp",
        asking_cents=2500,
        floor_cents=1800,
    )
    name = avatars.picture_name(listing, b"jpeg")
    assert re.fullmatch(r"item-a-b-c-d-[0-9a-f]{10}\.jpg", name), name
    assert avatars.picture_name(listing, b"other jpeg") != name


# --- Apify, where item pictures are kept --------------------------------------------------------


async def test_item_pictures_go_to_one_public_apify_store() -> None:
    calls: list[tuple[str, str, str | None]] = []
    records: dict[str, tuple[str | None, bytes]] = {}
    fail = False

    async def create(request: web.Request) -> web.Response:
        calls.append(("POST", request.path_qs, request.headers.get("Authorization")))
        return web.json_response({"data": {"id": "store123", "name": request.query["name"]}})

    async def update(request: web.Request) -> web.Response:
        calls.append(("PUT", request.path, json.dumps(await request.json())))
        return web.json_response({"data": {"id": "store123"}})

    async def put_record(request: web.Request) -> web.Response:
        if fail:
            return web.json_response({"error": {"type": "token-not-valid"}}, status=401)
        records[request.match_info["key"]] = (request.content_type, await request.read())
        return web.Response(status=201)

    app = web.Application()
    app.router.add_post("/v2/key-value-stores", create)
    app.router.add_put("/v2/key-value-stores/{store}", update)
    app.router.add_put("/v2/key-value-stores/{store}/records/{key}", put_record)
    async with local_server(app) as base:
        pictures = avatars.ApifyPictures("apify-test-token", f"{base}/v2")
        url = await pictures.upload(b"jpeg one", "item-lamp.jpg")
        assert url == f"{base}/v2/key-value-stores/store123/records/item-lamp.jpg"
        await pictures.upload(b"jpeg two", "item-bike.jpg")
        fail = True
        with pytest.raises(AvatarError, match="401"):
            await pictures.upload(b"jpeg three", "item-desk.jpg")
    assert calls == [
        (
            "POST",
            "/v2/key-value-stores?name=yardsale-agent-pictures",
            "Bearer apify-test-token",
        ),
        ("PUT", "/v2/key-value-stores/store123", '{"generalAccess": "ANYONE_WITH_ID_CAN_READ"}'),
    ]  # the store is found and opened once
    assert records == {
        "item-lamp.jpg": ("image/jpeg", b"jpeg one"),
        "item-bike.jpg": ("image/jpeg", b"jpeg two"),
    }


# --- Agentverse --------------------------------------------------------------------------------


async def test_a_profile_update_keeps_the_rest_of_the_profile() -> None:
    profile = {
        "description": "Sells things",
        "readme": "# Yardsale",
        "avatar_url": None,
        "banner_url": None,
        "starter_prompts": None,
    }
    puts: list[dict[str, Any]] = []

    async def get(request: web.Request) -> web.Response:
        return web.json_response(
            {
                "address": request.match_info["address"],
                "name": "Yardsale Orchestrator",
                "handle": "ys-orchestrator",
                "profile": profile,
            }
        )

    async def put(request: web.Request) -> web.Response:
        puts.append(await request.json())
        return web.json_response({})

    app = web.Application()
    app.router.add_get("/v2/agents/{address}", get)
    app.router.add_put("/v2/agents/{address}", put)
    async with local_server(app) as base:
        client = HostingClient("av-test-key", base)
        await client.update_profile("agent1qmarket", avatar_url="https://pics.example/m.jpg")
        await client.update_profile("agent1qitem", handle="trek-fx-3-hybrid")
    picture, handle = puts
    assert picture == {
        "address": "agent1qmarket",
        "name": "Yardsale Orchestrator",
        "profile": {
            "description": "Sells things",
            "readme": "# Yardsale",
            "avatar_url": "https://pics.example/m.jpg",
        },
    }
    assert handle["handle"] == "trek-fx-3-hybrid"
    assert handle["profile"] == {"description": "Sells things", "readme": "# Yardsale"}


async def test_free_handles_come_from_agentverse() -> None:
    answers = {
        "free": {"available": True, "handle": "free", "alternative": None},
        "taken": {"available": False, "handle": "taken", "alternative": "taken-2"},
        "stuck": {"available": False, "handle": "stuck", "alternative": None},
        "long": {"available": False, "handle": "long", "alternative": "x" * 21},
    }

    async def available(request: web.Request) -> web.Response:
        return web.json_response(answers[request.match_info["handle"]])

    app = web.Application()
    app.router.add_get("/v2/almanac/handles/{handle}/available", available)
    async with local_server(app) as base:
        client = HostingClient("av-test-key", base)
        assert await client.free_handle("free") == "free"
        assert await client.free_handle("taken") == "taken-2"
        assert await client.free_handle("stuck") is None
        assert await client.free_handle("long") is None


# --- Service agents ----------------------------------------------------------------------------


async def test_service_agents_send_their_saved_picture(folder: Path) -> None:
    (folder / "urls.json").write_text(
        json.dumps({"market": "https://pics.example/market.jpg", "shopper": "http://no.example"})
    )
    # Offline agents publish no profile, so this checks what a live connect would send.
    market = service_agent("market", "Yardsale Orchestrator", "ys-orchestrator", "d", True)
    assert market._avatar_url == "https://pics.example/market.jpg"
    shopper = service_agent("shopper", "yardsale-shopper", "ys-shopper", "d", True)
    assert shopper._avatar_url is None  # only https URLs count


class FakeClient(HostingClient):
    """Agentverse's profiles, where some service agents already have a picture."""

    def __init__(self, avatars_by_address: dict[str, str]) -> None:
        super().__init__("av-test-key", "http://127.0.0.1:9")
        self.avatars = avatars_by_address

    async def agent_profile(self, address: str) -> dict[str, Any]:
        return {"description": "d", "avatar_url": self.avatars.get(address)}


async def test_drawing_and_syncing_service_pictures(
    folder: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    prompts: list[str] = []

    async def draw(prompt: str) -> bytes:
        prompts.append(prompt)
        return png()

    assert await avatars.draw_missing(draw, set()) == list(avatars.SERVICE_PROMPTS)
    assert all(avatars.STYLE in prompt for prompt in prompts)
    assert await avatars.draw_missing(draw, set()) == []  # already drawn
    with Image.open(folder / "market.jpg") as picture:
        assert (picture.format, picture.size) == ("JPEG", (avatars.SIZE, avatars.SIZE))

    market = Identity.from_seed(derive_seed("market"), 0).address
    assert avatars.service_address("market") == market
    shopper = avatars.service_address("shopper")
    client = FakeClient(
        {market: "https://pics.example/market.jpg", shopper: "https://pics.example/shopper.jpg"}
    )
    missing = await avatars.sync(client)
    assert missing == ["identifier", "researcher", "appraiser", "scout"]
    assert avatars.saved_urls() == {
        "market": "https://pics.example/market.jpg",
        "shopper": "https://pics.example/shopper.jpg",
    }
    out = capsys.readouterr().out
    assert avatars.profile_link(avatars.service_address("identifier")) in out
    assert "Brand Studio" in out  # @yardsale's picture is set by hand

    # Drawing one again forgets its old URL, until it's uploaded and synced again.
    assert await avatars.draw_missing(draw, {"shopper"}) == ["shopper"]
    assert "shopper" not in avatars.saved_urls()
    client.avatars[shopper] = "https://pics.example/shopper-2.jpg"
    await avatars.sync(client)
    assert avatars.saved_urls()["shopper"] == "https://pics.example/shopper-2.jpg"
