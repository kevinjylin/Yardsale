"""Craigslist by email: buyers' emails reach the item's own agent, and its answers go back through
Craigslist's relay. The inbox is a fake (no IMAP or SMTP server), the Market and item agents are
real (offline), and payments are tests.fakes.FakePayments."""

from __future__ import annotations

import logging
import re
from collections.abc import AsyncIterator
from email import message_from_bytes, policy
from email.message import EmailMessage
from email.utils import make_msgid
from typing import Any, ClassVar, cast

import pytest
from uagents import Context

from tests import fakes, sim
from yardsale import bureau_server, craigslist_mail
from yardsale.checkout import Checkout
from yardsale.common import PipelineError
from yardsale.craigslist_mail import (
    CraigslistMail,
    ImapSmtpMailbox,
    MailConfigError,
    MailSettings,
    accepts,
    new_text,
    offer_cents,
    post_command,
)
from yardsale.models import AcceptCounter, Counter, Deal, Offer, OfferRejected
from yardsale.store import ACTIVE, PENDING, PROPOSED, SOLD, Listing, Store

BIKE = fakes.photo_url("bike")
INBOX = "yardsale.test@example.test"
PASSWORD = "abcdefghijklmnop"
SETTINGS = MailSettings(address=INBOX, password=PASSWORD)
ANN = "3f9a0c1d2e@reply.craigslist.org"
BOB = "77b1e0a9cc@reply.craigslist.org"
COUNTER = re.compile(r"best I can do on the .+ is \$([\d,]+)|I'm at \$([\d,]+)")


class FakeMailbox:
    """An inbox in memory. unseen() returns every unread message, Craigslist or not, so the
    code's own filtering is what's tested (the real IMAP search filters on the server too)."""

    def __init__(self) -> None:
        self.inbox: dict[str, bytes] = {}
        self.seen: set[str] = set()
        self.sent: list[EmailMessage] = []

    def deliver(self, raw: bytes) -> str:
        uid = str(len(self.inbox) + 1)
        self.inbox[uid] = raw
        return uid

    def unseen(self) -> list[tuple[str, bytes]]:
        return [(uid, raw) for uid, raw in self.inbox.items() if uid not in self.seen]

    def mark_seen(self, uid: str) -> None:
        self.seen.add(uid)

    def send(self, message: EmailMessage) -> None:
        self.sent.append(message)

    def close(self) -> None:
        pass


def email(
    text: str, subject: str, sender: str = ANN, msg_id: str | None = None, **headers: str
) -> bytes:
    message = EmailMessage()
    message["From"] = f"Buyer <{sender}>"
    message["To"] = "relay-seller@sale.craigslist.org"
    message["Subject"] = subject
    message["Message-ID"] = msg_id or make_msgid(domain="craigslist.org")
    for name, value in headers.items():
        message[name.replace("_", "-")] = value
    message.set_content(text)
    return bytes(message)


def body(message: EmailMessage) -> str:
    part = message.get_body(("plain",))
    assert part is not None
    return str(part.get_content())


def counter_in(text: str) -> int:
    found = COUNTER.search(text)
    assert found, text
    return int((found.group(1) or found.group(2)).replace(",", "")) * 100


class Shop:
    """A running Market with one listed bike (ask $360, lowest $300) posted on Craigslist."""

    def __init__(self, market: sim.Market, mailbox: FakeMailbox, payments: fakes.FakePayments):
        self.market = market
        self.mailbox = mailbox
        self.payments = payments
        self.seller = market.user("seller")
        self.title = ""

    async def list_and_post(self) -> str:
        await self.seller.say("sell this, at least $300", BIKE)
        await self.seller.say("ask $360")
        assert "agent" in await self.seller.say("approve")
        reply = await self.seller.say("craigslist")
        found = re.search(r"Title: (.+)", reply)
        assert found, reply
        self.title = found.group(1)
        return reply

    async def buyer_says(self, text: str, sender: str = ANN, **headers: str) -> EmailMessage:
        """One email from a buyer; returns the reply sent through the relay."""
        before = len(self.mailbox.sent)
        self.mailbox.deliver(email(text, f"{self.title} - $360 (sunset)", sender, **headers))
        rt = bureau_server.runtime()
        assert rt.craigslist is not None
        sent = await rt.craigslist.poll(self.market.agent._build_context())
        assert sent == 1 and len(self.mailbox.sent) == before + 1
        return self.mailbox.sent[-1]

    def bike(self) -> Listing:
        (row,) = self.market.store._db.execute("SELECT item_id FROM listings").fetchall()
        listing = self.market.store.get(row["item_id"])
        assert listing is not None
        return listing


@pytest.fixture
async def shop(store: Store) -> AsyncIterator[Shop]:
    payments = fakes.FakePayments()
    mailbox = FakeMailbox()
    async with sim.running_market(
        store, payments=payments, mail=SETTINGS, mailbox=mailbox
    ) as running:
        running_shop = Shop(running, mailbox, payments)
        await running_shop.list_and_post()
        yield running_shop


# --- Posting -------------------------------------------------------------------------------------


def test_post_commands() -> None:
    assert post_command("craigslist") == craigslist_mail.PostCommand(None)
    assert post_command("Craigslist 2") == craigslist_mail.PostCommand(2)
    assert post_command("post 3 on craigslist") == craigslist_mail.PostCommand(3)
    assert post_command("post it to Craigslist!") == craigslist_mail.PostCommand(None)
    assert post_command("find me a bike on craigslist") is None
    assert post_command("list it on craigslist") == craigslist_mail.PostCommand(None)
    assert post_command("ok, post it on craigslist") == craigslist_mail.PostCommand(None)
    assert post_command("yes, craigslist too") == craigslist_mail.PostCommand(None)
    assert post_command("put 2 on craigslist") == craigslist_mail.PostCommand(2)


async def test_the_seller_gets_a_post_to_paste(
    store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("YARDSALE_MAIL_ADDRESS", INBOX)
    monkeypatch.setenv("YARDSALE_MAIL_PASSWORD", PASSWORD)
    async with sim.running_market(store, mail=SETTINGS, mailbox=FakeMailbox()) as market:
        shop = Shop(market, FakeMailbox(), fakes.FakePayments())
        reply = await shop.list_and_post()
        assert "Price: 360" in reply and INBOX in reply and "post it yourself" in reply
        assert "$300" not in reply.split("Description:")[1]  # the post never shows the floor
        assert PASSWORD not in reply
        # Asking again gives the same title; "my listings" shows it's posted.
        assert f"Title: {shop.title}" in await shop.seller.say("craigslist 1")
        listings = await shop.seller.say("my listings")
        assert f'On Craigslist as "{shop.title}": no buyers have emailed yet.' in listings


async def test_a_proposal_must_be_approved_before_posting(market: sim.Market) -> None:
    seller = market.user("seller")
    await seller.say("sell this", BIKE)
    assert "Approve your listing first" in await seller.say("craigslist")


def test_same_titles_get_numbered_and_the_longest_title_matches(store: Store) -> None:
    for item_id, title in (("a", "Trek FX 2"), ("b", "Trek FX 2"), ("c", "Trek FX 2 Disc")):
        store.add_listing(Listing(item_id, "o", "s", item_id, PROPOSED, title, 36000, 30000))
        store.activate(item_id, "o", f"agent1q{item_id}")
    titles = [craigslist_mail.post(store, store.get(i)) for i in "abc"]  # type: ignore[arg-type]
    assert titles == ["Trek FX 2", "Trek FX 2 - 2", "Trek FX 2 Disc"]
    match = craigslist_mail.match_listing
    assert match(store, "Re: trek fx 2 disc - $360 (sunset)").item_id == "c"  # type: ignore[union-attr]
    assert match(store, "Trek FX-2 - $360").item_id == "a"  # type: ignore[union-attr]
    assert match(store, "Trek FX 2 - 2 - $360").item_id == "b"  # type: ignore[union-attr]
    assert match(store, "Specialized Sirrus") is None


# --- The item agent haggles by email -------------------------------------------------------------


async def test_a_question_gets_the_asking_price_in_the_buyers_thread(shop: Shop) -> None:
    msg_id = make_msgid(domain="craigslist.org")
    reply = await shop.buyer_says("Hi, is this still available?", msg_id=msg_id)
    assert reply["To"] == ANN and reply["From"] == INBOX
    assert reply["Subject"] == f"Re: {shop.title} - $360 (sunset)"
    assert reply["In-Reply-To"] == msg_id and reply["References"] == msg_id
    text = body(reply)
    assert "still available" in text and "$360" in text and "Yardsale agent" in text
    assert shop.mailbox.seen == {"1"}


async def test_lowballs_get_counters_from_the_item_agent_that_never_show_the_floor(
    shop: Shop, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    counters = []
    for offer in ("would you take $150?", "Can you do 250", "$270 cash, pick up today"):
        text = body(await shop.buyer_says(offer))
        assert "$300" not in text and "300" not in text, text
        counters.append(counter_in(text))
    assert all(30000 < c <= 36000 for c in counters), counters
    assert counters == sorted(counters, reverse=True)  # counters never go back up
    # The item agent kept the haggle, as it does for chat buyers.
    row = shop.market.store._db.execute("SELECT buyer, rounds FROM counters").fetchone()
    assert row["buyer"].startswith("chat:email-") and row["rounds"] >= 2
    # Nothing sensitive in any log: no address, no message text, no password, no floor.
    logs = "\n".join(record.getMessage() for record in caplog.records)
    for secret in (ANN, "craigslist.org", INBOX, PASSWORD, "pick up today", "$300"):
        assert secret not in logs, secret
    assert "Craigslist email from buyer" in logs


async def test_an_offer_at_the_floor_is_agreed_and_deal_sends_the_payment_link(
    shop: Shop,
) -> None:
    agreed = body(await shop.buyer_says("I can do $300 cash"))
    assert "$300 works for me" in agreed
    assert shop.bike().status == ACTIVE  # agreed isn't sold
    paid = body(await shop.buyer_says("Deal! I'll take it."))
    assert "it's yours for $300" in paid
    deal = shop.market.store.deal_for(shop.bike().item_id)
    assert deal is not None and deal.buyer.startswith("chat:email-")
    assert deal.payment_status == PENDING and deal.payment_url
    assert deal.payment_url in paid
    assert shop.bike().status == SOLD
    # Writing again resends the link rather than buying twice.
    assert deal.payment_url in body(await shop.buyer_says("Where do I pay?"))
    listings = await shop.seller.say("my listings")
    assert "went to a Craigslist buyer by email" in listings


async def test_yes_accepts_a_counter_at_its_price(shop: Shop) -> None:
    counter = counter_in(body(await shop.buyer_says("$280?")))
    assert "yours for" in body(await shop.buyer_says("ok"))
    deal = shop.market.store.deal_for(shop.bike().item_id)
    assert deal is not None and deal.price_cents == counter


async def test_two_buyers_cant_both_buy(shop: Shop) -> None:
    assert "works for me" in body(await shop.buyer_says("$300", sender=ANN))
    assert "works for me" in body(await shop.buyer_says("$310", sender=BOB))
    assert "it's yours" in body(await shop.buyer_says("deal", sender=BOB))
    assert "has sold" in body(await shop.buyer_says("deal", sender=ANN))
    deals = shop.market.store.deals()
    assert len(deals) == 1 and deals[0].price_cents == 31000


async def test_mail_that_isnt_a_buyers_relay_email_is_left_alone(shop: Shop) -> None:
    subject = f"{shop.title} - $360"
    shop.mailbox.deliver(email("$300?", subject, sender="friend@gmail.com"))
    shop.mailbox.deliver(
        email(
            "Your posting is live", f"POST/EDIT/DELETE: {shop.title}", sender="robot@craigslist.org"
        )
    )
    shop.mailbox.deliver(email("$300?", "Old couch - $50", sender=BOB))  # not a posted title
    shop.mailbox.deliver(email("Away", subject, sender=BOB, Auto_Submitted="auto-replied"))
    rt = bureau_server.runtime()
    assert rt.craigslist is not None
    ctx = shop.market.agent._build_context()
    assert await rt.craigslist.poll(ctx) == 0
    assert await rt.craigslist.poll(ctx) == 0
    assert shop.mailbox.sent == [] and shop.mailbox.seen == set()  # still unread


async def test_the_same_email_is_answered_once(shop: Shop) -> None:
    raw = email("$250?", f"{shop.title} - $360", msg_id="<same@craigslist.org>")
    shop.mailbox.deliver(raw)
    shop.mailbox.deliver(raw)
    rt = bureau_server.runtime()
    assert rt.craigslist is not None
    assert await rt.craigslist.poll(shop.market.agent._build_context()) == 1
    assert shop.mailbox.seen == {"1", "2"}


# --- When the item agent is slow -----------------------------------------------------------------


def posted_store() -> Store:
    store = Store()
    store.add_listing(Listing("bike", "o", "s", "r", PROPOSED, "Trek FX 2", 36000, 30000))
    store.activate("bike", "o", "agent1qbike")
    craigslist_mail.post(store, store.get("bike"))  # type: ignore[arg-type]
    return store


async def test_a_slow_item_agent_gets_one_holding_reply_and_a_retry() -> None:
    store = posted_store()
    calls: list[Offer | AcceptCounter] = []
    answering = False

    async def negotiate(
        ctx: Context, item_id: str, request: Offer | AcceptCounter
    ) -> Counter | Deal | OfferRejected:
        calls.append(request)
        if not answering:
            raise PipelineError("A Yardsale agent didn't answer within 60s.")
        return Counter(item_id=item_id, counter_cents=33000, note="", expires_at="x")

    mailbox = FakeMailbox()
    mail = CraigslistMail(store, Checkout(store, None), SETTINGS, negotiate, mailbox)
    ctx = cast(Context, None)
    mailbox.deliver(email("would you take $280", "Trek FX 2 - $360"))
    assert await mail.poll(ctx) == 1
    assert "Let me check" in body(mailbox.sent[0]) and mailbox.seen == set()
    assert await mail.poll(ctx) == 0  # still waiting: no second "let me check"
    answering = True
    assert await mail.poll(ctx) == 1
    assert "$330" in body(mailbox.sent[1]) and mailbox.seen == {"1"}
    assert [type(c) for c in calls] == [Offer, Offer, Offer]
    assert all(isinstance(c, Offer) and c.buyer_ref.startswith("email-") for c in calls)


# --- Reading buyers' words -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "cents"),
    [
        ("Would you take $250?", 25000),
        ("can you do 280", 28000),
        ("I see you're asking $360. Would you take $300?", 30000),
        ("300", 30000),
        ("$1,200.50 is my max", 120050),
        ("250 bucks cash", 25000),
        ("Is it still available? I can come at 5pm", None),
        ("Could you do 5pm tomorrow?", None),
        ("https://sfbay.craigslist.org/sfc/bik/d/trek/7791234567.html", None),
        ("I live 3 miles away", None),
    ],
)
def test_offers_are_read_with_plain_patterns(text: str, cents: int | None) -> None:
    assert offer_cents(text) == cents


@pytest.mark.parametrize(
    ("text", "yes"),
    [
        ("Deal!", True),
        ("I'll take it", True),
        ("Yes", True),
        ("ok sounds good", True),
        ("yes, is it still available?", False),
        ("no deal", False),
        ("Is it still available?", False),
    ],
)
def test_acceptance_is_read_with_plain_patterns(text: str, yes: bool) -> None:
    assert accepts(text) is yes


def test_quoted_replies_are_dropped() -> None:
    text = "Deal!\n\nOn Fri, Oct 24, 2026 at 3:00 PM Yardsale\n<x@y.z> wrote:\n> I'm at $340."
    assert new_text(text) == "Deal!"
    assert new_text("ok\n-- \nSent from my phone\n$999") == "ok"


# --- Settings and the real mailbox code, against fake servers ------------------------------------


def test_settings_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    assert craigslist_mail.settings_from_env() is None
    monkeypatch.setenv("YARDSALE_MAIL_ADDRESS", INBOX)
    with pytest.raises(MailConfigError):
        craigslist_mail.settings_from_env()
    monkeypatch.setenv("YARDSALE_MAIL_PASSWORD", "abcd efgh ijkl mnop")
    settings = craigslist_mail.settings_from_env()
    assert settings is not None and settings.password == PASSWORD
    assert settings.imap_host == "imap.gmail.com" and settings.links
    assert PASSWORD not in repr(settings)


class FakeImap:
    instances: ClassVar[list[FakeImap]] = []

    def __init__(self, host: str, port: int, timeout: float) -> None:
        self.calls: list[tuple[Any, ...]] = [("connect", host, port)]
        FakeImap.instances.append(self)

    def login(self, user: str, password: str) -> tuple[str, list[bytes]]:
        self.calls.append(("login", user, password))
        return "OK", [b""]

    def select(self, mailbox: str) -> tuple[str, list[bytes]]:
        return "OK", [b"1"]

    def noop(self) -> tuple[str, list[bytes]]:
        return "OK", [b""]

    def uid(self, command: str, *args: Any) -> tuple[str, list[Any]]:
        self.calls.append((command, *args))
        if command == "SEARCH":
            return "OK", [b"7"]
        if command == "FETCH":
            return "OK", [(b"7 (UID 7 BODY[] {10}", b"raw-email"), b")"]
        return "OK", [b""]

    def logout(self) -> None:
        pass


class FakeSmtp:
    sent: ClassVar[list[EmailMessage]] = []

    def __init__(self, host: str, port: int, timeout: float) -> None:
        self.host = host

    def __enter__(self) -> FakeSmtp:
        return self

    def __exit__(self, *exc: object) -> None:
        pass

    def login(self, user: str, password: str) -> None:
        assert (user, password) == (INBOX, PASSWORD)

    def send_message(self, message: EmailMessage) -> None:
        FakeSmtp.sent.append(message)


def test_the_imap_and_smtp_mailbox(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(craigslist_mail.imaplib, "IMAP4_SSL", FakeImap)
    monkeypatch.setattr(craigslist_mail.smtplib, "SMTP_SSL", FakeSmtp)
    mailbox = ImapSmtpMailbox(SETTINGS)
    assert mailbox.unseen() == [("7", b"raw-email")]
    mailbox.mark_seen("7")
    reply = EmailMessage()
    reply.set_content("hi")
    mailbox.send(reply)
    calls = FakeImap.instances[-1].calls
    assert calls[0] == ("connect", "imap.gmail.com", 993)
    assert calls[1] == ("login", INBOX, PASSWORD)
    search = next(c for c in calls if c[0] == "SEARCH")
    assert "UNSEEN" in search and '"craigslist.org"' in search  # filtered on the server
    assert ("FETCH", "7", "(BODY.PEEK[])") in calls  # PEEK: reading doesn't mark it read
    assert ("STORE", "7", "+FLAGS", "(\\Seen)") in calls
    assert FakeSmtp.sent == [reply]


def test_a_relayed_email_parses(store: Store) -> None:
    raw = email("hi", "x", Reply_To="Buyer <abc@reply.craigslist.org>", sender="abc@gmail.com")
    message = message_from_bytes(raw, policy=policy.default)
    assert isinstance(message, EmailMessage)
    assert craigslist_mail.relay_address(message) == "abc@reply.craigslist.org"
