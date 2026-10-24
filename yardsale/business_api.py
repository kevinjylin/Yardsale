"""Yardsale's HTTP API for @yardsale, its Fetch Business agent.

Fetch Business's "OpenAPI Integration" (a Business Tool) lets @yardsale call an outside API from
its own chat. With this API, @yardsale can find, haggle for, and sell a listing itself, instead of
telling buyers to message the Market:

    GET  /listings                search what's for sale (asking prices only)
    POST /haggles                 the buyer agent haggles with every match at once, under a max
    GET  /haggles/{id}            a haggle that was still running when POST answered
    POST /haggles/{id}/accept     buy at the haggled price; returns the payment link
    GET  /deals/{id}              whether a deal is paid
    GET  /openapi.json            this API's description (no key needed)

Every call except /openapi.json needs the X-Yardsale-Key header (or "Authorization: Bearer") to
match YARDSALE_API_KEY. Without that key the API doesn't start. It listens on
127.0.0.1:YARDSALE_API_PORT (8110 by default); a tunnel such as cloudflared makes it public, and
YARDSALE_API_URL is the public address the description gives.

A haggle's ID is its buyer: only a caller holding the ID can see its prices or accept them, the
way a chat conversation owns its haggles. The buyer's max and the sellers' floors stay out of
the logs, and floors never leave the item agents.
"""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import logging
import os
import secrets
import time
from collections.abc import Callable
from typing import Any

from aiohttp import web
from uagents import Context

from yardsale.chat import LOWEST_SHARE_REACH, STATUS_TEXT, Backend, haggle_text
from yardsale.common import PipelineError
from yardsale.hosting import profile_url
from yardsale.models import AcceptCounter, Counter, Deal, HaggleResult, OfferRejected
from yardsale.payments import PaymentError
from yardsale.pricing import PriceError, parse_usd, to_cents, usd, valid_cents
from yardsale.search import search
from yardsale.store import AGENTVERSE, PENDING, Listing

DEFAULT_PORT = 8110
KEY_HEADER = "X-Yardsale-Key"
ANSWER_WITHIN = 25.0  # seconds POST /haggles waits before answering "still haggling"
QUOTES_LAST = 24 * 3600  # seconds a haggled price can be accepted, like the item agents' counters
BUYER_PREFIX = "api-"  # buyer_ref of an API buyer; its deals are "chat:api-..."
# Short and lowercase, without look-alikes (0/o, 1/l/i), so a model passing it along copies it
# right. Every call needs the API key, so the ID doesn't have to be hard to guess.
ID_LETTERS = "abcdefghjkmnpqrstuvwxyz23456789"
ID_LENGTH = 8
NO_SUCH_HAGGLE = (
    "No haggle has that haggle_id. Copy haggle_id exactly as startHaggle returned it, or start a "
    "new haggle with startHaggle."
)

log = logging.getLogger("yardsale.api")


def new_haggle_id() -> str:
    return "".join(secrets.choice(ID_LETTERS) for _ in range(ID_LENGTH))


class ApiConfigError(ValueError):
    pass


def api_key() -> str | None:
    key = os.getenv("YARDSALE_API_KEY", "").strip()
    if key and len(key) < 24:
        raise ApiConfigError("YARDSALE_API_KEY must be at least 24 characters")
    return key or None


def api_port() -> int:
    raw = os.getenv("YARDSALE_API_PORT", "").strip()
    try:
        return int(raw) if raw else DEFAULT_PORT
    except ValueError:
        raise ApiConfigError("YARDSALE_API_PORT must be a port number") from None


def dollars(cents: int) -> float:
    return cents / 100


def _error(status: int, message: str, code: str) -> web.Response:
    return web.json_response({"error": message, "code": code}, status=status)


def _listing_json(listing: Listing) -> dict[str, Any]:
    """What a buyer may see: never the floor, the seller, or the appraisal."""
    data: dict[str, Any] = {
        "item_id": listing.item_id,
        "title": listing.title,
        "asking_usd": dollars(listing.asking_cents),
        "asking_price": usd(listing.asking_cents),
        "agent_address": listing.address,
    }
    if listing.item is not None:
        data.update(
            brand=listing.item.brand,
            model=listing.item.model,
            category=listing.item.category,
            condition=listing.item.condition,
            details=listing.item.details,
        )
    if listing.host == AGENTVERSE and listing.address:
        data["agent_profile_url"] = profile_url(listing.address)
        if listing.handle:
            data["agent_handle"] = f"@{listing.handle}"
    return data


def _amount(value: Any) -> int | None:
    """A max price from JSON: a number of dollars, or text like "$320"."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        cents = to_cents(float(value))
    elif isinstance(value, str):
        try:
            cents = parse_usd(value)
        except PriceError:
            return None
    else:
        return None
    return cents if cents is not None and valid_cents(cents) else None


class BusinessApi:
    """The API's handlers, over the Market's chat backend."""

    def __init__(
        self,
        backend: Backend,
        context: Callable[[], Context],
        key: str,
        public_url: str | None = None,
        answer_within: float = ANSWER_WITHIN,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.backend = backend
        self.store = backend.store
        self.context = context  # the Market's, for messages to the buyer and item agents
        self.key = key
        self.public_url = public_url.rstrip("/") if public_url else None
        self.answer_within = answer_within
        self.clock = clock
        self.running: dict[str, asyncio.Task[HaggleResult]] = {}

    # --- Plumbing ---------------------------------------------------------------------------

    def app(self) -> web.Application:
        app = web.Application(middlewares=[self._log, self._auth])
        app.router.add_get("/openapi.json", self.openapi)
        app.router.add_get("/listings", self.listings)
        app.router.add_post("/haggles", self.start_haggle)
        app.router.add_get("/haggles/{haggle_id}", self.get_haggle)
        app.router.add_post("/haggles/{haggle_id}/accept", self.accept)
        app.router.add_get("/deals/{deal_id}", self.get_deal)
        return app

    @web.middleware
    async def _log(self, request: web.Request, handler: Any) -> web.StreamResponse:
        """One line per call: the method, the route pattern (no IDs), the status, and the time."""
        started = time.monotonic()
        status = 500
        try:
            response = await handler(request)
            status = response.status
            return response
        except web.HTTPException as error:
            status = error.status
            raise
        finally:
            resource = request.match_info.route.resource
            route = resource.canonical if resource is not None else "(no route)"
            took = time.monotonic() - started
            log.info(f"API {request.method} {route} -> {status} in {took:.1f}s")

    @web.middleware
    async def _auth(self, request: web.Request, handler: Any) -> web.StreamResponse:
        if request.path == "/openapi.json":
            return await handler(request)
        given = request.headers.get(KEY_HEADER, "")
        bearer = request.headers.get("Authorization", "")
        if not given and bearer.lower().startswith("bearer "):
            given = bearer[7:]
        if not hmac.compare_digest(given.strip().encode(), self.key.encode()):
            return _error(401, f"Send the API key in the {KEY_HEADER} header.", "unauthorized")
        return await handler(request)

    def _state_key(self, haggle_id: str) -> str:
        return f"api:{haggle_id}"

    def _haggle_id(self, request: web.Request) -> str:
        """The path's haggle ID, forgiving the capitals or spaces a model may add to it."""
        given = request.match_info["haggle_id"]
        cleaned = given.strip().lower()
        if cleaned == given or self._load(given):  # older IDs are mixed case
            return given
        return cleaned

    def _load(self, haggle_id: str) -> dict[str, Any]:
        if not haggle_id.isascii() or len(haggle_id) > 64:
            return {}
        return self.store.load_conversation(self._state_key(haggle_id))

    def _save(self, haggle_id: str, state: dict[str, Any]) -> None:
        self.store.save_conversation(self._state_key(haggle_id), state)

    # --- The description --------------------------------------------------------------------

    async def openapi(self, request: web.Request) -> web.Response:
        base = self.public_url
        if base is None:
            proto = request.headers.get("X-Forwarded-Proto", request.scheme)
            host = request.headers.get("X-Forwarded-Host", request.host)
            base = f"{proto}://{host}"
        return web.json_response(openapi_spec(base))

    # --- Finding ----------------------------------------------------------------------------

    async def listings(self, request: web.Request) -> web.Response:
        query = request.query.get("q", "")[:200]
        budget = None
        if "max_usd" in request.query:
            budget = _amount(request.query["max_usd"])
            if budget is None:
                return _error(400, "max_usd must be a dollar amount, like 300.", "bad_amount")
        found = search(self.backend.available(), query, budget)
        return web.json_response(
            {
                "count": len(found),
                "listings": [_listing_json(listing) for listing in found],
                "next_step": (
                    "To get a lower price, call startHaggle with what the buyer wants and the most "
                    "they'll pay."
                    if found
                    else "Nothing matches right now. Try other words or a higher budget."
                ),
            }
        )

    # --- Haggling ---------------------------------------------------------------------------

    def _targets(self, query: str, max_cents: int) -> list[Listing]:
        reach = max_cents * LOWEST_SHARE_REACH[0] // LOWEST_SHARE_REACH[1]
        return [
            listing
            for listing in search(self.backend.available(), query, reach)
            if listing.asking_cents <= reach
        ]

    async def start_haggle(self, request: web.Request) -> web.Response:
        try:
            body = await request.json()
        except ValueError:
            return _error(400, "Send a JSON body.", "bad_request")
        if not isinstance(body, dict):
            return _error(400, "Send a JSON object.", "bad_request")
        query = str(body.get("looking_for", ""))[:200].strip()
        max_cents = _amount(body.get("max_usd"))
        if not query:
            return _error(400, "Say what the buyer is looking for in looking_for.", "bad_request")
        if max_cents is None:
            return _error(
                400, "max_usd must be the most the buyer will pay, like 300.", "bad_amount"
            )
        targets = self._targets(query, max_cents)
        if not targets:
            return web.json_response(
                {
                    "haggle_id": None,
                    "status": "nothing_to_haggle",
                    "items": [],
                    "summary": (
                        f'Nothing for sale for "{query}" is close enough to {usd(max_cents)} to '
                        "haggle over."
                    ),
                    "next_step": "Try other words or a higher max.",
                }
            )
        haggle_id = new_haggle_id()
        while self._load(haggle_id):
            haggle_id = new_haggle_id()
        buyer_ref = BUYER_PREFIX + haggle_id
        self._save(haggle_id, {"buyer_ref": buyer_ref, "status": "running"})
        task = asyncio.create_task(
            self.backend.haggle(self.context(), buyer_ref, max_cents, targets)
        )
        self.running[haggle_id] = task
        task.add_done_callback(lambda t: self._finish(haggle_id, buyer_ref, t))
        log.info(f"API haggle {haggle_id[:3]} started with {len(targets)} item(s)")
        try:
            await asyncio.wait_for(asyncio.shield(task), self.answer_within)
        except TimeoutError:
            return web.json_response(
                {
                    "haggle_id": haggle_id,
                    "status": "running",
                    "summary": f"Yardsale's buyer agent is haggling with {len(targets)} seller "
                    "agent(s) at once.",
                    "next_step": "Call getHaggle with this haggle_id. It waits for the result.",
                },
            )
        except Exception:  # _finish saved the failure for the caller
            pass
        return self._haggle_response(haggle_id)

    def _finish(self, haggle_id: str, buyer_ref: str, task: asyncio.Task[HaggleResult]) -> None:
        self.running.pop(haggle_id, None)
        state: dict[str, Any] = {"buyer_ref": buyer_ref}
        if task.cancelled():
            state.update(status="failed", error="The haggle was stopped.")
        elif (error := task.exception()) is not None:
            log.warning(f"API haggle {haggle_id[:3]} failed: {error}")
            reason = str(error) if isinstance(error, PipelineError) else "Something went wrong."
            state.update(status="failed", error=reason)
        else:
            result = task.result()
            quotes = {o.item_id: o.price_cents for o in result.outcomes if o.price_cents}
            state.update(
                status="done",
                result=result.model_dump(),
                quotes=quotes,
                best=result.best_item_id if result.best_item_id in quotes else "",
                expires_at=self.clock() + QUOTES_LAST,
            )
        self._save(haggle_id, state)

    def _haggle_response(self, haggle_id: str) -> web.Response:
        state = self._load(haggle_id)
        if not state:
            return _error(404, NO_SUCH_HAGGLE, "not_found")
        if state.get("status") == "running":
            if haggle_id not in self.running:
                return _error(410, "That haggle was interrupted. Start a new one.", "interrupted")
            return web.json_response(
                {
                    "haggle_id": haggle_id,
                    "status": "running",
                    "next_step": "Call getHaggle again. It waits for the result.",
                },
            )
        if state.get("status") == "failed":
            return _error(502, f"The haggle failed: {state.get('error')}", "haggle_failed")
        result = HaggleResult.model_validate(state["result"])
        quotes: dict[str, int] = state.get("quotes", {})
        items = [
            {
                "item_id": o.item_id,
                "title": o.title,
                "asking_usd": dollars(o.asking_cents),
                "status": o.status,
                "status_text": STATUS_TEXT.get(o.status, o.status),
                "price_usd": dollars(o.price_cents) if o.price_cents else None,
                "rounds": [
                    {
                        "buyer_offer_usd": dollars(r.offer_cents),
                        "seller_reply_usd": dollars(r.reply_cents),
                        "agreed": r.agreed,
                    }
                    for r in o.rounds
                ],
            }
            for o in result.outcomes
        ]
        best = state.get("best") or ""
        if best:
            next_step = (
                "To buy the best deal, call acceptDeal with this haggle_id. To buy another item "
                "with a price, also pass its item_id. Prices hold for 24 hours."
            )
        else:
            next_step = "No seller came down to the max. Try a higher max or other words."
        return web.json_response(
            {
                "haggle_id": haggle_id,
                "status": "done",
                "items": items,
                "best_item_id": best or None,
                "best_price_usd": dollars(quotes[best]) if best else None,
                "summary": haggle_text(result).rsplit("\n", 1)[0] if best else haggle_text(result),
                "next_step": next_step,
            }
        )

    async def get_haggle(self, request: web.Request) -> web.Response:
        """Waits up to answer_within seconds for a haggle that's still running."""
        haggle_id = self._haggle_id(request)
        task = self.running.get(haggle_id)
        if task is not None:
            # A timeout leaves it running; a failure was saved by _finish.
            with contextlib.suppress(Exception):
                await asyncio.wait_for(asyncio.shield(task), self.answer_within)
        return self._haggle_response(haggle_id)

    # --- Buying -----------------------------------------------------------------------------

    async def accept(self, request: web.Request) -> web.Response:
        haggle_id = self._haggle_id(request)
        state = self._load(haggle_id)
        if not state:
            return _error(404, NO_SUCH_HAGGLE, "not_found")
        if state.get("status") != "done":
            return self._haggle_response(haggle_id)
        try:
            body = await request.json() if request.can_read_body else {}
        except ValueError:
            body = {}
        item_id = str(body.get("item_id") or "") if isinstance(body, dict) else ""
        quotes: dict[str, int] = state.get("quotes", {})
        item_id = item_id or str(state.get("best") or "")
        if item_id not in quotes:
            return _error(
                409, "That item has no price on offer from this haggle.", "no_price_on_offer"
            )
        if self.clock() > float(state.get("expires_at", 0)):
            return _error(409, "These prices expired. Start a new haggle.", "expired")
        try:
            reply = await self.backend.negotiate(
                self.context(),
                item_id,
                AcceptCounter(
                    item_id=item_id, counter_cents=quotes[item_id], buyer_ref=state["buyer_ref"]
                ),
            )
        except PipelineError as ex:
            return _error(502, str(ex), "item_agent_unreachable")
        if isinstance(reply, OfferRejected):
            if reply.code in ("unavailable", "unknown_item"):
                return _error(409, "Sorry, that item is no longer for sale.", "unavailable")
            return _error(409, f"The seller's agent turned that down: {reply.reason}.", reply.code)
        if isinstance(reply, Counter):  # an item agent never answers an acceptance this way
            return _error(409, "The seller's agent asked for a different price.", "countered")
        quotes.pop(item_id, None)
        state.update(quotes=quotes, best="", deal_id=reply.deal_id)
        self._save(haggle_id, state)
        log.info(f"API haggle {haggle_id[:3]} bought {item_id} ({reply.deal_id})")
        return await self._deal_response(reply)

    async def _deal_response(self, deal: Deal) -> web.Response:
        record = await self.backend.checkout.start(deal.deal_id) or self.store.deal(deal.deal_id)
        listing = self.store.get(deal.item_id)
        title = listing.title if listing else deal.title
        data: dict[str, Any] = {
            "deal_id": deal.deal_id,
            "item_id": deal.item_id,
            "title": title,
            "price_usd": dollars(deal.price_cents),
            "payment_status": record.payment_status if record else "not_collected",
            "payment_url": record.payment_url if record else None,
        }
        if record and record.payment_url:
            data["next_step"] = (
                f"Give the buyer this payment link to pay {usd(deal.price_cents)}: "
                f"{record.payment_url} . Call getDeal with the deal_id to check that it's paid."
            )
        else:
            data["next_step"] = "The deal is recorded. Payment links are off on this server."
        return web.json_response(data)

    async def get_deal(self, request: web.Request) -> web.Response:
        deal = self.store.deal(request.match_info["deal_id"])
        if deal is None or not deal.buyer.startswith(f"chat:{BUYER_PREFIX}"):
            return _error(404, "No such deal.", "not_found")  # chat buyers' deals stay private
        if deal.payment_status == PENDING:
            try:
                deal = await self.backend.checkout.refresh(deal.deal_id) or deal
            except PaymentError as ex:
                log.warning(f"Couldn't check payment for {deal.deal_id}: {ex}")
        listing = self.store.get(deal.item_id)
        return web.json_response(
            {
                "deal_id": deal.deal_id,
                "item_id": deal.item_id,
                "title": listing.title if listing else "",
                "price_usd": dollars(deal.price_cents),
                "payment_status": deal.payment_status,
                "paid": deal.payment_status == "paid",
                "payment_url": deal.payment_url if deal.payment_status == PENDING else None,
            }
        )


async def serve(api: BusinessApi, port: int) -> web.AppRunner:
    """Start the API on this machine only; a tunnel makes it public."""
    runner = web.AppRunner(api.app(), access_log=None)  # paths carry haggle IDs
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", port).start()
    return runner


# --- The OpenAPI description ------------------------------------------------------------------

_MONEY = {"type": "number", "description": "US dollars"}
_ERROR = {
    "description": "Something went wrong; `error` says what, for the buyer.",
    "content": {
        "application/json": {
            "schema": {
                "type": "object",
                "properties": {"error": {"type": "string"}, "code": {"type": "string"}},
            }
        }
    },
}
_LISTING = {
    "type": "object",
    "properties": {
        "item_id": {"type": "string"},
        "title": {"type": "string"},
        "asking_usd": _MONEY,
        "asking_price": {"type": "string"},
        "brand": {"type": "string"},
        "model": {"type": "string"},
        "category": {"type": "string"},
        "condition": {"type": "string"},
        "details": {"type": "array", "items": {"type": "string"}},
        "agent_address": {"type": "string", "description": "The item's own agent"},
        "agent_profile_url": {"type": "string"},
        "agent_handle": {
            "type": "string",
            "description": "The item agent's Agentverse handle, such as @trek-fx-3-hybrid",
        },
    },
}
_HAGGLE = {
    "type": "object",
    "properties": {
        "haggle_id": {"type": "string", "nullable": True},
        "status": {"type": "string", "enum": ["running", "done", "nothing_to_haggle"]},
        "summary": {"type": "string", "description": "Every round, ready to show the buyer"},
        "next_step": {"type": "string"},
        "best_item_id": {"type": "string", "nullable": True},
        "best_price_usd": {"type": "number", "nullable": True},
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "item_id": {"type": "string"},
                    "title": {"type": "string"},
                    "asking_usd": _MONEY,
                    "status": {"type": "string"},
                    "status_text": {"type": "string"},
                    "price_usd": {"type": "number", "nullable": True},
                    "rounds": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "buyer_offer_usd": _MONEY,
                                "seller_reply_usd": _MONEY,
                                "agreed": {"type": "boolean"},
                            },
                        },
                    },
                },
            },
        },
    },
}
_DEAL = {
    "type": "object",
    "properties": {
        "deal_id": {"type": "string"},
        "item_id": {"type": "string"},
        "title": {"type": "string"},
        "price_usd": _MONEY,
        "payment_status": {
            "type": "string",
            "enum": ["not_collected", "pending", "paid", "expired", "rejected"],
        },
        "payment_url": {"type": "string", "nullable": True},
        "paid": {"type": "boolean"},
        "next_step": {"type": "string"},
    },
}


def _ok(schema: dict[str, Any], what: str) -> dict[str, Any]:
    return {"description": what, "content": {"application/json": {"schema": schema}}}


def _haggle_id_param() -> dict[str, Any]:
    return {
        "name": "haggle_id",
        "in": "path",
        "required": True,
        "schema": {"type": "string"},
        "description": "The haggle_id startHaggle returned, copied exactly (8 characters)",
    }


def openapi_spec(base_url: str) -> dict[str, Any]:
    return {
        "openapi": "3.0.3",
        "info": {
            "title": "Yardsale",
            "version": "1.0.0",
            "description": (
                "Yardsale sells used, one-of-a-kind items. Each item has its own AI agent that "
                "holds the seller's private lowest price. Yardsale's buyer agent haggles with "
                "every matching item's agent at once without revealing the buyer's max. Use it "
                "to find used items, haggle for the buyer, and buy with a payment link. Prices "
                "are in US dollars."
            ),
        },
        "servers": [{"url": base_url}],
        "components": {
            "securitySchemes": {"apiKey": {"type": "apiKey", "in": "header", "name": KEY_HEADER}}
        },
        "security": [{"apiKey": []}],
        "paths": {
            "/listings": {
                "get": {
                    "operationId": "searchListings",
                    "summary": "Find used items for sale",
                    "description": (
                        "Search Yardsale's listings by words, optionally under a budget. Shows "
                        "asking prices. Use it when a customer asks what's for sale."
                    ),
                    "parameters": [
                        {
                            "name": "q",
                            "in": "query",
                            "schema": {"type": "string"},
                            "description": 'What to look for, like "hybrid bike". Empty: all.',
                        },
                        {
                            "name": "max_usd",
                            "in": "query",
                            "schema": {"type": "number"},
                            "description": "Only items asking at most this much",
                        },
                    ],
                    "responses": {
                        "200": _ok(
                            {
                                "type": "object",
                                "properties": {
                                    "count": {"type": "integer"},
                                    "listings": {"type": "array", "items": _LISTING},
                                    "next_step": {"type": "string"},
                                },
                            },
                            "Matching listings, best match first",
                        ),
                        "400": _ERROR,
                    },
                }
            },
            "/haggles": {
                "post": {
                    "operationId": "startHaggle",
                    "summary": "Haggle for the buyer with every matching seller at once",
                    "description": (
                        "Use it when a customer wants a used item for less, or says the most "
                        "they'll pay. Yardsale's buyer agent haggles over several rounds with "
                        "every matching item's agent at once and never offers more than max_usd "
                        "or tells the sellers the max. Answers within about 25 seconds. If "
                        "status is running, call getHaggle with the haggle_id, which waits for "
                        "the result. Show the buyer the summary, then ask whether to buy the "
                        "best deal."
                    ),
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": ["looking_for", "max_usd"],
                                    "properties": {
                                        "looking_for": {
                                            "type": "string",
                                            "description": 'What the buyer wants, like "hybrid '
                                            'bike"',
                                        },
                                        "max_usd": {
                                            "type": "number",
                                            "description": "The most the buyer will pay",
                                        },
                                    },
                                }
                            }
                        },
                    },
                    "responses": {
                        "200": _ok(_HAGGLE, "The haggle, finished or still running"),
                        "400": _ERROR,
                        "401": _ERROR,
                    },
                }
            },
            "/haggles/{haggle_id}": {
                "get": {
                    "operationId": "getHaggle",
                    "summary": "Get a haggle's result",
                    "description": (
                        "Use it when startHaggle answered with status running. Waits up to about "
                        "25 seconds for the result; if it's still running, call it again."
                    ),
                    "parameters": [_haggle_id_param()],
                    "responses": {
                        "200": _ok(_HAGGLE, "The haggle, finished or still running"),
                        "404": _ERROR,
                        "502": _ERROR,
                    },
                }
            },
            "/haggles/{haggle_id}/accept": {
                "post": {
                    "operationId": "acceptDeal",
                    "summary": "Buy at the haggled price and get the payment link",
                    "description": (
                        "Use it only after the buyer says yes to a price from startHaggle. "
                        "Without item_id it buys the best deal. Returns a payment link to give "
                        "the buyer."
                    ),
                    "parameters": [_haggle_id_param()],
                    "requestBody": {
                        "required": False,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "item_id": {
                                            "type": "string",
                                            "description": "Another item from the haggle; "
                                            "leave out for the best deal",
                                        }
                                    },
                                }
                            }
                        },
                    },
                    "responses": {
                        "200": _ok(_DEAL, "Bought: here's the payment link"),
                        "404": _ERROR,
                        "409": _ERROR,
                        "502": _ERROR,
                    },
                }
            },
            "/deals/{deal_id}": {
                "get": {
                    "operationId": "getDeal",
                    "summary": "Check whether a purchase is paid",
                    "parameters": [
                        {
                            "name": "deal_id",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string"},
                        }
                    ],
                    "responses": {"200": _ok(_DEAL, "The deal"), "404": _ERROR},
                }
            },
            "/openapi.json": {
                "get": {
                    "operationId": "getOpenApi",
                    "summary": "This description",
                    "security": [],
                    "responses": {"200": {"description": "OpenAPI 3 JSON"}},
                }
            },
        },
    }
