import pytest

from yardsale.commands import Command, accepts_deal, budget, parse, seller_prices

# What ASI:One's planner sent on October 24 when the buyer said "accept that deal and buy it".
RELAYED_ACCEPT = (
    "The buyer accepts the negotiated deal for the used Specialized hybrid bike at the agreed "
    "price (within the $320 max budget). Please proceed to complete the purchase — send the "
    "payment link / next steps so we can finalize the transaction."
)


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


@pytest.mark.parametrize(
    "text",
    [
        RELAYED_ACCEPT,
        "Great, accept that deal and buy it for me.",
        "The user would like to take the deal.",
        "Please proceed with the purchase.",
    ],
)
def test_relayed_acceptances(text: str) -> None:
    assert accepts_deal(text)
    assert parse(text) is None  # these reach the chat as plain text, not as a command


@pytest.mark.parametrize(
    "text",
    [
        "The buyer does not accept the deal.",
        "I don't want to buy it",
        "Find me another bike I'd accept",
        "The buyer rejected the offer and won't proceed with the purchase.",
        "show me what else is for sale",
    ],
)
def test_not_acceptances(text: str) -> None:
    assert not accepts_deal(text)


def test_prices_in_a_sentence() -> None:
    assert seller_prices("sell this, at least $40, small dent") == (None, 4000, "")
    assert seller_prices("List this jacket for $60. Size M") == (6000, None, "")
    assert seller_prices("won't go below 90") == (None, 9000, "")
    assert budget("find me a road bike under $300") == (30000, "")
    assert budget("a road bike") == (None, "")


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("approve all", Command("approve", every=True)),
        ("list them all", Command("approve", every=True)),
        ("yes to all", Command("approve", every=True)),
        ("approve 2", Command("approve", picks=(2,))),
        ("approve #1, 3 and 4", Command("approve", picks=(1, 3, 4))),
        ("cancel 2", Command("cancel", picks=(2,))),
        ("cancel all", Command("cancel", every=True)),
        ("rules 2", Command("rules", pick=2)),
        ("lowest 2 $40", Command("edit", floor_cents=4000, pick=2)),
        ("lowest 2 40", Command("edit", floor_cents=4000, pick=2)),
        ("ask #3 to 60", Command("edit", asking_cents=6000, pick=3)),
        ("#2: ask $60, lowest $45", Command("edit", asking_cents=6000, floor_cents=4500, pick=2)),
        ("item 2 lowest 40", Command("edit", floor_cents=4000, pick=2)),
        ("lowest $40 on 2", Command("edit", floor_cents=4000, pick=2)),
        ("approve 2 at $50", Command("edit", asking_cents=5000, pick=2, approve=True)),
        ("list 2 on ebay", Command("ebay_list", pick=2)),  # still eBay's
        ("ask 60 lowest 40", Command("edit", asking_cents=6000, floor_cents=4000)),  # no number
    ],
)
def test_commands_by_number(text: str, expected: Command) -> None:
    """A seller with several photos waiting names each one by number."""
    assert parse(text) == expected


def test_prices_for_two_numbers_at_once_are_refused() -> None:
    command = parse("ask 2 $60, lowest 3 $40")
    assert command is not None and "One item at a time" in command.error
