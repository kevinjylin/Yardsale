"""Where a buyer is shopping: "in Berkeley", "near 94704", "SF" -> a ZIP code and a name.

The local sites search around a place: OfferUp and Facebook Marketplace around a ZIP code,
Craigslist in one city's site ("sfbay") within a radius of a ZIP code. A buyer's message names
the place, if at all, in words; `split` finds it, and the rest of the message is the search.
Every place resolves to a ZIP code, so one string (the ZIP) carries the place through the Deal
Finder's request (`FindDeals.zip`) and the cache, and each source turns it into what it needs.

Without a place in the message, searches use the server's own: YARDSALE_ZIP if it's set,
otherwise each site's default (Craigslist's YARDSALE_CRAIGSLIST_CITY, sfbay; OfferUp's
YARDSALE_OFFERUP_ZIP, 94103). `describe` names the place searched, for the reply.

The table is small on purpose: the Bay Area for the demo, and the cities with big campuses.
A ZIP code that isn't in it still works: its first three digits pick the Craigslist site.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Place:
    name: str  # as the reply says it: "Berkeley"
    zip_code: str  # 5 digits; what the sources search around
    craigslist: str  # the Craigslist site: "sfbay"
    facebook: str = ""  # Marketplace's city slug, when it has one


_SF = Place("San Francisco", "94103", "sfbay", "sanfrancisco")
_OAKLAND = Place("Oakland", "94612", "sfbay", "oakland")
_SAN_JOSE = Place("San Jose", "95113", "sfbay", "sanjose")
_LA = Place("Los Angeles", "90012", "losangeles", "la")
# Names and nicknames, longest first when matched. Lowercase, single spaces.
PLACES: dict[str, Place] = {
    "san francisco": _SF,
    "sf": _SF,
    "the city": _SF,
    "berkeley": Place("Berkeley", "94704", "sfbay", "berkeley"),
    "uc berkeley": Place("Berkeley", "94720", "sfbay", "berkeley"),
    "oakland": _OAKLAND,
    "east bay": Place("the East Bay", "94612", "sfbay", "oakland"),
    "emeryville": Place("Emeryville", "94608", "sfbay", "oakland"),
    "alameda": Place("Alameda", "94501", "sfbay", "oakland"),
    "albany": Place("Albany", "94706", "sfbay", "berkeley"),
    "richmond": Place("Richmond", "94804", "sfbay", "oakland"),
    "walnut creek": Place("Walnut Creek", "94596", "sfbay", "oakland"),
    "hayward": Place("Hayward", "94541", "sfbay", "oakland"),
    "fremont": Place("Fremont", "94538", "sfbay", "sanjose"),
    "san jose": _SAN_JOSE,
    "south bay": Place("the South Bay", "95113", "sfbay", "sanjose"),
    "santa clara": Place("Santa Clara", "95050", "sfbay", "sanjose"),
    "sunnyvale": Place("Sunnyvale", "94086", "sfbay", "sanjose"),
    "cupertino": Place("Cupertino", "95014", "sfbay", "sanjose"),
    "mountain view": Place("Mountain View", "94041", "sfbay", "sanjose"),
    "palo alto": Place("Palo Alto", "94301", "sfbay", "sanjose"),
    "stanford": Place("Stanford", "94305", "sfbay", "sanjose"),
    "menlo park": Place("Menlo Park", "94025", "sfbay", "sanjose"),
    "redwood city": Place("Redwood City", "94063", "sfbay", "sanfrancisco"),
    "san mateo": Place("San Mateo", "94401", "sfbay", "sanfrancisco"),
    "peninsula": Place("the Peninsula", "94401", "sfbay", "sanfrancisco"),
    "daly city": Place("Daly City", "94014", "sfbay", "sanfrancisco"),
    "north bay": Place("the North Bay", "94901", "sfbay", "sanfrancisco"),
    "santa cruz": Place("Santa Cruz", "95060", "sfbay", "sanjose"),
    "davis": Place("Davis", "95616", "sacramento", "sacramento"),
    "sacramento": Place("Sacramento", "95814", "sacramento", "sacramento"),
    "los angeles": _LA,
    "la": _LA,
    "ucla": Place("Westwood", "90024", "losangeles", "la"),
    "westwood": Place("Westwood", "90024", "losangeles", "la"),
    "usc": Place("Los Angeles", "90007", "losangeles", "la"),
    "riverside": Place("Riverside", "92507", "inlandempire", "riverside"),
    "ucr": Place("Riverside", "92521", "inlandempire", "riverside"),
    "irvine": Place("Irvine", "92617", "orangecounty", "irvine"),
    "uci": Place("Irvine", "92697", "orangecounty", "irvine"),
    "san diego": Place("San Diego", "92101", "sandiego", "sandiego"),
    "ucsd": Place("La Jolla", "92093", "sandiego", "sandiego"),
    "santa barbara": Place("Santa Barbara", "93101", "santabarbara", "santabarbara"),
    "ucsb": Place("Isla Vista", "93117", "santabarbara", "santabarbara"),
    "seattle": Place("Seattle", "98101", "seattle", "seattle"),
    "portland": Place("Portland", "97204", "portland", "portland"),
    "new york": Place("New York", "10001", "newyork", "nyc"),
    "nyc": Place("New York", "10001", "newyork", "nyc"),
    "boston": Place("Boston", "02108", "boston", "boston"),
    "chicago": Place("Chicago", "60601", "chicago", "chicago"),
    "austin": Place("Austin", "78701", "austin", "austin"),
}
# The first three digits of a ZIP code -> its Craigslist site, for ZIP codes not named above.
_ZIP3: tuple[tuple[int, int, str], ...] = (
    (900, 918, "losangeles"),
    (919, 921, "sandiego"),
    (922, 925, "inlandempire"),
    (926, 928, "orangecounty"),
    (930, 931, "santabarbara"),
    (940, 951, "sfbay"),
    (942, 942, "sacramento"),
    (956, 958, "sacramento"),
    (980, 981, "seattle"),
    (970, 972, "portland"),
    (100, 104, "newyork"),
    (21, 22, "boston"),
    (606, 606, "chicago"),
    (787, 787, "austin"),
)
# How Craigslist's own sites read, for a reply about a site-wide search.
SITE_NAMES = {"sfbay": "the SF Bay Area", "losangeles": "Los Angeles", "newyork": "New York"}
_ZIP = re.compile(r"\b(\d{5})(?:-\d{4})?\b")
_NEAR = r"(?:in|near|around|by|at|close\s+to|within\s+\d+\s*(?:mi|miles?)\s+of|from)"
_NAMES = "|".join(re.escape(name) for name in sorted(PLACES, key=len, reverse=True))
# Names that are also ordinary words or brands are only places after "in", "near", ...
_NOT_BARE = {"la", "the city", "richmond", "portland", "peninsula", "usc", "cal"}
_BARE = "|".join(
    re.escape(name) for name in sorted(PLACES, key=len, reverse=True) if name not in _NOT_BARE
)
# "in Berkeley", "near 94704", "around sf"; a bare name only when it ends the message, so
# "sf giants hat" stays a search.
_PLACE = re.compile(
    rf"\b{_NEAR}\s+(?:the\s+)?(?:zip(?:\s*code)?\s+)?(?P<zip>\d{{5}})(?:-\d{{4}})?\b|"
    rf"\b{_NEAR}\s+(?:the\s+)?(?P<named>{_NAMES})\b|"
    rf"\b(?P<bare_zip>\d{{5}})\s*$|"
    rf"(?:^|\s)(?P<last>{_BARE})\s*$",
    re.IGNORECASE,
)


def site_for_zip(zip_code: str) -> str:
    """The Craigslist site for a ZIP code, or "" when the table doesn't cover it."""
    if not re.fullmatch(r"\d{5}", zip_code or ""):
        return ""
    for place in PLACES.values():
        if place.zip_code == zip_code:
            return place.craigslist
    prefix = int(zip_code[:3])
    for low, high, site in _ZIP3:
        if low <= prefix <= high:
            return site
    return ""


def from_zip(zip_code: str) -> Place | None:
    """A ZIP code -> a place, named after a city in the table when it's one of its ZIP codes."""
    if not re.fullmatch(r"\d{5}", zip_code or ""):
        return None
    for place in PLACES.values():
        if place.zip_code == zip_code:
            return place
    return Place(zip_code, zip_code, site_for_zip(zip_code))


def split(text: str) -> tuple[Place | None, str]:
    """(the place a message names, the message without it). (None, text) when it names none."""
    found = _PLACE.search(text or "")
    if found is None:
        return None, text
    rest = " ".join((text[: found.start()] + " " + text[found.end() :]).split())
    zip_code = found.group("zip") or found.group("bare_zip")
    if zip_code:
        return from_zip(zip_code), rest
    name = " ".join((found.group("named") or found.group("last") or "").lower().split())
    return PLACES.get(name), rest


def zip_for(text: str) -> str:
    """The ZIP code of the place a message names, or ""."""
    place, _ = split(text)
    return place.zip_code if place else ""


def server_zip() -> str:
    """YARDSALE_ZIP: where this server's buyers and sellers are. "" means each site's default."""
    raw = os.getenv("YARDSALE_ZIP", "").strip()
    return raw if re.fullmatch(r"\d{5}", raw) else ""


def describe(zip_code: str, default_site: str = "") -> str:
    """The place a search covered, for the reply: "Berkeley (94704)", or the default site's
    name ("the SF Bay Area") when the search used each site's own default."""
    place = from_zip(zip_code)
    if place is not None:
        if place.name == zip_code:
            return f"ZIP code {zip_code}"
        return f"{place.name} ({place.zip_code})"
    site = default_site or os.getenv("YARDSALE_CRAIGSLIST_CITY", "sfbay").strip() or "sfbay"
    return SITE_NAMES.get(site, site)
