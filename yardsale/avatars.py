"""Profile pictures for Yardsale's agents, drawn by ASI:One.

ASI:One's image model draws each picture from a text prompt; it never sees a seller's photo.
An agent's profile holds only a picture's URL, so each picture has to be somewhere public.
Agentverse's own picture upload takes a signed-in browser session, not an API key (it answered
401 to the key on October 24), so the server can't use it.

- The service agents (the Market, Identifier, Researcher, Appraiser, Shopper, Market Scout, and
  Deal Finder) and @yardsale on Fetch Business: drawn once by the command below and saved in
  yardsale/readmes/avatars/. Each is uploaded by hand on its agent's Agentverse profile page
  (the Scout's and Deal Finder's were instead saved in the Apify picture store below and set
  through Agentverse's API); --sync then saves the URLs in yardsale/readmes/avatars/urls.json.
  service_agent() reads that file, so every Agentverse connect sends the same picture.
- Hosted item agents, with YARDSALE_ITEM_AVATARS=1: when one is created, a picture of the item
  is drawn from its title and saved in a public Apify key-value store, in the background. If
  that fails, the listing is live anyway, with Agentverse's default picture.

Drawing calls ASI:One, so each step is its own flag:

    uv run python -m yardsale.avatars --draw       # draw missing pictures (ASI:One)
    uv run python -m yardsale.avatars --sync       # save the pictures set on Agentverse
    uv run python -m yardsale.avatars --items      # handles (and pictures) for hosted items
    uv run python -m yardsale.avatars --draw --redraw shopper   # draw one again
    uv run python -m yardsale.avatars --items --redraw specialized-sirrus   # an item, by handle
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import binascii
import hashlib
import io
import json
import logging
import os
import re
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

import aiohttp
from PIL import Image, ImageOps, UnidentifiedImageError
from uagents_core.identity import Identity

from yardsale import config
from yardsale.common import ROOT, derive_seed
from yardsale.hosting import HostingClient, HostingError, claim_handle
from yardsale.photos import PhotoError, download_photo
from yardsale.store import ACTIVE, Listing, Store

IMAGE_API = "https://api.asi1.ai/v1/image/generate"
# The endpoint's default model answered "model not found" on October 24; its docs' example names
# asi1-mini.
IMAGE_MODEL = os.getenv("YARDSALE_ASI_IMAGE_MODEL", "asi1-mini")
FOLDER = Path(__file__).resolve().parent / "readmes" / "avatars"
URLS = FOLDER / "urls.json"
SIZE = 512  # pixels; Agentverse shows avatars at 40 to 88
DRAW_TIMEOUT = aiohttp.ClientTimeout(total=120)

# One look for every Yardsale picture. Image models garble lettering, so there is none.
STYLE = (
    "Flat vector illustration in a bold, friendly app-icon style: thick clean shapes, soft "
    "shading, and a warm palette of sunflower yellow, coral, and teal on a plain cream "
    "background. One centered subject that fills the square. No text, letters, numbers, or "
    "logos anywhere."
)

# What each agent does, as a picture. "yardsale" is @yardsale's, for Fetch Business.
SERVICE_PROMPTS = {
    "market": (
        "A cheerful yard sale stall with a striped awning. On its table are a bicycle, a desk "
        "lamp, and a pair of sneakers, each with a blank paper price tag, and a small friendly "
        "robot shopkeeper stands behind it, waving."
    ),
    "identifier": (
        "A large magnifying glass held over a sneaker, inside the corner brackets of a camera "
        "viewfinder, as if recognizing exactly what the item is."
    ),
    "researcher": (
        "A friendly robot reading a long paper receipt of past sales, with a small bar chart "
        "of prices floating beside it."
    ),
    "appraiser": "A brass balance scale weighing a blank paper price tag against a stack of coins.",
    "shopper": (
        "A friendly robot carrying a shopping bag, shaking hands over a blank price tag, as if "
        "it just haggled a good deal."
    ),
    "scout": (
        "A friendly robot with binoculars looking out over a row of neighborhood yard sale "
        "tables, each with a blank price tag, as if comparing their prices."
    ),
    "dealfinder": (
        "A friendly robot holding a big magnifying glass over a fan of three listing cards, "
        "each with a small picture of a secondhand item and a blank price tag, pulling out the "
        "best one, which glows with a gold star."
    ),
    "yardsale": (
        "A brand mark for a secondhand marketplace: a paper price tag shaped like a little "
        "house, with a string loop at the top and a small heart in its window."
    ),
}

# Agentverse names, for the command's output.
SERVICE_NAMES = {
    "market": "Yardsale Orchestrator",
    "identifier": "yardsale-identifier",
    "researcher": "yardsale-researcher",
    "appraiser": "yardsale-appraiser",
    "shopper": "yardsale-shopper",
    "scout": "yardsale-scout",
    "dealfinder": "yardsale-dealfinder",
}

# prompt -> the picture's bytes, as ASI:One returns them
Draw = Callable[[str], Awaitable[bytes]]
# a JPEG picture and a file name -> the picture's public https URL
Upload = Callable[[bytes, str], Awaitable[str]]

APIFY_API = "https://api.apify.com/v2"
PICTURE_STORE = "yardsale-agent-pictures"  # one Apify key-value store for every item picture
APIFY_TIMEOUT = aiohttp.ClientTimeout(total=30)

log = logging.getLogger("yardsale.avatars")


class AvatarError(RuntimeError):
    pass


@dataclass(frozen=True)
class ItemPictures:
    """How hosted item agents get their pictures: one function draws, the other publishes."""

    draw: Draw
    upload: Upload


class ApifyPictures:
    """Pictures in one Apify key-value store that anyone with its ID can read.

    Apify accounts keep their storage private by default, so the store is opened to readers
    when it's first used. Each record is served at a fixed URL with the type it was saved with.
    """

    def __init__(self, token: str, base_url: str = APIFY_API, name: str = PICTURE_STORE) -> None:
        self._token = token
        self.base_url = base_url.rstrip("/")
        self.name = name
        self._store: str | None = None
        self._lock = asyncio.Lock()

    async def _call(
        self, method: str, path: str, body: Any = None, image: bytes | None = None
    ) -> Any:
        headers = {"Authorization": f"Bearer {self._token}"}
        if image is not None:
            headers["Content-Type"] = "image/jpeg"
        try:
            async with (
                aiohttp.ClientSession(timeout=APIFY_TIMEOUT) as session,
                session.request(
                    method, self.base_url + path, json=body, data=image, headers=headers
                ) as resp,
            ):
                text = await resp.text()
        except (aiohttp.ClientError, TimeoutError) as ex:
            raise AvatarError(f"Apify couldn't be reached ({type(ex).__name__})") from ex
        if resp.status >= 400:
            route = path.split("?")[0]
            raise AvatarError(f"Apify {method} {route} returned {resp.status}: {text[:200]}")
        try:
            return json.loads(text) if text else None
        except ValueError:
            return None

    async def store_id(self) -> str:
        """The store's ID. The first call creates the store if needed and opens it to readers."""
        async with self._lock:
            if self._store is None:
                data = await self._call("POST", f"/key-value-stores?name={quote(self.name)}")
                store = data.get("data", {}).get("id") if isinstance(data, dict) else None
                if not isinstance(store, str) or not store:
                    raise AvatarError("Apify didn't return the picture store's ID")
                await self._call(
                    "PUT",
                    f"/key-value-stores/{store}",
                    {"generalAccess": "ANYONE_WITH_ID_CAN_READ"},
                )
                self._store = store
            return self._store

    async def upload(self, image: bytes, name: str) -> str:
        """Save a JPEG under `name` (a file name) and return its public URL."""
        store = await self.store_id()
        path = f"/key-value-stores/{store}/records/{quote(name, safe='')}"
        await self._call("PUT", path, image=image)
        return self.base_url + path


def item_kind(title: str) -> str:
    """What kind of thing a title names, without its brand, model, or size: the lowercase words
    at the end of its first part ("Specialized Sirrus 2.0 hybrid bike, size M" -> "hybrid
    bike"). Image models write the names they're given onto the picture, so names are left out."""
    kind: list[str] = []
    for word in reversed(title.split(",")[0].split()):
        if re.search(r"[A-Z0-9]", word):
            break
        kind.insert(0, word)
    return " ".join(kind)


def item_prompt(listing: Listing) -> str:
    """The item as a product picture: what kind of thing it is and how it looks. Its name,
    flaws, and prices are left out."""
    item = listing.item
    category = item.category.strip() if item is not None else ""
    subject = item_kind(listing.title) or category or "item"
    if category and category.lower() not in subject.lower():
        subject += f" ({category})"
    if item is not None:
        names = [n.lower() for n in (item.brand, item.model) if n and n.strip()]
        kept = [
            d.strip()
            for d in item.details[:3]
            if d.strip()
            and not re.search(r"\d", d)
            and not re.search(r"[A-Z]", d.strip()[1:])
            and not any(n in d.lower() for n in names)
        ]
        if kept:
            subject += ". Details: " + "; ".join(kept)
    return (
        f"A product picture of a secondhand {subject}. Show the whole item, clean and in good "
        "shape, with plain unmarked surfaces. Draw no price tags, labels, measurements, or "
        "dimension lines. " + STYLE
    )


def service_prompt(key: str) -> str:
    return f"{SERVICE_PROMPTS[key]} {STYLE}"


async def draw_with_asi_one(prompt: str) -> bytes:
    """ASI:One's image model (POST /v1/image/generate). On October 24 it answered like OpenAI's
    image API, {"data": [{"b64_json": ...}]}; its docs show {"images": [{"url": "data:..."}]}.
    Both are read."""
    api_key = os.getenv("ASI_ONE_API_KEY", "").strip()
    if not api_key:
        raise AvatarError("ASI_ONE_API_KEY is not set")
    body = {"prompt": prompt, "size": "1024x1024", "model": IMAGE_MODEL}
    headers = {"Authorization": f"Bearer {api_key}"}
    try:
        async with (
            aiohttp.ClientSession(timeout=DRAW_TIMEOUT) as session,
            session.post(IMAGE_API, json=body, headers=headers) as resp,
        ):
            if resp.status != 200:
                raise AvatarError(f"ASI:One returned {resp.status}: {(await resp.text())[:200]}")
            data = await resp.json(content_type=None)
    except (aiohttp.ClientError, TimeoutError, ValueError) as ex:
        raise AvatarError(f"ASI:One couldn't draw it ({type(ex).__name__})") from ex
    first = _first_image(data)
    encoded = first.get("b64_json")
    url = first.get("url")
    if isinstance(url, str) and url.startswith("data:"):
        encoded = url.split(",", 1)[-1]
    if isinstance(encoded, str) and encoded:
        try:
            return base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as ex:
            raise AvatarError("ASI:One's image wasn't valid base64") from ex
    if not isinstance(url, str) or not url:
        raise AvatarError("ASI:One's answer had no image")
    try:
        return await download_photo(url)
    except PhotoError as ex:
        raise AvatarError(f"Couldn't download ASI:One's image: {ex}") from ex


def _first_image(data: object) -> dict[str, object]:
    if isinstance(data, dict):
        for key in ("data", "images"):
            images = data.get(key)
            if isinstance(images, list) and images and isinstance(images[0], dict):
                return images[0]
    return {}


def to_avatar(image: bytes) -> bytes:
    """Any picture -> a square SIZE px JPEG, cropped from the middle."""
    try:
        with Image.open(io.BytesIO(image)) as original:
            square = ImageOps.fit(original.convert("RGB"), (SIZE, SIZE), Image.Resampling.LANCZOS)
    except (UnidentifiedImageError, OSError, ValueError) as ex:
        raise AvatarError("ASI:One's answer wasn't a picture") from ex
    buffer = io.BytesIO()
    square.save(buffer, "JPEG", quality=90)
    return buffer.getvalue()


# --- Service agents -----------------------------------------------------------------------------


def saved_urls() -> dict[str, str]:
    """The uploaded picture of each service agent (and @yardsale), by key."""
    try:
        data = json.loads(URLS.read_text())
    except (OSError, ValueError):
        return {}
    return {k: v for k, v in data.items() if isinstance(v, str) and v.startswith("https://")}


def service_avatar(key: str) -> str | None:
    return saved_urls().get(key)


def _save_urls(urls: dict[str, str]) -> None:
    URLS.write_text(json.dumps(dict(sorted(urls.items())), indent=2) + "\n")


def picture_path(key: str) -> Path:
    return FOLDER / f"{key}.jpg"


async def draw_missing(draw: Draw, redraw: set[str]) -> list[str]:
    """Draw every service picture that isn't saved yet (and the ones in `redraw`)."""
    FOLDER.mkdir(parents=True, exist_ok=True)
    drawn = []
    urls = saved_urls()
    for key in SERVICE_PROMPTS:
        path = picture_path(key)
        if path.exists() and key not in redraw:
            continue
        print(f"Drawing {key} with ASI:One...")
        path.write_bytes(to_avatar(await draw(service_prompt(key))))
        urls.pop(key, None)  # a new picture needs a new upload
        drawn.append(key)
        print(f"  saved {path.relative_to(ROOT)}")
    _save_urls(urls)
    return drawn


def service_address(key: str) -> str:
    return Identity.from_seed(derive_seed(key), 0).address


def profile_link(address: str) -> str:
    return f"https://agentverse.ai/agents/details/{address}/profile"


async def sync(client: HostingClient) -> list[str]:
    """Save the picture each service agent has on Agentverse in urls.json, so the server sends
    the same one whenever it connects. Returns the keys of agents that have no picture yet."""
    urls = saved_urls()
    missing = []
    for key, name in SERVICE_NAMES.items():
        address = service_address(key)
        url = (await client.agent_profile(address)).get("avatar_url")
        if isinstance(url, str) and url.startswith("https://"):
            urls[key] = url
            print(f"{name}: {url}")
        elif key in urls:
            print(f"{name}: not on Agentverse yet; the server sends {urls[key]} when it connects")
        else:
            missing.append(key)
            print(
                f"{name}: no picture yet. Upload {picture_path(key).relative_to(ROOT)} on "
                f"{profile_link(address)} (hover the picture), then run --sync again."
            )
    _save_urls(urls)
    print(
        f"@yardsale (Fetch Business): upload {picture_path('yardsale').relative_to(ROOT)} in "
        "Brand Studio."
    )
    return missing


# --- Item agents -------------------------------------------------------------------------------


def item_pictures_from_env() -> ItemPictures | None:
    """Pictures for hosted item agents, drawn by ASI:One and kept in Apify. Off unless
    YARDSALE_ITEM_AVATARS=1, since each one calls ASI:One and writes to the Apify account."""
    if os.getenv("YARDSALE_ITEM_AVATARS", "").strip().lower() not in ("1", "true", "yes", "on"):
        return None
    token = os.getenv("APIFY_TOKEN", "").strip()
    if not token or not os.getenv("ASI_ONE_API_KEY", "").strip():
        log.warning("Item pictures need ASI_ONE_API_KEY and APIFY_TOKEN, so they're off")
        return None
    return ItemPictures(draw_with_asi_one, ApifyPictures(token).upload)


def picture_name(listing: Listing, image: bytes) -> str:
    """A file name for the item's picture, in the characters Apify allows in record keys. It
    ends with a hash of the picture, so a new picture gets a new URL and no cache shows the old."""
    item = re.sub(r"[^A-Za-z0-9_.-]", "-", listing.item_id)[:150]
    return f"item-{item}-{hashlib.sha256(image).hexdigest()[:10]}.jpg"


async def picture_item(client: HostingClient, listing: Listing, pictures: ItemPictures) -> str:
    """Draw the item, publish the picture, and set it on its hosted agent. Returns the URL."""
    if not listing.address:
        raise AvatarError("The listing has no agent yet")
    image = to_avatar(await pictures.draw(item_prompt(listing)))
    url = await pictures.upload(image, picture_name(listing, image))
    # The hosting record's picture shows in Agentverse's header; the profile's, on the profile.
    await client.set_hosted_avatar(listing.address, url)
    await client.update_profile(listing.address, avatar_url=url)
    return url


async def picture_item_quietly(
    client: HostingClient, listing: Listing, pictures: ItemPictures
) -> None:
    """picture_item for a background task: failures are logged, never raised."""
    try:
        await picture_item(client, listing, pictures)
    except (AvatarError, HostingError) as ex:
        log.warning(f"No picture for the item agent of {listing.item_id}: {ex}")
        return
    log.info(f"The item agent of {listing.item_id} has its picture")


async def dress_hosted_items(
    client: HostingClient,
    store: Store,
    pictures: ItemPictures | None,
    redraw: frozenset[str] = frozenset(),
) -> set[str]:
    """Give active hosted item agents a handle and a picture, where they lack one. Items named
    in `redraw` (by handle or item ID) get a new picture anyway. Returns the names it found."""
    hosted = {str(a.get("address")): a for a in await client.agents()}
    found: set[str] = set()
    for listing in store.hosted():
        if listing.status != ACTIVE or not listing.address or listing.address not in hosted:
            continue
        again = redraw & {listing.item_id, listing.handle or ""}
        found |= again
        print(f"{listing.title} ({listing.address})")
        if not listing.handle:
            handle = await claim_handle(client, listing.address, listing.title)
            if handle:
                store.set_handle(listing.item_id, handle)
                print(f"  handle: @{handle}")
            else:
                print("  no handle (see the log)")
        else:
            print(f"  handle: @{listing.handle} (already)")
        if hosted[listing.address].get("avatar_url") and not again:
            print("  picture: already set")
        elif pictures is None:
            print("  picture: skipped (needs YARDSALE_ITEM_AVATARS=1)")
        else:
            try:
                print(f"  picture: {await picture_item(client, listing, pictures)}")
            except (AvatarError, HostingError) as ex:
                print(f"  picture failed: {ex}")
    return found


# --- Command line ------------------------------------------------------------------------------


async def _main(args: argparse.Namespace) -> int:
    redraw = set(args.redraw or [])
    services = redraw & set(SERVICE_PROMPTS)
    items = redraw - services  # hosted items, by handle or item ID
    if items and not args.items:
        print(f"Unknown picture: {', '.join(sorted(items))}. Choose from {list(SERVICE_PROMPTS)}")
        return 2
    if args.draw:
        try:
            await draw_missing(draw_with_asi_one, services)
        except AvatarError as ex:
            print(f"Drawing failed: {ex}")
            return 1
    if not (args.sync or args.items):
        return 0
    api_key = os.getenv("AGENTVERSE_API_KEY", "").strip()
    if not api_key:
        print("AGENTVERSE_API_KEY is not set.")
        return 1
    client = HostingClient(api_key)
    try:
        if args.sync:
            await sync(client)
        if args.items:
            found = await dress_hosted_items(
                client, Store(config.db_path("live")), item_pictures_from_env(), frozenset(items)
            )
            if items - found:
                print(f"No active hosted item named {', '.join(sorted(items - found))}")
                return 2
    except HostingError as ex:
        print(f"Agentverse: {ex}")
        return 1
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s [%(name)s] %(message)s")
    parser = argparse.ArgumentParser(description="Profile pictures for Yardsale's agents")
    parser.add_argument("--draw", action="store_true", help="draw missing pictures with ASI:One")
    parser.add_argument(
        "--redraw",
        nargs="+",
        metavar="NAME",
        help="draw these again: service keys with --draw, item handles or IDs with --items",
    )
    parser.add_argument(
        "--sync", action="store_true", help="save the service agents' pictures set on Agentverse"
    )
    parser.add_argument(
        "--items",
        action="store_true",
        help="handles for hosted item agents, and pictures with YARDSALE_ITEM_AVATARS=1",
    )
    sys.exit(asyncio.run(_main(parser.parse_args())))
