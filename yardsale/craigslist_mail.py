"""Craigslist by email: a listing's own agent haggles with buyers who email its Craigslist post.

Craigslist bans automated posting and scraping, so Yardsale never touches craigslist.org. The
seller says "craigslist" in the ASI:One chat and gets a ready-to-paste title, price, and
description, and posts it by hand with Yardsale's inbox (YARDSALE_MAIL_ADDRESS) as the posting's
email. A buyer who writes to the post goes through Craigslist's relay, which forwards the message
to that inbox from an anonymised address at craigslist.org.

The item's agent negotiates; email is only the channel. The Market polls the inbox (IMAP) every
POLL_EVERY seconds and turns each buyer's email into the same messages chat buyers' offers
become (bureau_server.negotiate), sent to the item's uAgent in this server or hosted on
Agentverse, with buyer_ref "email-<hash of the relay address>":

- A dollar amount becomes an Offer. The item agent agrees at or above the seller's floor, or
  counters without showing it; its Counter becomes the reply.
- "deal", "I'll take it", "yes" and the like, while a price is on the table, become an
  AcceptCounter at that price. The item agent closes the deal with the store's conditional
  update (so two buyers can't both buy), and the reply sets up the handoff: the buyer pays the
  seller in person, at a public spot on the seller's campus when the seller gave a school. No
  payment link: live on October 25, Craigslist's relay delivered a counter but silently dropped
  the reply carrying a Stripe link.
- Anything else gets the asking price and an invitation to make an offer.

If the item agent doesn't answer in time, the buyer gets "let me check and get back to you"
once, the email stays unread, and the next check sends it to the agent again. No price is ever
worked out here. Replies go back through the relay (SMTP) in the buyer's thread.

**Which post an email is about.** The subject is the buyer's own (live, a buyer wrote
"Waterbottle" to a ThermoFlask post), so the subject is the last resort. Craigslist's relay adds
a footer to every buyer's email with "Original craigslist post: <link>", and the link ends in the
post's ID. In order:

1. The post ID in that link, looked up in the post IDs this module has saved (craigslist_post_ids).
2. The link's slug ("san-francisco-thermoflask-double-wall": the city, then the title's first
   words) against every active post's title, when exactly one post fits best.
3. A posted title in the subject.

Post IDs are learned three ways: from Craigslist's posting confirmation ("cl posting: <title>",
from robot@craigslist.org, which is then marked read), from the first buyer email matched by slug
or subject, and from the seller pasting the post's link (record_post_link). An email that matches
nothing stays unread and is tried again once a post or post ID is recorded, or every
RETRY_SKIPPED seconds; the log says why it was skipped, never what it said.

**Craigslist chat.** Craigslist also offers an in-site chat. Its "new craigslist chat message"
emails carry no text, and reading the chat would mean logging in to craigslist.org, which its
terms forbid. Those notices are counted (chat_notices_since) and marked read, so the chat can tell
the seller a buyer wrote there; the post text asks sellers to choose email and turn chat off.

Mail that isn't from a Craigslist address is never downloaded. The Market can't message an
ASI:One conversation unprompted (it keeps only a hash of the session), so the seller sees
Craigslist buyers, sales, and the latest exchanges (recent_exchanges) when they ask.

The mail password stays in .env. Logs name a buyer by a short hash and a listing by its item ID;
email addresses, subjects, message text, prices, and the password never appear in them. What a
buyer wrote and what the agent answered (300 characters each) are kept only in SQLite, for the
seller.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import html
import imaplib
import logging
import os
import re
import smtplib
import sqlite3
import sys
import time
import weakref
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from email import message_from_bytes, policy
from email.message import EmailMessage
from email.utils import getaddresses, make_msgid
from typing import TYPE_CHECKING, Literal, Protocol

from uagents import Context

from yardsale import campus, students
from yardsale.advice import listing_description
from yardsale.common import PipelineError
from yardsale.models import AcceptCounter, Counter, Deal, Offer, OfferRejected
from yardsale.pricing import PriceError, parse_usd, usd, valid_cents
from yardsale.store import ACTIVE, PROPOSED, SOLD, Listing, Store

if TYPE_CHECKING:
    from yardsale.craigslist_buyer import Step as BuyerStep

POLL_EVERY = 10.0  # seconds between inbox checks
MAX_TITLE = 70  # Craigslist's limit for a posting title
MAX_PER_POLL = 20  # messages read per check; the rest wait for the next one
REPLIES_PER_HOUR = 20  # per buyer: stops two auto-responders from answering each other forever
RETRY_SKIPPED = 300.0  # seconds before an email that matched no post is read again
EXCHANGE_CHARS = 300  # characters of a buyer's email and of our reply kept for the seller
DEFAULT_IMAP_HOST = "imap.gmail.com"
DEFAULT_SMTP_HOST = "smtp.gmail.com"
IMAP_PORT, SMTP_PORT = 993, 465  # both over TLS
TIMEOUT = 30.0  # seconds for one IMAP or SMTP connection
SIGNATURE = "\n\n- the seller's Yardsale agent"
# Craigslist's own mail (posting confirmations, "POST/EDIT/DELETE: <title>") comes from these.
SYSTEM_SENDERS = {"robot", "noreply", "no-reply", "donotreply", "do-not-reply", "support", "abuse"}

log = logging.getLogger("yardsale.craigslist_mail")


class MailConfigError(ValueError):
    pass


@dataclass(frozen=True)
class MailSettings:
    address: str  # Yardsale's inbox: the email the Craigslist post is made with
    password: str = field(repr=False)  # an app password, never the account's own
    imap_host: str = DEFAULT_IMAP_HOST
    smtp_host: str = DEFAULT_SMTP_HOST


def settings_from_env() -> MailSettings | None:
    """YARDSALE_MAIL_* settings, or None when Craigslist email is off. Raises MailConfigError."""
    address = os.getenv("YARDSALE_MAIL_ADDRESS", "").strip()
    # Gmail shows app passwords in groups of four; the spaces aren't part of it.
    password = os.getenv("YARDSALE_MAIL_PASSWORD", "").replace(" ", "").strip()
    if not address and not password:
        return None
    if "@" not in address:
        raise MailConfigError("YARDSALE_MAIL_ADDRESS must be the inbox's email address")
    if not password:
        raise MailConfigError("YARDSALE_MAIL_PASSWORD (an app password) isn't set")
    return MailSettings(
        address=address,
        password=password,
        imap_host=os.getenv("YARDSALE_IMAP_HOST", "").strip() or DEFAULT_IMAP_HOST,
        smtp_host=os.getenv("YARDSALE_SMTP_HOST", "").strip() or DEFAULT_SMTP_HOST,
    )


def short_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


# --- Saved state -------------------------------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS craigslist_posts (
    item_id TEXT PRIMARY KEY REFERENCES listings (item_id),
    title TEXT NOT NULL,
    posted_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS craigslist_buyers (
    item_id TEXT NOT NULL REFERENCES listings (item_id),
    buyer_ref TEXT NOT NULL,
    price_cents INTEGER NOT NULL,
    offer_cents INTEGER NOT NULL,
    agreed INTEGER NOT NULL,
    updated_at REAL NOT NULL,
    PRIMARY KEY (item_id, buyer_ref)
);
CREATE TABLE IF NOT EXISTS craigslist_messages (
    message_key TEXT PRIMARY KEY,
    answered_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS craigslist_post_ids (
    post_id TEXT PRIMARY KEY,
    item_id TEXT NOT NULL REFERENCES listings (item_id),
    saved_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS craigslist_chat_notices (
    message_key TEXT PRIMARY KEY,
    received_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS craigslist_exchanges (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id TEXT NOT NULL REFERENCES listings (item_id),
    buyer_ref TEXT NOT NULL,
    at REAL NOT NULL,
    buyer_said TEXT NOT NULL,
    we_said TEXT NOT NULL,
    kind TEXT NOT NULL
);
"""
_ready: weakref.WeakSet[Store] = weakref.WeakSet()


def _db(store: Store) -> sqlite3.Connection:
    """The store's connection, with this module's tables. They live in the same file as the
    listings, so a restart keeps which listings are posted and which emails were answered.
    (Message keys are hashes: no Message-ID, which can name a sender, is kept.)"""
    db = store._db
    if store not in _ready:
        db.executescript(SCHEMA)
        _ready.add(store)
    return db


def _words(text: str) -> str:
    """Lowercase words and numbers, padded, so titles match whatever punctuation Craigslist or
    a mail client adds."""
    return " " + " ".join(re.findall(r"[a-z0-9]+", text.lower())) + " "


def posting_title(listing: Listing) -> str:
    title = " ".join(listing.title.split())
    if len(title) <= MAX_TITLE:
        return title
    return title[:MAX_TITLE].rsplit(" ", 1)[0].rstrip(" ,;:-")


def post(store: Store, listing: Listing) -> str:
    """Record that the seller is posting this listing on Craigslist and return the title to
    use. Asking again returns the same title. A title that another active post already uses gets
    " - 2" (and so on), so every buyer's email names exactly one listing."""
    db = _db(store)
    row = db.execute(
        "SELECT title FROM craigslist_posts WHERE item_id = ?", (listing.item_id,)
    ).fetchone()
    if row:
        return str(row["title"])
    taken = {
        _words(str(other["title"]))
        for other in db.execute(
            "SELECT p.title FROM craigslist_posts p JOIN listings l USING (item_id) "
            "WHERE l.status = ?",
            (ACTIVE,),
        )
    }
    base = posting_title(listing)
    title, n = base, 1
    while _words(title) in taken:
        n += 1
        suffix = f" - {n}"
        title = base[: MAX_TITLE - len(suffix)].rstrip() + suffix
    db.execute(
        "INSERT INTO craigslist_posts (item_id, title, posted_at) VALUES (?, ?, ?)",
        (listing.item_id, title, store.clock()),
    )
    return title


def posted_title(store: Store, item_id: str) -> str | None:
    row = (
        _db(store)
        .execute("SELECT title FROM craigslist_posts WHERE item_id = ?", (item_id,))
        .fetchone()
    )
    return str(row["title"]) if row else None


def match_listing(store: Store, subject: str) -> Listing | None:
    """The posted listing a buyer's subject names. The longest title in the subject wins, then
    a listing still for sale, then the newest post."""
    words = _words(subject)
    best: tuple[int, bool, float, str] | None = None
    for row in _db(store).execute(
        "SELECT p.item_id, p.title, p.posted_at, l.status FROM craigslist_posts p "
        "JOIN listings l USING (item_id)"
    ):
        title = _words(str(row["title"]))
        if title.strip() and title in words:
            key = (len(title), row["status"] == ACTIVE, float(row["posted_at"]), row["item_id"])
            if best is None or key > best:
                best = key
    return store.get(best[3]) if best else None


# A post's link: https://www.craigslist.org/view/d/<city-and-title-words>/<id> (new), or
# https://sfbay.craigslist.org/sfc/bik/d/<slug>/7791234567.html (old). The chat notice's
# ".../d/APPOK/#chat=..." has no ID after the slug, so it never matches.
_POST_LINK = re.compile(
    r"https?://(?:[a-z0-9-]+\.)*craigslist\.org/(?:[a-z0-9-]+/)*?d/(?P<slug>[a-z0-9-]+)/"
    r"(?P<id>[A-Za-z0-9]{6,40})(?:\.html?)?(?![A-Za-z0-9/])",
    re.IGNORECASE,
)
# Posting confirmations link to the post's manage page, which names its numeric ID.
_MANAGE_LINK = re.compile(
    r"https?://post\.craigslist\.org/manage/(?P<id>\d{6,15})(?!\d)", re.IGNORECASE
)
_FOOTER_START = re.compile(r"original craigslist post\s*:", re.IGNORECASE)


@dataclass(frozen=True)
class PostLink:
    post_id: str  # case kept: new-style IDs are mixed case
    slug: str  # lowercase words joined by "-", the city first; "" when the link has none


def post_links(text: str) -> list[PostLink]:
    """Every Craigslist post link in `text` (plain or HTML), the one after "Original craigslist
    post:" first, then in order. Each post ID once."""
    text = html.unescape(text)
    footer = _FOOTER_START.search(text)
    found = sorted(
        _POST_LINK.finditer(text),
        key=lambda m: (footer is None or m.start() < footer.start(), m.start()),
    )
    links: list[PostLink] = []
    for match in found:
        if all(link.post_id != match.group("id") for link in links):
            links.append(PostLink(match.group("id"), match.group("slug").lower()))
    return links


def _post_ids_in(text: str) -> list[str]:
    """Post IDs in a posting confirmation: post links' and manage pages'."""
    ids = [link.post_id for link in post_links(text)]
    for match in _MANAGE_LINK.finditer(html.unescape(text)):
        if match.group("id") not in ids:
            ids.append(match.group("id"))
    return ids


def _save_post_id(store: Store, post_id: str, item_id: str, replace: bool = False) -> None:
    verb = "INSERT OR REPLACE" if replace else "INSERT OR IGNORE"
    _db(store).execute(
        f"{verb} INTO craigslist_post_ids (post_id, item_id, saved_at) VALUES (?, ?, ?)",
        (post_id, item_id, store.clock()),
    )


def listing_for_post(store: Store, post_id: str) -> Listing | None:
    """The listing a saved Craigslist post ID belongs to."""
    row = (
        _db(store)
        .execute("SELECT item_id FROM craigslist_post_ids WHERE post_id = ?", (post_id,))
        .fetchone()
    )
    return store.get(str(row["item_id"])) if row else None


def record_post_link(store: Store, item_id: str, url: str) -> bool:
    """Save the post ID in a link the seller pasted (their live Craigslist post), so buyers'
    emails about it match whatever their subject says. Records the listing as posted if the
    chat hadn't. False when `url` isn't a Craigslist post link or the listing doesn't exist; a
    link saved before for another listing moves to this one."""
    links = post_links(url)
    listing = store.get(item_id)
    if not links or listing is None:
        return False
    post(store, listing)
    _save_post_id(store, links[0].post_id, item_id, replace=True)
    return True


def match_slug(store: Store, slug: str) -> Listing | None:
    """The active post whose title a link's slug names, when exactly one fits best. The slug is
    the city, then the title's first words ("san-francisco-thermoflask-double-wall"), so the
    longest run of slug words that ends the slug and is all in the title counts (its last word
    may be cut short). At least 2 words must fit, unless the title has only one."""
    words = re.findall(r"[a-z0-9]+", slug.lower())
    if not words:
        return None
    best, fits = 0, []
    for row in _db(store).execute(
        "SELECT p.item_id, p.title FROM craigslist_posts p JOIN listings l USING (item_id) "
        "WHERE l.status = ?",
        (ACTIVE,),
    ):
        title = _words(str(row["title"])).split()
        fit = 0
        for start in range(len(words)):
            *whole, last = words[start:]
            if all(w in title for w in whole) and any(t.startswith(last) for t in title):
                fit = len(words) - start
                break
        if fit < min(2, len(title)) or fit == 0:
            continue
        if fit > best:
            best, fits = fit, [str(row["item_id"])]
        elif fit == best:
            fits.append(str(row["item_id"]))
    return store.get(fits[0]) if len(fits) == 1 else None


@dataclass(frozen=True)
class Match:
    listing: Listing | None
    how: str  # "post id", "slug", "subject", or "none"
    reason: str  # for the log when nothing matched; never the email's content


def match_email(store: Store, message: EmailMessage) -> Match:
    """The posted listing a buyer's email is about: by the post ID in Craigslist's footer link,
    then the link's slug, then a posted title in the subject. A post ID first seen here (matched
    by slug or subject) is saved, so the next email matches by ID."""
    subject = str(message.get("Subject", ""))
    links = post_links(_all_text(message))
    for link in links:
        listing = listing_for_post(store, link.post_id)
        if listing is not None:
            return Match(listing, "post id", "")
    listing, how = None, "none"
    for link in links:
        listing = match_slug(store, link.slug)
        if listing is not None:
            how = "slug"
            break
    if listing is None:
        listing = match_listing(store, subject)
        how = "subject" if listing is not None else "none"
    if listing is None:
        found = "post link not recorded" if links else "no post link"
        words = len(_words(subject).split())
        return Match(None, "none", f"{found}, subject words {words}")
    if links:
        _save_post_id(store, links[0].post_id, listing.item_id)
    return Match(listing, how, "")


def posts_version(store: Store) -> tuple[int, int, float]:
    """Changes whenever a post or post ID is recorded: emails skipped before are read again."""
    db = _db(store)
    posts = db.execute("SELECT COUNT(*) AS n FROM craigslist_posts").fetchone()
    ids = db.execute(
        "SELECT COUNT(*) AS n, COALESCE(MAX(saved_at), 0) AS at FROM craigslist_post_ids"
    ).fetchone()
    return int(posts["n"]), int(ids["n"]), float(ids["at"])


# --- Craigslist chat notices and buyer exchanges, for the seller -------------------------------


def _record_chat_notice(store: Store, key: str) -> None:
    _db(store).execute(
        "INSERT OR IGNORE INTO craigslist_chat_notices VALUES (?, ?)", (key, store.clock())
    )


def chat_notices_since(store: Store, since: float) -> int:
    """How many "new craigslist chat message" notices arrived after `since` (store clock time).
    The notices don't say which post or what was written, and Yardsale can't read Craigslist
    chat, so the chat can only tell sellers "a buyer wrote in Craigslist chat; I can't read
    those, so ask buyers to email"."""
    row = (
        _db(store)
        .execute(
            "SELECT COUNT(*) AS n FROM craigslist_chat_notices WHERE received_at > ?", (since,)
        )
        .fetchone()
    )
    return int(row["n"])


@dataclass(frozen=True)
class Exchange:
    item_id: str
    title: str  # the posted title, or the listing's
    buyer_label: str  # "Buyer 1", "Buyer 2", ... in the order they first wrote about this item
    at: float  # store clock time of our reply
    buyer_said: str  # the buyer's new words, footer and quotes dropped, at most 300 characters
    we_said: str  # our reply, at most 300 characters
    kind: str  # Answer.kind: "asking", "counter", "agreed", "deal", ...


def _record_exchange(store: Store, answer: Answer) -> None:
    _db(store).execute(
        "INSERT INTO craigslist_exchanges (item_id, buyer_ref, at, buyer_said, we_said, kind) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
            answer.item_id,
            answer.buyer_ref,
            store.clock(),
            answer.buyer_said[:EXCHANGE_CHARS],
            answer.text[:EXCHANGE_CHARS],
            answer.kind,
        ),
    )


def recent_exchanges(store: Store, owner: str, limit: int = 5) -> list[Exchange]:
    """The latest emails answered on `owner`'s listings, newest first: what each buyer wrote and
    what the item's agent answered. Seller-only: the text is never logged."""
    db = _db(store)
    rows = db.execute(
        "SELECT e.item_id, e.buyer_ref, e.at, e.buyer_said, e.we_said, e.kind, "
        "l.title AS listing_title, p.title AS posted "
        "FROM craigslist_exchanges e JOIN listings l USING (item_id) "
        "LEFT JOIN craigslist_posts p USING (item_id) "
        "WHERE l.owner = ? ORDER BY e.at DESC, e.id DESC LIMIT ?",
        (owner, max(0, limit)),
    ).fetchall()
    labels: dict[str, dict[str, str]] = {}
    exchanges: list[Exchange] = []
    for row in rows:
        item_id = str(row["item_id"])
        if item_id not in labels:
            buyers = db.execute(
                "SELECT buyer_ref FROM craigslist_exchanges WHERE item_id = ? "
                "GROUP BY buyer_ref ORDER BY MIN(id)",
                (item_id,),
            )
            labels[item_id] = {
                str(b["buyer_ref"]): f"Buyer {n}" for n, b in enumerate(buyers, start=1)
            }
        exchanges.append(
            Exchange(
                item_id=item_id,
                title=str(row["posted"] or row["listing_title"]),
                buyer_label=labels[item_id].get(str(row["buyer_ref"]), "A buyer"),
                at=float(row["at"]),
                buyer_said=str(row["buyer_said"]),
                we_said=str(row["we_said"]),
                kind=str(row["kind"]),
            )
        )
    return exchanges


def _answered(store: Store, key: str) -> bool:
    row = (
        _db(store)
        .execute("SELECT 1 FROM craigslist_messages WHERE message_key = ?", (key,))
        .fetchone()
    )
    return row is not None


def _mark_answered(store: Store, key: str) -> None:
    _db(store).execute(
        "INSERT OR IGNORE INTO craigslist_messages VALUES (?, ?)", (key, store.clock())
    )


# --- The seller's side, in the ASI:One chat ----------------------------------------------------

_POST_COMMAND = re.compile(
    r"^\s*(?:(?:yes|yeah|ok(?:ay)?|sure)\b[,.!]?\s*)?(?:also\s+)?"
    r"(?:(?:post|list|put|sell|add)\s+(?:it\s+|this\s+|#?(?P<a>\d+)\s+)?(?:on|to)\s+craigslist"
    r"(?:\s+too)?|craigslist(?:\s+post|\s+too)?(?:\s+#?(?P<b>\d+))?)\s*[.!]?\s*$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class PostCommand:
    pick: int | None  # which of the seller's listings, numbered as in "my listings"


def post_command(text: str) -> PostCommand | None:
    """'craigslist', 'craigslist 2', 'post 2 on craigslist', 'post it on craigslist'."""
    found = _POST_COMMAND.match(text)
    if not found:
        return None
    number = found.group("a") or found.group("b")
    return PostCommand(int(number) if number else None)


def description(listing: Listing) -> str:
    """What Claude saw (brand, model, condition, details) and what the market says
    (advice.listing_description), then how to make an offer. Never the floor."""
    return listing_description(listing) + "\nEmail me an offer through Craigslist. I reply fast."


def post_text(listing: Listing, title: str, settings: MailSettings | None) -> str:
    """The post to paste. ASI:One's Markdown turns a single newline into a space, so every part
    is its own paragraph (blank lines between)."""
    dollars = -(-listing.asking_cents // 100)  # Craigslist takes whole dollars
    if settings is not None:
        contact = (
            f"For contact info, choose email and use {settings.address}, so buyers' emails "
            "reach me. If the form offers Craigslist chat, turn it off: I can only answer "
            "email, and can't read Craigslist chat. Use exactly this title: it's how I match "
            "Craigslist's emails to your item."
        )
    else:
        contact = (
            "Use exactly this title. (Email replies are off on this server: "
            "YARDSALE_MAIL_ADDRESS isn't set.)"
        )
    paragraphs = [
        f"Here's a Craigslist post for your {listing.title}. Craigslist doesn't allow automated "
        "posting, so post it yourself at craigslist.org (create a posting, then for sale by "
        "owner).",
        contact,
        f"Title: {title}",
        f"Price: ${dollars}",
        "Description:",
        *description(listing).splitlines(),
        "When a buyer emails, I answer within seconds: I haggle with your prices (never below "
        "your lowest), and once they say deal I set up where to meet; they pay you in person. Say "
        '"my listings" to see Craigslist buyers.',
        "Once it's up, paste the post's link here, so I know it's yours even when a buyer "
        "writes their own subject.",
    ]
    return "\n\n".join(p.strip() for p in paragraphs if p.strip())


def post_reply(store: Store, owner: str, command: PostCommand) -> str | None:
    """The chat's answer to a post command, or None when this conversation has nothing listed
    (then the words aren't about posting, and the chat reads them as usual)."""
    active = store.owned(owner, ACTIVE)
    if not active:
        if store.owned(owner, PROPOSED):
            return 'Approve your listing first, then say "craigslist" to get a post for it.'
        return None
    if command.pick is None and len(active) > 1:
        return 'Which one? Say "craigslist 2", using the numbers from "my listings".'
    index = (command.pick or 1) - 1
    if not 0 <= index < len(active):
        return f"Pick a number from 1 to {len(active)}."
    listing = active[index]
    title = post(store, listing)
    try:
        settings = settings_from_env()
    except MailConfigError:
        settings = None
    return post_text(listing, title, settings)


def meeting_place(store: Store, listing: Listing) -> str:
    """Where a Craigslist buyer and the seller meet: the best public spot on the seller's campus,
    as plain words ("Rivera Library, University of California Riverside"), or "" when the seller
    gave no school or the campus isn't mapped. No map link: Craigslist's relay may drop it."""
    school = students.school_of(store, listing.owner)
    if not school:
        return ""
    try:
        spot = students.where_to_meet(store, school).spot
    except students.StudentError:
        return ""
    return ", ".join(part for part in (spot.name, campus.school_label_for(school)) if part)


def seller_lines(store: Store, listings: list[Listing]) -> list[str]:
    """What "my listings" adds for listings posted on Craigslist."""
    db = _db(store)
    lines: list[str] = []
    for listing in listings:
        title = posted_title(store, listing.item_id)
        if title is None:
            continue
        if listing.status == ACTIVE:
            row = db.execute(
                "SELECT MAX(offer_cents) AS best FROM craigslist_buyers WHERE item_id = ?",
                (listing.item_id,),
            ).fetchone()
            # A buyer counts from their first email, before any price is agreed or countered.
            buyers = db.execute(
                "SELECT COUNT(*) AS n FROM (SELECT buyer_ref FROM craigslist_buyers "
                "WHERE item_id = ? UNION SELECT buyer_ref FROM craigslist_exchanges "
                "WHERE item_id = ?)",
                (listing.item_id, listing.item_id),
            ).fetchone()["n"]
            line = f'On Craigslist as "{title}": '
            if buyers == 0:
                line += "no buyers have emailed yet."
            else:
                line += f"{buyers} buyer{'s' if buyers > 1 else ''} emailing"
                best = row["best"]
                line += f", highest offer {usd(int(best))}." if best else ", no offer yet."
            lines.append(line)
        elif listing.status == SOLD:
            deal = store.deal_for(listing.item_id)
            if deal is None or not deal.buyer.startswith("chat:email-"):
                continue
            place = meeting_place(store, listing)
            where = f" at {place}" if place else " at the public place they suggest"
            lines.append(
                f"The {listing.title} went to a Craigslist buyer by email for "
                f"{usd(deal.price_cents)}. They pay you in person when you meet{where}; I've "
                "asked them for a time, and their answer shows below."
            )
    return lines


# --- Reading a buyer's email -------------------------------------------------------------------

_URL = re.compile(r"\b(?:https?://|www\.)\S+", re.IGNORECASE)
_EMAIL = re.compile(r"\S+@\S+")
_NUMBER = r"(\d{1,3}(?:,\d{3})+|\d+)(\.\d{1,2})?"
_AMOUNTS = (
    re.compile(r"\$\s?" + _NUMBER),
    re.compile(r"\b" + _NUMBER + r"\s*(?:dollars|bucks|usd)\b", re.IGNORECASE),
    re.compile(
        r"\b(?:offer|do|pay|give|take|accept|about)\s+(?:you\s+)?" + _NUMBER + r"\b"
        r"(?!\s*(?:am|pm|min|mins|minutes|hours|hrs|miles|mi|days|%|:|/))",
        re.IGNORECASE,
    ),
    re.compile(r"^\s*" + _NUMBER + r"\s*[?!.]*\s*$"),
)
_DOLLARS_AND_CENTS = re.compile(
    r"\b(\d{1,6})\s*(?:dollars?|bucks?)\s*(?:and\s+)?(\d{1,2})\s*cents?\b", re.IGNORECASE
)
_NO_DEAL = re.compile(r"\b(?:no|not)\s+(?:a\s+)?deal\b", re.IGNORECASE)
_ACCEPT = re.compile(
    r"\b(?:deal|i'?ll take it|i will take it|i accept|accepted|agreed|let'?s do it|"
    r"works for me|sounds good)\b",
    re.IGNORECASE,
)
_YES = re.compile(r"^\s*(?:yes|yeah|yep|yup|ok|okay|sure|perfect|great)\b", re.IGNORECASE)


_RELAY_FOOTER = re.compile(
    r"^(?:original craigslist post|about craigslist mail|please flag unwanted messages)\b",
    re.IGNORECASE,
)


def new_text(body: str) -> str:
    """The buyer's own words: quoted replies, signatures, and footers (Craigslist's relay
    footer, with the post's link and title words, included) dropped."""
    kept: list[str] = []
    for line in body.splitlines():
        stripped = line.strip()
        if _RELAY_FOOTER.match(stripped):
            break
        if stripped.startswith(">"):
            continue
        if stripped.lower().endswith("wrote:"):
            if kept and kept[-1].strip().lower().startswith("on "):
                kept.pop()  # "On Fri, Oct 24 ... <someone>\nwrote:"
            break
        if stripped in ("--", "-- ") or re.match(r"^(?:-{5,}|_{5,})", stripped):
            break
        if stripped.lower().startswith(("-----original message", "sent from my")):
            break
        kept.append(line)
    return "\n".join(kept).strip()[:2000]


def offer_cents(text: str) -> int | None:
    """The amount a buyer offers: the last dollar amount they wrote. Numbers in links, email
    addresses, times, and distances aren't offers."""
    plain = _EMAIL.sub(" ", _URL.sub(" ", text))
    found: dict[int, str] = {}
    for pattern in _AMOUNTS:
        for match in pattern.finditer(plain):
            found.setdefault(match.start(1), match.group(1) + (match.group(2) or ""))
    for match in _DOLLARS_AND_CENTS.finditer(plain):
        # "10 dollars and 50 cents" is $10.50, not the $10 the "dollars" pattern sees.
        found[match.start(1)] = f"{match.group(1)}.{int(match.group(2)):02d}"
    for _, amount in sorted(found.items(), reverse=True):
        try:
            cents = parse_usd(amount)
        except PriceError:
            continue
        if valid_cents(cents):
            return cents
    return None


def accepts(text: str) -> bool:
    if _NO_DEAL.search(text):
        return False
    return bool(_ACCEPT.search(text)) or (bool(_YES.match(text)) and "?" not in text)


def body_text(message: EmailMessage) -> str:
    part = message.get_body(preferencelist=("plain", "html"))
    if part is None:
        return ""
    try:
        content = part.get_content()
    except (LookupError, ValueError):
        return ""
    if not isinstance(content, str):
        return ""
    if part.get_content_subtype() == "html":
        content = re.sub(r"(?i)<br\s*/?>|<hr\b[^>]*>|</p>|</div>|</tr>", "\n", content)
        content = html.unescape(re.sub(r"<[^>]+>", " ", content))
    return content


def _all_text(message: EmailMessage) -> str:
    """Every text part, HTML as it came (links live in its tags), for finding post links."""
    texts: list[str] = []
    for part in message.walk():
        if part.get_content_maintype() != "text" or part.is_attachment():
            continue
        try:
            content = part.get_content()
        except (LookupError, ValueError, AttributeError):
            continue
        if isinstance(content, str):
            texts.append(content)
    return "\n".join(texts)


def _sender(message: EmailMessage) -> tuple[str, str]:
    """(local part, domain) of From, lowercase."""
    for _, address in getaddresses([str(v) for v in message.get_all("From", [])]):
        local, _, domain = address.strip().lower().rpartition("@")
        if local:
            return local, domain
    return "", ""


def _from_craigslist_itself(message: EmailMessage) -> bool:
    local, domain = _sender(message)
    craigslist = domain == "craigslist.org" or domain.endswith(".craigslist.org")
    return craigslist and local in SYSTEM_SENDERS


def _looks_like_system_mail(raw: bytes) -> bool:
    try:
        message = message_from_bytes(raw, policy=policy.default)
    except Exception:
        return False
    return isinstance(message, EmailMessage) and _from_craigslist_itself(message)


_CONFIRMATION = re.compile(
    r"^\s*(?:(?:re|fwd?)\s*:\s*)*(?:cl posting|craigslist posting|your craigslist posting|"
    r"post/edit/delete)\b[^:]*:\s*(?P<title>.+)$",
    re.IGNORECASE,
)


def _is_chat_notice(message: EmailMessage) -> bool:
    subject = " ".join(str(message.get("Subject", "")).lower().split())
    if "craigslist chat" in subject:
        return True
    return "new message in craigslist chat" in " ".join(body_text(message).lower().split())


def relay_address(message: EmailMessage) -> str | None:
    """The Craigslist relay address to answer, or None if this isn't a buyer's relayed email."""
    for header in ("Reply-To", "From"):
        for _, address in getaddresses([str(value) for value in message.get_all(header, [])]):
            local, _, domain = address.strip().lower().rpartition("@")
            relay = domain == "craigslist.org" or domain.endswith(".craigslist.org")
            if local and relay and local not in SYSTEM_SENDERS:
                return f"{local}@{domain}"
    return None


def from_our_seller_agent(raw: bytes) -> bool:
    """True for a relayed email our seller agent wrote (its signature is in the new text)."""
    message = message_from_bytes(raw, policy=policy.default)
    if not isinstance(message, EmailMessage) or relay_address(message) is None:
        return False
    return SIGNATURE.strip() in new_text(body_text(message))


def _automatic(message: EmailMessage) -> bool:
    return str(message.get("Auto-Submitted", "no")).strip().lower() != "no"


# --- Answering: the item's own agent negotiates; email is only the channel ---------------------

# Sends an Offer or AcceptCounter to the item's agent (in this server or hosted on Agentverse)
# and returns its answer: bureau_server.negotiate, the same path chat buyers' offers take.
# Raises PipelineError when the agent can't be reached or doesn't answer in time.
Negotiate = Callable[
    [Context, str, Offer | AcceptCounter], Awaitable[Counter | Deal | OfferRejected]
]

HOLDING = "Thanks! Let me check with the seller and get back to you shortly."


@dataclass
class Answer:
    kind: str  # what happened, for logs and tests: "asking", "counter", "agreed", "deal", ...
    item_id: str
    buyer_ref: str  # "email-<hash>": what the item agent knows this buyer as
    text: str
    key: str  # a hash of the email's Message-ID, recorded once the reply is sent
    buyer_said: str = ""  # the buyer's new words, kept for the seller (never logged)

    @property
    def final(self) -> bool:
        """False for "let me check": the email stays unread and is tried again."""
        return self.kind != "holding"


@dataclass(frozen=True)
class Skip:
    """An email left unread for now, and why (for the log: never its content)."""

    reason: str


@dataclass(frozen=True)
class SystemMail:
    """Craigslist's own email, handled and to be marked read: "chat" or "confirmation"."""

    kind: str


def _remember_price(store: Store, item_id: str, buyer_ref: str, reply: Counter, offer: int) -> None:
    _db(store).execute(
        "INSERT OR REPLACE INTO craigslist_buyers VALUES (?, ?, ?, ?, ?, ?)",
        (item_id, buyer_ref, reply.counter_cents, offer, int(reply.agreed), store.clock()),
    )


def _open_price(store: Store, item_id: str, buyer_ref: str) -> int | None:
    """The item agent's latest counter or agreed price to this buyer, as it last said."""
    row = (
        _db(store)
        .execute(
            "SELECT price_cents FROM craigslist_buyers WHERE item_id = ? AND buyer_ref = ?",
            (item_id, buyer_ref),
        )
        .fetchone()
    )
    return int(row["price_cents"]) if row else None


def _closed(listing: Listing) -> tuple[str, str]:
    if listing.status == SOLD:
        return "sold", f"Sorry, the {listing.title} has sold."
    return "unavailable", f"Sorry, the {listing.title} is no longer available."


class CraigslistAgent:
    """Turns one buyer's email into a message to the item's agent, and its answer into the
    reply. No price is worked out here: the item agent agrees, counters, or sells."""

    def __init__(self, store: Store, settings: MailSettings, negotiate: Negotiate) -> None:
        self.store = store
        self.settings = settings
        self.negotiate = negotiate

    def _handoff(self, listing: Listing, price_cents: int) -> str:
        """The reply that closes a deal: where to meet. No payment words ("pay", "cash",
        "Venmo") and no links: on October 25 Craigslist's relay dropped the reply with a Stripe
        link, and its scam filters look for both. The buyer pays the seller when they meet."""
        place = meeting_place(self.store, listing)
        where = (
            f"How about {place}? It's public and easy to find."
            if place
            else "Where's a public spot near you that works, like a coffee shop or a library?"
        )
        return (
            f"Deal! The {listing.title} is yours for {usd(price_cents)}. Let's meet to hand it "
            f"over. {where} What day and time work for you?"
        )

    def _meeting(self, listing: Listing) -> str:
        """Every later email from the buyer who bought it: their words go to the seller."""
        place = meeting_place(self.store, listing)
        at = f" at {place}" if place else ""
        return (
            f"Thanks! I've passed that on to the seller, who'll meet you{at} to hand over the "
            f"{listing.title}."
        )

    def _now_closed(self, listing: Listing) -> tuple[str, str]:
        return _closed(self.store.get(listing.item_id) or listing)

    async def answer(
        self, ctx: Context, listing: Listing, buyer_ref: str, text: str
    ) -> tuple[str, str]:
        """(kind, reply text) for one email from `buyer_ref` about `listing`."""
        item_id, title = listing.item_id, listing.title
        deal = self.store.deal_for(item_id)
        if deal is not None and deal.buyer == f"chat:{buyer_ref}":  # this buyer bought it
            return "meeting", self._meeting(listing)
        if listing.status != ACTIVE:
            return _closed(listing)

        amount = offer_cents(text)
        price = _open_price(self.store, item_id, buyer_ref)
        if price is not None and accepts(text) and amount in (None, price):
            accepted = await self.negotiate(
                ctx,
                item_id,
                AcceptCounter(item_id=item_id, counter_cents=price, buyer_ref=buyer_ref),
            )
            if isinstance(accepted, Deal):
                return "deal", self._handoff(listing, accepted.price_cents)
            if isinstance(accepted, OfferRejected) and accepted.code == "unavailable":
                return self._now_closed(listing)
            price = None  # the price lapsed: fall through to the asking price
        taking_it = bool(_ACCEPT.search(text)) and not _NO_DEAL.search(text)
        if amount is None and price is None and taking_it:
            amount = listing.asking_cents  # "deal, I'll take it" with nothing on the table
        if amount is not None:
            offer = min(amount, listing.asking_cents)
            reply = await self.negotiate(
                ctx, item_id, Offer(item_id=item_id, offer_cents=offer, buyer_ref=buyer_ref)
            )
            if isinstance(reply, Counter):
                _remember_price(self.store, item_id, buyer_ref, reply, offer)
                if reply.agreed and taking_it:
                    # "Deal, I'll take it for $11" and $11 is agreed: close it now rather than
                    # asking them to say deal a second time.
                    accepted = await self.negotiate(
                        ctx,
                        item_id,
                        AcceptCounter(
                            item_id=item_id, counter_cents=reply.counter_cents, buyer_ref=buyer_ref
                        ),
                    )
                    if isinstance(accepted, Deal):
                        return "deal", self._handoff(listing, accepted.price_cents)
                    if isinstance(accepted, OfferRejected) and accepted.code == "unavailable":
                        return self._now_closed(listing)
                if reply.agreed:
                    return "agreed", (
                        f'{usd(reply.counter_cents)} works for me. Reply "deal" and I\'ll send '
                        "you a payment link to lock it in."
                    )
                if offer * 2 < listing.asking_cents:
                    return "counter", (
                        f"Sorry, that's too low for the {title}. I'm at {usd(reply.counter_cents)}."
                    )
                return "counter", (
                    f"Thanks for the offer! The best I can do on the {title} is "
                    f'{usd(reply.counter_cents)}. Reply "deal" to take it, or send another '
                    "offer."
                )
            if isinstance(reply, OfferRejected) and reply.code == "unavailable":
                return self._now_closed(listing)
        if price is not None:
            return "reminder", (
                f'I\'m at {usd(price)} for the {title}. Reply "deal" to take it at that price, '
                "or send me an offer."
            )
        return "asking", (
            f"Hi! Yes, the {title} is still available. I'm asking {usd(listing.asking_cents)}. "
            "If you'd like it, reply with your offer (just a number is fine)."
        )

    def reply_to(self, message: EmailMessage, relay: str, text: str) -> EmailMessage:
        """A reply in the buyer's thread, through Craigslist's relay."""
        subject = " ".join(str(message.get("Subject", "")).split())
        reply = EmailMessage()
        reply["From"] = self.settings.address
        reply["To"] = relay
        reply["Subject"] = subject if subject.lower().startswith("re:") else f"Re: {subject}"
        reply["Message-ID"] = make_msgid(domain=self.settings.address.rpartition("@")[2])
        original = str(message.get("Message-ID", "")).strip()
        if original:
            reply["In-Reply-To"] = original
            references = " ".join(str(message.get("References", "")).split())
            reply["References"] = f"{references} {original}".strip()
        reply.set_content(text + SIGNATURE)
        return reply

    def system(self, raw: bytes) -> SystemMail | Skip | None:
        """Craigslist's own email (from robot@craigslist.org and the like): a chat notice is
        counted, a posting confirmation saves the post's ID. None for any other email; Skip for
        Craigslist mail that's neither, or a confirmation for a title nobody posted here."""
        message = message_from_bytes(raw, policy=policy.default)
        if not isinstance(message, EmailMessage) or not _from_craigslist_itself(message):
            return None
        key = short_hash(str(message.get("Message-ID", "")).strip() or repr(raw[:4096]))
        if _is_chat_notice(message):
            _record_chat_notice(self.store, key)
            return SystemMail("chat")
        subject = " ".join(str(message.get("Subject", "")).split())
        confirmation = _CONFIRMATION.match(subject)
        if confirmation is None:
            return Skip("a Craigslist system email that isn't a posting confirmation")
        listing = match_listing(self.store, confirmation.group("title"))
        if listing is None:
            return Skip("a posting confirmation for a title not posted here")
        ids = _post_ids_in(_all_text(message))
        for post_id in ids:
            _save_post_id(self.store, post_id, listing.item_id, replace=True)
        log.info(f"Craigslist confirmed the post of {listing.item_id} ({len(ids)} post IDs saved)")
        return SystemMail("confirmation")

    async def handle(
        self, ctx: Context, raw: bytes
    ) -> tuple[Answer, EmailMessage] | Literal["answered"] | Skip:
        """The reply to one raw email; "answered" if it was answered before; Skip (with the
        reason) if it isn't a buyer's email about a posted listing."""
        message = message_from_bytes(raw, policy=policy.default)
        if not isinstance(message, EmailMessage):
            return Skip("unreadable")
        relay = relay_address(message)
        subject = str(message.get("Subject", ""))
        if relay is None:
            return Skip("not from a Craigslist relay address")
        if _automatic(message):
            return Skip("an automatic reply")
        if subject.upper().startswith("POST/EDIT"):
            return Skip("a Craigslist system email")
        match = match_email(self.store, message)
        if match.listing is None:
            return Skip(match.reason)
        listing = match.listing
        key = short_hash(str(message.get("Message-ID", "")).strip() or f"{relay}|{raw[:4096]!r}")
        if _answered(self.store, key):
            return "answered"
        buyer_ref = f"email-{short_hash(relay)}"
        said = new_text(body_text(message))
        try:
            kind, text = await self.answer(ctx, listing, buyer_ref, said)
        except PipelineError:  # the item agent didn't answer in time: try again next check
            kind, text = "holding", HOLDING
        answer = Answer(kind, listing.item_id, buyer_ref, text, key, said)
        return answer, self.reply_to(message, relay, text)

    def answered(self, answer: Answer) -> None:
        _mark_answered(self.store, answer.key)


# --- The inbox ---------------------------------------------------------------------------------


class Mailbox(Protocol):
    """Where Craigslist mail comes from and replies go. Blocking; called from a thread."""

    def unseen(self) -> list[tuple[str, bytes]]:
        """(uid, raw message) for unread mail from a Craigslist address, without marking it
        read."""
        ...

    def mark_seen(self, uid: str) -> None: ...

    def send(self, message: EmailMessage) -> None: ...

    def close(self) -> None: ...


class MailError(RuntimeError):
    pass


def _why(ex: Exception) -> str:
    """For the log: the error's type, plus the mail server's own words for login and connection
    errors ("Invalid credentials", "Connection unexpectedly closed"). Other errors can carry an
    address, so they give only their type."""
    if isinstance(ex, imaplib.IMAP4.error | smtplib.SMTPAuthenticationError) or type(ex) in (
        smtplib.SMTPServerDisconnected,
        smtplib.SMTPConnectError,
    ):
        return f"{type(ex).__name__}: {str(ex)[:160]}"
    return type(ex).__name__


class ImapSmtpMailbox:
    """The real inbox: IMAP over TLS to read, SMTP over TLS to send, with an app password."""

    def __init__(self, settings: MailSettings, timeout: float = TIMEOUT) -> None:
        self.settings = settings
        self.timeout = timeout
        self._imap: imaplib.IMAP4_SSL | None = None

    def _connected(self) -> imaplib.IMAP4_SSL:
        if self._imap is None:
            imap = imaplib.IMAP4_SSL(self.settings.imap_host, IMAP_PORT, timeout=self.timeout)
            imap.login(self.settings.address, self.settings.password)
            status, _ = imap.select("INBOX")
            if status != "OK":
                raise MailError("couldn't open the inbox")
            self._imap = imap
        return self._imap

    def unseen(self) -> list[tuple[str, bytes]]:
        imap = self._connected()
        imap.noop()
        # The server filters, so other mail is never downloaded. BODY.PEEK leaves it unread.
        status, data = imap.uid(
            "SEARCH",
            None,  # type: ignore[arg-type]  # imaplib skips a None argument (no charset)
            "UNSEEN",
            "OR",
            "FROM",
            '"craigslist.org"',
            "HEADER",
            "Reply-To",
            '"craigslist.org"',
        )
        if status != "OK":
            raise MailError("the inbox search failed")
        uids = data[0].split() if data and data[0] else []
        found: list[tuple[str, bytes]] = []
        # The newest, so Craigslist mail left unread on purpose (robot mail, other posts) can
        # never fill every slot and stop new buyers' emails from being read.
        for uid in uids[-MAX_PER_POLL:]:
            status, parts = imap.uid("FETCH", uid.decode(), "(BODY.PEEK[])")
            if status != "OK":
                continue
            raw = next((p[1] for p in parts if isinstance(p, tuple) and len(p) > 1), None)
            if isinstance(raw, bytes):
                found.append((uid.decode(), raw))
        return found

    def mark_seen(self, uid: str) -> None:
        self._connected().uid("STORE", uid, "+FLAGS", "(\\Seen)")

    def send(self, message: EmailMessage) -> None:
        with smtplib.SMTP_SSL(self.settings.smtp_host, SMTP_PORT, timeout=self.timeout) as smtp:
            smtp.login(self.settings.address, self.settings.password)
            smtp.send_message(message)

    def close(self) -> None:
        imap, self._imap = self._imap, None
        if imap is not None:
            try:
                imap.logout()
            except (OSError, imaplib.IMAP4.error):
                pass


class CraigslistMail:
    """The Market's inbox loop: read new Craigslist mail, pass it to the item agents, send their
    answers back, and mark the mail read."""

    def __init__(
        self,
        store: Store,
        settings: MailSettings,
        negotiate: Negotiate,
        mailbox: Mailbox | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        from yardsale.craigslist_buyer import CraigslistBuyer  # it imports this module

        self.agent = CraigslistAgent(store, settings, negotiate)
        # Yardsale's buyer agent, emailing other people's Craigslist posts from the same inbox.
        self.buyer = CraigslistBuyer(store, settings)
        self.mailbox: Mailbox = mailbox or ImapSmtpMailbox(settings)
        self.clock = clock
        self._lock = asyncio.Lock()
        # uid -> (posts_version, clock) of mail left unread because it matched no post. It's
        # read again once a post or post ID is recorded, or after RETRY_SKIPPED seconds.
        self._skipped: dict[str, tuple[tuple[int, int, float], float]] = {}
        self._held: set[str] = set()  # emails already told "let me check"
        self._sent: defaultdict[str, deque[float]] = defaultdict(deque)

    def _too_many(self, buyer_ref: str) -> bool:
        """True once this buyer got REPLIES_PER_HOUR replies in the last hour. Only replies that
        went out count: on October 25 Gmail dropped every send for a few minutes, the retries
        used up the limit, and the buyer's emails were marked read without an answer."""
        now, sent = self.clock(), self._sent[buyer_ref]
        while sent and sent[0] < now - 3600:
            sent.popleft()
        return len(sent) >= REPLIES_PER_HOUR

    async def poll(self, ctx: Context) -> int:
        """Answer every new Craigslist email, as the Market (ctx). Returns how many replies went
        out. A check that's still running when the next one is due makes that one a no-op."""
        if self._lock.locked():
            return 0
        async with self._lock:
            try:
                new = await asyncio.to_thread(self.mailbox.unseen)
            except Exception as ex:  # a dropped connection or a refused login: retry next time
                log.warning(f"Couldn't read the Craigslist inbox ({_why(ex)})")
                await asyncio.to_thread(self.mailbox.close)
                return 0
            sent = 0
            for step in self.buyer.openings():
                if await self._buyer_step(None, step):
                    sent += 1
            unread = {uid for uid, _ in new}
            for uid in list(self._skipped):
                if uid not in unread:  # read or deleted elsewhere
                    del self._skipped[uid]
            # Confirmations first: a post ID they save can match a buyer's email in this check.
            ordered = sorted(new, key=lambda each: not _looks_like_system_mail(each[1]))
            for uid, raw in ordered:
                if not self._due(uid):
                    continue
                if await self._one(ctx, uid, raw):
                    sent += 1
            return sent

    def _due(self, uid: str) -> bool:
        """False while a skipped email has nothing new to match against."""
        skipped = self._skipped.get(uid)
        if skipped is None:
            return True
        version, at = skipped
        if version != posts_version(self.agent.store) or self.clock() - at >= RETRY_SKIPPED:
            del self._skipped[uid]
            return True
        return False

    def _skip(self, uid: str, reason: str) -> None:
        self._skipped[uid] = (posts_version(self.agent.store), self.clock())
        log.info(f"Craigslist email not matched to a post ({reason})")

    async def _one(self, ctx: Context, uid: str, raw: bytes) -> bool:
        # One dispatcher, so each email gets exactly one answer: Craigslist's own mail (chat
        # notices, posting confirmations) is recorded; a seller answering Yardsale's buyer agent
        # goes to it; everything else is a buyer writing to one of our posts.
        try:
            system = self.agent.system(raw)
            step = None if system is not None else self.buyer.handle(raw)
        except Exception as ex:
            log.warning(f"Couldn't read a Craigslist email ({type(ex).__name__})")
            return False
        if isinstance(system, SystemMail):
            if system.kind == "chat":
                log.info("A buyer wrote in Craigslist chat, which Yardsale can't read")
            await self._mark_seen(uid)
            return False
        if isinstance(system, Skip):
            self._skip(uid, system.reason)
            return False
        if step is not None:
            return await self._buyer_step(uid, step)
        if from_our_seller_agent(raw):
            # Our seller agent's own reply, relayed back, that no negotiation claims: answering
            # it would have the seller agent haggle with itself.
            await self._mark_seen(uid)
            return False
        try:
            handled = await self.agent.handle(ctx, raw)
        except Exception as ex:
            log.warning(f"Couldn't answer a Craigslist email ({type(ex).__name__})")
            return False
        if isinstance(handled, Skip):  # not (yet) a buyer's email about a posted listing
            self._skip(uid, handled.reason)
            return False
        if handled == "answered":  # the same email again, e.g. delivered twice
            await self._mark_seen(uid)
            return False
        answer, reply = handled
        who = answer.buyer_ref.removeprefix("email-")[:8]
        if not answer.final and answer.key in self._held:
            return False  # already told them we're checking; the next check tries again
        if self._too_many(answer.buyer_ref):
            log.warning(f"Not answering buyer {who} on {answer.item_id}: too many emails")
            self.agent.answered(answer)
            await self._mark_seen(uid)
            return False
        try:
            await asyncio.to_thread(self.mailbox.send, reply)
        except Exception as ex:  # the next check tries again; the email is still unread
            log.warning(f"Couldn't send a Craigslist reply ({_why(ex)})")
            await asyncio.to_thread(self.mailbox.close)
            return False
        self._sent[answer.buyer_ref].append(self.clock())
        log.info(f"Craigslist email from buyer {who} on {answer.item_id}: {answer.kind}")
        if not answer.final:
            self._held.add(answer.key)  # left unread, so the item agent gets it next time
            _record_exchange(self.agent.store, answer)  # the seller sees the offer is waiting
            return True
        self._held.discard(answer.key)
        self.agent.answered(answer)  # so it's never answered twice, even if it stays unread
        _record_exchange(self.agent.store, answer)  # for the seller to see in the chat
        await self._mark_seen(uid)
        return True

    async def _buyer_step(self, uid: str | None, step: BuyerStep) -> bool:
        """Send the buyer agent's email, if any, then save its state and mark the seller's
        email read. A failed send leaves both for the next check."""
        if step.email is not None:
            try:
                await asyncio.to_thread(self.mailbox.send, step.email)
            except Exception as ex:
                log.warning(f"Couldn't send a Craigslist negotiation email ({_why(ex)})")
                await asyncio.to_thread(self.mailbox.close)
                return False
        if step.kind != "answered":
            self.buyer.done(step)
        if uid is not None:
            await self._mark_seen(uid)
        return step.email is not None

    async def _mark_seen(self, uid: str) -> None:
        try:
            await asyncio.to_thread(self.mailbox.mark_seen, uid)
        except Exception as ex:
            log.warning(f"Couldn't mark a Craigslist email read ({type(ex).__name__})")


def check(settings: MailSettings) -> str:
    """Log in to IMAP and SMTP without sending anything. For trying the settings before a demo."""
    mailbox = ImapSmtpMailbox(settings)
    try:
        waiting = len(mailbox.unseen())
    finally:
        mailbox.close()
    with smtplib.SMTP_SSL(settings.smtp_host, SMTP_PORT, timeout=TIMEOUT) as smtp:
        smtp.login(settings.address, settings.password)
    return f"IMAP and SMTP logins work. Unread Craigslist emails waiting: {waiting}."


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Check Yardsale's Craigslist email settings.")
    parser.add_argument("--check", action="store_true", help="log in to IMAP and SMTP")
    args = parser.parse_args()  # .env is loaded on import, by yardsale.common
    try:
        mail = settings_from_env()
    except MailConfigError as error:
        sys.exit(str(error))
    if mail is None:
        sys.exit("YARDSALE_MAIL_ADDRESS and YARDSALE_MAIL_PASSWORD aren't set.")
    if not args.check:
        parser.print_help()
        sys.exit(0)
    try:
        print(check(mail))
    except (OSError, imaplib.IMAP4.error, smtplib.SMTPException, MailError) as error:
        sys.exit(f"Mail check failed: {type(error).__name__}")
