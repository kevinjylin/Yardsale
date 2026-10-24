"""Listing states, ownership, deals, and persistence in the SQLite store."""

from __future__ import annotations

import sqlite3
import stat
from pathlib import Path

import pytest

from yardsale.models import Appraisal, ItemDetails
from yardsale.pricing import PriceError
from yardsale.store import (
    ACTIVE,
    BUREAU,
    CANCELED,
    COUNTER_TTL,
    EXPIRED,
    NOT_COLLECTED,
    PAID,
    PENDING,
    PROPOSED,
    SOLD,
    Listing,
    Store,
)

ITEM = ItemDetails(
    title="Trek FX 2",
    brand="Trek",
    model="FX 2",
    category="Bicycles",
    condition="good",
    details=["medium"],
    search_query="Trek FX 2",
    generic=False,
    confidence="high",
)
APPRAISAL = Appraisal(
    request_id="r",
    comps_considered=5,
    matches=[],
    basis="5 sales",
    confidence="high",
    asking_cents=34000,
    floor_cents=31000,
    low_cents=31000,
    high_cents=37000,
)


def proposal(item_id: str = "item1", owner: str = "alice", ref: str = "ref1") -> Listing:
    return Listing(
        item_id=item_id,
        owner=owner,
        seller_id="payout-alice",
        listing_ref=ref,
        status=PROPOSED,
        title=ITEM.title,
        asking_cents=34000,
        floor_cents=31000,
        suggested_asking_cents=34000,
        suggested_floor_cents=31000,
        item=ITEM,
        appraisal=APPRAISAL,
    )


class Clock:
    def __init__(self) -> None:
        self.now = 1_000_000.0

    def __call__(self) -> float:
        return self.now


def test_a_proposal_is_not_for_sale(store: Store) -> None:
    store.add_listing(proposal())
    assert store.active() == []
    saved = store.get("item1")
    assert saved is not None and saved.status == PROPOSED
    assert saved.item == ITEM and saved.appraisal == APPRAISAL


def test_the_same_ref_returns_the_same_listing(store: Store) -> None:
    store.add_listing(proposal())
    again = store.add_listing(proposal(item_id="item2"))
    assert again.item_id == "item1"
    assert store.get("item2") is None
    # Another seller can reuse the ref.
    assert store.add_listing(proposal(item_id="item3", owner="bob")).item_id == "item3"


def test_only_the_owner_approves_and_approving_twice_is_harmless(store: Store) -> None:
    store.add_listing(proposal())
    assert store.activate("item1", "mallory", "agent1qaddr") is None
    assert store.get("item1").status == PROPOSED  # type: ignore[union-attr]
    first = store.activate("item1", "alice", "agent1qaddr")
    second = store.activate("item1", "alice", "agent1qother")
    assert first is not None and second is not None
    assert second.status == ACTIVE and second.address == "agent1qaddr"
    assert [listing.item_id for listing in store.active()] == ["item1"]


def test_reprice_recomputes_what_the_seller_did_not_set(store: Store) -> None:
    store.add_listing(proposal())
    changed = store.reprice("item1", "alice", 36000, None)
    assert changed is not None
    assert (changed.asking_cents, changed.floor_cents) == (36000, 31000)
    assert (changed.asking_from, changed.floor_from) == ("seller", "appraisal")
    changed = store.reprice("item1", "alice", None, 32000)
    assert changed is not None
    assert (changed.asking_cents, changed.floor_cents) == (36000, 32000)  # keeps the $360
    assert changed.floor_from == "seller"


def test_reprice_refuses_conflicting_prices_and_other_owners(store: Store) -> None:
    store.add_listing(proposal())
    store.reprice("item1", "alice", None, 32000)
    with pytest.raises(PriceError):
        store.reprice("item1", "alice", 30000, None)  # below the seller's own floor
    assert store.get("item1").asking_cents == 34000  # type: ignore[union-attr]
    assert store.reprice("item1", "mallory", 1, None) is None


def test_reprice_only_changes_proposals(store: Store) -> None:
    store.add_listing(proposal())
    store.activate("item1", "alice", "agent1qaddr")
    assert store.reprice("item1", "alice", 50000, None) is None


def test_cancel(store: Store) -> None:
    store.add_listing(proposal())
    assert store.cancel("item1", "mallory") is None
    canceled = store.cancel("item1", "alice")
    assert canceled is not None and canceled.status == CANCELED
    assert store.cancel("item1", "alice") is None
    assert store.activate("item1", "alice", "agent1qaddr") is None


def test_a_listing_sells_once(store: Store) -> None:
    store.add_listing(proposal())
    store.activate("item1", "alice", "agent1qaddr")
    store.save_counter("item1", "bob", 20000, 31000)
    deal = store.close_deal("item1", "carol", 33000)
    assert deal is not None and deal.payment_status == "not_collected"
    assert store.close_deal("item1", "bob", 34000) is None
    assert store.get("item1").status == SOLD  # type: ignore[union-attr]
    assert store.deal_for("item1") == deal
    assert store.counter("item1", "bob") is None  # open counters lapse
    assert store.active() == []
    assert store.cancel("item1", "alice") is None


def test_a_proposal_cannot_be_sold(store: Store) -> None:
    store.add_listing(proposal())
    assert store.close_deal("item1", "bob", 34000) is None


def test_counters_expire() -> None:
    clock = Clock()
    db = Store(clock=clock)
    db.add_listing(proposal())
    counter = db.save_counter("item1", "bob", 20000, 31000)
    assert counter.expires_at == clock.now + COUNTER_TTL
    assert counter.expires_iso.endswith("+00:00")
    newer = db.save_counter("item1", "bob", 25000, 32000)
    assert db.counter("item1", "bob") == newer  # replaces the earlier one


def test_state_survives_reopening(tmp_path: Path) -> None:
    path = tmp_path / "listings.sqlite3"
    db = Store(path)
    db.add_listing(proposal())
    db.activate("item1", "alice", "agent1qaddr")
    db.add_listing(proposal(item_id="item2", ref="ref2"))
    db.save_conversation("chat1", {"seen_photos": ["https://x/1.jpg"]})
    db.close()

    reopened = Store(path)
    assert [listing.address for listing in reopened.active()] == ["agent1qaddr"]
    assert [listing.item_id for listing in reopened.owned("alice", PROPOSED)] == ["item2"]
    assert reopened.load_conversation("chat1") == {"seen_photos": ["https://x/1.jpg"]}
    assert reopened.load_conversation("unknown") == {}
    reopened.close()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600  # floors and seller IDs are private


def test_owned_is_newest_first(store: Store) -> None:
    clock = Clock()
    db = Store(clock=clock)
    db.add_listing(proposal("old", ref="a"))
    clock.now += 1
    db.add_listing(proposal("new", ref="b"))
    assert [listing.item_id for listing in db.owned("alice")] == ["new", "old"]
    assert db.owned("bob") == []


# The schema before hosting, multi-round haggling, and payments were added.
OLD_SCHEMA = """
CREATE TABLE listings (
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
CREATE TABLE counters (
    item_id TEXT NOT NULL REFERENCES listings (item_id),
    buyer TEXT NOT NULL,
    offer_cents INTEGER NOT NULL,
    counter_cents INTEGER NOT NULL,
    expires_at REAL NOT NULL,
    PRIMARY KEY (item_id, buyer)
);
CREATE TABLE deals (
    deal_id TEXT PRIMARY KEY,
    item_id TEXT NOT NULL UNIQUE REFERENCES listings (item_id),
    buyer TEXT NOT NULL,
    price_cents INTEGER NOT NULL CHECK (price_cents > 0),
    payment_status TEXT NOT NULL DEFAULT 'not_collected',
    created_at REAL NOT NULL
);
CREATE TABLE conversations (key TEXT PRIMARY KEY, state TEXT NOT NULL, updated_at REAL NOT NULL);
INSERT INTO listings VALUES ('old', 'o', 's', 'r', 'sold', 'Old lamp', 4000, 3000,
    NULL, NULL, NULL, NULL, NULL, NULL, 'agent1qold', 1, 1);
INSERT INTO listings VALUES ('live', 'o', 's', 'r2', 'active', 'Old chair', 5000, 4000,
    NULL, NULL, NULL, NULL, NULL, NULL, 'agent1qlive', 1, 1);
INSERT INTO counters VALUES ('live', 'agent:bob', 3000, 4500, 99999999999);
INSERT INTO deals VALUES ('deal-old', 'old', 'agent:bob', 3500, 'not_collected', 2);
"""


def test_an_old_database_is_upgraded_in_place(tmp_path: Path) -> None:
    path = tmp_path / "old.sqlite3"
    with sqlite3.connect(path) as db:
        db.executescript(OLD_SCHEMA)
    store = Store(path)
    live = store.get("live")
    assert live is not None and live.host == BUREAU
    counter = store.counter("live", "agent:bob")
    assert counter is not None and (counter.rounds, counter.agreed) == (0, False)
    deal = store.deal("deal-old")
    assert deal is not None and deal.payment_status == NOT_COLLECTED
    assert deal.payment_url is None and deal.commission_cents is None
    started = store.start_payment("deal-old", "fake", "ref", "https://pay.example.test/x")
    assert started is not None and started.payment_status == PENDING
    store.close()
    Store(path).close()  # opening it again changes nothing


def test_payment_states_only_move_forward(store: Store) -> None:
    store.add_listing(
        Listing(
            item_id="lamp",
            owner="o",
            seller_id="s",
            listing_ref="r",
            status=PROPOSED,
            title="Lamp",
            asking_cents=4000,
            floor_cents=3000,
        )
    )
    store.activate("lamp", "o", "agent1qlamp")
    deal = store.close_deal("lamp", "chat:abc", 3500, "deal-1")
    assert deal is not None and deal.deal_id == "deal-1"
    assert store.close_deal("lamp", "chat:abc", 3500, "deal-1") == deal  # recorded once
    assert store.close_deal("lamp", "chat:xyz", 3600, "deal-2") is None
    assert store.mark_paid("deal-1", 175, 3325) is None  # no link yet
    assert store.start_payment("deal-1", "fake", "ref", "https://pay.example.test/1") is not None
    assert store.start_payment("deal-1", "fake", "ref2", "https://pay.example.test/2") is None
    paid = store.mark_paid("deal-1", 175, 3325)
    assert paid is not None and paid.payment_status == PAID and paid.paid_at is not None
    assert store.end_payment("deal-1", EXPIRED) is None  # paid stays paid
    assert [d.deal_id for d in store.deals(buyer="chat:abc")] == ["deal-1"]
    assert store.deals(payment_status=PENDING) == []
