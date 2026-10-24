"""Sellers in the ASI:One chat: a test agent plays ASI:One and talks to the real Market over the
Agent Chat Protocol. Claude, Apify, ASI:One's model, and photo downloads are fakes."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest
from uagents import Agent, Context, Protocol
from uagents_core.contrib.protocols.chat import (
    ChatAcknowledgement,
    ChatMessage,
    MetadataContent,
    Resource,
    ResourceContent,
    TextContent,
    chat_protocol_spec,
)

from tests.fakes import (
    BIKE,
    fake_fetch,
    fake_identify,
    fake_judge,
    fake_read,
    fake_research,
    run_agents,
    slow_research,
)
from yardsale import bureau_server, chat
from yardsale.researcher import Research

PHOTO_URL = "https://res.cloudinary.com/asi1/image/upload/v1/bike.heic"


def message(text: str = "", photo: str | None = None, mime_type: str = "image/heic") -> ChatMessage:
    content: list[Any] = [TextContent(type="text", text=text)] if text else []
    if photo:
        resource = Resource(uri=photo, metadata={"mime_type": mime_type})
        content.append(ResourceContent(type="resource", resource_id=uuid4(), resource=resource))
    return ChatMessage(timestamp=datetime.now(UTC), msg_id=uuid4(), content=content)


def reply_text(msg: ChatMessage) -> str:
    return msg.text()


async def converse(
    messages: list[ChatMessage], replies_expected: int, research: Research = fake_research
) -> tuple[list[ChatMessage], int]:
    """Send each message after the previous one is answered; return the replies and acks."""
    _, market, services = bureau_server.build(
        True, fake_identify, research, fake_judge, read=fake_read, fetch=fake_fetch
    )
    replies: list[ChatMessage] = []
    acks = 0
    to_send = list(messages)
    done = asyncio.Event()
    proto = Protocol(spec=chat_protocol_spec)

    @proto.on_message(ChatMessage)
    async def on_reply(ctx: Context, sender: str, msg: ChatMessage) -> None:
        replies.append(msg)
        await ctx.send(
            sender, ChatAcknowledgement(timestamp=datetime.now(UTC), acknowledged_msg_id=msg.msg_id)
        )
        if len(replies) >= replies_expected:
            done.set()
        elif to_send and reply_text(msg) != chat.STILL_WORKING:
            await ctx.send(sender, to_send.pop(0))  # same conversation (session) as before

    @proto.on_message(ChatAcknowledgement)
    async def on_ack(ctx: Context, sender: str, msg: ChatAcknowledgement) -> None:
        nonlocal acks
        acks += 1

    asi_one = Agent(
        name="test-asi-one",
        seed=uuid4().hex,
        loop=asyncio.get_running_loop(),
        enable_agent_inspector=False,
        publish_agent_details=False,
    )
    asi_one.include(proto)

    @asi_one.on_event("startup")
    async def start(ctx: Context) -> None:
        await ctx.send(market.address, to_send.pop(0))

    await run_agents([market, *services, asi_one], done)
    return replies, acks


async def test_photo_in_chat_becomes_an_item_agent() -> None:
    replies, acks = await converse([message("Sell this, at least $300", PHOTO_URL)], 1)

    text = reply_text(replies[0])
    assert f"Your {BIKE.title} is for sale." in text
    assert "Asking price: $340" in text
    assert "Lowest price you'll take: $300" in text
    assert "Based on 5 eBay sales of the same product" in text
    assert acks == 1

    rt = bureau_server.runtime()
    (address, listing), *_ = rt.listings.items()
    assert address in text
    assert listing.floor_cents == 30000  # from the typed message, not the appraisal
    assert listing.seller_id.startswith("asi1:session:")
    # The first reply in a conversation tells ASI:One that uploads are welcome.
    assert any(isinstance(c, MetadataContent) for c in replies[0].content)


async def test_slow_pricing_answers_first_then_sends_the_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(chat, "REPLY_WITHIN", 0.2)
    replies, _ = await converse([message("", PHOTO_URL)], 2, research=slow_research)
    assert reply_text(replies[0]) == chat.STILL_WORKING
    assert "is for sale" in reply_text(replies[1])


async def test_a_repeated_photo_link_is_not_listed_twice() -> None:
    # ASI:One's planner repeats earlier links when it relays the next message.
    replies, _ = await converse(
        [message("Sell this", PHOTO_URL), message(f"thanks! [bike]({PHOTO_URL})")], 2
    )
    assert "is for sale" in reply_text(replies[0])
    assert reply_text(replies[1]) == chat.HELP
    assert len(bureau_server.runtime().listings) == 1


async def test_photo_link_in_the_text_is_used() -> None:
    replies, _ = await converse([message(f"sell this ![photo]({PHOTO_URL})")], 1)
    assert "is for sale" in reply_text(replies[0])


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("hi, what do you do?", chat.HELP),
        ("I want to sell my bike", chat.NEEDS_PHOTO),
        ("I want to sell this, see the photo", chat.PHOTO_MISSING),
        ("find me a cheap road bike", chat.BUYING_NOT_READY),
    ],
)
async def test_messages_without_a_photo(text: str, expected: str) -> None:
    replies, _ = await converse([message(text)], 1)
    assert reply_text(replies[0]) == expected
    assert not bureau_server.runtime().listings


async def test_a_photo_that_cannot_be_downloaded_is_explained() -> None:
    replies, _ = await converse([message("sell", "https://example.com/broken.jpg")], 1)
    assert reply_text(replies[0]).startswith("I couldn't list this. I couldn't download")


def test_photos_are_found_in_resources_and_text() -> None:
    msg = message(
        "@agent1qxyz sell these https://example.com/a.png. Listing like "
        "https://www.ebay.com/itm/123 isn't a photo",
        PHOTO_URL,
    )
    msg.content.append(
        ResourceContent(
            type="resource",
            resource_id=uuid4(),
            resource=Resource(
                uri="https://example.com/clip.mp4", metadata={"mime_type": "video/mp4"}
            ),
        )
    )
    assert [p.url for p in chat.photos_in(msg)] == [PHOTO_URL, "https://example.com/a.png"]
    assert chat.clean_text(msg) == (
        "sell these Listing like https://www.ebay.com/itm/123 isn't a photo"
    )


def test_resource_link_can_be_in_metadata() -> None:
    msg = message()
    msg.content.append(
        ResourceContent(
            type="resource",
            resource_id=uuid4(),
            resource=Resource(
                uri="agent-storage://abc", metadata={"mime_type": "image/jpeg", "url": PHOTO_URL}
            ),
        )
    )
    assert [p.url for p in chat.photos_in(msg)] == [PHOTO_URL]


def test_prices_in_replies_read_naturally() -> None:
    assert chat.usd(34000) == "$340"
    assert chat.usd(1250) == "$12.50"
    assert chat.to_cents(39.99) == 3999
    assert chat.to_cents(0) is None
