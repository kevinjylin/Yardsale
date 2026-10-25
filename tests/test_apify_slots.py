"""Apify's limit on runs at once: the server never starts more than its slots allow, eBay sold
prices always get a slot, and a refused eBay search is retried and explained in plain words.

On October 25 a photo failed with Apify's raw 402 JSON in the seller's chat, because another
chat's deal search was using all 5 of the free plan's runs. Every Apify call here is faked.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from yardsale import researcher, sources
from yardsale.researcher import ResearchError, search_ebay_sold

SOLD = [{"title": "TI-30XIIS calculator", "soldPrice": "12.50", "url": "https://ebay.com/itm/1"}]
BUSY = {
    "error": {
        "type": "concurrent-runs-limit-exceeded",
        "message": "By launching this job you will exceed your limit of 5 concurrent Actor runs.",
    }
}


class Run:
    """One run-sync call. It's counted as running from entry until it answers."""

    def __init__(self, apify: FakeApify, url: str, status: int, body: Any) -> None:
        self.apify = apify
        self.sold = url == researcher.RUN_URL
        self.status = status
        self.body = body

    async def __aenter__(self) -> Run:
        self.apify.started.append("sold" if self.sold else "listing")
        self.apify.running += 1
        self.apify.listing_running += 0 if self.sold else 1
        self.apify.most = max(self.apify.most, self.apify.running)
        self.apify.most_listing = max(self.apify.most_listing, self.apify.listing_running)
        await asyncio.sleep(0.01)
        return self

    async def __aexit__(self, *args: object) -> None:
        self.apify.running -= 1
        self.apify.listing_running -= 0 if self.sold else 1

    async def json(self) -> Any:
        return self.body

    async def text(self) -> str:
        return json.dumps(self.body)


class FakeApify:
    """Stands in for aiohttp.ClientSession. `refusals` runs are answered 402 busy first."""

    def __init__(self, refusals: int = 0) -> None:
        self.refusals = refusals
        self.started: list[str] = []
        self.running = 0
        self.listing_running = 0
        self.most = 0
        self.most_listing = 0

    def session(self, **kwargs: Any) -> FakeApify:
        return self

    async def __aenter__(self) -> FakeApify:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    def post(self, url: str, **kwargs: Any) -> Run:
        if self.refusals:
            self.refusals -= 1
            return Run(self, url, 402, BUSY)
        body = SOLD if url == researcher.RUN_URL else [{"title": "listing"}]
        return Run(self, url, 201, body)


@pytest.fixture
def apify(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("APIFY_TOKEN", "test-token")
    monkeypatch.delenv("YARDSALE_APIFY_RUNS", raising=False)
    monkeypatch.setattr(sources, "BUSY_RETRY_SECONDS", (0.0, 0.0, 0.0))

    def install(fake: FakeApify) -> FakeApify:
        monkeypatch.setattr(sources.aiohttp, "ClientSession", fake.session)
        return fake

    return install


async def test_the_server_never_runs_more_than_five_and_sold_prices_skip_the_queue(
    apify: Any,
) -> None:
    fake = apify(FakeApify())
    listings = [sources.run_actor("some~actor", {}, 10, "0.25") for _ in range(8)]
    sold = search_ebay_sold("TI-30XIIS calculator", "used", 20)
    results = await asyncio.gather(*listings, sold)
    assert fake.most == 5
    assert fake.most_listing == 4  # one slot stays free for sold prices
    # The eBay sold search started with the first four listing runs, not after all eight.
    assert "sold" in fake.started[:5]
    assert results[-1][0].price_cents == 1250


async def test_a_refused_ebay_search_is_retried(apify: Any) -> None:
    fake = apify(FakeApify(refusals=2))
    comps = await search_ebay_sold("TI-30XIIS calculator", "used", 20)
    assert [comp.price_cents for comp in comps] == [1250]
    assert fake.started == ["sold", "sold", "sold"]


async def test_a_busy_ebay_search_is_told_in_plain_words(apify: Any) -> None:
    apify(FakeApify(refusals=4))
    with pytest.raises(ResearchError) as failed:
        await search_ebay_sold("TI-30XIIS calculator", "used", 20)
    assert str(failed.value) == "Too many price searches were running at once."


@pytest.mark.parametrize(("raw", "runs"), [("", 5), ("25", 25), ("0", 1), ("lots", 5)])
def test_the_limit_can_be_raised_for_a_paid_plan(
    monkeypatch: pytest.MonkeyPatch, raw: str, runs: int
) -> None:
    monkeypatch.setenv("YARDSALE_APIFY_RUNS", raw)
    assert sources.apify_runs_at_once() == runs
