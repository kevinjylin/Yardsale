# Yardsale Shopper

![tag:innovationlab](https://img.shields.io/badge/innovationlab-3D8BD3)
![tag:hackathon](https://img.shields.io/badge/hackathon-5F43F1)

Part of **Yardsale**: snap a photo of something you want to sell, and it becomes a live agent that
haggles for you.

This is Yardsale's buyer agent. A shopper tells Yardsale what they want and the most they'll pay.
This agent then haggles with every matching item agent at the same time, over several rounds:

- It opens at about two thirds of the asking price and meets each counter-offer halfway.
- It never offers more than the shopper's max, and never says what the max is.
- Each item agent never reveals its seller's lowest price: its counters stop short of it.

It doesn't buy anything. It reports every round with every item and the best price, and the
shopper picks one in the chat.

## The YardsaleHaggle protocol

Any agent can take the buyer's seat by speaking protocol **`YardsaleHaggle` 1.0.0** with an item
agent (role `buyer`):

| Message | From | Fields |
|---|---|---|
| `Offer` | buyer | `item_id`, `offer_cents`, optional `request_id` (echoed back) |
| `Counter` | item agent | `counter_cents`, `note`, `expires_at`, `agreed`. `agreed` is true when the item agent takes your offer at that price. |
| `AcceptCounter` | buyer | `item_id`, `counter_cents`: the exact price of your latest `Counter` |
| `Deal` | item agent | `deal_id`, `price_cents`. Yardsale then asks for payment through the Agent Payment Protocol. |
| `OfferRejected` | item agent | `reason`, `code` |

Nothing is sold until you send `AcceptCounter`, so you can haggle with several items and buy one.
Prices are integer US cents.
