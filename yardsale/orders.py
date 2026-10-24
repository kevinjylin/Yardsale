"""Orders: each deal as an order with a status, sent to a webhook for an outside order list.

The plan is to show Yardsale's sales on @yardsale's Orders page on Fetch Business. How an outside
agent adds orders there isn't known yet, so every status change is POSTed as JSON to
YARDSALE_ORDERS_WEBHOOK, when it's set. A deal's payment state gives its order status:

    not_collected -> pending      price agreed, no payment link yet
    pending       -> confirmed    the buyer committed and got a payment link
    paid          -> completed    paid; Yardsale keeps its commission and owes the seller the rest
    expired, rejected -> canceled the buyer didn't pay

With YARDSALE_ORDERS_SECRET set, each POST is signed: the X-Yardsale-Signature header is
"sha256=" and the hex HMAC-SHA256 of the body. Buyers' identities are never sent.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
from datetime import UTC, datetime
from typing import Any

import aiohttp

from yardsale.store import DealRecord, Listing

ORDER_STATUS = {
    "not_collected": "pending",
    "pending": "confirmed",
    "paid": "completed",
    "expired": "canceled",
    "rejected": "canceled",
}
TIMEOUT = aiohttp.ClientTimeout(total=10)

log = logging.getLogger("yardsale.orders")


def order_status(deal: DealRecord) -> str:
    return ORDER_STATUS.get(deal.payment_status, "pending")


def order(deal: DealRecord, listing: Listing | None) -> dict[str, Any]:
    """The order as the webhook sends it."""
    return {
        "order_id": deal.deal_id,
        "status": order_status(deal),
        "payment_status": deal.payment_status,
        "item_id": deal.item_id,
        "item": listing.title if listing else "",
        "item_agent": listing.address if listing else None,
        "price_cents": deal.price_cents,
        "currency": "USD",
        "commission_cents": deal.commission_cents,
        "buyer_kind": deal.buyer.split(":", 1)[0],  # chat or agent; never who
        "payment_provider": deal.payment_provider,
        "created_at": datetime.fromtimestamp(deal.created_at, UTC).isoformat(timespec="seconds"),
    }


def sign(body: bytes, secret: str) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


async def publish(deal: DealRecord, listing: Listing | None) -> bool:
    """Send the order's current status to the webhook. False if none is set or it failed;
    failures are logged and never stop a sale."""
    url = os.getenv("YARDSALE_ORDERS_WEBHOOK", "").strip()
    if not url:
        return False
    body = json.dumps(order(deal, listing)).encode()
    headers = {"Content-Type": "application/json"}
    secret = os.getenv("YARDSALE_ORDERS_SECRET", "").strip()
    if secret:
        headers["X-Yardsale-Signature"] = sign(body, secret)
    try:
        async with (
            aiohttp.ClientSession(timeout=TIMEOUT) as session,
            session.post(url, data=body, headers=headers) as response,
        ):
            if response.status >= 400:
                log.warning(f"Orders webhook returned {response.status} for {deal.deal_id}")
                return False
    except (aiohttp.ClientError, TimeoutError) as ex:
        log.warning(f"Orders webhook failed for {deal.deal_id}: {type(ex).__name__}")
        return False
    return True
