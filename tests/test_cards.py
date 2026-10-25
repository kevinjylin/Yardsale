"""How cards are laid out, against the way ASI:One's web app draws them: in a row, buttons,
pictures, and fields share the line, but a group, list, or section takes a whole line. So side
by side means buttons in a row, never boxes in a row."""

from __future__ import annotations

from typing import Any

import pytest
from uagents_core.contrib.protocols.chat import MetadataContent

from tests import sim
from yardsale import cards
from yardsale.card_schema import (
    ButtonNode,
    Card,
    CarouselCardPayload,
    CustomCardPayload,
    GroupNode,
    ListNode,
    SectionNode,
    extract_card,
)

FULL_LINE = (GroupNode, SectionNode, ListNode)  # what ASI:One gives a whole line in a row
PAY = "https://pay.example.test/deal-1"


@pytest.fixture(autouse=True)
def pictures_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("YARDSALE_CARD_ART", cards.ART_BASE)


def card(content: MetadataContent | None) -> Card:
    assert content is not None
    found = extract_card(content)
    assert found is not None, "ASI:One wouldn't show this card"
    return found


def walk(node: Any) -> list[Any]:
    found = [node]
    for child in getattr(node, "children", None) or []:
        found += walk(child)
    for entry in getattr(node, "items", None) or []:
        for child in getattr(entry, "children", None) or []:
            found += walk(child)
    return found


def rows(found: Card) -> list[GroupNode]:
    assert isinstance(found.payload, CustomCardPayload)
    return [
        n for n in walk(found.payload.root) if isinstance(n, GroupNode) and n.direction == "row"
    ]


def labels(row: GroupNode) -> list[str]:
    return [str(n.label) for n in row.children if isinstance(n, ButtonNode)]


def assert_laid_out(found: Card) -> None:
    """Nothing in a row would be pushed onto a line of its own, and no row has more than two
    buttons, so they fit a phone."""
    for row in rows(found):
        assert not [n for n in row.children if isinstance(n, FULL_LINE)], row
        assert len([n for n in row.children if isinstance(n, ButtonNode)]) <= cards.PER_ROW


def deal(n: int) -> dict[str, Any]:
    return {
        "id": f"craigslist:{n}",
        "title": f"Mini fridge {n}",
        "site": "Craigslist",
        "source": "craigslist",
        "price": f"${40 + n}",
        "url": f"https://craigslist.example.test/{n}",
        "why": "50% under the typical sold price",
        "location": "Riverside",
    }


def test_the_menu_puts_selling_and_buying_side_by_side() -> None:
    found = card(cards.menu(live=2, waiting=1, to_pay=1, orders=1, watching=1))
    assert found.resubmittable  # the menu stays tappable after the first tap
    assert_laid_out(found)
    tiles, *grid = [row for row in rows(found) if labels(row)]
    assert labels(tiles) == ["Sell something", "Find a deal"]
    assert all(isinstance(n, ButtonNode) and n.image is not None for n in tiles.children)
    assert [labels(row) for row in grid] == [
        ["Approve · 1 waiting", "My listings · 2 for sale"],
        ["My orders · 1 to pay", "Watches · 1 running"],
        ["Haggle on Craigslist"],
    ]


def test_without_pictures_the_tiles_are_plain_buttons(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("YARDSALE_CARD_ART", "")
    found = card(cards.menu())
    tiles = next(row for row in rows(found) if labels(row))
    assert labels(tiles) == ["Sell something", "Find a deal"]
    assert all(isinstance(n, ButtonNode) and n.image is None for n in tiles.children)


def test_screens_end_in_main_menu_with_buttons_two_to_a_row() -> None:
    found = card(
        cards.detail(
            "Trek FX 2 hybrid bike",
            None,
            [("Asking price", "$340"), ("Condition", "good")],
            [("Buy", {"action": "a"}), ("Offer", {"action": "b"}), ("Haggle", {"action": "c"})],
        )
    )
    assert_laid_out(found)
    assert [label for label, _ in sim.card_buttons(found)] == [
        "Buy",
        "Offer",
        "Haggle",
        "Main menu",
    ]
    assert "Asking price" in sim.card_text(found) and "$340" in sim.card_text(found)


def test_pay_opens_the_payment_page() -> None:
    found = card(cards.payment("deal-1", 7500, "mini fridge", "stripe", None, url=PAY))
    assert sim.card_links(found) == [("Pay $75", PAY)]
    assert ("I've paid", {"action": "paid", "deal_id": "deal-1"}) in sim.card_buttons(found)
    assert_laid_out(found)
    # A link that isn't http(s) is never offered as a button.
    found = card(cards.payment("deal-1", 7500, "mini fridge", "stripe", None, url="javascript:x"))
    assert sim.card_links(found) == []


def test_a_deal_opens_its_post_and_stays_tappable() -> None:
    found = card(cards.deal_detail(deal(1)))
    assert found.resubmittable
    assert sim.card_links(found) == [("Open on Craigslist", "https://craigslist.example.test/1")]
    assert ("Haggle for me", {"action": "negotiate_help"}) in sim.card_buttons(found)
    assert_laid_out(found)


def test_long_carousels_show_one_item_at_a_time() -> None:
    """ASI:One stacks a carousel's items one under another; "slide" shows one with arrows."""
    many = card(cards.deals([deal(n) for n in range(5)], "Your last search"))
    assert isinstance(many.payload, CarouselCardPayload)
    assert many.payload.style == "slide" and many.resubmittable
    assert many.payload.root_cta is not None and many.payload.root_cta.selection == {
        "action": "menu"
    }
    # Each deal names itself, so a tap on an older card still finds the right one.
    first = many.payload.items[0].primary_cta.selection
    assert first == {"action": "deal", "n": 0, "id": "craigslist:0"}
    few = card(cards.carousel("Two", None, many.payload.items[:2]))
    assert isinstance(few.payload, CarouselCardPayload) and few.payload.style is None


def test_meeting_spots_open_their_maps() -> None:
    found = card(
        cards.meetup(
            "UCR",
            [
                ("Rivera Library entrance", "library", "https://maps.example.test/rivera"),
                ("Bell Tower", "landmark", "https://maps.example.test/bell"),
            ],
        )
    )
    assert [url for _, url in sim.card_links(found)] == [
        "https://maps.example.test/rivera",
        "https://maps.example.test/bell",
    ]
    assert_laid_out(found)


def test_watches_lock_after_one_tap() -> None:
    """Watch numbers shift once one stops, so an old Stop #2 must not stop the wrong one."""
    found = card(cards.watches(['"mini fridge" under $60', '"road bike" under $300'], 0, "eBay"))
    assert not found.resubmittable
    stops = [selection for label, selection in sim.card_buttons(found) if label.startswith("Stop")]
    assert stops == [{"action": "unwatch", "n": 1}, {"action": "unwatch", "n": 2}]
    assert_laid_out(found)
