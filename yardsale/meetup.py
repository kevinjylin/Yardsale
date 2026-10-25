"""Where two students should meet: one good public place on their campus.

Neither student is asked where they live. The meeting place is picked from the campus itself:
somewhere central, public, and busy, which on a campus is a short walk from anywhere anyway.
That keeps signing up to one step, and it means nobody has to say which building they're in to
a stranger.

There's no ranking here. Claude names a campus's places best first (see campus.py) and the store
keeps that order, so the answer is the first place a student can actually be sent to. An earlier
version scored places by distance from the centroid of the dorms, with a per-kind detour
allowance; it existed to sort a few hundred raw OpenStreetMap tags, and went when they did.

The one judgement left is the one that must never be got wrong: never send a student to where
anybody lives.
"""

from __future__ import annotations

from dataclasses import dataclass

# Never proposed as a meeting place: somewhere someone lives.
PRIVATE_KINDS = frozenset({"dormitory", "residential", "house", "apartments"})


@dataclass(frozen=True)
class Spot:
    """A point on a campus: a building, or a place two students could meet.

    Coordinates default to 0.0 because a place Claude names arrives without them, and a name is
    what a student is told either way.
    """

    name: str
    latitude: float = 0.0
    longitude: float = 0.0
    kind: str = "landmark"
    indoor: bool = False
    opening_hours: str = ""

    @property
    def private(self) -> bool:
        return self.kind in PRIVATE_KINDS


@dataclass(frozen=True)
class Meetup:
    """A place to meet. A wrapper rather than a bare Spot, so what a meetup carries besides the
    place — a time, a confirmation — can be added without changing every caller."""

    spot: Spot


def meetable(places: list[Spot]) -> list[Spot]:
    """Places two students could actually meet: nowhere anyone lives."""
    return [place for place in places if not place.private]


def choose(places: list[Spot]) -> Meetup | None:
    """The one place to send two students, or None when a campus has nowhere public."""
    public = meetable(places)
    return Meetup(spot=public[0]) if public else None


def options(places: list[Spot], limit: int = 3) -> list[Meetup]:
    """A few places, so a student can pick another one."""
    return [Meetup(spot=place) for place in meetable(places)[:limit]]


def describe(meetup: Meetup) -> str:
    """How a meetup reads in the chat."""
    where = meetup.spot.name
    if meetup.spot.opening_hours:
        return f"{where} (open {meetup.spot.opening_hours})"
    return where
