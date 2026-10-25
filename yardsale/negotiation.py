"""How an item agent in the Market's Bureau answers a buyer, using the rules in haggle.py. The
same rules serve agents that message an item agent directly and chat buyers the Market and the
buyer agent haggle for. Agents hosted on Agentverse run the same rules from their own storage.

- An offer at or above the floor gets an agreement at the offered price.
- A lower offer gets a counter that comes down a little each round but never reaches the floor.
- Only accepting this buyer's latest counter or agreement, at its exact price and before it
  expires, closes the deal.
"""

from __future__ import annotations

from yardsale import haggle
from yardsale.common import derive_seed
from yardsale.models import Counter, Deal, OfferRejected
from yardsale.pricing import valid_cents
from yardsale.store import ACTIVE, Listing, Store


def buyer_key(sender: str, buyer_ref: str, trusted: str | set[str]) -> str:
    """Who is buying. Only Yardsale's own agents (the Market and the buyer agent) may haggle for
    someone else, a chat buyer; any other sender is the buyer itself, whatever buyer_ref it
    sends."""
    speaks_for_chats = {trusted} if isinstance(trusted, str) else trusted
    if buyer_ref and sender in speaks_for_chats:
        return f"chat:{buyer_ref}"
    return f"agent:{sender}"


def gap_cents(listing: Listing) -> int:
    """How far above the floor this item's counters stop. Private: it comes from the master
    seed, like the item agent's own key."""
    return haggle.private_gap(
        listing.asking_cents, listing.floor_cents, derive_seed(f"haggle:{listing.item_id}")
    )


def _rejected(item_id: str, code: str, reason: str, request_id: str) -> OfferRejected:
    return OfferRejected(item_id=item_id, reason=reason, code=code, request_id=request_id)


def _for_sale(store: Store, item_id: str, request_id: str) -> Listing | OfferRejected:
    listing = store.get(item_id)
    if listing is None:
        return _rejected(item_id, "unknown_item", "unknown item", request_id)
    if listing.status != ACTIVE:
        return _rejected(item_id, "unavailable", "this item is no longer for sale", request_id)
    return listing


def answer_offer(
    store: Store, item_id: str, buyer: str, offer_cents: int, request_id: str = ""
) -> Counter | OfferRejected:
    listing = _for_sale(store, item_id, request_id)
    if isinstance(listing, OfferRejected):
        return listing
    if not valid_cents(offer_cents):
        return _rejected(item_id, "invalid_amount", "offers must be a positive price", request_id)
    earlier = store.counter(item_id, buyer)
    state = haggle.SellerState()
    if earlier is not None and earlier.expires_at > store.clock():
        state = haggle.SellerState(earlier.rounds, earlier.counter_cents, earlier.agreed)
    move = haggle.seller_move(
        listing.asking_cents,
        listing.floor_cents,
        gap_cents(listing),
        offer_cents,
        state,
        listing.market_line,
    )
    saved = store.save_counter(
        item_id,
        buyer,
        offer_cents,
        move.price_cents,
        move.state.rounds,
        move.agreed,
    )
    return Counter(
        item_id=item_id,
        counter_cents=saved.counter_cents,
        note=move.note,
        expires_at=saved.expires_iso,
        request_id=request_id,
        agreed=move.agreed,
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
    record = store.close_deal(listing.item_id, buyer, counter_cents)
    if record is None:  # another buyer got there first
        return _rejected(item_id, "unavailable", "this item is no longer for sale", request_id)
    return Deal(
        item_id=listing.item_id,
        price_cents=counter_cents,
        deal_id=record.deal_id,
        title=listing.title,
        payment_status=record.payment_status,
        request_id=request_id,
    )
