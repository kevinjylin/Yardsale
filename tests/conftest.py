from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import pytest
from uagents.dispatch import dispatcher

from yardsale import offline


@pytest.fixture(autouse=True)
def isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    """Fresh agent addresses per test, no writes to the real .agent-data folder, no real keys."""
    monkeypatch.setenv("YARDSALE_MASTER_SEED", uuid4().hex)
    monkeypatch.setenv("ASI_ONE_API_KEY", "")
    monkeypatch.setattr(offline, "DIRECTORY", tmp_path / "directory")
    before = set(dispatcher.sinks)
    yield
    for address in set(dispatcher.sinks) - before:
        del dispatcher.sinks[address]
