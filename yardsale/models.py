"""Messages exchanged by the Yardsale Bureau prototype.

Prices are integer cents. No message here moves money; a Deal is an agreed price only.
"""

from __future__ import annotations

from uagents import Model


class ListItem(Model):
    """Seller -> Market: list one item. The Market spins up a new item agent for it."""

    listing_ref: str  # seller-side ID; resending the same ref returns the same item agent
    title: str
    asking_cents: int
    floor_cents: int  # private to the item agent, never sent to buyers
    seller_id: str


class Listed(Model):
    """Market -> seller: the item agent is live at `item_address`."""

    listing_ref: str
    item_id: str
    title: str
    asking_cents: int
    item_address: str
    almanac_registered: bool


class ListingRejected(Model):
    listing_ref: str
    reason: str


class Offer(Model):
    """Buyer -> item agent."""

    item_id: str
    offer_cents: int


class Counter(Model):
    """Item agent -> buyer: the offer was below the floor."""

    item_id: str
    counter_cents: int
    note: str


class AcceptCounter(Model):
    """Buyer -> item agent: accept the exact counter-offer this buyer received."""

    item_id: str
    counter_cents: int


class Deal(Model):
    """Item agent -> buyer: agreed price. The prototype records it; no payment is made."""

    item_id: str
    price_cents: int
    seller_id: str


class OfferRejected(Model):
    item_id: str
    reason: str
