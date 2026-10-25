"""A school's buildings and meeting places, from OpenStreetMap, fetched once and kept.

Overpass is free and needs no key, but its public instance expects a few hundred moderate
queries a day and slows a caller that keeps asking. A campus doesn't move, so every school is
fetched once into the store and read from there afterwards. No live chat ever waits on Overpass:
by the time a student names their dorm, the campus is already local.

What's collected per school:

- dormitories, which is what a student names ("I'm in Unit 1")
- cafes, libraries, student unions, plazas, parks and other public spots, which is where two
  students get sent

A school that OpenStreetMap maps poorly comes back thin or empty. That isn't an error, it's an
answer: the chat then asks the student to name a landmark instead of guessing.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

import aiohttp

from yardsale.meetup import Spot, metres_between

log = logging.getLogger("yardsale.campus")

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
FETCH_TIMEOUT = 180.0  # Overpass queues queries under load
GEOCODE_TIMEOUT = 30.0
# Around the campus centre. Generous, because a geocoded centre can sit a kilometre or two off
# the main campus, and students meet at cafes just off it anyway.
SEARCH_RADIUS_M = 3000
# Anything further than this from the centre isn't this campus, whatever the map service says.
# A second line of defence: the query is already scoped by distance, and this catches a bad
# centre or a service that ignores the scope.
MAX_PLACE_DISTANCE_M = 4000.0
# Both services ask callers to say who they are, so they can tell traffic apart.
USER_AGENT = "Yardsale/0.1 (campus meetup points)"

# OSM tags -> the kinds meetup.py scores. Checked in order, first match wins.
KIND_TAGS: tuple[tuple[str, str, str], ...] = (
    ("building", "dormitory", "dormitory"),
    ("amenity", "cafe", "cafe"),
    ("amenity", "library", "library"),
    ("amenity", "food_court", "food_court"),
    ("amenity", "college", "landmark"),
    ("amenity", "community_centre", "student_union"),
    ("leisure", "park", "park"),
    ("place", "square", "plaza"),
    ("amenity", "bench", "bench"),
)

# One query for a campus, scoped by distance from its centre and nothing else.
#
# An earlier version scoped by `area[name~"...",i][amenity=university]`. Don't: Nominatim
# resolves "berkeley.edu" to the generic "University of California", and that regex then matched
# every UC campus in the state. A real run came back with dorms in Berkeley and cafes in San
# Diego, 700 km apart. Names are ambiguous across a university system; coordinates aren't.
QUERY = """
[out:json][timeout:120];
(
  nwr(around:{radius},{lat},{lon})["building"="dormitory"];
  nwr(around:{radius},{lat},{lon})["amenity"~"^(cafe|library|food_court|community_centre)$"];
  nwr(around:{radius},{lat},{lon})["leisure"="park"];
  nwr(around:{radius},{lat},{lon})["place"="square"];
);
out center tags;
"""

# (school name, latitude, longitude) -> raw OSM elements. Injected so tests stay offline.
FetchCampus = Callable[[str, float, float], Awaitable[list[Any]]]
# school key -> (official name, latitude, longitude). Also injected.
Geocode = Callable[[str], Awaitable[tuple[str, float, float] | None]]
# school key -> its campus places, placing the school first. What students.py warms with.
FindCampus = Callable[[str], Awaitable[list[Spot]]]


class CampusError(RuntimeError):
    """A campus couldn't be fetched. The message is shown to the student."""


def kind_of(tags: dict[str, Any]) -> str | None:
    """Which kind of place an OSM element is, or None if it isn't one we use."""
    for tag, value, kind in KIND_TAGS:
        if str(tags.get(tag, "")).lower() == value:
            return kind
    if "university" in str(tags.get("amenity", "")).lower():
        return "landmark"
    return None


def _coordinates(element: dict[str, Any]) -> tuple[float, float] | None:
    """A node's own point, or the centre Overpass computes for a way or relation."""
    for lat_key, lon_key in (("lat", "lon"), ("center_lat", "center_lon")):
        lat, lon = element.get(lat_key), element.get(lon_key)
        if isinstance(lat, int | float) and isinstance(lon, int | float):
            return float(lat), float(lon)
    centre = element.get("center")
    if isinstance(centre, dict):
        lat, lon = centre.get("lat"), centre.get("lon")
        if isinstance(lat, int | float) and isinstance(lon, int | float):
            return float(lat), float(lon)
    return None


def parse_spot(element: Any) -> Spot | None:
    """One OSM element -> Spot. None for anything unnamed or unplaceable: a student can't be
    told to meet somewhere with no name."""
    if not isinstance(element, dict):
        return None
    tags = element.get("tags")
    if not isinstance(tags, dict):
        return None
    name = " ".join(str(tags.get("name") or "").split())[:120]
    kind = kind_of(tags)
    if not name or kind is None:
        return None
    point = _coordinates(element)
    if point is None or abs(point[0]) > 90 or abs(point[1]) > 180:
        return None
    indoor = kind in ("cafe", "library", "student_union", "food_court", "dormitory")
    return Spot(
        name=name,
        latitude=point[0],
        longitude=point[1],
        kind=kind,
        indoor=indoor,
        opening_hours=" ".join(str(tags.get("opening_hours") or "").split())[:120],
    )


def parse_campus(elements: list[Any]) -> list[Spot]:
    """Every usable place, one per name and kind so a building mapped twice appears once."""
    seen: set[tuple[str, str]] = set()
    spots: list[Spot] = []
    for element in elements:
        spot = parse_spot(element)
        if spot is None:
            continue
        key = (spot.name.lower(), spot.kind)
        if key not in seen:
            seen.add(key)
            spots.append(spot)
    return spots


async def fetch_from_overpass(name: str, latitude: float, longitude: float) -> list[Any]:
    """Ask Overpass for one campus. Raises CampusError with a readable reason."""
    query = QUERY.format(
        name=name.replace('"', ""), radius=SEARCH_RADIUS_M, lat=latitude, lon=longitude
    )
    timeout = aiohttp.ClientTimeout(total=FETCH_TIMEOUT)
    # Overpass asks callers to identify themselves so it can tell traffic apart.
    headers = {"User-Agent": "Yardsale/0.1 (campus meetup points)"}
    try:
        async with (
            aiohttp.ClientSession(timeout=timeout, headers=headers) as session,
            session.post(OVERPASS_URL, data={"data": query}) as resp,
        ):
            if resp.status == 429:
                raise CampusError("the map service is busy right now; try again in a minute")
            if resp.status != 200:
                raise CampusError(f"the map service returned {resp.status}")
            payload = await resp.json()
    except (aiohttp.ClientError, TimeoutError) as ex:
        raise CampusError(f"couldn't reach the map service ({ex})") from ex
    elements = payload.get("elements") if isinstance(payload, dict) else None
    if not isinstance(elements, list):
        raise CampusError("the map service sent something unexpected")
    return elements


def near(
    spots: list[Spot],
    latitude: float,
    longitude: float,
    limit_m: float = MAX_PLACE_DISTANCE_M,
) -> list[Spot]:
    """Only the places actually on this campus. Guards against a map service answering with
    another campus of the same university, which is what a loose name match once did."""
    centre = Spot(name="centre", latitude=latitude, longitude=longitude)
    return [spot for spot in spots if metres_between(centre, spot) <= limit_m]


async def geocode_school(school: str) -> tuple[str, float, float] | None:
    """A school key -> its official name and campus centre, from Nominatim.

    "berkeley.edu" is searched as "Berkeley university". That resolves the large universities a
    campus product starts with; anything it can't place comes back None and the school simply
    has no campus, which the chat explains rather than guessing at.
    """
    label = school_label_for(school)
    params = {"q": f"{label} university", "format": "json", "limit": "1"}
    timeout = aiohttp.ClientTimeout(total=GEOCODE_TIMEOUT)
    try:
        async with (
            aiohttp.ClientSession(timeout=timeout, headers={"User-Agent": USER_AGENT}) as session,
            session.get(NOMINATIM_URL, params=params) as resp,
        ):
            if resp.status != 200:
                raise CampusError(f"the place service returned {resp.status}")
            found = await resp.json()
    except (aiohttp.ClientError, TimeoutError) as ex:
        raise CampusError(f"couldn't reach the place service ({ex})") from ex
    if not isinstance(found, list) or not found or not isinstance(found[0], dict):
        return None
    first = found[0]
    try:
        latitude, longitude = float(first["lat"]), float(first["lon"])
    except (KeyError, TypeError, ValueError):
        return None
    if abs(latitude) > 90 or abs(longitude) > 180:
        return None
    name = " ".join(str(first.get("display_name") or label).split()).split(",")[0][:120]
    return name, latitude, longitude


def school_label_for(school: str) -> str:
    """The searchable part of a school key: "berkeley.edu" -> "Berkeley". Kept here so campus
    loading doesn't depend on schools.py."""
    stripped = school.split(".")[0] if "." in school else school
    return stripped.replace("-", " ").title()


def match_building(spots: list[Spot], named: str) -> Spot | None:
    """The building a student means. Exact name first, then a contained match, so "Unit 1"
    finds "Unit 1 Residence Hall"."""
    wanted = " ".join(named.split()).lower()
    if not wanted:
        return None
    for spot in spots:
        if spot.name.lower() == wanted:
            return spot
    partial = [s for s in spots if wanted in s.name.lower() or s.name.lower() in wanted]
    return min(partial, key=lambda s: len(s.name)) if len(partial) >= 1 else None


async def load(
    school: str,
    name: str,
    latitude: float,
    longitude: float,
    fetch: FetchCampus = fetch_from_overpass,
) -> list[Spot]:
    """Fetch one campus and log what came back. Callers cache the result; this doesn't."""
    elements = await fetch(name, latitude, longitude)
    found = parse_campus(elements)
    spots = near(found, latitude, longitude)
    if len(spots) < len(found):
        log.warning(
            f"{school}: dropped {len(found) - len(spots)} place(s) further than "
            f"{MAX_PLACE_DISTANCE_M:.0f} m from the campus centre"
        )
    dorms = sum(1 for s in spots if s.kind == "dormitory")
    log.info(f"{school}: {len(spots)} places on campus ({dorms} dorms) from {len(elements)} tags")
    if not spots:
        raise CampusError(
            "OpenStreetMap doesn't have this campus mapped yet, so I can't work out meeting "
            "points here."
        )
    return spots


async def load_quietly(
    school: str,
    name: str,
    latitude: float,
    longitude: float,
    fetch: FetchCampus = fetch_from_overpass,
) -> list[Spot]:
    """`load`, but a failure is logged and comes back empty instead of raising. For warming a
    campus in the background, where nobody is waiting on an answer."""
    try:
        return await load(school, name, latitude, longitude, fetch)
    except CampusError as ex:
        log.warning(f"{school}: couldn't load the campus ({ex})")
    except asyncio.CancelledError:
        raise
    except Exception:
        log.exception(f"{school}: couldn't load the campus")
    return []


async def find_and_load(
    school: str,
    geocode: Geocode = geocode_school,
    fetch: FetchCampus = fetch_from_overpass,
) -> list[Spot]:
    """Place a school from its key, then fetch its campus. Empty on any failure.

    This is the whole chain for a school nobody has used before: "berkeley.edu" in, buildings
    and meeting places out. Callers cache the result, so it runs once per school.
    """
    try:
        placed = await geocode(school)
    except CampusError as ex:
        log.warning(f"{school}: couldn't be placed ({ex})")
        return []
    except asyncio.CancelledError:
        raise
    except Exception:
        log.exception(f"{school}: couldn't be placed")
        return []
    if placed is None:
        log.warning(f"{school}: no university found for this email domain")
        return []
    name, latitude, longitude = placed
    log.info(f"{school}: found {name} at {latitude:.4f}, {longitude:.4f}")
    return await load_quietly(school, name, latitude, longitude, fetch)
