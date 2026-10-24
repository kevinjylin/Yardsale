# Yardsale

![tag:innovationlab](https://img.shields.io/badge/innovationlab-3D8BD3)
![tag:hackathon](https://img.shields.io/badge/hackathon-5F43F1)

Sell your used stuff from a photo, or buy secondhand for less. Yardsale prices used items from
real eBay sales, gives each item its own agent that haggles within the seller's limits, and sends
the buyer a payment link when they agree.

Use Yardsale when someone wants to: sell something they own, find out what a used item is worth,
list an item for sale, buy something used or secondhand, find a cheap deal, or have an agent
negotiate a price for them.

## Selling

Attach a picture of something you want to sell, and Yardsale:

1. works out what it is (brand, model, condition),
2. finds what the same item actually sold for on eBay in the last 90 days,
3. suggests an asking price and a private lowest price, and shows you the sales behind them.

Nothing is listed until you reply **approve** (or tap Approve). Before that you can change a
price ("ask $360", "lowest $280", or **rules** for a form) or reply **cancel**. Once you approve,
Yardsale starts an agent for your item and gives you its address. The agent haggles with buyers
for you: it agrees to any offer at or above your lowest price, and counters lower ones, coming
down a little each round without ever revealing your lowest price.

```text
Sell this
Sell this bike, I want at least $150
List this jacket for $60. Size M, worn twice.
What's this worth?
```

A price you give always wins over the eBay estimate. Pricing usually takes a minute or two; if it
takes longer than about 40 seconds, Yardsale replies right away and sends the proposal when it's
ready. Say **my listings** to see what you're selling, what sold, and what you'll be paid.

## Buying

Say what you want and the most you'll pay. Yardsale's buyer agent haggles with every matching
seller's agent at once, over several rounds, and shows you every round and the best price:

```text
Find me a hybrid bike, haggle for me, max $320
Accept
```

Or haggle yourself: search, pick a result by number, and make offers.

```text
Find me a cheap road bike under $300
Offer $250 on 2
Accept
```

When you accept, you get a payment link (Visa Pay By Link or Stripe, in test mode). Say **paid**
once you've paid, or **my orders** to check. Yardsale keeps a small commission from each sale and
the seller gets the rest.

## How it works

Yardsale is a team of agents. This one talks to you. It reads your message with ASI:One's
`asi1-mini`, then works with helpers that each have their own Agentverse profile:

- **Yardsale Identifier** looks at the photo (Claude vision).
- **Yardsale Researcher** finds recent eBay sold listings (Apify).
- **Yardsale Appraiser** decides which of those sales are the same product as your photo, then
  sets the price from them.
- **Yardsale Shopper** haggles for buyers with many item agents at once.
- **Item agents**, one per listed item, hold each seller's private lowest price.

## Messages

- **Agent Chat Protocol**, with Interactive Cards. Photos arrive as `ResourceContent` or as image
  links in the text.
- **Agent Payment Protocol** (role `seller`): an agent that buys an item gets a `RequestPayment`
  with the payment link; it answers with `CommitPayment` once paid and gets `CompletePayment`.
- **YardsaleHaggle 1.0.0**: any agent can haggle with an item agent directly (see the Yardsale
  Shopper profile).
- **YardsaleMarket**: `ListPhoto` (answered with `Proposed`, then `ApproveListing` or
  `CancelListing`), or `ListItem` with your own title and prices.
