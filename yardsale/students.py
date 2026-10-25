"""Signing a student up, placing them on campus, and keeping trades inside one school.

The flow a student goes through:

1. They give a school email. A six-digit code goes to that address and nothing is trusted yet.
2. They type the code back. Now their school is proved, and they can buy and sell.
3. They name their building ("I'm in Unit 1"). That's matched against the school's campus and
   kept, so meeting points can be worked out. It's never shown to another student.

The rule the whole campus product rests on: `visible_to` and `may_trade` only ever allow a
listing's owner and a buyer at the *same verified school*. An unverified student sees nothing,
so the trust claim ("you're meeting someone from your own university") can't be sidestepped by
skipping verification.

Nothing here sends mail. `begin` returns the code and the caller hands it to an injected sender,
so tests and offline runs never send anything.
"""

from __future__ import annotations

import logging

from yardsale import campus, schools
from yardsale.meetup import Meetup, Spot, choose, options
from yardsale.schools import CODE_TTL, MAX_ATTEMPTS, SchoolError
from yardsale.store import Listing, Store, Student

log = logging.getLogger("yardsale.students")

NOT_A_STUDENT = (
    "Yardsale on campus is for students trading with their own classmates. Send me your school "
    "email, like yours@berkeley.edu, and I'll check it."
)
NOT_VERIFIED = "Type the six-digit code I emailed you, and then you can buy and sell here."
CODE_EXPIRED = "That code has expired. Send me your school email again for a new one."
CODE_WRONG = "That code isn't right."
CODE_DEAD = "Too many wrong codes. Send me your school email again for a new one."
NO_BUILDING = (
    "Tell me which building you're in, like \"I'm in Unit 1\", and I'll put meetups halfway "
    "between you and the other student."
)


class StudentError(ValueError):
    """Something a student needs to fix. The message is shown to them."""


# --- Verification ---------------------------------------------------------------------------


def begin(store: Store, owner: str, email: str) -> tuple[Student, str]:
    """Start verification. Returns the student record and the code to email them.

    The code is returned rather than sent, so the caller decides how mail goes out, and a test
    can read it. Only its hash is stored.
    """
    address = schools.normalise_email(email)
    school = schools.school_key(address)  # raises SchoolError for a non-school address
    code = schools.new_code()
    student = store.save_code(
        owner, address, school, schools.hash_code(address, code), store.clock() + CODE_TTL
    )
    log.info(f"{owner}: verification started for {school}")
    return student, code


def confirm(store: Store, owner: str, code: str) -> Student:
    """Check a code and verify the student. Raises StudentError with what to do next."""
    student = store.student(owner)
    if student is None:
        raise StudentError(NOT_A_STUDENT)
    if student.verified:
        return student
    if not student.code_hash or student.code_expires_at is None:
        raise StudentError(CODE_EXPIRED)
    if student.code_expires_at <= store.clock():
        store.clear_code(owner)
        raise StudentError(CODE_EXPIRED)
    if not schools.code_matches(student.email, code, student.code_hash):
        if store.count_attempt(owner) >= MAX_ATTEMPTS:
            store.clear_code(owner)
            raise StudentError(CODE_DEAD)
        raise StudentError(CODE_WRONG)
    verified = store.mark_verified(owner)
    if verified is None:  # verified by another message in between; either way they're in
        verified = store.student(owner)
        assert verified is not None
    log.info(f"{owner}: verified at {verified.school}")
    return verified


def verified_student(store: Store, owner: str) -> Student:
    """The student, if they've proved their school. Raises StudentError otherwise."""
    student = store.student(owner)
    if student is None:
        raise StudentError(NOT_A_STUDENT)
    if not student.verified:
        raise StudentError(NOT_VERIFIED)
    return student


# --- Same school ----------------------------------------------------------------------------


def school_of(store: Store, owner: str) -> str:
    """The verified school of a conversation, or "" when it hasn't got one."""
    student = store.student(owner)
    return student.school if student is not None and student.verified else ""


def visible_to(store: Store, listing: Listing, owner: str) -> bool:
    """Whether a student may see a listing at all.

    Their own listings always. Otherwise only a listing whose seller is a verified student at
    the same school. An unverified student sees nothing.
    """
    if listing.owner == owner:
        return True
    mine = school_of(store, owner)
    if not mine:
        return False
    return schools.same_school(mine, school_of(store, listing.owner))


def listings_for(store: Store, listings: list[Listing], owner: str) -> list[Listing]:
    """Only the listings this student is allowed to see. Buyer search runs through here."""
    return [listing for listing in listings if visible_to(store, listing, owner)]


def may_trade(store: Store, listing: Listing, owner: str) -> bool:
    """Whether a student may offer on a listing: same school, and not their own item."""
    return listing.owner != owner and visible_to(store, listing, owner)


def check_may_trade(store: Store, listing: Listing, owner: str) -> None:
    """Raise StudentError explaining why a student can't buy this."""
    if listing.owner == owner:
        raise StudentError("That's your own listing.")
    verified_student(store, owner)  # raises if they haven't proved a school
    if not visible_to(store, listing, owner):
        raise StudentError(
            "That item is at a different university. Yardsale on campus keeps trades inside "
            "one school, so you can meet in person."
        )


# --- Placing a student on campus ------------------------------------------------------------


async def warm_campus(
    store: Store,
    school: str,
    find: campus.FindCampus = campus.find_and_load,
) -> int:
    """Make sure a school's campus is cached, fetching it once if not. Returns how many places
    it has. Safe to call often: a school already in the store costs one query."""
    if store.campus_loaded(school):
        return store.count_campus(school)
    places = await find(school)
    if not places:
        return 0
    kept = store.save_campus(school, places)
    log.info(f"{school}: cached {kept} campus places")
    return kept


async def warm_known_campuses(store: Store, find: campus.FindCampus = campus.find_and_load) -> int:
    """Load the campus of every school that has a verified student. Runs on an interval, so a
    school picked up between restarts still gets mapped."""
    loaded = 0
    for school in store.schools_with_students():
        if not store.campus_loaded(school):
            loaded += await warm_campus(store, school, find)
    return loaded


def set_building(store: Store, owner: str, named: str) -> Spot:
    """Match what a student typed against their campus and remember it.

    Raises StudentError when the campus isn't loaded or nothing matches, because a wrong
    building would send them to the wrong side of campus.
    """
    student = verified_student(store, owner)
    places = store.campus(student.school)
    if not places:
        raise StudentError(
            "I don't have a map of your campus yet, so I can't place you. Try again shortly."
        )
    spot = campus.match_building(places, named)
    if spot is None:
        raise StudentError(
            f"I couldn't find \"{named.strip()}\" on your campus. Try the building's full name."
        )
    store.set_building(owner, spot.name, spot.latitude, spot.longitude)
    log.info(f"{owner}: placed at {spot.name}")
    return spot


def where_to_meet(store: Store, seller_owner: str, buyer_owner: str) -> Meetup:
    """A public place between two students' buildings. Raises StudentError if either is unplaced.

    Neither student's building is in the result, only the meeting place.
    """
    seller, buyer = store.student(seller_owner), store.student(buyer_owner)
    for who in (seller, buyer):
        if who is None or not who.placed:
            raise StudentError(NO_BUILDING)
    assert seller is not None and buyer is not None
    first, second = seller.spot(), buyer.spot()
    assert first is not None and second is not None
    places = store.campus(seller.school)
    meeting = choose(first, second, places)
    if meeting is None:
        raise StudentError(
            "I couldn't find a public spot between you two. Agree a place in the chat instead."
        )
    return meeting


def other_places(store: Store, seller_owner: str, buyer_owner: str, limit: int = 3) -> list[Meetup]:
    """A few places to choose from, best first. Empty when either student is unplaced."""
    seller, buyer = store.student(seller_owner), store.student(buyer_owner)
    if seller is None or buyer is None or not seller.placed or not buyer.placed:
        return []
    first, second = seller.spot(), buyer.spot()
    assert first is not None and second is not None
    return options(first, second, store.campus(seller.school), limit=limit)


__all__ = [
    "CODE_DEAD",
    "CODE_EXPIRED",
    "CODE_WRONG",
    "NOT_A_STUDENT",
    "NOT_VERIFIED",
    "NO_BUILDING",
    "SchoolError",
    "StudentError",
    "begin",
    "check_may_trade",
    "confirm",
    "listings_for",
    "may_trade",
    "other_places",
    "school_of",
    "set_building",
    "verified_student",
    "visible_to",
    "where_to_meet",
]
