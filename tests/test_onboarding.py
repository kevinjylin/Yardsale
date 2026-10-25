"""The sign-up cards: are you a student, your email, your code.

Campus mode is off by default, so these turn it on explicitly. Nothing here sends mail.
"""

from __future__ import annotations

import pytest

from yardsale import onboarding, students
from yardsale.store import Store


@pytest.fixture
def campus_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("YARDSALE_CAMPUS", "1")


@pytest.fixture
def db():
    store = Store()
    yield store
    store.close()


def sent() -> tuple[list[tuple[str, str]], object]:
    """A sender that records instead of emailing."""
    outbox: list[tuple[str, str]] = []

    async def send(email: str, code: str) -> None:
        outbox.append((email, code))

    return outbox, send


def test_campus_mode_is_off_unless_asked_for(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("YARDSALE_CAMPUS", raising=False)
    assert not onboarding.campus_mode()
    for truthy in ("1", "true", "YES", "on"):
        monkeypatch.setenv("YARDSALE_CAMPUS", truthy)
        assert onboarding.campus_mode()
    for falsy in ("", "0", "no", "off"):
        monkeypatch.setenv("YARDSALE_CAMPUS", falsy)
        assert not onboarding.campus_mode()


def test_all_three_cards_build() -> None:
    for card in (onboarding.student_welcome(), onboarding.email_form(), onboarding.code_form()):
        assert card.type == "metadata" and card.metadata


async def test_the_whole_sign_up_works(db: Store) -> None:
    outbox, send = sent()
    text, card = await onboarding.start_verification(db, "asi1:a", "sam@berkeley.edu", send)
    assert "sam@berkeley.edu" in text and card is not None
    assert len(outbox) == 1
    email, code = outbox[0]
    assert email == "sam@berkeley.edu" and len(code) == 6

    student = students.confirm(db, "asi1:a", code)
    assert student.verified and student.school == "berkeley.edu"
    assert "Berkeley" in onboarding.verified_text(student.school)


async def test_a_personal_address_is_explained_not_an_error(db: Store) -> None:
    outbox, send = sent()
    text, card = await onboarding.start_verification(db, "asi1:a", "sam@gmail.com", send)
    assert "school email" in text
    assert card is not None  # the form comes back so they can try again
    assert outbox == []  # and no code was sent anywhere


async def test_a_typo_is_explained_not_an_error(db: Store) -> None:
    outbox, send = sent()
    text, _ = await onboarding.start_verification(db, "asi1:a", "not-an-email", send)
    assert "email address" in text and outbox == []


async def test_the_email_is_tidied_before_the_code_goes_out(db: Store) -> None:
    outbox, send = sent()
    await onboarding.start_verification(db, "asi1:a", "  Sam@Berkeley.EDU ", send)
    assert outbox[0][0] == "sam@berkeley.edu"


async def test_asking_again_replaces_the_old_code(db: Store) -> None:
    outbox, send = sent()
    await onboarding.start_verification(db, "asi1:a", "sam@berkeley.edu", send)
    await onboarding.start_verification(db, "asi1:a", "sam@berkeley.edu", send)
    first, second = outbox[0][1], outbox[1][1]
    with pytest.raises(students.StudentError):
        students.confirm(db, "asi1:a", first)  # the old one is dead
    assert students.confirm(db, "asi1:a", second).verified


async def test_the_development_sender_does_not_raise() -> None:
    """It only logs, so a server with no mail configured still runs."""
    assert await onboarding.log_code_for_development("a@b.edu", "123456") is None
