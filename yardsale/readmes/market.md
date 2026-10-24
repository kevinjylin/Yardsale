# Yardsale

Sell anything from a photo. Attach a picture of something you want to sell, and Yardsale:

1. works out what it is (brand, model, condition),
2. finds what the same item actually sold for on eBay in the last 90 days,
3. sets an asking price and a private lowest price, and
4. starts a new agent for your item that haggles with buyers for you. It counters low offers and
   accepts anything at or above your lowest price.

The reply shows the eBay sales behind the price and your item agent's address.

## Try it

Attach a photo and say anything you like, for example:

```text
Sell this
Sell this bike, I want at least $150
List this jacket for $60. Size M, worn twice.
```

A price you give always wins over the eBay estimate. Pricing usually takes a minute or two; if it
takes longer than about 40 seconds, Yardsale replies right away and sends the price when it's
ready.

## How it works

Yardsale is a team of agents. This one talks to you. It reads your message with ASI:One's
`asi1-mini`, then calls three helpers, each with its own Agentverse profile:

- **Yardsale Identifier** looks at the photo (Claude vision).
- **Yardsale Researcher** finds recent eBay sold listings (Apify).
- **Yardsale Appraiser** decides which of those sales are the same product as your photo, then
  sets the price from them.

No payment is made yet. A closed deal is an agreed price.

## Messages

Speaks the **Agent Chat Protocol**. Photos arrive as `ResourceContent` or as image links in the
text. Other agents can also send `ListPhoto` or `ListItem` (protocol `YardsaleMarket`).
