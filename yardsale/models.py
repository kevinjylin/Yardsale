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


# --- Photo listings: Market <-> seller ---------------------------------------------------------


class ListPhoto(Model):
    """Seller -> Market: list an item from a photo. The Market prices it, then spawns its agent."""

    listing_ref: str
    seller_id: str
    image_b64: str  # JPEG, already resized by the sender
    media_type: str
    note: str = ""  # optional seller hint, e.g. "size M, barely used"
    asking_cents: int | None = None  # seller overrides; None means use the appraisal
    floor_cents: int | None = None


class ListingUpdate(Model):
    """Market -> seller: progress while a photo listing is being priced."""

    listing_ref: str
    stage: str  # identify | research | appraise
    text: str


# --- Services: Identifier, Researcher, Appraiser ------------------------------------------------
# Every request carries a request_id that the reply echoes back. Any service may answer with
# ServiceError instead of its normal reply.


class ItemDetails(Model):
    title: str  # short, eBay-style listing title
    brand: str  # "" when unknown
    model: str  # "" when unknown
    category: str
    condition: str  # new | like new | good | fair | poor
    details: list[str]  # visible features that affect price: size, color, variant, damage
    search_query: str  # what an eBay buyer would type to find this exact product
    generic: bool  # True when there's no identifiable brand or model
    confidence: str  # high | medium | low


class IdentifyRequest(Model):
    """Market -> Identifier."""

    request_id: str
    image_b64: str
    media_type: str
    note: str = ""


class Identified(Model):
    """Identifier -> Market."""

    request_id: str
    item: ItemDetails


class ResearchRequest(Model):
    """Market -> Researcher: find recent eBay sold listings."""

    request_id: str
    query: str
    condition: str = "used"  # new | used | any
    max_results: int = 20


class Comp(Model):
    """One eBay sold listing."""

    title: str
    price_cents: int  # sold price of the item, excluding shipping
    shipping_cents: int | None
    condition: str
    sold_at: str  # YYYY-MM-DD
    url: str
    image_url: str | None
    best_offer: bool  # True when eBay hides the accepted offer; price_cents is the list price


class ResearchResult(Model):
    """Researcher -> Market."""

    request_id: str
    query: str
    comps: list[Comp]


class AppraiseRequest(Model):
    """Market -> Appraiser: compare the seller's item with the sold listings and set a price."""

    request_id: str
    item: ItemDetails
    image_b64: str
    media_type: str
    comps: list[Comp]


class MatchedComp(Model):
    comp: Comp
    same_product: bool
    similarity: float  # 0 to 1, how good a price reference this sale is
    reason: str


class Appraisal(Model):
    """Appraiser -> Market. Prices are None when nothing close enough was found."""

    request_id: str
    comps_considered: int
    matches: list[MatchedComp]  # the sales the price is based on, best first
    basis: str  # plain-English summary of what the price is based on
    confidence: str  # high | medium | low | none
    asking_cents: int | None
    floor_cents: int | None
    low_cents: int | None
    high_cents: int | None


class ServiceError(Model):
    request_id: str
    reason: str


class Appraised(Model):
    """Market -> seller: what the photo was identified as and how it was priced."""

    listing_ref: str
    item: ItemDetails
    appraisal: Appraisal
