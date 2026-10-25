"""Students: which school an email belongs to, and proving the student owns that email.

Yardsale on campus is same-school only. A listing is visible, searchable, and buyable only by
students with a verified email at the same school. That's the whole trust story: you're meeting
someone from your own university, not a stranger from the internet.

Matching a school needs no dataset. Two students are at the same school when their emails share
a registrable academic domain, so `rahul@berkeley.edu` and `sam@cs.berkeley.edu` match on
`berkeley.edu`. A friendly name is cosmetic; the key does the work, and it works offline.

Owning the address has to be proved, or "verified student" means nothing: anyone can type
someone else's address. `start_verification` makes a six-digit code and hands it to a sender
that the server injects, so tests and offline runs never send mail. Codes are stored as a keyed
hash, never in the clear, and they expire.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from collections.abc import Awaitable, Callable

from yardsale.common import derive_seed

# Suffixes that mean "a school". A domain ending in one of these is academic, and the school key
# is the label before it, so sub-departments land on the same school.
ACADEMIC_SUFFIXES = (
    ".edu",
    ".ac.uk",
    ".edu.au",
    ".ac.nz",
    ".edu.sg",
    ".ac.jp",
    ".edu.cn",
    ".ac.in",
    ".edu.in",
    ".ac.za",
    ".edu.mx",
    ".edu.br",
    ".ac.kr",
    ".edu.hk",
    ".ac.il",
)

CODE_LENGTH = 6
CODE_TTL = 15 * 60.0  # seconds a code can be used
MAX_ATTEMPTS = 5  # wrong guesses before a code is dead

# (email, code) -> sent. Injected, so nothing here sends mail on its own.
SendCode = Callable[[str, str], Awaitable[None]]

_EMAIL = re.compile(r"^[^@\s]+@([A-Za-z0-9.-]+\.[A-Za-z]{2,})$")


class SchoolError(ValueError):
    """The email can't be used. The message is shown to the student."""


def normalise_email(email: str) -> str:
    """Trimmed and lowercased. Addresses are compared and stored in this form."""
    return " ".join(email.split()).strip().lower()


def domain_of(email: str) -> str:
    """The domain part of an email. Raises SchoolError if it isn't an address."""
    match = _EMAIL.match(normalise_email(email))
    if match is None:
        raise SchoolError("That doesn't look like an email address.")
    return match.group(1)


def academic_suffix(domain: str) -> str | None:
    """The academic suffix a domain ends in, longest first so .edu.au beats .edu."""
    for suffix in sorted(ACADEMIC_SUFFIXES, key=len, reverse=True):
        if domain.endswith(suffix) and len(domain) > len(suffix):
            return suffix
    return None


def school_key(email: str) -> str:
    """The school two students must share, e.g. "berkeley.edu".

    Raises SchoolError for a non-school address, because a same-school marketplace can't place
    someone whose school it doesn't know.
    """
    domain = domain_of(email)
    suffix = academic_suffix(domain)
    if suffix is None:
        raise SchoolError(
            "Yardsale on campus needs your school email, like yours@berkeley.edu, so I can tell "
            "which university you're at."
        )
    label = domain[: -len(suffix)].rsplit(".", 1)[-1]  # cs.berkeley -> berkeley
    if not label:
        raise SchoolError("That school email is missing a university name before the domain.")
    return f"{label}{suffix}"


def same_school(one: str, two: str) -> bool:
    """Whether two school keys are the same university. Keys are already normalised."""
    return bool(one) and one == two


def school_label(key: str) -> str:
    """A readable name for a school key, for chat replies. The key is the authority; this is
    only how it reads. "berkeley.edu" -> "Berkeley"."""
    suffix = academic_suffix(key)
    name = key[: -len(suffix)] if suffix else key
    return name.replace("-", " ").replace(".", " ").title()


def new_code() -> str:
    """A six-digit code. secrets, not random: it's the only thing standing between someone and
    another student's identity."""
    return "".join(secrets.choice("0123456789") for _ in range(CODE_LENGTH))


def hash_code(email: str, code: str) -> str:
    """A keyed hash of a code, so the database never holds one that could be used.

    The email is part of the message, so a hash stolen from one row can't verify another.
    """
    key = derive_seed("student-verification").encode()
    message = f"{normalise_email(email)}:{code.strip()}".encode()
    return hmac.new(key, message, hashlib.sha256).hexdigest()


def code_matches(email: str, code: str, stored_hash: str) -> bool:
    """Constant-time comparison, so a wrong guess leaks nothing about how wrong it was."""
    return hmac.compare_digest(hash_code(email, code), stored_hash)
