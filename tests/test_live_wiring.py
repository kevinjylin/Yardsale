"""Live mode's wiring, built but not started: nothing here reaches the network (conftest fails
any test that tries), so this checks configuration, not Fetch's services."""

from __future__ import annotations

import aiohttp
import pytest

from tests import fakes
from yardsale import agentverse, bureau_server
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
    # The Market and the service agents use Agentverse mailboxes, not the Bureau's endpoint.
    for agent in (market, *services):
        assert all("agentverse" in e.url for e in agent._endpoints)
    store.close()


async def test_an_agentverse_outage_at_startup_doesnt_stop_the_server(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Agentverse dropping the connection while the server reconnects a mailbox (seen on venue
    Wi-Fi) used to crash the server on start. Now it's reported and the server carries on."""

    async def drops(**_: object) -> None:
        raise aiohttp.ServerDisconnectedError()

    monkeypatch.setattr(agentverse, "register_in_agentverse", drops)
    agent = agentverse.service_agent("market", "Test Market", "", "test", offline=False)

    ok, detail = await agentverse.connect_mailbox(agent, "av-test-key")

    assert not ok
    assert detail == "Agentverse couldn't be reached (ServerDisconnectedError)"
