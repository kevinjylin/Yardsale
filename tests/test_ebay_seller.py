"""Selling on the seller's own eBay too (ebay_seller.py): connecting through eBay's consent page,
listing an approved item, and answering eBay Best Offers with Yardsale's haggling rules. eBay is
a local stand-in (tests/fakes.py); nothing reaches eBay."""

from __future__ import annotations

import hashlib
import logging
import re
from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import parse_qs, urlparse

import aiohttp
import pytest

from tests import fakes, sim
from tests.helpers import local_server
from yardsale import bureau_server, commands, config
from yardsale.business_api import BusinessApi
from yardsale.config import EbaySettings
from yardsale.ebay_seller import GRANT_TTL, MAX_COUNTERS, EbayClient, EbayError, EbaySeller
from yardsale.store import ACTIVE, EBAY_ACTIVE, EBAY_ENDED, EBAY_SOLD, SOLD, Listing, Store

BIKE = fakes.photo_url("bike")
LINK = re.compile(r"\((https://auth\.sandbox\.ebay\.com/oauth2/authorize\?[^)\s]+)\)")
API_KEY = "test-key-for-the-business-api-0123456789"
FLOOR_TEXT = ("300.00", "$300")  # the bike's floor, as eBay or a person would write it


def state_of(link: str) -> str:
    return parse_qs(urlparse(link).query)["state"][0]


@pytest.fixture
async def fake_ebay() -> AsyncIterator[tuple[fakes.FakeEbay, str]]:
    fake = fakes.FakeEbay()
    async with local_server(fake.app()) as base:
        yield fake, base


def seller_for(store: Store, base: str) -> EbaySeller:
    return EbaySeller(EbayClient(fakes.EBAY_SETTINGS, api_base=base), store)


@pytest.fixture
async def shop(
    store: Store, fake_ebay: tuple[fakes.FakeEbay, str]
) -> AsyncIterator[tuple[sim.Market, fakes.FakeEbay, EbaySeller]]:
    fake, base = fake_ebay
    ebay = seller_for(store, base)
    async with sim.running_market(store, ebay=ebay) as market:
        yield market, fake, ebay


async def connect(user: sim.ChatUser, fake: fakes.FakeEbay, ebay: EbaySeller) -> None:
    """The seller asks for the link, consents on eBay, and comes back."""
    found = LINK.search(await user.say("connect ebay 94110"))
    assert found
    await ebay.finish_connect(state_of(found.group(1)), fake.issue_code())


async def listed(
    market: sim.Market, fake: fakes.FakeEbay, ebay: EbaySeller
) -> tuple[sim.ChatUser, Listing, str]:
    """A connected seller lists the bike (ask $360, lowest $300). (seller, listing, eBay ID)"""
    seller = market.user("seller")
    await connect(seller, fake, ebay)
    await seller.say("sell this, at least $300", BIKE)
    await seller.say("ask $360")
    reply = await seller.say("approve")
    assert "Also on eBay: https://sandbox.ebay.com/itm/" in reply, reply
    listing = market.store.owned(seller_owner(market, seller), ACTIVE)[0]
    record = market.store.ebay_listing(listing.item_id)
    assert record is not None
    return seller, listing, record.ebay_item_id


def seller_owner(market: sim.Market, user: sim.ChatUser) -> str:
    key = hashlib.sha256(f"session:{user.session}".encode()).hexdigest()[:24]
    return f"asi1:{key}"


# --- Settings and commands ----------------------------------------------------------------------


def test_settings_default_to_the_sandbox(monkeypatch: pytest.MonkeyPatch) -> None:
    assert config.ebay_settings() is None
    monkeypatch.setenv("EBAY_CLIENT_ID", "Yardsale-Test-SBX-abc")
    with pytest.raises(config.ConfigError, match="EBAY_CLIENT_SECRET, EBAY_RUNAME"):
        config.ebay_settings()
    monkeypatch.setenv("EBAY_CLIENT_SECRET", "s")
    monkeypatch.setenv("EBAY_RUNAME", "r")
    settings = config.ebay_settings()
    assert settings is not None and settings.env == "sandbox"
    assert "client_secret" not in repr(settings)
    monkeypatch.setenv("EBAY_CLIENT_ID", "Yardsale-Live-PRD-abc")
    with pytest.raises(config.ConfigError, match="production keyset"):
        config.ebay_settings()
    monkeypatch.setenv("YARDSALE_EBAY_ENV", "production")
    settings = config.ebay_settings()
    assert settings is not None and settings.env == "production"
    monkeypatch.setenv("YARDSALE_EBAY_ENV", "live")
    with pytest.raises(config.ConfigError):
        config.ebay_settings()


@pytest.mark.parametrize(
    ("text", "kind", "zip_code", "pick"),
    [
        ("connect ebay", "ebay_connect", "", None),
        ("Connect my eBay account", "ebay_connect", "", None),
        ("connect ebay 94110", "ebay_connect", "94110", None),
        ("disconnect ebay", "ebay_disconnect", "", None),
        ("unlink my ebay account", "ebay_disconnect", "", None),
        ("ebay zip 94110", "ebay_zip", "94110", None),
        ("my zip code is 94110-1234", "ebay_zip", "94110", None),
        ("list on ebay", "ebay_list", "", None),
        ("list it on ebay too", "ebay_list", "", None),
        ("list on ebay 2", "ebay_list", "", 2),
    ],
)
def test_ebay_commands(text: str, kind: str, zip_code: str, pick: int | None) -> None:
    command = commands.parse(text)
    assert command is not None
    assert (command.kind, command.zip_code, command.pick) == (kind, zip_code, pick)


# --- Connecting ---------------------------------------------------------------------------------


def test_consent_link(store: Store) -> None:
    ebay = EbaySeller(EbayClient(fakes.EBAY_SETTINGS), store)
    link = ebay.start_connect("asi1:conversation-key")
    parsed = urlparse(link)
    query = parse_qs(parsed.query)
    assert (parsed.scheme, parsed.netloc, parsed.path) == (
        "https",
        "auth.sandbox.ebay.com",
        "/oauth2/authorize",
    )
    assert query["client_id"] == [fakes.EBAY_SETTINGS.client_id]
    assert query["redirect_uri"] == [fakes.EBAY_SETTINGS.runame]
    assert query["response_type"] == ["code"]
    scopes = query["scope"][0].split()
    assert "https://api.ebay.com/oauth/api_scope/sell.inventory" in scopes
    assert "https://api.ebay.com/oauth/api_scope/sell.account" in scopes
    state = state_of(link)
    assert len(state) >= 40 and "conversation" not in state
    # Only a hash of the state is kept, tied to the conversation.
    rows = store._db.execute("SELECT state_hash, owner FROM ebay_grants").fetchall()
    assert [tuple(row) for row in rows] == [
        (hashlib.sha256(state.encode()).hexdigest(), "asi1:conversation-key")
    ]
    assert fakes.EBAY_SETTINGS.client_secret not in link
    production = EbaySettings("Yardsale-Live-PRD-1", "secret", "Yardsale-RuName", "production")
    assert EbayClient(production).consent_url("s").startswith("https://auth.ebay.com/oauth2/")


async def test_code_becomes_tokens_that_refresh(fake_ebay: tuple[fakes.FakeEbay, str]) -> None:
    fake, base = fake_ebay
    now = [1_000_000.0]
    store = Store(clock=lambda: now[0])
    ebay = seller_for(store, base)
    state = state_of(ebay.start_connect("asi1:abc"))
    assert await ebay.finish_connect(state, fake.issue_code()) == "asi1:abc"
    account = store.ebay_account("asi1:abc")
    assert account is not None and account.access_token in fake.access_tokens
    assert account.access_token not in repr(account) and account.refresh_token not in repr(account)
    assert await ebay.access_token("asi1:abc") == account.access_token
    assert fake.refreshes == 0

    now[0] += 7200 - 60  # within five minutes of expiring
    fresh = await ebay.access_token("asi1:abc")
    assert fresh != account.access_token and fresh in fake.access_tokens
    assert fake.refreshes == 1
    assert await ebay.access_token("asi1:abc") == fresh

    # A state works once, and only for a while.
    with pytest.raises(EbayError, match="expired or was already used"):
        await ebay.finish_connect(state, fake.issue_code())
    old = state_of(ebay.start_connect("asi1:def"))
    now[0] += GRANT_TTL + 1
    with pytest.raises(EbayError, match="expired or was already used"):
        await ebay.finish_connect(old, fake.issue_code())
    with pytest.raises(EbayError, match="expired or was already used"):
        await ebay.finish_connect("made-up-state", fake.issue_code())
    assert store.ebay_account("asi1:def") is None
    store.close()


async def test_callback_page_connects_and_the_chat_says_so(
    shop: tuple[sim.Market, fakes.FakeEbay, EbaySeller],
) -> None:
    market, fake, _ = shop
    seller = market.user("seller")
    reply = await seller.say("connect ebay")
    found = LINK.search(reply)
    assert found and "ZIP code" in reply and "password" in reply
    state = state_of(found.group(1))
    api = BusinessApi(
        bureau_server.MarketBackend(bureau_server.runtime()), market.agent._build_context, API_KEY
    )
    async with local_server(api.app()) as base, aiohttp.ClientSession() as session:
        # eBay's redirect carries no API key.
        params = {"state": state, "code": fake.issue_code(), "expires_in": "299"}
        async with session.get(f"{base}/ebay/callback", params=params) as response:
            assert response.status == 200
            assert "eBay connected. Go back to the chat." in await response.text()
        async with session.get(f"{base}/ebay/callback", params=params) as response:
            assert response.status == 400  # used once
        async with session.get(f"{base}/ebay/callback", params={"state": "x"}) as response:
            assert response.status == 400  # declined on eBay: no code
    reply = await seller.say("ebay zip 94110")
    assert reply.startswith("Your eBay account is connected.")
    assert "eBay listings use ZIP code 94110" in reply
    assert "connected" not in (await seller.say("help")).split("\n")[0]
    assert "already connected" in await seller.say("connect ebay")


# --- Listing ------------------------------------------------------------------------------------


async def test_approved_listing_goes_on_ebay_without_the_floor(
    shop: tuple[sim.Market, fakes.FakeEbay, EbaySeller],
) -> None:
    market, fake, ebay = shop
    seller, listing, ebay_item_id = await listed(market, fake, ebay)
    assert listing.floor_cents == 30000 and listing.asking_cents == 36000
    request = fake.items[ebay_item_id].request
    assert '<StartPrice currencyID="USD">360.00</StartPrice>' in request
    assert "<BestOfferEnabled>true</BestOfferEnabled>" in request
    assert "<PostalCode>94110</PostalCode>" in request
    assert "<CategoryID>177831</CategoryID>" in request
    assert "Trek FX 2 Disc" in request
    for name, sent in fake.calls:
        for floor in FLOOR_TEXT:
            assert floor not in sent, f"{name} sent the floor"
        assert "MinimumBestOfferPrice" not in sent and "BestOfferAutoAcceptPrice" not in sent
    assert fake.pictures and fake.pictures[0][:2] == b"\xff\xd8"  # the seller's JPEG
    assert f"On eBay: https://sandbox.ebay.com/itm/{ebay_item_id}" in await seller.say(
        "my listings"
    )
    # Listing it again doesn't make a second eBay listing.
    assert "already on eBay" in await seller.say("list on ebay")
    assert len(fake.items) == 1


async def test_ebay_failing_never_fails_approval(
    shop: tuple[sim.Market, fakes.FakeEbay, EbaySeller],
) -> None:
    market, fake, ebay = shop
    seller = market.user("seller")
    found = LINK.search(await seller.say("connect ebay"))
    assert found
    await ebay.finish_connect(state_of(found.group(1)), fake.issue_code())
    await seller.say("sell this, at least $300", BIKE)
    reply = await seller.say("approve")
    assert "is for sale at" in reply and "agent1q" in reply
    assert "eBay needs your ZIP code" in reply
    await seller.say("ebay zip 94110")
    fake.fail.add("AddFixedPriceItem")
    reply = await seller.say("list on ebay")
    assert "couldn't list" in reply and "Internal error" in reply
    assert market.store.owned(seller_owner(market, seller), ACTIVE)
    fake.fail.clear()
    assert "Also on eBay" in await seller.say("list on ebay")


# --- Best Offers --------------------------------------------------------------------------------


async def test_offer_at_the_floor_is_accepted(
    shop: tuple[sim.Market, fakes.FakeEbay, EbaySeller],
) -> None:
    market, fake, ebay = shop
    seller, listing, ebay_item_id = await listed(market, fake, ebay)
    offer_id = fake.offer(ebay_item_id, "bikefan99", 30000)
    await ebay.poll()
    assert fake.responses == [
        {
            "offer_id": offer_id,
            "action": "Accept",
            "counter": "",
            "message": "Deal. Thanks for the offer!",
        }
    ]
    assert market.store.get(listing.item_id).status == SOLD  # type: ignore[union-attr]
    deal = market.store.deal_for(listing.item_id)
    assert deal is not None and deal.price_cents == 30000 and deal.buyer == "ebay:bikefan99"
    assert market.store.ebay_listing(listing.item_id).status == EBAY_SOLD  # type: ignore[union-attr]
    assert "Sold on eBay: Trek FX 2 Disc" in await seller.say("my listings")
    await ebay.poll()  # nothing more to do
    assert len(fake.responses) == 1


async def test_offer_below_the_floor_gets_a_counter_above_it(
    shop: tuple[sim.Market, fakes.FakeEbay, EbaySeller],
) -> None:
    market, fake, ebay = shop
    _, listing, ebay_item_id = await listed(market, fake, ebay)
    previous = listing.asking_cents
    for n in range(MAX_COUNTERS):
        fake.offer(ebay_item_id, "bargainer", 25000 + n * 500)
        await ebay.poll()
        answer = fake.responses[-1]
        assert answer["action"] == "Counter", answer
        counter = round(float(answer["counter"]) * 100)
        assert listing.floor_cents < counter <= previous < listing.asking_cents or n == 0
        assert counter > listing.floor_cents
        assert answer["message"] and f"${counter // 100}" in answer["message"]
        assert len(answer["message"]) <= 250
        assert not any(floor in answer["message"] for floor in FLOOR_TEXT)
        previous = counter
    # eBay allows 5 counters: the 6th offer is declined, with the last price still standing.
    fake.offer(ebay_item_id, "bargainer", 28000)
    await ebay.poll()
    assert fake.responses[-1]["action"] == "Decline"
    assert f"${previous // 100}" in fake.responses[-1]["message"]
    # A lowball from someone else moves nothing.
    fake.offer(ebay_item_id, "lowballer", 10000)
    await ebay.poll()
    assert fake.responses[-1]["action"] == "Decline"
    assert "well under" in fake.responses[-1]["message"]
    assert market.store.get(listing.item_id).status == ACTIVE  # type: ignore[union-attr]


async def test_listings_stay_in_step(shop: tuple[sim.Market, fakes.FakeEbay, EbaySeller]) -> None:
    market, fake, ebay = shop
    seller, listing, ebay_item_id = await listed(market, fake, ebay)
    # Bought on eBay: sold on Yardsale too.
    fake.items[ebay_item_id].sales.append(("txn-1", "quickbuyer", 36000))
    await ebay.poll()
    assert market.store.get(listing.item_id).status == SOLD  # type: ignore[union-attr]
    deal = market.store.deal_for(listing.item_id)
    assert deal is not None and deal.deal_id == "ebay-txn-1" and deal.price_cents == 36000

    # Taken down on Yardsale: ended on eBay.
    await seller.say("sell this, at least $300", fakes.photo_url("bike-two"))
    assert "Also on eBay" in await seller.say("approve")
    second = market.store.owned(seller_owner(market, seller), ACTIVE)[0]
    record = market.store.ebay_listing(second.item_id)
    assert record is not None and record.status == EBAY_ACTIVE
    await seller.say("unlist")
    await ebay.poll()
    assert fake.items[record.ebay_item_id].ended
    assert market.store.ebay_listing(second.item_id).status == EBAY_ENDED  # type: ignore[union-attr]


async def test_disconnect_ends_listings_and_forgets_tokens(
    shop: tuple[sim.Market, fakes.FakeEbay, EbaySeller],
) -> None:
    market, fake, ebay = shop
    seller, listing, ebay_item_id = await listed(market, fake, ebay)
    reply = await seller.say("disconnect ebay")
    assert "Disconnected" in reply and "ended 1 eBay listing" in reply
    assert fake.items[ebay_item_id].ended
    assert market.store.ebay_account(seller_owner(market, seller)) is None
    assert market.store.get(listing.item_id).status == ACTIVE  # type: ignore[union-attr]
    assert "isn't connected" in await seller.say("disconnect ebay")


async def test_no_tokens_codes_or_floors_in_logs(
    shop: tuple[sim.Market, fakes.FakeEbay, EbaySeller], caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    market, fake, ebay = shop
    codes_before = set(fake.codes)
    _, listing, ebay_item_id = await listed(market, fake, ebay)
    account = market.store.ebay_account(listing.owner)
    assert account is not None
    market.store.update_ebay_access(listing.owner, account.access_token, 0)  # force a refresh
    fake.offer(ebay_item_id, "bargainer", 26000)
    await ebay.poll()
    fake.offer(ebay_item_id, "bikefan", 31000)
    await ebay.poll()
    assert fake.refreshes == 1
    secrets: list[Any] = [*fake.minted, fakes.EBAY_SETTINGS.client_secret]
    codes = [f"code-{n}" for n in range(1, 50)] + list(codes_before)
    for record in caplog.records:
        message = record.getMessage()
        for secret in secrets:
            assert secret not in message, f"{record.name} logged a token"
        if record.name.startswith("yardsale"):
            assert not any(code in message for code in codes), f"{record.name} logged a code"
            assert not any(floor in message for floor in FLOOR_TEXT), f"{record.name}: floor"
