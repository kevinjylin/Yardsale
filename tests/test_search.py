from yardsale.fakes import CATALOG
from yardsale.search import search
from yardsale.store import ACTIVE, Listing


def listing(item_id: str, key: str, asking: int, floor: int) -> Listing:
    item = CATALOG[key].item
    return Listing(
        item_id=item_id,
        owner="seller",
        seller_id="payout",
        listing_ref=item_id,
        status=ACTIVE,
        title=item.title,
        asking_cents=asking,
        floor_cents=floor,
        item=item,
    )


BIKE = listing("bike", "bike", 36000, 30000)
CHEAP_BIKE = listing("bike2", "bike", 20000, 15000)
ESPRESSO = listing("espresso", "espresso", 27000, 25000)
ALL = [BIKE, CHEAP_BIKE, ESPRESSO]


def test_words_match_titles_and_details() -> None:
    assert search(ALL, "espresso machines") == [ESPRESSO]
    assert search(ALL, "find me a cheap hybrid bike") == [CHEAP_BIKE, BIKE]  # cheapest first
    assert search(ALL, "Trek") == [CHEAP_BIKE, BIKE]
    assert search(ALL, "surfboard") == []


def test_better_matches_rank_first() -> None:
    assert search(ALL, "stainless espresso or bike")[0] == ESPRESSO


def test_no_words_lists_everything_cheapest_first() -> None:
    assert search(ALL, "what's for sale?") == [CHEAP_BIKE, ESPRESSO, BIKE]


def test_budget_uses_the_asking_price_never_the_floor() -> None:
    # The $360 bike has a $300 floor, but a $310 budget mustn't reveal that.
    assert search(ALL, "bike", budget_cents=31000) == [CHEAP_BIKE]


def test_prices_are_not_searchable() -> None:
    assert search(ALL, "300") == [] and search(ALL, "30000") == []
