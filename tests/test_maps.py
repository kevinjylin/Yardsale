"""Putting coordinates on a named campus place. Nothing here touches the network."""

from __future__ import annotations

import logging
from typing import Any

import pytest

from yardsale import maps
from yardsale.maps import (
    MapsError,
    Place,
    find_place,
    locate,
    locate_all,
    located,
    map_url,
    parse_place,
)
from yardsale.meetup import Spot

# Shape of a places:searchText response with the field mask this module sends, from Google's
# Places API (New) reference. One result, because maxResultCount is 1.
REPLY: dict[str, Any] = {
    "places": [
        {
            "id": "ChIJizvG8g1whYARMjhLDGJDZ2k",
            "formattedAddress": "Sproul Plaza, Berkeley, CA 94704, USA",
            "location": {"latitude": 37.8696, "longitude": -122.2595},
            "displayName": {"text": "Sproul Plaza", "languageCode": "en"},
        }
    ]
}

NAMED = Spot(name="Sproul Plaza", latitude=0.0, longitude=0.0, kind="plaza")
BERKELEY = "University of California Berkeley"


@pytest.fixture(autouse=True)
def no_google_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """conftest strips the other providers' keys; this one isn't in that list yet. No test may
    spend a Google lookup, and the warn-once flag is reset so each test sees its own warning."""
    monkeypatch.delenv("GOOGLE_MAPS_API_KEY", raising=False)
    monkeypatch.setattr(maps, "_warned_about_key", False)


def test_parses_a_places_response() -> None:
    place = parse_place(REPLY)
    assert place is not None
    assert place.name == "Sproul Plaza"
    assert place.latitude == 37.8696 and place.longitude == -122.2595
    assert place.address == "Sproul Plaza, Berkeley, CA 94704, USA"
    assert place.place_id == "ChIJizvG8g1whYARMjhLDGJDZ2k"


def test_a_place_links_to_itself_not_just_a_pin() -> None:
    place = parse_place(REPLY)
    assert place is not None
    assert place.map_url == (
        "https://www.google.com/maps/search/?api=1&query=37.869600%2C-122.259500"
        "&query_place_id=ChIJizvG8g1whYARMjhLDGJDZ2k"
    )


def test_a_place_without_an_id_still_links() -> None:
    trimmed = {"places": [{k: v for k, v in REPLY["places"][0].items() if k != "id"}]}
    place = parse_place(trimmed)
    assert place is not None and place.place_id == ""
    assert place.map_url == (
        "https://www.google.com/maps/search/?api=1&query=37.869600%2C-122.259500"
    )


def test_a_place_with_no_name_of_its_own_keeps_the_one_we_asked_for() -> None:
    trimmed = {"places": [{k: v for k, v in REPLY["places"][0].items() if k != "displayName"}]}
    place = parse_place(trimmed, "Sproul Plaza")
    assert place is not None and place.name == "Sproul Plaza" and place.address


@pytest.mark.parametrize(
    "payload",
    [
        None,
        "a string",
        42,
        [],
        {},
        {"places": None},
        {"places": []},  # Google found nothing, which is an answer, not a failure
        {"places": ["junk"]},
        {"places": [{}]},
        {"places": [{"location": None}]},
        {"places": [{"location": {}}]},
        {"places": [{"location": {"latitude": 37.87}}]},  # half a position is no position
        {"places": [{"location": {"latitude": 0.0, "longitude": 0.0}}]},
        {"places": [{"location": {"latitude": 91.0, "longitude": -122.26}}]},
        {"places": [{"location": {"latitude": "north", "longitude": "west"}}]},
    ],
)
def test_malformed_and_empty_responses_degrade_to_nothing(payload: object) -> None:
    assert parse_place(payload, "Sproul Plaza") is None


def test_a_missing_address_does_not_lose_the_coordinates() -> None:
    """Not every campus lawn has a street address."""
    place = parse_place({"places": [{"location": {"latitude": 37.87, "longitude": -122.26}}]})
    assert place is not None and place.address == "" and place.latitude == 37.87


def test_a_spot_with_no_coordinates_is_the_one_to_look_up() -> None:
    assert located(NAMED) is False
    assert located(Spot(name="Sproul Plaza", latitude=37.8696, longitude=-122.2595)) is True


async def test_a_lookup_puts_coordinates_on_a_named_spot() -> None:
    async def fake(place: str, university: str) -> Place | None:
        assert place == "Sproul Plaza" and university == BERKELEY
        return parse_place(REPLY)

    placed = await locate(NAMED, BERKELEY, fake)
    assert placed.latitude == 37.8696 and placed.longitude == -122.2595
    # Everything else about the Spot survives: store.py persists these fields.
    assert placed.name == "Sproul Plaza" and placed.kind == "plaza"


async def test_an_already_located_spot_is_never_looked_up_again() -> None:
    """Lookups are billed per request, so a Spot that knows where it is doesn't cost one."""
    asked: list[str] = []

    async def fake(place: str, university: str) -> Place | None:
        asked.append(place)
        return parse_place(REPLY)

    known = Spot(name="Doe Library", latitude=37.8722, longitude=-122.2592, kind="library")
    assert await locate(known, BERKELEY, fake) is known
    assert asked == []


async def test_a_place_google_cannot_find_leaves_the_spot_alone() -> None:
    async def nothing(place: str, university: str) -> Place | None:
        return None

    assert await locate(NAMED, BERKELEY, nothing) == NAMED


async def test_no_api_key_returns_the_spots_unchanged_and_warns_once(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The real lookup is the default here, and with no key it never reaches the network."""
    spots = [NAMED, Spot(name="Moffitt Library", latitude=0.0, longitude=0.0, kind="library")]
    with caplog.at_level(logging.WARNING, logger="yardsale.maps"):
        assert await locate_all(spots, BERKELEY) == spots
    warnings = [r for r in caplog.records if "GOOGLE_MAPS_API_KEY" in r.message]
    assert len(warnings) == 1  # two places, one line about the missing key


async def test_no_api_key_is_not_an_error() -> None:
    assert await find_place("Sproul Plaza", BERKELEY) is None


async def test_an_empty_place_name_is_never_looked_up(monkeypatch: pytest.MonkeyPatch) -> None:
    """Guards the paid call, as researcher.py does for an empty eBay search."""
    monkeypatch.setenv("GOOGLE_MAPS_API_KEY", "test-key")
    assert await find_place("   ", BERKELEY) is None


async def test_one_failed_lookup_does_not_lose_the_others(
    caplog: pytest.LogCaptureFixture,
) -> None:
    sproul = NAMED
    moffitt = Spot(name="Moffitt Library", latitude=0.0, longitude=0.0, kind="library")
    cafe = Spot(name="Free Speech Movement Cafe", latitude=0.0, longitude=0.0, kind="cafe")

    async def flaky(place: str, university: str) -> Place | None:
        if place == "Moffitt Library":
            raise MapsError("the place service returned 429: over quota")
        if place == "Free Speech Movement Cafe":
            raise RuntimeError("something nobody predicted")
        return parse_place(REPLY)

    with caplog.at_level(logging.WARNING, logger="yardsale.maps"):
        placed = await locate_all([sproul, moffitt, cafe], BERKELEY, flaky)
    assert len(placed) == 3
    assert placed[0].latitude == 37.8696  # the one that worked
    assert placed[1] == moffitt and placed[2] == cafe  # the two that didn't, untouched
    assert any("Moffitt" in r.message for r in caplog.records)


async def test_a_failing_lookup_is_raised_to_a_single_caller() -> None:
    """`locate` is the strict one; only the batch helper swallows failures."""

    async def broken(place: str, university: str) -> Place | None:
        raise MapsError("couldn't reach the place service")

    with pytest.raises(MapsError, match="couldn't reach"):
        await locate(NAMED, BERKELEY, broken)


async def test_an_empty_campus_costs_nothing() -> None:
    assert await locate_all([], BERKELEY) == []


def test_a_located_spot_links_to_its_coordinates() -> None:
    spot = Spot(name="Sproul Plaza", latitude=37.8696, longitude=-122.2595, kind="plaza")
    assert map_url(spot) == (
        "https://www.google.com/maps/search/?api=1&query=37.869600%2C-122.259500"
    )


def test_an_unlocated_spot_still_gets_a_usable_link() -> None:
    assert map_url(NAMED, BERKELEY) == (
        "https://www.google.com/maps/search/?api=1&query=Sproul+Plaza%2C+University+of"
        "+California+Berkeley"
    )
    assert map_url(NAMED) == "https://www.google.com/maps/search/?api=1&query=Sproul+Plaza"
