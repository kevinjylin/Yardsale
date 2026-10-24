"""How an item agent answers a buyer. The same rules serve agents that message an item agent
directly and chat buyers the Market haggles for.

- An offer at or above the floor closes the deal at the offered price.
- A lower offer gets a counter halfway to the asking price, never below the floor.
- Only accepting this buyer's latest counter, at its exact price and before it expires, closes
  the deal at the counter price.
"""

from __future__ import annotations

from yardsale.models import Counter, Deal, OfferRejected
from yardsale.pricing import counter_price, valid_cents
from yardsale.store import ACTIVE, Listing, Store


def buyer_key(sender: str, buyer_ref: str, market: str) -> str:
    """Who is buying. Only the Market may haggle for someone else (a chat buyer); any other
    sender is the buyer itself, whatever buyer_ref it sends."""
    if buyer_ref and sender == market:
        return f"chat:{buyer_ref}"
    return f"agent:{sender}"


def _rejected(item_id: str, code: str, reason: str, request_id: str) -> OfferRejected:
    return OfferRejected(item_id=item_id, reason=reason, code=code, request_id=request_id)


def _for_sale(store: Store, item_id: str, request_id: str) -> Listing | OfferRejected:
    listing = store.get(item_id)
    if listing is None:
        return _rejected(item_id, "unknown_item", "unknown item", request_id)
    if listing.status != ACTIVE:
        return _rejected(item_id, "unavailable", "this item is no longer for sale", request_id)
    return listing


def _deal(
    store: Store, listing: Listing, buyer: str, price_cents: int, request_id: str
) -> Deal | OfferRejected:
    record = store.close_deal(listing.item_id, buyer, price_cents)
    if record is None:  # another buyer got there first
        return _rejected(
            listing.item_id, "unavailable", "this item is no longer for sale", request_id
        )
    return Deal(
        item_id=listing.item_id,
        price_cents=price_cents,
        deal_id=record.deal_id,
        title=listing.title,
        payment_status=record.payment_status,
        request_id=request_id,
    )


def answer_offer(
    store: Store, item_id: str, buyer: str, offer_cents: int, request_id: str = ""
) -> Counter | Deal | OfferRejected:
    listing = _for_sale(store, item_id, request_id)
    if isinstance(listing, OfferRejected):
        return listing
    if not valid_cents(offer_cents):
        return _rejected(item_id, "invalid_amount", "offers must be a positive price", request_id)
    if offer_cents >= listing.floor_cents:
        return _deal(store, listing, buyer, offer_cents, request_id)
    counter = store.save_counter(
        item_id,
        buyer,
        offer_cents,
        counter_price(offer_cents, listing.asking_cents, listing.floor_cents),
    )
    return Counter(
        item_id=item_id,
        counter_cents=counter.counter_cents,
        note="That's too low for me.",
        expires_at=counter.expires_iso,
        request_id=request_id,
    )


def answer_accept(
    store: Store, item_id: str, buyer: str, counter_cents: int, request_id: str = ""
) -> Deal | OfferRejected:
    listing = _for_sale(store, item_id, request_id)
    if isinstance(listing, OfferRejected):
        return listing
    counter = store.counter(item_id, buyer)
    if counter is None:
        return _rejected(item_id, "no_counter", "no counter-offer was made to you", request_id)
    if counter.counter_cents != counter_cents:
        return _rejected(
            item_id,
            "counter_changed",
            "that isn't your latest counter-offer; accept the newest one",
            request_id,
        )
    if counter.expires_at <= store.clock():
        return _rejected(
            item_id, "counter_expired", "that counter-offer expired; make a new offer", request_id
        )
    return _deal(store, listing, buyer, counter_cents, request_id)
