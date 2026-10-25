# Yardsale Market Scout

![tag:innovationlab](https://img.shields.io/badge/innovationlab-3D8BD3)
![tag:hackathon](https://img.shields.io/badge/hackathon-5F43F1)

Part of **Yardsale**: snap a photo of something you want to sell, and it becomes a live agent that
haggles for you.

Send this agent a product search. It returns what similar items are listed for **right now** on
Craigslist, OfferUp, Facebook Marketplace, and eBay: for each site, how many listings look like
the same kind of item, their low, median, and high asking prices, and the three closest listings
with links. It searches every site at once through Apify actors.

The Yardsale Researcher finds what a product *sold* for; this agent finds what competitors are
*asking*. Yardsale prices an item from sold prices and uses the Scout's asking prices to suggest
a fair range, to warn a seller whose own price is far off the market, and to give the item's
agent reasons to cite while haggling.

## Messages

**`ScoutRequest`** (protocol `YardsaleScout`)

| Field | Type | Notes |
|---|---|---|
| `request_id` | string | Echoed back in the reply |
| `query` | string | For example "Trek FX 2 hybrid bike" |
| `zip_code` | string | Scopes the local sites; empty means each site's default area |
| `max_results` | int | Asked of each site, 1 to 50, default 20 |

**Reply: `ScoutReport`** with `request_id`, `query`, `sources`, and `failed`. Each entry in
`sources` has `source`, `count`, `low_cents`, `median_cents`, `high_cents`, `closest` (up to 3
listings with `title`, `price_cents`, `url`, `location`), and `prices_cents`. `failed` names the
sites that couldn't be searched. On failure the reply is **`ServiceError`** with `request_id` and
`reason`.
