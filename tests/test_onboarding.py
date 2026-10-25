"""The sign-up cards: are you a student, then your school email. No code, no location.

Campus mode is off by default, so these turn it on explicitly.
"""

from __future__ import annotations

import pytest

from yardsale import onboarding
from yardsale.meetup import Spot
from yardsale.store import Store

CAFE = Spot("Free Speech Movement Cafe", 37.8723, -122.2600, "cafe", indoor=True)
DORM = Spot("Unit 1 Residence Hall", 37.8679, -122.2553, "dormitory", indoor=True)


@pytest.fixture
def db():
    store = Store()
    yield store
    store.close()


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


def test_signing_up_is_one_step(db: Store) -> None:
    db.save_campus("berkeley.edu", [CAFE, DORM])
    text, card = onboarding.join(db, "asi1:a", "sam@berkeley.edu")
    assert "You're in, at Berkeley" in text
    assert card is None  # nothing else to ask for
    student = db.student("asi1:a")
    assert student is not None and student.school == "berkeley.edu"


def test_signing_up_names_the_campus_meeting_spot(db: Store) -> None:
    db.save_campus("berkeley.edu", [CAFE, DORM])
    text, _ = onboarding.join(db, "asi1:a", "sam@berkeley.edu")
    assert "Free Speech Movement Cafe" in text
    assert "Unit 1" not in text  # never a dorm


def test_an_unmapped_campus_still_signs_you_up(db: Store) -> None:
    text, card = onboarding.join(db, "asi1:a", "sam@nowhere.edu")
    assert "You're in, at Nowhere" in text and "agree a meeting spot" in text
    assert card is None
    assert db.student("asi1:a") is not None


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
