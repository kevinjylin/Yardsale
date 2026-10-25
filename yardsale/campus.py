"""A school's meeting places, named by Claude once and kept.

One Claude call per school: "berkeley.edu" in, three to five public spots on that campus out,
best first. Claude already knows the big campuses a student marketplace starts with, so there's
nothing to geocode and nothing to rank.

This replaced OpenStreetMap. Nominatim placed the campus and Overpass returned everything tagged
nearby, which took 42-53 seconds cold, well past the chat's reply budget, and the places weren't
the ones a student would name: OSM tags a departmental archive and a herbarium as libraries, so a
real Berkeley run put "Environmental Design Archives" in the top five. Asking Claude for Sproul
Plaza and the Free Speech Movement Cafe is one call and gets the answer a student would give.

A campus doesn't move, so every school is named once into the store and read from there
afterwards. No live chat ever waits on this call.

Spots come back without coordinates. A name is what a student is actually told, and a separate
place lookup fills the latitude and longitude in later. A school Claude doesn't recognise comes
back empty, which isn't an error: an empty campus is never cached, so it can be retried, and the
chat asks the student to agree a spot themselves instead of inventing one.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import replace
from typing import Literal

from pydantic import BaseModel

from yardsale import llm
from yardsale.meetup import Spot

log = logging.getLogger("yardsale.campus")

# school key -> places on that campus, best first. The Claude call, injected so tests stay
# offline.
NameSpots = Callable[[str], Awaitable[list[Spot]]]
# school key -> its campus places, best first. What students.py warms with.
FindCampus = Callable[[str], Awaitable[list[Spot]]]

SYSTEM = """You name public places on a named university campus where two students who have \
agreed a secondhand sale can meet to hand the item over. Name only places you actually know are \
on that campus, by the name students and signs use. An invented place sends two students \
somewhere that doesn't exist, so if you don't know the campus, say so and name nothing."""

INSTRUCTIONS = """Name 3 to 5 places on the campus of {label} where two students could meet to \
hand over a sold item. Best first: the one you'd tell a first-year to meet at goes first.

Every place must be:

- specific and well known, so a stranger can be told the name and find it without directions
- public: outdoors, or indoors somewhere anyone may walk in
- busy during the day, so whoever arrives first isn't waiting alone
- nowhere anyone lives, and not a lab, archive, departmental reading room, or anything that \
needs a card or a booking to enter

Fields per place:

- name: what it's called on campus.
- kind: the closest of student_union, cafe, library, food_court, plaza, landmark, park.
- indoor: true when waiting there means waiting inside.

Set recognised to false if you don't know this university's campus, and leave places empty. \
Don't guess at a campus from the name alone."""


class _Place(BaseModel):
    name: str
    # The kinds meetup.py and the store understand.
    kind: Literal["student_union", "cafe", "library", "food_court", "plaza", "landmark", "park"]
    indoor: bool


class _Places(BaseModel):
    recognised: bool
    places: list[_Place]


class CampusError(RuntimeError):
    """A campus couldn't be worked out. The message is shown to the student."""


def school_label_for(school: str) -> str:
    """The searchable part of a school key: "berkeley.edu" -> "Berkeley". Kept here so campus
    loading doesn't depend on schools.py."""
    stripped = school.split(".")[0] if "." in school else school
    return stripped.replace("-", " ").title()


def usable(spots: list[Spot]) -> list[Spot]:
    """Tidy the names and drop anything a student couldn't be sent to, keeping the order given.

    A nameless spot is no use: the name is the whole instruction. The same place twice would
    waste one of the few alternatives a student gets to choose from.
    """
    seen: set[str] = set()
    kept: list[Spot] = []
    for spot in spots:
        name = " ".join(spot.name.split())[:120]
        if not name or name.lower() in seen:
            continue
        seen.add(name.lower())
        kept.append(replace(spot, name=name))
    return kept


async def name_spots_with_claude(school: str) -> list[Spot]:
    """Ask Claude for one campus's meeting places, best first. Empty when it doesn't know the
    university."""
    label = school_label_for(school)
    text = INSTRUCTIONS.format(label=f"{label} university")
    answer = await llm.ask(SYSTEM, [{"type": "text", "text": text}], _Places)
    if not answer.recognised:
        log.warning(f"{school}: Claude doesn't know this university's campus")
        return []
    # No coordinates: Claude knows names, not points, and a separate place lookup fills them in.
    return [Spot(name=place.name, kind=place.kind, indoor=place.indoor) for place in answer.places]


async def load(school: str, name_spots: NameSpots = name_spots_with_claude) -> list[Spot]:
    """Work out one campus's meeting places. Raises CampusError with a readable reason.

    Callers cache the result; this doesn't.
    """
    try:
        spots = usable(await name_spots(school))
    except llm.LLMError as ex:  # no key, or Claude declined
        raise CampusError(f"couldn't work out the meeting points here ({ex})") from ex
    if not spots:
        raise CampusError(
            "I don't know this university's campus, so I can't work out meeting points here."
        )
    log.info(f"{school}: {len(spots)} meeting places ({', '.join(s.name for s in spots)})")
    return spots


async def find_and_load(school: str, name_spots: NameSpots = name_spots_with_claude) -> list[Spot]:
    """A school's meeting places from its key alone, best first. Empty on any failure.

    This is the whole chain for a school nobody has used before: "berkeley.edu" in, places to
    meet out. Callers cache the result, so it runs once per school.
    """
    try:
        return await load(school, name_spots)
    except CampusError as ex:
        log.warning(f"{school}: no campus ({ex})")
    except asyncio.CancelledError:
        raise
    except Exception:
        log.exception(f"{school}: couldn't work out the campus")
    return []
