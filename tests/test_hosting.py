"""Item agents hosted on Agentverse (hosting.py, hosted_item_template.py).

A stand-in for Agentverse's Hosting API runs each uploaded agent in this process, so the
generated code is the code under test: it must compile, speak the same protocol as the Bureau's
item agents, and haggle by the same rules.
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any
from uuid import uuid4

import aiohttp
import pytest
import uagents
from aiohttp import web
from uagents import Agent, Model
from uagents_core.identity import Identity

from tests import sim
from tests.helpers import Peer, local_server
from yardsale import hosting, models
from yardsale.hosting import HOSTED_MODELS, HostingClient, HostingError, host_item, item_agent_code
from yardsale.models import AcceptCounter, Counter, Deal, ItemSold, Offer, OfferRejected
from yardsale.search import search
from yardsale.store import AGENTVERSE, BUREAU, PROPOSED, SOLD, Listing, Store

BUYER_KINDS = (Counter, Deal, OfferRejected)
PROFILE = re.compile(r"agentverse\.ai/agents/details/(agent1q[0-9a-z]+)/profile")


@pytest.fixture(autouse=True)
def storage_in_tmp(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)  # uAgents keeps agent storage in the working directory


async def no_manifest(manifest: dict[str, Any]) -> None:
    """Agentverse would publish the protocol manifest; tests stay offline."""


def run_hosted(code: str, seed: str, storage: dict[str, Any]) -> tuple[Agent, dict[str, Any]]:
    """Run a hosted agent's code the way Agentverse would, with this seed and storage."""

    def make_agent(*args: Any, **kwargs: Any) -> Agent:
        agent = Agent(
            name="hosted",
            seed=seed,
            loop=asyncio.get_running_loop(),
            enable_agent_inspector=False,
            publish_agent_details=False,
        )
        agent.publish_manifest = no_manifest  # type: ignore[method-assign]
        return agent

    namespace: dict[str, Any] = {"__name__": "agentverse_hosted"}
    real = uagents.Agent
    uagents.Agent = make_agent  # type: ignore[misc]
    try:
        exec(compile(code, "agent.py", "exec", dont_inherit=True), namespace)
    finally:
        uagents.Agent = real  # type: ignore[misc]
    agent: Agent = namespace["agent"]
    for key, value in storage.items():
        agent._storage.set(key, value)
    agent.setup()
    return agent, namespace


class FakeAgentverse(HostingClient):
    """Agentverse's Hosting API, in memory. Started agents run in this process."""

    def __init__(self) -> None:
        super().__init__("test-key", "http://127.0.0.1:9")
        self.hosted: dict[str, dict[str, Any]] = {}
        self.deleted: list[str] = []
        self.fail_start = False

    async def create(self, name: str, readme: str, short_description: str) -> str:
        seed = uuid4().hex
        address = Identity.from_seed(seed, 0).address
        self.hosted[address] = {
            "seed": seed,
            "name": name,
            "readme": readme,
            "secrets": {},
            "storage": {},
            "code": "",
            "agent": None,
        }
        return address

    async def set_secret(self, address: str, name: str, secret: str) -> None:
        self.hosted[address]["secrets"][name] = secret

    async def set_storage(self, address: str, key: str, value: str) -> None:
        self.hosted[address]["storage"][key] = value

    async def upload_code(self, address: str, code: str) -> None:
        self.hosted[address]["code"] = code

    async def start(self, address: str) -> None:
        if self.fail_start:
            raise HostingError("Agentverse POST /v1/hosting/agents/... returned 500")
        entry = self.hosted[address]
        entry["agent"], entry["namespace"] = run_hosted(
            entry["code"], entry["seed"], entry["storage"]
        )

    async def delete(self, address: str) -> None:
        entry = self.hosted.pop(address)
        self.deleted.append(address)
        if entry["agent"] is not None:
            await sim.stop([entry["agent"]])

    async def agents(self) -> list[dict[str, Any]]:
        return [{"address": a, "name": e["name"]} for a, e in self.hosted.items()]

    async def stop_all(self) -> None:
        await sim.stop([e["agent"] for e in self.hosted.values() if e["agent"] is not None])


def bike(item_id: str = "bike") -> Listing:
    return Listing(
        item_id=item_id,
        owner="seller",
        seller_id="payout",
        listing_ref=item_id,
        status=PROPOSED,
        title="Trek FX 2",
        asking_cents=36000,
        floor_cents=30000,
    )


SETTINGS = {
    "item_id": "bike",
    "title": "Trek FX 2",
    "asking_cents": 36000,
    "market": "agent1qmarket",
    "shopper": "agent1qshopper",
}


# --- The generated code ------------------------------------------------------------------------


def test_the_generated_code_speaks_the_same_protocol() -> None:
    code = item_agent_code(SETTINGS)
    compile(code, "agent.py", "exec", dont_inherit=True)
    assert "yardsale" not in "".join(
        line for line in code.splitlines() if line.startswith(("import", "from"))
    )  # only packages Agentverse allows
    assert "floor" not in json.dumps(SETTINGS)


async def test_generated_messages_match_the_bureaus() -> None:
    _, namespace = run_hosted(item_agent_code(SETTINGS), uuid4().hex, {})
    for model in HOSTED_MODELS:
        copy = namespace[model.__name__]
        assert Model.build_schema_digest(copy) == Model.build_schema_digest(model)
    assert namespace["HAGGLE_SPEC"].digest == models.HAGGLE_SPEC.digest
    assert namespace["CONFIG"] == SETTINGS


async def test_a_hosted_agent_haggles_and_reports_its_sale(monkeypatch: pytest.MonkeyPatch) -> None:
    market = Peer("market", ItemSold)
    settings = {**SETTINGS, "market": market.address}
    monkeypatch.setenv("YARDSALE_LIMITS", json.dumps({"floor_cents": 30000, "gap_cents": 1500}))
    agent, _ = run_hosted(item_agent_code(settings), uuid4().hex, {})
    bob, carol = Peer("bob", *BUYER_KINDS), Peer("carol", *BUYER_KINDS)
    try:
        counter = await bob.ask(agent.address, Offer(item_id="bike", offer_cents=25000), Counter)
        assert 31500 <= counter.counter_cents < 36000 and not counter.agreed
        lower = await bob.ask(agent.address, Offer(item_id="bike", offer_cents=27000), Counter)
        assert lower.counter_cents < counter.counter_cents
        stale = await bob.ask(
            agent.address,
            AcceptCounter(item_id="bike", counter_cents=counter.counter_cents),
            OfferRejected,
        )
        assert stale.code == "counter_changed"
        nobody = await carol.ask(
            agent.address,
            AcceptCounter(item_id="bike", counter_cents=lower.counter_cents),
            OfferRejected,
        )
        assert nobody.code == "no_counter"
        deal = await bob.ask(
            agent.address, AcceptCounter(item_id="bike", counter_cents=lower.counter_cents), Deal
        )
        sold = await market.receive(ItemSold)
        assert (sold.deal_id, sold.price_cents, sold.buyer) == (
            deal.deal_id,
            lower.counter_cents,
            f"agent:{bob.address}",
        )
        gone = await carol.ask(
            agent.address, Offer(item_id="bike", offer_cents=36000), OfferRejected
        )
        assert gone.code == "unavailable"
    finally:
        await sim.stop([agent, bob.agent, carol.agent, market.agent])


async def test_a_hosted_agent_without_limits_takes_no_offers() -> None:
    agent, _ = run_hosted(item_agent_code(SETTINGS), uuid4().hex, {})
    bob = Peer("bob", *BUYER_KINDS)
    try:
        rejected = await bob.ask(
            agent.address, Offer(item_id="bike", offer_cents=36000), OfferRejected
        )
        assert rejected.code == "unavailable"
    finally:
        await sim.stop([agent, bob.agent])


# --- Creating hosted agents --------------------------------------------------------------------


async def test_host_item_sets_up_the_agent_and_keeps_the_floor_secret() -> None:
    agentverse = FakeAgentverse()
    try:
        address = await host_item(agentverse, bike(), 1500, "agent1qmarket", "agent1qshopper")
        entry = agentverse.hosted[address]
        limits = {"floor_cents": 30000, "gap_cents": 1500}
        assert json.loads(entry["secrets"]["YARDSALE_LIMITS"]) == limits
        assert json.loads(entry["storage"]["limits"]) == limits
        assert entry["name"] == "Trek FX 2"
        assert "$360" in entry["readme"] and "$300" not in entry["readme"]
        assert "tag:innovationlab" in entry["readme"]
        assert "30000" not in entry["code"]
    finally:
        await agentverse.stop_all()


def test_agent_names_fit_what_agentverse_shows() -> None:
    assert hosting.agent_name("Trek FX 2") == "Trek FX 2"
    # Agentverse cut "Yardsale item: Specialized Sirrus..." to "Yardsale item: Specialized Sirru".
    name = hosting.agent_name("Specialized Sirrus hybrid bike (test listing)")
    assert name == "Specialized Sirrus hybrid bike"
    assert len(hosting.agent_name("x" * 50)) == hosting.NAME_LIMIT


async def test_a_failed_start_deletes_the_half_made_agent() -> None:
    agentverse = FakeAgentverse()
    agentverse.fail_start = True
    with pytest.raises(HostingError):
        await host_item(agentverse, bike(), 1500, "agent1qmarket", "agent1qshopper")
    assert agentverse.hosted == {} and len(agentverse.deleted) == 1


async def test_a_slow_agentverse_is_a_hosting_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """A timeout is a HostingError, so approval falls back to running the agent in the server."""

    async def slow(request: web.Request) -> web.Response:
        await asyncio.sleep(1)
        return web.json_response({})

    app = web.Application()
    app.router.add_post("/v1/hosting/agents", slow)
    monkeypatch.setattr(hosting, "TIMEOUT", aiohttp.ClientTimeout(total=0.05))
    async with local_server(app) as base:
        with pytest.raises(HostingError, match="couldn't be reached"):
            await HostingClient("av-test-key", base).create("Trek FX 2", "readme", "a bike")


def test_hosting_is_off_unless_a_limit_is_set(monkeypatch: pytest.MonkeyPatch) -> None:
    assert hosting.hosted_limit() == 0
    monkeypatch.setenv("YARDSALE_HOST_ITEMS", "5")
    assert hosting.hosted_limit() == 5
    monkeypatch.setenv("YARDSALE_HOST_ITEMS", "many")
    assert hosting.hosted_limit() == 0


# --- Through the Market -------------------------------------------------------------------------


async def approve_bike(market: sim.Market, name: str = "seller") -> str:
    seller = market.user(name)
    await seller.say("sell this, at least $300", sim_photo())
    await seller.say("ask $360")
    return await seller.say("approve")


def sim_photo() -> str:
    from tests import fakes

    return fakes.photo_url("bike")


async def test_approved_items_are_hosted_and_sold_through_the_chat(store: Store) -> None:
    agentverse = FakeAgentverse()
    async with sim.running_market(store, hosting=agentverse, host_limit=1) as market:
        try:
            approved = await approve_bike(market)
            found = PROFILE.search(approved)
            assert found, approved
            address = found.group(1)
            (listing,) = store.active()
            assert (listing.host, listing.address) == (AGENTVERSE, address)
            assert address in agentverse.hosted

            # Past the limit, the next item runs in the Bureau.
            assert not PROFILE.search(await approve_bike(market, "second"))
            assert {listing.host for listing in store.active()} == {AGENTVERSE, BUREAU}

            # The buyer agent haggles with both, the hosted one included.
            haggled = await market.user("haggler").say("haggle for a hybrid bike, max $340")
            assert "haggled with 2 seller agent(s)" in haggled
            assert "didn't answer" not in haggled

            buyer = market.user("buyer")
            await buyer.say("hybrid bike")
            found = search(store.active(), "hybrid bike")  # the order the chat showed
            number = next(n for n, item in enumerate(found, 1) if item.address == address)
            assert "agreed to $360" in await buyer.say(f"offer $360 on {number}")
            assert "Deal!" in await buyer.say("accept")
            await asyncio.sleep(0.2)  # the hosted agent's ItemSold report
            hosted = store.get(listing.item_id)
            assert hosted is not None and hosted.status == SOLD
            (deal,) = store.deals()
            assert deal.item_id == listing.item_id and deal.price_cents == 36000
        finally:
            await agentverse.stop_all()


async def test_only_a_listings_own_hosted_agent_can_report_its_sale(store: Store) -> None:
    agentverse = FakeAgentverse()
    async with sim.running_market(store, hosting=agentverse, host_limit=1) as market:
        try:
            await approve_bike(market)
            (listing,) = store.active()
            mallory = Peer("mallory")
            await mallory.send(
                market.address,
                ItemSold(
                    item_id=listing.item_id,
                    deal_id="deal-fake",
                    buyer="chat:x",
                    price_cents=100,
                ),
            )
            await asyncio.sleep(0.2)
            assert store.deals() == [] and store.active() == [listing]
        finally:
            await agentverse.stop_all()


async def test_a_hosting_failure_falls_back_to_the_bureau(store: Store) -> None:
    agentverse = FakeAgentverse()
    agentverse.fail_start = True
    async with sim.running_market(store, hosting=agentverse, host_limit=3) as market:
        approved = await approve_bike(market)
        assert not PROFILE.search(approved) and "is for sale" in approved
        (listing,) = store.active()
        assert listing.host == BUREAU and agentverse.hosted == {}
