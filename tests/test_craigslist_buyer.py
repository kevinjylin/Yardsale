"""Craigslist for buyers: Yardsale's buyer agent emails a Craigslist seller and haggles within
the buyer's max. The inbox and Craigslist's relay are fakes; the Market, its seller-side email
agent, and the item agents are real (offline)."""

from __future__ import annotations

import logging
from email.message import EmailMessage
from email.utils import make_msgid
from typing import cast

import pytest
from uagents import Context

from tests import fakes, sim
from tests.test_craigslist_mail import INBOX, PASSWORD, SETTINGS, FakeMailbox, Shop, body, email
from yardsale import bureau_server, craigslist_buyer, haggle
from yardsale.checkout import Checkout
from yardsale.craigslist_buyer import (
    AGREED,
    EMAIL_ROUNDS,
    MARK,
    NEEDS_YOU,
    SOLD,
    STOPPED,
    WAITING,
    Negotiation,
    command,
    next_move,
    opening,
    read_answer,
)
from yardsale.craigslist_mail import CraigslistMail
from yardsale.models import AcceptCounter, Counter, Deal, Offer, OfferRejected
from yardsale.pricing import usd
from yardsale.store import ACTIVE, Store
from yardsale.store import SOLD as LISTING_SOLD

POST = "a1b2c3d4e5@sale.craigslist.org"  # a post's relay address, as Reply shows it
SELLER = "5e11e55e11@reply.craigslist.org"  # how the relay shows the seller to us
ANN = "3f9a0c1d2e@reply.craigslist.org"  # a buyer emailing one of Yardsale's own posts
BUYER = "asi1:buyerhash"


def fresh(store: Store, max_cents: int = 25000, asking: int | None = 30000) -> Negotiation:
    return craigslist_buyer.start(store, BUYER, POST, max_cents, "Trek FX 2 bike", asking)


def play(n: Negotiation, answers: list[str]) -> tuple[Negotiation, list[str]]:
    """Run a negotiation against scripted seller answers; returns the end state and emails."""
    move = opening(n)
    sent = [move.text or ""]
    n = move.state
    for answer in answers:
        move = next_move(n, answer)
        n = move.state
        if move.text is not None:
            sent.append(move.text)
    return n, sent


# --- Reading the seller ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "reading"),
    [
        ("$280 and it's yours", ("price", 28000)),
        ("I could do 260", ("price", 26000)),
        ("Deal!", ("take", None)),
        ("ok", ("take", None)),
        ("Sure, when can you come?", ("take", None)),
        ("Yes", ("take", None)),
        ("not sure about that", ("unclear", None)),
        ("Sorry, it sold yesterday", ("sold", None)),
        ("It's no longer available", ("sold", None)),
        ("Not sold yet! When could you come?", ("unclear", None)),
        ("Price is firm", ("firm", None)),
        ("No", ("firm", None)),
        ("Yes it's still available", ("unclear", None)),
        ("no deal", ("firm", None)),
    ],
)
def test_seller_answers_are_read_with_plain_patterns(
    text: str, reading: tuple[str, int | None]
) -> None:
    assert read_answer(text) == reading


# --- The buyer agent's moves ---------------------------------------------------------------------


def test_the_opening_offer_is_below_the_max_and_asks_if_its_available(store: Store) -> None:
    n = fresh(store, max_cents=25000, asking=30000)
    move = opening(n)
    assert move.state.offer_cents == haggle.opening_offer(30000, 24900) < 25000
    assert move.text is not None and "still available" in move.text
    assert usd(move.state.offer_cents) in move.text and "$250" not in move.text
    # Without an asking price the first email asks for it, then opens once it's named.
    n = craigslist_buyer.start(store, BUYER, "x9@sale.craigslist.org", 25000, "Lamp")
    move = opening(n)
    assert move.state.offer_cents is None and move.text and "What price" in move.text
    then = next_move(move.state, "I'm asking $40")
    assert then.state.asking_cents == 4000 and then.state.offer_cents == 2600  # 65% of $40


@pytest.mark.parametrize("max_cents", [15000, 22500, 25000, 27500, 29900, 30000, 45000])
@pytest.mark.parametrize("give", [0, 500, 1000, 2500, 5000])
def test_offers_never_reach_the_max_and_no_email_states_it(
    store: Store, max_cents: int, give: int
) -> None:
    """A seller who comes down by `give` each round, from $300. Whatever happens, no offer is
    at or above the max, the max never appears in an email, and it ends within the round cap."""
    n = fresh(store, max_cents=max_cents, asking=30000)
    price, answers = 30000, []
    for _ in range(EMAIL_ROUNDS + 2):
        price -= give
        answers.append(f"Best I can do is {usd(price)}")
    end, sent = play(n, answers)
    assert end.status in (AGREED, STOPPED)
    assert end.rounds <= EMAIL_ROUNDS
    assert end.offer_cents is not None and end.offer_cents < max_cents
    for text in sent:
        assert usd(max_cents) not in text
    if end.status == AGREED:
        assert end.price_cents is not None and end.price_cents <= max_cents


def test_deal_after_an_offer_agrees_at_that_offer(store: Store) -> None:
    n = opening(fresh(store)).state
    move = next_move(n, "Deal, come get it")
    assert move.kind == "agreed" and move.state.status == AGREED
    assert move.state.price_cents == n.offer_cents
    assert move.text is not None and "pick" in move.text and "in person" in move.text
    assert usd(move.state.price_cents or 0) not in move.text  # the price is never restated
    # Later emails (pickup details) are kept for the buyer, and nothing more is sent.
    after = next_move(move.state, "Saturday 10am at the Ferry Building works")
    assert after.text is None and "Ferry Building" in after.state.note


def test_sold_ends_it_without_a_reply(store: Store) -> None:
    move = next_move(opening(fresh(store)).state, "Sorry, it's sold.")
    assert move.state.status == SOLD and move.text is None


def test_firm_over_the_max_stops_politely(store: Store) -> None:
    n = next_move(opening(fresh(store, max_cents=25000)).state, "I'm at $290").state
    move = next_move(n, "Sorry, that's firm")
    assert move.state.status == STOPPED and move.text and "pass" in move.text
    under = next_move(opening(fresh(store, max_cents=29500)).state, "I'm at $290").state
    assert next_move(under, "firm").state.status == AGREED


def test_unclear_answers_get_one_nudge_then_go_to_the_buyer(store: Store) -> None:
    n = opening(fresh(store)).state
    first = next_move(n, "Where are you located?")
    assert first.kind == "nudge" and first.text and usd(n.offer_cents or 0) in first.text
    second = next_move(first.state, "Hello?")
    assert second.state.status == NEEDS_YOU and second.text is None


def test_a_chatty_seller_cant_keep_it_going(store: Store) -> None:
    n = opening(fresh(store)).state
    sent = 0
    for _ in range(30):
        move = next_move(n, "Best I can do is $299")
        n = move.state
        sent += move.text is not None
    assert n.status not in craigslist_buyer.LIVE and sent <= EMAIL_ROUNDS + 1


# --- The chat command -----------------------------------------------------------------------------


def test_negotiate_commands() -> None:
    cmd = command(f'negotiate {POST} "Trek FX 2 bike" max $250 asking $300')
    assert cmd is not None
    assert (cmd.relay, cmd.max_cents, cmd.asking_cents, cmd.title) == (
        POST,
        25000,
        30000,
        "Trek FX 2 bike",
    )
    cmd = command(f"haggle with [{POST.upper()}](mailto:{POST}) for the trek bike, max $250")
    assert cmd is not None and cmd.relay == POST and cmd.title == "trek bike"
    url = "https://sfbay.craigslist.org/sfc/bik/d/san-francisco-trek-fx-2/7791234567.html"
    cmd = command(f"negotiate {POST} {url} max $250")
    assert cmd is not None and cmd.post_url == url and "trek fx 2" in cmd.title
    assert command(f"negotiate {POST}") is not None  # the chat asks for the max
    assert command("find me a bike on craigslist, max $250") is None
    assert command("email me at someone@example.com") is None


async def test_the_buyer_starts_and_follows_a_negotiation_in_the_chat(
    market: sim.Market, monkeypatch: pytest.MonkeyPatch
) -> None:
    buyer = market.user("buyer")
    assert "off on this server" in await buyer.say(f"negotiate {POST} max $250")
    monkeypatch.setenv("YARDSALE_MAIL_ADDRESS", INBOX)
    monkeypatch.setenv("YARDSALE_MAIL_PASSWORD", PASSWORD)
    assert "most you'd pay" in await buyer.say(f"negotiate {POST}")
    reply = await buyer.say(f'negotiate {POST} "Trek FX 2" max $250 asking $300')
    assert "$195 to start" in reply and "$250" not in reply and "in person" in reply
    assert "already negotiating" in await buyer.say(f"negotiate {POST} max $250")
    assert "Someone is already" in await market.user("other").say(f"negotiate {POST} max $9")
    status = await buyer.say("negotiations")
    assert "Trek FX 2: the first email is about to go out." in status
    assert "Trek FX 2" in await buyer.say("my orders")
    assert "no Craigslist negotiations" in await market.user("third").say("negotiations")


# --- The inbox: one dispatcher, two sides ---------------------------------------------------------


def relayed(message: EmailMessage, sender: str, keep_ids: bool = True) -> bytes:
    """What Craigslist's relay delivers for a message we sent: from an anonymised address,
    with the threading headers kept (or, if `keep_ids` is False, replaced)."""
    copy = EmailMessage()
    copy["From"] = sender
    copy["To"] = INBOX
    copy["Subject"] = str(message["Subject"])
    if keep_ids:
        copy["Message-ID"] = str(message["Message-ID"])
        for name in ("In-Reply-To", "References"):
            if message[name]:
                copy[name] = str(message[name])
    else:
        copy["Message-ID"] = make_msgid(domain="craigslist.org")
    copy.set_content(body(message))
    return bytes(copy)


async def unused_negotiate(
    ctx: Context, item_id: str, request: Offer | AcceptCounter
) -> Counter | Deal | OfferRejected:
    raise AssertionError("the seller side shouldn't be asked")


async def test_the_opening_email_goes_out_once_in_its_own_thread(store: Store) -> None:
    mailbox = FakeMailbox()
    mail = CraigslistMail(store, Checkout(store, None), SETTINGS, unused_negotiate, mailbox)
    n = fresh(store)
    ctx = cast(Context, None)
    assert await mail.poll(ctx) == 1
    assert await mail.poll(ctx) == 0
    (sent,) = mailbox.sent
    assert sent["To"] == POST and sent["From"] == INBOX
    assert f"Yardsale ref {n.ref}" in str(sent["Subject"]) and "Trek FX 2 bike" in str(
        sent["Subject"]
    )
    assert MARK in body(sent) and "AI agent" in body(sent)
    saved = craigslist_buyer.get(store, n.ref)
    assert saved is not None and saved.status == WAITING and saved.rounds == 1

    # The seller answers in the thread: it goes to the buyer agent, which counters.
    seller_id = make_msgid(domain="craigslist.org")
    reply = email(
        "I could do $280",
        f"Re: {sent['Subject']}",
        SELLER,
        seller_id,
        In_Reply_To=sent["Message-ID"],
        References=sent["Message-ID"],
    )
    mailbox.deliver(reply)
    assert await mail.poll(ctx) == 1
    counter = mailbox.sent[-1]
    assert counter["To"] == SELLER and counter["In-Reply-To"] == seller_id
    assert str(counter["References"]) == f"{sent['Message-ID']} {seller_id}"
    mailbox.deliver(reply)  # delivered twice: read once
    assert await mail.poll(ctx) == 0 and len(mailbox.sent) == 2


async def test_the_ref_finds_the_thread_when_the_relay_rewrites_message_ids(store: Store) -> None:
    mailbox = FakeMailbox()
    mail = CraigslistMail(store, Checkout(store, None), SETTINGS, unused_negotiate, mailbox)
    n = fresh(store)
    ctx = cast(Context, None)
    await mail.poll(ctx)
    quoted = f"Sure!\n\nOn Fri someone wrote:\n> {body(mailbox.sent[0])}"
    mailbox.deliver(email(quoted, "Re: your email", SELLER))
    assert await mail.poll(ctx) == 1
    saved = craigslist_buyer.get(store, n.ref)
    assert saved is not None and saved.status == AGREED


async def test_each_email_goes_to_exactly_one_side(
    store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A buyer writing to Yardsale's own post goes to the item agent; a seller answering our
    buyer agent goes to the buyer agent, even when its subject names one of our posts."""
    monkeypatch.setenv("YARDSALE_MAIL_ADDRESS", INBOX)
    monkeypatch.setenv("YARDSALE_MAIL_PASSWORD", PASSWORD)
    mailbox = FakeMailbox()
    async with sim.running_market(
        store, payments=fakes.FakePayments(), mail=SETTINGS, mailbox=mailbox
    ) as market:
        shop = Shop(market, mailbox, fakes.FakePayments())
        await shop.list_and_post()
        rt = bureau_server.runtime()
        assert rt.craigslist is not None
        ctx = market.agent._build_context()
        n = craigslist_buyer.start(store, BUYER, POST, 25000, shop.title, 30000)
        assert await rt.craigslist.poll(ctx) == 1  # our opening email
        opening_email = mailbox.sent[-1]

        mailbox.deliver(email("would you take $300?", f"{shop.title} - $360", ANN))
        mailbox.deliver(
            email(
                "$280 is my lowest",
                f"Re: {shop.title}",  # names our own post, but answers our buyer agent
                SELLER,
                In_Reply_To=str(opening_email["Message-ID"]),
            )
        )
        assert await rt.craigslist.poll(ctx) == 2
        to_ann, to_seller = mailbox.sent[-2:]
        assert to_ann["To"] == ANN and "seller's Yardsale agent" in body(to_ann)
        assert to_seller["To"] == SELLER and MARK in body(to_seller)
        assert mailbox.seen == {"1", "2"}
        saved = craigslist_buyer.get(store, n.ref)
        assert saved is not None and saved.seller_cents == 28000 and saved.rounds == 2


@pytest.mark.parametrize("keep_ids", [True, False])
@pytest.mark.parametrize(
    ("max_cents", "ending"), [(34000, AGREED), (25000, STOPPED)], ids=["deal", "no-deal"]
)
async def test_our_buyer_agent_haggles_with_our_seller_agent_through_the_relay(
    store: Store,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    keep_ids: bool,
    max_cents: int,
    ending: str,
) -> None:
    """The buyer agent emails one of Yardsale's own Craigslist posts (ask $360, lowest $300).
    Both agents answer each other through a fake relay until one of them stops; it ends."""
    caplog.set_level(logging.INFO)
    monkeypatch.setenv("YARDSALE_MAIL_ADDRESS", INBOX)
    monkeypatch.setenv("YARDSALE_MAIL_PASSWORD", PASSWORD)
    mailbox = FakeMailbox()
    as_buyer = "b0b0b0b0b0@reply.craigslist.org"  # how the relay shows our buyer agent
    async with sim.running_market(
        store, payments=fakes.FakePayments(), mail=SETTINGS, mailbox=mailbox
    ) as market:
        shop = Shop(market, mailbox, fakes.FakePayments())
        await shop.list_and_post()
        buyer = market.user("buyer")
        dollars = max_cents // 100
        await buyer.say(f'negotiate {POST} "{shop.title}" max ${dollars} asking $360')
        rt = bureau_server.runtime()
        assert rt.craigslist is not None
        ctx = market.agent._build_context()

        done = 0
        for _ in range(40):
            sent = await rt.craigslist.poll(ctx)
            new = mailbox.sent[done:]
            done = len(mailbox.sent)
            for message in new:
                to = str(message["To"]).lower()
                sender = as_buyer if to in (POST, SELLER) else SELLER
                assert to in (POST, SELLER, as_buyer)
                mailbox.deliver(relayed(message, sender, keep_ids))
            if sent == 0 and not new:
                break
        else:
            pytest.fail("the two agents kept emailing each other")

        (row,) = store._db.execute("SELECT buyer FROM craigslist_negotiations").fetchall()
        (n,) = craigslist_buyer.owned(store, row["buyer"])
        assert n.status == ending
        assert len(mailbox.sent) <= 2 * (EMAIL_ROUNDS + 2)
        listing = shop.bike()
        if ending == AGREED:
            assert listing.status == LISTING_SOLD and n.price_cents is not None
            deal = store.deal_for(listing.item_id)
            assert deal is not None and deal.price_cents == n.price_cents <= max_cents
            assert n.price_cents >= 30000  # never under the seller's lowest
        else:
            assert listing.status == ACTIVE
        assert "agreed at" in await buyer.say("negotiations") or ending != AGREED

        ours = [m for m in mailbox.sent if MARK in body(m)]
        assert ours and all(f"${dollars}" not in body(m) + str(m["Subject"]) for m in ours)
        logged = "\n".join(record.getMessage() for record in caplog.records)
        assert f"${dollars}" not in logged and str(max_cents) not in logged
        assert "craigslist.org" not in logged and "Best I can do" not in logged
