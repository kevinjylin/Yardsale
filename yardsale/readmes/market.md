# Yardsale

Sell anything from a photo, or find something to buy.

## Selling

Attach a picture of something you want to sell, and Yardsale:

1. works out what it is (brand, model, condition),
2. finds what the same item actually sold for on eBay in the last 90 days,
3. suggests an asking price and a private lowest price, and shows you the sales behind them.

Nothing is listed until you reply **approve** (or tap Approve). Before that you can change a
price ("ask $360", "lowest $280") or reply **cancel**. Once you approve, Yardsale starts a new
agent for your item and gives you its address. The agent haggles with buyers for you: it
counters low offers and accepts anything at or above your lowest price, which buyers never see.

```text
Sell this
Sell this bike, I want at least $150
List this jacket for $60. Size M, worn twice.
```

A price you give always wins over the eBay estimate. Pricing usually takes a minute or two; if it
takes longer than about 40 seconds, Yardsale replies right away and sends the proposal when it's
ready. Say **my listings** to see what you're selling and what sold.

## Buying

Say what you're looking for, then pick a result by number and make an offer:

```text
Find me a cheap road bike under $300
2
Offer $250
Accept
```

The item's agent closes the deal, or counters. **accept** takes its counter-offer, which is good
for 24 hours.

No payment is made yet. A closed deal is an agreed price.

## How it works

Yardsale is a team of agents. This one talks to you. It reads your message with ASI:One's
`asi1-mini`, then calls three helpers, each with its own Agentverse profile:

- **Yardsale Identifier** looks at the photo (Claude vision).
- **Yardsale Researcher** finds recent eBay sold listings (Apify).
- **Yardsale Appraiser** decides which of those sales are the same product as your photo, then
  sets the price from them.

## Messages

Speaks the **Agent Chat Protocol**. Photos arrive as `ResourceContent` or as image links in the
text. Other agents can also use protocol `YardsaleMarket`: `ListPhoto` (answered with `Proposed`,
then `ApproveListing` or `CancelListing`), or `ListItem` with their own title and prices.
