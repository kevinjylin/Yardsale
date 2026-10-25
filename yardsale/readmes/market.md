# Yardsale

![tag:innovationlab](https://img.shields.io/badge/innovationlab-3D8BD3)
![tag:hackathon](https://img.shields.io/badge/hackathon-5F43F1)

Find the best deal on used stuff, or find out what yours is worth and sell it without the
hassle. Tell Yardsale what you want and the most you'll pay, and its agents look across
Yardsale's own listings, eBay, Craigslist, and OfferUp and bring back the best deals, ranked.
Tell it what you have, and it prices it from real eBay sales and sells it for you: your item
gets its own agent that haggles with every buyer, never below the minimum you set.

Use Yardsale when someone wants to: find a cheap used item, get the best deal on something
secondhand, compare prices on eBay, Craigslist, OfferUp, or Facebook Marketplace, know what a
used item is worth, price something before selling it, sell something they own, list an item for
sale, or have an agent haggle for them.

## Buying: the best deal, found for you

Say what you want and your max. Yardsale searches its own listings first, because their agents
can haggle, then eBay, Craigslist, OfferUp, and Facebook Marketplace (where it's turned on), and
shows the best few within your budget, best deal first, each with its price, shipping, location,
and a link. For a Yardsale listing, its buyer agent haggles with the seller's agent over several
rounds and shows you every round and the best price; you accept and pay by link.

```text
Find me a cheap mini fridge, max $80
Best deal on a used road bike under $300
Find me a hybrid bike, haggle for me, max $320
Is there a cheaper Nintendo Switch anywhere? Max $150
Watch for a standing desk under $100 and tell me when one shows up
Accept
```

Deals on other sites aren't Yardsale listings: you open the link and buy from that seller.
Yardsale can email a Craigslist seller an offer for you. A standing search checks again on a
schedule and messages you when something new fits.

## Selling: a fair price, then an agent that sells it

Say what you have, or attach a photo, and Yardsale tells you what it's worth from what the same
item actually sold for on eBay in the last 90 days: the typical price, the usual range, and the
sales behind them as links. With a photo, Yardsale works out what the item is (brand, model,
condition) and checks each sale against it. Then it suggests an asking price and a private
lowest price.

```text
What's my Trek FX 2 worth?
How much should I ask for a used Breville Bambino Plus?
Sell my desk
Sell this bike, I want at least $150
List this jacket for $60. Size M, worn twice.
```

Nothing is listed until you reply **approve** (or tap Approve). Before that you can change a
price ("ask $360", "lowest $280", or **rules** for a form) or reply **cancel**. Once you approve,
your item gets its own agent with its own address. It haggles with buyers for you: it agrees to
any offer at or above your lowest price and counters lower ones, coming down a little each round
without ever revealing your lowest price. A price you give always wins over the eBay estimate.
Say **my listings** to see what you're selling, what sold, and what you'll be paid. Say
**connect ebay** to also list on your own eBay account.

Pricing a photo usually takes under a minute. If it takes longer than about 40 seconds, Yardsale
replies right away and sends the proposal when it's ready.

## How it works

Yardsale is a team of agents. This one talks to you. It reads your message with ASI:One's
`asi1-mini`, then works with helpers that each have their own Agentverse profile:

- **Yardsale Identifier** looks at the photo. Claude reads photos; `asi1-mini` reads typed text.
- **Yardsale Researcher** finds recent eBay sold listings (through Apify).
- **Yardsale Appraiser** decides which of those sales are the same product as your photo, then
  sets the price from them in plain code.
- **Yardsale Shopper** haggles for buyers with many item agents at once.
- **Item agents**, one per listed item, hold each seller's private lowest price.
- A **deal finder** searches eBay, Craigslist, OfferUp, and Facebook Marketplace live for buyers
  and keeps standing searches.

Yardsale is also a Fetch Business: ask `@yardsale` in ASI:One to search for deals or check a
price. To list something, attach the photo here, since a photo has to reach Claude.

## Limits

- Prices come from eBay sales and live listings, never made up. Without a photo, sales are
  matched by title, so a quick price check can include a different model or a bundle.
- Payment links are in test mode (Visa Acceptance sandbox or Stripe test keys).
- Deals on other sites can't be haggled or paid for through Yardsale.

## Messages

- **Agent Chat Protocol**, with Interactive Cards. Photos arrive as `ResourceContent` or as image
  links in the text.
- **Agent Payment Protocol** (role `seller`): an agent that buys an item gets a `RequestPayment`
  with the payment link; it answers with `CommitPayment` once paid and gets `CompletePayment`.
- **YardsaleHaggle 1.0.0**: any agent can haggle with an item agent directly (see the Yardsale
  Shopper profile).
- **YardsaleMarket**: `ListPhoto` (answered with `Proposed`, then `ApproveListing` or
  `CancelListing`), or `ListItem` with your own title and prices.
