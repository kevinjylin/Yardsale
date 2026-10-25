"""Messages the chat doesn't recognize: a seller asking about buyers, a question about what an
unclear message meant, and the greeting only on first contact. Every provider is a fake."""

from __future__ import annotations

import pytest

from tests import fakes, sim
from yardsale import asione, chat, commands, craigslist_mail
from yardsale.asione import SellerMessage
from yardsale.store import Store

BIKE = fakes.photo_url("bike")
GREETING = "Hi, I'm Yardsale"
LIVE_MESSAGES = (
    "@ys-orchestrator someone messaged us message them back",
    "someone messaged me about the waterbottle reply to them and haggle",
)


def haggles(store: Store) -> int:
    """Offers item agents have answered: any haggle, by a chat buyer or the buyer agent."""
    return int(store._db.execute("SELECT COUNT(*) FROM counters").fetchone()[0])


async def list_and_post(seller: sim.ChatUser) -> None:
    await seller.say("sell this, at least $300", BIKE)
    await seller.say("approve")
    assert "Title:" in await seller.say("craigslist")


@pytest.mark.parametrize("text", LIVE_MESSAGES)
async def test_a_seller_asking_about_buyers_gets_them_not_help(
    store: Store, monkeypatch: pytest.MonkeyPatch, text: str
) -> None:
    """Seen live: after posting on Craigslist, these got the greeting and the home menu."""
    monkeypatch.setenv("YARDSALE_MAIL_ADDRESS", "yardsale.test@example.com")
    monkeypatch.setenv("YARDSALE_MAIL_PASSWORD", "app-password")
    async with sim.running_market(store) as market:
        seller = market.user("seller")
        await list_and_post(seller)
        reply = await seller.exchange(sim.chat_message(text))
        shown = sim.shown(reply)
        assert GREETING not in shown
        assert "Buyers for your Trek FX 2 Disc hybrid bike" in reply.text()
        assert "no buyers have emailed yet" in reply.text()
        assert chat.AGENT_ANSWERS_EMAIL in reply.text()  # it doesn't need telling
        assert chat.CRAIGSLIST_CHAT in reply.text()  # Craigslist chat can't be read
        assert "$300" not in shown  # the floor stays out
        assert sim.button(reply, "listings")
        assert haggles(store) == 0  # "haggle" from a seller starts no haggle


async def test_buyers_view_says_when_email_is_off(market: sim.Market) -> None:
    seller = market.user("seller")
    await seller.say("sell this, at least $300", BIKE)
    await seller.say("approve")
    reply = await seller.exchange(sim.chat_message("any buyers?"))
    assert chat.EMAIL_OFF in reply.text()
    assert 'isn\'t on Craigslist yet: say "craigslist"' in reply.text()
    assert sim.button(reply, "craigslist")


async def test_asi_ones_buyers_reading_shows_buyers(store: Store) -> None:
    """Words no pattern knows still reach the buyers view when ASI:One reads them as buyers."""

    async def read(text: str, has_photo: bool) -> SellerMessage:
        if "hitting me up" in text:
            return SellerMessage(intent="buyers", asking_usd=None, floor_usd=None, note="")
        return await fakes.read(text, has_photo)

    async with sim.running_market(store, read=read) as market:
        seller = market.user("seller")
        await seller.say("sell this, at least $300", BIKE)
        await seller.say("approve")
        reply = await seller.say("people keep hitting me up, what now")
        assert "Buyers for your Trek" in reply and GREETING not in reply


async def test_status_shows_my_listings(market: sim.Market) -> None:
    seller = market.user("seller")
    await seller.say("sell this, at least $300", BIKE)
    await seller.say("approve")
    reply = await seller.say("did it sell?")
    assert "For sale:" in reply and "Trek" in reply


async def test_an_unclear_message_with_a_proposal_asks_about_it(market: sim.Market) -> None:
    seller = market.user("seller")
    await seller.say("sell this, at least $300", BIKE)
    reply = await seller.exchange(sim.chat_message("hmm what about the thing"))
    shown = sim.shown(reply)
    assert GREETING not in shown and chat.HOW_IT_WORKS not in shown
    assert (
        "Did you mean your Trek FX 2 Disc hybrid bike, medium, waiting for your approval" in shown
    )
    approve = sim.button(reply, "approve")
    assert sim.button(reply, "review")
    assert "Did you mean" in sim.card_text(sim.card_of(reply))  # type: ignore[arg-type]
    assert "is for sale at" in await seller.tap(approve)


async def test_the_greeting_is_for_first_contact_only(market: sim.Market) -> None:
    someone = market.user("someone")
    first = await someone.exchange(sim.chat_message("blorp"))
    assert first.text() == chat.HELP
    again = await someone.exchange(sim.chat_message("blorp"))
    assert again.text() == chat.UNCLEAR and GREETING not in sim.shown(again)
    assert GREETING not in await someone.say("help")


async def test_a_returning_sellers_unclear_message_asks_and_yes_answers(
    market: sim.Market,
) -> None:
    seller = market.user("seller")
    await seller.say("sell this, at least $300", BIKE)
    await seller.say("approve")
    reply = await seller.exchange(sim.chat_message("hmm what about the thing"))
    shown = sim.shown(reply)
    assert GREETING not in shown
    assert "Did you mean buyers for your Trek" in reply.text()
    assert sim.button(reply, "buyers") and sim.button(reply, "listings")
    assert "Buyers for your Trek" in await seller.say("yes")
    # The question is answered: another "yes" means nothing now.
    assert "Buyers for your Trek" not in await seller.say("yes")
    await seller.say("hmm what about the thing")
    assert "For sale:" in await seller.say("the second one")
    await seller.say("hmm what about the thing")
    assert "Tell me in other words" in await seller.say("no")


async def test_haggle_with_them_from_a_seller_never_starts_a_haggle(
    market: sim.Market, store: Store
) -> None:
    seller = market.user("seller")
    await seller.say("sell this, at least $300", BIKE)
    await seller.say("approve")
    # Nothing searched: "them" are the seller's buyers.
    assert "Buyers for your Trek" in await seller.say("haggle with them")
    # With search results too, it asks which, and neither answer haggles.
    assert "(yours)" in await seller.say("find me a bike")
    reply = await seller.exchange(sim.chat_message("haggle with them"))
    assert "haggle with the sellers in your search" in reply.text()
    assert sim.button(reply, "haggle_form") and sim.button(reply, "buyers")
    assert "Buyers for your Trek" in await seller.say("the second one")
    await seller.say("haggle with them")
    assert chat.ASK_MAX in await seller.say("the first one")  # asks the max; doesn't haggle
    assert haggles(store) == 0


@pytest.mark.parametrize(
    ("text", "intent"),
    [
        (LIVE_MESSAGES[0], "buyers"),
        (LIVE_MESSAGES[1], "buyers"),
        ("any buyers?", "buyers"),
        ("did anyone reply", "buyers"),
        ("did my lamp sell?", "status"),
        ("how's my listing", "status"),
        ("find me a road bike under $300", "buy"),
        ("sell my desk, at least $40", "sell"),
        ("hello", "other"),
    ],
)
def test_read_locally_knows_seller_questions(text: str, intent: str) -> None:
    assert asione.read_locally(text, False).intent == intent


def test_choice_answers() -> None:
    assert commands.choice_answer("yes", 2) == 0
    assert commands.choice_answer("Yeah, please", 2) == 0
    assert commands.choice_answer("the second one", 2) == 1
    assert commands.choice_answer("2", 2) == 1
    assert commands.choice_answer("third", 2) is None
    assert commands.choice_answer("something else", 2) == -1
    assert commands.choice_answer("yes, approve it", 2) is None
    assert commands.choice_answer("haggle with them", 2) is None


async def test_buyers_view_shows_what_buyers_wrote_and_chat_notices(
    store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The seller sees each answered email and hears about Craigslist chat they can't read."""
    monkeypatch.setenv("YARDSALE_MAIL_ADDRESS", "yardsale.test@example.com")
    monkeypatch.setenv("YARDSALE_MAIL_PASSWORD", "app-password")
    async with sim.running_market(store) as market:
        seller = market.user("seller")
        await list_and_post(seller)
        (listing,) = store.active()
        answer = craigslist_mail.Answer(
            "asking",
            listing.item_id,
            "email-abc123",
            "It's still available at $360. Make me an offer.",
            "key-1",
            buyer_said="Hello is the bike still available",
        )
        craigslist_mail._record_exchange(store, answer)
        craigslist_mail._record_chat_notice(store, "notice-1")
        reply = await seller.say("someone messaged me, reply to them")
        assert '"Hello is the bike still available"' in reply
        assert "I answered: \"It's still available at $360" in reply
        assert "Buyer 1 on your" in reply
        assert "1 chat message came in over the last day" in reply


async def test_a_pasted_post_link_is_saved_for_the_listing(
    market: sim.Market, store: Store
) -> None:
    """Buyers write their own subjects, so the seller's post link is what matches their email."""
    seller = market.user("seller")
    await seller.say("sell this, at least $300", BIKE)
    await seller.say("approve")
    (listing,) = store.active()
    link = "https://www.craigslist.org/view/d/san-francisco-trek-fx-disc/otenzZmpUXQLFoGHL2bxr7"
    reply = await seller.say(f"here it is {link}")
    assert "that Craigslist post is your Trek" in reply
    found = craigslist_mail.listing_for_post(store, "otenzZmpUXQLFoGHL2bxr7")
    assert found is not None and found.item_id == listing.item_id


async def test_an_offer_waiting_on_the_agent_shows_in_the_chat(
    store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Seen live: a buyer got "let me check" and the seller's chat said no buyers had emailed."""
    monkeypatch.setenv("YARDSALE_MAIL_ADDRESS", "yardsale.test@example.com")
    monkeypatch.setenv("YARDSALE_MAIL_PASSWORD", "app-password")
    async with sim.running_market(store) as market:
        seller = market.user("seller")
        await list_and_post(seller)
        (listing,) = store.active()
        held = craigslist_mail.Answer(
            "holding",
            listing.item_id,
            "email-abc123",
            craigslist_mail.HOLDING,
            "key-2",
            buyer_said="can you do 10 dollars and 50 cents?",
        )
        craigslist_mail._record_exchange(store, held)
        reply = await seller.say("any buyers?")
        assert "1 buyer emailing, no offer yet" in reply
        assert '"can you do 10 dollars and 50 cents?"' in reply
        assert "working on an answer" in reply
        assert "no buyers have emailed yet" not in reply
