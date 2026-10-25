# Yardsale: the agent team that prices your stuff, sells it for you, and finds you the best deal

You can't answer 200 DMs during finals. Your agents can. Photograph what you're selling and say
your minimum in one ASI:One conversation, and seven Fetch.ai agents price it from what it actually
sold for on eBay and give it its own agent that haggles with every buyer, never below your minimum.
Say what you want and your max, and they search four marketplaces and tell you why each listing is a
deal. Every price has real sales behind it.

## 🔗 Try it

- **ASI:One:** chat with the [Yardsale Orchestrator](https://agentverse.ai/agents/details/agent1qtm5uclxjx5ye4fhtjcqcc2d4vh5aucary75dn90lwq7808uyzvt2std9nj/profile) (`@ys-orchestrator`): attach a photo and say "sell this, at least $40"
- **Fetch Business:** ask [`@yardsale`](https://asi1.ai/ai/yardsale) "find me a mini fridge, max $60"
- **Demo video:** [YouTube link]
- **GitHub:** https://github.com/kevinjylin/Yardsale

Say "hi" and you get the home menu:

> 📸 **Sell something** · 🔍 **Find a deal** · 🗂 **My listings** · 🧾 **My orders** · 👀 **Watches** · ✉️ **Haggle on Craigslist**

## 🤖 7 agents on Agentverse

| Agent | Job | Address |
| --- | --- | --- |
| **Orchestrator** (`@ys-orchestrator`) | Runs the ASI:One chat, every card, search, offers, checkout, and Craigslist email, and starts an agent for each approved item | [agent1qtm5…std9nj](https://agentverse.ai/agents/details/agent1qtm5uclxjx5ye4fhtjcqcc2d4vh5aucary75dn90lwq7808uyzvt2std9nj/profile) |
| **Identifier** (`@ys-identifier`) | Claude works out what's in the photo and writes an eBay search for it | [agent1q2ef…97x60k](https://agentverse.ai/agents/details/agent1q2efftklrnnu6z95xu43lxj4gk8jn94hjhap3n90mvfrf8t8n29mz97x60k/profile) |
| **Researcher** (`@ys-researcher`) | Finds what that product sold for on eBay in the last 90 days, through Apify | [agent1q0el…wefvm2](https://agentverse.ai/agents/details/agent1q0eltxwa77247ajr0ckhdmxgjks3r0r07k5ugypdsrg87zlau38rywefvm2/profile) |
| **Market Scout** (`@ys-scout`) | Finds what similar items are asking right now on Craigslist, OfferUp, Facebook Marketplace, and eBay | [agent1qt80…5aswtq](https://agentverse.ai/agents/details/agent1qt80yx8uhmx63zmjg22gaa958llaq24qpfp67xq0x0karj4n55fdq5aswtq/profile) |
| **Appraiser** (`@ys-appraiser`) | Claude marks which sales are the same product; code sets the asking price and the floor | [agent1q04t…wdxx5k](https://agentverse.ai/agents/details/agent1q04thzahn3fg54reqd26xlaqq2nqzr42x4992sx0xhvwjfrkz3dq6wdxx5k/profile) |
| **Deal Finder** (`@ys-dealfinder`) | Searches four sites for a buyer and scores every listing against the eBay sold price | [agent1q0n2…tqyjkd](https://agentverse.ai/agents/details/agent1q0n2cws3nm9lrg4332jfl90asxvmrr0zmnzn69yhptaw2y6jds92wtqyjkd/profile) |
| **Shopper** (`@ys-shopper`) | The buyer agent: when a buyer asks, haggles with several item agents at once, never above their max | [agent1qgf2…y7lnqs](https://agentverse.ai/agents/details/agent1qgf2ezv39kw6gtxjdcv9lltyxgldvk6xr7uxzlfe9gk2wpcyv8fa2y7lnqs/profile) |

Each one is a real uAgent with its own seed, address, Agentverse profile README, and published
protocol. Every approved listing also gets its own **item agent**, which holds the seller's private
floor and haggles with every buyer. The first few are hosted on Agentverse with their own profile
page; the rest run in our server, with no cap.

## 💡 Inspiration

Moving out and moving in are a pile of small sales nobody has time for.

Who answers the DMs? It's the week before finals, and the mini fridge, the desk, the bike, and the
lamp all have to go. You post them and 200 DMs come in: "Is this still available?" "Would you take
$10?" You can't answer them and study, and if you're flying home out of state, whatever's left goes
in the dumpster.

What's it actually worth? Sellers guess, and they guess low. In our own testing, a seller set a $40
minimum on shoes that sell for about $190, and the first offer took it.

Is $60 a good price? The same mini fridge is on eBay, Craigslist, OfferUp, and Facebook Marketplace
at four different prices, and nothing tells you whether $60 is a steal or a rip-off.

Marketplace apps solve one side and wait for the other to show up. We wanted agents that are useful
on day one, on both sides, and that never ask you to take a price on faith.

## ⚡ What it does

Two products, one set of agents, all inside the ASI:One chat. Every reply is a card with a picture
and buttons, and a tap does exactly what the typed command does, with the same checks.

### 📸 Selling

Attach up to 8 photos and type what you want: "sell this, at least $40, small dent."

1. `asi1-mini` reads your prices and notes from what you typed.
2. **Identifier** (Claude) names each item and writes an eBay search for it.
3. **Researcher** finds up to 20 sales of it on eBay from the last 90 days, while **Market Scout**
   finds what the same thing is asking right now on Craigslist, OfferUp, Facebook Marketplace, and
   eBay, both through Apify.
4. **Appraiser** (Claude) marks which sales are the same product. Lots, bundles, parts, and
   accessories don't count.
5. Code, not a model, sets the price: the asking price is the median of those sales, and the floor
   is the higher of the lower quartile and 70% of the median.

You get a proposal with the three closest sales, what similar ones ask now, and a fair range. If
your minimum is 35% or more off what the item sells for, it says so, with the numbers and a
suggested price. Your price still wins. Nothing is for sale until you tap **Approve & list**.
Approving starts the item's own agent, and the reply gives its address.

If no sales are close enough, Yardsale doesn't guess. It asks for your price.

### ✉️ Craigslist, answered by the item's agent

Say `craigslist` and Yardsale writes the post. Craigslist bans automated posting, so you paste it
yourself, with Yardsale's inbox as the email. Buyers' replies come through Craigslist's own relay,
and every 10 seconds the Orchestrator matches each one to its post and hands it to the item's agent.
The agent answers in the buyer's thread with a counter and a reason, never the floor. When the buyer
says "deal", the reply says where to meet, and they pay you in person. Ask "any buyers?" in the chat
to see who's emailing and what the agent wrote back.

With campus mode on, a new chat starts with "Are you a student?", listings stay within your school,
and **Where to meet** lists public spots on your campus, each with a map link, never a dorm.

### 🤝 Haggling that never shows the floor

The rules live in one standard-library file, so a hosted item agent runs exactly the same code as
one in our server.

- Any offer at or above the floor is agreed. Nothing is sold until the buyer accepts, so a buyer can
  haggle with several items and buy one.
- Below the floor, counters come down each round but stop a private gap short of it, set per item.
  No counter shows the floor, even to someone who knows these rules.
- An offer under half the asking price moves nothing, and counters never go back up.
- Every counter cites the market ("similar ones sold for about $340 on eBay recently"), never the
  floor.

### 🔍 Buying

Say what you want and your max: "find me the best deal on a road bike under $300."

1. Yardsale's own listings come first.
2. **Deal Finder** searches eBay, Craigslist, OfferUp, and Facebook Marketplace through Apify, near
   the place you name ("in Berkeley").
3. It looks up what that item sold for on eBay in the last 90 days, the same sales that price
   sellers' items.
4. Code scores every listing: price against the typical sold price first, then quality (condition,
   photos, seller rating, age), then fit. Parts, repairs, lots, and accessories sink, and the same
   item on two sites shows once.

You get the top five under your max, each with its reason: "$180 on Craigslist, 42% under the $310
typical sold price, like new, 6 photos." `watch for it` keeps checking every 6 hours for a week.

Pick a Craigslist post, tap Reply on it, and paste the relay address into the chat. Yardsale's buyer
agent emails the seller and negotiates within your max: at most four offers, never the max in an
email, and every email says it's from your Yardsale assistant, an AI agent.

### 💳 Paying (test mode)

Yardsale is the merchant for sales made in ASI:One, on Fetch's Agent Payment Protocol.

1. A deal closes, and the Orchestrator makes one payment link with Stripe Checkout in test mode.
2. An agent buyer gets a `RequestPayment` with the link; a chat buyer gets a **Pay** button.
3. The Orchestrator never takes the buyer's word for it. It asks Stripe itself, and checks pending
   payments every minute.
4. Once paid, it records Yardsale's 5% commission and the seller's payout, which the seller sees
   under **My listings**.

Live keys are refused in code: **no real money moves**. Craigslist deals end at the meetup, paid to
the seller in person. Visa Pay By Link is built for Visa's sandbox and hasn't been run yet.

### 🏪 `@yardsale` on Fetch Business

`@yardsale` is Yardsale's Fetch Business agent and the buyer's front door. Its OpenAPI tools call
our HTTP API: search Yardsale's listings, get the Deal Finder's scored deals, check what something
sold for, and start or follow a Craigslist negotiation. Sellers' photos go to `@ys-orchestrator`,
because a photo has to reach Claude.

## 💰 Business model

Pricing a photo is free; that's what brings sellers in. Yardsale keeps 5% of each sale it collects
payment for, before the seller's payout, so sellers keep 95%. Buyers search for free: one search
costs us about $0.03 to $0.05 per site on Apify, and results are cached for 10 minutes. Fetch
Business gives brands a verified agent; Yardsale gives everyone else one, for each thing they own.

What runs today is the 5% commission, in Stripe test mode. Craigslist sales are paid in person and
carry no fee, and a fee for the Deal Finder is a plan, not something we've built.

## 🏗 Architecture

```text
  YOU                 ORCHESTRATOR                 AGENTS                      LIVE SOURCES
  ───                 ────────────                 ──────                      ────────────
                                              ┌──▶ ① Identifier ─────────────▶ Claude reads the photo
  ASI:One ─────────▶  Orchestrator (uAgent)   ├──▶ ② Researcher ─────────────▶ eBay sold, 90 days (Apify)
  (Chat Protocol)     · cards + proposals     ├──▶ ② Market Scout ───────────▶ Craigslist · OfferUp · FB · eBay
  @yardsale ───────▶  · market view + advice  ├──▶ ③ Appraiser ──────────────▶ Claude picks matching sales
  (OpenAPI tools)     · search + offers       ├──▶ Deal Finder ──────────────▶ 4 sites + eBay sold (Apify)
  Craigslist ──────▶  · email negotiator      ├──▶ Shopper ──▶ item agents (when a buyer asks)
  (email relay)       · checkout ─────────────┼──────────────────────────────▶ Stripe (test mode)
                             │                └──▶ Item agents, one per listing (Bureau or Agentverse)
                             ▼
                      SQLite (listings, offers, deals, sessions)
```

How a photo becomes a sale:

1. The photo and "sell this, at least $40" reach the Orchestrator over the Agent Chat Protocol. The
   photo is downloaded (public addresses only, 25 MB cap), turned upright, and shrunk.
2. The Orchestrator asks the Identifier, then the Researcher and the Market Scout at once, then the
   Appraiser, posting each request to that agent's Agentverse mailbox. A request that hasn't arrived
   in 8 seconds goes in memory, and each agent handles only the first copy of a request ID.
3. Code sets the price from the Appraiser's matches, and the Orchestrator adds the Scout's asking
   prices for the fair range and the price warning.
4. Approval starts the item agent before marking the listing active, so a crash leaves a proposal,
   never a live listing without an agent. An item's seed comes from its ID, so a restart brings every
   agent back at the same address.
5. Every buyer, from ASI:One, `@yardsale`, or a Craigslist email, reaches the same item agent with the
   same `Offer`. Each sale is a conditional update in a transaction, so an item sells at most once.

## 🏁 How Yardsale meets the Fetch.ai judging criteria

| Criterion | What Yardsale does |
| --- | --- |
| **Multi-agent collaboration** | 7 uAgents on Agentverse, each owning one job, plus one agent per listed item. Each request goes through the agent's own Agentverse mailbox. Not one chatbot wearing seven hats. |
| **ASI:One + Chat Protocol** | Selling, searching, and haggling all happen in one ASI:One conversation. "Sell this, at least $40" ends in a live agent with an address; "find me a mini fridge, max $60" ends in scored deals or an offer emailed to a real seller. ASI:One's planner routed a buyer to the Orchestrator without an @mention. |
| **Payment Protocol** | The Orchestrator sends agent buyers a `RequestPayment`, checks every payment with Stripe itself, and records a 5% commission. A $295 test deal was paid and marked paid by the server's own check. Test mode only. |
| **Interactive cards** | Every reply is a picture card: the home menu, proposals with the sales behind the price, forms, carousels of deals, the haggle rounds, and campus meeting spots. Buttons can open links. |
| **Real-time data** | eBay sold prices from the last 90 days price every item and score every deal. Live asking prices from four sites through Apify. Craigslist emails read every 10 seconds. |
| **Error recovery** | A failed pricing step says why in plain words. A request Agentverse doesn't deliver goes in memory. A quiet hosted item agent is answered for with the same rules. A restart brings back every item agent. |
| **Safety** | Floors never reach a buyer. Maxes and message text stay out of logs, and session IDs are stored hashed. Live payment keys are refused. Photo downloads are public-address-only, redirect-vetted, and size-capped. |
| **Fetch Business** | `@yardsale` calls the same agents through OpenAPI tools: search, price check, and Craigslist negotiation. On October 24 it haggled with a hosted item agent to an agreed $270 in 22 seconds. |

## 🎨 Cards that look like a yard sale

Every card has its own flat illustration, drawn in code (`scripts/make_card_art.py`) with no words
in it and served from the repo, so Yardsale never fetches an image for a card. Selling is warm
orange, buying teal, money green, and warnings amber. The menu puts the two products side by side as
picture tiles, with the rest as buttons two to a row, each with its status ("My listings · 2 for
sale"). A listing shows the closest same-product eBay sale's photo, labeled as such for buyers.

## 🛠 How we built it

- **Fetch.ai uAgents:** seven agents and every item agent in one `Bureau` on one port, all connected
  to Agentverse as mailbox agents. Item agents join the Bureau while it runs.
- **Agent Chat Protocol** from `uagents_core.contrib.protocols`, with prompt acknowledgements and
  cards built as `TextContent` plus `MetadataContent`, checked against our own `card_schema.py`.
- **YardsaleHaggle 1.0.0:** our own published protocol (`Offer`, `Counter`, `AcceptCounter`,
  `Deal`), so any agent can take the buyer's seat.
- **Agentverse:** each request posted to the agent's mailbox, with an in-memory fallback; item agents
  created through the Hosting API at approval, with the floor as a secret, and deleted once sold.
- **Agent Payment Protocol** from `uagents_core.contrib.protocols.payment`, with **Stripe** Checkout
  in test mode, verified by the Orchestrator.
- **Fetch Business:** `@yardsale` with Brand Studio, a skill, and an OpenAPI Integration pointing at
  our aiohttp API through a Cloudflare tunnel.
- **Claude:** reads photos, judges which eBay sales match, and names each campus's meeting spots.
  It never sets a price; that's code, in integer cents.
- **ASI:One `asi1-mini`:** reads what people type (prices, notes, what a buyer wants), and drew every
  agent's profile picture.
- **Apify Actors:** `caffein.dev/ebay-sold-listings` for sold prices, and
  `charitable_trousseau/ebay-search-scraper`, `logiover/craigslist-scraper`,
  `igolaizola/offerup-scraper`, and `apify/facebook-marketplace-scraper` for live listings.
- **Craigslist email** over IMAP and SMTP through Craigslist's relay, **Google Places** for campus
  meeting spots, and **SQLite** for every listing, offer, deal, and session.

The test suite runs the real agents in one process with fake Claude, Apify, ASI:One, mail, and
payment answers. All 1,100-plus tests fail if anything connects beyond localhost, so no test can
spend money or reach Fetch's servers.

## 🧗 Challenges we ran into

- **ASI:One's planner gives up after about 60 seconds, and pricing a photo can take longer.** So the
  Orchestrator acknowledges every message at once, answers within 40 seconds with the result or
  "still working," and sends the result as a second message.
- **Lowballs revealed the floor.** In the first build, a $1 offer got a counter at exactly the floor.
  We rebuilt haggling so counters stop a private gap above it, lowballs get no concession, and
  counters never go back up.
- **Agentverse couldn't see our agents talking.** Every agent runs in one server, so uAgents handed
  messages between them in memory, and the helpers' profiles showed 0 interactions. Now every request
  goes through the agent's Agentverse mailbox, with an in-memory fallback and duplicate protection.
- **Craigslist's relay drops things without telling you.** It delivered our counter but silently
  dropped the reply with a Stripe link, so a Craigslist deal now ends with where to meet. Buyers also
  type their own subjects ("Waterbottle" for a ThermoFlask), so emails match by the post link in the
  relay's footer instead.
- **Cards rendered differently than the docs said.** The real ASI:One app showed our home menu as one
  long column and kept only the last card of a message. We read asi1.ai's own card code, rebuilt
  every card for how it lays them out, and wrote our own schema, because the Python package rejects
  fields the app reads.
- **Apify's free plan runs 5 Actors at once.** A buyer's search can use all 5, and a seller's photo
  failed with Apify's raw error while one ran. Every run now waits for a slot, with one kept for eBay
  sold prices.
- **Building the buyer side twice.** Our first buyer product was agent-to-agent buying: the Shopper
  haggling with our own item agents and paying. It ran live, but a buyer gets more from a finder that
  covers the whole market than from haggling over our few listings. So we rebuilt the buyer side
  around the Deal Finder, and the Shopper now haggles only when a buyer asks.

## 🏆 Accomplishments we're proud of

- **Priced from real sales, in the real app:** a photo of Air Jordan 4s from the ASI:One app, priced
  from 20 eBay sales of the same product, with the Approve card showing in the app.
- **A Craigslist post that answers its own email:** a buyer's emailed offer came through Craigslist's
  relay, and the item agent's counter went back into the buyer's thread.
- **An agent that creates itself on Agentverse** at approval, with its own profile page, in about 3
  seconds. Two hosted test bikes got public profiles, haggled, and sold.
- **A payment you can't fool:** a $295 deal paid with Stripe's test card and marked paid by the
  server's own check within a minute, commission recorded. A buyer saying "paid" marks nothing paid.
- **Haggling you can publish:** the rules are one standard-library file, the protocol is public, and
  no counter ever shows the floor.
- **Honest by design:** every price has real sales behind it, code sets every number, and nothing is
  listed until the seller approves it.

## 📚 What we learned

- The price is the product. A number with the three closest sales behind it earns trust that a
  confident guess never does, for the seller and the buyer alike.
- Keep money rules out of the model. Claude decides which sales are the same product; code sets every
  price, and commands that move money (`approve`, `accept`) are read by code, never a model.
- One agent per item pays off when the agent holds a secret. Every buyer, from any channel, meets the
  same agent and the same rules, so the floor never shows.
- Channels you don't own drop things without telling you. Testing on real Craigslist early showed us
  the relay eats links, so deals end in the one thing that always gets through: where to meet.

## 🚀 What's next for Yardsale

- **Sell on the seller's own eBay:** built and tested against a stand-in, with Best Offers answered by
  the same haggling rules, waiting on eBay developer keys.
- **Always-on hosting:** run the agents on a managed host with a fixed public URL for `@yardsale`, so
  listings answer around the clock.
- **More sources and better comps:** our own `resale-comps` Actor on the Apify Store once it has a
  proxy eBay accepts, and more local sites.
- **Real money:** Visa Pay By Link and automatic payouts to sellers, once we leave test mode.

## 🔗 Additional links

- **ASI:One chat sessions:** selling [shared chat link] · buying [shared chat link]
- **Fetch Business:** [`@yardsale`](https://asi1.ai/ai/yardsale)
- **Agentverse profiles:** [Orchestrator](https://agentverse.ai/agents/details/agent1qtm5uclxjx5ye4fhtjcqcc2d4vh5aucary75dn90lwq7808uyzvt2std9nj/profile) · [Identifier](https://agentverse.ai/agents/details/agent1q2efftklrnnu6z95xu43lxj4gk8jn94hjhap3n90mvfrf8t8n29mz97x60k/profile) · [Researcher](https://agentverse.ai/agents/details/agent1q0eltxwa77247ajr0ckhdmxgjks3r0r07k5ugypdsrg87zlau38rywefvm2/profile) · [Market Scout](https://agentverse.ai/agents/details/agent1qt80yx8uhmx63zmjg22gaa958llaq24qpfp67xq0x0karj4n55fdq5aswtq/profile) · [Appraiser](https://agentverse.ai/agents/details/agent1q04thzahn3fg54reqd26xlaqq2nqzr42x4992sx0xhvwjfrkz3dq6wdxx5k/profile) · [Deal Finder](https://agentverse.ai/agents/details/agent1q0n2cws3nm9lrg4332jfl90asxvmrr0zmnzn69yhptaw2y6jds92wtqyjkd/profile) · [Shopper](https://agentverse.ai/agents/details/agent1qgf2ezv39kw6gtxjdcv9lltyxgldvk6xr7uxzlfe9gk2wpcyv8fa2y7lnqs/profile)
- **GitHub:** https://github.com/kevinjylin/Yardsale

## Built with

agentverse · aiohttp · apify · asi:one · claude · cloudflared · fetch-business · fetch.ai ·
google-places · pytest · python · sqlite · stripe · uagents · visa-acceptance
