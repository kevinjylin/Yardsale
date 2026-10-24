# Yardsale Identifier

![tag:innovationlab](https://img.shields.io/badge/innovationlab-3D8BD3)
![tag:hackathon](https://img.shields.io/badge/hackathon-5F43F1)

Part of **Yardsale**: snap a photo of something you want to sell, and it becomes a live agent that
haggles for you.

Send this agent a photo of a secondhand item. It works out what the item is and writes the search
an eBay buyer would type to find that exact product. It uses Claude's vision.

## Messages

**`IdentifyRequest`** (protocol `YardsaleIdentify`)

| Field | Type | Notes |
|---|---|---|
| `request_id` | string | Echoed back in the reply |
| `image_b64` | string | Base64 photo, ideally 1280 px or smaller |
| `media_type` | string | For example `image/jpeg` |
| `note` | string | Optional seller hint, such as "size M" |

**Reply: `Identified`** with `request_id` and `item`: title, brand, model, category, condition
(new, like new, good, fair, poor), details, search_query, generic (no brand or model), and
confidence. On failure the reply is **`ServiceError`** with `request_id` and `reason`.
