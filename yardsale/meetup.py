"""Where two students should meet: one good public place on their campus.

Neither student is asked where they live. The meeting place is picked from the campus itself:
somewhere central, public, and busy, which on a campus is a short walk from anywhere anyway.
That keeps signing up to one step, and it means nobody has to say which building they're in to
a stranger.

Central means near the middle of where students actually are, so the centre is the average of
the campus's dormitories when it has any, and of everything when it doesn't. A place's kind is
then worth a detour, in metres: a busy cafe you can wait inside beats a closer patch of grass,
because whoever arrives first needs somewhere to stand. "Public and busy" is the property that
matters when two people who haven't met are meeting.

Distances are straight-line times a detour factor, which is close enough on a campus and needs
no routing service.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

EARTH_RADIUS_M = 6_371_000.0
# Walking never follows a straight line. Paths, buildings, and crossings add roughly a third on
# a campus, which is accurate enough to rank places and needs no routing API.
DETOUR = 1.3
WALK_METRES_PER_MINUTE = 80.0  # an unhurried walk

# What each kind of place is worth going further from the centre for, in metres. Indoors,
# staffed, and busy beat open ground.
KIND_BONUS = {
    "student_union": 400.0,
    "cafe": 360.0,
    "library": 340.0,
    "food_court": 300.0,
    "plaza": 180.0,
    "landmark": 160.0,
    "park": 120.0,
    "bench": 40.0,
}
# Never proposed as a meeting place: somewhere someone lives.
PRIVATE_KINDS = frozenset({"dormitory", "residential", "house", "apartments"})
# OpenStreetMap tags a departmental reading room, a herbarium, and a map collection all as
# libraries. They're real, and they're the wrong answer: a stranger can't find them, they're
# often inside a building that needs a card, and nobody waits in an archive. A real run on
# Berkeley put "Anthropology Reading Room" and "Environmental Design Archives" in the top five.
NOT_A_MEETING_PLACE = (
    "archive",
    "reading room",
    "collection",
    "herbarium",
    "storage",
    "annex",
    "stacks",
    "lab",
    "office",
)


@dataclass(frozen=True)
class Spot:
    """A point on a campus: a building, or a place two students could meet."""

    name: str
    latitude: float
    longitude: float
    kind: str = "landmark"
    indoor: bool = False
    opening_hours: str = ""

    @property
    def private(self) -> bool:
        return self.kind in PRIVATE_KINDS


@dataclass(frozen=True)
class Meetup:
    """A place to meet, and how far it is from the middle of campus."""

    spot: Spot
    metres_from_centre: float

    @property
    def walk_minutes(self) -> int:
        return max(1, round(self.metres_from_centre * DETOUR / WALK_METRES_PER_MINUTE))


def metres_between(one: Spot, two: Spot) -> float:
    """Great-circle distance in metres."""
    lat1, lat2 = math.radians(one.latitude), math.radians(two.latitude)
    dlat = lat2 - lat1
    dlon = math.radians(two.longitude - one.longitude)
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(a)))


def walk_metres(one: Spot, two: Spot) -> float:
    """What someone actually walks, allowing for not walking through buildings."""
    return metres_between(one, two) * DETOUR


def centre_of(places: list[Spot]) -> Spot | None:
    """The middle of where students are: the average of the dorms, or of everything if a campus
    has none mapped. Averaged on the sphere, so it doesn't drift near the poles or the meridian.
    """
    if not places:
        return None
    dorms = [place for place in places if place.kind == "dormitory"]
    using = dorms or places
    x = sum(math.cos(math.radians(p.latitude)) * math.cos(math.radians(p.longitude)) for p in using)
    y = sum(math.cos(math.radians(p.latitude)) * math.sin(math.radians(p.longitude)) for p in using)
    z = sum(math.sin(math.radians(p.latitude)) for p in using)
    count = len(using)
    x, y, z = x / count, y / count, z / count
    return Spot(
        name="campus centre",
        latitude=math.degrees(math.atan2(z, math.hypot(x, y))),
        longitude=math.degrees(math.atan2(y, x)),
    )


def findable(place: Spot) -> bool:
    """Whether a stranger could be told to meet here and actually find it."""
    name = place.name.lower()
    return not any(word in name for word in NOT_A_MEETING_PLACE)


def meetable(places: list[Spot]) -> list[Spot]:
    """Places two students could actually meet: nowhere anyone lives, and nowhere a stranger
    couldn't find."""
    return [place for place in places if not place.private and findable(place)]


def score(meetup: Meetup) -> float:
    """Lower is better: distance from the centre of campus, less what this kind of place is
    worth going further for. So a cafe 360 m further out still beats a central lawn."""
    return meetup.metres_from_centre - KIND_BONUS.get(meetup.spot.kind, 0.0)


def rank(places: list[Spot]) -> list[Meetup]:
    """Every place two students could meet, best first."""
    centre = centre_of(places)
    if centre is None:
        return []
    scored = [
        Meetup(spot=place, metres_from_centre=metres_between(centre, place))
        for place in meetable(places)
    ]
    return sorted(scored, key=score)


def choose(places: list[Spot]) -> Meetup | None:
    """The one place to send two students, or None when a campus has nowhere public."""
    best = rank(places)
    return best[0] if best else None


def options(places: list[Spot], limit: int = 3) -> list[Meetup]:
    """A few places, so a student can pick another one."""
    return rank(places)[:limit]


def describe(meetup: Meetup) -> str:
    """How a meetup reads in the chat."""
    where = meetup.spot.name
    if meetup.spot.opening_hours:
        return f"{where} (open {meetup.spot.opening_hours})"
    return where
