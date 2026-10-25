"""Signing a student up, and keeping trades inside one school.

Two steps, and the second is optional:

1. They give a school email. The school is whatever it ends in, and that's it: they're in.
2. When they trade, the meeting point comes from their campus, not from them.

The rule the campus product rests on: `visible_to` and `may_trade` only allow a listing's owner
and someone at the *same* school. Someone who hasn't given a school email sees nothing.

Nothing is verified. The school is a claim (see schools.py), so this says which school a
conversation *says* it's at, not which one it's really at.
"""

from __future__ import annotations

import logging

from yardsale import campus, maps, schools
from yardsale.meetup import Meetup, Spot, choose, options
from yardsale.store import Listing, Store, Student

log = logging.getLogger("yardsale.students")

NOT_A_STUDENT = (
    "Yardsale on campus is for students trading with their own classmates. Send me your school "
    "email, like yours@berkeley.edu, and I'll set you up."
)
NO_CAMPUS = (
    "I don't have a map of your campus yet, so I can't suggest a meeting spot. Agree a place in "
    "the chat and you're fine."
)


class StudentError(ValueError):
    """Something a student needs to fix. The message is shown to them."""


# --- Signing up -----------------------------------------------------------------------------


def join(store: Store, owner: str, email: str) -> Student:
    """Take a school email and sign the conversation up.

    Raises SchoolError (from schools.school_key) for an address with no school in it, because a
    same-school marketplace can't place someone whose school it doesn't know.
    """
    address = schools.normalise_email(email)
    school = schools.school_key(address)
    student = store.enrol(owner, address, school)
    log.info(f"{owner}: joined at {school}")
    return student


def enrolled(store: Store, owner: str) -> Student:
    """The student, if this conversation has given a school email. Raises StudentError if not."""
    student = store.student(owner)
    if student is None:
        raise StudentError(NOT_A_STUDENT)
    return student


# --- Same school ----------------------------------------------------------------------------


def school_of(store: Store, owner: str) -> str:
    """The school a conversation gave, or "" when it hasn't given one."""
    student = store.student(owner)
    return student.school if student is not None else ""


def visible_to(store: Store, listing: Listing, owner: str) -> bool:
    """Whether a student may see a listing at all.

    Their own listings always. Otherwise only a listing whose seller is at the same school.
    Someone who hasn't given a school email sees nothing.
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
    enrolled(store, owner)  # raises if they haven't given a school email
    if not visible_to(store, listing, owner):
        raise StudentError(
            "That item is at a different university. Yardsale on campus keeps trades inside "
            "one school, so you can meet in person."
        )


# --- Campuses and meeting points ------------------------------------------------------------


async def find_campus(school: str) -> list[Spot]:
    """Claude names a school's meeting spots, then Google puts them on the map.

    Two steps because they answer different questions. Claude knows that you meet at Sproul
    Plaza at Berkeley, which is the part a chat message needs. Google knows where Sproul Plaza
    is, which is what turns it into a link. Without a Google key the names come through on their
    own and the spots keep no coordinates, which costs nothing a student would notice.
    """
    spots = await campus.find_and_load(school)
    if not spots:
        return spots
    return await maps.locate_all(spots, campus.school_label_for(school))


async def warm_campus(store: Store, school: str, find: campus.FindCampus = find_campus) -> int:
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


async def warm_known_campuses(store: Store, find: campus.FindCampus = find_campus) -> int:
    """Load the campus of every school someone has signed up from. Runs on an interval, so a
    school picked up between restarts still gets mapped."""
    loaded = 0
    for school in store.schools_with_students():
        if not store.campus_loaded(school):
            loaded += await warm_campus(store, school, find)
    return loaded


def where_to_meet(store: Store, school: str) -> Meetup:
    """A public place on campus for two of its students to meet.

    Neither student is asked where they are: it's one central spot on their own campus, which on
    a campus is a short walk from anywhere. Raises StudentError when the campus isn't mapped.
    """
    meeting = choose(store.campus(school))
    if meeting is None:
        raise StudentError(NO_CAMPUS)
    return meeting


def other_places(store: Store, school: str, limit: int = 3) -> list[Meetup]:
    """A few spots on campus to choose from, best first. Empty when it isn't mapped."""
    return options(store.campus(school), limit=limit)


__all__ = [
    "NOT_A_STUDENT",
    "NO_CAMPUS",
    "StudentError",
    "check_may_trade",
    "enrolled",
    "join",
    "listings_for",
    "may_trade",
    "other_places",
    "school_of",
    "visible_to",
    "warm_campus",
    "warm_known_campuses",
    "where_to_meet",
]
