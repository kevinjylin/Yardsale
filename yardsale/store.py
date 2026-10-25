"""Durable state: listings, counter-offers, deals and their payments, and chat conversations, in
one SQLite file. Also each listing's photo, and for sellers who connected eBay (ebay_seller.py),
their eBay tokens, their listings' eBay copies, and the eBay offers already answered. Tables added
since the first version are created in older files on open (CREATE TABLE IF NOT EXISTS).

The Market, the item agents, and the chat share one connection in one event loop. Every status
change is a conditional UPDATE inside a transaction, so a listing is approved, canceled, or sold
at most once however messages interleave.

No seeds are stored here. An item agent's address comes from the master seed and the item ID, so
re-creating the agents for active listings after a restart gives the same addresses. Item agents
hosted on Agentverse (host = 'agentverse') keep running there and aren't re-created. Once their
listing is sold or canceled, the Market deletes them and records when (agent_deleted_at).

Listing states:

    proposed --approve--> active --deal--> sold
        |                    |
        +------cancel--------+-----------> canceled

A deal's payment: not_collected (no checkout yet) -> pending (payment link sent) -> paid, or
expired / rejected when the buyer doesn't pay.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from yardsale.models import Appraisal, ItemDetails, MarketView
from yardsale.pricing import listing_prices
from yardsale.sources import ExternalListing

PROPOSED, ACTIVE, SOLD, CANCELED = "proposed", "active", "sold", "canceled"
COUNTER_TTL = 24 * 3600.0  # seconds a counter-offer can be accepted
BUREAU, AGENTVERSE = "bureau", "agentverse"  # where a listing's item agent runs
NOT_COLLECTED, PENDING, PAID, EXPIRED, REJECTED = (
    "not_collected",
    "pending",
    "paid",
    "expired",
    "rejected",
)
EBAY_ACTIVE, EBAY_SOLD, EBAY_ENDED = "active", "sold", "ended"  # an eBay listing's state

SCHEMA = """
CREATE TABLE IF NOT EXISTS listings (
    item_id TEXT PRIMARY KEY,
    owner TEXT NOT NULL,
    seller_id TEXT NOT NULL,
    listing_ref TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('proposed', 'active', 'sold', 'canceled')),
    title TEXT NOT NULL,
    asking_cents INTEGER NOT NULL CHECK (asking_cents > 0),
    floor_cents INTEGER NOT NULL CHECK (floor_cents > 0 AND floor_cents <= asking_cents),
    seller_asking_cents INTEGER,
    seller_floor_cents INTEGER,
    suggested_asking_cents INTEGER,
    suggested_floor_cents INTEGER,
    item_json TEXT,
    appraisal_json TEXT,
    address TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    host TEXT NOT NULL DEFAULT 'bureau',
    handle TEXT,
    agent_deleted_at REAL,
    market_json TEXT,
    UNIQUE (owner, listing_ref)
);
CREATE INDEX IF NOT EXISTS listings_by_status ON listings (status);
CREATE TABLE IF NOT EXISTS counters (
    item_id TEXT NOT NULL REFERENCES listings (item_id),
    buyer TEXT NOT NULL,
    offer_cents INTEGER NOT NULL,
    counter_cents INTEGER NOT NULL,
    expires_at REAL NOT NULL,
    rounds INTEGER NOT NULL DEFAULT 0,
    agreed INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (item_id, buyer)
);
CREATE TABLE IF NOT EXISTS deals (
    deal_id TEXT PRIMARY KEY,
    item_id TEXT NOT NULL UNIQUE REFERENCES listings (item_id),
    buyer TEXT NOT NULL,
    price_cents INTEGER NOT NULL CHECK (price_cents > 0),
    payment_status TEXT NOT NULL DEFAULT 'not_collected',
    created_at REAL NOT NULL,
    payment_provider TEXT,
    payment_ref TEXT,
    payment_url TEXT,
    commission_cents INTEGER,
    payout_cents INTEGER,
    paid_at REAL
);
CREATE INDEX IF NOT EXISTS deals_by_payment ON deals (payment_status);
CREATE TABLE IF NOT EXISTS conversations (
    key TEXT PRIMARY KEY,
    state TEXT NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS photos (
    key TEXT PRIMARY KEY,
    media_type TEXT NOT NULL,
    data BLOB NOT NULL
);
CREATE TABLE IF NOT EXISTS ebay_grants (
    state_hash TEXT PRIMARY KEY,
    owner TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS ebay_accounts (
    owner TEXT PRIMARY KEY,
    access_token TEXT NOT NULL,
    access_expires_at REAL NOT NULL,
    refresh_token TEXT NOT NULL,
    refresh_expires_at REAL NOT NULL,
    connected_at REAL NOT NULL,
    announced INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS ebay_listings (
    item_id TEXT PRIMARY KEY REFERENCES listings (item_id),
    owner TEXT NOT NULL,
    ebay_item_id TEXT NOT NULL,
    url TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('active', 'sold', 'ended')),
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS ebay_offers (
    offer_id TEXT PRIMARY KEY,
    item_id TEXT NOT NULL,
    action TEXT NOT NULL,
    answered_at REAL NOT NULL,
    buyer TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS watches (
    watch_id TEXT PRIMARY KEY,
    owner TEXT NOT NULL,
    query TEXT NOT NULL,
    scope TEXT NOT NULL DEFAULT '',
    budget_cents INTEGER,
    sources TEXT NOT NULL,
    every_seconds REAL NOT NULL,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL,
    last_run_at REAL,
    stopped INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS watches_by_owner ON watches (owner, stopped);
CREATE TABLE IF NOT EXISTS watch_hits (
    watch_id TEXT NOT NULL REFERENCES watches (watch_id) ON DELETE CASCADE,
    source TEXT NOT NULL,
    external_id TEXT NOT NULL,
    found_at REAL NOT NULL,
    told INTEGER NOT NULL DEFAULT 0,
    listing_json TEXT NOT NULL,
    PRIMARY KEY (watch_id, source, external_id)
);
CREATE INDEX IF NOT EXISTS watch_hits_untold ON watch_hits (watch_id, told);
"""

# Columns added after the first prototype. Older database files get them on open.
ADDED_COLUMNS = {
    "listings": {
        "host": "TEXT NOT NULL DEFAULT 'bureau'",
        "handle": "TEXT",
        "agent_deleted_at": "REAL",
        "market_json": "TEXT",
    },
    "counters": {
        "rounds": "INTEGER NOT NULL DEFAULT 0",
        "agreed": "INTEGER NOT NULL DEFAULT 0",
    },
    "deals": {
        "payment_provider": "TEXT",
        "payment_ref": "TEXT",
        "payment_url": "TEXT",
        "commission_cents": "INTEGER",
        "payout_cents": "INTEGER",
        "paid_at": "REAL",
    },
}


@dataclass
class Listing:
    item_id: str
    owner: str  # who may approve, edit, or cancel: an agent address, or asi1:<chat>
    seller_id: str  # the seller's own payout reference; never shown to buyers
    listing_ref: str  # the seller's ID for this listing
    status: str
    title: str
    asking_cents: int
    floor_cents: int  # private: only the seller and the item agent see it
    seller_asking_cents: int | None = None  # prices the seller gave, which win
    seller_floor_cents: int | None = None
    suggested_asking_cents: int | None = None  # the appraisal's
    suggested_floor_cents: int | None = None
    item: ItemDetails | None = None
    appraisal: Appraisal | None = None  # seller-only: includes the suggested floor
    market: MarketView | None = None  # sold prices beside live asking prices; nothing private
    address: str | None = None  # the item agent's, once approved
    created_at: float = 0.0
    updated_at: float = 0.0
    host: str = BUREAU  # where the item agent runs: this server's Bureau, or Agentverse
    handle: str | None = None  # the hosted item agent's Agentverse handle, without the @

    @property
    def asking_from(self) -> str:
        if self.seller_asking_cents is None and self.asking_cents == self.suggested_asking_cents:
            return "appraisal"
        return "seller"

    @property
    def floor_from(self) -> str:
        if self.seller_floor_cents is not None:
            return "seller"
        return "appraisal" if self.floor_cents == self.suggested_floor_cents else "default"

    @property
    def market_line(self) -> str:
        """Market evidence the item agent cites while haggling (pricing.market_line). Built from
        sold and asking prices only, never from the floor."""
        return self.market.line if self.market is not None else ""

    def searchable_text(self) -> str:
        """Public words a buyer's search can match. Never includes prices."""
        parts = [self.title]
        if self.item is not None:
            parts += [self.item.brand, self.item.model, self.item.category, *self.item.details]
        return " ".join(parts)


@dataclass
class CounterOffer:
    item_id: str
    buyer: str
    offer_cents: int  # the buyer's latest offer
    counter_cents: int  # the item agent's latest counter, or the price it agreed to
    expires_at: float
    rounds: int = 0  # serious offers below the floor so far
    agreed: bool = False

    @property
    def expires_iso(self) -> str:
        return datetime.fromtimestamp(self.expires_at, UTC).isoformat(timespec="seconds")


@dataclass
class DealRecord:
    deal_id: str
    item_id: str
    buyer: str
    price_cents: int
    payment_status: str  # not_collected | pending | paid | expired | rejected
    created_at: float
    payment_provider: str | None = None  # stripe | visa
    payment_ref: str | None = None  # the provider's checkout or payment link ID
    payment_url: str | None = None  # where the buyer pays
    commission_cents: int | None = None  # Yardsale's share, recorded when paid
    payout_cents: int | None = None  # the seller's share, recorded when paid
    paid_at: float | None = None


@dataclass
class EbayAccount:
    """A seller's eBay grant (ebay_seller.py). The tokens never go in logs or replies."""

    owner: str
    access_token: str = field(repr=False)
    access_expires_at: float
    refresh_token: str = field(repr=False)
    refresh_expires_at: float
    connected_at: float
    announced: bool


@dataclass
class EbayListing:
    item_id: str
    owner: str
    ebay_item_id: str
    url: str
    status: str  # active | sold | ended
    created_at: float
    updated_at: float


_EBAY_LISTING_COLUMNS = "item_id, owner, ebay_item_id, url, status, created_at, updated_at"


@dataclass
class Watch:
    """A standing search of other sites for one buyer, polled until it expires."""

    watch_id: str
    owner: str  # the conversation that may see and stop it: asi1:<hash>
    query: str
    scope: str = ""  # what the sources search: a Craigslist city, an eBay site
    budget_cents: int | None = None  # filters on the asking price only
    sources: tuple[str, ...] = ()  # source names; empty means every known source
    every_seconds: float = 0.0
    created_at: float = 0.0
    expires_at: float = 0.0
    last_run_at: float | None = None
    stopped: bool = False

    def due_at(self) -> float:
        return (
            self.created_at if self.last_run_at is None else self.last_run_at + self.every_seconds
        )


_LISTING_COLUMNS = (
    "item_id, owner, seller_id, listing_ref, status, title, asking_cents, floor_cents, "
    "seller_asking_cents, seller_floor_cents, suggested_asking_cents, suggested_floor_cents, "
    "item_json, appraisal_json, address, created_at, updated_at, host, handle, market_json"
)
_DEAL_COLUMNS = (
    "deal_id, item_id, buyer, price_cents, payment_status, created_at, payment_provider, "
    "payment_ref, payment_url, commission_cents, payout_cents, paid_at"
)
_COUNTER_COLUMNS = "item_id, buyer, offer_cents, counter_cents, expires_at, rounds, agreed"
_WATCH_COLUMNS = (
    "watch_id, owner, query, scope, budget_cents, sources, every_seconds, created_at, "
    "expires_at, last_run_at, stopped"
)


def _watch(row: sqlite3.Row) -> Watch:
    fields = dict(row)
    return Watch(
        **{
            **fields,
            "sources": tuple(name for name in fields["sources"].split(",") if name),
            "stopped": bool(fields["stopped"]),
        }
    )


def _listing(row: sqlite3.Row) -> Listing:
    data = dict(row)
    item_json, appraisal_json = data.pop("item_json"), data.pop("appraisal_json")
    market_json = data.pop("market_json")
    return Listing(
        **data,
        item=ItemDetails.model_validate_json(item_json) if item_json else None,
        appraisal=Appraisal.model_validate_json(appraisal_json) if appraisal_json else None,
        market=MarketView.model_validate_json(market_json) if market_json else None,
    )


def _deal(row: sqlite3.Row) -> DealRecord:
    return DealRecord(**dict(row))


def _counter(row: sqlite3.Row) -> CounterOffer:
    data = dict(row)
    data["agreed"] = bool(data["agreed"])
    return CounterOffer(**data)


class Store:
    def __init__(self, path: str | Path = ":memory:", clock: Callable[[], float] = time.time):
        self.path = str(path)
        self.clock = clock
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(self.path, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA foreign_keys = ON")
        self._db.executescript(SCHEMA)
        self._add_missing_columns()
        if self.path != ":memory:":
            os.chmod(self.path, 0o600)  # seller IDs and floor prices

    def close(self) -> None:
        self._db.close()

    def _add_missing_columns(self) -> None:
        for table, columns in ADDED_COLUMNS.items():
            have = {row["name"] for row in self._db.execute(f"PRAGMA table_info({table})")}
            for name, declaration in columns.items():
                if name not in have:
                    self._db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {declaration}")

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        self._db.execute("BEGIN IMMEDIATE")
        try:
            yield self._db
        except BaseException:
            self._db.execute("ROLLBACK")
            raise
        self._db.execute("COMMIT")

    # --- Listings ---------------------------------------------------------------------------

    def add_listing(self, listing: Listing) -> Listing:
        """Save a new proposal. If the owner already used this listing_ref, return that one."""
        now = self.clock()
        listing.created_at = listing.updated_at = now
        try:
            self._db.execute(
                f"INSERT INTO listings ({_LISTING_COLUMNS}) VALUES ({', '.join('?' * 20)})",
                (
                    listing.item_id,
                    listing.owner,
                    listing.seller_id,
                    listing.listing_ref,
                    listing.status,
                    listing.title,
                    listing.asking_cents,
                    listing.floor_cents,
                    listing.seller_asking_cents,
                    listing.seller_floor_cents,
                    listing.suggested_asking_cents,
                    listing.suggested_floor_cents,
                    listing.item.model_dump_json() if listing.item else None,
                    listing.appraisal.model_dump_json() if listing.appraisal else None,
                    listing.address,
                    now,
                    now,
                    listing.host,
                    listing.handle,
                    listing.market.model_dump_json() if listing.market else None,
                ),
            )
        except sqlite3.IntegrityError:
            existing = self.find_ref(listing.owner, listing.listing_ref)
            if existing is None:
                raise
            return existing
        return listing

    def get(self, item_id: str) -> Listing | None:
        row = self._db.execute(
            f"SELECT {_LISTING_COLUMNS} FROM listings WHERE item_id = ?", (item_id,)
        ).fetchone()
        return _listing(row) if row else None

    def find_ref(self, owner: str, listing_ref: str) -> Listing | None:
        row = self._db.execute(
            f"SELECT {_LISTING_COLUMNS} FROM listings WHERE owner = ? AND listing_ref = ?",
            (owner, listing_ref),
        ).fetchone()
        return _listing(row) if row else None

    def owned(self, owner: str, *statuses: str) -> list[Listing]:
        """The owner's listings, newest first, optionally only those in the given states."""
        query = f"SELECT {_LISTING_COLUMNS} FROM listings WHERE owner = ?"
        if statuses:
            query += f" AND status IN ({', '.join('?' * len(statuses))})"
        query += " ORDER BY created_at DESC, rowid DESC"
        return [_listing(row) for row in self._db.execute(query, (owner, *statuses))]

    def active(self) -> list[Listing]:
        rows = self._db.execute(
            f"SELECT {_LISTING_COLUMNS} FROM listings WHERE status = ? ORDER BY created_at",
            (ACTIVE,),
        )
        return [_listing(row) for row in rows]

    def hosted(self) -> list[Listing]:
        """Every listing whose agent was hosted on Agentverse, whatever its state now."""
        rows = self._db.execute(
            f"SELECT {_LISTING_COLUMNS} FROM listings WHERE host = ? ORDER BY created_at",
            (AGENTVERSE,),
        )
        return [_listing(row) for row in rows]

    def on_agentverse(self) -> list[Listing]:
        """Listings whose hosted agent is still on Agentverse: active ones, and sold or canceled
        ones whose agent hasn't been deleted yet."""
        rows = self._db.execute(
            f"SELECT {_LISTING_COLUMNS} FROM listings WHERE host = ? AND address IS NOT NULL "
            "AND agent_deleted_at IS NULL ORDER BY created_at",
            (AGENTVERSE,),
        )
        return [_listing(row) for row in rows]

    def agent_deleted(self, item_id: str) -> None:
        """Record that a sold or canceled listing's hosted agent was deleted from Agentverse."""
        self._db.execute(
            "UPDATE listings SET agent_deleted_at = ? WHERE item_id = ? AND status IN (?, ?)",
            (self.clock(), item_id, SOLD, CANCELED),
        )

    def reprice(
        self, item_id: str, owner: str, seller_asking: int | None, seller_floor: int | None
    ) -> Listing | None:
        """New seller prices for a proposal. A price not given keeps any earlier seller price;
        the rest is worked out again from the suggestion. Raises PriceError for bad prices."""
        with self._transaction() as db:
            current = self._owned_in(db, item_id, owner, PROPOSED)
            if current is None:
                return None
            if seller_asking is None:
                seller_asking = current.seller_asking_cents
            if seller_floor is None:
                seller_floor = current.seller_floor_cents
            asking, floor = listing_prices(
                current.suggested_asking_cents,
                current.suggested_floor_cents,
                seller_asking,
                seller_floor,
            )
            db.execute(
                "UPDATE listings SET asking_cents = ?, floor_cents = ?, seller_asking_cents = ?, "
                "seller_floor_cents = ?, updated_at = ? WHERE item_id = ?",
                (asking, floor, seller_asking, seller_floor, self.clock(), item_id),
            )
        return self.get(item_id)

    def activate(
        self,
        item_id: str,
        owner: str,
        address: str,
        host: str = BUREAU,
        handle: str | None = None,
    ) -> Listing | None:
        """proposed -> active. Approving twice returns the active listing; anything else (sold,
        canceled, someone else's) returns None."""
        self._db.execute(
            "UPDATE listings SET status = ?, address = ?, host = ?, handle = ?, updated_at = ? "
            "WHERE item_id = ? AND owner = ? AND status = ?",
            (ACTIVE, address, host, handle, self.clock(), item_id, owner, PROPOSED),
        )
        listing = self.get(item_id)
        if listing is None or listing.owner != owner or listing.status != ACTIVE:
            return None
        return listing

    def set_handle(self, item_id: str, handle: str) -> None:
        """Record a hosted item agent's handle, given after it went live."""
        self._db.execute("UPDATE listings SET handle = ? WHERE item_id = ?", (handle, item_id))

    def cancel(self, item_id: str, owner: str) -> Listing | None:
        """proposed or active -> canceled, for the owner only. Open counter-offers lapse."""
        with self._transaction() as db:
            changed = db.execute(
                "UPDATE listings SET status = ?, updated_at = ? "
                "WHERE item_id = ? AND owner = ? AND status IN (?, ?)",
                (CANCELED, self.clock(), item_id, owner, PROPOSED, ACTIVE),
            ).rowcount
            if not changed:
                return None
            db.execute("DELETE FROM counters WHERE item_id = ?", (item_id,))
        return self.get(item_id)

    def _owned_in(
        self, db: sqlite3.Connection, item_id: str, owner: str, status: str
    ) -> Listing | None:
        row = db.execute(
            f"SELECT {_LISTING_COLUMNS} FROM listings "
            "WHERE item_id = ? AND owner = ? AND status = ?",
            (item_id, owner, status),
        ).fetchone()
        return _listing(row) if row else None

    # --- Haggling ---------------------------------------------------------------------------

    def save_counter(
        self,
        item_id: str,
        buyer: str,
        offer_cents: int,
        counter_cents: int,
        rounds: int = 0,
        agreed: bool = False,
    ) -> CounterOffer:
        """This buyer's current counter-offer or agreed price. A newer one replaces any earlier
        one."""
        counter = CounterOffer(
            item_id,
            buyer,
            offer_cents,
            counter_cents,
            self.clock() + COUNTER_TTL,
            rounds,
            agreed,
        )
        self._db.execute(
            f"INSERT OR REPLACE INTO counters ({_COUNTER_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (item_id, buyer, offer_cents, counter_cents, counter.expires_at, rounds, int(agreed)),
        )
        return counter

    def counter(self, item_id: str, buyer: str) -> CounterOffer | None:
        """This buyer's latest counter-offer for the item, expired or not."""
        row = self._db.execute(
            f"SELECT {_COUNTER_COLUMNS} FROM counters WHERE item_id = ? AND buyer = ?",
            (item_id, buyer),
        ).fetchone()
        return _counter(row) if row else None

    def close_deal(
        self, item_id: str, buyer: str, price_cents: int, deal_id: str | None = None
    ) -> DealRecord | None:
        """active -> sold with one deal. Returns None if the item isn't for sale anymore, so
        two buyers can't both get it.

        A hosted item agent closes its own deals and reports them with their deal_id; recording
        the same deal_id again returns the deal already recorded."""
        deal = DealRecord(
            deal_id=deal_id or f"deal-{uuid4().hex[:12]}",
            item_id=item_id,
            buyer=buyer,
            price_cents=price_cents,
            payment_status=NOT_COLLECTED,
            created_at=self.clock(),
        )
        with self._transaction() as db:
            sold = db.execute(
                "UPDATE listings SET status = ?, updated_at = ? WHERE item_id = ? AND status = ?",
                (SOLD, deal.created_at, item_id, ACTIVE),
            ).rowcount
            if not sold:
                existing = self.deal(deal_id) if deal_id else None
                if existing is not None and existing.item_id == item_id:
                    return existing
                return None
            db.execute(
                "INSERT INTO deals (deal_id, item_id, buyer, price_cents, payment_status, "
                "created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    deal.deal_id,
                    item_id,
                    buyer,
                    price_cents,
                    deal.payment_status,
                    deal.created_at,
                ),
            )
            db.execute("DELETE FROM counters WHERE item_id = ?", (item_id,))
        return deal

    def deal_for(self, item_id: str) -> DealRecord | None:
        row = self._db.execute(
            f"SELECT {_DEAL_COLUMNS} FROM deals WHERE item_id = ?", (item_id,)
        ).fetchone()
        return _deal(row) if row else None

    def deal(self, deal_id: str) -> DealRecord | None:
        row = self._db.execute(
            f"SELECT {_DEAL_COLUMNS} FROM deals WHERE deal_id = ?", (deal_id,)
        ).fetchone()
        return _deal(row) if row else None

    def deals(
        self, *, buyer: str | None = None, payment_status: str | None = None
    ) -> list[DealRecord]:
        """Deals, newest first, optionally for one buyer or in one payment state."""
        query, args = f"SELECT {_DEAL_COLUMNS} FROM deals WHERE 1 = 1", []
        if buyer is not None:
            query += " AND buyer = ?"
            args.append(buyer)
        if payment_status is not None:
            query += " AND payment_status = ?"
            args.append(payment_status)
        query += " ORDER BY created_at DESC, rowid DESC"
        return [_deal(row) for row in self._db.execute(query, args)]

    # --- Payments ---------------------------------------------------------------------------

    def start_payment(self, deal_id: str, provider: str, ref: str, url: str) -> DealRecord | None:
        """not_collected -> pending, with the buyer's payment link. None if a link was already
        made (or the deal is gone), so one deal never gets two links."""
        changed = self._db.execute(
            "UPDATE deals SET payment_status = ?, payment_provider = ?, payment_ref = ?, "
            "payment_url = ? WHERE deal_id = ? AND payment_status = ?",
            (PENDING, provider, ref, url, deal_id, NOT_COLLECTED),
        ).rowcount
        return self.deal(deal_id) if changed else None

    def mark_paid(
        self, deal_id: str, commission_cents: int, payout_cents: int
    ) -> DealRecord | None:
        """pending -> paid, recording Yardsale's commission and the seller's payout. None if it
        wasn't pending, so a payment is only counted once."""
        changed = self._db.execute(
            "UPDATE deals SET payment_status = ?, commission_cents = ?, payout_cents = ?, "
            "paid_at = ? WHERE deal_id = ? AND payment_status = ?",
            (PAID, commission_cents, payout_cents, self.clock(), deal_id, PENDING),
        ).rowcount
        return self.deal(deal_id) if changed else None

    def end_payment(self, deal_id: str, status: str) -> DealRecord | None:
        """pending -> expired or rejected: the buyer didn't pay. The item stays sold; the
        seller can list it again."""
        if status not in (EXPIRED, REJECTED):
            raise ValueError(f"not an ending payment status: {status}")
        changed = self._db.execute(
            "UPDATE deals SET payment_status = ? WHERE deal_id = ? AND payment_status IN (?, ?)",
            (status, deal_id, PENDING, NOT_COLLECTED),
        ).rowcount
        return self.deal(deal_id) if changed else None

    # --- Chat conversations -----------------------------------------------------------------

    def load_conversation(self, key: str) -> dict[str, Any]:
        row = self._db.execute("SELECT state FROM conversations WHERE key = ?", (key,)).fetchone()
        return dict(json.loads(row["state"])) if row else {}

    def save_conversation(self, key: str, state: dict[str, Any]) -> None:
        self._db.execute(
            "INSERT OR REPLACE INTO conversations VALUES (?, ?, ?)",
            (key, json.dumps(state), self.clock()),
        )

    # --- Photos -----------------------------------------------------------------------------

    def save_photo(self, key: str, media_type: str, data: bytes) -> None:
        """A listing's prepared photo (key: its item ID), kept for listing it on eBay."""
        self._db.execute("INSERT OR REPLACE INTO photos VALUES (?, ?, ?)", (key, media_type, data))

    def photo(self, key: str) -> tuple[str, bytes] | None:
        """(media type, bytes), or None."""
        row = self._db.execute(
            "SELECT media_type, data FROM photos WHERE key = ?", (key,)
        ).fetchone()
        return (row["media_type"], bytes(row["data"])) if row else None

    def move_photo(self, old_key: str, new_key: str) -> None:
        self._db.execute(
            "INSERT OR REPLACE INTO photos SELECT ?, media_type, data FROM photos WHERE key = ?",
            (new_key, old_key),
        )
        self._db.execute("DELETE FROM photos WHERE key = ?", (old_key,))

    # --- eBay (ebay_seller.py) --------------------------------------------------------------

    def add_ebay_grant(self, state_hash: str, owner: str) -> None:
        """A consent link sent to this owner, waiting for eBay's redirect."""
        self._db.execute(
            "INSERT OR REPLACE INTO ebay_grants VALUES (?, ?, ?)",
            (state_hash, owner, self.clock()),
        )

    def take_ebay_grant(self, state_hash: str, newer_than: float) -> str | None:
        """The owner a consent link was made for, used once. None if unknown or too old."""
        with self._transaction() as db:
            row = db.execute(
                "SELECT owner, created_at FROM ebay_grants WHERE state_hash = ?", (state_hash,)
            ).fetchone()
            db.execute(
                "DELETE FROM ebay_grants WHERE state_hash = ? OR created_at <= ?",
                (state_hash, newer_than),
            )
        if row is None or row["created_at"] <= newer_than:
            return None
        return str(row["owner"])

    def save_ebay_account(
        self,
        owner: str,
        access_token: str,
        access_expires_at: float,
        refresh_token: str,
        refresh_expires_at: float,
    ) -> None:
        self._db.execute(
            "INSERT OR REPLACE INTO ebay_accounts VALUES (?, ?, ?, ?, ?, ?, 0)",
            (
                owner,
                access_token,
                access_expires_at,
                refresh_token,
                refresh_expires_at,
                self.clock(),
            ),
        )

    def ebay_account(self, owner: str) -> EbayAccount | None:
        row = self._db.execute(
            "SELECT owner, access_token, access_expires_at, refresh_token, refresh_expires_at, "
            "connected_at, announced FROM ebay_accounts WHERE owner = ?",
            (owner,),
        ).fetchone()
        if row is None:
            return None
        data = dict(row)
        data["announced"] = bool(data["announced"])
        return EbayAccount(**data)

    def update_ebay_access(self, owner: str, access_token: str, expires_at: float) -> None:
        self._db.execute(
            "UPDATE ebay_accounts SET access_token = ?, access_expires_at = ? WHERE owner = ?",
            (access_token, expires_at, owner),
        )

    def announce_ebay(self, owner: str) -> bool:
        """True once, the first time it's called after the owner connected eBay."""
        return bool(
            self._db.execute(
                "UPDATE ebay_accounts SET announced = 1 WHERE owner = ? AND announced = 0",
                (owner,),
            ).rowcount
        )

    def delete_ebay_account(self, owner: str) -> bool:
        return bool(
            self._db.execute("DELETE FROM ebay_accounts WHERE owner = ?", (owner,)).rowcount
        )

    def add_ebay_listing(
        self, item_id: str, owner: str, ebay_item_id: str, url: str
    ) -> EbayListing:
        now = self.clock()
        record = EbayListing(item_id, owner, ebay_item_id, url, EBAY_ACTIVE, now, now)
        self._db.execute(
            f"INSERT OR REPLACE INTO ebay_listings ({_EBAY_LISTING_COLUMNS}) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (item_id, owner, ebay_item_id, url, EBAY_ACTIVE, now, now),
        )
        return record

    def ebay_listing(self, item_id: str) -> EbayListing | None:
        row = self._db.execute(
            f"SELECT {_EBAY_LISTING_COLUMNS} FROM ebay_listings WHERE item_id = ?", (item_id,)
        ).fetchone()
        return EbayListing(**dict(row)) if row else None

    def ebay_listings(
        self, *, owner: str | None = None, status: str | None = None
    ) -> list[EbayListing]:
        query, args = f"SELECT {_EBAY_LISTING_COLUMNS} FROM ebay_listings WHERE 1 = 1", []
        if owner is not None:
            query += " AND owner = ?"
            args.append(owner)
        if status is not None:
            query += " AND status = ?"
            args.append(status)
        query += " ORDER BY created_at"
        return [EbayListing(**dict(row)) for row in self._db.execute(query, args)]

    def set_ebay_status(self, item_id: str, status: str) -> None:
        self._db.execute(
            "UPDATE ebay_listings SET status = ?, updated_at = ? WHERE item_id = ?",
            (status, self.clock(), item_id),
        )

    def ebay_offer_answered(self, offer_id: str) -> bool:
        row = self._db.execute(
            "SELECT 1 FROM ebay_offers WHERE offer_id = ?", (offer_id,)
        ).fetchone()
        return row is not None

    def record_ebay_answer(self, offer_id: str, item_id: str, action: str, buyer: str) -> None:
        self._db.execute(
            "INSERT OR REPLACE INTO ebay_offers (offer_id, item_id, action, answered_at, buyer) "
            "VALUES (?, ?, ?, ?, ?)",
            (offer_id, item_id, action, self.clock(), buyer),
        )

    def ebay_counters_sent(self, item_id: str, buyer: str) -> int:
        """How many counter-offers this eBay buyer got for the item (eBay allows 5)."""
        row = self._db.execute(
            "SELECT COUNT(*) AS n FROM ebay_offers WHERE item_id = ? AND buyer = ? "
            "AND action = 'counter'",
            (item_id, buyer),
        ).fetchone()
        return int(row["n"])

    # --- Watches ----------------------------------------------------------------------------
    # A standing search over other sites. watch_hits is both the seen-set that stops a listing
    # being reported twice and the queue of findings the buyer hasn't been told about yet.

    def add_watch(self, watch: Watch) -> Watch:
        now = self.clock()
        watch.created_at = now
        self._db.execute(
            f"INSERT INTO watches ({_WATCH_COLUMNS}) VALUES ({', '.join('?' * 11)})",
            (
                watch.watch_id,
                watch.owner,
                watch.query,
                watch.scope,
                watch.budget_cents,
                ",".join(watch.sources),
                watch.every_seconds,
                now,
                watch.expires_at,
                watch.last_run_at,
                int(watch.stopped),
            ),
        )
        return watch

    def watch(self, watch_id: str) -> Watch | None:
        row = self._db.execute(
            f"SELECT {_WATCH_COLUMNS} FROM watches WHERE watch_id = ?", (watch_id,)
        ).fetchone()
        return _watch(row) if row else None

    def watches_for(self, owner: str, running_only: bool = True) -> list[Watch]:
        """One owner's watches, oldest first, so a buyer's numbering stays stable."""
        sql = f"SELECT {_WATCH_COLUMNS} FROM watches WHERE owner = ?"
        if running_only:
            sql += " AND stopped = 0"
        return [_watch(row) for row in self._db.execute(sql + " ORDER BY created_at", (owner,))]

    def due_watches(self, limit: int = 50) -> list[Watch]:
        """Watches that are running, unexpired, and haven't been polled for every_seconds."""
        now = self.clock()
        return [
            _watch(row)
            for row in self._db.execute(
                f"SELECT {_WATCH_COLUMNS} FROM watches WHERE stopped = 0 AND expires_at > ?"
                " AND (last_run_at IS NULL OR last_run_at + every_seconds <= ?)"
                " ORDER BY last_run_at IS NOT NULL, last_run_at LIMIT ?",
                (now, now, limit),
            )
        ]

    def mark_polled(self, watch_id: str) -> None:
        self._db.execute(
            "UPDATE watches SET last_run_at = ? WHERE watch_id = ?", (self.clock(), watch_id)
        )

    def stop_watch(self, watch_id: str, owner: str) -> Watch | None:
        """Only the owner may stop their own watch. Returns None if it isn't theirs."""
        changed = self._db.execute(
            "UPDATE watches SET stopped = 1 WHERE watch_id = ? AND owner = ? AND stopped = 0",
            (watch_id, owner),
        ).rowcount
        return self.watch(watch_id) if changed else None

    def expire_watches(self) -> int:
        """Stop watches whose week is up. Returns how many were stopped."""
        return self._db.execute(
            "UPDATE watches SET stopped = 1 WHERE stopped = 0 AND expires_at <= ?",
            (self.clock(),),
        ).rowcount

    def record_hits(self, watch_id: str, listings: list[ExternalListing]) -> list[ExternalListing]:
        """Save findings, returning only the ones not seen before. Repeats are ignored, so a
        listing that stays up for days is reported once."""
        now = self.clock()
        fresh: list[ExternalListing] = []
        with self._transaction() as db:
            for listing in listings:
                added = db.execute(
                    "INSERT OR IGNORE INTO watch_hits"
                    " (watch_id, source, external_id, found_at, told, listing_json)"
                    " VALUES (?, ?, ?, ?, 0, ?)",
                    (
                        watch_id,
                        listing.source,
                        listing.external_id,
                        now,
                        json.dumps(asdict(listing)),
                    ),
                ).rowcount
                if added:
                    fresh.append(listing)
        return fresh

    def untold_hits(self, watch_id: str) -> list[ExternalListing]:
        """Findings the buyer hasn't been shown, oldest first."""
        return [
            ExternalListing(**json.loads(row["listing_json"]))
            for row in self._db.execute(
                "SELECT listing_json FROM watch_hits WHERE watch_id = ? AND told = 0"
                " ORDER BY found_at",
                (watch_id,),
            )
        ]

    def count_untold(self, owner: str) -> int:
        row = self._db.execute(
            "SELECT COUNT(*) AS n FROM watch_hits JOIN watches USING (watch_id)"
            " WHERE watches.owner = ? AND watch_hits.told = 0",
            (owner,),
        ).fetchone()
        return int(row["n"])

    def mark_told(self, watch_id: str) -> None:
        self._db.execute("UPDATE watch_hits SET told = 1 WHERE watch_id = ?", (watch_id,))
