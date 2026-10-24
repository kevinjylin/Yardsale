"""Photo handling shared by the sell command and ASI:One chat: download, fix rotation, shrink."""

from __future__ import annotations

import base64
import io
import ipaddress
import os
from pathlib import Path
from urllib.parse import urlparse

import aiohttp
from PIL import Image, ImageOps, UnidentifiedImageError
from pillow_heif import register_heif_opener
from uagents_core.identity import Identity
from uagents_core.storage import ExternalStorage

MAX_EDGE = 1280  # pixels; plenty for identification and keeps agent messages small
MAX_DOWNLOAD_BYTES = 25 * 1024 * 1024
# Where ASI:One puts chat uploads. Only the agent the file was sent to can read it, so the
# download is signed with that agent's identity. ExternalStorage's own default URL is broken in
# uagents_core 0.4.12 (it reads a config field that doesn't exist), so it's spelled out here.
STORAGE_URL = os.getenv("AGENTVERSE_URL", "https://agentverse.ai").rstrip("/") + "/v1/storage"

register_heif_opener()  # iPhone photos are HEIC


class PhotoError(ValueError):
    """The photo couldn't be used; the message is shown to the seller."""


def prepare_photo(data: bytes) -> tuple[str, str]:
    """Any common photo format (HEIC included) -> upright JPEG at most MAX_EDGE px, as base64."""
    try:
        with Image.open(io.BytesIO(data)) as original:
            image = ImageOps.exif_transpose(original).convert("RGB")
    except (UnidentifiedImageError, OSError) as ex:
        raise PhotoError("That file isn't a photo I can read. Send a JPEG, PNG, or HEIC.") from ex
    image.thumbnail((MAX_EDGE, MAX_EDGE))
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", quality=85)
    return base64.b64encode(buffer.getvalue()).decode(), "image/jpeg"


def load_photo(path: Path) -> tuple[str, str]:
    return prepare_photo(path.read_bytes())


def _public_web_url(url: str) -> bool:
    """http(s) only, and not this machine or a private network address."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return False
    host = parsed.hostname
    if host == "localhost" or host.endswith(".localhost"):
        return False
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return True  # a host name, not an IP literal
    return address.is_global


async def download_photo(url: str) -> bytes:
    """Fetch an uploaded photo, such as the link ASI:One sends for a chat attachment."""
    if not _public_web_url(url):
        raise PhotoError("I can only open photo links that start with http:// or https://.")
    timeout = aiohttp.ClientTimeout(total=30)
    try:
        async with (
            aiohttp.ClientSession(timeout=timeout) as session,
            session.get(url) as resp,
        ):
            if resp.status != 200:
                raise PhotoError(
                    f"I couldn't download the photo (the link returned {resp.status})."
                )
            data = bytearray()
            async for chunk in resp.content.iter_chunked(64 * 1024):
                data.extend(chunk)
                if len(data) > MAX_DOWNLOAD_BYTES:
                    raise PhotoError("That photo is over 25 MB. Send a smaller one.")
            return bytes(data)
    except (aiohttp.ClientError, TimeoutError) as ex:
        raise PhotoError("I couldn't download the photo. Try sending it again.") from ex


def download_from_storage(identity: Identity, asset_id: str) -> bytes:
    """Fetch a chat upload from Agentverse storage. Blocking: run it in a thread."""
    storage = ExternalStorage(identity=identity, storage_url=STORAGE_URL)
    try:
        stored = storage.download(asset_id)
        data = base64.b64decode(stored["contents"], validate=True)
    except (OSError, RuntimeError, KeyError, TypeError, ValueError) as ex:  # ValueError: bad base64
        raise PhotoError("I couldn't open the attachment. Please send the photo again.") from ex
    if len(data) > MAX_DOWNLOAD_BYTES:
        raise PhotoError("That photo is over 25 MB. Send a smaller one.")
    return data
