# The pitch

Cal Hacks 13.0, Sunday, October 25, 2026. Three minutes on stage, two minutes of questions. Two
phones on the big screen: the **seller phone** (a teammate) and the **buyer phone** (the lead,
who talks). The seller phone never speaks; it taps.

The swagger is in the framing. The facts stay exact, because the Fetch judges read the repo and
the Devpost against what's said on stage. Every claim below is true of `main` and has run live
unless a stage direction says otherwise. [calhacks-plan.md](calhacks-plan.md) has the long
version of the judge answers and what's unverified.

## The line

> **Every object you own gets its own agent. It knows your price, it knows the market, and it
> never gets tired of "is this still available?"**

Hallway versions:

- Photograph it. Set your minimum. Your agents price it, list it, and haggle for you.
- Yardsale is the marketplace where the listing negotiates.
- Fetch Business gives brands a verified agent. Yardsale gives everyone else one, for every thing
  they own.

## Before the lights

- The server is up on one machine only, the tunnel is up, and `curl <tunnel>/openapi.json`
  answers. Phone hotspot, not venue Wi-Fi.
- The Craigslist post for the demo item went up at least an hour ago (confirmation link clicked),
  with Yardsale's inbox as the contact and chat turned off.
- The demo search was run once within the last 10 minutes, so step 6 answers from cache in
  seconds.
- Have the recording of the Craigslist round trip and the `@yardsale` search on the laptop. If
  the relay is slow or the tunnel is gone, play the recording and say so.

## The script

Timings assume a fast, confident pace. Lines in bold are the ones to land; pause after them.

### 0:00 Cold open (both phones dark)

> It's the week before finals and you're moving out. Mini fridge, desk, bike, lamp. You post them,
> and by midnight you have two hundred DMs. "Is this still available." "Would you take ten
> dollars." You can answer them, or you can study. And if your flight home is Saturday, whatever's
> left goes in the dumpster.
>
> **We built Yardsale. You don't sell your stuff. Your agents do.**

### 0:20 Product 1, the seller (seller phone up)

*Seller phone: in ASI:One, `@ys-orchestrator`, attach the photo, type "sell this, at least $40",
send. The lead talks over the pricing (under a minute).*

> That one message just woke up four agents on Agentverse. Claude looks at the photo and names the
> thing. The Researcher pulls ninety days of eBay sales of it, through Apify. The Market Scout
> checks what it's asking right now on Craigslist, OfferUp, and Facebook Marketplace near campus.
> The Appraiser judges which sales are really the same item, and plain code sets the price.
>
> **No model guesses a number. Every dollar on this screen has a receipt.**

### 0:50 The proposal lands

*Seller phone: the proposal card and the carousel of sales. Point at the warning line.*

> Asking price. The three sales behind it. What it's going for down the street. And this line:
> "$40 is far under what this sells for." Yardsale tells you before a buyer finds out.

*Seller phone: tap **Approve & list**. The reply gives the item agent's address.*

> **And now it's alive.** The item is an agent, with its own Agentverse address and profile. It
> holds the minimum. Nobody else ever will.

*Seller phone: type `craigslist`. Show the post text, then switch to the live post.*

> It also writes the Craigslist post. Craigslist bans bots, so a human pastes it. This one went up
> this morning.

### 1:15 The haggle (buyer phone up)

*Buyer phone: reply to the live Craigslist post by email: "would you take $25?"*

> I'm a stranger on Craigslist. I lowball. Watch the thread.

*The agent's reply arrives in the thread: a counter and a reason.*

> That's the item's agent, answering through Craigslist's own email relay. It countered, it cited
> the market, and it didn't blink. The floor never left the agent. I say "deal".

*Buyer phone: reply "deal". The payment link arrives; pay with Stripe's test card. Seller phone:
`my listings` shows it sold and the payout after 5%.*

> Paid. Sold. The seller answered zero DMs.
>
> **You can't answer two hundred DMs during finals. Your agents can.**

### 1:50 Product 2, the buyer

> Now flip the table. Same market data, other side.

*Buyer phone: open `@yardsale` on ASI:One and type "find me the best deal on a mini fridge, max
$60".*

> This is `@yardsale`, Yardsale's Fetch Business agent. The front door. It checks Yardsale's own
> listings first, then the Deal Finder fans out across eBay, Craigslist, OfferUp, and Facebook
> Marketplace at once and scores every listing against what mini fridges actually sold for.
>
> **Not the cheapest. The cheapest relative to the truth.**

*The deals arrive, each with a one-line reason and a link.*

> "Forty-five on Craigslist, thirty-eight percent under the typical sold price, six photos,
> posted yesterday." Pick one, paste its reply address, and the buyer agent emails the seller and
> negotiates inside your max. It says it's an AI. It never says your number.

### 2:30 What's real

> Seven agents on Agentverse, plus one for every listed item. `@yardsale` on Fetch Business
> calling them through OpenAPI tools. Claude reads the photos. ASI:One's models read the words,
> and routed a buyer to us with no @mention. Apify under every price. Stripe, test mode, took the
> payment, and Yardsale kept five percent.
>
> A new marketplace has no buyers and no listings. Ours doesn't wait for either. The seller's
> agent lives on Craigslist from the first post, and the Deal Finder is useful with zero listings
> because it searches where listings already are.

### 2:50 Close

> **Fetch Business gives brands a verified agent. Yardsale gives everyone else one, for every
> thing they own.**
>
> Photograph it. Set your minimum. Go study. We're Yardsale.

About 480 spoken words, which is three minutes at a brisk pace. If it runs long, cut "What it's
going for down the street" and the second paragraph of "What's real" first.

## Fallbacks on stage

| If | Then say |
| --- | --- |
| The Craigslist reply is slow | "Craigslist's relay takes its time; here's the same thread from this morning," and play the recording. |
| `@yardsale` only knows `searchListings` (the Business Tool wasn't re-created) | Run product 2 in `@ys-orchestrator`'s chat instead: the same search and the same `negotiate` command. Say `@yardsale` is the front door and show its page at asi1.ai/ai/yardsale. |
| ASI:One doesn't route to us | @mention `@ys-orchestrator`. "Routing is ASI:One's call; the agents are ours." |
| The tunnel is gone | Same as the Business Tool row. Don't restart `cloudflared` on stage. |
| Facebook is off in the demo (`YARDSALE_FACEBOOK` unset) | Say "eBay, Craigslist, and OfferUp" and nothing else changes. |

## Judges' questions, answered in one breath

- **"Isn't that your agent talking to your agent?"** Not on that screen. I was a stranger on
  Craigslist, and the seller the buyer agent emails is a person. Agent-to-agent haggling is built
  and the protocol is published, but a buyer gets more from the whole market than from our own
  few listings, so it's in the repo, not the show.
- **"Isn't this a scraper?"** Apify fetches. We price, score, warn, haggle, and email. Scrapers
  don't counter-offer.
- **"Does ASI:One read the photo?"** No. Claude reads photos. `asi1-mini` reads typed text.
  ASI:One finds and routes to the agents. The README says exactly that.
- **"Is the payment real?"** Stripe test mode. Going live is a key swap. Visa Pay By Link is
  built against the sandbox and hasn't been run yet.
- **"Do you post to Craigslist automatically?"** Never. Craigslist bans it. We write the post, a
  human pastes it, and Craigslist's own email relay brings the buyers to the agent.
- **"Why an agent per item instead of a database row?"** Because the minimum has to live
  somewhere no buyer can reach. One agent holds it, answers ASI:One, `@yardsale`, and Craigslist
  email with the same rules, and the hosted ones get their own Agentverse profile. Our server ran
  forty of them in one test.
- **"Another marketplace with no users?"** Supply comes from Craigslist on day one, demand from
  the Deal Finder on day one. Yardsale's own listings grow in between. Next: import a seller's
  existing eBay and Facebook listings so their whole closet gets agents.
- **"It saves the seller a minute."** The photo saves a minute. The week after is the product:
  the DMs, the lowballs, being online at 11 PM. The agent does the week.
- **"How do you make money?"** Five percent of every sale we collect payment for, taken before
  the seller's payout. Pricing a photo is free. The Deal Finder is free for now and costs us
  about a dime a search.
- **"How many agents?"** Seven services on Agentverse, plus one per listed item. We didn't pad
  it.
- **"Will Craigslist sellers mind an AI emailing them?"** Every email says it's the buyer's AI
  assistant. One thread per post the buyer chose, four offers at most, and the buyer pastes the
  address themselves.
- **"What's next?"** Listing on the seller's own eBay with the same agent answering Best Offers,
  built and waiting on eBay's developer keys. Hosting every item agent on Agentverse. A real
  payout rail.
- **"Why Fetch Business?"** Because the buyer's AI should find a verified `@yardsale`, not a
  random address. It's the front door; the agents behind it are the product.

## Never say

- That ASI:One reads, sees, or understands the photo. Claude does.
- "Real money", "real payment", or "we charge the card". Test mode.
- "Twenty seconds." A photo takes under a minute.
- "We scrape eBay / Craigslist / Facebook." Apify Actors fetch listings; Yardsale fetches no pages.
- "We post it to Craigslist." The seller does.
- Any number that isn't on a screen or in the README.

## Devpost blurb

**Tagline:** Every object you own gets its own agent. It knows your price, it knows the market,
and it never gets tired of "is this still available?"

**What it does.** Sellers photograph their things in ASI:One (up to 8 per message) and say their
minimum. Claude identifies each item, the Researcher prices it from 90 days of eBay sold listings
(Apify), the Market Scout adds what the same thing is asking right now on Craigslist, OfferUp,
and Facebook Marketplace nearby, and plain code sets the asking price and the floor. If the
minimum is far off the market, Yardsale says so with the numbers before anything is listed. On
approval the item becomes its own agent on Fetch's Almanac or Agentverse, holding the minimum
and haggling with every buyer, from ASI:One or from a Craigslist post by email, citing the market
and never revealing the floor. Buyers tell `@yardsale`, Yardsale's Fetch Business agent, what
they want and their max; the Deal Finder searches eBay, Craigslist, OfferUp, and Facebook
Marketplace at once, scores every listing against what it actually sold for, explains each pick,
and can email a Craigslist seller to negotiate inside the max.

**How it's built.** Seven uAgents on Agentverse (Orchestrator, Identifier, Researcher, Appraiser,
Market Scout, Deal Finder, Shopper) in one Bureau, plus an agent per listed item, speaking the
Agent Chat Protocol with Interactive Cards in every reply. `@yardsale` on Fetch Business calls
them through an OpenAPI Business Tool. Claude reads photos and judges which sales match;
`asi1-mini` reads typed text; Apify Actors supply every price; Stripe test mode takes payment
with a 5% commission; SQLite keeps state across restarts.
