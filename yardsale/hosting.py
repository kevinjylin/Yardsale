"""Item agents hosted on Agentverse, created through Agentverse's Hosting API.

When a seller approves a listing and hosting is on, the Market creates the item's agent on
Agentverse instead of in its own Bureau: it creates the agent with a profile (title, asking
price, how to make an offer), gives it the floor and private gap as a secret (and in its
storage, in case the secret isn't readable as an environment variable), uploads its code, and
starts it. It also gets a handle made from the item's title (@trek-fx-3-hybrid) and, in the
background, a picture of the item (avatars.py). The seller gets the agent's Agentverse profile
link. When the listing is sold or canceled, the Market deletes the agent; a delete that fails
(Agentverse unreachable, say) is tried again every minute and when the server starts.

The code is hosted_item_template.py with haggle.py and the message classes from models.py pasted
in, so a hosted agent haggles exactly like one in the Bureau. Hosted agents may only import
Agentverse's allowed packages; this code needs uagents, uagents-core, and the standard library.

Hosting is on in live mode when AGENTVERSE_API_KEY is set and YARDSALE_HOST_ITEMS is above 0.
That number caps how many item agents are on Agentverse at once, counting closed listings' agents
not deleted yet (Agentverse limits agents per account, reportedly 8 free and 25 on Premium, and
Yardsale's six mailbox agents count too).
Past the cap, or if hosting fails, the item agent runs in the Bureau and registers on the
Almanac as before.

    uv run python -m yardsale.hosting --list       # Yardsale's hosted agents on Agentverse
    uv run python -m yardsale.hosting --cleanup    # delete the ones whose listing isn't active

The server does what --cleanup does by itself; the command is for agents left behind while it
wasn't running.
"""

from __future__ import annotations

import argparse
import ast
import asyncio
import inspect
import json
import logging
import os
import re
import sys
import unicodedata
from pathlib import Path
from typing import Any
from urllib.parse import quote

import aiohttp

from yardsale import config, haggle
from yardsale.models import AcceptCounter, Counter, Deal, ItemSold, Offer, OfferRejected
from yardsale.pricing import usd
from yardsale.store import ACTIVE, Listing, Store

API = "https://agentverse.ai"
TEMPLATE = Path(__file__).resolve().parent / "hosted_item_template.py"
HOSTED_CODE_MARK = "# --- hosted code ---\n"
HOSTED_MODELS = (Offer, Counter, AcceptCounter, Deal, OfferRejected, ItemSold)
NAME_PREFIX = "Yardsale item: "  # how hosted agents were named before October 24
NAME_LIMIT = 32  # Agentverse profiles show only the first 32 characters of a name
HANDLE_LIMIT = 20  # Agentverse handles are at most 20 characters
TIMEOUT = aiohttp.ClientTimeout(total=30)
BADGES = (
    "![tag:innovationlab](https://img.shields.io/badge/innovationlab-3D8BD3)\n"
    "![tag:hackathon](https://img.shields.io/badge/hackathon-5F43F1)\n"
)

log = logging.getLogger("yardsale.hosting")


class HostingError(RuntimeError):
    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status  # Agentverse's HTTP status, or None if it wasn't an HTTP error


def hosted_limit() -> int:
    """YARDSALE_HOST_ITEMS: how many item agents may be on Agentverse at once (0 = none)."""
    raw = os.getenv("YARDSALE_HOST_ITEMS", "").strip()
    try:
        return max(0, int(raw)) if raw else 0
    except ValueError:
        return 0


def profile_url(address: str) -> str:
    return f"https://agentverse.ai/agents/details/{address}/profile"


def agent_name(title: str) -> str:
    """The item's title as its agent's name, cut at a word to fit what Agentverse shows."""
    title = " ".join(title.split())
    if len(title) <= NAME_LIMIT:
        return title
    head = title[: NAME_LIMIT + 1]
    cut = head.rsplit(" ", 1)[0].rstrip(" ,;:-(") if " " in head else ""
    return cut or title[:NAME_LIMIT]


def item_handle(title: str) -> str:
    """A handle that says what the item is: the title before any comma or parentheses, as
    lowercase words joined by hyphens, cut at a word to fit Agentverse's 20 characters.
    "Trek FX 3 hybrid bike, medium" -> "trek-fx-3-hybrid"."""
    name = re.split(r"[,(]", title, maxsplit=1)[0]
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    # "2.0" stays one word, so a cut never leaves half a version number.
    words = [w.replace(".", "-") for w in re.findall(r"[a-z0-9]+(?:\.[0-9]+)*", ascii_name)]
    handle = ""
    for word in words:
        longer = f"{handle}-{word}" if handle else word
        if len(longer) > HANDLE_LIMIT:
            break
        handle = longer
    if not handle and words:
        handle = words[0][:HANDLE_LIMIT].strip("-")
    if len(handle) < 3:
        handle = f"{handle}-item" if handle else "yardsale-item"
    return handle


# --- The hosted agent's code and profile -------------------------------------------------------


def _haggle_source() -> str:
    """haggle.py without its docstring and imports (the hosted code imports what it needs)."""
    tree = ast.parse(inspect.getsource(haggle))
    body = [
        node
        for node in tree.body
        if not isinstance(node, ast.Import | ast.ImportFrom)
        and not (isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant))
    ]
    return ast.unparse(ast.Module(body=body, type_ignores=[]))


def item_agent_code(settings: dict[str, Any]) -> str:
    """The Python source of one hosted item agent. `settings` holds item_id, title,
    asking_cents, market, and shopper; never the floor."""
    template = TEMPLATE.read_text()
    if HOSTED_CODE_MARK not in template:
        raise HostingError("hosted_item_template.py lost its hosted code line")
    agent_code = template.split(HOSTED_CODE_MARK, 1)[1]
    models = "\n\n".join(inspect.getsource(model) for model in HOSTED_MODELS)
    return "\n\n".join(
        [
            f'"""Yardsale item agent for {settings["title"]!r}, managed by Yardsale. Generated by'
            ' yardsale/hosting.py."""',
            # No "from __future__ import annotations": annotations are evaluated as the code
            # runs, so the dataclasses and messages work however Agentverse loads the file.
            "import hashlib\nimport hmac\nimport json\nimport os\nimport time\n"
            "from dataclasses import dataclass, replace\nfrom fractions import Fraction\n"
            "from typing import Any\nfrom uuid import uuid4\n\n"
            "from uagents import Agent, Context, Model, Protocol\n"
            "from uagents_core.protocol import ProtocolSpecification",
            "# --- Haggling rules (yardsale/haggle.py) ---\n" + _haggle_source(),
            "# --- Messages (yardsale/models.py) ---\n" + models,
            f"CONFIG: dict[str, Any] = json.loads({json.dumps(json.dumps(settings))})",
            agent_code,
        ]
    )


def item_readme(listing: Listing) -> str:
    details = ""
    if listing.item is not None:
        parts = [f"Condition: {listing.item.condition}."]
        if listing.item.details:
            parts.append("Details: " + "; ".join(listing.item.details) + ".")
        details = " ".join(parts) + "\n\n"
    return (
        f"# {listing.title}\n\n{BADGES}\n"
        f"For sale on **Yardsale** at **{usd(listing.asking_cents)}**. {details}"
        "This agent is the item itself: it holds its seller's private lowest price and haggles "
        "with buyers. It agrees to a fair offer and counters a low one, coming down a little "
        "each round, but never says what its lowest price is.\n\n"
        "## How to buy\n\n"
        'Ask Yardsale in ASI:One (for example "find me a ..."), or have your agent speak the '
        "**YardsaleHaggle 1.0.0** protocol with this agent: send `Offer` (`item_id`, "
        "`offer_cents`); the answer is a `Counter` (`agreed` is true when it takes your offer). "
        "Send `AcceptCounter` with that exact price to buy. Yardsale then sends a payment "
        "request through the Agent Payment Protocol.\n\n"
        f"Item ID: `{listing.item_id}`. Prices are in US cents.\n"
    )


# --- The Hosting API ---------------------------------------------------------------------------


class HostingClient:
    """Agentverse's APIs for the agents on Yardsale's account, authenticated with an Agentverse
    API key: the Hosting API (v1) for item agents, plus handles and profile pictures (v2) for
    any of its agents."""

    def __init__(self, api_key: str, base_url: str = API) -> None:
        self._key = api_key
        self.base_url = base_url.rstrip("/")

    async def _call(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        headers = {"Authorization": f"Bearer {self._key}"}
        try:
            async with (
                aiohttp.ClientSession(timeout=TIMEOUT) as session,
                session.request(
                    method, f"{self.base_url}{path}", json=body, headers=headers
                ) as response,
            ):
                text = await response.text()
                if response.status >= 400:
                    raise HostingError(
                        f"Agentverse {method} {path.split('/agent1')[0]} returned "
                        f"{response.status}: {text[:200]}",
                        response.status,
                    )
                return json.loads(text) if text.strip() else None
        except (aiohttp.ClientError, TimeoutError) as ex:
            raise HostingError(f"Agentverse couldn't be reached ({type(ex).__name__})") from ex

    async def create(self, name: str, readme: str, short_description: str) -> str:
        data = await self._call(
            "POST",
            "/v1/hosting/agents",
            {"name": name, "readme": readme, "short_description": short_description},
        )
        address = data.get("address") if isinstance(data, dict) else None
        if not isinstance(address, str) or not address.startswith("agent1"):
            raise HostingError("Agentverse didn't return the new agent's address")
        return address

    async def upload_code(self, address: str, code: str) -> None:
        files = [{"language": "python", "name": "agent.py", "value": code}]
        await self._call("PUT", f"/v1/hosting/agents/{address}/code", {"code": json.dumps(files)})

    async def set_secret(self, address: str, name: str, secret: str) -> None:
        await self._call(
            "POST", "/v1/hosting/secrets", {"address": address, "name": name, "secret": secret}
        )

    async def set_storage(self, address: str, key: str, value: str) -> None:
        await self._call("PUT", f"/v1/hosting/agents/{address}/storage/{key}", {"value": value})

    async def start(self, address: str) -> None:
        await self._call("POST", f"/v1/hosting/agents/{address}/start")

    async def delete(self, address: str) -> None:
        """Delete a hosted agent. One that's already gone (404) counts as deleted."""
        try:
            await self._call("DELETE", f"/v1/hosting/agents/{address}")
        except HostingError as ex:
            if ex.status != 404:
                raise

    async def agents(self) -> list[dict[str, Any]]:
        data = await self._call("GET", "/v1/hosting/agents")
        items = data.get("items", data) if isinstance(data, dict) else data
        return [a for a in items or [] if isinstance(a, dict)]

    # Handles and profile pictures. The v2 calls are the ones Agentverse's own web app makes.

    async def free_handle(self, wanted: str) -> str | None:
        """`wanted` if no agent has it yet, else Agentverse's suggestion (None if it has none)."""
        data = await self._call("GET", f"/v2/almanac/handles/{quote(wanted, safe='')}/available")
        if not isinstance(data, dict):
            return None
        handle = wanted if data.get("available") else data.get("alternative")
        return handle if isinstance(handle, str) and 0 < len(handle) <= HANDLE_LIMIT else None

    async def agent(self, address: str) -> dict[str, Any]:
        """An agent's name, handle, and profile, as Agentverse's web app reads them."""
        current = await self._call("GET", f"/v2/agents/{address}")
        if not isinstance(current, dict):
            raise HostingError("Agentverse didn't return the agent's profile")
        return current

    async def agent_profile(self, address: str) -> dict[str, Any]:
        """The agent's profile: description, readme, avatar_url, and so on."""
        profile = (await self.agent(address)).get("profile")
        return profile if isinstance(profile, dict) else {}

    async def update_profile(
        self, address: str, handle: str | None = None, avatar_url: str | None = None
    ) -> None:
        """Set an agent's handle or picture, keeping the rest of its profile."""
        current = await self.agent(address)
        profile = {k: v for k, v in (current.get("profile") or {}).items() if v is not None}
        if avatar_url is not None:
            profile["avatar_url"] = avatar_url
        body: dict[str, Any] = {"address": address, "profile": profile}
        if current.get("name"):
            body["name"] = current["name"]
        if handle is not None:
            body["handle"] = handle
        await self._call("PUT", f"/v2/agents/{address}", body)

    async def set_hosted_avatar(self, address: str, avatar_url: str) -> None:
        await self._call("PUT", f"/v1/hosting/agents/{address}", {"avatar_url": avatar_url})


async def host_item(
    client: HostingClient, listing: Listing, gap_cents: int, market: str, shopper: str
) -> str:
    """Create, configure, and start the listing's agent on Agentverse. Returns its address.
    Deletes the half-made agent if any step fails."""
    address = await client.create(
        agent_name(listing.title),
        item_readme(listing),
        f"{listing.title}, for sale on Yardsale at {usd(listing.asking_cents)}. Make an offer."[
            :200
        ],
    )
    try:
        limits = json.dumps({"floor_cents": listing.floor_cents, "gap_cents": gap_cents})
        await client.set_secret(address, "YARDSALE_LIMITS", limits)
        await client.set_storage(address, "limits", limits)
        await client.upload_code(
            address,
            item_agent_code(
                {
                    "item_id": listing.item_id,
                    "title": listing.title,
                    "asking_cents": listing.asking_cents,
                    "market": market,
                    "shopper": shopper,
                }
            ),
        )
        await client.start(address)
    except HostingError:
        try:
            await client.delete(address)
        except HostingError:
            log.warning(f"Couldn't delete the half-made hosted agent {address}")
        raise
    return address


async def claim_handle(client: HostingClient, address: str, title: str) -> str | None:
    """Give a hosted item agent a handle made from its title, or Agentverse's suggestion if
    that's taken. None if Agentverse said no; the agent works the same without one."""
    try:
        handle = await client.free_handle(item_handle(title))
        if handle is None:
            log.warning(f"No free handle for {address}")
            return None
        await client.update_profile(address, handle=handle)
    except HostingError as ex:
        log.warning(f"Couldn't give {address} a handle: {ex}")
        return None
    return handle


# --- Command line -----------------------------------------------------------------------------


async def _main(cleanup: bool) -> int:
    api_key = os.getenv("AGENTVERSE_API_KEY", "").strip()
    if not api_key:
        print("AGENTVERSE_API_KEY is not set.")
        return 1
    client = HostingClient(api_key)
    store = Store(config.db_path("live"))
    # Only agents Yardsale made: ones its listings point at (or named the old way). Any other
    # hosted agents on the account are left alone.
    ours = {listing.address: listing for listing in store.hosted()}
    hosted = [
        a
        for a in await client.agents()
        if a.get("address") in ours or str(a.get("name", "")).startswith(NAME_PREFIX)
    ]
    active = {listing.address for listing in store.hosted() if listing.status == ACTIVE}
    for agent in hosted:
        address = str(agent.get("address", ""))
        keep = address in active
        print(f"{'active ' if keep else 'stale  '} {address}  {agent.get('name')}")
        if cleanup and not keep:
            await client.delete(address)
            if address in ours:
                store.agent_deleted(ours[address].item_id)
            print("         deleted")
    if not hosted:
        print("No Yardsale item agents are hosted on Agentverse.")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Yardsale's item agents on Agentverse")
    parser.add_argument("--list", action="store_true", help="list hosted item agents")
    parser.add_argument(
        "--cleanup", action="store_true", help="delete hosted agents whose listing isn't active"
    )
    args = parser.parse_args()
    sys.exit(asyncio.run(_main(args.cleanup)))
