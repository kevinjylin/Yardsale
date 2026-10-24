import base64
import io
from pathlib import Path

import pytest
from PIL import Image

from yardsale.photos import MAX_EDGE, PhotoError, download_photo, load_photo, prepare_photo


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
