"""Selling on eBay too: a seller connects their own eBay account, and an approved listing also
goes up on eBay with Best Offer on, answered by Yardsale's haggling rules.

This is the selling side. `ebay.py` is the buying side (live listings found through Apify).

Sellers aren't developers. Yardsale holds one eBay developer keyset (EBAY_CLIENT_ID,
EBAY_CLIENT_SECRET, EBAY_RUNAME), and each seller grants access on eBay's own consent page
(OAuth Authorization Code grant). Yardsale never asks for, takes, or stores an eBay password.

1. "connect ebay" in the chat gives the seller a consent link. Its `state` is a random token
   saved (hashed) with the conversation for GRANT_TTL seconds, used once.
2. eBay sends the seller back to the RuName's accept URL, `<public API URL>/ebay/callback`
   (business_api.py), with a code. The code becomes an access token (2 hours) and a refresh
   token (about 18 months), saved per conversation in the store. Tokens, codes, and floors never
   go in logs.
3. On "approve", the listing also goes up on eBay through the Trading API: the photo is uploaded
   to eBay Picture Services, eBay suggests a category from the item's search words, and
   AddFixedPriceItem lists it at the asking price with Best Offer on, local pickup, no returns.
   The floor is never sent: eBay's automatic accept and decline prices stay unset, because eBay
   could show them, and Yardsale answers every offer itself.
4. Every minute the Market checks each eBay listing (`poll`): if it was sold or taken down on
   Yardsale, the eBay listing is ended; if someone bought it on eBay, the Yardsale listing is
   marked sold; and each new Best Offer gets haggle.seller_move with the listing's floor and
   private gap, like any Yardsale buyer: at or above the floor it's accepted (a sale on eBay),
   below it the agent counters, and the counter's note goes in the reply the eBay buyer reads.
   Counters stop a private gap above the floor, so they never show it. Each eBay buyer has its own
   haggle (buyer "ebay:<user ID>"). eBay allows 5 counters per side, so after 5 the agent
   declines and says its last price still stands. Messages stay within eBay's 250 characters.

The Trading API was picked over the Inventory API because only the Trading API answers a buyer's
Best Offer (GetBestOffers, RespondToBestOffer; the Negotiation API has no counters), it's not
documented whether those calls work on Inventory API listings, and AddFixedPriceItem lists an
item without the seller first opting into business policies (optInToProgram), creating policies,
and a merchant location. Sandbox publishOffer has also been reported to fail with shipping
package errors (25001).

Sandbox (api.sandbox.ebay.com) is the default; production only with YARDSALE_EBAY_ENV=production.
None of this has run against eBay yet: it's tested against a local stand-in (tests/fakes.py).
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import html
import logging
import secrets
import xml.etree.ElementTree as ET
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlencode

import aiohttp

from yardsale import haggle
from yardsale.config import ConfigError, EbaySettings, ebay_settings
from yardsale.negotiation import gap_cents
from yardsale.store import (
    ACTIVE,
    EBAY_ACTIVE,
    EBAY_ENDED,
    EBAY_SOLD,
    EbayListing,
    Listing,
    Store,
)

log = logging.getLogger("yardsale.ebay")

HOSTS = {
    "sandbox": ("https://auth.sandbox.ebay.com", "https://api.sandbox.ebay.com"),
    "production": ("https://auth.ebay.com", "https://api.ebay.com"),
}
ITEM_PAGES = {"sandbox": "https://sandbox.ebay.com/itm/", "production": "https://www.ebay.com/itm/"}
# eBay's scope names are the same in the sandbox. The Trading API takes the user token in the
# X-EBAY-API-IAF-TOKEN header.
SCOPES = (
    "https://api.ebay.com/oauth/api_scope",
    "https://api.ebay.com/oauth/api_scope/sell.inventory",
    "https://api.ebay.com/oauth/api_scope/sell.account",
)
MAX_COUNTERS = 5  # eBay allows at most 5 counter-offers per side in one negotiation
NS = "urn:ebay:apis:eBLBaseComponents"
COMPATIBILITY_LEVEL = "1331"
SITE_US = "0"
TIMEOUT = aiohttp.ClientTimeout(total=30)
GRANT_TTL = 15 * 60.0  # seconds a consent link works
REFRESH_EARLY = 5 * 60.0  # refresh an access token this long before it expires
CHECK_EVERY = 60.0  # seconds between checks of eBay listings and offers
MAX_TITLE = 80  # eBay's limit
MAX_SELLER_RESPONSE = 250
ALREADY_ENDED = {"1047"}  # "the auction has been closed": ended or sold on eBay already
NEW_CONDITION, USED_CONDITION = "1000", "3000"
NEEDS_ZIP = 'eBay needs your ZIP code for the listing. Say "ebay zip 94110" with yours.'
NOT_CONNECTED = 'Your eBay account isn\'t connected. Say "connect ebay" to connect it.'
LINK_USED = (
    'This link expired or was already used. Go back to the chat and say "connect ebay" for a new '
    "one."
)


class EbayError(RuntimeError):
    """eBay refused or couldn't be reached. The message is safe to show the seller: it never
    holds a token, a code, or a floor."""

    def __init__(self, message: str, code: str = "") -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class TokenGrant:
    access_token: str = field(repr=False)
    expires_in: float
    refresh_token: str = field(repr=False)
    refresh_expires_in: float


@dataclass(frozen=True)
class BestOffer:
    offer_id: str
    buyer: str  # the eBay user ID
    price_cents: int


@dataclass(frozen=True)
class EbaySale:
    transaction_id: str
    buyer: str
    price_cents: int


def _amount(cents: int) -> str:
    return f"{cents // 100}.{cents % 100:02d}"


def _cents(text: str) -> int | None:
    try:
        value = Decimal(text.strip())
    except (InvalidOperation, AttributeError):
        return None
    cents = int((value * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))
    return cents if cents > 0 else None


def _path(path: str) -> str:
    return "/".join(f"{{{NS}}}{part}" for part in path.split("/"))


def _text(element: ET.Element, path: str) -> str:
    found = element.find(_path(path))
    return (found.text or "").strip() if found is not None else ""


def _add(parent: ET.Element, tag: str, text: str | None = None, **attrs: str) -> ET.Element:
    child = ET.SubElement(parent, tag, attrs)
    if text is not None:
        child.text = text
    return child


def _request(call: str) -> ET.Element:
    return ET.Element(f"{call}Request", {"xmlns": NS})


def short_title(title: str) -> str:
    """At most 80 characters, cut at a word."""
    title = " ".join(title.split())
    if len(title) <= MAX_TITLE:
        return title
    cut = title[: MAX_TITLE + 1].rsplit(" ", 1)[0]
    return (cut if len(cut) >= MAX_TITLE // 2 else title[:MAX_TITLE]).rstrip(" ,;:-")


def description_html(listing: Listing) -> str:
    lines = [listing.title]
    if listing.item is not None:
        lines.append(f"Condition: {listing.item.condition}")
        if listing.item.details:
            lines.append("Details: " + "; ".join(listing.item.details))
    lines.append(
        "Local pickup. Sold through Yardsale: make an offer and the seller's agent answers."
    )
    return "".join(f"<p>{html.escape(line)}</p>" for line in lines)


class EbayClient:
    """eBay's OAuth and Trading API, for one developer keyset."""

    def __init__(
        self, settings: EbaySettings, api_base: str | None = None, auth_base: str | None = None
    ) -> None:
        self.settings = settings
        auth_host, api_host = HOSTS[settings.env]
        self.auth_base = (auth_base or auth_host).rstrip("/")
        self.api_base = (api_base or api_host).rstrip("/")

    def consent_url(self, state: str) -> str:
        query = urlencode(
            {
                "client_id": self.settings.client_id,
                "response_type": "code",
                "redirect_uri": self.settings.runame,
                "scope": " ".join(SCOPES),
                "state": state,
            }
        )
        return f"{self.auth_base}/oauth2/authorize?{query}"

    def item_url(self, ebay_item_id: str) -> str:
        return ITEM_PAGES[self.settings.env] + ebay_item_id

    # --- OAuth ----------------------------------------------------------------------------

    async def _token(self, form: dict[str, str]) -> dict[str, Any]:
        basic = base64.b64encode(
            f"{self.settings.client_id}:{self.settings.client_secret}".encode()
        ).decode()
        headers = {
            "Authorization": f"Basic {basic}",
            "Content-Type": "application/x-www-form-urlencoded",
        }
        try:
            async with (
                aiohttp.ClientSession(timeout=TIMEOUT) as session,
                session.post(
                    f"{self.api_base}/identity/v1/oauth2/token", data=form, headers=headers
                ) as response,
            ):
                status = response.status
                try:
                    data = await response.json(content_type=None)
                except ValueError:
                    data = {}
        except (aiohttp.ClientError, TimeoutError) as ex:
            raise EbayError(f"couldn't reach eBay's sign-in ({type(ex).__name__})") from None
        if status >= 400 or not isinstance(data, dict) or "access_token" not in data:
            # eBay's error names say what's wrong, like "invalid_grant"; they hold no secrets.
            reason = str(data.get("error", "")) if isinstance(data, dict) else ""
            raise EbayError(f"eBay refused the sign-in ({reason or status})")
        return data

    async def exchange_code(self, code: str) -> TokenGrant:
        data = await self._token(
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": self.settings.runame,
            }
        )
        if not data.get("refresh_token"):
            raise EbayError("eBay didn't give a refresh token")
        return TokenGrant(
            str(data["access_token"]),
            float(data.get("expires_in", 7200)),
            str(data["refresh_token"]),
            float(data.get("refresh_token_expires_in", 47304000)),
        )

    async def refresh(self, refresh_token: str) -> tuple[str, float]:
        """A new access token and its lifetime in seconds."""
        data = await self._token(
            {
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "scope": " ".join(SCOPES),
            }
        )
        return str(data["access_token"]), float(data.get("expires_in", 7200))

    # --- Trading API ----------------------------------------------------------------------

    async def trading(
        self,
        token: str,
        request: ET.Element,
        picture: tuple[bytes, str] | None = None,
    ) -> ET.Element:
        call = request.tag.removesuffix("Request")
        body = ET.tostring(request, encoding="utf-8", xml_declaration=True)
        headers = {
            "X-EBAY-API-CALL-NAME": call,
            "X-EBAY-API-SITEID": SITE_US,
            "X-EBAY-API-COMPATIBILITY-LEVEL": COMPATIBILITY_LEVEL,
            "X-EBAY-API-IAF-TOKEN": token,
        }
        data: bytes | aiohttp.FormData
        if picture is None:
            headers["Content-Type"] = "text/xml"
            data = body
        else:
            data = aiohttp.FormData()
            data.add_field("XML Payload", body.decode(), content_type="text/xml")
            data.add_field("image", picture[0], filename="photo.jpg", content_type=picture[1])
        try:
            async with (
                aiohttp.ClientSession(timeout=TIMEOUT) as session,
                session.post(f"{self.api_base}/ws/api.dll", data=data, headers=headers) as response,
            ):
                status, raw = response.status, await response.read()
        except (aiohttp.ClientError, TimeoutError) as ex:
            raise EbayError(f"couldn't reach eBay ({type(ex).__name__})") from None
        try:
            root = ET.fromstring(raw)
        except ET.ParseError:
            raise EbayError(f"eBay answered {call} with {status} and no XML") from None
        if _text(root, "Ack") not in ("Success", "Warning"):
            for error in root.findall(_path("Errors")):
                if _text(error, "SeverityCode") != "Warning":
                    message = _text(error, "LongMessage") or _text(error, "ShortMessage")
                    raise EbayError(
                        f"eBay refused {call}: {message[:300]}", _text(error, "ErrorCode")
                    )
            raise EbayError(f"eBay refused {call} ({status})")
        return root

    async def upload_picture(self, token: str, photo: bytes, media_type: str) -> str:
        request = _request("UploadSiteHostedPictures")
        _add(request, "PictureName", "yardsale-photo")
        root = await self.trading(token, request, picture=(photo, media_type))
        url = _text(root, "SiteHostedPictureDetails/FullURL")
        if not url:
            raise EbayError("eBay didn't return the photo's address")
        return url

    async def suggest_category(self, token: str, query: str) -> str:
        request = _request("GetSuggestedCategories")
        _add(request, "Query", query[:350])
        root = await self.trading(token, request)
        category = root.find(_path("SuggestedCategoryArray/SuggestedCategory/Category/CategoryID"))
        if category is None or not (category.text or "").strip():
            raise EbayError("eBay couldn't suggest a category for this item")
        return (category.text or "").strip()

    async def add_item(
        self,
        token: str,
        listing: Listing,
        category_id: str,
        picture_url: str,
        postal_code: str,
    ) -> str:
        """List it at the asking price with Best Offer on. Returns eBay's item ID. Never sends
        the floor: no automatic accept or decline price."""
        request = _request("AddFixedPriceItem")
        item = _add(request, "Item")
        _add(item, "Title", short_title(listing.title))
        _add(item, "Description", description_html(listing))
        _add(_add(item, "PrimaryCategory"), "CategoryID", category_id)
        _add(item, "StartPrice", _amount(listing.asking_cents), currencyID="USD")
        _add(item, "CategoryMappingAllowed", "true")
        condition = listing.item.condition if listing.item else ""
        if condition == "new":
            _add(item, "ConditionID", NEW_CONDITION)
        else:
            _add(item, "ConditionID", USED_CONDITION)
            if condition:
                _add(item, "ConditionDescription", f"{condition.capitalize()} condition")
        _add(item, "Country", "US")
        _add(item, "Currency", "USD")
        _add(item, "DispatchTimeMax", "3")
        _add(item, "ListingDuration", "GTC")
        _add(item, "ListingType", "FixedPriceItem")
        _add(_add(item, "PictureDetails"), "PictureURL", picture_url)
        _add(item, "PostalCode", postal_code)
        _add(item, "Quantity", "1")
        _add(item, "SKU", listing.item_id)
        _add(_add(item, "BestOfferDetails"), "BestOfferEnabled", "true")
        _add(_add(item, "ReturnPolicy"), "ReturnsAcceptedOption", "ReturnsNotAccepted")
        shipping = _add(item, "ShippingDetails")
        _add(shipping, "ShippingType", "Flat")
        option = _add(shipping, "ShippingServiceOptions")
        _add(option, "ShippingServicePriority", "1")
        _add(option, "ShippingService", "LocalPickup")
        _add(option, "ShippingServiceCost", "0.00", currencyID="USD")
        root = await self.trading(token, request)
        ebay_item_id = _text(root, "ItemID")
        if not ebay_item_id:
            raise EbayError("eBay didn't return an item ID")
        return ebay_item_id

    async def best_offers(self, token: str, ebay_item_id: str) -> list[BestOffer]:
        """Offers waiting for the seller's answer, including buyers' counter-offers."""
        request = _request("GetBestOffers")
        _add(request, "ItemID", ebay_item_id)
        _add(request, "BestOfferStatus", "Active")
        _add(request, "DetailLevel", "ReturnAll")
        root = await self.trading(token, request)
        offers = []
        for offer in root.iter(f"{{{NS}}}BestOffer"):
            status = _text(offer, "Status")
            cents = _cents(_text(offer, "Price"))
            offer_id = _text(offer, "BestOfferID")
            if status not in ("", "Active", "Pending") or cents is None or not offer_id:
                continue
            offers.append(BestOffer(offer_id, _text(offer, "Buyer/UserID"), cents))
        return offers

    async def respond(
        self,
        token: str,
        ebay_item_id: str,
        offer_id: str,
        action: str,
        message: str,
        counter_cents: int | None = None,
    ) -> None:
        """Accept, Decline, or Counter one offer, with a message the buyer reads."""
        request = _request("RespondToBestOffer")
        _add(request, "ItemID", ebay_item_id)
        _add(request, "BestOfferID", offer_id)
        _add(request, "Action", action)
        if counter_cents is not None:
            _add(request, "CounterOfferPrice", _amount(counter_cents), currencyID="USD")
            _add(request, "CounterOfferQuantity", "1")
        if message:
            _add(request, "SellerResponse", message[:MAX_SELLER_RESPONSE])
        await self.trading(token, request)

    async def sales(self, token: str, ebay_item_id: str) -> list[EbaySale]:
        """Purchases of the item on eBay: Buy It Now, or a buyer accepting a counter."""
        request = _request("GetItemTransactions")
        _add(request, "ItemID", ebay_item_id)
        root = await self.trading(token, request)
        found = []
        for sale in root.iter(f"{{{NS}}}Transaction"):
            cents = _cents(_text(sale, "TransactionPrice"))
            if cents is not None:
                found.append(
                    EbaySale(_text(sale, "TransactionID"), _text(sale, "Buyer/UserID"), cents)
                )
        return found

    async def end_item(self, token: str, ebay_item_id: str) -> None:
        request = _request("EndFixedPriceItem")
        _add(request, "ItemID", ebay_item_id)
        _add(request, "EndingReason", "NotAvailable")
        await self.trading(token, request)


def hash_state(state: str) -> str:
    return hashlib.sha256(state.encode()).hexdigest()


class EbaySeller:
    """Connecting sellers' eBay accounts, listing on eBay, and answering eBay offers."""

    def __init__(self, client: EbayClient, store: Store) -> None:
        self.client = client
        self.store = store
        self._token_locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._list_locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._polling = asyncio.Lock()

    # --- Connecting ---------------------------------------------------------------------------

    def connected(self, owner: str) -> bool:
        return self.store.ebay_account(owner) is not None

    def start_connect(self, owner: str) -> str:
        """The consent link for this conversation. Its state is random, not the owner."""
        state = secrets.token_urlsafe(32)
        self.store.add_ebay_grant(hash_state(state), owner)
        return self.client.consent_url(state)

    async def finish_connect(self, state: str, code: str) -> str:
        """eBay sent the seller back with a code: save their tokens. Returns the owner."""
        if not state or len(state) > 128 or not state.isascii():
            raise EbayError(LINK_USED)
        owner = self.store.take_ebay_grant(hash_state(state), self.store.clock() - GRANT_TTL)
        if owner is None:
            raise EbayError(LINK_USED)
        grant = await self.client.exchange_code(code)
        now = self.store.clock()
        self.store.save_ebay_account(
            owner,
            grant.access_token,
            now + grant.expires_in,
            grant.refresh_token,
            now + grant.refresh_expires_in,
        )
        log.info("An eBay account was connected")
        return owner

    def decline_connect(self, state: str) -> None:
        if state and len(state) <= 128 and state.isascii():
            self.store.take_ebay_grant(hash_state(state), self.store.clock() - GRANT_TTL)

    async def disconnect(self, owner: str) -> tuple[int, int]:
        """End the seller's live eBay listings, then forget their tokens. (ended, not ended)"""
        ended = failed = 0
        for record in self.store.ebay_listings(owner=owner, status=EBAY_ACTIVE):
            try:
                await self._end(record)
                ended += 1
            except EbayError as ex:
                log.warning(f"Couldn't end eBay listing for {record.item_id}: {ex}")
                self.store.set_ebay_status(record.item_id, EBAY_ENDED)
                failed += 1
        self.store.delete_ebay_account(owner)
        return ended, failed

    async def access_token(self, owner: str) -> str:
        async with self._token_locks[owner]:
            account = self.store.ebay_account(owner)
            if account is None:
                raise EbayError(NOT_CONNECTED)
            now = self.store.clock()
            if account.access_expires_at - REFRESH_EARLY > now:
                return account.access_token
            if account.refresh_expires_at <= now:
                self.store.delete_ebay_account(owner)
                raise EbayError('Your eBay sign-in expired. Say "connect ebay" again.')
            token, lifetime = await self.client.refresh(account.refresh_token)
            self.store.update_ebay_access(owner, token, now + lifetime)
            return token

    # --- Listing ------------------------------------------------------------------------------

    async def list_item(self, listing: Listing, postal_code: str | None) -> EbayListing:
        """Put an active Yardsale listing on eBay too. Listing it twice returns the first."""
        async with self._list_locks[listing.item_id]:
            existing = self.store.ebay_listing(listing.item_id)
            if existing is not None and existing.status == EBAY_ACTIVE:
                return existing
            current = self.store.get(listing.item_id)
            if current is None or current.status != ACTIVE:
                raise EbayError("it isn't for sale on Yardsale anymore")
            if not postal_code:
                raise EbayError(NEEDS_ZIP)
            photo = self.store.photo(current.item_id)
            if photo is None:
                raise EbayError("I don't have its photo anymore, and eBay needs one")
            token = await self.access_token(current.owner)
            picture_url = await self.client.upload_picture(token, photo[1], photo[0])
            query = current.item.search_query if current.item else ""
            category = await self.client.suggest_category(token, query or current.title)
            ebay_item_id = await self.client.add_item(
                token, current, category, picture_url, postal_code
            )
            record = self.store.add_ebay_listing(
                current.item_id, current.owner, ebay_item_id, self.client.item_url(ebay_item_id)
            )
            log.info(f"Listed {current.item_id} on eBay as item {ebay_item_id}")
            return record

    # --- Every minute -------------------------------------------------------------------------

    async def poll(self) -> None:
        """End eBay listings that left Yardsale, record eBay sales, and answer new offers."""
        async with self._polling:  # one check at a time
            for record in self.store.ebay_listings(status=EBAY_ACTIVE):
                try:
                    await self._check(record)
                except EbayError as ex:
                    log.warning(f"eBay check for {record.item_id} failed: {ex}")
                except Exception:
                    log.exception(f"eBay check for {record.item_id} failed")

    async def _end(self, record: EbayListing) -> None:
        try:
            await self.client.end_item(await self.access_token(record.owner), record.ebay_item_id)
        except EbayError as ex:
            if ex.code not in ALREADY_ENDED:
                raise
        self.store.set_ebay_status(record.item_id, EBAY_ENDED)
        log.info(f"Ended the eBay listing for {record.item_id}")

    async def _check(self, record: EbayListing) -> None:
        listing = self.store.get(record.item_id)
        if not self.connected(record.owner):
            return  # disconnected: nothing can be done with this listing on eBay
        if listing is None or listing.status != ACTIVE:
            await self._end(record)
            return
        token = await self.access_token(record.owner)
        sales = await self.client.sales(token, record.ebay_item_id)
        if sales:
            self._record_sale(record, sales[0])
            return
        for offer in await self.client.best_offers(token, record.ebay_item_id):
            if await self._answer(token, record, offer):
                return  # sold

    def _record_sale(self, record: EbayListing, sale: EbaySale) -> None:
        deal = self.store.close_deal(
            record.item_id,
            f"ebay:{sale.buyer}",
            sale.price_cents,
            deal_id=f"ebay-{sale.transaction_id or record.ebay_item_id}",
        )
        self.store.set_ebay_status(record.item_id, EBAY_SOLD)
        if deal is None:
            log.warning(f"{record.item_id} sold on eBay but was no longer for sale on Yardsale")
        else:
            log.info(f"{record.item_id} sold on eBay ({deal.deal_id})")

    async def _answer(self, token: str, record: EbayListing, offer: BestOffer) -> bool:
        """Answer one eBay Best Offer with Yardsale's rules. True if it was accepted (sold)."""
        if self.store.ebay_offer_answered(offer.offer_id):
            return False
        listing = self.store.get(record.item_id)
        if listing is None or listing.status != ACTIVE:
            return False
        buyer = f"ebay:{offer.buyer}"
        earlier = self.store.counter(listing.item_id, buyer)
        state = haggle.SellerState()
        if earlier is not None and earlier.expires_at > self.store.clock():
            state = haggle.SellerState(earlier.rounds, earlier.counter_cents, earlier.agreed)
        move = haggle.seller_move(
            listing.asking_cents, listing.floor_cents, gap_cents(listing), offer.price_cents, state
        )
        if move.agreed:
            await self.client.respond(
                token, record.ebay_item_id, offer.offer_id, "Accept", "Deal. Thanks for the offer!"
            )
            self.store.record_ebay_answer(offer.offer_id, listing.item_id, "accept", buyer)
            deal = self.store.close_deal(
                listing.item_id, buyer, offer.price_cents, deal_id=f"ebay-offer-{offer.offer_id}"
            )
            self.store.set_ebay_status(listing.item_id, EBAY_SOLD)
            if deal is None:
                log.warning(f"{listing.item_id} sold on eBay but was no longer for sale here")
            else:
                log.info(f"Accepted an eBay offer for {listing.item_id} ({deal.deal_id})")
            return True
        saved_cents = move.price_cents
        if self.store.ebay_counters_sent(listing.item_id, buyer) >= MAX_COUNTERS:
            # eBay allows no more counters: decline, and say the last price still stands.
            saved_cents = state.price_cents or move.price_cents
            note = (
                f"I can't counter again on eBay, but {haggle.usd(saved_cents)} still stands. "
                "Offer that and it's yours."
            )
            await self.client.respond(token, record.ebay_item_id, offer.offer_id, "Decline", note)
            action = "decline"
        elif move.price_cents >= listing.asking_cents or move.price_cents <= offer.price_cents:
            # Countering at the list price says nothing new: decline, with the agent's note.
            await self.client.respond(
                token, record.ebay_item_id, offer.offer_id, "Decline", move.note
            )
            action = "decline"
        else:
            await self.client.respond(
                token,
                record.ebay_item_id,
                offer.offer_id,
                "Counter",
                move.note,
                counter_cents=move.price_cents,
            )
            action = "counter"
        self.store.save_counter(
            listing.item_id, buyer, offer.price_cents, saved_cents, move.state.rounds, False
        )
        self.store.record_ebay_answer(offer.offer_id, listing.item_id, action, buyer)
        log.info(f"Answered an eBay offer for {listing.item_id}: {action}")
        return False


def from_env(store: Store) -> EbaySeller | None:
    """eBay selling from EBAY_CLIENT_ID, EBAY_CLIENT_SECRET, EBAY_RUNAME, and
    YARDSALE_EBAY_ENV, or None when they aren't set (or are wrong, which is logged)."""
    try:
        settings = ebay_settings()
    except ConfigError as error:
        log.error(f"eBay selling is off: {error}")
        return None
    if settings is None:
        return None
    return EbaySeller(EbayClient(settings), store)
