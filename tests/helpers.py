"""Test helpers: an agent that sends any message and collects the replies, and a local web
server standing in for an outside service."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TypeVar
from uuid import uuid4

from aiohttp import web
from uagents import Agent, Context, Model, Protocol

M = TypeVar("M", bound=Model)
WAIT = 10.0  # seconds; replies in one process take milliseconds


class Peer:
    def __init__(self, name: str, *kinds: type[Model]) -> None:
        self.inbox: asyncio.Queue[tuple[str, Model]] = asyncio.Queue()
        self.agent = Agent(
            name=name,
            seed=uuid4().hex,
            loop=asyncio.get_running_loop(),
            enable_agent_inspector=False,
            publish_agent_details=False,
        )
        proto = Protocol(name=f"Test{name.title()}", version="0.1.0")

        async def collect(ctx: Context, sender: str, msg: Model) -> None:
            await self.inbox.put((sender, msg))

        for kind in kinds:
            proto.on_message(kind)(collect)
        self.agent.include(proto)
        self.agent.setup()

    @property
    def address(self) -> str:
        return self.agent.address

    async def send(self, to: str, msg: Model) -> None:
        await self.agent._build_context().send(to, msg)

    async def receive(self, kind: type[M]) -> M:
        """The next message of this kind; anything else that arrives first is dropped."""
        while True:
            _, msg = await asyncio.wait_for(self.inbox.get(), WAIT)
            if isinstance(msg, kind):
                return msg

    async def ask(self, to: str, msg: Model, kind: type[M]) -> M:
        await self.send(to, msg)
        return await self.receive(kind)


@asynccontextmanager
async def local_server(app: web.Application) -> AsyncIterator[str]:
    """Serve `app` on this machine and yield its base URL."""
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        await runner.cleanup()
