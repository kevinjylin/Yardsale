"""Messages exchanged by Yardsale's agents.

Prices are integer cents. A Deal is an agreed price; payment is collected afterwards by the
Market's checkout (checkout.py) through the Agent Payment Protocol.
"""

from __future__ import annotations

from uagents import Model
from uagents_core.protocol import ProtocolSpecification


class ListItem(Model):
    """Seller -> Market: list one item with the seller's own title and prices. Nothing here is a
    suggestion, so it goes live without a separate approval step."""

    listing_ref: str  # seller-side ID; resending the same ref returns the same item agent
    title: str
    asking_cents: int
    floor_cents: int  # private to the seller and the item agent, never sent to buyers
    seller_id: str


class Listed(Model):
    """Market -> seller: the item agent is live at `item_address`."""

    listing_ref: str
    item_id: str
    title: str
    asking_cents: int
    item_address: str
    almanac_registered: bool  # False offline: the address exists only on this server


class ListingRejected(Model):
    listing_ref: str
    reason: str


# --- Haggling: buyer <-> item agent ---------------------------------------------------------
# The published YardsaleHaggle protocol (HAGGLE_SPEC below): any agent can take the buyer's seat.
# A buyer is identified by its agent address. The Market and the buyer agent haggle for chat
# buyers, so they add buyer_ref (the chat) to tell them apart; item agents ignore buyer_ref from
# anyone else. request_id, when set, is echoed in the reply.
#
# Offer -> Counter. A Counter with agreed=True means the item agent takes the offer at
# counter_cents. Nothing is sold until the buyer sends AcceptCounter with that exact price, so a
# buyer can haggle with several items at once and buy only one.


class Offer(Model):
    """Buyer -> item agent."""

    item_id: str
    offer_cents: int
    buyer_ref: str = ""
    request_id: str = ""


class Counter(Model):
    """Item agent -> buyer: the price this buyer can have now. Accept with AcceptCounter."""

    item_id: str
    counter_cents: int
    note: str
    expires_at: str = ""  # ISO 8601 UTC; accepting after this is refused
    request_id: str = ""
    agreed: bool = False  # True: the item agent took the buyer's offer at this price


class AcceptCounter(Model):
    """Buyer -> item agent: buy at the exact price of this buyer's latest Counter."""

    item_id: str
    counter_cents: int
    buyer_ref: str = ""
    request_id: str = ""


class Deal(Model):
    """Item agent -> buyer: sold, once per item. The Market's checkout then asks the buyer for
    payment (a RequestPayment for agents, a payment link in the chat)."""

    item_id: str
    price_cents: int
    deal_id: str = ""
    title: str = ""
    payment_status: str = "not_collected"  # not_collected -> pending (link sent) -> paid
    request_id: str = ""


class OfferRejected(Model):
    item_id: str
    reason: str
    # unknown_item | unavailable | invalid_amount | no_counter | counter_expired | counter_changed
    code: str = ""
    request_id: str = ""


HAGGLE_SPEC = ProtocolSpecification(
    name="YardsaleHaggle",
    version="1.0.0",
    interactions={
        Offer: {Counter, OfferRejected},
        AcceptCounter: {Deal, OfferRejected},
        Counter: set(),
        Deal: set(),
        OfferRejected: set(),
    },
    roles={"item": {Offer, AcceptCounter}, "buyer": {Counter, Deal, OfferRejected}},
)


class ItemSold(Model):
    """Hosted item agent -> Market: a buyer bought this item, so the Market records the deal and
    starts checkout. Item agents in the Market's own Bureau share its store and don't send it."""

    item_id: str
    deal_id: str
    buyer: str  # agent:<address> or chat:<ref>, as the item agent keyed the buyer
    price_cents: int


# --- The buyer agent: Market <-> Shopper --------------------------------------------------------


class HaggleTarget(Model):
    item_id: str
    address: str  # the item agent's
    title: str
    asking_cents: int


class HaggleRequest(Model):
    """Market -> buyer agent: haggle with these items at once for one chat buyer, never above
    max_cents. The buyer agent doesn't buy anything; the shopper picks from the result."""

    request_id: str
    buyer_ref: str
    max_cents: int  # the shopper's limit; never sent to an item agent
    items: list[HaggleTarget]


class HaggleRound(Model):
    offer_cents: int
    reply_cents: int  # the item agent's counter, or the price it agreed to
    agreed: bool


class HaggleOutcome(Model):
    item_id: str
    title: str
    asking_cents: int
    # agreed: the item agent took an offer. counter: its latest counter is within the max.
    # over_max: it wouldn't come down to the max. unavailable / no_answer: no haggle.
    status: str
    price_cents: int | None  # what the shopper can buy it for now (agreed or counter)
    rounds: list[HaggleRound]
    note: str = ""


class HaggleResult(Model):
    """Buyer agent -> Market: every round with every item, and the best price found."""

    request_id: str
    max_cents: int
    outcomes: list[HaggleOutcome]
    best_item_id: str = ""


# --- Photo listings: Market <-> seller ---------------------------------------------------------


class ListPhoto(Model):
    """Seller -> Market: price an item from a photo. The Market answers with Appraised and
    Proposed; nothing is listed until the seller sends ApproveListing."""

    listing_ref: str
    seller_id: str
    image_b64: str  # JPEG, already resized by the sender
    media_type: str
    note: str = ""  # optional seller hint, e.g. "size M, barely used"
    asking_cents: int | None = None  # seller overrides; None means use the appraisal
    floor_cents: int | None = None
    zip_code: str = ""  # where the seller is, for local asking prices; "" means the server's


class Proposed(Model):
    """Market -> seller: the suggested listing, saved and waiting for approval."""

    listing_ref: str
    item_id: str
    title: str
    asking_cents: int
    floor_cents: int  # only the seller sees this
    asking_from: str  # seller | appraisal
    floor_from: str  # seller | appraisal | default (70% of asking)


class ApproveListing(Model):
    """Seller -> Market: go live, optionally with changed prices. Answered with Listed."""

    listing_ref: str
    asking_cents: int | None = None
    floor_cents: int | None = None


class CancelListing(Model):
    """Seller -> Market: drop a proposal, or take a live listing down."""

    listing_ref: str


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


# --- The Market Scout: what similar items are listed for right now ----------------------------
# The Researcher finds what sold; the Scout finds what competitors are asking today on Craigslist,
# OfferUp, Facebook Marketplace, and eBay (through Apify actors, sources.py). Asking prices only:
# nothing here is a sale, so they never set the price on their own (pricing.market_view).


class ScoutRequest(Model):
    """Market -> Scout: look up live asking prices for this product."""

    request_id: str
    query: str
    zip_code: str = ""  # scopes the local sites; "" means each source's default
    max_results: int = 20  # asked of each source


class ScoutListing(Model):
    """One live listing, as the seller sees it: the price and a link, nothing private."""

    source: str  # craigslist | offerup | facebook | ebay
    title: str
    price_cents: int
    url: str
    location: str = ""


class SourcePrices(Model):
    """Asking prices on one site for listings that look like the same kind of item."""

    source: str
    count: int
    low_cents: int  # lower quartile (the lowest listing with fewer than 4)
    median_cents: int
    high_cents: int  # upper quartile (the highest listing with fewer than 4)
    closest: list[ScoutListing]  # up to 3, nearest the median
    prices_cents: list[int]  # every kept listing's price, so the Market can pool sites


class ScoutReport(Model):
    """Scout -> Market. A site that couldn't be searched is named in `failed`; a report with no
    sources at all is still a report, so a Scout failure never blocks a proposal."""

    request_id: str
    query: str
    sources: list[SourcePrices]
    failed: list[str]


class MarketView(Model):
    """What the item is worth, from both sides: what it sold for (the Researcher's eBay sales, as
    the Appraiser matched them) and what competitors are asking right now (the Scout). Worked out
    in code (pricing.market_view). Nothing here is a floor, so all of it can be shown to buyers."""

    sold_count: int
    sold_median_cents: int | None  # the appraisal's asking suggestion (rounded median)
    sold_low_cents: int | None  # the sales' quartiles
    sold_high_cents: int | None
    asking_count: int  # live listings across every site the Scout searched
    asking_median_cents: int | None
    asking_low_cents: int | None  # quartiles of the live asking prices
    asking_high_cents: int | None
    range_low_cents: int | None  # the suggested asking range
    range_high_cents: int | None
    sources: list[SourcePrices]
    line: str = ""  # one sentence of market evidence for haggling and listings; never a floor


class Appraised(Model):
    """Market -> seller: what the photo was identified as and how it was priced."""

    listing_ref: str
    item: ItemDetails
    appraisal: Appraisal


# --- The Deal Finder: Market <-> Deal Finder ----------------------------------------------------


class FindDeals(Model):
    """Market -> Deal Finder: the best-priced, best-quality listings for a buyer's search on
    other sites (eBay, Craigslist, OfferUp, ...), scored against what the item sells for."""

    request_id: str
    query: str  # the words to search for; the Deal Finder strips filler itself
    max_cents: int | None = None  # the most the buyer pays, shipping included; None for any
    zip: str = ""  # a ZIP code for sources that search around one; "" for the server's default


class FoundDeal(Model):
    """One listing on another site, scored. Prices are integer cents; shipping is None when
    the site doesn't say."""

    source: str  # craigslist | ebay | offerup | facebook
    external_id: str
    title: str
    url: str
    price_cents: int
    shipping_cents: int | None
    total_cents: int  # price plus known shipping, what the buyer pays
    location: str = ""
    condition: str = ""
    image_url: str | None = None
    posted_at: str = ""  # YYYY-MM-DD when the site gives one
    accepts_offers: bool = False
    photo_count: int | None = None
    seller_rating: float | None = None  # percent positive, when the site gives one
    latitude: float | None = None  # Craigslist gives these
    longitude: float | None = None
    score: float = 0.0  # higher is better; price against the market first, then quality, fit
    versus_market_pct: int | None = None  # positive: under the typical sold price; None: unknown
    why: str = ""  # one line: "35% under the $185 typical sold price, like new, 6 photos"
    # parts | accessory | missing_words | over_budget | no_price (price_cents is 0
    # then, meaning not listed). uAgents models are pydantic v1, which copies a default list
    # per instance.
    flags: list[str] = []  # noqa: RUF012


class SiteOutcome(Model):
    """How one site's search went (elsewhere.SiteResult): "ok" with `raw` listings, `kept` of
    them fitting; "empty", it answered with none; "failed", with `reason` (timeout, blocked,
    busy, credit, setup, error); "pending", still searching when the answer went out. A failed
    or pending site never counts as having nothing."""

    source: str
    status: str
    raw: int = 0
    kept: int = 0
    reason: str = ""


class DealsFound(Model):
    """Deal Finder -> Market: the top deals, best first, with the market price they're judged
    against. typical_cents is None when no recent eBay sales matched the search."""

    request_id: str
    query: str
    max_cents: int | None
    deals: list[FoundDeal]
    searched: list[str]  # sources that answered
    failed: list[str]  # sources that couldn't be searched
    considered: int  # listings the sources returned before filtering
    typical_cents: int | None = None  # the median of matching eBay sales in the last 90 days
    comps_used: int = 0  # how many sales that median is from
    comps_note: str = ""  # why there's no market price, when there isn't
    # The market price's spread (lower and upper quartile) and what it's from, "4 eBay sales
    # of the same thing in the last 90 days"; None and "" without a market price.
    comps_low_cents: int | None = None
    comps_high_cents: int | None = None
    comps_basis: str = ""
    # Per site, in the order searched. A site still searching when the answer went out (the
    # Deal Finder answers by its deadline, about 20 s) is "pending".
    sites: list[SiteOutcome] = []  # noqa: RUF012
    place: str = ""  # where the local sites searched: "Berkeley (94704)", "the SF Bay Area"
