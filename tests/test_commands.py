import pytest

from yardsale.commands import Command, budget, parse, seller_prices


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("approve", Command("approve")),
        ("Yes please!", Command("approve")),
        ("looks good", Command("approve")),
        ("cancel", Command("cancel")),
        ("no", Command("cancel")),
        ("ask $360", Command("edit", asking_cents=36000)),
        ("lowest $280", Command("edit", floor_cents=28000)),
        ("lowest price 280", Command("edit", floor_cents=28000)),  # not an asking price
        ("ask 360, at least 280", Command("edit", asking_cents=36000, floor_cents=28000)),
        ("make it $1,250.50", Command("edit", asking_cents=125050)),
        ("approve at $350", Command("edit", asking_cents=35000, approve=True)),
        ("list it for $60", Command("edit", asking_cents=6000)),  # not an approval
        ("offer $200", Command("offer", amount_cents=20000)),
        ("offer 200 on #2", Command("offer", amount_cents=20000, pick=2)),
        ("how about 250 for 1?", Command("offer", amount_cents=25000, pick=1)),
        ("I'll pay 99.99", Command("offer", amount_cents=9999)),
        ("accept", Command("accept")),
        ("deal!", Command("accept")),
        ("2", Command("select", pick=2)),
        ("#3", Command("select", pick=3)),
        ("tell me about 1", Command("select", pick=1)),
        ("my listings", Command("listings")),
        ("unlist 2", Command("unlist", pick=2)),
        ("take it down", Command("unlist")),
        ("help", Command("help")),
        ("350", Command("amount", amount_cents=35000)),
        ("$12", Command("amount", amount_cents=1200)),
    ],
)
def test_commands(text: str, expected: Command) -> None:
    assert parse(text) == expected


@pytest.mark.parametrize(
    "text", ["", "hi there", "find me a road bike", "what's for sale?", "at least $40.999"]
)
def test_not_commands(text: str) -> None:
    assert parse(text) is None


def test_bad_amounts_are_errors_not_guesses() -> None:
    command = parse("offer $2,000,000")
    assert command is not None and command.amount_cents is None
    assert "between" in command.error


def test_prices_in_a_sentence() -> None:
    assert seller_prices("sell this, at least $40, small dent") == (None, 4000, "")
    assert seller_prices("List this jacket for $60. Size M") == (6000, None, "")
    assert seller_prices("won't go below 90") == (None, 9000, "")
    assert budget("find me a road bike under $300") == (30000, "")
    assert budget("a road bike") == (None, "")
