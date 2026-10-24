"""Payment links for closed deals, and Yardsale's commission. Test mode only.

When a buyer and an item agent agree, the Market's checkout (checkout.py) makes one payment link
for the deal and checks whether it was paid. Two providers:

- Visa Pay By Link (Visa Acceptance), in its sandbox: VISA_MERCHANT_ID, VISA_KEY_ID, and
  VISA_SHARED_SECRET from the Visa Acceptance Business Center. Links are made with the Pay By
  Link API. Visa documents a webhook for "paid"; without a public server, payment is checked by
  searching transactions for the link's purchase number. Not tried against Visa yet.
- Stripe Checkout in test mode: STRIPE_SECRET_KEY, which must be a test key (sk_test_...).

YARDSALE_PAYMENTS picks one ("visa" or "stripe"); unset, Visa is used when its keys are set,
then Stripe. With neither, deals close without a payment link and stay "not_collected".

Yardsale is the merchant: the buyer pays Yardsale, which keeps YARDSALE_COMMISSION_PERCENT
(default 5%) and owes the seller the rest. Payouts to sellers aren't automated.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from email.utils import formatdate
from typing import Any, Literal, Protocol
from urllib.parse import urlparse

import aiohttp

PaymentState = Literal["pending", "paid", "expired"]
DEFAULT_COMMISSION_PERCENT = Decimal(5)
RETURN_URL = "https://asi1.ai"  # where the buyer lands after paying: back to the chat
TIMEOUT = aiohttp.ClientTimeout(total=20)


class PaymentError(RuntimeError):
    """The provider refused or couldn't be reached. The message is safe to show a buyer."""


class PaymentConfigError(ValueError):
    pass


@dataclass(frozen=True)
class PaymentLink:
    provider: str
    ref: str  # the provider's ID for the link or checkout
    url: str  # where the buyer pays


class PaymentProvider(Protocol):
    name: str
    label: str  # how the chat names it, e.g. "Visa Pay By Link"

    async def create(self, deal_id: str, title: str, price_cents: int) -> PaymentLink: ...

    async def state(self, link: PaymentLink) -> PaymentState: ...


def commission_percent() -> Decimal:
    raw = os.getenv("YARDSALE_COMMISSION_PERCENT", "").strip()
    try:
        percent = Decimal(raw) if raw else DEFAULT_COMMISSION_PERCENT
    except ArithmeticError as ex:
        raise PaymentConfigError("YARDSALE_COMMISSION_PERCENT must be a number") from ex
    if not 0 <= percent < 100:
        raise PaymentConfigError("YARDSALE_COMMISSION_PERCENT must be from 0 to 99")
    return percent


def commission_text() -> str:
    """The commission as people write it: "5", "7.5", "10"."""
    return format(commission_percent().normalize(), "f")


def split(price_cents: int, percent: Decimal | None = None) -> tuple[int, int]:
    """(Yardsale's commission, the seller's payout) for a sale. Half cents round up."""
    share = commission_percent() if percent is None else percent
    fee = int((Decimal(price_cents) * share / 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))
    return fee, price_cents - fee


def _dollars(cents: int) -> str:
    return f"{cents // 100}.{cents % 100:02d}"


async def _json(response: aiohttp.ClientResponse, provider: str) -> dict[str, Any]:
    try:
        data = await response.json(content_type=None)
    except (ValueError, aiohttp.ContentTypeError):
        data = {}
    if response.status >= 400 or not isinstance(data, dict):
        # The provider's message can name fields but never includes our keys.
        detail = ""
        if isinstance(data, dict):
            error = data.get("error") or data
            if isinstance(error, dict):
                detail = str(error.get("message") or error.get("reason") or "")[:200]
        raise PaymentError(
            f"{provider} returned {response.status}{': ' + detail if detail else ''}"
        )
    return data


# --- Stripe Checkout (test mode) ---------------------------------------------------------------


class StripeCheckout:
    name = "stripe"
    label = "Stripe (test mode)"

    def __init__(
        self, secret_key: str, base_url: str = "https://api.stripe.com", return_url: str = ""
    ) -> None:
        if not secret_key.startswith(("sk_test_", "rk_test_")):
            raise PaymentConfigError("STRIPE_SECRET_KEY must be a test key (sk_test_...)")
        self._key = secret_key
        self.base_url = base_url.rstrip("/")
        self.return_url = return_url or RETURN_URL

    def _headers(self, idempotency_key: str = "") -> dict[str, str]:
        headers = {"Authorization": f"Bearer {self._key}"}
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        return headers

    async def create(self, deal_id: str, title: str, price_cents: int) -> PaymentLink:
        form = {
            "mode": "payment",
            "line_items[0][quantity]": "1",
            "line_items[0][price_data][currency]": "usd",
            "line_items[0][price_data][unit_amount]": str(price_cents),
            "line_items[0][price_data][product_data][name]": title[:200] or "Yardsale item",
            "client_reference_id": deal_id,
            "metadata[deal_id]": deal_id,
            "success_url": self.return_url,
            "cancel_url": self.return_url,
        }
        try:
            async with (
                aiohttp.ClientSession(timeout=TIMEOUT) as session,
                session.post(
                    f"{self.base_url}/v1/checkout/sessions",
                    data=form,
                    headers=self._headers(f"yardsale-{deal_id}"),
                ) as response,
            ):
                data = await _json(response, "Stripe")
        except aiohttp.ClientError as ex:
            raise PaymentError("Stripe couldn't be reached") from ex
        if not data.get("id") or not data.get("url"):
            raise PaymentError("Stripe didn't return a checkout link")
        return PaymentLink(self.name, str(data["id"]), str(data["url"]))

    async def state(self, link: PaymentLink) -> PaymentState:
        try:
            async with (
                aiohttp.ClientSession(timeout=TIMEOUT) as session,
                session.get(
                    f"{self.base_url}/v1/checkout/sessions/{link.ref}", headers=self._headers()
                ) as response,
            ):
                data = await _json(response, "Stripe")
        except aiohttp.ClientError as ex:
            raise PaymentError("Stripe couldn't be reached") from ex
        if data.get("payment_status") == "paid":
            return "paid"
        if data.get("status") == "expired":
            return "expired"
        return "pending"


# --- Visa Pay By Link (Visa Acceptance sandbox) ------------------------------------------------


class VisaPayByLink:
    """Visa Acceptance's Pay By Link REST API, signed with an HTTP Signature (HMAC-SHA256)."""

    name = "visa"
    label = "Visa Pay By Link (sandbox)"

    def __init__(
        self,
        merchant_id: str,
        key_id: str,
        shared_secret: str,
        base_url: str = "https://apitest.visaacceptance.com",
    ) -> None:
        if not (merchant_id and key_id and shared_secret):
            raise PaymentConfigError("Visa needs VISA_MERCHANT_ID, VISA_KEY_ID, VISA_SHARED_SECRET")
        try:
            self._secret = base64.b64decode(shared_secret, validate=True)
        except ValueError as ex:
            raise PaymentConfigError(
                "VISA_SHARED_SECRET must be the base64 key Visa gave you"
            ) from ex
        self.merchant_id = merchant_id
        self.key_id = key_id
        self.base_url = base_url.rstrip("/")
        self.host = urlparse(self.base_url).netloc
        hostname = urlparse(self.base_url).hostname or ""
        if not (hostname.startswith("apitest.") or hostname in ("127.0.0.1", "localhost")):
            raise PaymentConfigError("Visa payments run against the sandbox (apitest.) only")

    def signed_headers(self, method: str, path: str, body: bytes | None) -> dict[str, str]:
        """Visa Acceptance's HTTP Signature headers for one request."""
        headers = {
            "host": self.host,
            "date": formatdate(usegmt=True),
            "v-c-merchant-id": self.merchant_id,
        }
        signed = ["host", "date", "request-target"]
        lines = [
            f"host: {headers['host']}",
            f"date: {headers['date']}",
            f"request-target: {method.lower()} {path}",
        ]
        if body is not None:
            digest = base64.b64encode(hashlib.sha256(body).digest()).decode()
            headers["digest"] = f"SHA-256={digest}"
            signed.append("digest")
            lines.append(f"digest: {headers['digest']}")
        signed.append("v-c-merchant-id")
        lines.append(f"v-c-merchant-id: {self.merchant_id}")
        signature = base64.b64encode(
            hmac.new(self._secret, "\n".join(lines).encode(), hashlib.sha256).digest()
        ).decode()
        headers["signature"] = (
            f'keyid="{self.key_id}", algorithm="HmacSHA256", '
            f'headers="{" ".join(signed)}", signature="{signature}"'
        )
        if body is not None:
            headers["content-type"] = "application/json"
        return headers

    async def _request(
        self, method: str, path: str, payload: dict[str, Any] | None
    ) -> dict[str, Any]:
        body = json.dumps(payload).encode() if payload is not None else None
        headers = self.signed_headers(method, path, body)
        headers.pop("host")  # aiohttp sets it from the URL
        try:
            async with (
                aiohttp.ClientSession(timeout=TIMEOUT) as session,
                session.request(
                    method, f"{self.base_url}{path}", data=body, headers=headers
                ) as response,
            ):
                return await _json(response, "Visa")
        except aiohttp.ClientError as ex:
            raise PaymentError("Visa couldn't be reached") from ex

    async def create(self, deal_id: str, title: str, price_cents: int) -> PaymentLink:
        amount = _dollars(price_cents)
        data = await self._request(
            "POST",
            "/ipl/v2/payment-links",
            {
                "processingInformation": {
                    "linkType": "PURCHASE",
                    "requestPhone": False,
                    "requestShipping": False,
                },
                "purchaseInformation": {"purchaseNumber": deal_id},
                "orderInformation": {
                    "amountDetails": {"currency": "USD", "totalAmount": amount},
                    "lineItems": [
                        {
                            "productName": title[:100] or "Yardsale item",
                            "quantity": 1,
                            "unitPrice": amount,
                        }
                    ],
                },
            },
        )
        url = (data.get("purchaseInformation") or {}).get("paymentLink")
        if not url or not data.get("id"):
            raise PaymentError("Visa didn't return a payment link")
        # The purchase number (the deal ID) is what a payment through the link is filed under.
        return PaymentLink(self.name, deal_id, str(url))

    async def state(self, link: PaymentLink) -> PaymentState:
        data = await self._request(
            "POST",
            "/tss/v2/searches",
            {
                "save": False,
                "name": "yardsale-payment-check",
                "timezone": "UTC",
                "query": f"clientReferenceInformation.code:{link.ref}",
                "offset": 0,
                "limit": 5,
                "sort": "id:asc,submitTimeUtc:asc",
            },
        )
        found = (data.get("_embedded") or {}).get("transactionSummaries") or []
        for summary in found:
            steps = (summary.get("applicationInformation") or {}).get("applications") or []
            if any(step.get("rFlag") == "SOK" or str(step.get("rCode")) == "1" for step in steps):
                return "paid"
        return "pending"


def provider_from_env() -> PaymentProvider | None:
    """The configured provider, or None when no payment keys are set. Raises
    PaymentConfigError for keys that can't work, such as a live Stripe key."""
    choice = os.getenv("YARDSALE_PAYMENTS", "").strip().lower()
    visa_keys = [
        os.getenv(k, "").strip() for k in ("VISA_MERCHANT_ID", "VISA_KEY_ID", "VISA_SHARED_SECRET")
    ]
    stripe_key = os.getenv("STRIPE_SECRET_KEY", "").strip()
    return_url = os.getenv("YARDSALE_PAYMENT_RETURN_URL", "").strip()
    if choice not in ("", "visa", "stripe", "none"):
        raise PaymentConfigError("YARDSALE_PAYMENTS must be visa, stripe, or none")
    if choice == "none":
        return None
    if choice == "visa" or (not choice and all(visa_keys)):
        base = os.getenv("VISA_API_BASE", "").strip() or "https://apitest.visaacceptance.com"
        merchant_id, key_id, shared_secret = visa_keys
        return VisaPayByLink(merchant_id, key_id, shared_secret, base_url=base)
    if choice == "stripe" or (not choice and stripe_key):
        return StripeCheckout(stripe_key, return_url=return_url)
    return None
