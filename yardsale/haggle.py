"""Haggling rules for both sides: the item agent selling and the buyer agent buying.

Neither side reveals its limit:

- The item agent agrees to any offer at or above the seller's floor, like eBay's auto-accept
  price. Below the floor it counters, coming down a little each round, but its counters stop
  short of the floor by a private gap, so no counter ever shows where the floor is. An offer under
  half the asking price is a lowball: the agent doesn't come down for it at all.
- The buyer agent opens at about two thirds of the asking price and meets each counter halfway,
  never going above the shopper's max. Its last offer is the max itself, if it gets that far.
  It stops when the seller agrees, when a counter is close enough to take, or when the seller
  won't come down to the max.

An agreement isn't a sale. The buyer closes it with AcceptCounter, so a buyer agent can haggle
with several items at once and buy only the best one. Once an item agent agrees to a price with
a buyer, that buyer's lower offers get the same price back, so agreements can't be used to probe
downward for the floor.

This module uses only the standard library: agents hosted on Agentverse run a copy of it
(hosting.py), so the rules are the same wherever an item agent runs. Prices are integer cents.
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass, replace
from fractions import Fraction

LOWBALL = Fraction(1, 2)  # offers under half the asking price don't move the item agent
KEEP_PER_ROUND = Fraction(3, 5)  # each round the counter keeps 60% of the distance left to go
GAP_MIN, GAP_SPREAD = Fraction(3, 20), Fraction(1, 5)  # the private gap: 15-35% of the spread
OPENING = Fraction(13, 20)  # the buyer agent opens at 65% of the asking price
MEET = Fraction(1, 2)  # and meets each counter halfway
MAX_ROUNDS = 6  # offers the buyer agent makes to one item before giving up


def step(asking_cents: int) -> int:
    """Price steps a person would use: whole dollars, or quarters under $20."""
    return 100 if asking_cents >= 2000 else 25


def round_up(cents: int | Fraction, unit: int) -> int:
    return int(-(-Fraction(cents) // unit)) * unit


def round_down(cents: int | Fraction, unit: int) -> int:
    return int(Fraction(cents) // unit) * unit


def private_gap(asking_cents: int, floor_cents: int, secret: str) -> int:
    """How far above the floor this item's counters stop: 15-35% of the asking-to-floor spread,
    at least one cent. The share comes from a secret, so buyers can't work the floor out from
    the counters even knowing these rules."""
    spread = asking_cents - floor_cents
    if spread <= 0:
        return 0
    digest = hmac.new(secret.encode(), b"yardsale-haggle-gap", hashlib.sha256).digest()
    share = GAP_MIN + GAP_SPREAD * Fraction(int.from_bytes(digest[:4], "big"), 2**32)
    return max(1, round_up(spread * share, 1))


@dataclass(frozen=True)
class SellerState:
    """One buyer's haggle with one item, as the item agent remembers it."""

    rounds: int = 0  # serious offers below the floor so far (lowballs don't count)
    price_cents: int | None = None  # the item agent's latest counter or agreed price
    agreed: bool = False


@dataclass(frozen=True)
class SellerMove:
    price_cents: int
    agreed: bool  # True: the item agent takes the buyer's offer at this price
    note: str
    state: SellerState


def counter_cents(asking_cents: int, floor_cents: int, gap_cents: int, rounds: int) -> int:
    """The item agent's counter after this many serious rounds. Always above the floor (unless
    the floor is the asking price), never above the asking price."""
    if floor_cents >= asking_cents:
        return asking_cents
    lowest = floor_cents + max(1, gap_cents)
    if lowest >= asking_cents:
        return asking_cents
    given = (asking_cents - lowest) * (1 - KEEP_PER_ROUND ** max(0, rounds))
    return min(asking_cents, max(lowest, round_up(asking_cents - given, step(asking_cents))))


def seller_move(
    asking_cents: int, floor_cents: int, gap_cents: int, offer_cents: int, state: SellerState
) -> SellerMove:
    """The item agent's answer to one offer: agree, or counter."""
    if state.agreed and state.price_cents is not None:
        return SellerMove(
            state.price_cents,
            True,
            f"We already agreed on {usd(state.price_cents)}. Accept it to buy.",
            state,
        )
    if offer_cents >= floor_cents:
        # Never more than the asking price or the agent's own latest counter.
        price = min(offer_cents, asking_cents, state.price_cents or asking_cents)
        return SellerMove(
            price,
            True,
            "Deal at that price. Accept it to buy.",
            replace(state, agreed=True, price_cents=price),
        )
    lowball = offer_cents < asking_cents * LOWBALL
    rounds = state.rounds if lowball else state.rounds + 1
    price = counter_cents(asking_cents, floor_cents, gap_cents, rounds)
    if state.price_cents is not None:
        price = min(price, state.price_cents)  # counters never go back up
    if lowball:
        note = f"That's well under what it's worth. I'm at {usd(price)}."
    else:
        note = f"I can come down to {usd(price)}."
    return SellerMove(price, False, note, SellerState(rounds=rounds, price_cents=price))


# --- The buyer agent -------------------------------------------------------------------------


@dataclass(frozen=True)
class BuyerMove:
    kind: str  # "offer": make this offer; "take": the seller's price is good, stop; "stop": no deal
    cents: int


def opening_offer(asking_cents: int, max_cents: int) -> int:
    return max(1, min(max_cents, round_down(asking_cents * OPENING, step(asking_cents))))


def buyer_move(
    asking_cents: int, max_cents: int, last_offer_cents: int, seller_cents: int, rounds: int
) -> BuyerMove:
    """The buyer agent's next step after the item agent countered `last_offer_cents` with
    `seller_cents`. Never offers above `max_cents`."""
    unit = step(asking_cents)
    if seller_cents <= max_cents and seller_cents - last_offer_cents <= unit:
        return BuyerMove("take", seller_cents)  # close enough: take their price
    if rounds >= MAX_ROUNDS:
        return BuyerMove("take" if seller_cents <= max_cents else "stop", seller_cents)
    halfway = last_offer_cents + (seller_cents - last_offer_cents) * MEET
    offer = min(max_cents, round_down(halfway, unit))
    if rounds == MAX_ROUNDS - 1 and seller_cents > max_cents:
        offer = max_cents  # the last offer: everything the shopper allowed
    if offer >= seller_cents:  # halfway rounded up to their price
        return BuyerMove("take", seller_cents)
    if offer <= last_offer_cents:
        if last_offer_cents >= max_cents:
            return BuyerMove("stop", seller_cents)  # already at the max and they won't meet it
        offer = min(max_cents, last_offer_cents + unit)
    return BuyerMove("offer", offer)


def usd(cents: int) -> str:
    """$340 for whole dollars, $12.50 otherwise (the same as pricing.usd)."""
    return f"${cents // 100:,}" if cents % 100 == 0 else f"${cents // 100:,}.{cents % 100:02d}"
