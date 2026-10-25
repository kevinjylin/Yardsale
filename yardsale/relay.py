"""The Market's requests to its service agents, sent through Agentverse.

Every Yardsale agent runs in one server, so uagents hands a message between them in memory and
Agentverse never sees it: the Identifier, Researcher, Appraiser, Market Scout, and Deal Finder
showed 0 interactions on their profiles. In live mode the Market posts each request to the
service's Agentverse mailbox instead, the way an agent elsewhere would, and the service fetches
it from there (uagents checks a mailbox every second). Replies still come back in memory.

A request Agentverse refuses, or one that hasn't arrived within ARRIVE_WITHIN seconds, is handed
over in memory instead. Services call claim() before doing any work, so whichever copy arrives
first is handled and the other is dropped: no request is lost or done twice.

Offline, and with YARDSALE_VIA_AGENTVERSE=0, every request is handed over in memory.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections import deque
from dataclasses import dataclass, field
from uuid import uuid4

from uagents import Agent, Model
from uagents.communication import dispatch_local_message, send_exchange_envelope
from uagents.dispatch import dispatcher
from uagents_core.envelope import Envelope
from uagents_core.types import DeliveryStatus

log = logging.getLogger("yardsale.relay")

ARRIVE_WITHIN = 8.0  # seconds a request gets to arrive through Agentverse before going in memory
EXPIRES_AFTER = 300  # seconds; the envelope's own expiry
REMEMBER_FOR = 900.0  # seconds a request ID is kept, so a copy arriving late is still dropped
AGENTVERSE = "agentverse.ai"  # a service's mailbox endpoint is on this host


@dataclass
class _Sent:
    label: str
    started: float
    arrived: asyncio.Event = field(default_factory=asyncio.Event)
    claimed: bool = False
    in_memory: bool = False  # handed over in memory after Agentverse refused it or was slow
    hop: float | None = None  # seconds from posting to the service picking it up from Agentverse


_on = False
_sent: dict[str, _Sent] = {}
hops: deque[tuple[str, float]] = deque(maxlen=200)  # (label, seconds), for measuring


def configure(offline: bool) -> bool:
    """Turn the relay on for a live server unless YARDSALE_VIA_AGENTVERSE is "0". Returns
    whether it's on."""
    global _on
    _on = not offline and os.getenv("YARDSALE_VIA_AGENTVERSE", "1").strip() != "0"
    _sent.clear()
    return _on


def enabled() -> bool:
    return _on


def claim(request_id: str) -> bool:
    """Whether a service should handle this copy of a request. Always True for a request the
    relay didn't send (another agent's, a test's); for one it did, True only for the first
    copy to arrive."""
    sent = _sent.get(request_id)
    if sent is None:
        return True
    if sent.claimed:
        return False
    sent.claimed = True
    if not sent.in_memory:
        sent.hop = time.monotonic() - sent.started
        hops.append((sent.label, sent.hop))
    sent.arrived.set()
    return True


async def deliver(sender: Agent, address: str, message: Model, request_id: str, label: str) -> str:
    """Send `message` from `sender` to the service at `address`: through Agentverse when the
    relay is on, else in memory. Returns once the service has it ("agentverse" or "memory").
    Raises DeliveryError if neither route reaches it."""
    if not _on:
        await _in_memory(sender, address, message)
        return "memory"
    _forget_old()
    sent = _Sent(label=label, started=time.monotonic())
    _sent[request_id] = sent
    try:
        refused = await _post(sender, address, message)
    except Exception as ex:  # an unreachable Almanac or Agentverse
        refused = type(ex).__name__
    if refused is None:
        try:
            await asyncio.wait_for(sent.arrived.wait(), ARRIVE_WITHIN)
        except TimeoutError:
            log.warning(
                f"{label}: not picked up from Agentverse within {ARRIVE_WITHIN:.0f}s; "
                "handing it over directly"
            )
        else:
            log.info(f"{label}: through Agentverse in {sent.hop or 0:.1f}s")
            return "agentverse"
    else:
        log.warning(f"{label}: Agentverse didn't take it ({refused}); handing it over directly")
    if sent.claimed:  # the Agentverse copy arrived while we gave up on it
        return "agentverse"
    sent.in_memory = True
    await _in_memory(sender, address, message)
    return "memory"


class DeliveryError(Exception):
    pass


async def _in_memory(sender: Agent, address: str, message: Model) -> None:
    if not dispatcher.contains(address):
        raise DeliveryError("that agent isn't running in this server")
    status = await dispatch_local_message(
        sender=sender.address,
        destination=address,
        schema_digest=Model.build_schema_digest(message),
        message=message.model_dump_json(),
        session_id=uuid4(),
    )
    if status.status == DeliveryStatus.FAILED:
        raise DeliveryError(status.detail)


async def _post(sender: Agent, address: str, message: Model) -> str | None:
    """Post a signed envelope to the service's Agentverse mailbox. None once Agentverse has
    it, else why not."""
    target, endpoints = await sender._resolver.resolve(address)
    # Only Agentverse counts: an endpoint on this machine would hand it straight back here.
    endpoints = [e for e in endpoints if AGENTVERSE in e]
    if not target or not endpoints:
        return "no Agentverse mailbox on the Almanac"
    envelope = Envelope(
        version=1,
        sender=sender.address,
        target=target,
        session=uuid4(),
        schema_digest=Model.build_schema_digest(message),
        expires=int(time.time()) + EXPIRES_AFTER,
    )
    envelope.encode_payload(message.model_dump_json())
    envelope.sign(sender.identity)
    status = await send_exchange_envelope(envelope, endpoints)
    if isinstance(status, Envelope) or status.status != DeliveryStatus.FAILED:
        return None
    return status.detail[:200]


def _forget_old() -> None:
    now = time.monotonic()
    for request_id, sent in list(_sent.items()):
        if now - sent.started > REMEMBER_FOR:
            del _sent[request_id]
