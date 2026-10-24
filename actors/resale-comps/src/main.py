"""Resale Comps: recent eBay sold prices for a product, screened and summarized.

Input matches caffein.dev/ebay-sold-listings (keywords, count, daysToScrape, itemCondition,
ebaySite), and so do the dataset items, so a caller can switch Actors without code changes. On
top of that, sales that aren't one working item are left out, and the run's OUTPUT record holds
the price spread (low, 25th percentile, median, 75th percentile, high).
"""

from __future__ import annotations

import asyncio
import re
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode

import httpx
from apify import Actor

from .comps import Sale, parse_results, screen, summarize

PER_PAGE = 240  # eBay's largest page
MAX_PAGES = 5
CONDITION_CODES = {"new": "1000|1500", "used": "3000", "any": ""}
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/141.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}
TITLE = re.compile(r"<title>(.*?)</title>", re.IGNORECASE | re.DOTALL)


def search_url(site: str, keywords: str, condition: str, page: int) -> str:
    params = {
        "_nkw": keywords,
        "LH_Sold": "1",
        "LH_Complete": "1",
        "_sop": "13",  # most recently ended first
        "_ipg": str(PER_PAGE),
        "_pgn": str(page),
    }
    if CONDITION_CODES.get(condition):
        params["LH_ItemCondition"] = CONDITION_CODES[condition]
    return f"https://www.{site}/sch/i.html?{urlencode(params)}"


async def fetch_page(url: str, proxy: Any, attempts: int = 3) -> str:
    for attempt in range(1, attempts + 1):
        proxy_url = await proxy.new_url() if proxy is not None else None
        try:
            async with httpx.AsyncClient(
                proxy=proxy_url, headers=HEADERS, timeout=30, follow_redirects=True
            ) as client:
                response = await client.get(url)
            found = "s-item" in response.text or "s-card" in response.text
            if response.status_code == 200 and found:
                return response.text
            title = TITLE.search(response.text)
            page = " ".join(title.group(1).split())[:80] if title else "no title"
            Actor.log.warning(f"eBay answered {response.status_code}, {page!r} (attempt {attempt})")
        except httpx.HTTPError as ex:
            Actor.log.warning(f"eBay request failed: {type(ex).__name__} (attempt {attempt})")
        await asyncio.sleep(attempt)
    raise RuntimeError(f"eBay didn't return results after {attempts} attempts")


async def comps_for(
    keywords: str, count: int, days: int, condition: str, site: str, proxy: Any
) -> list[Sale]:
    since = (datetime.now(UTC) - timedelta(days=days)).date()
    sales: list[Sale] = []
    for page in range(1, MAX_PAGES + 1):
        html = await fetch_page(search_url(site, keywords, condition, page), proxy)
        found = parse_results(html)
        if not found and page == 1:
            # Keep the page so a layout change can be fixed from the run's key-value store.
            await Actor.set_value("UNPARSED_PAGE", html, content_type="text/html")
            Actor.log.warning("No sales read from eBay's first page; saved it as UNPARSED_PAGE")
        sales += screen(found, keywords, since)
        kept = [s for s in sales if not s.excludedReason]
        if len(kept) >= count or len(found) < PER_PAGE // 2:
            break
    kept = [s for s in sales if not s.excludedReason][:count]
    return kept + [s for s in sales if s.excludedReason]


async def main() -> None:
    async with Actor:
        given = await Actor.get_input() or {}
        keywords = [str(k).strip() for k in given.get("keywords") or [] if str(k).strip()]
        if not keywords:
            raise ValueError("Give at least one search in keywords")
        count = max(1, min(int(given.get("count", 20)), 200))
        days = max(1, min(int(given.get("daysToScrape", 90)), 90))
        condition = str(given.get("itemCondition", "any"))
        site = str(given.get("ebaySite", "ebay.com"))
        include_excluded = bool(given.get("includeExcluded", False))
        proxy = await Actor.create_proxy_configuration(
            actor_proxy_input=given.get("proxyConfiguration") or {"useApifyProxy": True}
        )

        summaries = {}
        for query in keywords:
            sales = await comps_for(query, count, days, condition, site, proxy)
            summaries[query] = summarize(sales)
            # Pay-per-result: only kept sales are pushed unless asked otherwise.
            items = [s.as_item() | {"keyword": query} for s in sales]
            if not include_excluded:
                items = [item for item in items if not item["excludedReason"]]
            await Actor.push_data(items)
            Actor.log.info(f"{query!r}: {summaries[query]}")
        await Actor.set_value("OUTPUT", summaries)
