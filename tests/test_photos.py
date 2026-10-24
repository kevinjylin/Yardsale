import base64
import io
import socket
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from aiohttp import web
from aiohttp.resolver import ThreadedResolver
from PIL import Image

from yardsale import photos
from yardsale.photos import (
    MAX_EDGE,
    PhotoError,
    PublicOnlyResolver,
    check_prepared_photo,
    download_photo,
    load_photo,
    prepare_photo,
)


def test_large_photo_is_shrunk_to_jpeg(tmp_path: Path) -> None:
    path = tmp_path / "big.png"
    Image.new("RGB", (4032, 3024), "blue").save(path)
    image_b64, media_type = load_photo(path)
    assert media_type == "image/jpeg"
    image = Image.open(io.BytesIO(base64.b64decode(image_b64)))
    assert image.format == "JPEG"
    assert max(image.size) == MAX_EDGE


def test_iphone_heic_photo_is_read(tmp_path: Path) -> None:
    path = tmp_path / "photo.heic"
    Image.new("RGB", (640, 480), "green").save(path)
    image_b64, _ = load_photo(path)
    assert Image.open(io.BytesIO(base64.b64decode(image_b64))).size == (640, 480)


def test_a_file_that_is_not_a_photo_is_refused() -> None:
    with pytest.raises(PhotoError):
        prepare_photo(b"<html>not a photo</html>")


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "http://localhost:8100/submit",
        "http://127.0.0.1/x.jpg",
        "http://10.0.0.5/a.png",
    ],
)
async def test_only_public_web_links_are_downloaded(url: str) -> None:
    with pytest.raises(PhotoError):
        await download_photo(url)


def test_sideways_phone_photo_is_turned_upright() -> None:
    image = Image.new("RGB", (200, 100), "blue")
    exif = image.getexif()
    exif[0x0112] = 6  # orientation: rotate 90 degrees clockwise to view
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", exif=exif.tobytes())
    image_b64, _ = prepare_photo(buffer.getvalue())
    assert Image.open(io.BytesIO(base64.b64decode(image_b64))).size == (100, 200)


def test_a_huge_image_is_refused_before_decoding() -> None:
    buffer = io.BytesIO()
    Image.new("1", (8000, 7000)).save(buffer, "PNG")  # 56 megapixels, small file
    with pytest.raises(PhotoError, match="too large"):
        prepare_photo(buffer.getvalue())


@pytest.mark.parametrize(
    ("image_b64", "media_type"),
    [("", "image/jpeg"), ("not base64!", "image/jpeg"), ("aGk=", "text/html"), ("aGk=", "")],
)
def test_photos_from_other_agents_are_checked(image_b64: str, media_type: str) -> None:
    with pytest.raises(PhotoError):
        check_prepared_photo(image_b64, media_type)
    check_prepared_photo("aGk=", "image/jpeg")


async def test_host_names_that_point_at_private_networks_are_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def resolves_to(self: Any, host: str, port: int = 0, family: Any = None) -> Any:
        address = {"public.example": "93.184.216.34", "sneaky.example": "10.0.0.7"}[host]
        return [{"hostname": host, "host": address, "port": port, "family": socket.AF_INET,
                 "proto": 0, "flags": 0}]  # fmt: skip

    monkeypatch.setattr(ThreadedResolver, "resolve", resolves_to)
    resolver = PublicOnlyResolver()
    assert (await resolver.resolve("public.example", 443))[0]["host"] == "93.184.216.34"
    with pytest.raises(OSError):
        await resolver.resolve("sneaky.example", 443)


@pytest.fixture
async def photo_server(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[str]:
    """A local web server, allowed as if it were public. It serves /photo.jpg and redirects."""

    async def photo(request: web.Request) -> web.Response:
        buffer = io.BytesIO()
        Image.new("RGB", (8, 8), "red").save(buffer, "JPEG")
        return web.Response(body=buffer.getvalue(), content_type="image/jpeg")

    async def redirect(request: web.Request) -> web.Response:
        raise web.HTTPFound(request.query["to"])

    app = web.Application()
    app.router.add_get("/photo.jpg", photo)
    app.router.add_get("/redirect", redirect)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]
    base = f"http://127.0.0.1:{port}"
    real_check = photos._public_web_url
    monkeypatch.setattr(
        photos, "_public_web_url", lambda url: url.startswith(base) or real_check(url)
    )
    yield base
    await runner.cleanup()


async def test_redirects_are_followed(photo_server: str) -> None:
    data = await download_photo(f"{photo_server}/redirect?to=/photo.jpg")
    assert prepare_photo(data)[1] == "image/jpeg"


async def test_redirects_to_private_addresses_are_refused(photo_server: str) -> None:
    with pytest.raises(PhotoError, match="public photo links"):
        await download_photo(f"{photo_server}/redirect?to=http://169.254.169.254/latest")


async def test_downloads_stop_at_the_size_limit(
    photo_server: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(photos, "MAX_DOWNLOAD_BYTES", 100)
    with pytest.raises(PhotoError, match="over 25 MB"):
        await download_photo(f"{photo_server}/photo.jpg")
