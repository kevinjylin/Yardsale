"""Live mode's wiring, built but not started: nothing here reaches the network (conftest fails
any test that tries), so this checks configuration, not Fetch's services."""

from __future__ import annotations

import pytest

from tests import fakes
from yardsale import bureau_server
from yardsale.store import PROPOSED, Listing, Store


async def test_live_build_restores_item_agents_at_the_public_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("YARDSALE_PUBLIC_URL", "https://yardsale.example")
    store = Store()
    store.add_listing(
        Listing(
            item_id="bike",
            owner="seller",
            seller_id="payout",
            listing_ref="r",
            status=PROPOSED,
            title="Trek FX 2",
            asking_cents=36000,
            floor_cents=30000,
        )
    )
    store.activate("bike", "seller", "agent1qplaceholder")

    _, market, services = bureau_server.build(
        False, fakes.identify, fakes.research, fakes.judge, store=store
    )
    rt = bureau_server.runtime()
    assert not rt.offline and not rt.started
    (item_agent,) = rt.agents.values()
    assert [e.url for e in item_agent._endpoints] == ["https://yardsale.example/submit"]
    # The four public agents use Agentverse mailboxes, not the Bureau's endpoint.
    for agent in (market, *services):
        assert all("agentverse" in e.url for e in agent._endpoints)
    store.close()
