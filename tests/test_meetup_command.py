"""Asking where to meet, on demand: "where should we meet", "meetup spot", or the Where to
meet button. The spots come from the campus already named for the student's school; no map
service is called here.
"""

from __future__ import annotations

import hashlib

import pytest

from tests import sim
from yardsale import students
from yardsale.meetup import Spot
from yardsale.store import Store

SPROUL = Spot("Sproul Plaza", 0.0, 0.0, "plaza")
CAFE = Spot(
    "Free Speech Movement Cafe", 37.8723, -122.2600, "cafe", indoor=True, opening_hours="8am-10pm"
)
LIBRARY = Spot("Moffitt Library", 37.8725, -122.2608, "library", indoor=True)
DORM = Spot("Unit 1 Residence Hall", 37.8679, -122.2553, "dormitory", indoor=True)


def owner_of(user: sim.ChatUser) -> str:
    """The store's owner key for a chat user, the way chat.py derives it from the session."""
    key = hashlib.sha256(f"session:{user.session}".encode()).hexdigest()[:24]
    return f"asi1:{key}"


@pytest.fixture(autouse=True)
def campus_mode_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("YARDSALE_CAMPUS", raising=False)


async def test_a_student_asks_where_to_meet(market: sim.Market, store: Store) -> None:
    buyer = market.user("buyer")
    students.join(store, owner_of(buyer), "sam@berkeley.edu")
    store.save_campus("berkeley.edu", [SPROUL, DORM, CAFE, LIBRARY])

    reply = await buyer.exchange(sim.chat_message("where should we meet?"))
    text = sim.shown(reply)
    assert text.startswith("Meet on campus at Berkeley. Best spot first:")
    assert "1. [Sproul Plaza](https://www.google.com/maps/search/" in text
    assert "2. [Free Speech Movement Cafe (open 8am-10pm)]" in text
    assert "3. [Moffitt Library]" in text
    assert "Unit 1" not in text  # never a dorm
    assert "Berkeley" in text.split("1. [Sproul Plaza](")[1].split(")")[0]  # the search is scoped
    assert sim.button(reply, "buy") == {"action": "buy"}

    # The button says the same thing, and so does every other way of asking.
    assert "Sproul Plaza" in await buyer.tap({"action": "meet"})
    for asking in ("meetup spot?", "where do I pick it up", "location for meetup on campus"):
        assert "Sproul Plaza" in await buyer.say(asking)


async def test_the_menu_offers_meetups_only_to_a_student(market: sim.Market, store: Store) -> None:
    buyer = market.user("buyer")
    menu = sim.card_of(await buyer.exchange(sim.chat_message("help")))
    assert menu is not None
    assert all(selection.get("action") != "meet" for _, selection in sim.card_buttons(menu))
    students.join(store, owner_of(buyer), "sam@berkeley.edu")
    assert sim.button(await buyer.exchange(sim.chat_message("help")), "meet") == {"action": "meet"}


async def test_without_a_school_the_answer_explains(market: sim.Market) -> None:
    buyer = market.user("buyer")
    text = await buyer.say("where should we meet?")
    assert text.startswith("Meeting spots are a campus feature")
    assert "agree a place" in text


async def test_in_campus_mode_without_a_school_it_asks_for_the_email(
    market: sim.Market, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("YARDSALE_CAMPUS", "1")
    buyer = market.user("buyer")
    # The first message in campus mode gets only the "are you a student?" card.
    welcome = await buyer.exchange(sim.chat_message("hi"))
    assert sim.button(welcome, "student") is not None
    # Asking before signing up gets the email form, not the spots.
    reply = await buyer.exchange(sim.chat_message("where can we meet"))
    assert "What's your school email?" in sim.shown(reply)
    assert sim.button(reply, "student_email") is not None


async def test_a_school_with_no_campus_yet_says_so(market: sim.Market, store: Store) -> None:
    buyer = market.user("buyer")
    students.join(store, owner_of(buyer), "sam@nowhere.edu")
    assert (await buyer.say("meetup spot")).startswith(students.NO_CAMPUS)
