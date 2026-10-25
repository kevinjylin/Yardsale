# Yardsale Deal Finder

![tag:innovationlab](https://img.shields.io/badge/innovationlab-3D8BD3)
![tag:hackathon](https://img.shields.io/badge/hackathon-5F43F1)

Part of **Yardsale**: snap a photo of something you want to sell, and it becomes a live agent;
say what you want to buy, and Yardsale's agents find the best deal for you.

This agent is the buyer's deal finder. Give it a search and a max, and it searches eBay,
Craigslist, OfferUp, and Facebook Marketplace at the same time (through Apify Actors), looks up
what that item actually sold for on eBay in the last 90 days, and returns the best few listings
within the max, each scored and explained in one line:

> $120 on Craigslist, 35% under the $185 typical sold price, like new, 6 photos, posted 2 days ago

Each listing is scored by its price against the market first (price plus shipping against the
median of matching recent sales), then quality (the site's condition words, photo count, seller
rating, a complete title, how recently it was posted), then how well the title fits the search.
Listings sold as parts, for repair, or in lots sink, as do accessories of the thing asked for.
The same item posted on two sites is shown once. Nothing it finds is a Yardsale listing, so the
buyer opens the link to buy from the seller.

## Messages

**`FindDeals`** (protocol `YardsaleDeals`)

| Field | Type | Notes |
|---|---|---|
| `request_id` | string | Echoed back in the reply |
| `query` | string | What the buyer wants, like "road bike" |
| `max_cents` | int or null | The most the buyer pays, shipping included; null for any price |
| `zip` | string | A ZIP code for sources that search around one; "" for the server's default |

**Reply: `DealsFound`**, within about 20 seconds, with `request_id`, `query`, `max_cents`,
`deals` (best first), `searched` and `failed` (which sites answered), `sites` (each site's
outcome; a site still searching at 20 seconds is `pending`, and asking again with the same
search picks up its results), `considered`, `typical_cents` (the market price, or null),
`comps_used`, `comps_low_cents` and `comps_high_cents` (its spread), `comps_basis`, and
`comps_note`. Each deal has `source`, `title`, `url`, `price_cents`,
`shipping_cents`, `total_cents`, `location`, `condition`, `image_url`, `posted_at`,
`accepts_offers`, `photo_count`, `seller_rating`, `score`, `versus_market_pct` (positive means
under the typical sold price), `why`, and `flags`. On failure the reply is **`ServiceError`**
with `request_id` and `reason`. Prices are integer US cents.
