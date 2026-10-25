"""The first thing a student sees: are you a student, and what's your school email.

Yardsale on campus is same-school only, so a conversation can't do anything until it has a
school. This is the way in, as Interactive Cards rather than typed commands: a card is harder to
get wrong than remembering to type an address, and a tapped button can't be misread the way free
text can.

    "Are you a student?"  -> [Yes, I'm a student] [No]
      Yes -> a form asking for a school email
        submitted -> signed up, and told where their campus meets

Two taps and an address. The school is read off the end of the email and taken at face value,
so there's no code to wait for, and nobody is asked where they live: the meeting spot comes
from the campus itself.

Cards and typed text both work: `students.join` does the real work and these are only one way
to reach it. A client that can't render cards still gets the text.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from uuid import uuid4

from uagents_core.contrib.protocols.chat import MetadataContent
from uagents_core.contrib.protocols.chat.cards import (
    ButtonAction,
    ButtonNode,
    CtaAction,
    CustomCardPayload,
    FormCardPayload,
    FormField,
    GroupNode,
    HeadingNode,
    TextNode,
    create_card_content,
)

from yardsale import campus, schools, students
from yardsale.schools import SchoolError
from yardsale.store import Store

log = logging.getLogger("yardsale.onboarding")


def campus_mode() -> bool:
    """YARDSALE_CAMPUS=1 turns the campus product on: same-school only, and every conversation
    starts by settling which school it is.

    Off by default, deliberately. Switching it on changes what every seller and buyer sees, and
    a conversation with no school email can neither list nor search. The flag keeps that a decision
    somebody makes rather than a side effect of this code existing.
    """
    return os.getenv("YARDSALE_CAMPUS", "").strip().lower() in ("1", "true", "yes", "on")


ARE_YOU_A_STUDENT = (
    "Hi, I'm Yardsale. On campus I only connect students at the same university, so you're "
    "always buying from and selling to your own classmates. Are you a student?"
)
NOT_A_STUDENT_YET = (
    "No problem. Yardsale on campus is just for students at the moment, so there's nothing I "
    "can sell you yet. Come back with a school email and I'll get you set up."
)
ASK_FOR_EMAIL = "Great. What's your school email? I only use it to tell which university you're at."
JOINED = (
    "You're in, at {school}. You can sell ({sell}) and buy from other {school} students.\n\n"
    "When you trade I'll suggest somewhere on campus to meet, so nobody has to say where they "
    "live."
)
SELL_HINT = "send me a photo of something"
# The card buttons that sign a student up. A tap on one of these is never answered with the
# welcome again.
SIGN_UP_ACTIONS = frozenset({"student", "not_student", "student_email"})


def signing_up(choice: Mapping[str, object] | None) -> bool:
    """Whether a tapped card button is one of the sign-up steps."""
    return choice is not None and choice.get("action") in SIGN_UP_ACTIONS


def student_welcome() -> MetadataContent:
    """The first card a conversation sees: are you a student?"""
    return create_card_content(
        CustomCardPayload(
            root=GroupNode(
                type="group",
                direction="column",
                children=[
                    HeadingNode(type="heading", value="Yardsale on campus"),
                    TextNode(
                        type="text",
                        value="I connect students at the same university, so you're always "
                        "trading with your own classmates.",
                    ),
                    GroupNode(
                        type="group",
                        direction="row",
                        children=[
                            ButtonNode(
                                type="button",
                                label="Yes, I'm a student",
                                primary=True,
                                action=ButtonAction(selection={"action": "student"}),
                            ),
                            ButtonNode(
                                type="button",
                                label="No",
                                action=ButtonAction(selection={"action": "not_student"}),
                            ),
                        ],
                    ),
                ],
            )
        ),
        card_id=uuid4(),
    )


def email_form() -> MetadataContent:
    return create_card_content(
        FormCardPayload(
            title="Your school email",
            fields=[
                FormField(
                    name="email",
                    kind="email",
                    label="School email",
                    required=True,
                    placeholder="you@berkeley.edu",
                )
            ],
            submit_cta=CtaAction(
                label="Send me a code", selection={"action": "student_email"}, primary=True
            ),
        ),
        card_id=uuid4(),
    )


def joined_text(school: str) -> str:
    """What a student is told once they're in."""
    return JOINED.format(school=schools.school_label(school), sell=SELL_HINT)


def join(store: Store, owner: str, email: str) -> tuple[str, MetadataContent | None]:
    """Sign a student up from an email. Returns the reply and a card if one is needed again.

    This answers at once and never waits on the campus lookup. Working a campus out is a Claude
    call, and a student doesn't need a meeting spot until they actually trade. The caller
    schedules `warm` instead, so the first student from a school starts the lookup without
    waiting for it.

    A bad address comes back as a message rather than an error, because the student simply
    needs to try again.
    """
    try:
        student = students.join(store, owner, email)
    except SchoolError as ex:
        return str(ex), email_form()
    return joined_text(student.school), None


async def warm(store: Store, school: str, find: campus.FindCampus = students.find_campus) -> None:
    """Map a school's campus if it isn't mapped yet. Meant to be run in the background, so it
    returns nothing and never raises: campus.find_and_load already swallows a failure, and
    students.warm_campus logs what it cached."""
    await students.warm_campus(store, school, find)
