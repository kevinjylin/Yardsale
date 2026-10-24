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
        completion(
            {
                "intent": "sell",
                "asking_usd": 60,
                "floor_usd": 45,
                "note": "size M",
                "query": "",
                "budget_usd": None,
            }
        )
    )

    message = await asione.read_seller_message("List this for $60, at least 45. Size M", True)

    assert (message.asking_usd, message.floor_usd, message.note) == (60, 45, "size M")
    auth, body = requests[0]
    assert auth == "Bearer test-key"
    assert body["model"] == "asi1-mini"
    assert body["response_format"]["type"] == "json_schema"
    assert body["response_format"]["json_schema"]["strict"] is True
    assert "Photo attached: yes" in body["messages"][1]["content"]
    assert set(body["response_format"]["json_schema"]["schema"]["required"]) == {
        "intent",
        "asking_usd",
        "floor_usd",
        "note",
        "query",
        "budget_usd",
    }


async def test_a_photo_is_always_a_sale(asi_one: tuple[list[Any], list[Answer]]) -> None:
    _, answers = asi_one
    answers.append(
        completion(
            {
                "intent": "other",
                "asking_usd": None,
                "floor_usd": None,
                "note": "",
                "query": "",
                "budget_usd": None,
            }
        )
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
async def test_asi_one_trouble_falls_back_to_the_local_reader(
    asi_one: tuple[list[Any], list[Answer]], answer: Answer
) -> None:
    _, answers = asi_one
    answers.append(answer)
    message = await asione.read_seller_message("sell this for $50", True)
    assert message == asione.read_locally("sell this for $50", True)
    assert message.asking_usd == 50


async def test_no_key_means_no_request() -> None:
    message = await asione.read_seller_message("sell my bike", False)  # conftest clears the key
    assert message.intent == "sell"


@pytest.mark.parametrize(
    ("text", "has_photo", "expected"),
    [
        ("sell this, at least $40, small dent", True, ("sell", None, 40, "small dent")),
        (
            "List this jacket for $60. Size M, worn twice.",
            True,
            ("sell", 60, None, "Size M, worn twice"),
        ),
        ("", True, ("sell", None, None, "")),
        ("I want to sell my bike", False, ("sell", None, None, "")),
        ("hi, what do you do?", False, ("other", None, None, "")),
    ],
)
def test_local_reader_for_sellers(
    text: str, has_photo: bool, expected: tuple[str, float | None, float | None, str]
) -> None:
    message = asione.read_locally(text, has_photo)
    assert (message.intent, message.asking_usd, message.floor_usd, message.note) == expected


def test_local_reader_for_buyers() -> None:
    message = asione.read_locally("find me a cheap road bike under $300", False)
    assert (message.intent, message.query, message.budget_usd) == ("buy", "cheap road bike", 300)
    message = asione.read_locally("do you have any espresso machines?", False)
    assert (message.intent, message.query) == ("buy", "espresso machines")
