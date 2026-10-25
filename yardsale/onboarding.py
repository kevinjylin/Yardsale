"""The first thing a student sees: are you a student, what's your email, here's a code.

Yardsale on campus is same-school only, so a conversation can't do anything until it has a
verified school. This is the way in, as Interactive Cards rather than typed commands: a card is
harder to get wrong than remembering to type an address, and a tapped button can't be misread
the way free text can.

    "Are you a student?"  -> [Yes, I'm a student] [No]
      Yes -> a form asking for a school email
        submitted -> a code goes to that address, and a form asking for the code
          submitted -> verified, and they're asked which building they're in

Cards and typed text both work: `students.begin` and `students.confirm` do the real work, and
these are only two ways to reach them. A client that can't render cards still gets the text.

Nothing here sends mail. The sender is injected, so tests and offline runs never send anything.
"""

from __future__ import annotations

import logging
import os
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

from yardsale import schools, students
from yardsale.schools import SchoolError, SendCode
from yardsale.store import Store

log = logging.getLogger("yardsale.onboarding")


def campus_mode() -> bool:
    """YARDSALE_CAMPUS=1 turns the campus product on: same-school only, and every conversation
    starts by settling which school it is.

    Off by default, deliberately. Switching it on changes what every seller and buyer sees, and
    an unverified conversation can neither list nor search. The flag keeps that a decision
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
ASK_FOR_EMAIL = "Great. What's your school email? I'll send a six-digit code to check it."
CODE_SENT = "I've sent a six-digit code to {email}. Type it here and you're in."
VERIFIED = (
    "You're verified at {school}. You can sell ({sell}) and buy from other {school} students.\n\n"
    "One more thing: which building are you in? I use it to put meetups halfway between you and "
    "whoever you're trading with. Nobody is ever told your building, only where to meet."
)
SELL_HINT = "send me a photo of something"


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


def code_form() -> MetadataContent:
    return create_card_content(
        FormCardPayload(
            title="Check your email",
            fields=[
                FormField(
                    name="code",
                    kind="text",
                    label="Six-digit code",
                    required=True,
                    placeholder="123456",
                )
            ],
            submit_cta=CtaAction(
                label="Verify", selection={"action": "student_code"}, primary=True
            ),
        ),
        card_id=uuid4(),
    )


def verified_text(school: str) -> str:
    return VERIFIED.format(school=schools.school_label(school), sell=SELL_HINT)


async def start_verification(
    store: Store, owner: str, email: str, send: SendCode
) -> tuple[str, MetadataContent | None]:
    """Take an email, send a code, and ask for it. Returns the reply and the next card.

    A bad address comes back as a message rather than an error, because the student simply
    needs to try again.
    """
    try:
        _, code = students.begin(store, owner, email)
    except SchoolError as ex:
        return str(ex), email_form()
    await send(schools.normalise_email(email), code)
    return CODE_SENT.format(email=schools.normalise_email(email)), code_form()


async def log_code_for_development(email: str, code: str) -> None:
    """The default sender: writes the code to the server log instead of emailing it.

    This is for building and demoing, where whoever runs the server is also the student. It is
    not a way to run this for real: a code in a log is a code anyone with the log can use. Inject
    a real sender before anyone else's address is verified.
    """
    log.warning(
        f"No email sender configured, so verification is in development mode: the code for "
        f"{email} is {code}. Inject a real sender before using this for real."
    )
