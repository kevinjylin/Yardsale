"""Real coordinates and a shareable map link for a place named on a campus.

Claude can name the places on a campus it knows, "Sproul Plaza" or "Moffitt Library", but it
doesn't know where they are, so the Spots it produces arrive with 0.0 coordinates. The name is
already the useful part of a chat message; coordinates only turn it into a link
by distance and what a map link needs to point at. This fills them in, one lookup per place.

Google's Places API is billed per request and per field group, which is why the field mask asks
for the location, the address and the place's id and nothing else, one result at a time, and why
a Spot that already has coordinates is never looked up again. Callers cache what comes back (the
store keeps a school's campus), so a campus costs one round of lookups and no more.

Without GOOGLE_MAPS_API_KEY none of this is an error: lookups come back None, Spots pass through
unchanged, and the chat still has a named place to send two students to.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from typing import Any
from urllib.parse import urlencode

import aiohttp

from yardsale.meetup import Spot
from yardsale.sources import coordinates, one_line

log = logging.getLogger("yardsale.maps")

# The Places API (New). The legacy /maps/api/place/textsearch endpoint is closed to projects
# created after March 2025, and only the new one takes a field mask, which is what keeps a
# lookup in the cheapest billing tier.
SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"
# Exactly the fields a Spot and its map link need. Every extra group costs more per request.
FIELD_MASK = "places.id,places.location,places.formattedAddress,places.displayName"
LOOKUP_TIMEOUT = 20.0  # seconds; a text search answers in well under one
MAPS_SEARCH_URL = "https://www.google.com/maps/search/"
NAME_LIMIT = 120  # as store.py allows for a Spot's name
ADDRESS_LIMIT = 200

# (place name, university name) -> where it is, or None when nothing was found or no key is set.
# Injected so tests stay offline and nothing is billed, as researcher.Research is.
LookupPlace = Callable[[str, str], Awaitable["Place | None"]]

_warned_about_key = False


class MapsError(RuntimeError):
    """A place couldn't be looked up. The message is readable enough to show a student."""


@dataclass(frozen=True)
class Place:
    """One place Google found, with a link anyone can open."""

    name: str
    latitude: float
    longitude: float
    address: str = ""
    place_id: str = ""

    @property
    def map_url(self) -> str:
        """A link that stays pointed at this place.

        The coordinates are what a map without the place id can still show, and the id is what
        makes Google open the place itself, with its name, hours and photos, rather than a bare
        pin. Both go in, because Google ignores the id if it no longer resolves.
        """
        query = {"api": "1", "query": _point(self.latitude, self.longitude)}
        if self.place_id:
            query["query_place_id"] = self.place_id
        return f"{MAPS_SEARCH_URL}?{urlencode(query)}"


def api_key() -> str:
    """GOOGLE_MAPS_API_KEY, or "" when it isn't configured.

    Read per call, never at import, so a server started before .env was filled in picks the key
    up without a restart. The warning is logged once: a campus is dozens of places, and dozens
    of identical lines would bury everything else.
    """
    global _warned_about_key
    key = os.getenv("GOOGLE_MAPS_API_KEY", "").strip()
    if not key and not _warned_about_key:
        _warned_about_key = True
        log.warning(
            "GOOGLE_MAPS_API_KEY is not set, so campus places keep their names but get no "
            "coordinates or map links (see .env.example)"
        )
    return key


def _point(latitude: float, longitude: float) -> str:
    """A coordinate pair as Google Maps wants it. Six decimals is about 0.1 m, so trimming the
    float's tail loses nothing and keeps the link short."""
    return f"{latitude:.6f},{longitude:.6f}"


def located(spot: Spot) -> bool:
    """Whether a Spot knows where it is. Exactly (0, 0) is the "not looked up yet" marker the
    naming step leaves behind; no campus is really in the Gulf of Guinea."""
    return not (spot.latitude == 0.0 and spot.longitude == 0.0)


def map_url(spot: Spot, university: str = "") -> str:
    """A Google Maps link for a Spot, to put in a chat reply.

    A located Spot links to its coordinates. One that was never looked up links to a search for
    its name, which is still the right link to send: Google usually finds "Sproul Plaza,
    University of California Berkeley" even though we couldn't.
    """
    if located(spot):
        query = _point(spot.latitude, spot.longitude)
    else:
        query = ", ".join(part for part in (spot.name, university) if part.strip())
    return f"{MAPS_SEARCH_URL}?{urlencode({'api': '1', 'query': query})}"


def parse_place(payload: Any, fallback_name: str = "") -> Place | None:
    """The first result of a searchText response -> Place. None when there's nothing usable.

    Every field is treated as optional, because a field mask Google changes its mind about, or a
    place with no street address, should cost us a lookup and not an exception.
    """
    if not isinstance(payload, dict):
        return None
    places = payload.get("places")
    if not isinstance(places, list) or not places or not isinstance(places[0], dict):
        return None
    first = places[0]
    position = first.get("location")
    if not isinstance(position, dict):
        return None
    latitude, longitude = coordinates(position.get("latitude"), position.get("longitude"))
    if latitude is None or longitude is None:
        return None
    display = first.get("displayName")
    name = display.get("text") if isinstance(display, dict) else None
    return Place(
        name=one_line(name or fallback_name, NAME_LIMIT),
        latitude=latitude,
        longitude=longitude,
        address=one_line(first.get("formattedAddress"), ADDRESS_LIMIT),
        place_id=one_line(first.get("id"), NAME_LIMIT),
    )


async def find_place(place: str, university: str) -> Place | None:
    """Look one campus place up with Google's Places text search.

    None means "no answer, carry on": no key, nothing to search for, or nothing found. Raises
    MapsError only when Google was reached and said something went wrong.
    """
    key = api_key()
    name = " ".join(place.split())
    if not key or not name:
        return None
    query = ", ".join(part for part in (name, " ".join(university.split())) if part)
    headers = {
        "X-Goog-Api-Key": key,
        "X-Goog-FieldMask": FIELD_MASK,
        "Content-Type": "application/json",
    }
    # One result: the rest would be billed for and thrown away.
    body = {"textQuery": query, "maxResultCount": 1, "languageCode": "en"}
    timeout = aiohttp.ClientTimeout(total=LOOKUP_TIMEOUT)
    try:
        async with (
            aiohttp.ClientSession(timeout=timeout) as session,
            session.post(SEARCH_URL, json=body, headers=headers) as resp,
        ):
            if resp.status != 200:
                detail = (await resp.text())[:200]
                raise MapsError(f"the place service returned {resp.status}: {detail}")
            payload = await resp.json()
    except (aiohttp.ClientError, TimeoutError) as ex:
        raise MapsError(f"couldn't reach the place service ({ex})") from ex
    return parse_place(payload, name)


async def locate(spot: Spot, university: str, lookup: LookupPlace = find_place) -> Spot:
    """A Spot with real coordinates, or the same Spot when it couldn't be placed.

    An already located Spot is returned untouched rather than looked up again, because every
    lookup is billed. Raises MapsError if the service fails; `locate_all` is the forgiving
    version.
    """
    if located(spot):
        return spot
    found = await lookup(spot.name, university)
    if found is None:
        return spot
    # Only the coordinates are taken. The name is the one Claude or the student used, and the
    # kind and opening hours are ours; Google's display name for a campus building is often a
    # department or a chain ("Peet's Coffee") rather than the place anyone is told to meet at.
    return replace(spot, latitude=found.latitude, longitude=found.longitude)


async def locate_all(
    spots: list[Spot], university: str, lookup: LookupPlace = find_place
) -> list[Spot]:
    """Coordinates for a whole campus at once, in the order given.

    One place failing loses that place's coordinates and nothing else: a student still gets the
    list, with the names that are the useful part of it. Lookups run together because a campus
    is dozens of them and a chat reply has about 40 seconds.
    """

    async def one(spot: Spot) -> Spot:
        try:
            return await locate(spot, university, lookup)
        except MapsError as ex:
            log.warning(f"no coordinates for {spot.name!r}: {ex}")
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception(f"no coordinates for {spot.name!r}")
        return spot

    return list(await asyncio.gather(*(one(spot) for spot in spots)))
