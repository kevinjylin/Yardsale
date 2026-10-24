"""The Resale Comps Apify Actor's parsing and screening (actors/resale-comps/src/comps.py), on
pages shaped like eBay's two sold-listings layouts. The Actor itself needs Apify to run."""

from __future__ import annotations

import importlib.util
import sys
from datetime import date
from pathlib import Path
from types import ModuleType

COMPS = Path(__file__).resolve().parents[1] / "actors" / "resale-comps" / "src" / "comps.py"


def load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("resale_comps", COMPS)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["resale_comps"] = module
    spec.loader.exec_module(module)
    return module


comps = load()

OLD_LAYOUT = """
<ul class="srp-results">
<li class="s-item"><div class="s-item__title"><span>Shop on eBay</span></div>
  <a class="s-item__link" href="https://ebay.com/itm/123456"></a>
  <span class="s-item__price">$20.00</span></li>
<li class="s-item s-item__pl-on-bottom"><div class="s-item__wrapper">
  <div class="s-item__image-section"><a href="https://www.ebay.com/itm/111?hash=a">
    <img src="https://i.ebayimg.com/images/g/a/s-l140.jpg" alt=""></a></div>
  <div class="s-item__info">
    <div class="s-item__caption"><span class="s-item__caption--signal POSITIVE">
      <span>Sold  Oct 21, 2026</span></span></div>
    <a class="s-item__link" href="https://www.ebay.com/itm/111?hash=a">
      <div class="s-item__title"><span role="heading">
      <span class="LIGHT_HIGHLIGHT">New Listing</span>
      Trek FX 2 Disc Hybrid Bike M</span></div></a>
    <div class="s-item__subtitle"><span class="SECONDARY_INFO">Pre-Owned</span></div>
    <span class="s-item__price"><span class="POSITIVE">$340.00</span></span>
    <span class="s-item__shipping s-item__logisticsCost">+$25.00 delivery</span>
  </div></div></li>
<li class="s-item"><div class="s-item__info">
    <span class="s-item__caption--signal POSITIVE">Sold  Sep 30, 2026</span>
    <a class="s-item__link" href="https://www.ebay.com/itm/222"><div class="s-item__title">
      <span>Trek FX 2 Disc Hybrid Bike M</span></div></a>
    <div class="s-item__subtitle"><span class="SECONDARY_INFO">Pre-Owned</span></div>
    <span class="s-item__price"><span class="STRIKETHROUGH">$520.00</span></span>
    <span class="s-item__formatBestOfferAccepted">Best offer accepted</span>
    <span class="s-item__shipping">Free delivery</span>
  </div></li>
<li class="s-item"><div class="s-item__info">
    <span class="s-item__caption--signal POSITIVE">Sold  Oct 2, 2026</span>
    <a class="s-item__link" href="https://www.ebay.com/itm/333"><div class="s-item__title">
      <span>Lot of 3 Trek FX 2 Disc wheels, for parts</span></div></a>
    <span class="s-item__price">$90.00</span></div></li>
<li class="s-item"><a class="s-item__link" href="https://www.ebay.com/itm/444">
    <div class="s-item__title"><span>Trek FX 2 price range</span></div></a>
    <span class="s-item__price">$10.00 to $20.00</span></li>
</ul>
"""

NEW_LAYOUT = """
<ul class="srp-results">
<li class="s-card s-card--horizontal" data-listingid="555"><div class="su-card-container">
  <div class="su-media"><a href="https://www.ebay.com/itm/555?_skw=trek">
    <img class="s-card__image" src="https://i.ebayimg.com/images/g/b/s-l500.webp"></a></div>
  <div class="su-card-container__content">
    <div class="s-card__caption">
      <span class="su-styled-text positive">Sold  Oct 3, 2026</span></div>
    <a class="su-link" href="https://www.ebay.com/itm/555?_skw=trek"><div class="s-card__title">
      <span class="su-styled-text primary">Trek FX 2 Disc 2022 hybrid, M</span>
      <span class="clipped">Opens in a new window or tab</span></div></a>
    <div class="s-card__subtitle"><span class="su-styled-text secondary">Pre-Owned</span></div>
    <div class="s-card__attribute-row">
      <span class="su-styled-text s-card__price">$1,360.50</span></div>
    <div class="s-card__attribute-row">
      <span class="su-styled-text secondary">Free delivery</span></div>
  </div></div></li>
<li class="s-card"><a class="su-link" href="https://www.ebay.com/itm/555?_skw=again">
  <div class="s-card__title"><span>Trek FX 2 Disc 2022 hybrid, M</span></div></a>
  <span class="s-card__price">$1,360.50</span></li>
</ul>
"""


def test_the_older_layout() -> None:
    sales = comps.parse_results(OLD_LAYOUT)
    assert [s.url for s in sales] == [
        "https://www.ebay.com/itm/111",
        "https://www.ebay.com/itm/222",
        "https://www.ebay.com/itm/333",
    ]
    first, best_offer, lot = sales
    assert first.title == "Trek FX 2 Disc Hybrid Bike M"
    assert (first.soldPrice, first.shippingPrice, first.condition) == (
        "340.00",
        "25.00",
        "Pre-Owned",
    )
    assert (first.endedAt, first.isBestOfferAccepted) == ("2026-10-21", False)
    assert first.thumbnailUrl == "https://i.ebayimg.com/images/g/a/s-l140.jpg"
    assert (best_offer.soldPrice, best_offer.isBestOfferAccepted) == ("520.00", True)
    assert best_offer.shippingPrice == "0.00"
    assert lot.condition == ""


def test_the_newer_layout() -> None:
    (sale,) = comps.parse_results(NEW_LAYOUT)  # the repeat of the same listing is dropped
    assert sale.title == "Trek FX 2 Disc 2022 hybrid, M"
    assert (sale.soldPrice, sale.endedAt, sale.condition) == ("1360.50", "2026-10-03", "Pre-Owned")
    assert sale.url == "https://www.ebay.com/itm/555" and sale.shippingPrice == "0.00"


def test_screening_and_the_price_spread() -> None:
    sales = comps.screen(comps.parse_results(OLD_LAYOUT), "trek fx 2 disc", date(2026, 10, 1))
    reasons = {s.url.rsplit("/", 1)[1]: s.excludedReason for s in sales}
    assert reasons == {"111": "", "222": "older than the date range", "333": "lot"}
    summary = comps.summarize(sales)
    assert summary["kept"] == 1 and summary["median"] == "340.00"
    assert summary["excluded"] == {"older than the date range": 1, "lot": 1}


def test_searching_for_lots_keeps_lots() -> None:
    sales = comps.screen(comps.parse_results(OLD_LAYOUT), "trek wheels lot for parts")
    assert all(not s.excludedReason for s in sales)


def test_best_offer_sales_only_count_when_nothing_else_is_left() -> None:
    sale = comps.Sale
    priced = comps.summarize(
        [sale("a", "100.00"), sale("b", "200.00"), sale("c", "900.00", isBestOfferAccepted=True)]
    )
    assert (priced["pricedFrom"], priced["median"], priced["high"]) == (2, "150.00", "200.00")
    only_offers = comps.summarize([sale("c", "900.00", isBestOfferAccepted=True)])
    assert only_offers["median"] == "900.00"


def test_quartiles() -> None:
    sale = comps.Sale
    summary = comps.summarize([sale(str(n), f"{n}.00") for n in (300, 320, 340, 360, 380)])
    assert (summary["p25"], summary["median"], summary["p75"]) == ("320.00", "340.00", "360.00")
    four = comps.summarize([sale(str(n), f"{n}.00") for n in (100, 200, 300, 401)])
    assert (four["p25"], four["median"], four["p75"]) == ("175.00", "250.00", "325.25")
