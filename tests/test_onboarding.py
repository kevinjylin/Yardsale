"""The sign-up cards: are you a student, then your school email.

No code, no location, and no waiting on a map service. Campus mode is off by default, so the
tests that care about it turn it on explicitly.
"""

from __future__ import annotations

import pytest

from yardsale import onboarding, students
from yardsale.meetup import Spot
from yardsale.store import Store

CAFE = Spot("Free Speech Movement Cafe", 37.8723, -122.2600, "cafe", indoor=True)
DORM = Spot("Unit 1 Residence Hall", 37.8679, -122.2553, "dormitory", indoor=True)
SPROUL = Spot("Sproul Plaza", 0.0, 0.0, "plaza")  # as Claude names it: no coordinates yet


@pytest.fixture
def db():
    store = Store()
    yield store
    store.close()


def finder(*places: Spot):
    """A stand-in for fetching a campus, so no test reaches a map service."""
    calls: list[str] = []

    async def find(school: str) -> list[Spot]:
        calls.append(school)
        return list(places)

    return find, calls


# --- The flag -------------------------------------------------------------------------------


def test_campus_mode_is_off_unless_asked_for(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("YARDSALE_CAMPUS", raising=False)
    assert not onboarding.campus_mode()
    for truthy in ("1", "true", "YES", "on"):
        monkeypatch.setenv("YARDSALE_CAMPUS", truthy)
        assert onboarding.campus_mode()
    for falsy in ("", "0", "no", "off"):
        monkeypatch.setenv("YARDSALE_CAMPUS", falsy)
        assert not onboarding.campus_mode()


def test_both_cards_build() -> None:
    for card in (onboarding.student_welcome(), onboarding.email_form()):
        assert card.type == "metadata" and card.metadata


# --- Signing up -----------------------------------------------------------------------------


def test_signing_up_is_one_step(db: Store) -> None:
    text, card = onboarding.join(db, "asi1:a", "sam@berkeley.edu")
    assert "You're in, at Berkeley" in text
    assert card is None  # nothing else to ask for
    student = db.student("asi1:a")
    assert student is not None and student.school == "berkeley.edu"


def test_signing_up_never_waits_on_a_map(db: Store) -> None:
    """Working a campus out is a Claude call, so joining says nothing about a meeting spot and
    the lookup happens behind the reply."""
    text, _ = onboarding.join(db, "asi1:a", "sam@berkeley.edu")
    assert "somewhere on campus" in text
    assert not db.campus_loaded("berkeley.edu")  # joining didn't fetch anything


def test_a_personal_address_is_explained_not_an_error(db: Store) -> None:
    text, card = onboarding.join(db, "asi1:a", "sam@gmail.com")
    assert "school email" in text
    assert card is not None  # the form comes back so they can try again
    assert db.student("asi1:a") is None


def test_a_typo_is_explained_not_an_error(db: Store) -> None:
    text, card = onboarding.join(db, "asi1:a", "not-an-email")
    assert "email address" in text and card is not None
    assert db.student("asi1:a") is None


def test_the_email_is_tidied(db: Store) -> None:
    onboarding.join(db, "asi1:a", "  Sam@Berkeley.EDU ")
    student = db.student("asi1:a")
    assert student is not None and student.email == "sam@berkeley.edu"


def test_giving_a_new_address_moves_the_student(db: Store) -> None:
    onboarding.join(db, "asi1:a", "sam@berkeley.edu")
    onboarding.join(db, "asi1:a", "sam@stanford.edu")
    student = db.student("asi1:a")
    assert student is not None and student.school == "stanford.edu"


# --- Mapping the campus behind the reply ----------------------------------------------------


async def test_the_first_student_from_a_school_maps_it(db: Store) -> None:
    """It used to be left to a half-hourly interval, so a school nobody had used was told its
    campus wasn't mapped for up to thirty minutes."""
    find, calls = finder(CAFE, DORM)
    onboarding.join(db, "asi1:a", "sam@berkeley.edu")
    await onboarding.warm(db, "berkeley.edu", find)
    assert calls == ["berkeley.edu"]
    assert db.campus_loaded("berkeley.edu")


async def test_the_second_student_does_not_pay_for_it_again(db: Store) -> None:
    find, calls = finder(CAFE, DORM)
    await onboarding.warm(db, "berkeley.edu", find)
    await onboarding.warm(db, "berkeley.edu", find)
    assert calls == ["berkeley.edu"]  # fetched once, ever


async def test_a_campus_nothing_is_mapped_for_is_not_cached_as_empty(db: Store) -> None:
    """So it can be tried again, rather than being permanently broken."""
    find, _ = finder()
    await onboarding.warm(db, "nowhere.edu", find)
    assert not db.campus_loaded("nowhere.edu")


async def test_the_meeting_spot_appears_once_the_campus_is_mapped(db: Store) -> None:
    find, _ = finder(CAFE, DORM)
    onboarding.join(db, "asi1:a", "sam@berkeley.edu")
    await onboarding.warm(db, "berkeley.edu", find)
    meeting = students.where_to_meet(db, "berkeley.edu")
    assert meeting.spot.name == "Free Speech Movement Cafe"
    assert not meeting.spot.private  # never the dorm


# --- Does it reach the chat? ----------------------------------------------------------------


def test_the_chat_builds_a_meeting_line_with_a_map_link(
    db: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole point of the feature: a closed deal says where to hand the item over."""
    from yardsale import campus, maps, meetup, students

    monkeypatch.setenv("YARDSALE_CAMPUS", "1")
    onboarding.join(db, "asi1:a", "sam@berkeley.edu")
    db.save_campus("berkeley.edu", [SPROUL, CAFE, DORM])

    meeting = students.where_to_meet(db, "berkeley.edu")
    link = maps.map_url(meeting.spot, campus.school_label_for("berkeley.edu"))
    line = f"Meet at [{meetup.describe(meeting)}]({link}) to hand it over."

    assert "Sproul Plaza" in line
    assert "Unit 1" not in line  # never a dorm
    assert "google.com/maps" in link
    assert "Berkeley" in link  # the university scopes the search when there are no coordinates


def test_a_map_link_works_without_a_google_key(db: Store) -> None:
    """No key means no coordinates, and a search link is still the right thing to send."""
    from yardsale import maps

    assert not maps.located(SPROUL)
    link = maps.map_url(SPROUL, "Berkeley")
    assert link.startswith("https://") and "Sproul" in link.replace("%20", " ").replace("+", " ")
