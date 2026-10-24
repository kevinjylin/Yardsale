# Resale Comps: eBay sold prices for one item

What is this used thing worth? Resale Comps looks up what the same item actually sold for on eBay
in the last 90 days, screens out sales that would throw the price off, and returns the spread a
reseller would quote: the low, the 25th percentile, the median, the 75th percentile, and the high.

Built for [Yardsale](https://github.com/kevinjylin/Yardsale), where a photo of an item becomes a
listing priced from real sales, and an agent that haggles for it.

## What it does

1. Searches eBay's **sold** listings for each search you give, most recent first.
2. Reads each sale: title, sold price, shipping, condition, the date it sold, the link, and the
   photo. It reads both of eBay's result layouts.
3. Leaves out sales that aren't one working item: lots, bundles, "for parts" or broken items,
   and empty boxes or cases. A word you searched for yourself doesn't count against a sale, so
   "lego lot" still finds lots.
4. Leaves out sales older than the date range.
5. Works out the price spread from the sales it kept. Sales that ended in an accepted Best Offer
   are left out of it when there are others, because eBay shows their list price, not what the
   buyer paid.

## Input

| Field | Default | What it does |
| --- | --- | --- |
| `keywords` | (required) | Searches, as you'd type them into eBay. |
| `count` | 20 | Sales to return per search (1-200). |
| `daysToScrape` | 90 | Only sales that ended in this many days. |
| `itemCondition` | `any` | `any`, `new`, or `used`. |
| `ebaySite` | `ebay.com` | Prices are read in US dollars. |
| `includeExcluded` | false | Also return screened-out sales, each with its `excludedReason`. |
| `proxyConfiguration` | Apify Proxy | eBay blocks some datacenter addresses; residential is the most reliable. |

The input and the dataset fields match `caffein.dev/ebay-sold-listings`, so a program using that
Actor can switch to this one without code changes. Yardsale does it with one setting:
`YARDSALE_APIFY_ACTOR=<your-username>~resale-comps`.

## Output

Each dataset item is one sale:

```json
{
  "keyword": "Trek FX 2 Disc hybrid bike medium",
  "title": "Trek FX 2 Disc Hybrid Bike M",
  "soldPrice": "340.00",
  "soldCurrency": "USD",
  "shippingPrice": "25.00",
  "condition": "Pre-Owned",
  "endedAt": "2026-10-21",
  "url": "https://www.ebay.com/itm/...",
  "thumbnailUrl": "https://i.ebayimg.com/images/...",
  "isBestOfferAccepted": false,
  "excludedReason": ""
}
```

The run's `OUTPUT` record holds the spread for each search:

```json
{
  "Trek FX 2 Disc hybrid bike medium": {
    "sales": 24, "kept": 20, "excluded": {"lot": 2, "parts": 1, "older than the date range": 1},
    "currency": "USD", "pricedFrom": 19,
    "low": "300.00", "p25": "320.00", "median": "340.00", "p75": "360.00", "high": "380.00"
  }
}
```

## Limits

- It reads eBay's public search pages, which can change. The parser works from each result's
  text and links rather than exact page structure, and its tests cover both current layouts.
- eBay keeps about 90 days of sold listings.
- Prices are read in US dollars, so non-US eBay sites aren't offered yet.

## Running it locally

```bash
cd actors/resale-comps
pip install -r requirements.txt
apify run --input='{"keywords": ["Breville Bambino Plus"], "count": 10}'
```

`apify push` publishes it to your Apify account.
