import math

from yardsale.appraiser import Judgement, appraise
from yardsale.models import Comp


def comp(dollars: int, title: str = "Trek FX 2 hybrid bike", best_offer: bool = False) -> Comp:
    return Comp(
        title=title,
        price_cents=dollars * 100,
        shipping_cents=None,
        condition="Pre-Owned",
        sold_at="2026-10-01",
        url=f"https://www.ebay.com/itm/{dollars}{title}",
        image_url=None,
        best_offer=best_offer,
    )


def same(index: int) -> Judgement:
    return Judgement(index=index, same_product=True, similarity=0.9, reason="same")


def other(index: int, similarity: float) -> Judgement:
    return Judgement(index=index, same_product=False, similarity=similarity, reason="other")


def test_same_product_sales_set_the_price() -> None:
    comps = [comp(d) for d in (100, 110, 120, 130, 140)] + [comp(30, "Bike lights lot")]
    appraisal = appraise("r", comps, [*(same(i) for i in range(5)), other(5, 0.1)])
    assert appraisal.confidence == "high"
    assert appraisal.asking_cents == 12000  # median
    assert appraisal.low_cents == 10500
    assert appraisal.high_cents == 13500
    assert appraisal.floor_cents == 11000  # lower quartile ($105), rounded half up
    assert len(appraisal.matches) == 5
    assert appraisal.comps_considered == 6


def test_best_offer_prices_are_left_out_when_possible() -> None:
    comps = [comp(100), comp(120), comp(500, best_offer=True)]
    appraisal = appraise("r", comps, [same(0), same(1), same(2)])
    assert appraisal.confidence == "medium"  # 3 same-product sales
    assert appraisal.asking_cents == 11000  # from the 2 that show what was paid
    assert "left out 1 Best Offer sale)" in appraisal.basis


def test_best_offer_prices_are_used_when_they_are_all_there_is() -> None:
    comps = [comp(100, best_offer=True), comp(120, best_offer=True), comp(140, best_offer=True)]
    appraisal = appraise("r", comps, [same(0), same(1), same(2)])
    assert appraisal.asking_cents == 12000
    assert "Best Offer" not in appraisal.basis


def test_similarity_of_exactly_0_6_counts() -> None:
    comps = [comp(100), comp(140, "Giant Escape 3")]
    appraisal = appraise("r", comps, [same(0), other(1, 0.6)])
    assert len(appraisal.matches) == 2
    appraisal = appraise("r", comps, [same(0), other(1, 0.59)])
    assert len(appraisal.matches) == 1


def test_a_bad_similarity_from_claude_counts_as_none() -> None:
    comps = [comp(100), comp(140, "Giant Escape 3")]
    appraisal = appraise("r", comps, [other(0, math.nan), other(1, 7.0)])
    assert [m.similarity for m in appraisal.matches] == [1.0]  # 7.0 is clamped; nan dropped


def test_prices_are_whole_cents() -> None:
    # An even count of odd prices has a half-cent median; nothing comes back fractional.
    comps = [comp(d) for d in (99, 101, 103, 106)]
    appraisal = appraise("r", comps, [same(i) for i in range(4)])
    for cents in (appraisal.asking_cents, appraisal.floor_cents, appraisal.low_cents):
        assert isinstance(cents, int)


def test_few_matches_fall_back_to_similar_items() -> None:
    comps = [
        comp(100),
        comp(90, "Giant Escape 3"),
        comp(110, "Trek FX 1"),
        comp(400, "Trek Domane"),
    ]
    appraisal = appraise("r", comps, [same(0), other(1, 0.7), other(2, 0.8), other(3, 0.3)])
    assert appraisal.confidence == "low"
    assert len(appraisal.matches) == 3
    assert appraisal.matches[0].same_product  # same-product sale listed first
    assert appraisal.asking_cents == 10000
    assert "rough estimate" in appraisal.basis


def test_nothing_close_gives_no_price() -> None:
    appraisal = appraise("r", [comp(100), comp(200)], [other(0, 0.2), other(1, 0.1)])
    assert appraisal.confidence == "none"
    assert appraisal.asking_cents is None
    assert appraisal.floor_cents is None
    assert appraisal.matches == []


def test_ignores_bad_and_repeated_indexes() -> None:
    comps = [comp(100), comp(120), comp(140)]
    judgements = [same(0), same(1), same(2), same(7), same(-1), other(0, 0.0)]
    appraisal = appraise("r", comps, judgements)
    assert len(appraisal.matches) == 3
    assert all(m.same_product for m in appraisal.matches)
