"""Which school an email belongs to.

Yardsale on campus is same-school only: a listing is visible and buyable only to students whose
email is at the same school. The school comes from the end of the address and nothing else, so
this works offline and needs no dataset. `rahul@berkeley.edu` and `sam@cs.berkeley.edu` both
land on `berkeley.edu`; a friendly name is cosmetic, the key does the work.

Nothing is proved here. A student says which school they're at by the address they give, and
that's taken at face value, so "same school" is a claim rather than a verified fact. It keeps
signing up to one step. Anything that depends on really being at a school needs a check this
module deliberately doesn't do.
"""

from __future__ import annotations

import re

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
