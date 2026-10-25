"""Craigslist for buyers: Yardsale emails a Craigslist seller for the buyer and haggles, within
the buyer's max, without ever telling the seller the max.

**Where the seller's address comes from.** Craigslist shows a post's relay address
(`<random>@sale.craigslist.org`) only after a reader taps Reply, and that step can ask for a
CAPTCHA. Craigslist's terms forbid scripts and scrapers, collecting users' information, and
getting around anything that controls access to the site, so Yardsale never fetches the
address itself, and the scraped search results (craigslist.py) don't carry one. The buyer taps
Reply on the post and pastes the address into the ASI:One chat:

    negotiate 3f9a0c1d2e@sale.craigslist.org "Trek FX 2 hybrid bike" max $250 asking $300

The title (in quotes, or the post's link) goes in the email's subject, the way a mail client's
reply would. The asking price is optional; without it the first email asks the seller's price.

**How it haggles.** The first email asks whether the item is still available and makes an
opening offer (haggle.opening_offer). Each answer is read with plain patterns, no model: a dollar
amount is the seller's price, "yes", "deal", "sure", or "ok" after an offer takes it, and "sold"
or "no longer available" ends it. The next offer comes from haggle.buyer_move, at most
EMAIL_ROUNDS offers. Every offer stays at least one price step under the buyer's max, and no
email ever states the max (an agreed price is never repeated back, in case the seller named
exactly the max). Once a price is agreed, the last email says the buyer pays in person at pickup
and asks for a time and place. Yardsale takes no payment for Craigslist deals.

**Threads.** Emails go out from Yardsale's inbox (craigslist_mail's settings and Mailbox), the
same inbox that answers buyers who email Yardsale's own Craigslist posts. Every email we send
gets its own Message-ID, kept as a hash. craigslist_mail.CraigslistMail asks `route()` about
each new email first: one that answers a Message-ID we sent as a buyer (In-Reply-To or
References), or that names a negotiation's "Yardsale ref" in its subject or quoted text, belongs
here. Everything else goes to the seller side. An email we wrote ourselves, relayed back to the
inbox (our buyer agent writing to one of Yardsale's own posts), always goes to the seller side,
so Yardsale's two agents can haggle with each other through Craigslist's relay. That exchange
ends: the buyer side stops after EMAIL_ROUNDS offers or MAX_SELLER_EMAILS answers, and sends
nothing once a negotiation is agreed, stopped, or sold.

**What the buyer sees.** The Market can't message an ASI:One conversation unprompted, so the
buyer says "negotiations" (or "my orders") to see each one's state.

The buyer's max stays in the database. Logs name a negotiation by its ref and say what happened
("counter", "agreed"); prices, addresses, and message text never appear in them.
"""

from __future__ import annotations

import logging
import re
import secrets
import sqlite3
import weakref
from dataclasses import dataclass, replace
from email import message_from_bytes, policy
from email.message import EmailMessage
from email.utils import make_msgid
from typing import Literal

from yardsale import haggle
from yardsale.craigslist_mail import (
    MailConfigError,
    MailSettings,
    body_text,
    new_text,
    offer_cents,
    relay_address,
    settings_from_env,
    short_hash,
)
from yardsale.pricing import PriceError, parse_usd, usd, valid_cents
from yardsale.store import Store

EMAIL_ROUNDS = 4  # offers the buyer agent makes by email before it gives up
MAX_SELLER_EMAILS = 10  # answers read per negotiation; after that nothing more is sent
MAX_NUDGES = 1  # unclear answers re-asked before handing the thread back to the buyer
MAX_NOTE = 500  # characters of the seller's words kept for the buyer, once a price is agreed
REF_LETTERS = "abcdefghjkmnpqrstuvwxyz23456789"  # no 0, o, 1, l, or i
REF_LENGTH = 8
SIGNATURE = (
    "\n\n- the buyer's Yardsale assistant (an AI agent writing on the buyer's behalf). "
    "Yardsale ref {ref}"
)
MARK = "the buyer's Yardsale assistant"  # in every email this module writes

# Statuses. OPENING: the first email hasn't gone out yet. WAITING: waiting for the seller.
OPENING, WAITING = "opening", "waiting"
AGREED, STOPPED, SOLD, NEEDS_YOU = "agreed", "stopped", "sold", "needs_you"
LIVE = (OPENING, WAITING)

log = logging.getLogger("yardsale.craigslist_buyer")


# --- Saved state -------------------------------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS craigslist_negotiations (
    ref TEXT PRIMARY KEY,
    buyer TEXT NOT NULL,
    relay TEXT NOT NULL,
    title TEXT NOT NULL,
    post_url TEXT NOT NULL,
    asking_cents INTEGER,
    max_cents INTEGER NOT NULL,
    status TEXT NOT NULL,
    offer_cents INTEGER,
    seller_cents INTEGER,
    price_cents INTEGER,
    rounds INTEGER NOT NULL,
    answers INTEGER NOT NULL,
    nudges INTEGER NOT NULL,
    note TEXT NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS craigslist_buyer_sent (
    message_key TEXT PRIMARY KEY,
    ref TEXT NOT NULL,
    sent_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS craigslist_buyer_read (
    message_key TEXT PRIMARY KEY,
    ref TEXT NOT NULL,
    read_at REAL NOT NULL
);
"""
_ready: weakref.WeakSet[Store] = weakref.WeakSet()


def _db(store: Store) -> sqlite3.Connection:
    db = store._db
    if store not in _ready:
        db.executescript(SCHEMA)
        _ready.add(store)
    return db


@dataclass(frozen=True)
class Negotiation:
    ref: str  # also the "Yardsale ref" in every email
    buyer: str  # the ASI:One conversation's owner, "asi1:<hash>"
    relay: str  # the post's Craigslist relay address, as the buyer pasted it
    title: str
    post_url: str
    asking_cents: int | None  # None until the seller names a price
    max_cents: int  # the buyer's max: never logged, never emailed
    status: str
    offer_cents: int | None  # our latest offer
    seller_cents: int | None  # the seller's latest price
    price_cents: int | None  # the agreed price
    rounds: int  # offers made
    answers: int  # seller emails read
    nudges: int
    note: str  # the seller's words after agreeing (pickup details), for the buyer
    created_at: float
    updated_at: float


def _row(row: sqlite3.Row | None) -> Negotiation | None:
    return Negotiation(**dict(row)) if row is not None else None


def get(store: Store, ref: str) -> Negotiation | None:
    return _row(
        _db(store).execute("SELECT * FROM craigslist_negotiations WHERE ref = ?", (ref,)).fetchone()
    )


def owned(store: Store, buyer: str) -> list[Negotiation]:
    rows = _db(store).execute(
        "SELECT * FROM craigslist_negotiations WHERE buyer = ? ORDER BY created_at", (buyer,)
    )
    return [Negotiation(**dict(row)) for row in rows]


def live_for_relay(store: Store, relay: str) -> Negotiation | None:
    return _row(
        _db(store)
        .execute(
            "SELECT * FROM craigslist_negotiations WHERE relay = ? AND status IN (?, ?)",
            (relay, *LIVE),
        )
        .fetchone()
    )


def start(
    store: Store,
    buyer: str,
    relay: str,
    max_cents: int,
    title: str = "",
    asking_cents: int | None = None,
    post_url: str = "",
) -> Negotiation:
    """Record a negotiation. The opening email goes out on the inbox's next check. Raises
    ValueError when the address isn't a Craigslist relay or the prices make no sense."""
    relay = relay.strip().lower()
    if not RELAY.fullmatch(relay):
        raise ValueError("that isn't a Craigslist reply address (like abc123@sale.craigslist.org)")
    if not valid_cents(max_cents) or max_cents < 2:
        raise ValueError("the max must be a price, like $250")
    if asking_cents is not None and not valid_cents(asking_cents):
        raise ValueError("the asking price must be a price, like $300")
    now = store.clock()
    ref = "".join(secrets.choice(REF_LETTERS) for _ in range(REF_LENGTH))
    title = " ".join(title.split())[:120]
    _db(store).execute(
        "INSERT INTO craigslist_negotiations VALUES "
        "(?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, 0, 0, 0, '', ?, ?)",
        (ref, buyer, relay, title, post_url[:300], asking_cents, max_cents, OPENING, now, now),
    )
    found = get(store, ref)
    assert found is not None
    return found


def _save(store: Store, n: Negotiation) -> None:
    _db(store).execute(
        "UPDATE craigslist_negotiations SET asking_cents = ?, status = ?, offer_cents = ?, "
        "seller_cents = ?, price_cents = ?, rounds = ?, answers = ?, nudges = ?, note = ?, "
        "updated_at = ? WHERE ref = ?",
        (
            n.asking_cents,
            n.status,
            n.offer_cents,
            n.seller_cents,
            n.price_cents,
            n.rounds,
            n.answers,
            n.nudges,
            n.note,
            store.clock(),
            n.ref,
        ),
    )


def _key(message_id: str) -> str:
    return short_hash(message_id.strip())


def _sent_ref(store: Store, message_id: str) -> str | None:
    row = (
        _db(store)
        .execute("SELECT ref FROM craigslist_buyer_sent WHERE message_key = ?", (_key(message_id),))
        .fetchone()
    )
    return str(row["ref"]) if row else None


# --- Reading the seller's answer, with plain patterns ------------------------------------------

RELAY = re.compile(r"[a-z0-9][a-z0-9._+-]{0,63}@(?:[a-z0-9-]+\.)*craigslist\.org")
_REF = re.compile(r"\byardsale\s+ref\s+([a-z2-9]{8})\b", re.IGNORECASE)
_NOT_SOLD = re.compile(r"\b(?:not|isn'?t|hasn'?t|haven'?t)\s+(?:been\s+|yet\s+)?sold\b", re.I)
_SOLD = re.compile(
    r"\b(?:sold|no\s+longer\s+(?:available|for\s+sale)|not\s+available|(?:it'?s|is)\s+gone|"
    r"sale\s+pending|pending\s+(?:sale|pickup)|already\s+(?:gone|taken)|been\s+taken)\b",
    re.IGNORECASE,
)
_NO_DEAL = re.compile(
    r"\b(?:no|not)\s+(?:a\s+)?deal\b|\bnot\s+sure\b|\bcan'?t\b|\bwon'?t\b|\bcannot\b", re.I
)
_FIRM = re.compile(
    r"\b(?:firm|non-?negotiable|no\s+lower|not\s+going\s+(?:any\s+)?lower|"
    r"can'?t\s+go\s+(?:any\s+)?lower|no\s+less|price\s+is\s+(?:the\s+)?price)\b|"
    r"^\s*(?:no|nope|nah|sorry)\b",
    re.IGNORECASE,
)
_TAKE = re.compile(
    r"\b(?:deal|sure|ok(?:ay)?|sounds\s+good|works\s+for\s+me|that\s+works|you\s+got\s+it|agreed|"
    r"i\s+accept|i\s+can\s+do\s+that|it'?s\s+yours|let'?s\s+do\s+it|that'?s\s+fine|fine\s+by\s+me)\b",
    re.IGNORECASE,
)
_YES = re.compile(r"^\s*(?:yes|yeah|yep|yup)\b", re.IGNORECASE)
_AVAILABLE = re.compile(r"\bavailable\b", re.IGNORECASE)

Reading = Literal["sold", "price", "take", "firm", "unclear"]


def read_answer(text: str) -> tuple[Reading, int | None]:
    """What the seller said: ("price", cents), ("take", None), ("firm", None), ("sold", None),
    or ("unclear", None)."""
    if _SOLD.search(text) and not _NOT_SOLD.search(text):
        return "sold", None
    amount = offer_cents(text)
    if amount is not None:
        return "price", amount
    if _TAKE.search(text) and not _NO_DEAL.search(text):
        return "take", None
    if _FIRM.search(text):
        return "firm", None
    if _YES.match(text) and not _AVAILABLE.search(text):
        return "take", None  # "yes" alone answers "would you take $X?"
    return "unclear", None


# --- Deciding the next email ---------------------------------------------------------------------


def ceiling(n: Negotiation) -> int:
    """The most this negotiation ever offers: one price step under the max, so no email states
    the max itself."""
    unit = haggle.step(n.asking_cents or n.max_cents)
    return n.max_cents - unit if n.max_cents > unit else n.max_cents - 1


@dataclass(frozen=True)
class Move:
    kind: str  # for logs and tests: "opening", "counter", "agreed", "stopped", "sold", ...
    state: Negotiation  # saved once the email (if any) is sent
    text: str | None  # the email to send, without the signature; None sends nothing


def _offer_text(n: Negotiation, cents: int, first: bool) -> str:
    what = f"your {n.title}" if n.title else "the item you posted"
    if first:
        return (
            f"Hi! Is {what} still available? I'm writing for a buyer who's interested. "
            f"Would you take {usd(cents)}? They'd pay in person when they pick it up."
        )
    if n.rounds + 1 >= EMAIL_ROUNDS:
        return f"Thanks for working with me. Would you take {usd(cents)}? That's my best offer."
    return f"Thanks for getting back to me. Could you do {usd(cents)}?"


AGREED_TEXT = (
    "Deal, that works for us. Thank you! My buyer will pay you in person when they pick it up. "
    "What time and place work for pickup? I'll pass it on to them."
)
STOP_TEXT = (
    "Thanks for getting back to me. That's more than my buyer can spend, so we'll pass this "
    "time. Good luck with the sale!"
)


def opening(n: Negotiation) -> Move:
    """The first email: is it available, and an opening offer when the asking price is known."""
    if n.asking_cents is None:
        what = f"your {n.title}" if n.title else "the item you posted"
        text = (
            f"Hi! Is {what} still available? I'm writing for a buyer who's interested. "
            "What price are you asking? They'd pay in person when they pick it up."
        )
        return Move("opening", _with(n, status=WAITING), text)
    offer = haggle.opening_offer(n.asking_cents, ceiling(n))
    state = _with(n, status=WAITING, offer_cents=offer, rounds=1)
    return Move("opening", state, _offer_text(n, offer, True))


def _with(n: Negotiation, **changes: object) -> Negotiation:
    return replace(n, **changes)  # type: ignore[arg-type]


def _agree(n: Negotiation, price: int) -> Move:
    return Move("agreed", _with(n, status=AGREED, price_cents=price), AGREED_TEXT)


def _stop(n: Negotiation, seller: int | None) -> Move:
    return Move("stopped", _with(n, status=STOPPED, seller_cents=seller), STOP_TEXT)


def _nudge(n: Negotiation) -> Move:
    if n.nudges >= MAX_NUDGES:
        return Move("needs_you", _with(n, status=NEEDS_YOU), None)
    state = _with(n, nudges=n.nudges + 1)
    if n.offer_cents is None:
        return Move("nudge", state, "Thanks! What price are you asking for it?")
    return Move("nudge", state, f"Thanks! Just to check: would you take {usd(n.offer_cents)}?")


def _counter(n: Negotiation, seller: int) -> Move:
    """The seller's price is above our last offer: counter, take it, or stop."""
    assert n.offer_cents is not None and n.asking_cents is not None
    top = ceiling(n)
    # haggle.buyer_move counts up to haggle.MAX_ROUNDS; email gets fewer rounds, ending the same
    # way (the last offer is the ceiling, then take or stop).
    rounds = n.rounds + max(0, haggle.MAX_ROUNDS - EMAIL_ROUNDS)
    move = haggle.buyer_move(n.asking_cents, top, n.offer_cents, seller, rounds)
    n = _with(n, seller_cents=seller)
    if move.kind == "take" or (move.kind == "stop" and seller <= n.max_cents):
        if seller > n.max_cents:
            return _stop(n, seller)
        return _agree(n, seller)
    if move.kind == "stop" or n.rounds >= EMAIL_ROUNDS:
        return _stop(n, seller)
    text = _offer_text(n, move.cents, False)
    return Move("counter", _with(n, offer_cents=move.cents, rounds=n.rounds + 1), text)


def next_move(n: Negotiation, text: str) -> Move:
    """Our answer to one email from the seller."""
    if n.status not in LIVE:
        note = n.note
        if n.status == AGREED and not note:
            note = text[:MAX_NOTE]  # the seller's pickup details, shown to the buyer
        return Move("closed", _with(n, note=note, answers=n.answers + 1), None)
    n = _with(n, answers=n.answers + 1)
    if n.answers > MAX_SELLER_EMAILS:
        return Move("needs_you", _with(n, status=NEEDS_YOU), None)
    reading, amount = read_answer(text)
    if reading == "sold":
        return Move("sold", _with(n, status=SOLD), None)
    if reading == "price" and amount is not None:
        if n.offer_cents is None:  # the seller's asking price, answering our first email
            n = _with(n, asking_cents=n.asking_cents or amount, seller_cents=amount)
            offer = haggle.opening_offer(amount, ceiling(n))
            if offer >= amount:
                return _agree(n, amount) if amount <= n.max_cents else _stop(n, amount)
            state = _with(n, offer_cents=offer, rounds=1)
            return Move("opening", state, _offer_text(n, offer, False))
        if amount <= n.offer_cents:  # they came down to our offer (or below it)
            return _agree(_with(n, seller_cents=amount), amount)
        if n.asking_cents is None:
            n = _with(n, asking_cents=amount)
        return _counter(n, amount)
    if reading == "take" and n.offer_cents is not None:
        return _agree(n, n.offer_cents)
    if reading == "firm" and n.offer_cents is not None:
        held = n.seller_cents or n.asking_cents
        if held is None:
            return _nudge(n)
        return _agree(n, held) if held <= n.max_cents else _stop(n, held)
    return _nudge(n)


# --- The inbox side: routing and emails ----------------------------------------------------------


@dataclass(frozen=True)
class Step:
    """One email to send (or none) and the state to save once it's out."""

    kind: str
    ref: str
    move: Move | None
    email: EmailMessage | None
    read_key: str | None  # the incoming email's key, recorded once handled


class CraigslistBuyer:
    """The buyer agent's half of the inbox: opening emails, and the sellers' answers."""

    def __init__(self, store: Store, settings: MailSettings) -> None:
        self.store = store
        self.settings = settings

    def _email(
        self, n: Negotiation, text: str, answering: EmailMessage | None, to: str
    ) -> EmailMessage:
        message = EmailMessage()
        message["From"] = self.settings.address
        message["To"] = to
        if answering is None:
            subject = f"{n.title or 'Your Craigslist post'} (Yardsale ref {n.ref})"
        else:
            subject = " ".join(str(answering.get("Subject", "")).split())
            if not subject.lower().startswith("re:"):
                subject = f"Re: {subject}"
            if n.ref not in subject:
                subject += f" (Yardsale ref {n.ref})"
        message["Subject"] = subject
        message["Message-ID"] = make_msgid(domain=self.settings.address.rpartition("@")[2])
        if answering is not None:
            original = str(answering.get("Message-ID", "")).strip()
            if original:
                message["In-Reply-To"] = original
                references = " ".join(str(answering.get("References", "")).split())
                message["References"] = f"{references} {original}".strip()
        message.set_content(text + SIGNATURE.format(ref=n.ref))
        return message

    def openings(self) -> list[Step]:
        """First emails waiting to go out."""
        rows = _db(self.store).execute(
            "SELECT * FROM craigslist_negotiations WHERE status = ? ORDER BY created_at",
            (OPENING,),
        )
        steps: list[Step] = []
        for row in rows.fetchall():
            n = Negotiation(**dict(row))
            move = opening(n)
            assert move.text is not None
            steps.append(
                Step("opening", n.ref, move, self._email(n, move.text, None, n.relay), None)
            )
        return steps

    def route(self, message: EmailMessage) -> Negotiation | None:
        """The negotiation this email answers, or None when it belongs to the seller side."""
        if relay_address(message) is None:
            return None
        own = str(message.get("Message-ID", "")).strip()
        if own and _sent_ref(self.store, own) is not None:
            return None  # our own buyer email, relayed to one of Yardsale's own posts
        text = body_text(message)
        if MARK in new_text(text):
            return None  # written by this module: the seller side answers it
        ids = " ".join(str(message.get(h, "")) for h in ("In-Reply-To", "References"))
        for message_id in re.findall(r"<[^<>\s]+>", ids):
            ref = _sent_ref(self.store, message_id)
            if ref is not None:
                return get(self.store, ref)
        # The relay may rewrite Message-IDs; our ref is in the subject and in quoted replies.
        for found in _REF.finditer(f"{message.get('Subject', '')}\n{text}"):
            n = get(self.store, found.group(1).lower())
            if n is not None:
                return n
        return None

    def handle(self, raw: bytes) -> Step | None:
        """The step for one raw email, or None when it isn't a seller answering us."""
        message = message_from_bytes(raw, policy=policy.default)
        if not isinstance(message, EmailMessage):
            return None
        n = self.route(message)
        if n is None:
            return None
        key = short_hash(str(message.get("Message-ID", "")).strip() or repr(raw[:4096]))
        seen = (
            _db(self.store)
            .execute("SELECT 1 FROM craigslist_buyer_read WHERE message_key = ?", (key,))
            .fetchone()
        )
        if seen is not None:
            return Step("answered", n.ref, None, None, None)
        move = next_move(n, new_text(body_text(message)))
        email = None
        if move.text is not None:
            to = relay_address(message) or n.relay
            email = self._email(move.state, move.text, message, to)
        return Step(move.kind, n.ref, move, email, key)

    def done(self, step: Step) -> None:
        """Save a step once its email is sent (or when it sends nothing)."""
        now = self.store.clock()
        db = _db(self.store)
        if step.move is not None:
            _save(self.store, step.move.state)
        if step.email is not None:
            db.execute(
                "INSERT OR IGNORE INTO craigslist_buyer_sent VALUES (?, ?, ?)",
                (_key(str(step.email["Message-ID"])), step.ref, now),
            )
        if step.read_key is not None:
            db.execute(
                "INSERT OR IGNORE INTO craigslist_buyer_read VALUES (?, ?, ?)",
                (step.read_key, step.ref, now),
            )
        log.info(f"Craigslist negotiation {step.ref}: {step.kind}")


# --- The buyer's side, in the ASI:One chat -------------------------------------------------------

_ADDRESS_IN_TEXT = re.compile(
    r"(?<![a-z0-9._+-])(" + RELAY.pattern + r")(?![a-z0-9-])", re.IGNORECASE
)
_POST_URL = re.compile(r"https?://[a-z0-9.-]*craigslist\.org/\S+", re.IGNORECASE)
_AMOUNT = r"\$?\s?((?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d{1,2})?)"
_MAX = re.compile(
    r"\b(?:max(?:imum)?|up\s+to|at\s+most|budget(?:\s+of)?(?:\s+is)?|no\s+more\s+than|under)"
    r"\s*(?:of|is|=|:)?\s*" + _AMOUNT,
    re.IGNORECASE,
)
_ASKING = re.compile(
    r"\b(?:asking|listed\s+(?:at|for)|priced\s+at|price)\s*(?:is|of|=|:)?\s*" + _AMOUNT,
    re.IGNORECASE,
)
_QUOTED = re.compile(r"[\"“]([^\"”]{2,120})[\"”]")
_FILLER = re.compile(
    r"\b(?:negotiate|haggle|email|for|with|on|the|seller|of|at|please|me|craigslist|post|"
    r"listing|and|mailto)\b|[\[\]()<>:,;!?]",
    re.IGNORECASE,
)
_NEGOTIATIONS = re.compile(
    r"^\s*(?:my\s+)?(?:craigslist\s+)?negotiations?\s*[.!?]?\s*$|"
    r"^\s*how(?:'s|\s+is|\s+are)\s+(?:my\s+)?(?:craigslist\s+)?negotiations?\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class NegotiateCommand:
    relay: str
    max_cents: int | None
    asking_cents: int | None
    title: str
    post_url: str


def _cents(text: str | None) -> int | None:
    if text is None:
        return None
    try:
        cents = parse_usd(text)
    except PriceError:
        return None
    return cents if valid_cents(cents) else None


def title_from_url(url: str) -> str:
    """Words from a post link's slug (/d/<place-and-title>/<id>.html)."""
    found = re.search(r"/d/([a-z0-9-]+)/", url.lower())
    return found.group(1).replace("-", " ") if found else ""


def command(text: str) -> NegotiateCommand | None:
    """A message naming a Craigslist reply address: negotiate for that post."""
    found = _ADDRESS_IN_TEXT.search(text)
    if found is None:
        return None
    relay = found.group(1).lower()
    url_found = _POST_URL.search(text)
    post_url = url_found.group(0).rstrip(").,") if url_found else ""
    max_found = _MAX.search(text)
    asking_found = _ASKING.search(text)
    quoted = _QUOTED.search(text)
    if quoted:
        title = quoted.group(1)
    else:
        rest = _ADDRESS_IN_TEXT.sub(" ", text)
        for part in (post_url, *(m.group(0) for m in (max_found, asking_found) if m)):
            if part:
                rest = rest.replace(part, " ")
        title = _FILLER.sub(" ", rest)
        title = " ".join(title.split())
        if len(title) < 3:
            title = ""
    if not title and post_url:
        title = title_from_url(post_url)
    return NegotiateCommand(
        relay=relay,
        max_cents=_cents(max_found.group(1)) if max_found else None,
        asking_cents=_cents(asking_found.group(1)) if asking_found else None,
        title=title,
        post_url=post_url,
    )


def asks_for_negotiations(text: str) -> bool:
    return bool(_NEGOTIATIONS.search(text))


HOW_TO = (
    "To have me negotiate with a Craigslist seller: open the post, tap Reply, copy the email "
    "address it shows (like abc123@sale.craigslist.org), and send it to me with the title and "
    'your max, like: negotiate abc123@sale.craigslist.org "Trek FX 2 bike" max $250 asking $300.'
)


def chat_reply(store: Store, buyer: str, cmd: NegotiateCommand) -> str:
    """The chat's answer to a negotiate command."""
    try:
        settings = settings_from_env()
    except MailConfigError:
        settings = None
    if settings is None:
        return (
            "Email negotiation is off on this server (YARDSALE_MAIL_ADDRESS isn't set), so I "
            "can't write to that seller."
        )
    if cmd.relay.lower() == settings.address.lower():
        return "That's Yardsale's own inbox. Paste the address the post's Reply button shows."
    if cmd.max_cents is None:
        return (
            "What's the most you'd pay? Send it with the address, like: negotiate "
            f"{cmd.relay} max $250. I never tell the seller your max."
        )
    current = live_for_relay(store, cmd.relay)
    if current is not None:
        if current.buyer == buyer:
            return "I'm already negotiating for that post. " + status_line(current)
        return "Someone is already negotiating for that post through Yardsale. Try again later."
    try:
        n = start(store, buyer, cmd.relay, cmd.max_cents, cmd.title, cmd.asking_cents, cmd.post_url)
    except ValueError as error:
        return f"I can't negotiate that one: {error}."
    what = f'"{n.title}"' if n.title else "that post"
    if n.asking_cents is not None:
        opener = f"offer {usd(haggle.opening_offer(n.asking_cents, ceiling(n)))} to start"
    else:
        opener = "ask the seller's price first"
    return (
        f"OK. I'll email the seller of {what} from Yardsale's inbox in a few seconds, through "
        f"Craigslist's relay. I'll {opener}, haggle by email, and never offer more than your max "
        "or tell the seller what it is. Every email says it's from your Yardsale assistant. "
        "Craigslist deals are paid to the seller in person at pickup; Yardsale takes no payment "
        'for them. I can\'t message you here when the seller answers, so say "negotiations" to '
        "check on it."
    )


def status_line(n: Negotiation) -> str:
    what = n.title or "Craigslist post"
    if n.status == OPENING:
        return f"{what}: the first email is about to go out."
    if n.status == WAITING:
        if n.offer_cents is None:
            return f"{what}: asked the seller's price, waiting for an answer."
        line = f"{what}: offered {usd(n.offer_cents)}"
        if n.seller_cents is not None:
            line += f", seller at {usd(n.seller_cents)}"
        return line + f" (offer {n.rounds} of {EMAIL_ROUNDS}), waiting for the seller."
    if n.status == AGREED and n.price_cents is not None:
        line = (
            f"{what}: agreed at {usd(n.price_cents)}. Pay the seller in person at pickup "
            "(Yardsale takes no payment for Craigslist deals)."
        )
        if n.note:
            line += f' The seller wrote: "{" ".join(n.note.split())[:300]}"'
        else:
            line += " I asked the seller for a pickup time and place."
        return line
    if n.status == STOPPED:
        seller = f" (their price was {usd(n.seller_cents)})" if n.seller_cents else ""
        return f"{what}: no deal, the seller wouldn't come down to your max{seller}."
    if n.status == SOLD:
        return f"{what}: the seller says it's sold or no longer available."
    return (
        f"{what}: the seller's answer needs you. Reply to the post yourself through Craigslist "
        "to carry on."
    )


def buyer_lines(store: Store, buyer: str) -> list[str]:
    """What "negotiations" and "my orders" show for Craigslist negotiations."""
    found = owned(store, buyer)
    if not found:
        return []
    return ["Craigslist, by email:"] + [f"{i}. {status_line(n)}" for i, n in enumerate(found, 1)]


def negotiations_reply(store: Store, buyer: str) -> str:
    lines = buyer_lines(store, buyer)
    return "\n".join(lines) if lines else "You have no Craigslist negotiations. " + HOW_TO
