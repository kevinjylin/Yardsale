"""eBay sold-listing pages to resale comps. Standard library only, so it's easy to test.

parse_results() reads one page of eBay's sold-listings search. It works from the text and link
of each result rather than exact page structure, so it handles both of eBay's result layouts
(s-item and s-card). screen() drops sales that aren't one working item (lots, bundles, parts,
empty boxes), and summarize() gives the price spread a reseller would quote.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from html.parser import HTMLParser
from typing import Any

PRICE = re.compile(r"\$\s?([\d,]+(?:\.\d{2})?)")
SOLD_ON = re.compile(r"\bSold\s+([A-Z][a-z]{2})\s+(\d{1,2}),\s+(\d{4})")
SHIPPING = re.compile(r"\+?\s*\$\s?([\d,]+(?:\.\d{2})?)\s+(?:shipping|delivery)", re.IGNORECASE)
FREE_SHIPPING = re.compile(r"\bfree\s+(?:shipping|delivery)\b", re.IGNORECASE)
BEST_OFFER = re.compile(r"\bbest\s+offer\s+accepted\b", re.IGNORECASE)
CONDITIONS = (
    "Brand New",
    "New (Other)",
    "New",
    "Open Box",
    "Certified - Refurbished",
    "Excellent - Refurbished",
    "Very Good - Refurbished",
    "Good - Refurbished",
    "Seller refurbished",
    "Refurbished",
    "Pre-Owned",
    "Used",
    "For parts or not working",
)
PLACEHOLDER_TITLES = {"shop on ebay", ""}
# Words that mean a sale isn't one working item. Ignored when the search itself uses them.
NOT_ONE_ITEM = {
    "lot": re.compile(r"\b(?:lot\s+of|lots?\b|job\s+lot)", re.IGNORECASE),
    "bundle": re.compile(r"\b(?:bundle|set\s+of\s+\d+|\d+\s*x\s)", re.IGNORECASE),
    "parts": re.compile(
        r"\b(?:for\s+parts|parts\s+only|not\s+working|broken|as[\s-]is|repair)\b", re.IGNORECASE
    ),
    "box only": re.compile(r"\b(?:empty\s+box|box\s+only|case\s+only|no\s+console)\b", re.I),
}


@dataclass
class Sale:
    """One sold listing, with the same field names as caffein.dev/ebay-sold-listings."""

    title: str
    soldPrice: str
    soldCurrency: str = "USD"
    shippingPrice: str | None = None
    condition: str = ""
    endedAt: str = ""
    url: str = ""
    thumbnailUrl: str = ""
    isBestOfferAccepted: bool = False
    excludedReason: str = ""

    def cents(self) -> int:
        return dollars_to_cents(self.soldPrice)

    def as_item(self) -> dict[str, Any]:
        return asdict(self)


def dollars_to_cents(text: str) -> int:
    whole, _, part = text.replace(",", "").partition(".")
    return int(whole or 0) * 100 + int((part + "00")[:2])


def cents_to_dollars(cents: int) -> str:
    return f"{cents // 100}.{cents % 100:02d}"


@dataclass
class _Result:
    texts: list[tuple[str, str]] = field(default_factory=list)  # (classes, text)
    link: str = ""
    image: str = ""


class _ResultsParser(HTMLParser):
    """Collects each search result's texts (with the classes around them), link, and image."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.results: list[_Result] = []
        self._stack: list[tuple[str, str]] = []  # (tag, class)
        self._depth_of_result: int | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {name: value or "" for name, value in attrs}
        classes = values.get("class", "")
        if tag in ("br", "img", "input", "meta", "link", "hr", "source"):
            self._void(tag, values)
            return
        self._stack.append((tag, classes))
        if (
            self._depth_of_result is None
            and tag == "li"
            and re.search(r"\bs-(?:item|card)\b", classes)
        ):
            self._depth_of_result = len(self._stack)
            self.results.append(_Result())
        elif self._depth_of_result is not None and tag == "a" and not self.results[-1].link:
            href = values.get("href", "")
            if "/itm/" in href:
                self.results[-1].link = href.split("?", 1)[0]

    def _void(self, tag: str, values: dict[str, str]) -> None:
        if self._depth_of_result is None or tag != "img" or self.results[-1].image:
            return
        source = values.get("src", "") or values.get("data-src", "")
        if source.startswith("https://") and "ebayimg" in source:
            self.results[-1].image = source

    def handle_endtag(self, tag: str) -> None:
        while self._stack:
            open_tag, _ = self._stack.pop()
            if self._depth_of_result is not None and len(self._stack) < self._depth_of_result:
                self._depth_of_result = None
            if open_tag == tag:
                break

    def handle_data(self, data: str) -> None:
        text = " ".join(data.split())
        if self._depth_of_result is None or not text:
            return
        classes = " ".join(c for _, c in self._stack[self._depth_of_result - 1 :])
        self.results[-1].texts.append((classes, text))


def _sold_on(text: str) -> str:
    found = SOLD_ON.search(text)
    if not found:
        return ""
    month, day, year = found.groups()
    return datetime.strptime(f"{month} {day} {year}", "%b %d %Y").date().isoformat()


def _sale(result: _Result) -> Sale | None:
    def joined(*names: str) -> str:
        return " ".join(t for c, t in result.texts if any(n in c for n in names))

    title = joined("__title")
    for noise in ("New Listing", "Opens in a new window or tab"):
        title = title.replace(noise, "")
    title = " ".join(title.split())
    if title.lower() in PLACEHOLDER_TITLES or not result.link:
        return None
    # A Best Offer sale shows only its struck-through list price; it's flagged below.
    prices = [t for c, t in result.texts if "__price" in c and "STRIKETHROUGH" not in c]
    prices = prices or [t for c, t in result.texts if "__price" in c]
    price = PRICE.search(" ".join(prices))
    if price is None or " to " in " ".join(prices):  # a price range isn't one sale
        return None
    everything = " ".join(t for _, t in result.texts)
    shipping = None
    if FREE_SHIPPING.search(everything):
        shipping = "0.00"
    elif found := SHIPPING.search(everything):
        shipping = cents_to_dollars(dollars_to_cents(found.group(1)))
    described = joined("SECONDARY_INFO", "__subtitle")
    condition = next(
        (c for c in CONDITIONS if re.search(rf"(?<!\w){re.escape(c)}(?!\w)", described)), ""
    )
    return Sale(
        title=title[:300],
        soldPrice=cents_to_dollars(dollars_to_cents(price.group(1))),
        shippingPrice=shipping,
        condition=condition,
        endedAt=_sold_on(everything),
        url=result.link,
        thumbnailUrl=result.image,
        isBestOfferAccepted=bool(BEST_OFFER.search(everything)),
    )


def parse_results(html: str) -> list[Sale]:
    """The sold listings on one eBay search page, in page order, without duplicates."""
    parser = _ResultsParser()
    parser.feed(html)
    parser.close()
    sales: list[Sale] = []
    seen: set[str] = set()
    for result in parser.results:
        sale = _sale(result)
        if sale is not None and sale.url not in seen:
            seen.add(sale.url)
            sales.append(sale)
    return sales


def screen(sales: list[Sale], query: str, since: date | None = None) -> list[Sale]:
    """Mark sales that aren't one working item, or are older than `since`. Words the search
    itself uses don't count against a sale ("lego lot" searches can keep lots)."""
    for sale in sales:
        if since is not None and sale.endedAt and sale.endedAt < since.isoformat():
            sale.excludedReason = "older than the date range"
            continue
        for reason, pattern in NOT_ONE_ITEM.items():
            if pattern.search(sale.title) and not pattern.search(query):
                sale.excludedReason = reason
                break
    return sales


def _quantile(sorted_cents: list[int], q: tuple[int, int]) -> int:
    """Linear-interpolated quantile, rounded half up to the cent. q is a fraction (num, den)."""
    num, den = q
    position_num = num * (len(sorted_cents) - 1)
    low = position_num // den
    rest = position_num - low * den
    if rest == 0:
        return sorted_cents[low]
    span = sorted_cents[low + 1] - sorted_cents[low]
    return sorted_cents[low] + (2 * span * rest + den) // (2 * den)


def summarize(sales: list[Sale]) -> dict[str, Any]:
    """The spread of the kept sales' prices (Best Offer sales left out when there are others:
    eBay shows their list price, not what the buyer paid)."""
    kept = [s for s in sales if not s.excludedReason]
    priced = [s for s in kept if not s.isBestOfferAccepted] or kept
    cents = sorted(s.cents() for s in priced)
    summary: dict[str, Any] = {
        "sales": len(sales),
        "kept": len(kept),
        "excluded": {},
        "currency": "USD",
    }
    for sale in sales:
        if sale.excludedReason:
            summary["excluded"][sale.excludedReason] = (
                summary["excluded"].get(sale.excludedReason, 0) + 1
            )
    if cents:
        summary.update(
            pricedFrom=len(cents),
            low=cents_to_dollars(cents[0]),
            p25=cents_to_dollars(_quantile(cents, (1, 4))),
            median=cents_to_dollars(_quantile(cents, (1, 2))),
            p75=cents_to_dollars(_quantile(cents, (3, 4))),
            high=cents_to_dollars(cents[-1]),
        )
    return summary
