"""The haggling rules on their own (haggle.py): what each side may and may not do."""

from __future__ import annotations

import random

import pytest

from yardsale import haggle
from yardsale.haggle import SellerState, buyer_move, opening_offer, private_gap, seller_move


def run(asking: int, floor: int, gap: int, max_cents: int) -> tuple[str, int | None, list[int]]:
    """One buyer agent against one item agent. Returns how it ended, the price, and the
    item agent's prices round by round."""
    state = SellerState()
    offer = opening_offer(asking, max_cents)
    prices: list[int] = []
    for n in range(1, haggle.MAX_ROUNDS + 1):
        assert 0 < offer <= max_cents
        move = seller_move(asking, floor, gap, offer, state)
        state = move.state
        prices.append(move.price_cents)
        if move.agreed:
            return "agreed", move.price_cents, prices
        step = buyer_move(asking, max_cents, offer, move.price_cents, n)
        if step.kind != "offer":
            return step.kind, step.cents, prices
        offer = step.cents
    raise AssertionError("the buyer agent should stop by MAX_ROUNDS")


def test_many_random_haggles_keep_both_limits() -> None:
    rng = random.Random(7)
    for _ in range(3000):
        asking = rng.choice([rng.randint(5, 2000) * 100, rng.randint(4, 79) * 25])
        floor = rng.randint(asking // 2, asking)
        gap = private_gap(asking, floor, str(rng.random()))
        max_cents = rng.randint(asking // 3, asking * 6 // 5)
        kind, price, prices = run(asking, floor, gap, max_cents)
        # The item agent's counters never show the floor, and never go back up.
        counters = prices[:-1] if kind == "agreed" else prices
        assert all(p > floor for p in counters) or floor >= asking
        assert prices == sorted(prices, reverse=True)
        if kind in ("agreed", "take"):
            assert price is not None and price <= max_cents and price <= asking
        if kind == "agreed":
            assert price is not None and price >= floor
        # A deal is found whenever one is possible.
        if max_cents >= floor:
            assert kind in ("agreed", "take"), (asking, floor, gap, max_cents)


def test_the_gap_is_private_and_in_range() -> None:
    gaps = {private_gap(36000, 30000, f"secret-{n}") for n in range(50)}
    assert len(gaps) > 10  # each item's secret gives it its own gap
    assert all(900 <= gap <= 2100 for gap in gaps)  # 15-35% of the $60 spread
    assert private_gap(36000, 36000, "s") == 0


def test_a_lowball_moves_nothing() -> None:
    move = seller_move(36000, 30000, 1500, 1000, SellerState())
    assert (move.price_cents, move.agreed, move.state.rounds) == (36000, False, 0)
    assert "well under" in move.note


def test_an_agreement_holds_and_never_goes_lower() -> None:
    agreed = seller_move(36000, 30000, 1500, 31000, SellerState())
    assert agreed.agreed and agreed.price_cents == 31000
    again = seller_move(36000, 30000, 1500, 25000, agreed.state)
    assert again.agreed and again.price_cents == 31000


def test_an_offer_over_asking_agrees_at_asking() -> None:
    move = seller_move(36000, 30000, 1500, 40000, SellerState())
    assert move.agreed and move.price_cents == 36000


@pytest.mark.parametrize(
    ("asking", "max_cents", "opening"),
    [(36000, 50000, 23400), (36000, 20000, 20000), (1500, 5000, 975)],
)
def test_the_opening_offer(asking: int, max_cents: int, opening: int) -> None:
    assert opening_offer(asking, max_cents) == opening


def test_the_buyer_takes_a_counter_one_step_away() -> None:
    assert buyer_move(36000, 32000, 31000, 31100, 2) == haggle.BuyerMove("take", 31100)


def test_the_buyer_stops_at_its_max() -> None:
    assert buyer_move(36000, 29000, 29000, 33000, 3) == haggle.BuyerMove("stop", 33000)


def test_the_last_offer_is_the_max() -> None:
    last = haggle.MAX_ROUNDS - 1
    assert buyer_move(36000, 32000, 28000, 33000, last) == haggle.BuyerMove("offer", 32000)


def test_prices_read_like_pricing_usd() -> None:
    from yardsale.pricing import usd

    for cents in (0, 5, 1250, 34000, 123456):
        assert haggle.usd(cents) == usd(cents)
