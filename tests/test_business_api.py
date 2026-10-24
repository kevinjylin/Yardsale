"""The HTTP API @yardsale (the Fetch Business agent) calls: finding, haggling, buying, and its
OpenAPI description. Runs against the offline Market with fake payments, on this machine only."""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from typing import Any

import aiohttp
import pytest

from tests import fakes, sim
from tests.helpers import local_server
from yardsale import bureau_server
from yardsale.business_api import KEY_HEADER, BusinessApi, openapi_spec
from yardsale.store import PAID, PENDING, Store

KEY = "test-key-for-the-business-api-0123456789"
PUBLIC = "https://yardsale-api.example.test"
BIKE = fakes.photo_url("bike")


class Client:
    def __init__(self, base: str, session: aiohttp.ClientSession) -> None:
        self.base = base
        self.session = session

    async def call(
        self, method: str, path: str, body: Any = None, key: str | None = KEY, **params: Any
    ) -> tuple[int, Any]:
        headers = {KEY_HEADER: key} if key is not None else {}
        async with self.session.request(
            method, self.base + path, json=body, params=params or None, headers=headers
        ) as response:
            return response.status, await response.json()


@pytest.fixture
async def shop(store: Store) -> AsyncIterator[tuple[sim.Market, fakes.FakePayments, Client]]:
    payments = fakes.FakePayments()
    async with sim.running_market(store, payments=payments) as market:
        api = BusinessApi(
            bureau_server.MarketBackend(bureau_server.runtime()),
            market.agent._build_context,
            KEY,
            PUBLIC,
        )
        async with local_server(api.app()) as base, aiohttp.ClientSession() as session:
            yield market, payments, Client(base, session)


async def list_bike(market: sim.Market, name: str, asking: int, lowest: int) -> None:
    seller = market.user(name)
    await seller.say("sell this", BIKE)
    await seller.say(f"ask ${asking}")
    await seller.say(f"lowest ${lowest}")
    assert "agent1q" in await seller.say("approve")


def test_the_description_is_openapi_3_with_every_operation() -> None:
    spec = openapi_spec(PUBLIC)
    assert spec["openapi"].startswith("3.")
    assert spec["servers"] == [{"url": PUBLIC}]
    scheme = spec["components"]["securitySchemes"]["apiKey"]
    assert scheme == {"type": "apiKey", "in": "header", "name": KEY_HEADER}
    ids = {op["operationId"] for path in spec["paths"].values() for op in path.values()}
    assert {"searchListings", "startHaggle", "getHaggle", "acceptDeal", "getDeal"} <= ids
    json.dumps(spec)  # plain JSON


async def test_every_call_but_the_description_needs_the_key(shop: Any) -> None:
    _, _, client = shop
    status, spec = await client.call("GET", "/openapi.json", key=None)
    assert status == 200 and spec["servers"] == [{"url": PUBLIC}]
    assert (await client.call("GET", "/listings", key=None))[0] == 401
    assert (await client.call("GET", "/listings", key="wrong"))[0] == 401
    assert (await client.call("POST", "/haggles", {"looking_for": "x", "max_usd": 1}, key=""))[
        0
    ] == 401
    async with client.session.get(
        client.base + "/listings", headers={"Authorization": f"Bearer {KEY}"}
    ) as response:
        assert response.status == 200


async def test_search_shows_asking_prices_never_floors(shop: Any) -> None:
    market, _, client = shop
    await list_bike(market, "ann", 360, 300)
    status, found = await client.call("GET", "/listings", q="hybrid bike")
    assert status == 200 and found["count"] == 1
    (bike,) = found["listings"]
    assert bike["asking_usd"] == 360 and bike["asking_price"] == "$360"
    text = json.dumps(found)
    assert "floor" not in text and "300" not in text
    assert (await client.call("GET", "/listings", q="bike", max_usd=200))[1]["count"] == 0
    assert (await client.call("GET", "/listings", max_usd="lots"))[0] == 400


async def test_haggle_accept_and_pay(shop: Any, store: Store) -> None:
    market, payments, client = shop
    await list_bike(market, "ann", 360, 300)
    await list_bike(market, "ben", 340, 290)

    status, haggle = await client.call(
        "POST", "/haggles", {"looking_for": "hybrid bike", "max_usd": 320}
    )
    assert status == 200 and haggle["status"] == "done"
    assert len(haggle["items"]) == 2
    assert 0 < haggle["best_price_usd"] <= 320
    for item in haggle["items"]:
        for r in item["rounds"]:
            assert r["buyer_offer_usd"] <= 320
            assert r["agreed"] or r["seller_reply_usd"] not in (300, 290)  # never a floor
    assert "haggled with 2 seller agent(s) at once" in haggle["summary"]

    status, deal = await client.call("POST", f"/haggles/{haggle['haggle_id']}/accept")
    assert status == 200
    assert deal["price_usd"] == haggle["best_price_usd"]
    assert deal["payment_status"] == PENDING
    assert deal["payment_url"] == f"https://pay.example.test/{deal['deal_id']}"
    (record,) = store.deals()
    assert record.buyer.startswith("chat:api-")

    # The same price can't be bought twice.
    again = await client.call("POST", f"/haggles/{haggle['haggle_id']}/accept")
    assert again[0] == 409 and again[1]["code"] == "no_price_on_offer"

    status, checked = await client.call("GET", f"/deals/{deal['deal_id']}")
    assert status == 200 and checked["paid"] is False
    payments.pay(deal["deal_id"])
    status, checked = await client.call("GET", f"/deals/{deal['deal_id']}")
    assert checked["payment_status"] == PAID and checked["paid"] is True
    assert checked["payment_url"] is None


async def test_calls_are_logged_without_ids_or_the_max(
    shop: Any, caplog: pytest.LogCaptureFixture
) -> None:
    market, _, client = shop
    await list_bike(market, "ann", 360, 300)
    caplog.set_level(logging.INFO, logger="yardsale.api")
    _, haggle = await client.call("POST", "/haggles", {"looking_for": "bike", "max_usd": 317})
    _, deal = await client.call("POST", f"/haggles/{haggle['haggle_id']}/accept")
    await client.call("GET", f"/deals/{deal['deal_id']}")
    await client.call("GET", "/listings", key="wrong")
    lines = [r.getMessage() for r in caplog.records if r.name == "yardsale.api"]
    assert "API POST /haggles -> 200" in "\n".join(lines)
    assert any(line.startswith("API POST /haggles/{haggle_id}/accept -> 200") for line in lines)
    assert any(line.startswith("API GET /deals/{deal_id} -> 200") for line in lines)
    assert any(line.startswith("API GET /listings -> 401") for line in lines)
    text = "\n".join(lines)
    assert haggle["haggle_id"] not in text  # only a short prefix, never the whole ID
    assert "317" not in text and "300" not in text


async def test_a_slow_haggle_answers_later(store: Store) -> None:
    payments = fakes.FakePayments()
    async with sim.running_market(store, payments=payments) as market:
        await list_bike(market, "ann", 360, 300)
        api = BusinessApi(
            bureau_server.MarketBackend(bureau_server.runtime()),
            market.agent._build_context,
            KEY,
            answer_within=0.0,
        )
        async with local_server(api.app()) as base, aiohttp.ClientSession() as session:
            client = Client(base, session)
            status, started = await client.call(
                "POST", "/haggles", {"looking_for": "bike", "max_usd": 320}
            )
            # A plain 200: tool runners can read 202 as a failure.
            assert status == 200 and started["status"] == "running"
            # getHaggle waits for the result, so one call is enough.
            api.answer_within = 5.0
            status, haggle = await client.call("GET", f"/haggles/{started['haggle_id']}")
            assert status == 200 and haggle["status"] == "done"
            assert haggle["best_price_usd"] is not None


async def test_bad_requests_and_other_buyers_deals(shop: Any, store: Store) -> None:
    market, _, client = shop
    await list_bike(market, "ann", 360, 300)
    assert (await client.call("POST", "/haggles", {"looking_for": "bike"}))[0] == 400
    assert (await client.call("POST", "/haggles", {"looking_for": "", "max_usd": 300}))[0] == 400
    assert (await client.call("POST", "/haggles", {"looking_for": "bike", "max_usd": -5}))[0] == 400
    nothing = await client.call("POST", "/haggles", {"looking_for": "piano", "max_usd": 300})
    assert nothing[0] == 200 and nothing[1]["status"] == "nothing_to_haggle"
    assert (await client.call("POST", "/haggles/no-such-haggle/accept"))[0] == 404
    assert (await client.call("GET", "/haggles/no-such-haggle"))[0] == 404

    # A chat buyer's deal isn't visible through the API.
    buyer = market.user("chat-buyer")
    await buyer.say("find me a hybrid bike, haggle for me, max $350")
    assert "Deal!" in await buyer.say("accept")
    (chat_deal,) = store.deals()
    assert (await client.call("GET", f"/deals/{chat_deal.deal_id}"))[0] == 404
