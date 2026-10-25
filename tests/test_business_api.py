"""The HTTP API @yardsale (the Fetch Business agent) calls: finding, negotiating with Craigslist
sellers by email, haggling and buying (with the haggle tools on), and its OpenAPI description.
Runs against the offline Market with fake payments, on this machine only."""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from dataclasses import replace
from typing import Any

import aiohttp
import pytest

from tests import fakes, sim
from tests.helpers import local_server
from tests.test_craigslist_mail import INBOX, PASSWORD
from yardsale import bureau_server, craigslist_buyer
from yardsale.business_api import (
    HAGGLE_PATHS,
    ID_LENGTH,
    ID_LETTERS,
    KEY_HEADER,
    NEGOTIATION_BUYER,
    BusinessApi,
    negotiation_json,
    openapi_spec,
)
from yardsale.store import PAID, PENDING, Store

KEY = "test-key-for-the-business-api-0123456789"
PUBLIC = "https://yardsale-api.example.test"
BIKE = fakes.photo_url("bike")
POST = "a1b2c3d4e5@sale.craigslist.org"  # a Craigslist post's reply address, as Reply shows it
ALWAYS = {"searchListings", "searchOtherSites", "priceCheck", "startNegotiation", "getNegotiation"}
HAGGLING = {"startHaggle", "getHaggle", "acceptDeal", "getDeal"}


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
            haggle=True,
        )
        async with local_server(api.app()) as base, aiohttp.ClientSession() as session:
            yield market, payments, Client(base, session)


async def list_bike(market: sim.Market, name: str, asking: int, lowest: int) -> None:
    seller = market.user(name)
    await seller.say("sell this", BIKE)
    await seller.say(f"ask ${asking}")
    await seller.say(f"lowest ${lowest}")
    assert "agent1q" in await seller.say("approve")


def operations(spec: Any) -> set[str]:
    return {op["operationId"] for path in spec["paths"].values() for op in path.values()}


def test_the_description_is_openapi_3_with_the_haggle_tools_only_when_asked() -> None:
    spec = openapi_spec(PUBLIC)
    assert spec["openapi"].startswith("3.")
    assert spec["servers"] == [{"url": PUBLIC}]
    scheme = spec["components"]["securitySchemes"]["apiKey"]
    assert scheme == {"type": "apiKey", "in": "header", "name": KEY_HEADER}
    # Agent-to-agent buying was dropped from the pitch: by default Fetch doesn't see it at all.
    assert operations(spec) == ALWAYS | {"getOpenApi"}
    assert "startHaggle" not in spec["info"]["description"]
    assert "startNegotiation" in spec["info"]["description"]
    json.dumps(spec)  # plain JSON

    with_haggling = openapi_spec(PUBLIC, haggle=True)
    assert operations(with_haggling) == ALWAYS | HAGGLING | {"getOpenApi"}
    assert set(HAGGLE_PATHS) <= set(with_haggling["paths"])
    assert "startHaggle" in with_haggling["info"]["description"]
    json.dumps(with_haggling)


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
    assert "startHaggle" in found["next_step"]  # the shop fixture has the haggle tools on
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
    # @yardsale's next turn sees only the chat, so the ID goes in the text it shows the buyer.
    assert haggle["summary"].endswith(f"\nHaggle ID: {haggle['haggle_id']}")
    assert f"'Haggle ID: {haggle['haggle_id']}' in your reply" in haggle["next_step"]

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


async def test_haggle_ids_are_short_and_forgive_capitals_and_spaces(
    shop: Any, store: Store
) -> None:
    market, _, client = shop
    await list_bike(market, "ann", 360, 300)
    _, haggle = await client.call("POST", "/haggles", {"looking_for": "bike", "max_usd": 320})
    haggle_id = haggle["haggle_id"]
    assert len(haggle_id) == ID_LENGTH and set(haggle_id) <= set(ID_LETTERS)

    # A model passing the ID along may capitalize it or add a space.
    status, again = await client.call("GET", f"/haggles/{haggle_id.upper()}")
    assert status == 200 and again["haggle_id"] == haggle_id
    status, deal = await client.call("POST", f"/haggles/%20{haggle_id.upper()}/accept")
    assert status == 200 and deal["price_usd"] == haggle["best_price_usd"]

    # A mistyped ID says to copy it exactly, so the caller doesn't take it for an expiry.
    typo = haggle_id[:-1] + ("a" if haggle_id[-1] != "a" else "b")
    status, error = await client.call("POST", f"/haggles/{typo}/accept")
    assert status == 404 and error["code"] == "not_found"
    assert "exactly" in error["error"]

    # Haggles saved before IDs were shortened are mixed case, and still found.
    store.save_conversation("api:OldMixedCaseId", {"buyer_ref": "api-x", "status": "failed"})
    assert (await client.call("GET", "/haggles/OldMixedCaseId"))[1]["code"] == "haggle_failed"


async def test_a_lost_haggle_id_is_found_again_by_haggling_again(shop: Any) -> None:
    market, _, client = shop
    await list_bike(market, "ann", 360, 300)
    await list_bike(market, "ben", 340, 290)
    ask = {"looking_for": "hybrid bike", "max_usd": 320}
    _, first = await client.call("POST", "/haggles", ask)
    status, error = await client.call("POST", "/haggles/qqqqqqqq/accept")
    assert status == 404 and "call startHaggle again" in error["error"]

    # The 404 promises the same price from a new haggle, so it has to hold.
    _, second = await client.call("POST", "/haggles", ask)
    assert second["haggle_id"] != first["haggle_id"]
    assert second["best_item_id"] == first["best_item_id"]
    assert second["best_price_usd"] == first["best_price_usd"]
    status, deal = await client.call("POST", f"/haggles/{second['haggle_id']}/accept")
    assert status == 200 and deal["price_usd"] == first["best_price_usd"]


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

    # An unknown ID logs its first 3 characters and length, to show what the caller sent.
    caplog.clear()
    await client.call("POST", "/haggles/xyzmistyped/accept")
    await client.call("GET", "/haggles/abcdefgh")
    lines = [r.getMessage() for r in caplog.records if r.name == "yardsale.api"]
    assert "API haggle 'xyz' not found (11 characters)" in lines
    assert "API haggle 'abc' not found (8 characters)" in lines
    assert not any("mistyped" in line or "abcdefgh" in line for line in lines)


async def test_a_slow_haggle_answers_later(store: Store) -> None:
    payments = fakes.FakePayments()
    async with sim.running_market(store, payments=payments) as market:
        await list_bike(market, "ann", 360, 300)
        api = BusinessApi(
            bureau_server.MarketBackend(bureau_server.runtime()),
            market.agent._build_context,
            KEY,
            answer_within=0.0,
            haggle=True,
        )
        async with local_server(api.app()) as base, aiohttp.ClientSession() as session:
            client = Client(base, session)
            status, started = await client.call(
                "POST", "/haggles", {"looking_for": "bike", "max_usd": 320}
            )
            # A plain 200: tool runners can read 202 as a failure.
            assert status == 200 and started["status"] == "running"
            assert f"Haggle ID: {started['haggle_id']}" in started["next_step"]
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
    await buyer.say("find me a hybrid bike")
    await buyer.say("haggle with the seller, max $350")
    assert "Deal!" in await buyer.say("accept")
    (chat_deal,) = store.deals()
    assert (await client.call("GET", f"/deals/{chat_deal.deal_id}"))[0] == 404


async def test_the_haggle_tools_are_off_unless_asked_for(store: Store) -> None:
    """Without YARDSALE_API_HAGGLE the four calls aren't described, and a Business Tool that
    still has them from an earlier import gets a 404 that says so."""
    async with sim.running_market(store, payments=fakes.FakePayments()) as market:
        await list_bike(market, "ann", 360, 300)
        api = BusinessApi(
            bureau_server.MarketBackend(bureau_server.runtime()),
            market.agent._build_context,
            KEY,
        )
        async with local_server(api.app()) as base, aiohttp.ClientSession() as session:
            client = Client(base, session)
            _, spec = await client.call("GET", "/openapi.json", key=None)
            assert operations(spec) == ALWAYS | {"getOpenApi"}
            for method, path in (
                ("POST", "/haggles"),
                ("GET", "/haggles/abcdefgh"),
                ("POST", "/haggles/abcdefgh/accept"),
                ("GET", "/deals/x"),
            ):
                status, error = await client.call(method, path, {"looking_for": "b", "max_usd": 9})
                assert (status, error["code"]) == (404, "haggle_off"), path
                assert "startNegotiation" in error["error"]
            # The key is still checked first, and the search doesn't point at a missing tool.
            assert (await client.call("POST", "/haggles", {}, key="wrong"))[0] == 401
            status, found = await client.call("GET", "/listings", q="hybrid bike")
            assert status == 200 and found["count"] == 1
            assert "startHaggle" not in found["next_step"]


async def test_start_and_follow_a_craigslist_negotiation(
    shop: Any, store: Store, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _, _, client = shop
    ask = {"relay_address": POST, "title": "Trek FX 2 bike", "max_usd": 250, "asking_usd": 300}
    status, error = await client.call("POST", "/negotiations", ask)
    assert status == 503 and error["code"] == "mail_off"  # no inbox on this server
    monkeypatch.setenv("YARDSALE_MAIL_ADDRESS", INBOX)
    monkeypatch.setenv("YARDSALE_MAIL_PASSWORD", PASSWORD)
    caplog.set_level(logging.INFO, logger="yardsale.api")

    status, started = await client.call("POST", "/negotiations", ask)
    assert status == 200 and started["status"] == "opening"
    ref = started["ref"]
    assert len(ref) == 8 and set(ref) <= set(ID_LETTERS)
    assert started["title"] == "Trek FX 2 bike" and started["asking_usd"] == 300
    assert "$195 to start" in started["summary"] and "in person" in started["summary"]
    assert f"Negotiation ref: {ref}" in started["next_step"]
    assert "250" not in json.dumps(started)  # the max never comes back
    (saved,) = craigslist_buyer.owned(store, NEGOTIATION_BUYER)
    assert (saved.ref, saved.max_cents, saved.asking_cents) == (ref, 25000, 30000)

    # Following it, with the ref capitalised as a model might write it.
    status, again = await client.call("GET", f"/negotiations/{ref.upper()}")
    assert status == 200 and again["ref"] == ref
    assert "first email is about to go out" in again["summary"]
    assert f"Negotiation ref: {ref}" in again["next_step"] and "250" not in json.dumps(again)

    # Asked again for the same post, it's the same negotiation, not a second one.
    status, twice = await client.call("POST", "/negotiations", ask)
    assert status == 200 and twice["ref"] == ref and twice["already_started"] is True
    assert "already negotiating" in twice["summary"]
    assert len(craigslist_buyer.owned(store, NEGOTIATION_BUYER)) == 1

    # A chat buyer's negotiation isn't visible here, and nobody can join it.
    other = craigslist_buyer.start(store, "asi1:someone", "zz9@sale.craigslist.org", 20000, "Lamp")
    assert (await client.call("GET", f"/negotiations/{other.ref}"))[0] == 404
    status, error = await client.call(
        "POST", "/negotiations", {"relay_address": "zz9@sale.craigslist.org", "max_usd": 10}
    )
    assert status == 409 and error["code"] == "taken"
    status, error = await client.call("GET", "/negotiations/qqqqqqqq")
    assert status == 404 and "exactly" in error["error"]

    # The log has the routes, never the ref, the title, or the max.
    text = "\n".join(r.getMessage() for r in caplog.records if r.name == "yardsale.api")
    assert "API POST /negotiations -> 200" in text
    assert "API GET /negotiations/{ref} -> 200" in text
    assert ref not in text and "Trek" not in text and "250" not in text


async def test_bad_negotiation_requests_and_lenient_addresses(
    shop: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("YARDSALE_MAIL_ADDRESS", INBOX)
    monkeypatch.setenv("YARDSALE_MAIL_PASSWORD", PASSWORD)
    _, _, client = shop
    bad = {"relay_address": "someone@gmail.com", "max_usd": 250}
    assert (await client.call("POST", "/negotiations", bad))[1]["code"] == "bad_request"
    assert (await client.call("POST", "/negotiations", {"relay_address": POST}))[1][
        "code"
    ] == "bad_amount"
    assert (
        await client.call(
            "POST", "/negotiations", {"relay_address": POST, "max_usd": 250, "asking_usd": "lots"}
        )
    )[1]["code"] == "bad_amount"
    assert (await client.call("POST", "/negotiations", "nope"))[0] == 400
    assert (await client.call("POST", "/negotiations", {"relay_address": POST}, key=None))[0] == 401

    # The address may arrive inside what the buyer pasted, the max as text, and the title can
    # come from the post's link.
    status, started = await client.call(
        "POST",
        "/negotiations",
        {
            "relay_address": f"Here it is: {POST.upper()} thanks",
            "post_url": "https://sfbay.craigslist.org/eby/bik/d/berkeley-trek-fx-2-hybrid-bike/7777.html",
            "max_usd": "$250",
        },
    )
    assert status == 200 and started["relay_address"] == POST
    assert started["title"] == "berkeley trek fx 2 hybrid bike"
    assert started["asking_usd"] is None and "ask the seller's price first" in started["summary"]


def test_a_finished_negotiation_tells_the_buyer_how_it_ended(store: Store) -> None:
    n = craigslist_buyer.start(store, NEGOTIATION_BUYER, POST, 25000, "Trek FX 2 bike", 30000)
    agreed = replace(
        n,
        status=craigslist_buyer.AGREED,
        offer_cents=22000,
        seller_cents=22000,
        price_cents=22000,
        rounds=2,
        note="Saturday 10am at Ashby BART works.",
    )
    data = negotiation_json(agreed)
    assert data["agreed_usd"] == 220 and data["offers_made"] == 2
    assert data["seller_note"] == "Saturday 10am at Ashby BART works."
    assert "agreed at $220" in data["summary"] and "in person" in data["next_step"]
    assert "250" not in json.dumps(data)

    stopped = replace(n, status=craigslist_buyer.STOPPED, seller_cents=28000, rounds=4)
    data = negotiation_json(stopped)
    assert "no deal" in data["summary"] and "raise their max" in data["next_step"]
    assert "250" not in json.dumps(data)
    for status in (craigslist_buyer.SOLD, craigslist_buyer.NEEDS_YOU):
        assert negotiation_json(replace(n, status=status))["next_step"]
