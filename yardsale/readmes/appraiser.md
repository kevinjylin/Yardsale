# Yardsale Appraiser

![tag:innovationlab](https://img.shields.io/badge/innovationlab-3D8BD3)
![tag:hackathon](https://img.shields.io/badge/hackathon-5F43F1)

Part of **Yardsale**: snap a photo of something you want to sell, and it becomes a live agent that
haggles for you.

Send this agent an item (photo and details) and a list of eBay sold listings. It decides which
sales are the same product, using Claude to compare the photos, titles, and conditions. Then it
sets an asking price from the median of those sales and a lowest acceptable price.

## Messages

**`AppraiseRequest`** (protocol `YardsaleAppraise`)

| Field | Type | Notes |
|---|---|---|
| `request_id` | string | Echoed back in the reply |
| `item` | object | Same shape as the Yardsale Identifier's `item` |
| `image_b64`, `media_type` | string | The seller's photo |
| `comps` | list | Same shape as the Yardsale Researcher's `comps` |

**Reply: `Appraisal`** with `request_id`, the matched sales (each with `same_product`,
`similarity`, and a short `reason`), a plain-English `basis`, `confidence` (high, medium, low,
none), and `asking_cents`, `floor_cents`, `low_cents`, `high_cents`. Prices are empty when no sale
was close enough. On failure the reply is **`ServiceError`** with `request_id` and `reason`.
