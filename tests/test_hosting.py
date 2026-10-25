"""Item agents hosted on Agentverse (hosting.py, hosted_item_template.py).

A stand-in for Agentverse's Hosting API runs each uploaded agent in this process, so the
generated code is the code under test: it must compile, speak the same protocol as the Bureau's
item agents, and haggle by the same rules.
"""

from __future__ import annotations

import asyncio
import io
import json
import re
from pathlib import Path
from typing import Any
from uuid import uuid4

import aiohttp
import pytest
import uagents
from aiohttp import web
from PIL import Image
from uagents import Agent, Model
from uagents_core.identity import Identity

from tests import sim
from tests.helpers import Peer, local_server
from yardsale import avatars, bureau_server, hosting, models
from yardsale.avatars import AvatarError, ItemPictures
from yardsale.hosting import HOSTED_MODELS, HostingClient, HostingError, host_item, item_agent_code
from yardsale.models import AcceptCounter, Counter, Deal, ItemSold, Offer, OfferRejected
from yardsale.search import search
from yardsale.store import AGENTVERSE, BUREAU, CANCELED, PROPOSED, SOLD, Listing, Store

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
        self.fail_delete = False  # as if Agentverse couldn't be reached
        self.handles: dict[str, str] = {}  # address -> handle
        self.avatars: dict[str, str] = {}  # address -> picture URL
        self.uploads: dict[str, bytes] = {}  # picture URL -> its bytes
        self.fail_handles = False

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
        if self.fail_delete:
            raise HostingError("Agentverse couldn't be reached (ClientConnectorError)")
        entry = self.hosted.pop(address)
        self.deleted.append(address)
        if entry["agent"] is not None:
            await sim.stop([entry["agent"]])

    async def agents(self) -> list[dict[str, Any]]:
        return [
            {"address": a, "name": e["name"], "avatar_url": self.avatars.get(a)}
            for a, e in self.hosted.items()
        ]

    async def free_handle(self, wanted: str) -> str | None:
        if self.fail_handles:
            raise HostingError("Agentverse GET /v2/almanac/handles returned 500")
        return f"{wanted[:18]}-2" if wanted in self.handles.values() else wanted

    async def update_profile(
        self, address: str, handle: str | None = None, avatar_url: str | None = None
    ) -> None:
        if handle is not None:
            self.handles[address] = handle
        if avatar_url is not None:
            self.avatars[address] = avatar_url

    async def set_hosted_avatar(self, address: str, avatar_url: str) -> None:
        assert address in self.hosted
        self.avatars[address] = avatar_url

    async def publish_picture(self, image: bytes, name: str) -> str:
        """Stands in for the public picture store (Apify in the server)."""
        url = f"https://pictures.example/{name}"
        self.uploads[url] = image
        return url

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


def test_item_handles_say_what_the_item_is() -> None:
    cases = {
        "Trek FX 3 hybrid bike, medium": "trek-fx-3-hybrid",
        "Trek FX 2 Disc hybrid bike, medium": "trek-fx-2-disc",
        # A version number is one word, so it's kept whole or left out.
        "Specialized Sirrus 2.0 hybrid bike (test listing)": "specialized-sirrus",
        "Sirrus 2.0": "sirrus-2-0",
        "Air Jordan 4 Retro 'Black Cat', size 10": "air-jordan-4-retro",
        "Crème brûlée torch": "creme-brulee-torch",
        "TV": "tv-item",
        "日本の時計": "yardsale-item",
        "Supercalifragilisticexpialidocious lamp": "supercalifragilistic",
    }
    for title, handle in cases.items():
        assert hosting.item_handle(title) == handle, title
        assert len(handle) <= hosting.HANDLE_LIMIT


def test_hosting_is_off_unless_a_limit_is_set(monkeypatch: pytest.MonkeyPatch) -> None:
    assert hosting.hosted_limit() == 0
    monkeypatch.setenv("YARDSALE_HOST_ITEMS", "5")
    assert hosting.hosted_limit() == 5
    monkeypatch.setenv("YARDSALE_HOST_ITEMS", "many")
    assert hosting.hosted_limit() == 0


# --- Through the Market -------------------------------------------------------------------------


async def list_bike(market: sim.Market, name: str = "seller") -> tuple[sim.ChatUser, str]:
    """A seller lists a bike: their conversation and the reply to "approve"."""
    seller = market.user(name)
    await seller.say("sell this, at least $300", sim_photo())
    await seller.say("ask $360")
    return seller, await seller.say("approve")


async def approve_bike(market: sim.Market, name: str = "seller") -> str:
    _, approved = await list_bike(market, name)
    return approved


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

            # Sold, so its agent is deleted from Agentverse.
            await asyncio.gather(*bureau_server.runtime().background)
            assert agentverse.deleted == [address] and address not in agentverse.hosted
            assert store.on_agentverse() == []
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


async def test_unlisting_a_hosted_item_deletes_its_agent(store: Store) -> None:
    agentverse = FakeAgentverse()
    async with sim.running_market(store, hosting=agentverse, host_limit=1) as market:
        try:
            seller, _ = await list_bike(market)
            (listing,) = store.active()
            assert "is being deleted" in await seller.say("unlist")
            await asyncio.gather(*bureau_server.runtime().background)
            assert agentverse.deleted == [listing.address] and agentverse.hosted == {}
            unlisted = store.get(listing.item_id)
            assert unlisted is not None and unlisted.status == CANCELED
            assert store.on_agentverse() == []
        finally:
            await agentverse.stop_all()


async def test_a_failed_delete_is_tried_again(store: Store) -> None:
    agentverse = FakeAgentverse()
    async with sim.running_market(store, hosting=agentverse, host_limit=1) as market:
        try:
            seller, _ = await list_bike(market)
            (listing,) = store.active()
            agentverse.fail_delete = True
            await seller.say("unlist")
            rt = bureau_server.runtime()
            await asyncio.gather(*rt.background)
            assert listing.address in agentverse.hosted
            assert [x.item_id for x in store.on_agentverse()] == [listing.item_id]

            # The undeleted agent still takes the only hosting slot.
            assert not PROFILE.search(await approve_bike(market, "second"))

            agentverse.fail_delete = False
            await bureau_server.delete_closed_hosted(rt)  # the next sweep
            assert agentverse.deleted == [listing.address] and store.on_agentverse() == []
            assert PROFILE.search(await approve_bike(market, "third"))
        finally:
            await agentverse.stop_all()


async def test_on_start_the_market_deletes_agents_of_closed_listings(store: Store) -> None:
    """Agents whose listing closed while the server was down, even with hosting off."""
    agentverse = FakeAgentverse()
    addresses = {}
    for item_id in ("sold", "canceled", "active"):
        store.add_listing(bike(item_id))
        addresses[item_id] = await agentverse.create("Trek FX 2", "", "")
        store.activate(item_id, "seller", addresses[item_id], host=AGENTVERSE)
    store.close_deal("sold", "chat:x", 36000)
    store.cancel("canceled", "seller")
    async with sim.running_market(store, hosting=agentverse, host_limit=0):
        for _ in range(100):
            if len(agentverse.deleted) == 2:
                break
            await asyncio.sleep(0.02)
    assert sorted(agentverse.deleted) == sorted([addresses["sold"], addresses["canceled"]])
    assert list(agentverse.hosted) == [addresses["active"]]
    assert [x.item_id for x in store.on_agentverse()] == ["active"]


def test_only_closed_listings_are_marked_deleted(store: Store) -> None:
    store.add_listing(bike())
    store.activate("bike", "seller", "agent1qbike", host=AGENTVERSE)
    store.agent_deleted("bike")
    assert [x.item_id for x in store.on_agentverse()] == ["bike"]
    store.cancel("bike", "seller")
    store.agent_deleted("bike")
    assert store.on_agentverse() == []


async def test_deleting_an_agent_thats_already_gone_succeeds() -> None:
    async def delete(request: web.Request) -> web.Response:
        gone = request.match_info["address"] == "agent1qgone"
        return web.Response(status=404 if gone else 500, text="no")

    app = web.Application()
    app.router.add_delete("/v1/hosting/agents/{address}", delete)
    async with local_server(app) as base:
        client = HostingClient("test-key", base)
        await client.delete("agent1qgone")
        with pytest.raises(HostingError) as failed:
            await client.delete("agent1qbroken")
        assert failed.value.status == 500
    with pytest.raises(HostingError) as unreachable:
        await client.delete("agent1qgone")  # the server has stopped
    assert unreachable.value.status is None


async def test_a_hosting_failure_falls_back_to_the_bureau(store: Store) -> None:
    agentverse = FakeAgentverse()
    agentverse.fail_start = True
    async with sim.running_market(store, hosting=agentverse, host_limit=3) as market:
        approved = await approve_bike(market)
        assert not PROFILE.search(approved) and "is for sale" in approved
        (listing,) = store.active()
        assert listing.host == BUREAU and agentverse.hosted == {}


# --- Handles and pictures ----------------------------------------------------------------------


def png(width: int = 1024, height: int = 1024) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (240, 200, 60)).save(buffer, "PNG")
    return buffer.getvalue()


async def test_hosted_item_agents_get_a_handle_and_a_picture(store: Store) -> None:
    agentverse = FakeAgentverse()
    prompts: list[str] = []

    async def draw(prompt: str) -> bytes:
        prompts.append(prompt)
        return png(1792, 1024)

    pictures = ItemPictures(draw, agentverse.publish_picture)
    async with sim.running_market(
        store, hosting=agentverse, host_limit=2, pictures=pictures
    ) as market:
        try:
            seller = market.user("seller")
            await seller.say("sell this, at least $300", sim_photo())
            await seller.say("ask $360")
            approved = await seller.say("approve")
            assert "live on Agentverse as @trek-fx-2-disc" in approved, approved
            (listing,) = store.active()
            assert listing.handle == "trek-fx-2-disc"
            assert agentverse.handles[listing.address or ""] == "trek-fx-2-disc"
            await asyncio.gather(*bureau_server.runtime().background)
            url = agentverse.avatars[listing.address or ""]
            with Image.open(io.BytesIO(agentverse.uploads[url])) as picture:
                assert (picture.format, picture.size) == ("JPEG", (512, 512))
            (prompt,) = prompts
            assert "hybrid bike" in prompt and "matte black" in prompt
            assert "Trek" not in prompt and "FX" not in prompt
            assert "$300" not in prompt and "300" not in prompt  # never the floor
            assert "(@trek-fx-2-disc)" in await seller.say("my listings")

            # Agentverse suggests another handle when one is taken.
            await approve_bike(market, "second")
            second = next(x for x in store.active() if x.item_id != listing.item_id)
            assert second.handle == "trek-fx-2-disc-2"
        finally:
            await agentverse.stop_all()


async def test_handles_and_pictures_are_extras(store: Store) -> None:
    """If Agentverse won't give a handle or ASI:One can't draw, the item is listed anyway."""
    agentverse = FakeAgentverse()
    agentverse.fail_handles = True

    async def draw(prompt: str) -> bytes:
        raise AvatarError("ASI:One returned 500")

    pictures = ItemPictures(draw, agentverse.publish_picture)
    async with sim.running_market(
        store, hosting=agentverse, host_limit=1, pictures=pictures
    ) as market:
        try:
            approved = await approve_bike(market)
            assert PROFILE.search(approved) and " as @" not in approved
            await asyncio.gather(*bureau_server.runtime().background)
            (listing,) = store.active()
            assert listing.host == AGENTVERSE and listing.handle is None
            assert agentverse.avatars == {}
        finally:
            await agentverse.stop_all()


async def test_without_item_pictures_nothing_is_drawn(store: Store) -> None:
    agentverse = FakeAgentverse()
    async with sim.running_market(store, hosting=agentverse, host_limit=1) as market:
        try:
            await approve_bike(market)
            assert bureau_server.runtime().background == set()
            assert agentverse.avatars == {} and len(agentverse.handles) == 1
        finally:
            await agentverse.stop_all()


async def test_the_command_pictures_hosted_items_and_redraws_by_handle(store: Store) -> None:
    agentverse = FakeAgentverse()
    drawn: list[str] = []

    async def draw(prompt: str) -> bytes:
        drawn.append(prompt)
        buffer = io.BytesIO()
        Image.new("RGB", (64, 64), (40 * len(drawn), 90, 60)).save(buffer, "PNG")
        return buffer.getvalue()  # a different picture each time

    pictures = ItemPictures(draw, agentverse.publish_picture)
    async with sim.running_market(store, hosting=agentverse, host_limit=1) as market:
        try:
            await approve_bike(market)
            (listing,) = store.active()
            handle = listing.handle or ""
            assert handle and agentverse.avatars == {}  # pictures were off

            assert await avatars.dress_hosted_items(agentverse, store, pictures) == set()
            first = agentverse.avatars[listing.address or ""]
            assert len(drawn) == 1
            await avatars.dress_hosted_items(agentverse, store, pictures)
            assert len(drawn) == 1  # it has one already

            found = await avatars.dress_hosted_items(
                agentverse, store, pictures, frozenset({handle, "no-such-item"})
            )
            assert found == {handle} and len(drawn) == 2
            assert agentverse.avatars[listing.address or ""] != first  # a new URL
        finally:
            await agentverse.stop_all()


# --- The market line ---------------------------------------------------------------------------

LINE = "similar ones sold for about $340 on eBay recently, and locals are asking $310 to $380"


def priced_bike() -> Listing:
    from yardsale.models import MarketView

    listing = bike()
    listing.market = MarketView(
        sold_count=6,
        sold_median_cents=34000,
        sold_low_cents=31000,
        sold_high_cents=37000,
        asking_count=5,
        asking_median_cents=34000,
        asking_low_cents=31000,
        asking_high_cents=38000,
        range_low_cents=34000,
        range_high_cents=34000,
        sources=[],
        line=LINE,
    )
    return listing


async def test_a_hosted_agent_cites_the_market_line_from_its_storage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("YARDSALE_LIMITS", json.dumps({"floor_cents": 30000, "gap_cents": 1500}))
    agent, _ = run_hosted(item_agent_code(SETTINGS), uuid4().hex, {"market_line": LINE})
    bob = Peer("bob", *BUYER_KINDS)
    try:
        low = await bob.ask(agent.address, Offer(item_id="bike", offer_cents=1000), Counter)
        assert low.note == f"That's well under what it's worth. I'm at $360. S{LINE[1:]}."
        counter = await bob.ask(agent.address, Offer(item_id="bike", offer_cents=25000), Counter)
        assert counter.note.startswith("I can come down to $") and LINE[1:] in counter.note
        assert "300" not in counter.note and "300" not in low.note
    finally:
        await sim.stop([agent, bob.agent])


async def test_host_item_stores_the_market_line_beside_the_limits() -> None:
    agentverse = FakeAgentverse()
    try:
        address = await host_item(
            agentverse, priced_bike(), 1500, "agent1qmarket", "agent1qshopper"
        )
        entry = agentverse.hosted[address]
        assert entry["storage"]["market_line"] == LINE
        assert f"Priced from the market: {LINE}." in entry["readme"]
        assert "$300" not in entry["readme"] and "30000" not in entry["code"]
        plain = await host_item(agentverse, bike("bike-2"), 1500, "agent1qmarket", "agent1qshopper")
        assert "market_line" not in agentverse.hosted[plain]["storage"]
    finally:
        await agentverse.stop_all()
