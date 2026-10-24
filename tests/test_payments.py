"""Payment links (payments.py), checkout (checkout.py), and the orders webhook (orders.py),
against local stand-ins for Stripe, Visa Acceptance, and an order list."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
from decimal import Decimal
from typing import Any

import aiohttp
import pytest
from aiohttp import web

from tests import fakes
from tests.helpers import local_server
from yardsale import orders, payments
from yardsale.checkout import Checkout
from yardsale.payments import (
    PaymentConfigError,
    PaymentError,
    PaymentLink,
    StripeCheckout,
    VisaPayByLink,
    commission_text,
    provider_from_env,
    split,
)
from yardsale.store import EXPIRED, NOT_COLLECTED, PAID, PENDING, PROPOSED, Listing, Store

SECRET = base64.b64encode(b"visa-test-shared-secret-32-bytes!").decode()
PAYMENT_KEYS = (
    "YARDSALE_PAYMENTS",
    "VISA_MERCHANT_ID",
    "VISA_KEY_ID",
    "VISA_SHARED_SECRET",
    "VISA_API_BASE",
    "STRIPE_SECRET_KEY",
    "YARDSALE_COMMISSION_PERCENT",
    "YARDSALE_ORDERS_WEBHOOK",
    "YARDSALE_ORDERS_SECRET",
)


@pytest.fixture(autouse=True)
def no_payment_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in PAYMENT_KEYS:
        monkeypatch.delenv(name, raising=False)


# --- Commission ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("price", "percent", "expected"),
    [(36000, None, (1800, 34200)), (999, None, (50, 949)), (1000, Decimal("7.5"), (75, 925))],
)
def test_split(price: int, percent: Decimal | None, expected: tuple[int, int]) -> None:
    assert split(price, percent) == expected


def test_commission_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    assert commission_text() == "5"
    monkeypatch.setenv("YARDSALE_COMMISSION_PERCENT", "10")
    assert commission_text() == "10" and split(36000) == (3600, 32400)
    monkeypatch.setenv("YARDSALE_COMMISSION_PERCENT", "7.50")
    assert commission_text() == "7.5"
    for bad in ("lots", "100", "-1"):
        monkeypatch.setenv("YARDSALE_COMMISSION_PERCENT", bad)
        with pytest.raises(PaymentConfigError):
            split(1000)


# --- Choosing a provider ------------------------------------------------------------------------


def test_no_keys_means_no_payments() -> None:
    assert provider_from_env() is None


def test_only_test_keys_and_sandboxes_are_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_live_123")
    with pytest.raises(PaymentConfigError, match="test key"):
        provider_from_env()
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_123")
    assert isinstance(provider_from_env(), StripeCheckout)

    for name, value in (("VISA_MERCHANT_ID", "m"), ("VISA_KEY_ID", "k")):
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("VISA_SHARED_SECRET", SECRET)
    assert isinstance(provider_from_env(), VisaPayByLink)  # Visa first when both are set
    monkeypatch.setenv("YARDSALE_PAYMENTS", "stripe")
    assert isinstance(provider_from_env(), StripeCheckout)
    monkeypatch.setenv("YARDSALE_PAYMENTS", "none")
    assert provider_from_env() is None
    monkeypatch.setenv("YARDSALE_PAYMENTS", "visa")
    monkeypatch.setenv("VISA_API_BASE", "https://api.visaacceptance.com")
    with pytest.raises(PaymentConfigError, match="sandbox"):
        provider_from_env()
    monkeypatch.setenv("YARDSALE_PAYMENTS", "paypal")
    with pytest.raises(PaymentConfigError):
        provider_from_env()


def test_a_visa_secret_must_be_base64() -> None:
    with pytest.raises(PaymentConfigError, match="base64"):
        VisaPayByLink("m", "k", "not base64!")


# --- Stripe Checkout ----------------------------------------------------------------------------


async def test_stripe_checkout_links_and_states() -> None:
    seen: list[tuple[str, dict[str, Any]]] = []
    sessions: dict[str, dict[str, Any]] = {}

    async def create(request: web.Request) -> web.Response:
        assert request.headers["Authorization"] == "Bearer sk_test_abc"
        form = dict(await request.post())
        seen.append((request.headers.get("Idempotency-Key", ""), form))
        sessions["cs_test_1"] = {"id": "cs_test_1", "status": "open", "payment_status": "unpaid"}
        return web.json_response({**sessions["cs_test_1"], "url": "https://checkout.test/cs_1"})

    async def read(request: web.Request) -> web.Response:
        session = sessions.get(request.match_info["id"])
        if session is None:
            return web.json_response({"error": {"message": "No such session"}}, status=404)
        return web.json_response(session)

    app = web.Application()
    app.router.add_post("/v1/checkout/sessions", create)
    app.router.add_get("/v1/checkout/sessions/{id}", read)
    async with local_server(app) as base:
        stripe = StripeCheckout("sk_test_abc", base_url=base)
        link = await stripe.create("deal-1", "Trek FX 2", 34000)
        assert link == PaymentLink("stripe", "cs_test_1", "https://checkout.test/cs_1")
        key, form = seen[0]
        assert key == "yardsale-deal-1"  # a retry can't make a second checkout
        assert form["line_items[0][price_data][unit_amount]"] == "34000"
        assert form["client_reference_id"] == "deal-1" and form["mode"] == "payment"
        assert await stripe.state(link) == "pending"
        sessions["cs_test_1"]["payment_status"] = "paid"
        assert await stripe.state(link) == "paid"
        sessions["cs_test_1"].update(payment_status="unpaid", status="expired")
        assert await stripe.state(link) == "expired"
        with pytest.raises(PaymentError, match="404: No such session") as error:
            await stripe.state(PaymentLink("stripe", "cs_missing", ""))
        assert "sk_test" not in str(error.value)


async def test_a_provider_that_times_out_counts_as_unreachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A slow network (as at a venue) is a PaymentError, so the minute-by-minute check moves on."""

    async def slow(request: web.Request) -> web.Response:
        await asyncio.sleep(1)
        return web.json_response({})

    app = web.Application()
    app.router.add_post("/v1/checkout/sessions", slow)
    app.router.add_get("/v1/checkout/sessions/{id}", slow)
    monkeypatch.setattr(payments, "TIMEOUT", aiohttp.ClientTimeout(total=0.05))
    async with local_server(app) as base:
        stripe = StripeCheckout("sk_test_abc", base_url=base)
        with pytest.raises(PaymentError, match="couldn't be reached"):
            await stripe.create("deal-1", "Trek FX 2", 34000)
        with pytest.raises(PaymentError, match="couldn't be reached"):
            await stripe.state(PaymentLink("stripe", "cs_test_1", ""))


# --- Visa Pay By Link ---------------------------------------------------------------------------


def check_visa_signature(request: web.Request, body: bytes) -> None:
    """Check the HTTP Signature the way Visa Acceptance describes it."""
    fields = dict(part.strip().split("=", 1) for part in request.headers["Signature"].split(", "))
    fields = {name: value.strip('"') for name, value in fields.items()}
    assert fields["keyid"] == "key-1" and fields["algorithm"] == "HmacSHA256"
    values = {
        "host": request.headers["Host"],
        "date": request.headers["Date"],
        "request-target": f"{request.method.lower()} {request.path}",
        "digest": request.headers.get("Digest", ""),
        "v-c-merchant-id": request.headers["v-c-merchant-id"],
    }
    if body:
        expected_digest = base64.b64encode(hashlib.sha256(body).digest()).decode()
        assert values["digest"] == f"SHA-256={expected_digest}"
    text = "\n".join(f"{name}: {values[name]}" for name in fields["headers"].split())
    expected = hmac.new(base64.b64decode(SECRET), text.encode(), hashlib.sha256).digest()
    assert fields["signature"] == base64.b64encode(expected).decode()


async def test_visa_pay_by_link() -> None:
    created: list[dict[str, Any]] = []
    paid: set[str] = set()

    async def make_link(request: web.Request) -> web.Response:
        body = await request.read()
        check_visa_signature(request, body)
        created.append(json.loads(body))
        return web.json_response(
            {"id": "link-1", "purchaseInformation": {"paymentLink": "https://pay.visa.test/1"}},
            status=201,
        )

    async def search(request: web.Request) -> web.Response:
        body = await request.read()
        check_visa_signature(request, body)
        code = json.loads(body)["query"].split(":", 1)[1]
        summaries = []
        if code in paid:
            summaries = [{"applicationInformation": {"applications": [{"rFlag": "SOK"}]}}]
        return web.json_response({"_embedded": {"transactionSummaries": summaries}})

    app = web.Application()
    app.router.add_post("/ipl/v2/payment-links", make_link)
    app.router.add_post("/tss/v2/searches", search)
    async with local_server(app) as base:
        visa = VisaPayByLink("merchant-1", "key-1", SECRET, base_url=base)
        link = await visa.create("deal-9", "Trek FX 2", 34050)
        assert link == PaymentLink("visa", "deal-9", "https://pay.visa.test/1")
        order = created[0]["orderInformation"]
        assert order["amountDetails"] == {"currency": "USD", "totalAmount": "340.50"}
        assert created[0]["purchaseInformation"]["purchaseNumber"] == "deal-9"
        assert await visa.state(link) == "pending"
        paid.add("deal-9")
        assert await visa.state(link) == "paid"
        wrong = VisaPayByLink("merchant-1", "key-1", base64.b64encode(b"x" * 32).decode(), base)
        with pytest.raises(PaymentError, match="Visa returned 500"):
            await wrong.create("deal-10", "Lamp", 1000)  # the stand-in rejects the signature


# --- Checkout and orders ------------------------------------------------------------------------


def sold_lamp(store: Store) -> str:
    store.add_listing(
        Listing(
            item_id="lamp",
            owner="o",
            seller_id="s",
            listing_ref="r",
            status=PROPOSED,
            title="Lamp",
            asking_cents=4000,
            floor_cents=3000,
        )
    )
    store.activate("lamp", "o", "agent1qlamp")
    deal = store.close_deal("lamp", "chat:abc", 3500)
    assert deal is not None
    return deal.deal_id


async def test_checkout_makes_one_link_and_records_the_payment(store: Store) -> None:
    provider = fakes.FakePayments()
    calls = 0
    create = provider.create

    async def counted(deal_id: str, title: str, price_cents: int) -> PaymentLink:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.01)
        return await create(deal_id, title, price_cents)

    provider.create = counted  # type: ignore[method-assign]
    checkout = Checkout(store, provider)
    deal_id = sold_lamp(store)
    first, second = await asyncio.gather(checkout.start(deal_id), checkout.start(deal_id))
    assert calls == 1
    assert first is not None and first.payment_status == PENDING
    assert second is not None and second.payment_url == first.payment_url
    assert (await checkout.refresh(deal_id)).payment_status == PENDING  # type: ignore[union-attr]
    provider.pay(deal_id)
    await checkout.refresh_all()
    paid = store.deal(deal_id)
    assert paid is not None and paid.payment_status == PAID
    assert (paid.commission_cents, paid.payout_cents) == (175, 3325)


async def test_an_unpaid_link_expires(store: Store) -> None:
    provider = fakes.FakePayments()
    checkout = Checkout(store, provider)
    deal_id = sold_lamp(store)
    await checkout.start(deal_id)
    provider.expired.add(provider.links[deal_id].ref)
    expired = await checkout.refresh(deal_id)
    assert expired is not None and expired.payment_status == EXPIRED


async def test_without_a_provider_deals_stay_uncollected(store: Store) -> None:
    checkout = Checkout(store, None)
    deal_id = sold_lamp(store)
    deal = await checkout.start(deal_id)
    assert deal is not None and deal.payment_status == NOT_COLLECTED and not checkout.enabled


async def test_a_failed_link_leaves_the_deal_to_retry(store: Store) -> None:
    provider = fakes.FakePayments()
    provider.fail = True
    checkout = Checkout(store, provider)
    deal_id = sold_lamp(store)
    assert (await checkout.start(deal_id)).payment_status == NOT_COLLECTED  # type: ignore[union-attr]
    provider.fail = False
    assert (await checkout.start(deal_id)).payment_status == PENDING  # type: ignore[union-attr]


async def test_orders_are_sent_signed_and_without_the_buyer(
    store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    received: list[tuple[str, bytes]] = []

    async def hook(request: web.Request) -> web.Response:
        received.append((request.headers.get("X-Yardsale-Signature", ""), await request.read()))
        return web.Response(status=204)

    app = web.Application()
    app.router.add_post("/orders", hook)
    async with local_server(app) as base:
        monkeypatch.setenv("YARDSALE_ORDERS_WEBHOOK", f"{base}/orders")
        monkeypatch.setenv("YARDSALE_ORDERS_SECRET", "hook-secret")
        provider = fakes.FakePayments()
        checkout = Checkout(store, provider)
        deal_id = sold_lamp(store)
        await checkout.start(deal_id)
        provider.pay(deal_id)
        await checkout.refresh(deal_id)
    statuses = []
    for signature, body in received:
        assert signature == orders.sign(body, "hook-secret")
        order = json.loads(body)
        assert "abc" not in body.decode() and order["buyer_kind"] == "chat"
        statuses.append((order["status"], order["commission_cents"]))
    assert statuses == [("confirmed", None), ("completed", 175)]


async def test_a_broken_orders_webhook_never_stops_a_sale(
    store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def broken(request: web.Request) -> web.Response:
        return web.Response(status=500)

    app = web.Application()
    app.router.add_post("/orders", broken)
    async with local_server(app) as base:
        monkeypatch.setenv("YARDSALE_ORDERS_WEBHOOK", f"{base}/orders")
        deal_id = sold_lamp(store)
        deal = store.deal(deal_id)
        assert deal is not None
        assert await orders.publish(deal, store.get("lamp")) is False
        checkout = Checkout(store, fakes.FakePayments())
        assert (await checkout.start(deal_id)).payment_status == PENDING  # type: ignore[union-attr]
