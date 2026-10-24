"""Durable state: listings, counter-offers, deals, and chat conversations, in one SQLite file.

The Market, the item agents, and the chat share one connection in one event loop. Every status
change is a conditional UPDATE inside a transaction, so a listing is approved, canceled, or sold
at most once however messages interleave.

No seeds are stored here. An item agent's address comes from the master seed and the item ID, so
re-creating the agents for active listings after a restart gives the same addresses.

Listing states:

    proposed --approve--> active --deal--> sold
        |                    |
        +------cancel--------+-----------> canceled
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from yardsale.models import Appraisal, ItemDetails
from yardsale.pricing import listing_prices

PROPOSED, ACTIVE, SOLD, CANCELED = "proposed", "active", "sold", "canceled"
COUNTER_TTL = 24 * 3600.0  # seconds a counter-offer can be accepted

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
    UNIQUE (owner, listing_ref)
);
CREATE INDEX IF NOT EXISTS listings_by_status ON listings (status);
CREATE TABLE IF NOT EXISTS counters (
    item_id TEXT NOT NULL REFERENCES listings (item_id),
    buyer TEXT NOT NULL,
    offer_cents INTEGER NOT NULL,
    counter_cents INTEGER NOT NULL,
    expires_at REAL NOT NULL,
    PRIMARY KEY (item_id, buyer)
);
CREATE TABLE IF NOT EXISTS deals (
    deal_id TEXT PRIMARY KEY,
    item_id TEXT NOT NULL UNIQUE REFERENCES listings (item_id),
    buyer TEXT NOT NULL,
    price_cents INTEGER NOT NULL CHECK (price_cents > 0),
    payment_status TEXT NOT NULL DEFAULT 'not_collected',
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS conversations (
    key TEXT PRIMARY KEY,
    state TEXT NOT NULL,
    updated_at REAL NOT NULL
);
"""


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
    address: str | None = None  # the item agent's, once approved
    created_at: float = 0.0
    updated_at: float = 0.0

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
    offer_cents: int
    counter_cents: int
    expires_at: float

    @property
    def expires_iso(self) -> str:
        return datetime.fromtimestamp(self.expires_at, UTC).isoformat(timespec="seconds")


@dataclass
class DealRecord:
    deal_id: str
    item_id: str
    buyer: str
    price_cents: int
    payment_status: str  # not_collected: payments aren't built
    created_at: float


_LISTING_COLUMNS = (
    "item_id, owner, seller_id, listing_ref, status, title, asking_cents, floor_cents, "
    "seller_asking_cents, seller_floor_cents, suggested_asking_cents, suggested_floor_cents, "
    "item_json, appraisal_json, address, created_at, updated_at"
)


def _listing(row: sqlite3.Row) -> Listing:
    data = dict(row)
    item_json, appraisal_json = data.pop("item_json"), data.pop("appraisal_json")
    return Listing(
        **data,
        item=ItemDetails.model_validate_json(item_json) if item_json else None,
        appraisal=Appraisal.model_validate_json(appraisal_json) if appraisal_json else None,
    )


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
        if self.path != ":memory:":
            os.chmod(self.path, 0o600)  # seller IDs and floor prices

    def close(self) -> None:
        self._db.close()

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
                f"INSERT INTO listings ({_LISTING_COLUMNS}) VALUES ({', '.join('?' * 17)})",
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

    def activate(self, item_id: str, owner: str, address: str) -> Listing | None:
        """proposed -> active. Approving twice returns the active listing; anything else (sold,
        canceled, someone else's) returns None."""
        self._db.execute(
            "UPDATE listings SET status = ?, address = ?, updated_at = ? "
            "WHERE item_id = ? AND owner = ? AND status = ?",
            (ACTIVE, address, self.clock(), item_id, owner, PROPOSED),
        )
        listing = self.get(item_id)
        if listing is None or listing.owner != owner or listing.status != ACTIVE:
            return None
        return listing

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
        self, item_id: str, buyer: str, offer_cents: int, counter_cents: int
    ) -> CounterOffer:
        """This buyer's current counter-offer. A newer one replaces any earlier one."""
        counter = CounterOffer(
            item_id, buyer, offer_cents, counter_cents, self.clock() + COUNTER_TTL
        )
        self._db.execute(
            "INSERT OR REPLACE INTO counters VALUES (?, ?, ?, ?, ?)",
            (item_id, buyer, offer_cents, counter_cents, counter.expires_at),
        )
        return counter

    def counter(self, item_id: str, buyer: str) -> CounterOffer | None:
        """This buyer's latest counter-offer for the item, expired or not."""
        row = self._db.execute(
            "SELECT item_id, buyer, offer_cents, counter_cents, expires_at FROM counters "
            "WHERE item_id = ? AND buyer = ?",
            (item_id, buyer),
        ).fetchone()
        return CounterOffer(**dict(row)) if row else None

    def close_deal(self, item_id: str, buyer: str, price_cents: int) -> DealRecord | None:
        """active -> sold with one deal. Returns None if the item isn't for sale anymore, so
        two buyers can't both get it."""
        deal = DealRecord(
            deal_id=f"deal-{uuid4().hex[:12]}",
            item_id=item_id,
            buyer=buyer,
            price_cents=price_cents,
            payment_status="not_collected",
            created_at=self.clock(),
        )
        with self._transaction() as db:
            sold = db.execute(
                "UPDATE listings SET status = ?, updated_at = ? WHERE item_id = ? AND status = ?",
                (SOLD, deal.created_at, item_id, ACTIVE),
            ).rowcount
            if not sold:
                return None
            db.execute(
                "INSERT INTO deals VALUES (?, ?, ?, ?, ?, ?)",
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
            "SELECT deal_id, item_id, buyer, price_cents, payment_status, created_at FROM deals "
            "WHERE item_id = ?",
            (item_id,),
        ).fetchone()
        return DealRecord(**dict(row)) if row else None

    # --- Chat conversations -----------------------------------------------------------------

    def load_conversation(self, key: str) -> dict[str, Any]:
        row = self._db.execute("SELECT state FROM conversations WHERE key = ?", (key,)).fetchone()
        return dict(json.loads(row["state"])) if row else {}

    def save_conversation(self, key: str, state: dict[str, Any]) -> None:
        self._db.execute(
            "INSERT OR REPLACE INTO conversations VALUES (?, ?, ?)",
            (key, json.dumps(state), self.clock()),
        )
