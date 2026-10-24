"""The ASI:One reader, against a local stand-in for api.asi1.ai."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import pytest
from aiohttp import web

from yardsale import asione

Answer = tuple[int, Any]


@pytest.fixture
async def asi_one(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[tuple[list[Any], list[Answer]]]:
    """A local ASI:One. Tests push answers and read back the requests it got."""
    requests: list[Any] = []
    answers: list[Answer] = []

    async def completions(request: web.Request) -> web.Response:
        requests.append((request.headers.get("Authorization"), await request.json()))
        status, body = answers.pop(0)
        return web.json_response(body, status=status)

    app = web.Application()
    app.router.add_post("/v1/chat/completions", completions)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]
    monkeypatch.setattr(asione, "API_URL", f"http://127.0.0.1:{port}/v1/chat/completions")
    monkeypatch.setenv("ASI_ONE_API_KEY", "test-key")
    yield requests, answers
    await runner.cleanup()


def completion(fields: dict[str, Any]) -> Answer:
    return 200, {"choices": [{"message": {"role": "assistant", "content": json.dumps(fields)}}]}


async def test_typed_prices_and_notes_are_read(asi_one: tuple[list[Any], list[Answer]]) -> None:
    requests, answers = asi_one
    answers.append(
        completion({"intent": "sell", "asking_usd": 60, "floor_usd": 45, "note": "size M"})
    )

    message = await asione.read_seller_message("List this for $60, at least 45. Size M", True)

    assert (message.asking_usd, message.floor_usd, message.note) == (60, 45, "size M")
    auth, body = requests[0]
    assert auth == "Bearer test-key"
    assert body["model"] == "asi1-mini"
    assert body["response_format"]["type"] == "json_schema"
    assert body["response_format"]["json_schema"]["strict"] is True
    assert "Photo attached: yes" in body["messages"][1]["content"]


async def test_a_photo_is_always_a_sale(asi_one: tuple[list[Any], list[Answer]]) -> None:
    _, answers = asi_one
    answers.append(
        completion({"intent": "other", "asking_usd": None, "floor_usd": None, "note": ""})
    )
    assert (await asione.read_seller_message("what do you think?", True)).intent == "sell"


@pytest.mark.parametrize(
    "answer",
    [
        (401, {"error": "invalid key"}),
        completion({"intent": "sell"}),  # missing fields
        (200, {"choices": []}),
    ],
)
async def test_asi_one_trouble_falls_back_to_defaults(
    asi_one: tuple[list[Any], list[Answer]], answer: Answer
) -> None:
    _, answers = asi_one
    answers.append(answer)
    message = await asione.read_seller_message("sell this for $50", True)
    assert message == asione.fallback(has_photo=True)


async def test_no_key_means_no_request() -> None:
    message = await asione.read_seller_message("sell my bike", False)  # conftest clears the key
    assert message == asione.fallback(has_photo=False)
