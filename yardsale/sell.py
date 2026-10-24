"""Sell something from a photo. Start bureau_server first, then:

    uv run python -m yardsale.sell photo.jpg
    uv run python -m yardsale.sell photo.heic --note "size M, worn twice" --floor 40
    uv run python -m yardsale.sell photo.jpg --offline

The photo is resized and sent to the Market. The Identifier, Researcher, and Appraiser work out
what it is and what it's worth, then the Market spawns a live agent for it. This prints each
step, the eBay sales behind the price, and the new item agent's address. Exits 0 once listed.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from uagents import Agent, Context, Protocol
from uagents.registration import AlmanacApiRegistrationPolicy
from uagents.resolver import Resolver
from uagents_core.registration import AgentRegistrationPolicy

from yardsale.common import (
    MARKET_FILE,
    SELLER_PORT,
    almanac_endpoints,
    derive_seed,
    dollars,
    endpoint,
    read_json,
    wait_until,
)
from yardsale.models import Appraised, Listed, ListingRejected, ListingUpdate, ListPhoto
from yardsale.offline import LocalDirectoryResolver, NoRegistration, publish_locally
from yardsale.photos import PhotoError, load_photo

log = logging.getLogger("yardsale.sell")


def print_appraisal(msg: Appraised) -> None:
    item, appraisal = msg.item, msg.appraisal
    print(f"\nItem: {item.title}")
    maker = " ".join(part for part in (item.brand, item.model) if part) or "no brand or model seen"
    print(f"  {maker} | {item.category} | condition: {item.condition} | sure: {item.confidence}")
    print(f"\neBay: checked {appraisal.comps_considered} sales. Based on {appraisal.basis}.")
    for match in appraisal.matches[:10]:
        kind = "same" if match.same_product else f"~{match.similarity:.0%}"
        best_offer = " (best offer)" if match.comp.best_offer else ""
        print(
            f"  {dollars(match.comp.price_cents):>9}{best_offer}  {kind:>5}  "
            f"{match.comp.title[:70]}\n{'':>18}{match.comp.url}"
        )
    if appraisal.asking_cents is not None and appraisal.floor_cents is not None:
        spread = ""
        if appraisal.low_cents is not None and appraisal.high_cents is not None:
            spread = (
                f", most sold for {dollars(appraisal.low_cents)}-{dollars(appraisal.high_cents)}"
            )
        print(
            f"\nSuggested: ask {dollars(appraisal.asking_cents)}, "
            f"lowest {dollars(appraisal.floor_cents)}{spread} (confidence: {appraisal.confidence})"
        )


async def main(args: argparse.Namespace) -> int:
    if not MARKET_FILE.exists():
        print("Start bureau_server first; it writes the Market address.", file=sys.stderr)
        return 2
    market_info = read_json(MARKET_FILE)
    if bool(market_info["offline"]) != args.offline:
        print("bureau_server and sell must both use --offline, or neither.", file=sys.stderr)
        return 2
    market: str = market_info["address"]
    try:
        image_b64, media_type = load_photo(args.photo)
    except (OSError, PhotoError) as ex:
        print(f"Can't use {args.photo}: {ex}", file=sys.stderr)
        return 2
    print(f"Photo ready ({len(image_b64) * 3 // 4 // 1024} KB). Sending it to the Market...")

    done = asyncio.Event()
    exit_code = 1
    proto = Protocol(name="YardsaleSeller", version="0.1.0")

    @proto.on_message(ListingUpdate)
    async def on_update(ctx: Context, sender: str, msg: ListingUpdate) -> None:
        print(f"- {msg.text}")

    @proto.on_message(Appraised)
    async def on_appraised(ctx: Context, sender: str, msg: Appraised) -> None:
        print_appraisal(msg)

    @proto.on_message(Listed)
    async def on_listed(ctx: Context, sender: str, msg: Listed) -> None:
        nonlocal exit_code
        print(
            f"\nListed '{msg.title}' at {dollars(msg.asking_cents)}.\n"
            f"Its agent is live at {msg.item_address} "
            f"(on the Almanac: {'yes' if msg.almanac_registered else 'no'})"
        )
        exit_code = 0
        done.set()

    @proto.on_message(ListingRejected)
    async def on_rejected(ctx: Context, sender: str, msg: ListingRejected) -> None:
        print(f"\nNot listed: {msg.reason}")
        done.set()

    almanac_policy = None if args.offline else AlmanacApiRegistrationPolicy(logger=log)
    policy: AgentRegistrationPolicy = almanac_policy or NoRegistration()
    resolver: Resolver | None = LocalDirectoryResolver() if args.offline else None
    agent = Agent(
        name="yardsale-seller",
        seed=derive_seed(f"seller:{args.seller}"),
        port=SELLER_PORT,
        endpoint=[endpoint(SELLER_PORT)],
        resolve=resolver,
        registration_policy=policy,
        loop=asyncio.get_running_loop(),
        publish_agent_details=False,
        enable_agent_inspector=False,
    )
    agent.include(proto)
    if args.offline:
        publish_locally(agent.address, endpoint(SELLER_PORT))

    @agent.on_event("startup")
    async def send_photo(ctx: Context) -> None:
        if not args.offline:
            # Replies come back through the Almanac, so register first and check the Market.
            async def registered() -> bool:
                assert almanac_policy is not None
                return almanac_policy.last_successful_registration is not None

            async def market_found() -> bool:
                return bool(await almanac_endpoints(market))

            await wait_until(registered, "this seller's Almanac registration")
            await wait_until(market_found, "the Market on the Almanac")
        await ctx.send(
            market,
            ListPhoto(
                listing_ref=f"photo-{uuid4().hex[:8]}",
                seller_id=args.seller,
                image_b64=image_b64,
                media_type=media_type,
                note=args.note,
                asking_cents=_cents(args.ask),
                floor_cents=_cents(args.floor),
            ),
        )

    # setup() + start_server() rather than run_async(), which cancels every task in the loop
    # when it stops, including this function.
    agent.setup()
    server = asyncio.get_running_loop().create_task(agent.start_server())
    try:
        await asyncio.wait_for(done.wait(), args.timeout)
    except TimeoutError:
        print(f"\nNo answer from the Market after {args.timeout:.0f}s.", file=sys.stderr)
    server.cancel()
    return exit_code


def _cents(amount: str | None) -> int | None:
    return None if amount is None else int(Decimal(amount) * 100)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("photo", type=Path)
    parser.add_argument("--note", default="", help="anything the photo doesn't show")
    parser.add_argument("--ask", help="your asking price in dollars (default: from eBay sales)")
    parser.add_argument("--floor", help="your lowest price in dollars (default: from eBay sales)")
    parser.add_argument("--seller", default="me", help="seller ID stored with the listing")
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--timeout", type=float, default=600.0)
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s [%(name)s] %(message)s")
    sys.exit(asyncio.run(main(args)))
