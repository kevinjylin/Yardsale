"""Buyer search over active listings: plain word matching, no paid service.

A listing matches when its public words (title, brand, model, category, details) share a word
with the search. More shared words rank higher; ties go to the cheaper item. A budget filters on
the asking price only, so search results never hint at a floor.
"""

from __future__ import annotations

import re

from yardsale.store import Listing

STOPWORDS = {
    "a", "an", "and", "any", "anything", "buy", "can", "do", "find", "for", "get", "got",
    "have", "i", "im", "in", "is", "it", "item", "items", "like", "looking", "me", "my", "need",
    "of", "on", "or", "please", "sale", "search", "sell", "selling", "show", "some", "something",
    "the", "there", "to", "used", "want", "what", "whats", "with", "you", "your",
}  # fmt: skip
CHEAP = {"cheap", "cheaper", "cheapest", "affordable", "budget", "inexpensive", "bargain"}
LIMIT = 5


def words(text: str) -> list[str]:
    """Lowercase words with a trailing plural s dropped, so "bikes" finds "bike"."""
    found = re.findall(r"[a-z0-9]+", text.lower().replace("'", ""))
    return [w[:-1] if len(w) > 3 and w.endswith("s") and not w.endswith("ss") else w for w in found]


def search(
    listings: list[Listing], query: str, budget_cents: int | None = None, limit: int = LIMIT
) -> list[Listing]:
    wanted = [w for w in words(query) if w not in STOPWORDS and w not in CHEAP]
    affordable = [
        listing
        for listing in listings
        if budget_cents is None or listing.asking_cents <= budget_cents
    ]
    if not wanted:  # "what's for sale?": everything, cheapest first
        return sorted(affordable, key=lambda listing: listing.asking_cents)[:limit]
    scored: list[tuple[int, Listing]] = []
    for listing in affordable:
        have = set(words(listing.searchable_text()))
        score = sum(1 for w in set(wanted) if w in have)
        if score:
            scored.append((score, listing))
    scored.sort(key=lambda pair: (-pair[0], pair[1].asking_cents, pair[1].item_id))
    return [listing for _, listing in scored[:limit]]
