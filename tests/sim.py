"""Run Yardsale's agents in one process with no network, for the tests.

Agents are started without servers; uAgents' dispatcher delivers messages between agents in the
same process directly. ChatUser plays ASI:One for one conversation. Call offline.stay_offline()
first.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from uagents import Agent, Context, Protocol
from uagents.dispatch import dispatcher
from uagents_core.contrib.protocols.chat import (
    ChatAcknowledgement,
    ChatMessage,
    MetadataContent,
    Resource,
    ResourceContent,
    StartSessionContent,
    TextContent,
    chat_protocol_spec,
)
from uagents_core.contrib.protocols.chat.cards import (
    Card,
    SelectionValue,
    create_card_response_content,
    extract_card,
)

from tests import fakes
from yardsale import bureau_server, chat
from yardsale.appraiser import Judge
from yardsale.asione import ReadMessage
from yardsale.avatars import ItemPictures
from yardsale.chat import FetchPhoto
from yardsale.craigslist_mail import Mailbox, MailSettings
from yardsale.ebay_seller import EbaySeller
from yardsale.hosting import HostingClient
from yardsale.identifier import Identify
from yardsale.payments import PaymentProvider
from yardsale.researcher import Research
from yardsale.sources import Source
from yardsale.store import Store

REPLY_TIMEOUT = 10.0


def card_of(msg: ChatMessage) -> Card | None:
    """The Interactive Card a reply carries, if any."""
    for block in msg.content:
        if isinstance(block, MetadataContent) and (card := extract_card(block)) is not None:
            return card
    return None


def cards_of(msg: ChatMessage) -> list[Card]:
    """Every Interactive Card a reply carries, in order."""
    return [
        card
        for block in msg.content
        if isinstance(block, MetadataContent) and (card := extract_card(block)) is not None
    ]


def card_text(card: Card) -> str:
    """Everything a card shows, one piece per line: titles, text, "label: value" rows, badges,
    carousel items, and button labels. What a person reading the card would see."""
    lines: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, list):
            for child in node:
                walk(child)
            return
        if not isinstance(node, dict):
            return
        for key in ("title", "subtitle", "value", "label", "secondary_text", "placeholder"):
            if isinstance(node.get(key), str) and node.get("type") != "image":
                if key == "value" and "label" in node and node.get("type") is None:
                    continue
                lines.append(node[key])
        if "label" in node and isinstance(node.get("value"), str) and node.get("type") is None:
            lines.pop() if lines and lines[-1] == node["label"] else None
            lines.append(f"{node['label']}: {node['value']}")
        for key in ("primary_cta", "submit_cta", "approve_cta", "reject_cta", "badges"):
            walk(node.get(key))
        for key in ("root", "children", "items", "summary_rows", "ctas", "fields"):
            walk(node.get(key))

    walk(card.payload.model_dump(exclude_none=True))
    return "\n".join(lines)


def card_buttons(card: Card) -> list[tuple[str, dict[str, Any]]]:
    """Every (label, selection) a person can tap on a card, in order."""
    found: list[tuple[str, dict[str, Any]]] = []

    def walk(node: Any) -> None:
        if isinstance(node, list):
            for child in node:
                walk(child)
            return
        if not isinstance(node, dict):
            return
        if node.get("type") == "button":
            found.append((node["label"], dict(node["action"]["selection"])))
        if isinstance(node.get("action"), dict) and node.get("type") is None:
            found.append(("", dict(node["action"]["selection"])))
        for key in ("primary_cta", "submit_cta", "approve_cta", "reject_cta"):
            if isinstance(node.get(key), dict):
                found.append((node[key]["label"], dict(node[key]["selection"])))
        for cta in node.get("ctas") or []:
            found.append((cta["label"], dict(cta["selection"])))
        for key in ("root", "children", "items"):
            walk(node.get(key))

    walk(card.payload.model_dump(exclude_none=True))
    return found


def button(msg: ChatMessage, action: str, **match: Any) -> dict[str, Any]:
    """The selection of the first button on a reply's card with this action (and fields)."""
    card = card_of(msg)
    assert card is not None, msg.text()
    for _, selection in card_buttons(card):
        if selection.get("action") == action and all(
            selection.get(k) == v for k, v in match.items()
        ):
            return selection
    raise AssertionError(f"no {action} button in {card_buttons(card)}")


def shown(msg: ChatMessage) -> str:
    """A reply as a person sees it: the text, then what its card shows."""
    card = card_of(msg)
    return msg.text() + ("\n" + card_text(card) if card is not None else "")


# Messages the Market sends right after an answer, which ChatUser.exchange sets aside.
FOLLOW_UPS = ("closest eBay sales behind that price",)


def start(agents: list[Agent]) -> None:
    for agent in agents:
        agent.setup()


async def stop(agents: list[Agent]) -> None:
    """Stop the agents' tasks and take them off the dispatcher, like a process exiting."""
    tasks: list[asyncio.Task[Any]] = []
    for agent in agents:
        for task in (agent._message_queue_task, agent._dispenser_task, agent._registration_task):
            if task is not None:
                tasks.append(task)
        tasks += [*agent._message_tasks, *agent._interval_tasks]
        dispatcher.unregister(agent.address, agent)
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


@dataclass
class Market:
    """A running Market with its services and item agents."""

    agent: Agent
    services: list[Agent]
    store: Store
    users: list[ChatUser] = field(default_factory=list)

    @property
    def address(self) -> str:
        return self.agent.address

    def user(self, name: str, session: UUID | None = None) -> ChatUser:
        user = ChatUser(name, self.address, session)
        self.users.append(user)
        return user


@asynccontextmanager
async def running_market(
    store: Store,
    identify: Identify = fakes.identify,
    research: Research = fakes.research,
    judge: Judge = fakes.judge,
    read: ReadMessage = fakes.read,
    fetch: FetchPhoto = fakes.fetch,
    payments: PaymentProvider | None = None,
    hosting: HostingClient | None = None,
    host_limit: int = 0,
    round_timeout: float = 5.0,
    pictures: ItemPictures | None = None,
    ebay: EbaySeller | None = None,
    mail: MailSettings | None = None,
    mailbox: Mailbox | None = None,
    sources: dict[str, Source] | None = None,
    scout_sources: dict[str, Source] | None = None,
) -> AsyncIterator[Market]:
    """The Market and friends in offline mode, with the store's active listings restored."""
    _, market, services = bureau_server.build(
        True,
        identify,
        research,
        judge,
        read=read,
        fetch=fetch,
        store=store,
        payments=payments,
        hosting=hosting,
        host_limit=host_limit,
        round_timeout=round_timeout,
        pictures=pictures,
        ebay=ebay,
        mail=mail,
        mailbox=mailbox,
        sources=sources,
        scout_sources=scout_sources,
    )
    rt = bureau_server.runtime()
    start([market, *services, *rt.agents.values()])
    rt.started = True
    running = Market(market, services, store)
    try:
        yield running
    finally:
        await stop([market, *services, *rt.agents.values(), *(u.agent for u in running.users)])


def chat_message(
    text: str = "",
    photo: str | None = None,
    mime_type: str = "image/jpeg",
    start_session: bool = False,
) -> ChatMessage:
    content: list[Any] = [StartSessionContent(type="start-session")] if start_session else []
    if text:
        content.append(TextContent(type="text", text=text))
    if photo:
        resource = Resource(uri=photo, metadata={"mime_type": mime_type})
        content.append(ResourceContent(type="resource", resource_id=uuid4(), resource=resource))
    return ChatMessage(timestamp=datetime.now(UTC), msg_id=uuid4(), content=content)


class ChatUser:
    """One ASI:One conversation with the Market: one agent, one session."""

    def __init__(self, name: str, market: str, session: UUID | None = None) -> None:
        self.name = name
        self.market = market
        self.session: UUID = session or uuid4()  # the same session continues a conversation
        self.replies: asyncio.Queue[ChatMessage] = asyncio.Queue()
        self.follow_ups: list[ChatMessage] = []  # sent after an answer, like the sales carousel
        self.interim: list[str] = []  # "still searching other sites" replies, set aside
        self.acks = 0
        self.agent = Agent(
            name=name,
            seed=uuid4().hex,
            loop=asyncio.get_running_loop(),
            enable_agent_inspector=False,
            publish_agent_details=False,
        )
        proto = Protocol(spec=chat_protocol_spec)

        @proto.on_message(ChatMessage)
        async def on_reply(ctx: Context, sender: str, msg: ChatMessage) -> None:
            await ctx.send(
                sender,
                ChatAcknowledgement(timestamp=datetime.now(UTC), acknowledged_msg_id=msg.msg_id),
            )
            await self.replies.put(msg)

        @proto.on_message(ChatAcknowledgement)
        async def on_ack(ctx: Context, sender: str, msg: ChatAcknowledgement) -> None:
            self.acks += 1

        self.agent.include(proto)
        self.agent.setup()

    async def send(self, msg: ChatMessage) -> None:
        ctx = self.agent._build_context()
        ctx._session = self.session  # ASI:One keeps one session per conversation
        await ctx.send(self.market, msg)

    async def next_reply(self) -> ChatMessage:
        return await asyncio.wait_for(self.replies.get(), REPLY_TIMEOUT)

    async def say(self, text: str = "", photo: str | None = None) -> str:
        """Send a message and return the Market's answer, skipping "still working" replies."""
        return shown(await self.exchange(chat_message(text, photo)))

    async def tap(self, selection: dict[str, SelectionValue] | None) -> str:
        """Tap a card button with this selection (None dismisses the card)."""
        response = create_card_response_content(selection=selection, cancelled=selection is None)
        msg = ChatMessage(timestamp=datetime.now(UTC), msg_id=uuid4(), content=[response])
        return shown(await self.exchange(msg))

    async def exchange(self, msg: ChatMessage) -> ChatMessage:
        """Send a message and return the Market's answer, skipping "still working" replies."""
        await self.send(msg)
        while True:
            reply = await self.next_reply()
            if any(marker in reply.text() for marker in FOLLOW_UPS):
                self.follow_ups.append(reply)
            elif reply.text().endswith(chat.SEARCHING_ELSEWHERE):
                self.interim.append(reply.text())
            elif reply.text() not in (chat.STILL_WORKING, chat.WORKING):
                return reply
