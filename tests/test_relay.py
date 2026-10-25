"""The relay: the Market's requests to its service agents go through Agentverse in live mode,
fall back to in-memory delivery, and are handled exactly once. Nothing here leaves this machine:
posting to Agentverse is replaced, and the Almanac lookup is a stub."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator
from types import SimpleNamespace
from typing import Any, cast
from uuid import uuid4

import pytest
from uagents import Agent, Model
from uagents_core.envelope import Envelope
from uagents_core.identity import Identity
from uagents_core.types import DeliveryStatus, MsgStatus

from tests import sim
from tests.helpers import WAIT, Peer
from yardsale import config, offline, relay

offline.stay_offline()


class Ask(Model):
    request_id: str


@pytest.fixture(autouse=True)
def fresh_relay(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Each test starts with the relay off and no history, whatever .env says."""
    monkeypatch.delenv("YARDSALE_VIA_AGENTVERSE", raising=False)
    relay.configure(offline=True)
    relay.hops.clear()
    yield
    relay.configure(offline=True)
    relay.hops.clear()


@pytest.fixture
async def agents() -> AsyncIterator[tuple[Peer, Peer]]:
    """A stand-in Market and a service that collects Ask messages, both in this process."""
    market, service = Peer("market"), Peer("service", Ask)
    yield market, service
    await sim.stop([market.agent, service.agent])


def no_post(monkeypatch: pytest.MonkeyPatch) -> None:
    async def post(*args: Any) -> str | None:
        raise AssertionError("the relay is off, so nothing should go to Agentverse")

    monkeypatch.setattr(relay, "_post", post)


def posted_ok(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Agentverse takes every post; returns the addresses posted to."""
    posted: list[str] = []

    async def post(sender: Agent, address: str, message: Model) -> str | None:
        posted.append(address)
        return None

    monkeypatch.setattr(relay, "_post", post)
    return posted


def count_in_memory(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Record in-memory handovers instead of making them."""
    handed: list[str] = []

    async def in_memory(sender: Agent, address: str, message: Model) -> None:
        handed.append(address)

    monkeypatch.setattr(relay, "_in_memory", in_memory)
    return handed


async def handled(service: Peer) -> list[str]:
    """Request IDs the service would work on: every Ask it got whose claim() was granted. Each
    caller has had one copy handed over in memory, so waits for it."""
    received = [await asyncio.wait_for(service.inbox.get(), WAIT)]
    await asyncio.sleep(0.02)  # anything sent twice by mistake would be here by now
    while not service.inbox.empty():
        received.append(service.inbox.get_nowait())
    out = []
    for _, msg in received:
        assert isinstance(msg, Ask)
        if relay.claim(msg.request_id):
            out.append(msg.request_id)
    return out


# --- relay off ---------------------------------------------------------------------------------


async def test_offline_hands_requests_over_in_memory(
    monkeypatch: pytest.MonkeyPatch, agents: tuple[Peer, Peer]
) -> None:
    market, service = agents
    no_post(monkeypatch)
    assert relay.configure(offline=True) is False
    assert not relay.enabled()
    how = await relay.deliver(market.agent, service.address, Ask(request_id="r1"), "r1", "identify")
    assert how == "memory"
    assert await handled(service) == ["r1"]
    assert not relay.hops


async def test_switch_off_in_live_mode_hands_requests_over_in_memory(
    monkeypatch: pytest.MonkeyPatch, agents: tuple[Peer, Peer]
) -> None:
    market, service = agents
    no_post(monkeypatch)
    monkeypatch.setenv("YARDSALE_VIA_AGENTVERSE", "0")
    assert relay.configure(offline=False) is False
    how = await relay.deliver(market.agent, service.address, Ask(request_id="r2"), "r2", "research")
    assert how == "memory"
    assert await handled(service) == ["r2"]


def test_live_mode_turns_it_on_unless_set_to_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    assert relay.configure(offline=False) is True
    for value in ("1", "yes", ""):
        monkeypatch.setenv("YARDSALE_VIA_AGENTVERSE", value)
        assert relay.configure(offline=False) is True
    monkeypatch.setenv("YARDSALE_VIA_AGENTVERSE", " 0 ")
    assert relay.configure(offline=False) is False
    monkeypatch.delenv("YARDSALE_VIA_AGENTVERSE")
    assert relay.configure(offline=True) is False


async def test_in_memory_needs_the_agent_in_this_server(agents: tuple[Peer, Peer]) -> None:
    market, _ = agents
    nowhere = Identity.from_seed(uuid4().hex, 0).address
    with pytest.raises(relay.DeliveryError):
        await relay.deliver(market.agent, nowhere, Ask(request_id="r3"), "r3", "appraise")


# --- relay on ----------------------------------------------------------------------------------


async def test_picked_up_from_agentverse(
    monkeypatch: pytest.MonkeyPatch, agents: tuple[Peer, Peer]
) -> None:
    market, service = agents
    posted = posted_ok(monkeypatch)
    handed = count_in_memory(monkeypatch)
    relay.configure(offline=False)

    async def service_fetches_mailbox() -> bool:
        await asyncio.sleep(0.02)
        return relay.claim("r4")

    fetch = asyncio.create_task(service_fetches_mailbox())
    how = await relay.deliver(market.agent, service.address, Ask(request_id="r4"), "r4", "scout")
    assert how == "agentverse"
    assert await fetch is True
    assert posted == [service.address] and handed == []
    assert len(relay.hops) == 1
    label, seconds = relay.hops[0]
    assert label == "scout" and 0 < seconds < relay.ARRIVE_WITHIN
    assert relay.claim("r4") is False  # any second copy is dropped


async def test_refused_by_agentverse_goes_in_memory(
    monkeypatch: pytest.MonkeyPatch, agents: tuple[Peer, Peer]
) -> None:
    market, service = agents

    async def refuse(sender: Agent, address: str, message: Model) -> str | None:
        return "no Agentverse mailbox on the Almanac"

    monkeypatch.setattr(relay, "_post", refuse)
    relay.configure(offline=False)
    how = await relay.deliver(market.agent, service.address, Ask(request_id="r5"), "r5", "identify")
    assert how == "memory"
    assert await handled(service) == ["r5"]
    assert not relay.hops  # an in-memory handover isn't an Agentverse hop


async def test_post_that_raises_goes_in_memory(
    monkeypatch: pytest.MonkeyPatch, agents: tuple[Peer, Peer]
) -> None:
    market, service = agents

    async def unreachable(sender: Agent, address: str, message: Model) -> str | None:
        raise ConnectionError("Almanac API unreachable")

    monkeypatch.setattr(relay, "_post", unreachable)
    relay.configure(offline=False)
    how = await relay.deliver(market.agent, service.address, Ask(request_id="r6"), "r6", "research")
    assert how == "memory"
    assert await handled(service) == ["r6"]


async def test_slow_agentverse_goes_in_memory_and_the_late_copy_is_dropped(
    monkeypatch: pytest.MonkeyPatch, agents: tuple[Peer, Peer]
) -> None:
    market, service = agents
    posted = posted_ok(monkeypatch)
    monkeypatch.setattr(relay, "ARRIVE_WITHIN", 0.05)
    relay.configure(offline=False)
    how = await relay.deliver(market.agent, service.address, Ask(request_id="r7"), "r7", "appraise")
    assert how == "memory" and posted == [service.address]
    assert await handled(service) == ["r7"]  # the in-memory copy is worked on
    assert relay.claim("r7") is False  # the Agentverse copy, fetched late, is not
    assert not relay.hops


async def test_agentverse_copy_first_then_in_memory_copy_is_dropped(
    monkeypatch: pytest.MonkeyPatch, agents: tuple[Peer, Peer]
) -> None:
    """The service takes the Agentverse copy after the relay gave up but before the in-memory
    copy reaches it: only the first is worked on."""
    market, service = agents
    posted_ok(monkeypatch)
    monkeypatch.setattr(relay, "ARRIVE_WITHIN", 0.05)
    relay.configure(offline=False)
    how = await relay.deliver(market.agent, service.address, Ask(request_id="r8"), "r8", "scout")
    assert how == "memory"
    assert relay.claim("r8") is True  # Agentverse's copy wins the race
    assert await handled(service) == []  # so the in-memory one is dropped


async def test_claimed_while_giving_up_counts_as_agentverse(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A copy claimed between the wait running out and the in-memory handover isn't sent
    twice."""
    handed = count_in_memory(monkeypatch)
    posted_ok(monkeypatch)
    relay.configure(offline=False)

    async def wait_for(awaitable: Any, *_: Any) -> None:
        cast(Any, awaitable).close()  # the Event.wait() coroutine, never awaited
        relay.claim("r9")  # the mailbox copy lands just as the wait runs out
        raise TimeoutError

    monkeypatch.setattr(relay.asyncio, "wait_for", wait_for)
    market = SimpleNamespace(address="market")
    how = await relay.deliver(cast(Agent, market), "service", Ask(request_id="r9"), "r9", "x")
    assert how == "agentverse" and handed == []


def test_claim_unknown_request_is_always_handled() -> None:
    assert relay.claim("never-relayed") is True
    assert relay.claim("never-relayed") is True  # a request the relay didn't send isn't tracked


async def test_old_requests_are_forgotten(monkeypatch: pytest.MonkeyPatch) -> None:
    count_in_memory(monkeypatch)

    async def refuse(sender: Agent, address: str, message: Model) -> str | None:
        return "refused"

    monkeypatch.setattr(relay, "_post", refuse)
    monkeypatch.setattr(relay, "REMEMBER_FOR", 0.0)
    relay.configure(offline=False)
    market = cast(Agent, SimpleNamespace(address="market"))
    await relay.deliver(market, "service", Ask(request_id="old"), "old", "x")
    await asyncio.sleep(0.01)
    await relay.deliver(market, "service", Ask(request_id="new"), "new", "x")
    assert "old" not in relay._sent and "new" in relay._sent


# --- posting to the mailbox ----------------------------------------------------------------------


class StubResolver:
    def __init__(self, target: str | None, endpoints: list[str]) -> None:
        self.target, self.endpoints = target, endpoints

    async def resolve(self, destination: str) -> tuple[str | None, list[str]]:
        return self.target, self.endpoints


def stub_sender(resolver: StubResolver) -> Agent:
    identity = Identity.from_seed(uuid4().hex, 0)
    return cast(
        Agent, SimpleNamespace(address=identity.address, identity=identity, _resolver=resolver)
    )


def capture_sends(
    monkeypatch: pytest.MonkeyPatch, status: DeliveryStatus = DeliveryStatus.DELIVERED
) -> list[tuple[Envelope, list[str]]]:
    sent: list[tuple[Envelope, list[str]]] = []

    async def send(envelope: Envelope, endpoints: list[str], sync: bool = False) -> MsgStatus:
        sent.append((envelope, endpoints))
        return MsgStatus(
            status=status,
            detail="mailbox full" if status == DeliveryStatus.FAILED else "ok",
            destination=envelope.target,
            endpoint=endpoints[0],
            session=envelope.session,
        )

    monkeypatch.setattr(relay, "send_exchange_envelope", send)
    return sent


@pytest.mark.parametrize(
    "endpoints",
    [
        ["http://127.0.0.1:8100/submit"],
        ["http://localhost:8100/submit", "https://abc.ngrok.app/submit"],
        [],
    ],
)
async def test_post_skips_endpoints_that_arent_agentverse(
    monkeypatch: pytest.MonkeyPatch, endpoints: list[str]
) -> None:
    sent = capture_sends(monkeypatch)
    service = Identity.from_seed(uuid4().hex, 0).address
    sender = stub_sender(StubResolver(service, endpoints))
    refused = await relay._post(sender, service, Ask(request_id="p1"))
    assert refused == "no Agentverse mailbox on the Almanac"
    assert sent == []


async def test_post_with_no_almanac_entry_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    sent = capture_sends(monkeypatch)
    sender = stub_sender(StubResolver(None, ["https://agentverse.ai/v1/submit"]))
    assert await relay._post(sender, "nobody", Ask(request_id="p2")) is not None
    assert sent == []


async def test_post_sends_a_signed_envelope_to_agentverse_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent = capture_sends(monkeypatch)
    service = Identity.from_seed(uuid4().hex, 0).address
    mailbox = "https://agentverse.ai/v1/submit"
    sender = stub_sender(StubResolver(service, ["http://127.0.0.1:8100/submit", mailbox]))
    message = Ask(request_id="p3")
    assert await relay._post(sender, service, message) is None
    [(envelope, endpoints)] = sent
    assert endpoints == [mailbox]
    assert envelope.sender == sender.address and envelope.target == service
    assert envelope.schema_digest == Model.build_schema_digest(message)
    assert envelope.verify()
    decoded = envelope.decode_payload()
    assert Ask.model_validate_json(decoded) == message


async def test_post_refused_by_agentverse_says_why(monkeypatch: pytest.MonkeyPatch) -> None:
    capture_sends(monkeypatch, DeliveryStatus.FAILED)
    service = Identity.from_seed(uuid4().hex, 0).address
    sender = stub_sender(StubResolver(service, ["https://agentverse.ai/v1/submit"]))
    assert await relay._post(sender, service, Ask(request_id="p4")) == "mailbox full"


# --- readiness check ---------------------------------------------------------------------------


def _relay_line(mode: str) -> str:
    text, _ = config.report(mode)
    [line] = [line for line in text.splitlines() if "YARDSALE_VIA_AGENTVERSE" in line]
    return line


def test_readiness_shows_the_switch(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _relay_line("live").startswith("  [ok  ]")
    assert "Agentverse" in _relay_line("live")
    assert "in memory" in _relay_line("offline")
    monkeypatch.setenv("YARDSALE_VIA_AGENTVERSE", "0")
    assert _relay_line("live").startswith("  [warn]")
    assert "in memory" in _relay_line("live")
    # the check and the relay agree
    assert relay.configure(offline=False) is False
