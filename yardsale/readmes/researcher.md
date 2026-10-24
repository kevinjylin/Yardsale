# Yardsale Researcher

![tag:innovationlab](https://img.shields.io/badge/innovationlab-3D8BD3)
![tag:hackathon](https://img.shields.io/badge/hackathon-5F43F1)

Part of **Yardsale**: snap a photo of something you want to sell, and it becomes a live agent that
haggles for you.

Send this agent a product search. It returns what that product actually sold for on eBay in the
last 90 days, with each listing's title, sold price, shipping, condition, sale date, link, and
photo. It uses an Apify eBay sold-listings scraper.

## Messages

**`ResearchRequest`** (protocol `YardsaleResearch`)

| Field | Type | Notes |
|---|---|---|
| `request_id` | string | Echoed back in the reply |
| `query` | string | For example "Trek FX 2 hybrid bike" |
| `condition` | string | `new`, `used`, or `any` |
| `max_results` | int | 1 to 50, default 20 |

**Reply: `ResearchResult`** with `request_id`, `query`, and `comps`. Each comp has title,
price_cents, shipping_cents, condition, sold_at, url, image_url, and best_offer (true when eBay
hides the accepted offer and the price shown is the list price). On failure the reply is
**`ServiceError`** with `request_id` and `reason`.
