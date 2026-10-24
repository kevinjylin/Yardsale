"""Offline mode: a stand-in for the Almanac so the prototype runs without Fetch's servers.

Each process writes `<address>` -> endpoint files into a local folder, and the resolver reads
them. Nothing is registered anywhere. Live mode uses the real Almanac instead of this module.
"""

from __future__ import annotations

from typing import Any

from uagents.resolver import Resolver
from uagents_core.registration import AgentRegistrationPolicy, BatchRegistrationPolicy

from yardsale.common import DATA_DIR

DIRECTORY = DATA_DIR / "local_directory"


def publish_locally(address: str, endpoint: str) -> None:
    DIRECTORY.mkdir(parents=True, exist_ok=True)
    (DIRECTORY / address).write_text(endpoint)


class LocalDirectoryResolver(Resolver):
    async def resolve(self, destination: str) -> tuple[str | None, list[str]]:
        address = destination.rsplit("://", 1)[-1]
        entry = DIRECTORY / address
        if not entry.is_file():
            return None, []
        return address, [entry.read_text().strip()]


class NoRegistration(AgentRegistrationPolicy):
    async def register(self, *args: Any, **kwargs: Any) -> None:
        return None


class NoBatchRegistration(BatchRegistrationPolicy):
    def add_agent(self, *args: Any, **kwargs: Any) -> None:
        return None

    async def register(self) -> None:
        return None
