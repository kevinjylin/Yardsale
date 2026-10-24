"""The whole product in one process, with no keys, no network, and no cost:

    uv run python -m yardsale.demo
    uv run python -m yardsale.demo --verbose   # with the agents' logs

The agents, messages, prices, and database are the real ones. Claude, Apify, ASI:One, and photo
downloads are the fakes in fakes.py, and chat users stand in for ASI:One. Item agent addresses
are real uAgents addresses from a throwaway master seed, created on this machine only and never
registered anywhere.

1. A seller sends a bike photo: "sell this, at least $300, small scratch on the top tube".
2. They raise the asking price, then approve. The item agent's address appears.
3. Another seller lists an espresso machine.
4. Two buyers find the bike and lowball it. Both get counter-offers. One accepts; the other is
   too late, and the bike drops out of search.
5. The server restarts: everything is rebuilt from the database. The espresso machine's agent
   comes back at the same address, and two buyers offer the asking price at once. One gets it.
6. Failures: a mug with no close eBay sales (the seller prices it), an eBay outage, and a photo
   link that won't download.

Exits 0 when every check passes.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import re
import secrets
import sys
import tempfile
from pathlib import Path

from yardsale import bureau_server, fakes, offline, sim
from yardsale.store import SOLD, Store

ADDRESS = re.compile(r"agent1q[0-9a-z]{50,}")


class Demo:
    def __init__(self) -> None:
        self.results: list[tuple[str, bool]] = []

    def check(self, label: str, ok: bool) -> None:
        self.results.append((label, ok))
        print(f"   [{'PASS' if ok else 'FAIL'}] {label}")

    @staticmethod
    def step(title: str) -> None:
        print(f"\n== {title}")

    @staticmethod
    async def say(user: sim.ChatUser, text: str = "", photo: str | None = None) -> str:
        shown = text + (f" [photo: {Path(photo).name}]" if photo else "")
        print(f"\n{user.name} > {shown}")
        reply = await user.say(text, photo)
        print("\n".join(f"  Yardsale: {line}" for line in reply.splitlines()))
        return reply


async def run(folder: Path) -> list[tuple[str, bool]]:
    demo = Demo()
    say, check, step = demo.say, demo.check, demo.step
    db = folder / "demo.sqlite3"

    store = Store(db)
    async with sim.running_market(store) as market:
        step("1. A seller sends a photo")
        seller = market.user("seller")
        reply = await say(
            seller,
            "sell this, at least $300, small scratch on the top tube",
            fakes.photo_url("bike"),
        )
        check(
            "the proposal uses the seller's lowest price", "Lowest price you'll take: $300" in reply
        )
        check("nothing is for sale before approval", not store.active())

        step("2. The seller edits the price and approves")
        reply = await say(seller, "ask $360")
        check("the new asking price is shown", "Asking price: $360 (your price" in reply)
        reply = await say(seller, "approve")
        found = ADDRESS.search(reply)
        bike_address = found.group(0) if found else ""
        check("approving shows the item agent's address", bool(bike_address))
        (bike,) = store.active()
        check("the listing is active at that address", bike.address == bike_address)

        step("3. Another seller lists an espresso machine")
        seller2 = market.user("seller-2")
        await say(seller2, "", fakes.photo_url("espresso"))
        reply = await say(seller2, "yes")
        check("the second listing is live", "is for sale at $270" in reply)
        espresso = next(item for item in store.active() if item.item_id != bike.item_id)

        step("4. Two buyers haggle over the bike")
        alice, bob, carol = market.user("alice"), market.user("bob"), market.user("carol")
        reply = await say(alice, "find me a cheap hybrid bike")
        check("search finds the bike", "Trek FX 2 Disc" in reply)
        details = await say(alice, "1")
        check(
            "buyers never see the floor price",
            "$300" not in reply + details and "lowest" not in (reply + details).lower(),
        )
        reply = await say(alice, "offer $250")
        check(
            "a lowball gets a counter halfway to the asking price", "countered with $305" in reply
        )
        await say(bob, "any trek bikes?")
        reply = await say(bob, "offer $220 on 1")
        check("a counter is never below the floor", "countered with $300" in reply)
        reply = await say(carol, "accept")
        check("a buyer with no counter-offer can't accept one", "no counter-offer" in reply)
        reply = await say(alice, "accept")
        check("accepting the counter closes the deal", "Deal!" in reply and "$305" in reply)
        check("the deal records no payment", "No payment has been taken" in reply)
        reply = await say(bob, "accept")
        check("the second buyer is too late", "no longer for sale" in reply)
        reply = await say(bob, "find me a bike")
        check("a sold item drops out of search", "Trek" not in reply)
        reply = await say(seller, "my listings")
        check(
            "the seller sees the sale", "Sold: Trek FX 2 Disc hybrid bike, medium for $305" in reply
        )
        seller2_session = seller2.session

    step("5. The server restarts")
    store.close()
    store = Store(db)
    async with sim.running_market(store) as market:
        restored = bureau_server.runtime().agents
        check(
            "the espresso machine's agent is back at the same address",
            espresso.item_id in restored and restored[espresso.item_id].address == espresso.address,
        )
        check("the sold bike's agent isn't restored", bike.item_id not in restored)
        seller2 = market.user("seller-2", session=seller2_session)
        reply = await say(seller2, "my listings")
        check(
            "the seller's conversation survives the restart",
            bool(espresso.address) and str(espresso.address) in reply,
        )
        dan, erin = market.user("dan"), market.user("erin")
        await say(dan, "espresso machine")
        await say(erin, "do you have an espresso machine?")
        print("\ndan and erin > offer $270 on 1   (at the same moment)")
        replies = await asyncio.gather(dan.say("offer $270 on 1"), erin.say("offer $270 on 1"))
        for name, text in zip(("dan", "erin"), replies, strict=True):
            print(f"  Yardsale to {name}: {text.splitlines()[0]}")
        deals = [text for text in replies if "Deal!" in text]
        check("two buyers at once: exactly one gets it", len(deals) == 1)
        sold = store.get(espresso.item_id)
        check("the item is sold once", sold is not None and sold.status == SOLD)

        step("6. Things that go wrong")
        seller3 = market.user("seller-3")
        reply = await say(seller3, "sell this", fakes.photo_url("mug"))
        check("no close eBay sales means no suggested price", "no recent eBay sales" in reply)
        reply = await say(seller3, "ask $18")
        check("the seller's own price makes a proposal", "Asking price: $18 (your price)" in reply)
        reply = await say(seller3, "approve")
        check("and it can go live", "is for sale at $18" in reply)
        reply = await say(seller3, "sell this too", fakes.photo_url("camera"))
        check("an eBay outage is explained", "Apify returned 503" in reply)
        reply = await say(seller3, "and this", "https://example.com/broken.jpg")
        check("a photo that won't download is explained", "couldn't download" in reply)
        active = [listing.title for listing in store.active()]
        check(
            "only approved, unsold items are for sale", active == ["Handmade speckled ceramic mug"]
        )
    store.close()
    return demo.results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--verbose", action="store_true", help="show the agents' logs")
    args = parser.parse_args()
    if not args.verbose:
        logging.disable(logging.WARNING)
    offline.stay_offline()
    # A throwaway master seed: these item agents exist for this run only.
    os.environ["YARDSALE_MASTER_SEED"] = secrets.token_urlsafe(32)
    with tempfile.TemporaryDirectory(prefix="yardsale-demo-") as folder:
        offline.DIRECTORY = Path(folder) / "local_directory"
        results = asyncio.run(run(Path(folder)))
    passed = sum(ok for _, ok in results)
    print(f"\nResult: {passed}/{len(results)} checks passed")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
