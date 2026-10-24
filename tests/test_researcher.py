import pytest

from yardsale.researcher import parse_comp

# Shape of one result from the caffein.dev/ebay-sold-listings actor (from its README).
SAMPLE = {
    "itemId": "306671421088",
    "url": "https://www.ebay.com/itm/306671421088",
    "title": "Apple iPhone 13 Pro Max - 128GB - Unlocked - Cracked Back",
    "condition": "Pre-Owned",
    "endedAt": "2025-12-22T05:00:00.000Z",
    "soldPrice": "215",
    "soldCurrency": "USD",
    "isBestOfferAccepted": False,
    "shippingPrice": "6.20",
    "shippingCurrency": "USD",
    "thumbnailUrl": "https://i.ebayimg.com/thumbs/images/g/abc123/s-l500.jpg",
}


def test_parses_an_actor_result() -> None:
    comp = parse_comp(SAMPLE)
    assert comp is not None
    assert comp.price_cents == 21500
    assert comp.shipping_cents == 620
    assert comp.sold_at == "2025-12-22"
    assert comp.condition == "Pre-Owned"
    assert comp.image_url == SAMPLE["thumbnailUrl"]
    assert comp.best_offer is False


def test_reads_formatted_prices() -> None:
    comp = parse_comp({**SAMPLE, "soldPrice": "$1,250.50", "shippingPrice": None})
    assert comp is not None
    assert comp.price_cents == 125050
    assert comp.shipping_cents is None


def test_skips_results_it_cannot_price() -> None:
    assert parse_comp({**SAMPLE, "soldPrice": None}) is None
    assert parse_comp({**SAMPLE, "soldPrice": "see description"}) is None
    assert parse_comp({**SAMPLE, "soldPrice": "0"}) is None
    assert parse_comp({**SAMPLE, "soldCurrency": "GBP"}) is None


@pytest.mark.parametrize("price", ["NaN", "Infinity", "-5", "1e12", True, [], {"x": 1}])
def test_malformed_prices_are_skipped_not_fatal(price: object) -> None:
    assert parse_comp({**SAMPLE, "soldPrice": price}) is None


@pytest.mark.parametrize("raw", [None, "a string", 42, ["list"]])
def test_results_that_are_not_objects_are_skipped(raw: object) -> None:
    assert parse_comp(raw) is None


def test_only_web_links_are_kept() -> None:
    comp = parse_comp({**SAMPLE, "url": "javascript:alert(1)", "thumbnailUrl": "file:///x"})
    assert comp is not None
    assert comp.url == ""
    assert comp.image_url is None


def test_half_cents_round_up() -> None:
    comp = parse_comp({**SAMPLE, "soldPrice": "10.005"})
    assert comp is not None and comp.price_cents == 1001
