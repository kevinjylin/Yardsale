from __future__ import annotations

import ipaddress
import socket
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from uagents.dispatch import dispatcher

from tests import sim
from yardsale import offline
from yardsale.store import Store

# No test talks to Fetch's ledger or Almanac, even to say an agent started.
offline.stay_offline()

PROVIDER_KEYS = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "APIFY_TOKEN",
    "ASI_ONE_API_KEY",
    "AGENTVERSE_API_KEY",
    "YARDSALE_PUBLIC_URL",
    "YARDSALE_API_KEY",
    "YARDSALE_API_URL",
    "YARDSALE_DB",
    "EBAY_CLIENT_ID",
    "EBAY_CLIENT_SECRET",
    "EBAY_RUNAME",
    "YARDSALE_EBAY_ENV",
)


@pytest.fixture(autouse=True)
def isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    """Fresh agent addresses per test, no writes to the real .agent-data folder, and no keys,
    even if .env has real ones: a test can never reach a paid service."""
    monkeypatch.setenv("YARDSALE_MASTER_SEED", uuid4().hex)
    for name in PROVIDER_KEYS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(offline, "DIRECTORY", tmp_path / "directory")
    before = set(dispatcher.sinks)
    yield
    for address in set(dispatcher.sinks) - before:
        del dispatcher.sinks[address]


def _local(host: Any) -> bool:
    if host in ("localhost", None, "") or not isinstance(host, str):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


@pytest.fixture(autouse=True)
def no_internet(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Fail any test that looks up or connects to anything but this machine, even when the code
    under test catches the error (uAgents logs and swallows some network failures)."""
    real_connect, real_connect_ex = socket.socket.connect, socket.socket.connect_ex
    real_getaddrinfo = socket.getaddrinfo
    attempts: list[str] = []

    def check(address: Any) -> None:
        host = address[0] if isinstance(address, tuple) else None
        if not _local(host):
            attempts.append(str(host))
            raise RuntimeError(f"a test tried to reach {host}")

    def connect(self: socket.socket, address: Any) -> None:
        check(address)
        real_connect(self, address)

    def connect_ex(self: socket.socket, address: Any) -> int:
        check(address)
        return real_connect_ex(self, address)

    def getaddrinfo(host: Any, *args: Any, **kwargs: Any) -> Any:
        if not _local(host):
            attempts.append(str(host))
            raise RuntimeError(f"a test tried to look up {host}")
        return real_getaddrinfo(host, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket.socket, "connect_ex", connect_ex)
    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)
    yield
    assert not attempts, f"tests must stay offline, but this one tried to reach {attempts}"


@pytest.fixture
def store() -> Iterator[Store]:
    db = Store()
    yield db
    db.close()


@pytest.fixture
async def market(store: Store) -> AsyncIterator[sim.Market]:
    """The Market, its services, and the store's item agents, with every provider faked."""
    async with sim.running_market(store) as running:
        yield running
