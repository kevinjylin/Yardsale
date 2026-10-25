"""Where two students should meet: a public place between their two buildings.

Both sides give a building, never an address, and neither is ever told the other's. Only the
meeting place is shared. A dorm is close enough to a home address that telling a stranger which
one you're in is the thing to avoid, even when the stranger goes to your university.

Picking a place is one objective: make the longer of the two walks as short as possible, then
prefer somewhere public. For two people the plain midpoint already minimises the longer walk,
but the midpoint is usually a lawn or the inside of a building, so real places near it are
scored and the best one wins.

A place's kind is worth a detour, in metres: a busy cafe you can wait inside is better than a
closer patch of grass, and "public and busy" is the safety property that matters when two people
who haven't met are meeting. Distances are straight-line times a detour factor, which is close
enough on a campus and needs no routing service.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

EARTH_RADIUS_M = 6_371_000.0
# Walking never follows a straight line. Paths, buildings, and crossings add roughly a third on
# a campus, which is accurate enough to rank places and needs no routing API.
DETOUR = 1.3
WALK_METRES_PER_MINUTE = 80.0  # an unhurried walk

# What each kind of place is worth going further for, in metres of the longer walk. Indoors,
# staffed, and busy beat open ground, because someone arriving first has somewhere to stand.
KIND_BONUS = {
    "student_union": 200.0,
    "cafe": 180.0,
    "library": 170.0,
    "food_court": 150.0,
    "plaza": 90.0,
    "landmark": 80.0,
    "park": 60.0,
    "bench": 20.0,
}
# Never proposed as a meeting place: somewhere someone lives.
PRIVATE_KINDS = frozenset({"dormitory", "residential", "house", "apartments"})
DEFAULT_RADIUS_M = 1200.0  # campus-sized: how far from the midpoint to consider places


@dataclass(frozen=True)
class Spot:
    """A point on a campus: a building a student names, or a place they could meet."""

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
    """A chosen place and what it costs each side. The walks are what each student is told."""

    spot: Spot
    metres_from_first: float
    metres_from_second: float

    @property
    def longer_walk_metres(self) -> float:
        return max(self.metres_from_first, self.metres_from_second)

    @property
    def longer_walk_minutes(self) -> int:
        return max(1, round(self.longer_walk_metres / WALK_METRES_PER_MINUTE))

    @property
    def fair(self) -> bool:
        """Neither side walks more than twice as far as the other."""
        shorter = min(self.metres_from_first, self.metres_from_second)
        return self.longer_walk_metres <= max(100.0, shorter * 2)


def metres_between(one: Spot, two: Spot) -> float:
    """Great-circle distance in metres."""
    lat1, lat2 = math.radians(one.latitude), math.radians(two.latitude)
    dlat = lat2 - lat1
    dlon = math.radians(two.longitude - one.longitude)
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(a)))


def walk_metres(one: Spot, two: Spot) -> float:
    """What someone actually walks, allowing for not being able to walk through buildings."""
    return metres_between(one, two) * DETOUR


def midpoint(one: Spot, two: Spot) -> Spot:
    """The point halfway between two spots. For two people this already minimises the longer
    walk, so it's the right thing to look for real places around."""
    lat1, lon1 = math.radians(one.latitude), math.radians(one.longitude)
    lat2, lon2 = math.radians(two.latitude), math.radians(two.longitude)
    x = math.cos(lat1) * math.cos(lon1) + math.cos(lat2) * math.cos(lon2)
    y = math.cos(lat1) * math.sin(lon1) + math.cos(lat2) * math.sin(lon2)
    z = math.sin(lat1) + math.sin(lat2)
    return Spot(
        name="midpoint",
        latitude=math.degrees(math.atan2(z, math.hypot(x, y))),
        longitude=math.degrees(math.atan2(y, x)),
    )


def meetable(places: list[Spot]) -> list[Spot]:
    """Places two students could actually meet: nowhere anyone lives."""
    return [place for place in places if not place.private]


def score(meetup: Meetup) -> float:
    """Lower is better: the longer walk, less what this kind of place is worth going further
    for. So a cafe 180 m further than a bare lawn still wins."""
    return meetup.longer_walk_metres - KIND_BONUS.get(meetup.spot.kind, 0.0)


def measure(spot: Spot, first: Spot, second: Spot) -> Meetup:
    return Meetup(
        spot=spot,
        metres_from_first=walk_metres(first, spot),
        metres_from_second=walk_metres(second, spot),
    )


def choose(
    first: Spot, second: Spot, places: list[Spot], radius_m: float = DEFAULT_RADIUS_M
) -> Meetup | None:
    """The best place to meet between two buildings, or None when nothing is close enough.

    Only places within `radius_m` of the midpoint are considered, so a great cafe on the far
    side of campus doesn't win on its bonus alone.
    """
    centre = midpoint(first, second)
    nearby = [place for place in meetable(places) if metres_between(centre, place) <= radius_m]
    if not nearby:
        return None
    return min((measure(place, first, second) for place in nearby), key=score)


def options(
    first: Spot,
    second: Spot,
    places: list[Spot],
    limit: int = 3,
    radius_m: float = DEFAULT_RADIUS_M,
) -> list[Meetup]:
    """The best few places, so a student can pick another one."""
    centre = midpoint(first, second)
    nearby = [place for place in meetable(places) if metres_between(centre, place) <= radius_m]
    return sorted((measure(place, first, second) for place in nearby), key=score)[:limit]


def describe(meetup: Meetup) -> str:
    """How a meetup reads in the chat. Never mentions either student's building."""
    minutes = meetup.longer_walk_minutes
    return f"{meetup.spot.name}, about a {minutes}-minute walk for each of you"
