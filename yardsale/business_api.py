"""Yardsale's HTTP API for @yardsale, its Fetch Business agent.

Fetch Business's "OpenAPI Integration" (a Business Tool) lets @yardsale call an outside API from
its own chat. Fetch reads /openapi.json and makes one tool per operation, so the descriptions
below are written for the model that picks between them. The two products come first: deals for
buyers (Yardsale's listings, then the best deals on other sites) and prices for sellers (what
something is worth, from real eBay sales). Haggling and buying stay available but are marked
"advanced" and say to use them only when the buyer explicitly asks to haggle with a Yardsale
seller, so @yardsale searches (Yardsale and other sites) for "the best deal" or "the best price":

    GET  /listings                search what's for sale on Yardsale (asking prices only)
    GET  /other-sites             the best deals on eBay, Craigslist, OfferUp... under a max,
                                  with a deal score when the finder gives one (links only)
    GET  /price-check             what an item is worth: recent eBay sold prices for the words,
                                  plus local asking prices when a Scout agent is running
    POST /haggles                 advanced: the buyer agent haggles with every match at once
    GET  /haggles/{id}            a haggle that was still running when POST answered
    POST /haggles/{id}/accept     advanced: buy at the haggled price; returns the payment link
    GET  /deals/{id}              whether a deal is paid
    GET  /openapi.json            this API's description (no key needed)
    GET  /ebay/callback           where eBay sends a seller after connecting their account (no
                                  key; its one-time `state` is checked instead). Not a tool.

Photos can't come through this API. A seller with a photo is sent to the Market agent
(@ys-orchestrator) in ASI:One, where Claude reads the photo; ASI:One's models read typed text
only. A price check here matches eBay sales by the seller's words, without Claude's same-product
judgement, so it says so.

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
import html
import importlib
import logging
import os
import secrets
import time
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import uuid4

from aiohttp import web
from uagents import Context

from yardsale import deals
from yardsale.chat import LOWEST_SHARE_REACH, STATUS_TEXT, Backend, haggle_text
from yardsale.common import PipelineError
from yardsale.deals import ScoredDeal
from yardsale.ebay_seller import EbayError
from yardsale.elsewhere import Found, search_words, site_name
from yardsale.hosting import profile_url
from yardsale.models import (
    AcceptCounter,
    Comp,
    Counter,
    Deal,
    HaggleResult,
    OfferRejected,
    ResearchRequest,
    ResearchResult,
)
from yardsale.payments import PaymentError
from yardsale.pricing import (
    PriceError,
    median,
    parse_usd,
    quartiles,
    round_cents,
    round_price,
    to_cents,
    usd,
    valid_cents,
)
from yardsale.search import search
from yardsale.sources import ExternalListing
from yardsale.store import AGENTVERSE, PENDING, Listing

DEFAULT_PORT = 8110
KEY_HEADER = "X-Yardsale-Key"
EBAY_CALLBACK = "/ebay/callback"  # eBay's redirect after a seller connects (ebay_seller.py)
ANSWER_WITHIN = 25.0  # seconds POST /haggles waits before answering "still haggling"
QUOTES_LAST = 24 * 3600  # seconds a haggled price can be accepted, like the item agents' counters
BUYER_PREFIX = "api-"  # buyer_ref of an API buyer; its deals are "chat:api-..."
PRICE_CHECK_CACHE = 600.0  # seconds a price check's sales are reused (each one is a paid search)
PRICE_CHECK_SALES = 20  # eBay sales asked for, like a photo listing
PRICE_CHECK_EXAMPLES = 3  # sales shown, like the proposal's "closest sales"
PRICE_CHECK_WAIT = 180.0  # seconds to wait for the Researcher, which may queue behind photos
SCOUT_WAIT = 20.0  # seconds a Scout agent gets before its local block is left out
MAX_CACHED_CHECKS = 64

# (ctx, query) -> recent eBay sold listings. The default asks the Market's Researcher agent.
ResearchComps = Callable[[Context, str], Awaitable[list[Comp]]]
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


def _no_such_haggle(haggle_id: str) -> web.Response:
    """Logs the start and length of the unknown ID, so a mistyped one can be told apart."""
    log.info(f"API haggle {haggle_id[:3]!r} not found ({len(haggle_id)} characters)")
    return _error(404, NO_SUCH_HAGGLE, "not_found")


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


def _external_json(listing: ExternalListing) -> dict[str, Any]:
    """A listing on another site, for @yardsale to show with its link."""
    price, shipping, total = listing.price_cents, listing.shipping_cents, listing.total_cents
    data: dict[str, Any] = {
        "site": site_name(listing.source),
        "title": listing.title,
        "price_usd": dollars(price) if price is not None else None,
        "shipping_usd": dollars(shipping) if shipping is not None else None,
        "total_usd": dollars(total) if total is not None else None,
        "url": listing.url,
        "location": listing.location,
        "condition": listing.condition,
        "accepts_offers": listing.accepts_offers,
    }
    if listing.image_url:
        data["image_url"] = listing.image_url
    # The deal finder may score each listing (elsewhere.py). Older results have neither field,
    # and the description marks both optional, so a tool runner copes either way.
    score = getattr(listing, "score", None)
    if isinstance(score, int | float) and not isinstance(score, bool) and score == score:
        data["score"] = float(score)
    reason = getattr(listing, "reason", None)
    if isinstance(reason, str) and reason.strip():
        data["reason"] = reason.strip()[:300]
    return data


def _deal_json(deal: ScoredDeal) -> dict[str, Any]:
    """A scored deal: the listing plus why it's a good one (deals.py)."""
    data = _external_json(deal.listing)
    data["versus_market_pct"] = deal.versus_market_pct
    data["score"] = deal.score
    data["reason"] = deals.reason(deal, site_name(deal.listing.source))
    data["flags"] = list(deal.flags)
    return data


def _other_sites_json(found: Found, sites: str) -> dict[str, Any]:
    failed = [site_name(name) for name in found.failed]
    if found.deals:
        next_step = (
            "Show the buyer these deals with their links and each one's reason. They aren't "
            "Yardsale listings: Yardsale's agents can't haggle for them or take payment, so the "
            "buyer opens the link to buy from the seller. Craigslist sellers usually take offers "
            "by email."
        )
    else:
        next_step = "Nothing fits on other sites either. Try other words or a higher max."
    unsearched = f" ({', '.join(failed)} couldn't be searched)" if failed else ""
    market = found.market
    if market is not None:
        against = (
            f"Each is priced against the typical sold price on eBay, {usd(market.typical_cents)} "
            f"from {market.basis}."
        )
    else:
        against = "No recent eBay sales matched, so prices are compared with each other."
    return {
        "status": "done",
        "count": len(found.deals),
        "listings": [_deal_json(deal) for deal in found.deals],
        "searched": [site_name(name) for name in found.searched],
        "failed": failed,
        "typical_sold_usd": dollars(market.typical_cents) if market else None,
        "comps_used": market.sales if market else 0,
        "summary": (
            f"{len(found.deals)} deal(s) on {sites}{unsearched}, best first: price against the "
            f"market, then quality, then fit. {against}"
        ),
        "next_step": next_step,
    }


# --- Price checks ------------------------------------------------------------------------------


def _price_cents(comps: list[Comp]) -> list[int]:
    """The sold prices a summary is made from. An accepted Best Offer shows the list price, not
    what was paid, so those are left out whenever there are others (as appraise() does)."""
    paid = [c.price_cents for c in comps if not c.best_offer]
    return paid or [c.price_cents for c in comps]


def comps_summary(query: str, comps: list[Comp]) -> dict[str, Any]:
    """What recent eBay sales say an item sells for, in plain code (no model sets a price).

    The typical price is the median; the usual range is the lower and upper quartiles; the
    suggested asking price is the median rounded the way a person would price it. The sales
    match the words only: without a photo, nobody has checked that each one is the same product.
    """
    if not comps:
        return {
            "query": query,
            "count": 0,
            "typical_usd": None,
            "low_usd": None,
            "high_usd": None,
            "suggested_asking_usd": None,
            "basis": "No eBay sales in the last 90 days matched those words.",
            "examples": [],
        }
    prices = _price_cents(comps)
    middle = median(prices)
    low, high = quartiles(prices)
    skipped = len(comps) - len(prices)
    basis = f"{len(prices)} eBay sales in the last 90 days matching the words"
    if skipped:
        basis += f" (left out {skipped} accepted Best Offer sale{'s' if skipped != 1 else ''})"
    basis += (
        ". Matched by title, not by photo, so some may be a different model or a bundle; a "
        "photo sent to @ys-orchestrator gets each sale checked."
    )
    examples = [
        {
            "title": c.title,
            "sold_usd": dollars(c.price_cents),
            "sold_at": c.sold_at,
            "condition": c.condition,
            "url": c.url,
        }
        for c in sorted(comps, key=lambda c: abs(c.price_cents - middle))[:PRICE_CHECK_EXAMPLES]
    ]
    return {
        "query": query,
        "count": len(prices),
        "typical_usd": dollars(round_cents(middle)),
        "low_usd": dollars(round_cents(low)),
        "high_usd": dollars(round_cents(high)),
        "suggested_asking_usd": dollars(round_price(middle)),
        "basis": basis,
        "examples": examples,
    }


async def research_via_market(ctx: Context, query: str) -> list[Comp]:
    """Ask the Market's Researcher agent (the same one photo listings use) for recent sales."""
    from yardsale import bureau_server  # it imports this module, so not at import time

    rt = bureau_server.runtime()
    result = await bureau_server.call_service(
        ctx,
        rt.services.researcher,
        ResearchRequest(
            request_id=uuid4().hex, query=query, condition="used", max_results=PRICE_CHECK_SALES
        ),
        ResearchResult,
        PRICE_CHECK_WAIT,
    )
    return result.comps


async def scout_local(query: str, zip_code: str) -> dict[str, Any] | None:
    """What a Scout agent (yardsale/scout.py) says things like this are asking for nearby. The
    module is another agent's work: it's used only if it imports and has `local_prices(query,
    zip_code)` returning a JSON-able dict; anything else leaves the block out."""
    try:
        scout = importlib.import_module("yardsale.scout")
    except ImportError:
        return None
    local_prices = getattr(scout, "local_prices", None)
    if not callable(local_prices):
        return None
    try:
        answer = local_prices(query, zip_code)
        if asyncio.iscoroutine(answer):
            answer = await asyncio.wait_for(answer, SCOUT_WAIT)
    except Exception as ex:  # a scout failure never fails the price check
        log.warning(f"Price check: the Scout couldn't answer ({type(ex).__name__})")
        return None
    if not isinstance(answer, dict) or not answer:
        return None
    if not answer.get("count") and not answer.get("failed"):
        return None  # no live sources set up: nothing to say about local asking prices
    return dict(answer)


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
        research: ResearchComps = research_via_market,
        cache_for: float = PRICE_CHECK_CACHE,
    ) -> None:
        self.backend = backend
        self.store = backend.store
        self.context = context  # the Market's, for messages to the buyer and item agents
        self.key = key
        self.public_url = public_url.rstrip("/") if public_url else None
        self.answer_within = answer_within
        self.clock = clock
        self.research = research
        self.cache_for = cache_for
        self.running: dict[str, asyncio.Task[HaggleResult]] = {}
        # (words, zip) -> (started, the check). A running check is joined, a finished one reused.
        self.checks: dict[tuple[str, str], tuple[float, asyncio.Task[dict[str, Any]]]] = {}

    # --- Plumbing ---------------------------------------------------------------------------

    def app(self) -> web.Application:
        app = web.Application(middlewares=[self._log, self._auth])
        app.router.add_get("/openapi.json", self.openapi)
        app.router.add_get("/listings", self.listings)
        app.router.add_get("/other-sites", self.other_sites)
        app.router.add_get("/price-check", self.price_check)
        app.router.add_post("/haggles", self.start_haggle)
        app.router.add_get("/haggles/{haggle_id}", self.get_haggle)
        app.router.add_post("/haggles/{haggle_id}/accept", self.accept)
        app.router.add_get("/deals/{deal_id}", self.get_deal)
        app.router.add_get(EBAY_CALLBACK, self.ebay_callback)
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
        if request.path in ("/openapi.json", EBAY_CALLBACK):  # eBay's redirect sends no key
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
                    "Show the buyer these with their asking prices, then call searchOtherSites "
                    "with the same q and max_usd so they can compare real listings elsewhere. "
                    "Call startHaggle only if the buyer explicitly asks Yardsale to haggle with "
                    "a Yardsale seller."
                    if found
                    else "Nothing on Yardsale matches right now. Call searchOtherSites with the "
                    "same q and max_usd to find deals on other sites, or try other words."
                    if self.backend.finder.enabled
                    else "Nothing matches right now. Try other words or a higher budget."
                ),
            }
        )

    # --- Other sites ------------------------------------------------------------------------

    async def other_sites(self, request: web.Request) -> web.Response:
        """The best deals on other sites, waiting up to answer_within seconds. A search still
        running keeps going, and the same q and max_usd pick it up (elsewhere.DealFinder)."""
        finder = self.backend.finder
        if not finder.enabled:
            return _error(503, "Searching other sites is off on this server.", "sources_off")
        query = request.query.get("q", "")[:200]
        if not search_words(query):
            return _error(400, 'Say what the buyer wants in q, like "road bike".', "bad_request")
        budget = None
        if "max_usd" in request.query:
            budget = _amount(request.query["max_usd"])
            if budget is None:
                return _error(400, "max_usd must be a dollar amount, like 300.", "bad_amount")
        task = finder.start(query, budget)
        try:
            found = await asyncio.wait_for(asyncio.shield(task), self.answer_within)
        except TimeoutError:
            return web.json_response(
                {
                    "status": "running",
                    "count": 0,
                    "listings": [],
                    "summary": f"Yardsale is searching {finder.site_names()}.",
                    "next_step": "Call searchOtherSites again with the same q and max_usd. It "
                    "picks up the same search and waits for the result.",
                }
            )
        return web.json_response(_other_sites_json(found, finder.site_names()))

    # --- Price checks -----------------------------------------------------------------------

    def _forget_old_checks(self) -> None:
        now = self.clock()
        for key, (started, task) in list(self.checks.items()):
            failed = task.done() and (task.cancelled() or task.exception() is not None)
            if (task.done() and now - started > self.cache_for) or failed:
                del self.checks[key]
        while len(self.checks) > MAX_CACHED_CHECKS:
            oldest = min(self.checks, key=lambda k: self.checks[k][0])
            if not self.checks[oldest][1].done():
                break
            del self.checks[oldest]

    async def _check(self, query: str, zip_code: str) -> dict[str, Any]:
        started = time.monotonic()
        comps, local = await asyncio.gather(
            self.research(self.context(), query), scout_local(query, zip_code)
        )
        summary = comps_summary(query, comps)
        if local is not None:
            summary["local"] = local
        log.info(
            f"API price check: {summary['count']} sale(s){' and local prices' if local else ''} "
            f"in {time.monotonic() - started:.1f}s"
        )
        return summary

    async def price_check(self, request: web.Request) -> web.Response:
        """What something is worth, from recent eBay sales (the Researcher) and, when a Scout
        agent is running, local asking prices. Cached per query; a check still running after
        answer_within seconds keeps going, and the same q picks it up."""
        query = " ".join(request.query.get("q", "").split())[:200]
        if not search_words(query):
            return _error(
                400, 'Say what the item is in q, like "Trek FX 2 hybrid bike".', "bad_request"
            )
        zip_code = request.query.get("zip", "").strip()[:10]
        if zip_code and not zip_code.replace("-", "").isalnum():
            return _error(400, "zip must be a postal code, like 94704.", "bad_request")
        self._forget_old_checks()
        key = (query.lower(), zip_code)
        cached = self.checks.get(key)
        if cached is None:
            task = asyncio.ensure_future(self._check(query, zip_code))
            self.checks[key] = (self.clock(), task)
        else:
            task = cached[1]
        try:
            summary = await asyncio.wait_for(asyncio.shield(task), self.answer_within)
        except TimeoutError:
            return web.json_response(
                {
                    "status": "running",
                    "query": query,
                    "count": 0,
                    "summary": "Yardsale is looking up recent eBay sales.",
                    "next_step": "Call priceCheck again with the same q (and zip). It picks up "
                    "the same check and waits for the result.",
                }
            )
        except PipelineError as ex:
            return _error(502, f"Yardsale couldn't look up eBay sales: {ex}", "research_failed")
        except Exception:
            log.exception("Price check failed")
            return _error(502, "Yardsale couldn't look up eBay sales right now.", "research_failed")
        if summary["count"]:
            next_step = (
                "Tell the seller the typical price and the usual range, say they come from real "
                "eBay sales of the last 90 days matched by title, and show the example sales as "
                "links. To list the item, they attach a photo in a message to @ys-orchestrator "
                "in ASI:One (Claude reads the photo and checks each sale against it); the "
                "minimum they set stays private."
            )
        else:
            next_step = (
                "Ask for the brand and model, or other words a listing title would have, and "
                "call priceCheck again. To sell it anyway, the seller attaches a photo in a "
                "message to @ys-orchestrator in ASI:One and names their price."
            )
        if "local" in summary:
            next_step += " The local block is what similar items are asking for nearby right now."
        return web.json_response({"status": "done", **summary, "next_step": next_step})

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
            return _no_such_haggle(haggle_id)
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
            return _no_such_haggle(haggle_id)
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

    # --- eBay sign-in ------------------------------------------------------------------------

    async def ebay_callback(self, request: web.Request) -> web.Response:
        """Where eBay sends a seller back after its consent page (the RuName's accept URL, and
        its decline URL too). The state must be one this server gave out, unused and recent."""
        ebay = self.backend.ebay
        if ebay is None:
            return _page(404, "eBay isn't set up on this Yardsale server.")
        state = request.query.get("state", "")
        code = request.query.get("code", "")
        if not code:
            ebay.decline_connect(state)
            return _page(
                400,
                'eBay wasn\'t connected. Go back to the chat and say "connect ebay" to try again.',
            )
        try:
            await ebay.finish_connect(state, code)
        except EbayError as ex:
            log.warning(f"eBay sign-in failed: {ex}")
            return _page(400, f"eBay wasn't connected. {ex}")
        return _page(200, "eBay connected. Go back to the chat.")


def _page(status: int, message: str) -> web.Response:
    body = (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        "<title>Yardsale</title></head>"
        "<body style='font-family: system-ui, sans-serif; max-width: 32rem; margin: 3rem auto; "
        f"padding: 0 1rem'><h1>Yardsale</h1><p>{html.escape(message)}</p></body></html>"
    )
    return web.Response(status=status, text=body, content_type="text/html")


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
_EXTERNAL = {
    "type": "object",
    "properties": {
        "site": {"type": "string", "description": "eBay, Craigslist, OfferUp, ..."},
        "title": {"type": "string"},
        "price_usd": {"type": "number", "nullable": True},
        "shipping_usd": {"type": "number", "nullable": True, "description": "Unknown: null"},
        "total_usd": {"type": "number", "nullable": True, "description": "Price plus shipping"},
        "url": {
            "type": "string",
            "description": "The listing on its own site: give the buyer this",
        },
        "location": {"type": "string"},
        "condition": {"type": "string"},
        "accepts_offers": {"type": "boolean", "description": "The site lets the buyer offer"},
        "image_url": {"type": "string"},
        "versus_market_pct": {
            "type": "integer",
            "nullable": True,
            "description": (
                "Percent under what the item typically sold for on eBay in the last 90 days, "
                "shipping included; negative means above it; null when no sales matched"
            ),
        },
        "score": {"type": "number", "description": "Higher is better; the list is sorted by it"},
        "reason": {
            "type": "string",
            "description": (
                'One line to show the buyer: "$120 on Craigslist, 35% under the $185 typical '
                'sold price, like new, 6 photos"'
            ),
        },
        "flags": {
            "type": "array",
            "items": {"type": "string"},
            "description": "parts, accessory, missing_words, or far_under: reasons for caution",
        },
    },
}
_SALE = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "sold_usd": _MONEY,
        "sold_at": {"type": "string", "description": "YYYY-MM-DD"},
        "condition": {"type": "string"},
        "url": {"type": "string", "description": "The sale on eBay"},
    },
}
_PRICE_CHECK = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["running", "done"]},
        "query": {"type": "string"},
        "count": {"type": "integer", "description": "Sales the prices come from"},
        "typical_usd": {"type": "number", "nullable": True, "description": "Median sold price"},
        "low_usd": {"type": "number", "nullable": True, "description": "Lower quartile"},
        "high_usd": {"type": "number", "nullable": True, "description": "Upper quartile"},
        "suggested_asking_usd": {
            "type": "number",
            "nullable": True,
            "description": "The typical price rounded the way sellers price",
        },
        "basis": {"type": "string", "description": "Where the numbers come from, for the seller"},
        "examples": {
            "type": "array",
            "items": _SALE,
            "description": "Up to 3 sales nearest the typical price, with links",
        },
        "local": {
            "type": "object",
            "additionalProperties": True,
            "description": (
                "Only when Yardsale's Scout agent is running: what similar items are asking "
                "for nearby (Craigslist, OfferUp, Facebook Marketplace). Missing otherwise."
            ),
        },
        "summary": {"type": "string"},
        "next_step": {"type": "string"},
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
                "Yardsale does two things. For buyers: say what you want and the most you'll "
                "pay, and Yardsale finds the best deals, first among its own listings "
                "(searchListings), then across eBay, Craigslist, OfferUp and Facebook "
                "Marketplace with a deal score and a reason for each (searchOtherSites). For "
                "sellers: say what you have and get what it's worth from real eBay sales of "
                "the last 90 days, and what similar items are asking nearby (priceCheck). To "
                "list an item for sale, a seller attaches a photo in a message to Yardsale's "
                "Market agent, @ys-orchestrator, in ASI:One: Claude reads the photo and prices "
                "it from matching sales; ASI:One's models read typed text only, and this API "
                "takes no photos. Each listed item has its own agent that holds the seller's "
                "private lowest price. Advanced: Yardsale's buyer agent can haggle with every "
                "matching item's agent at once under the buyer's max and buy with a payment "
                "link (startHaggle, acceptDeal); use those only when the buyer explicitly asks "
                "Yardsale to haggle with a Yardsale seller, never for a buyer who just wants a "
                "good deal or the best price (search for those). Never invent listings, "
                "prices, or sales: every number comes from a call. Prices are in US dollars."
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
                    "summary": "Find used items for sale on Yardsale",
                    "description": (
                        "Start here when a buyer wants something. Searches Yardsale's own "
                        "listings by words, optionally under a budget, and shows asking prices "
                        "(never a seller's lowest price). Then call searchOtherSites with the "
                        "same words and max, whatever the count, so the buyer compares real "
                        "listings with links. Don't haggle unless the buyer asks to."
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
            "/other-sites": {
                "get": {
                    "operationId": "searchOtherSites",
                    "summary": "Find the best deals on eBay, Craigslist, OfferUp and Facebook",
                    "description": (
                        "Use it for every shopping request, with searchListings: a buyer "
                        "asking for the best deal or price wants these compared. "
                        "Yardsale's Deal Finder agent searches eBay, Craigslist, OfferUp, and "
                        "other sites once, prices each listing against what the item typically "
                        "sold for on eBay in the last 90 days, and returns the best few at or "
                        "under max_usd (price plus shipping), best first. Each listing has its "
                        "site, title, price, total with shipping, url, a score (higher is "
                        "better), and a one-line reason; show the buyer the reasons and links. "
                        "These are NOT Yardsale listings: no haggling or payment through "
                        "Yardsale. Answers within about "
                        "25 seconds; if status is running, call it again with the same q and "
                        "max_usd."
                    ),
                    "parameters": [
                        {
                            "name": "q",
                            "in": "query",
                            "required": True,
                            "schema": {"type": "string"},
                            "description": 'What the buyer wants, like "road bike"',
                        },
                        {
                            "name": "max_usd",
                            "in": "query",
                            "schema": {"type": "number"},
                            "description": "The most the buyer will pay, shipping included",
                        },
                    ],
                    "responses": {
                        "200": _ok(
                            {
                                "type": "object",
                                "properties": {
                                    "status": {"type": "string", "enum": ["running", "done"]},
                                    "count": {"type": "integer"},
                                    "listings": {"type": "array", "items": _EXTERNAL},
                                    "searched": {"type": "array", "items": {"type": "string"}},
                                    "failed": {"type": "array", "items": {"type": "string"}},
                                    "typical_sold_usd": {
                                        "type": "number",
                                        "nullable": True,
                                        "description": (
                                            "What the item typically sold for on eBay in the "
                                            "last 90 days; the deals are priced against it"
                                        ),
                                    },
                                    "comps_used": {
                                        "type": "integer",
                                        "description": "How many sales that price is from",
                                    },
                                    "summary": {"type": "string"},
                                    "next_step": {"type": "string"},
                                },
                            },
                            "Deals on other sites, or a search still running",
                        ),
                        "400": _ERROR,
                        "401": _ERROR,
                        "503": _ERROR,
                    },
                }
            },
            "/price-check": {
                "get": {
                    "operationId": "priceCheck",
                    "summary": "What a used item is worth, from real eBay sales",
                    "description": (
                        "Use it when someone asks what their item is worth or what to ask for it "
                        '("what\'s my Trek FX 2 worth?"). Looks up eBay sales of the last 90 '
                        "days matching the words and works out the typical price, the usual "
                        "range, and a suggested asking price in plain code, with up to 3 example "
                        "sales as links. The sales are matched by title, not by photo, so say "
                        "so. With zip, and when Yardsale's Scout agent is running, a local block "
                        "says what similar items are asking for nearby. Photos can't be sent "
                        "here: to list the item, or to have each sale checked against a photo, "
                        "the seller attaches the photo in a message to @ys-orchestrator in "
                        "ASI:One (Claude reads photos; ASI:One's models read typed text only). "
                        "Answers within about 25 seconds; if status is running, call it again "
                        "with the same q."
                    ),
                    "parameters": [
                        {
                            "name": "q",
                            "in": "query",
                            "required": True,
                            "schema": {"type": "string"},
                            "description": (
                                "The item as a listing title would name it: brand, model, size, "
                                'like "Trek FX 2 Disc hybrid bike medium"'
                            ),
                        },
                        {
                            "name": "zip",
                            "in": "query",
                            "schema": {"type": "string"},
                            "description": "The seller's ZIP code, for local asking prices",
                        },
                    ],
                    "responses": {
                        "200": _ok(_PRICE_CHECK, "The price check, finished or still running"),
                        "400": _ERROR,
                        "401": _ERROR,
                        "502": _ERROR,
                    },
                }
            },
            "/haggles": {
                "post": {
                    "operationId": "startHaggle",
                    "summary": "Advanced: haggle for the buyer with every matching Yardsale seller",
                    "description": (
                        "Advanced. Use it only when the buyer explicitly asks Yardsale to haggle "
                        "with the seller of a Yardsale listing that searchListings returned. "
                        'Wanting "the best deal", "the best price", or something cheap is not '
                        "a request to haggle: search instead. "
                        "Yardsale's buyer agent haggles over several rounds with every matching "
                        "item's agent at once and never offers more than max_usd or tells the "
                        "sellers the max. Answers within about 25 seconds. If status is running, "
                        "call getHaggle with the haggle_id, which waits for the result. Show the "
                        "buyer the summary, then ask whether to buy the best deal. It can't "
                        "haggle for listings on other sites."
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
                    "summary": "Advanced: get a haggle's result",
                    "description": (
                        "Advanced. Use it only when startHaggle answered with status running. "
                        "Waits up to about 25 seconds for the result; if it's still running, "
                        "call it again."
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
                    "summary": "Advanced: buy at the haggled price and get the payment link",
                    "description": (
                        "Advanced. Use it only after the buyer says yes to a price from "
                        "startHaggle. Without item_id it buys the best deal. Returns a payment "
                        "link (test mode) to give the buyer; only the payment provider decides "
                        "that a deal is paid."
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
                    "summary": "Advanced: check whether a purchase is paid",
                    "description": (
                        "Advanced. Use it only for a deal_id acceptDeal returned, when the buyer "
                        "asks whether their payment went through."
                    ),
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
