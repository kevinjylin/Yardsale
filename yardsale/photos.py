"""Photo handling shared by the sell command and ASI:One chat: download, fix rotation, shrink."""

from __future__ import annotations

import base64
import binascii
import io
import ipaddress
import socket
from pathlib import Path
from urllib.parse import urlparse

import aiohttp
from aiohttp.abc import ResolveResult
from aiohttp.resolver import ThreadedResolver
from PIL import Image, ImageOps, UnidentifiedImageError
from pillow_heif import register_heif_opener
from yarl import URL

MAX_EDGE = 1280  # pixels; plenty for identification and keeps agent messages small
MAX_DOWNLOAD_BYTES = 25 * 1024 * 1024
MAX_PIXELS = 50_000_000  # a 48 MP phone photo fits; bigger is a decompression bomb
MAX_REDIRECTS = 3
MAX_PHOTO_B64 = 8 * 1024 * 1024  # a prepared photo is a few hundred KB of base64
PHOTO_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}  # what Claude reads

register_heif_opener()  # iPhone photos are HEIC


class PhotoError(ValueError):
    """The photo couldn't be used; the message is shown to the seller."""


def prepare_photo(data: bytes) -> tuple[str, str]:
    """Any common photo format (HEIC included) -> upright JPEG at most MAX_EDGE px, as base64."""
    try:
        with Image.open(io.BytesIO(data)) as original:
            # Image.open only reads the header, so this check runs before decoding any pixels.
            if original.width * original.height > MAX_PIXELS:
                raise PhotoError("That photo is too large. Send one under 50 megapixels.")
            image = ImageOps.exif_transpose(original).convert("RGB")
    except PhotoError:
        raise
    except Image.DecompressionBombError as ex:
        raise PhotoError("That photo is too large. Send one under 50 megapixels.") from ex
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError) as ex:
        raise PhotoError("That file isn't a photo I can read. Send a JPEG, PNG, or HEIC.") from ex
    image.thumbnail((MAX_EDGE, MAX_EDGE))
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", quality=85)
    return base64.b64encode(buffer.getvalue()).decode(), "image/jpeg"


def load_photo(path: Path) -> tuple[str, str]:
    return prepare_photo(path.read_bytes())


def check_prepared_photo(image_b64: str, media_type: str) -> None:
    """A photo another agent sent as base64: a type Claude reads, a sane size, valid base64."""
    if media_type not in PHOTO_TYPES:
        raise PhotoError(f"Send the photo as JPEG, PNG, WebP, or GIF, not {media_type[:40]!r}.")
    if not image_b64 or len(image_b64) > MAX_PHOTO_B64:
        raise PhotoError("The photo is empty or too large. Resize it to about 1280 px first.")
    try:
        base64.b64decode(image_b64, validate=True)
    except (binascii.Error, ValueError) as ex:
        raise PhotoError("The photo isn't valid base64.") from ex


def _public_ip(host: str) -> bool:
    try:
        return ipaddress.ip_address(host).is_global
    except ValueError:  # includes scoped IPv6 such as fe80::1%en0
        return False


def _public_web_url(url: str) -> bool:
    """http(s) only, and not this machine or a private network address."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return False
    host = parsed.hostname
    if host == "localhost" or host.endswith(".localhost"):
        return False
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return True  # a host name; PublicOnlyResolver checks what it points at
    return _public_ip(host)


class PublicOnlyResolver(ThreadedResolver):
    """Looks up host names, but refuses any that point at this machine or a private network.

    Checking at connect time, not before, means a name can't pass the check and then resolve
    to 127.0.0.1 a moment later.
    """

    async def resolve(
        self, host: str, port: int = 0, family: socket.AddressFamily = socket.AF_INET
    ) -> list[ResolveResult]:
        results = await super().resolve(host, port, family)
        if not results or not all(_public_ip(result["host"]) for result in results):
            raise OSError(f"{host} doesn't point at a public address")
        return results


async def download_photo(url: str) -> bytes:
    """Fetch an uploaded photo, such as the link ASI:One sends for a chat attachment.

    Redirects are followed by hand, so every hop gets the same public-address check.
    """
    timeout = aiohttp.ClientTimeout(total=30)
    connector = aiohttp.TCPConnector(resolver=PublicOnlyResolver())
    try:
        async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
            for _ in range(MAX_REDIRECTS + 1):
                if not _public_web_url(url):
                    raise PhotoError(
                        "I can only open public photo links that start with http:// or https://."
                    )
                async with session.get(url, allow_redirects=False) as resp:
                    if resp.status in (301, 302, 303, 307, 308) and "Location" in resp.headers:
                        url = str(resp.url.join(URL(resp.headers["Location"])))
                        continue
                    if resp.status != 200:
                        raise PhotoError(
                            f"I couldn't download the photo (the link returned {resp.status})."
                        )
                    if (resp.content_length or 0) > MAX_DOWNLOAD_BYTES:
                        raise PhotoError("That photo is over 25 MB. Send a smaller one.")
                    data = bytearray()
                    async for chunk in resp.content.iter_chunked(64 * 1024):
                        data.extend(chunk)
                        if len(data) > MAX_DOWNLOAD_BYTES:
                            raise PhotoError("That photo is over 25 MB. Send a smaller one.")
                    return bytes(data)
            raise PhotoError("The photo link redirected too many times.")
    except (aiohttp.ClientError, TimeoutError) as ex:
        raise PhotoError("I couldn't download the photo. Try sending it again.") from ex
