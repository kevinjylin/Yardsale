"""Agents that live on Agentverse: the Market (Yardsale), Identifier, Researcher, and Appraiser.

In live mode these agents use an Agentverse mailbox, so they show up under your Agentverse account
and other agents can reach them through Agentverse. They still run in this machine's Bureau.

Each one has to be connected to your account once. With AGENTVERSE_API_KEY set, the server does
that at startup. Without it, the server logs an Inspector link per agent; open it while signed in
to Agentverse, click Connect, and choose Mailbox.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from urllib.parse import quote

import aiohttp
from uagents import Agent
from uagents.mailbox import AgentverseConnectRequest, register_in_agentverse
from uagents_core.registration import RegistrationRequest

from yardsale.common import derive_seed
from yardsale.offline import LocalDirectoryResolver

READMES = Path(__file__).resolve().parent / "readmes"
INSPECTOR = "https://agentverse.ai/inspect/"

log = logging.getLogger("yardsale.agentverse")


def service_agent(key: str, name: str, handle: str, description: str, offline: bool) -> Agent:
    """A long-lived agent with a stable address and an Agentverse profile (README + description).

    `name` and `handle` must match what's set on Agentverse: connecting sends both, so a different
    value here would overwrite the one there.

    Offline, it finds other processes' agents (the test buyer, the sell command) through the
    local directory instead of the Almanac.
    """
    return Agent(
        name=name,
        handle=handle,
        seed=derive_seed(key),
        mailbox=not offline,
        resolve=LocalDirectoryResolver() if offline else None,
        description=description,
        readme_path=str(READMES / f"{key}.md"),
        publish_agent_details=not offline,
        # The Inspector endpoints are what Agentverse's Connect button talks to.
        enable_agent_inspector=not offline,
        # A slow request (Apify can take a minute) shouldn't block the next one.
        handle_messages_concurrently=True,
        loop=asyncio.get_running_loop(),
    )


def inspector_url(agent: Agent, port: int) -> str:
    uri = quote(f"http://127.0.0.1:{port}")
    return f"{INSPECTOR}?uri={uri}&address={agent.address}"


async def connect_mailbox(agent: Agent, api_key: str) -> tuple[bool, str]:
    """Do what the Inspector's Connect -> Mailbox button does, using an Agentverse API key.
    A network failure is reported, not raised, so the server still starts: an agent connected
    before keeps its mailbox."""
    # Mirrors Agent._handle_connect in uagents 0.25.x.
    details = RegistrationRequest(
        address=agent.address,
        name=agent.name,
        handle=agent._handle,
        url=None,
        profile=agent._build_registration_profile(),
        endpoints=agent._endpoints,
        protocols=list(agent.protocols.keys()),
        metadata=agent.metadata,
    )
    try:
        result = await register_in_agentverse(
            request=AgentverseConnectRequest(user_token=api_key, agent_type="mailbox"),
            identity=agent._identity,
            prefix=agent._prefix,
            agentverse=agent._agentverse,
            agent_details=details,
        )
    except (aiohttp.ClientError, TimeoutError) as ex:
        return False, f"Agentverse couldn't be reached ({type(ex).__name__})"
    return result.success, result.detail or ""
