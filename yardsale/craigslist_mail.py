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
  update (so two buyers can't both buy), and the reply carries the payment link (checkout.py,
  test mode).
- Anything else gets the asking price and an invitation to make an offer.

If the item agent doesn't answer in time, the buyer gets "let me check and get back to you"
once, the email stays unread, and the next check sends it to the agent again. No price is ever
worked out here. Replies go back through the relay (SMTP) in the buyer's thread.

A buyer's email is matched to a listing by the posting title in its subject. Mail that isn't
from a Craigslist relay address is never downloaded, and relay mail that doesn't name a posted
title is left unread. The Market can't message an ASI:One conversation unprompted (it keeps only
a hash of the session), so the seller sees Craigslist buyers and sales under "my listings".

The mail password stays in .env. Logs name a buyer by a short hash and a listing by its item ID;
email addresses, message text, prices, and the password never appear in them.
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

from yardsale.checkout import Checkout
from yardsale.common import PipelineError
from yardsale.models import AcceptCounter, Counter, Deal, Offer, OfferRejected
from yardsale.pricing import PriceError, parse_usd, usd, valid_cents
from yardsale.store import ACTIVE, NOT_COLLECTED, PAID, PENDING, PROPOSED, SOLD, Listing, Store

if TYPE_CHECKING:
    from yardsale.craigslist_buyer import Step as BuyerStep

POLL_EVERY = 10.0  # seconds between inbox checks
MAX_TITLE = 70  # Craigslist's limit for a posting title
MAX_PER_POLL = 20  # messages read per check; the rest wait for the next one
REPLIES_PER_HOUR = 20  # per buyer: stops two auto-responders from answering each other forever
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
    links: bool = True  # False: don't put payment links in replies (if Craigslist strips them)


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
    links = os.getenv("YARDSALE_MAIL_LINKS", "1").strip().lower() not in ("0", "false", "no")
    return MailSettings(
        address=address,
        password=password,
        imap_host=os.getenv("YARDSALE_IMAP_HOST", "").strip() or DEFAULT_IMAP_HOST,
        smtp_host=os.getenv("YARDSALE_SMTP_HOST", "").strip() or DEFAULT_SMTP_HOST,
        links=links,
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
"""
_ready: weakref.WeakSet[Store] = weakref.WeakSet()


def _db(store: Store) -> sqlite3.Connection:
    """The store's connection, with this module's two tables. They live in the same file as the
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
    lines = [f"{listing.title}."]
    item = listing.item
    if item is not None:
        lines.append(f"Condition: {item.condition}.")
        if item.details:
            lines.append("Details: " + "; ".join(item.details) + ".")
    if listing.appraisal is not None and listing.appraisal.matches:
        lines.append("Priced from recent sales of the same item.")
    lines.append("Email me an offer through Craigslist. I reply fast.")
    return "\n".join(lines)


def post_text(listing: Listing, title: str, settings: MailSettings | None) -> str:
    dollars = -(-listing.asking_cents // 100)  # Craigslist takes whole dollars
    if settings is not None:
        inbox = (
            f" Post it with {settings.address} as the contact email, so buyers' messages "
            "reach me. Use exactly this title: it's how I tell which item a buyer means."
        )
    else:
        inbox = (
            " Use exactly this title. (Email replies are off on this server: "
            "YARDSALE_MAIL_ADDRESS isn't set.)"
        )
    return (
        f"Here's a Craigslist post for your {listing.title}. Craigslist doesn't allow automated "
        "posting, so post it yourself at craigslist.org (create a posting, then for sale by "
        f"owner).{inbox}\n\n"
        f"Title: {title}\n"
        f"Price: {dollars}\n"
        f"Description:\n{description(listing)}\n\n"
        "When a buyer emails, I answer within seconds: I haggle with your prices (never below "
        'your lowest), and send a payment link once they say deal. Say "my listings" to see '
        "Craigslist buyers."
    )


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
                "SELECT COUNT(*) AS buyers, MAX(offer_cents) AS best FROM craigslist_buyers "
                "WHERE item_id = ?",
                (listing.item_id,),
            ).fetchone()
            buyers = int(row["buyers"])
            line = f'On Craigslist as "{title}": '
            if buyers == 0:
                line += "no buyers have emailed yet."
            else:
                line += (
                    f"{buyers} buyer{'s' if buyers > 1 else ''} emailing, highest offer "
                    f"{usd(int(row['best']))}."
                )
            lines.append(line)
        elif listing.status == SOLD:
            deal = store.deal_for(listing.item_id)
            if deal is None or not deal.buyer.startswith("chat:email-"):
                continue
            line = f"The {listing.title} went to a Craigslist buyer by email"
            if deal.payment_status == PAID:
                line += ", paid. Arrange pickup by replying to their email."
            elif deal.payment_status == PENDING and deal.payment_url:
                line += f". Their payment link, if you'd rather text it to them: {deal.payment_url}"
            else:
                line += "."
            lines.append(line)
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
_NO_DEAL = re.compile(r"\b(?:no|not)\s+(?:a\s+)?deal\b", re.IGNORECASE)
_ACCEPT = re.compile(
    r"\b(?:deal|i'?ll take it|i will take it|i accept|accepted|agreed|let'?s do it|"
    r"works for me|sounds good)\b",
    re.IGNORECASE,
)
_YES = re.compile(r"^\s*(?:yes|yeah|yep|yup|ok|okay|sure|perfect|great)\b", re.IGNORECASE)


def new_text(body: str) -> str:
    """The buyer's own words: quoted replies, signatures, and footers dropped."""
    kept: list[str] = []
    for line in body.splitlines():
        stripped = line.strip()
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
    found: list[tuple[int, str]] = []
    for pattern in _AMOUNTS:
        for match in pattern.finditer(plain):
            found.append((match.start(1), match.group(1) + (match.group(2) or "")))
    for _, amount in sorted(found, reverse=True):
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
        content = re.sub(r"(?i)<br\s*/?>|</p>|</div>", "\n", content)
        content = html.unescape(re.sub(r"<[^>]+>", " ", content))
    return content


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

    @property
    def final(self) -> bool:
        """False for "let me check": the email stays unread and is tried again."""
        return self.kind != "holding"


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

    def __init__(
        self, store: Store, checkout: Checkout, settings: MailSettings, negotiate: Negotiate
    ) -> None:
        self.store = store
        self.checkout = checkout
        self.settings = settings
        self.negotiate = negotiate

    def _payment_text(self, price_cents: int, url: str | None, status: str) -> str:
        if status == PAID:
            return "Payment received, thanks! Reply here to set up pickup."
        if url and self.settings.links:
            return (
                f"Great, it's yours for {usd(price_cents)}. Pay here to lock it in (secure "
                f"checkout): {url}\n\nOnce it's paid, reply here to set up pickup."
            )
        if url:
            return (
                f"Great, it's yours for {usd(price_cents)}. Craigslist doesn't always pass links "
                "along, so reply with a phone number and the seller will text you the payment "
                "link."
            )
        return (
            f"Great, it's yours for {usd(price_cents)}. Reply here and we'll sort out payment "
            "and pickup."
        )

    async def _settle(self, deal_id: str) -> str:
        record = await self.checkout.start(deal_id) or self.store.deal(deal_id)
        if record is None:
            return "Something went wrong closing the deal. Please reply again in a minute."
        return self._payment_text(record.price_cents, record.payment_url, record.payment_status)

    def _now_closed(self, listing: Listing) -> tuple[str, str]:
        return _closed(self.store.get(listing.item_id) or listing)

    async def answer(
        self, ctx: Context, listing: Listing, buyer_ref: str, text: str
    ) -> tuple[str, str]:
        """(kind, reply text) for one email from `buyer_ref` about `listing`."""
        item_id, title = listing.item_id, listing.title
        deal = self.store.deal_for(item_id)
        if deal is not None and deal.buyer == f"chat:{buyer_ref}":  # this buyer bought it
            if deal.payment_status == NOT_COLLECTED and self.checkout.enabled:
                return "paying", await self._settle(deal.deal_id)
            status = deal.payment_status
            return "paying", self._payment_text(deal.price_cents, deal.payment_url, status)
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
                return "deal", await self._settle(accepted.deal_id)
            if isinstance(accepted, OfferRejected) and accepted.code == "unavailable":
                return self._now_closed(listing)
            price = None  # the price lapsed: fall through to the asking price
        if amount is not None:
            offer = min(amount, listing.asking_cents)
            reply = await self.negotiate(
                ctx, item_id, Offer(item_id=item_id, offer_cents=offer, buyer_ref=buyer_ref)
            )
            if isinstance(reply, Counter):
                _remember_price(self.store, item_id, buyer_ref, reply, offer)
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

    async def handle(
        self, ctx: Context, raw: bytes
    ) -> tuple[Answer, EmailMessage] | Literal["answered"] | None:
        """The reply to one raw email; "answered" if it was answered before; None if it isn't
        a buyer's email about a posted listing."""
        message = message_from_bytes(raw, policy=policy.default)
        if not isinstance(message, EmailMessage):
            return None
        relay = relay_address(message)
        subject = str(message.get("Subject", ""))
        if relay is None or _automatic(message) or subject.upper().startswith("POST/EDIT"):
            return None
        listing = match_listing(self.store, subject)
        if listing is None:
            return None
        key = short_hash(str(message.get("Message-ID", "")).strip() or f"{relay}|{raw[:4096]!r}")
        if _answered(self.store, key):
            return "answered"
        buyer_ref = f"email-{short_hash(relay)}"
        try:
            kind, text = await self.answer(ctx, listing, buyer_ref, new_text(body_text(message)))
        except PipelineError:  # the item agent didn't answer in time: try again next check
            kind, text = "holding", HOLDING
        answer = Answer(kind, listing.item_id, buyer_ref, text, key)
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
        for uid in uids[:MAX_PER_POLL]:
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
        checkout: Checkout,
        settings: MailSettings,
        negotiate: Negotiate,
        mailbox: Mailbox | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        from yardsale.craigslist_buyer import CraigslistBuyer  # it imports this module

        self.agent = CraigslistAgent(store, checkout, settings, negotiate)
        # Yardsale's buyer agent, emailing other people's Craigslist posts from the same inbox.
        self.buyer = CraigslistBuyer(store, settings)
        self.mailbox: Mailbox = mailbox or ImapSmtpMailbox(settings)
        self.clock = clock
        self._lock = asyncio.Lock()
        self._skipped: set[str] = set()  # uids of mail that isn't ours to answer; left unread
        self._held: set[str] = set()  # emails already told "let me check"
        self._sent: defaultdict[str, deque[float]] = defaultdict(deque)

    def _too_many(self, buyer_ref: str) -> bool:
        now, sent = self.clock(), self._sent[buyer_ref]
        while sent and sent[0] < now - 3600:
            sent.popleft()
        if len(sent) >= REPLIES_PER_HOUR:
            return True
        sent.append(now)
        return False

    async def poll(self, ctx: Context) -> int:
        """Answer every new Craigslist email, as the Market (ctx). Returns how many replies went
        out. A check that's still running when the next one is due makes that one a no-op."""
        if self._lock.locked():
            return 0
        async with self._lock:
            try:
                new = await asyncio.to_thread(self.mailbox.unseen)
            except Exception as ex:  # a dropped connection or a refused login: retry next time
                log.warning(f"Couldn't read the Craigslist inbox ({type(ex).__name__})")
                await asyncio.to_thread(self.mailbox.close)
                return 0
            sent = 0
            for step in self.buyer.openings():
                if await self._buyer_step(None, step):
                    sent += 1
            for uid, raw in new:
                if uid in self._skipped:
                    continue
                if await self._one(ctx, uid, raw):
                    sent += 1
            return sent

    async def _one(self, ctx: Context, uid: str, raw: bytes) -> bool:
        # One dispatcher, so each email gets exactly one answer: a seller answering Yardsale's
        # buyer agent goes to it; everything else is a buyer writing to one of our posts.
        try:
            step = self.buyer.handle(raw)
        except Exception as ex:
            log.warning(f"Couldn't read a Craigslist email ({type(ex).__name__})")
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
        if handled is None:
            self._skipped.add(uid)  # not a buyer's email about a posted listing: leave it
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
            log.warning(f"Couldn't send a Craigslist reply ({type(ex).__name__})")
            await asyncio.to_thread(self.mailbox.close)
            return False
        log.info(f"Craigslist email from buyer {who} on {answer.item_id}: {answer.kind}")
        if not answer.final:
            self._held.add(answer.key)  # left unread, so the item agent gets it next time
            return True
        self._held.discard(answer.key)
        self.agent.answered(answer)  # so it's never answered twice, even if it stays unread
        await self._mark_seen(uid)
        return True

    async def _buyer_step(self, uid: str | None, step: BuyerStep) -> bool:
        """Send the buyer agent's email, if any, then save its state and mark the seller's
        email read. A failed send leaves both for the next check."""
        if step.email is not None:
            try:
                await asyncio.to_thread(self.mailbox.send, step.email)
            except Exception as ex:
                log.warning(f"Couldn't send a Craigslist negotiation email ({type(ex).__name__})")
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
