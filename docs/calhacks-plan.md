# Cal Hacks 13.0 plan

Written on the night of Friday, October 23, 2026. Cal Hacks 13.0 runs October 23–25 in San
Francisco, and **submissions close Sunday, October 25 at 9 AM PT**. This page covers the event's
rules, Fetch's track rules, the pitch, what Fetch Business offers, how far the code is from the
pitch, and the build order. [background.md](background.md) has the product decisions and how the
code works.

## The event

From the [Cal Hacks 13.0 Devpost](https://cal-hacks-13-0.devpost.com/):

- The [submission window](https://cal-hacks-13-0.devpost.com/details/dates) opens Friday,
  October 23, 2026 at 6 PM PDT and closes Sunday, October 25 at 9 AM PDT.
- Be at the venue by Sunday 8 AM PT. Submit by Sunday 9 AM PT, with teammates. The deadline is
  hard.
- The submission needs an image of the project and the GitHub repository.
- All work must be done during the hackathon.
- Each team gets 3 minutes to pitch and 2 minutes of judges' questions. The demo can be live or
  pre-recorded.
- Sponsors judge from the Devpost video, so it has to show the whole flow on its own.

**Judging criteria** (the same for every track on Devpost):

| Criterion | What judges check |
| --- | --- |
| Application | Could someone realistically adopt it, and would it clearly help? Who it helps, what problem it solves, why people would use it. |
| Intentionality | An intuitive, engaging experience for the target user. Every design choice has a reason. |
| Functionality | Works as intended, with a smooth live demo and a clear video on Devpost. |
| Creativity | Original framing, unexpected combinations of ideas or technologies, clearly different from similar work. |
| Technical complexity | Command of the frameworks, sound architecture, engineering depth beyond the basics. |

**Tracks we're entering:**

| Track | Prizes | Notes |
| --- | --- | --- |
| Fetch.ai: Best Use of Fetch.ai | $1,500 / $1,000 / $500 | Rules below. |
| Visa: Best use of Reimagining Shopping | $5,000 | Devpost gives no description. Ask the Visa team what they judge on. |
| Apify: Best use of Apify | $2,000 / $1,000 / $800 / $400 / $400 | |
| Apify: Best Actor | $400 | For an Actor we publish to the Apify Store. |

Not entering: Microsoft (Built for the Real World, $3,800) and the other sponsor tracks. Each one
would add a piece the demo doesn't need.

## Fetch's track rules

From Fetch's Cal Hacks hackpack. The challenge is "ASI:One Agent Challenge – From Intent to
Action": an agent that ASI:One can find, that understands what the user wants, and that takes
real action. It should be more than a chatbot or a thin wrapper around an API. The hackpack
links a [submission document](https://docs.google.com/document/d/1UDW-X1C24hxZviFOQzjTeh0pXRNAoflMb8lhJqZP9Z0)
(needs a Google sign-in), summarized under "The ASI:One Submission Agent" below.

**Required to win anything:**

| Requirement | Yardsale today |
| --- | --- |
| At least one agent registered on Agentverse | Yes: the Market, Identifier, Researcher, and Appraiser are active mailbox agents on Agentverse (checked October 23). The Shopper (buyer agent) joins them on the next live start, and item agents can be hosted there. |
| Implements the Agent Chat Protocol | Yes, the Market. |
| Discoverable and directly usable through ASI:One | Buying: yes, without an @mention (ASI:One's planner picked the Market on October 24). Selling: by @mention; the planner tried to price items itself. |
| Meaningful tool use or multi-agent orchestration | Yes: three pricing agents, Apify, Claude, an agent per item, and a buyer agent haggling with several item agents at once. |
| The main workflow happens entirely inside an ASI:One conversation, with no custom frontend | Selling, haggling, and buying happen in the chat; payment is a link from the chat. |
| Public GitHub repo with instructions to run or test it | The repo is public and the README has run instructions. |

**Bonus points:**

| Bonus | How Yardsale gets it |
| --- | --- |
| Effective multi-agent collaboration | The pricing agents; a buyer agent haggling with several item agents at once over a published protocol (code done October 24). |
| Payment Protocol with a credible monetization model | The Market sends agent buyers a Payment Protocol `RequestPayment` with the payment link; Yardsale keeps a 5% commission on each sale (test mode; a Stripe test payment went through end to end on October 24; Visa not yet run). |
| Interactive Cards | Approve card (seen in ASI:One); sales carousel, seller prices form, haggling card, and payment card (code done October 24, not yet seen in ASI:One). |
| Reliability and recovery from failed tool calls | Failed pricing steps return a reason, a resent message gets the same answer, restarts bring every active item agent back, and the server starts even when Agentverse drops the connection. |
| Creative use of real-time data and external services | Live eBay sold listings through Apify. |
| Could keep running after the hackathon | Saved state in SQLite; needs a hosted server. |

**What to submit for Fetch:**

- The Devpost submission, and also a registration through the Cal Hacks ASI:One Submission Agent
  (steps below).
- A demo video of 3 to 5 minutes. The live pitch is 3 minutes, so the Devpost video can run
  longer.
- The README lists each agent's name and address, and links any extra resources needed to run
  it (API keys and accounts). Done: the README's [Agents section](../README.md#agents).
- Every agent's README carries both badges, so it's filed under Innovation Lab. Done for the
  repo README and the four Agentverse profile files in [yardsale/readmes/](../yardsale/readmes/).
  The profiles on Agentverse pick up the badges the next time each agent connects: start the
  live server with `AGENTVERSE_API_KEY` set, or click Connect in each agent's Inspector link.
  Check the profile pages afterwards.

```markdown
![tag:innovationlab](https://img.shields.io/badge/innovationlab-3D8BD3)
![tag:hackathon](https://img.shields.io/badge/hackathon-5F43F1)
```

**The ASI:One Submission Agent**, from Fetch's
[submission document](https://docs.google.com/document/d/1UDW-X1C24hxZviFOQzjTeh0pXRNAoflMb8lhJqZP9Z0)
and [video](https://drive.google.com/file/d/1EDfKrM7l8soO-40eKkPYDAEkRUTwQKyf):

1. **Get the links ready.** The form needs the Devpost project URL and the demo video URL, so
   create the Devpost entry and upload the video first.
   - *ASI:One shared chat URLs* (bonus): open the chat where the agent is demoed on
     [asi1.ai](https://asi1.ai), click the search icon in the left sidebar, then **Share chat**
     under Current chat. Links look like `https://asi1.ai/shared-chat/...`. Share both the
     selling chat and the buying chat.
   - *Agentverse profile URLs* (bonus): `https://agentverse.ai/agents/details/<address>/profile`
     for each agent. The README's [Agents section](../README.md#agents) links all four.
2. **The team lead creates the submission.** In ASI:One, message the Cal Hacks ASI:One
   Submission agent ("Hi" works) through the
   [festival dashboard](https://asi1.ai/auth/signup?returnTo=%2Ffestival%2Fcalhacks2026%2Fdashboard%3Futm_source%3Dcalhacks2026).
   Click **Create team (I'm the lead)** and fill in: project name, table number (optional), your
   name, your email, team size including you (1–4), what problem the project solves, the public
   GitHub URL, the Devpost URL, and the demo video URL. For bonus points, also give the number of
   agents and their profile URLs, and the number of shared chats and their URLs (comma
   separated). Click **Continue**, check the review, and click **Confirm submission**.
3. **Share the Team ID** (it looks like `lahacks-ce25ed57`) with teammates.
4. **Each teammate joins.** In ASI:One, message the same agent, click **Join with Team ID**,
   enter the ID, then their name and email, and click **Join team**.
5. **Check the status.** "Incomplete" means teammates still have to join. "Submitted" means
   everyone has joined and it's done. The lead can click **Edit details** any time before the
   deadline.

**What this changes in the plan:**

- **No custom frontend.** The haggling has to show inside the ASI:One chat, not on a separate
  web page. Cards can't update in place (each update is a new message), so the haggling result
  arrives as one card showing every round.
- **Payment as a link in the chat.** Fetch's own Stripe example says plain ASI:One chat "often
  will not show" its embedded checkout card. A payment link in the chat doesn't depend on that,
  and Visa's Pay By Link is exactly that.
- **Fetch Business isn't in the rules.** The required pieces (Agentverse, Chat Protocol, ASI:One)
  come first. Fetch Business is the extra that sets the pitch apart. Few projects use it, so a
  working `@yardsale` stands out, but nothing else in the pitch should depend on it.

## The pitch

Decided October 24: Yardsale is two products, one for sellers and one for buyers. Anything else
built this weekend either serves one of them or is listed under "Also built" below.

**One line:** Photograph your stuff, set your minimums, and agents sell it for you. Say what you
want and the most you'll pay, and an agent finds it and haggles for you.

**Product 1: sellers.** A student is moving out of their apartment right before finals.
Everything in the dorm has to go, and posting it all brings 200+ DMs to answer ("is this still
available?", "would you take $10?") while they study, and it's worse when they're flying home
out of state. With Yardsale they photograph each item and say their minimum ("sell this, at
least $40"). Claude identifies it, eBay sales from the last 90 days set a suggested price, and
once the seller approves, the item gets its own agent that knows the minimum. The listing is on
ASI:One and `@yardsale`, and Yardsale writes a Craigslist post for the seller to paste. Every
buyer, in ASI:One or by email from the Craigslist post, haggles with the item's agent, which
never goes below the minimum and never shows it.

**Product 2: buyers.**

1. **Yardsale listings.** A buyer asks ASI:One or `@yardsale` for something, with a max.
   Yardsale's buyer agent haggles with every matching item agent at once, and the buyer pays
   through a link.
2. **The deal finder.** Most of what a buyer wants won't be on Yardsale. So the buyer writes what
   they want and the most they'll pay, and the agent searches eBay, Craigslist, and OfferUp for
   the best deals under that max. For a Craigslist post the buyer picks, the agent emails the
   seller and negotiates the price for them.

**What's built** (October 24, evening):

| Piece | Product | Status |
| --- | --- | --- |
| Photo → item identified (Claude) and priced from eBay sold listings (Apify) | Sellers | Built. Run live from the ASI:One app October 23–24; one photo took 36 seconds. |
| Several photos in one message | Sellers | In progress. Today only the first photo in a message is priced. |
| Minimum read from the seller's own words (`asi1-mini`) | Sellers | Built |
| Approve → item agent with its own address | Sellers | Built. Hosted on Agentverse with its own profile, live October 24. |
| Craigslist post text (`craigslist`), pasted by the seller | Sellers | Built |
| Item agent haggles with Craigslist buyers by email, payment link on a deal | Sellers | Built; tested with a fake inbox only, not yet run against real Craigslist or Gmail |
| Search, haggle, and buy Yardsale listings in ASI:One | Buyers 1 | Built. Live October 24, including through ASI:One's planner without an @mention. |
| The same through `@yardsale` on Fetch Business | Buyers 1 | Built. Live October 24 through a Cloudflare tunnel (Plus trial). |
| Payment link, 5% commission | Both | Built for test mode. A Stripe test payment went through end to end October 24. |
| eBay and Craigslist listings for sale now (Apify Actors) | Buyers 2 | Search code written and tried on real results; not yet reachable from the chat or `@yardsale` (in progress) |
| OfferUp | Buyers 2 | No source yet |
| Standing searches ("watch for ...", every 6 hours for a week) | Buyers 2 | Storage and polling written; no chat commands yet |
| Agent emails a Craigslist seller and negotiates for the buyer | Buyers 2 | In progress |

**Also built / next** (not on either product's main path):

- **Selling on the seller's own eBay** ([ebay_seller.py](../yardsale/ebay_seller.py)): `connect
  ebay`, list on approve, and Best Offers answered with the item agent's rules. Tested only
  against a local stand-in, and blocked on eBay developer keys
  ([ebay-setup.md](ebay-setup.md)). Next once keys arrive.
- **Visa Pay By Link**: built for Visa's sandbox, never run (no keys). Stripe test mode is what
  the demo uses.
- **Orders webhook**: every order status change can be POSTed out, but Fetch Business's Orders
  page takes orders only from Square or SumUp POS.
- **Our own resale comps Actor**: pushed to Apify, but eBay answers 403 through Apify's proxy.

**Why it fits what Fetch is building.** The challenge is "From Intent to Action", and both
products end in action: a sale, or an offer sent to a real seller. One demo shows:

1. Discovery through ASI:One and Fetch Business (`@yardsale`).
2. Agents working out a deal: a buyer's agent and item agents, or an item agent and a person
   emailing from Craigslist.
3. AI payments, through a payment link and the Payment Protocol for agent buyers.

**The marketplace problem.** A new marketplace has no buyers and no listings. Yardsale doesn't
wait for either. A seller's Craigslist post brings Craigslist's buyers to the item agent, and the
deal finder is useful on day one with nothing listed, because it searches where listings already
are.

**How Yardsale makes money.** A commission on each sale Yardsale collects payment for (ASI:One,
`@yardsale`, and Craigslist buyers who pay through the link), taken before the seller's payout:
5%, so sellers keep 95%. Pricing a photo is free, since that's what brings sellers in. The deal
finder has no fee yet; one search costs Yardsale about $0.09 of Apify (eBay about $0.05,
Craigslist about $0.04).

**Who it's for.** Sellers: students moving out, with more stuff than time. Buyers: students
moving in and bargain hunters, who shop by asking ASI:One.

**Earlier work.** Past shopping projects (Shopybara, VShop AI, Revi) were buyer-side helpers
that stopped before the purchase. Agent Santa negotiated between agents but faked payment. DIAL
haggled with humans by phone. Yardsale covers both sides: the inventory is agents, they haggle
with buyers wherever they come from, and the deal ends in a payment link.

**How to frame it:**

- **Two products, one story.** The seller moving out and the buyer looking for a deal. Don't
  present eBay selling, Visa, or the Orders webhook as products.
- **The agent answers the DMs.** Suggested line: "You can't answer 200 DMs during finals. Your
  agents can."
- **One agent per item, many channels.** ASI:One, `@yardsale`, and Craigslist email are just
  where buyers come from. The same item agent answers all of them with the same rules, so the
  minimum never leaves it.
- **`@yardsale` is the front door for buyers.** It's Yardsale's Fetch Business agent, and it
  searches, haggles, and buys through Yardsale's API. Sellers use `@ys-orchestrator`, because a
  photo has to reach Claude. Fetch Business is the reveal, not the premise.
- **The long tail of Fetch Business.** Suggested line: "Fetch Business gives brands a verified
  agent. Yardsale gives everyone else one, for each thing they own."
- **Be exact about models.** Claude reads photos, `asi1-mini` reads typed text, and ASI:One finds
  and routes to the agents. ASI:One's models don't take images, Fetch judges know it, and they
  check the repo and Devpost against what the pitch claims. Never imply ASI:One reads the photos.
- **Be exact about Craigslist.** Craigslist bans automated posting, so the seller posts by hand;
  Yardsale writes the text. Buyers' emails reach Yardsale through Craigslist's relay. Yardsale
  doesn't fetch eBay or Craigslist pages itself: listings come from Apify Actors.
- **Be exact about what's in progress.** Several photos per message, the deal finder in the chat,
  OfferUp, and emailing Craigslist sellers for a buyer aren't done yet. Say so if they aren't
  done by the pitch.
- **Don't dwell on outside services.** Relying on Apify and Claude is fine, as long as the flow
  works inside ASI:One and can be demoed there. Show the flow; explain the services when asked.
- **The agent count.** The submission asks how many agents there are, and a typical entry has
  about ten plus an orchestrator. Ours is five service agents (the Market, Identifier,
  Researcher, Appraiser, and Shopper) plus one per listed item. Don't add agents just to raise
  the number.

## What Fetch Business offers

From [business.fetch.ai](https://business.fetch.ai/), checked October 23:

- "Create a verified business agent that customers' AIs can discover, talk to, and transact
  with." Its four promises are Discoverable, Verified, Connected to live data, and Ready to
  transact ("answer, book, and sell with your rules").
- Its demo: a customer asks ASI:One for running shoes under $150. ASI:One finds verified
  businesses, the customer's AI talks to the Nike agent, and checkout is generated.
- Integrations: Shopify (catalog and inventory), Stripe (checkout links and payments), Square,
  SumUp, SumUp POS, Google Calendar, and "APIs" for custom inventory, booking, and policy data.
- Two ways in: brands claim a verified agent, and businesses or creators build one.

From other sources:

- Fetch Business launched in November 2025 with "Claim Your Agent". A brand verifies, claims a
  handle such as `@Nike`, then connects an existing agent or builds one with low-code tools. The
  agent lives on Agentverse, where ASI:One finds it
  ([VentureBeat](https://venturebeat.com/ai/the-google-search-of-ai-agents-fetch-launches-asi-one-and-business-tier-for)).
- A Fetch Business agent can create Stripe payment links and read a Square catalog
  ([Fetch Business blog, March 2026](https://business.fetch.ai/blog/commerce-capabilities)).
- On December 18, 2025, Fetch announced AI-to-AI payment: ASI:One personal AIs pay on a user's
  behalf with temporary Visa credentials, within spending limits the user sets
  ([Fetch blog](https://www.fetch.ai/blog/world-s-first-ai-to-ai-payment-for-real-world-transactions)).

From the workbench at `business.fetch.ai/workbench`:

- Anyone can sign in with a personal email and create a personal agent. Claiming a verified brand
  handle is a separate step.
- The sidebar (October 24) has Brand Studio, Business Information, Analytics, Services (Artifacts,
  Schedule, Skills), Social Media, Events, Sphere, Integrations, Orders, Messages, and Settings.
- The Orders page lists each order with a status (Pending, Confirmed, Cancelled, Completed,
  Rejected) and can be searched by notes. Its URL names the agent:
  `business.fetch.ai/workbench/orders?agentId=...`.
- A bubble tea shop's ordering agent, connected through Square, has put real orders on that page,
  each with the drink, its options, the customer, and the time.
- A used-item sale maps onto those statuses: Pending when a price is agreed, Confirmed when the
  seller approves, Completed when it's paid.

**What we set up on October 24.** `@yardsale` is a personal Fetch Business agent named YardSale
(handle `yardsale`), with a public page at [asi1.ai/ai/yardsale](https://asi1.ai/ai/yardsale):

- Brand Studio: a description of Yardsale and five starter prompts (finding a deal, selling,
  paying, haggling, pricing).
- Business Information: a Plain Text entry explaining how buying and selling work and naming the
  Market agent, `@ys-orchestrator`, with its address.
- Skills: "Find, haggle for, and buy used items on Yardsale", a playbook that says which
  Yardsale tool to call for browsing, haggling, buying, and checking a payment, never to make
  up listings, and to send sellers (who need to attach a photo) to `@ys-orchestrator`.
- Integrations: an OpenAPI Integration ("Business Tool") named Yardsale Market, pointing at
  Yardsale's HTTP API ([business_api.py](../yardsale/business_api.py)) through a Cloudflare
  tunnel. Fetch made 6 tools from its OpenAPI description (`searchListings`, `startHaggle`,
  `getHaggle`, `acceptDeal`, `getDeal`, and the description itself). It asks the buyer to
  confirm before `startHaggle` and `acceptDeal`.

First, on the free plan, `@yardsale` could only point people to the Market. Before the first
skill it searched the web and made up a "listing sweep". With the skill, it gave the exact
`@ys-orchestrator` message to send, and said it can't message `@ys-orchestrator` itself. Every
integration showed "Upgrade".

Then the Plus plan's 14-day trial was started on October 24 ($75 a month afterwards, so cancel
before November 7 unless it's kept), and the OpenAPI Integration was added. Tested in
`@yardsale`'s own ASI:One chat, with no `@ys-orchestrator`:

- "Find me a used hybrid bike, haggle for me, max $300": it searched, haggled through
  `startHaggle` (Yardsale's buyer agent against a hosted item agent, $221 then $270 agreed, in
  22 seconds), showed both rounds, and offered a "Yes, buy it" button.
- On an earlier haggle that was agreed at $283, "Accept the deal" called `acceptDeal`, which
  closed the deal and made a Stripe test checkout link in 6 seconds.
- The first try failed. Agentverse briefly dropped the buyer agent's mailbox, the haggle ran past
  the 25 seconds `startHaggle` waits, and `@yardsale`'s follow-up checks failed; it told the
  buyer Yardsale's "status service" had an error. Since then a running haggle answers with a
  plain 200 (not 202), and `getHaggle` waits up to 25 seconds for the result.

So `@yardsale` is now a working front door for buyers. Sellers still go to `@ys-orchestrator`,
because a photo has to reach Claude.

Orders: with the trial, the apps (Stripe, Square, SumUp, SumUp POS, E-commerce, Google
Calendar) became toggles that connect an outside account, and orders come from Square or SumUp
POS. Nothing lets an outside API add orders, so Yardsale's sales don't reach the Orders page.

Still to ask the Fetch team: how an outside agent or API adds orders, and whether a Business Tool
can point at a URL that changes (each quick tunnel gets a new one).

## The demo, step by step

Two phones on the big screen: a friend's (the seller) and the presenter's (the buyer). Steps 1–7
are the seller product, and 8–10 are the buyer's deal finder. Selling and asking for things
happen in ASI:One; the Craigslist email is a second channel the same item agent answers.

| Step | Phone | What happens | Sponsor | Built? |
| --- | --- | --- | --- | --- |
| 1. Snap | Seller | The friend attaches a photo in ASI:One (`@ys-orchestrator`) and types "sell this, at least $40". Claude identifies the item; `asi1-mini` reads the minimum. | Claude, Fetch (ASI:One) | Yes, one photo per message. Several photos per message is in progress. |
| 2. Price | Seller | The Researcher finds eBay sold listings through Apify, and code sets the asking price and floor. | Apify | Yes (`caffein.dev/ebay-sold-listings`; our own Actor is blocked by eBay) |
| 3. Approve | Seller | The proposal card shows the sales behind the price. The friend approves, and the item's agent goes live with its address and, when hosted, an Agentverse profile. | Fetch (Interactive Cards, Agentverse) | Yes. Approve card seen in ASI:One; hosting run live October 24. |
| 4. Post | Seller | `craigslist` gives the post text. The friend has already pasted it on Craigslist with Yardsale's inbox as the email; show the live post. | | Yes; posted by hand. Not yet tried on real Craigslist. |
| 5. Email | Buyer | From the presenter's phone, reply to the Craigslist post with a low offer. | | Yes; relay unverified |
| 6. Haggle back | Buyer | The item agent answers in the email thread with a counter that doesn't show the floor. The presenter replies "deal" and gets a payment link. | Fetch (uAgents) | Yes; tested with a fake inbox only |
| 7. Paid | Both | Pay with Stripe's test card. The friend's `my listings` shows the item sold and the payout after 5%. | Stripe (test mode) | Yes; Stripe run live October 24 |
| 8. Ask | Buyer | Ask `@yardsale` (or ASI:One) "find me a mini fridge, max $60". Nothing on Yardsale matches. | Fetch Business, ASI:One | Search yes |
| 9. Find | Buyer | The deal finder lists the best eBay, Craigslist, and OfferUp results under $60. | Apify | eBay and Craigslist searches written, wiring in progress; OfferUp has no source yet |
| 10. Negotiate | Buyer | The presenter picks a Craigslist post, and the agent emails its seller an offer and negotiates. | | In progress |

Before the pitch:

- Make the Craigslist post at least an hour early. Craigslist emails a confirmation link first,
  and how long the relay takes to deliver a buyer's email is unknown.
- Rehearse steps 5–6 on real Craigslist and time them. If the relay is slow, keep a recording of
  them as the fallback.
- The Devpost video can run longer than 3 minutes, so it also shows Buyers 1: a buyer agent
  haggling with item agents in ASI:One and buying, which is proven live.

## How close the code is

State of the code on Saturday, October 24, morning (not yet committed; live runs noted):

| Piece | Status |
| --- | --- |
| Photo → price from sold listings | Built. With real keys, a photo of Air Jordan 4s was priced from 20 matching eBay sales on October 23. Each step's time is logged. |
| One agent per item, with its own address | Built. Registered on the Almanac by default. |
| Item agents hosted on Agentverse | Built: `YARDSALE_HOST_ITEMS=N` hosts up to N through the Hosting API, falling back to the server. Run live October 24: two hosted test bikes got public profiles, haggled, and sold. |
| Seller approval, cards | Built: Approve card, sales carousel, prices form. The Approve card showed in the real ASI:One app. |
| Buyer search and offers in the chat | Built. Live October 24: haggled and bought from a second ASI:One chat, and through ASI:One's planner with no @mention. |
| Haggling within the seller's limits | Rebuilt: several rounds, agree-then-accept, counters stop a private gap above the floor. |
| Buyer agent holding the shopper's max | Built (the Shopper), with a haggling card. |
| `@yardsale` on Fetch Business | Set up October 24: a description, a knowledge entry, a skill, and (on the Plus trial) an OpenAPI Integration with Yardsale's HTTP API, so it searches, haggles, and buys in its own chat. Live October 24 through a Cloudflare quick tunnel. Sellers still go to `@ys-orchestrator`. The Orders page takes orders only from Square or SumUp POS; every order status change can go to `YARDSALE_ORDERS_WEBHOOK`. |
| Payment Protocol, Visa, commission | Built for test mode: Visa Pay By Link sandbox or Stripe test checkout, Payment Protocol for agent buyers, 5% commission. Stripe run live October 24: a $295 deal was paid with a test card and recorded with its commission. Visa not yet run (no sandbox keys). |
| Our own Apify Actor | Written in `actors/resale-comps/` and pushed to Apify (private). eBay answers 403 through Apify's default proxy, so it finds no sales yet. Switching is `YARDSALE_APIFY_ACTOR`. |
| Innovation Lab badges, agent addresses in the README | Done in the repo. Agentverse shows the badges after each agent reconnects. |
| Public URL | `YARDSALE_PUBLIC_URL` exists but hasn't been used. ASI:One reaches the Market through its Agentverse mailbox, and item agents run in the same server or on Agentverse, so the chat flow may not need it; outside agents do. |

The installed cards module has carousel, detail, form, review, and custom cards, so the sales
carousel, rules form, and haggling card need no new library. A tapped button comes back as JSON
text after an @mention, or as plain prose when ASI:One's planner relays it, so both have to be
read.

## Known problems

- **Lowballs revealed the floor** until the October 24 rebuild: a $1 offer used to get a
  counter at exactly the floor. Now counters stop a private gap above it, and lowballs get no
  concession. A buyer making many identities could still narrow a floor down.
- **"20 seconds" is wrong.** A photo took 36 seconds on October 24 (identify 11 s, eBay 12 s,
  appraise 14 s). Say "under a minute" on stage.
- **"Agentverse address".** Only hosted item agents have an Agentverse profile. Hosting worked
  live on October 24, so run the demo with `YARDSALE_HOST_ITEMS` set.
- **Apify credit.** The team has $200 of Apify credit (October 24), about 2,500 photos at
  $0.08 each, so rehearsing with real photos is fine.
- **Accepting through ASI:One's planner** failed until October 24: the planner rewrites
  "accept" as a sentence. Fixed; see [background.md](background.md).
- **Wi-Fi that intercepts HTTPS cuts the server off.** On the morning of October 24, every
  agent's mailbox check failed for a long stretch with "certificate is not valid for
  agentverse.ai" (a network answering for Agentverse with its own certificate, as a sign-in
  page on public Wi-Fi does). While that lasts, ASI:One's messages wait in the mailboxes and
  the chat gets no answer. The server recovered by itself once the network was fixed, but a
  restart during a dropped connection crashed it until a fix the same day. On stage, use a phone
  hotspot or a network already signed in, and check the server log before starting.
- **The Orders page can't show our sales.** On the Plus trial, orders come only from a connected
  Square or SumUp POS account, so the planned closing moment (a sale on the Orders page) isn't
  possible. Show `@yardsale` haggling and buying in its own chat instead.
- **`@yardsale` depends on the tunnel.** Its Business Tool calls Yardsale's API through a
  Cloudflare quick tunnel, which gets a new URL every time `cloudflared` restarts; the Business
  Tool then has to be pointed at the new URL. Keep the tunnel running from before the demo to
  after it, and check `curl <tunnel>/openapi.json` first.
- **The Plus trial ends November 7.** Started October 24; it's $75 a month after that.

## Build order

**Friday night**

1. Ask the Fetch and Visa teams the questions below. Ask about the mechanics (agent slots,
   connecting `@yardsale`, orders) rather than walking them through the whole idea; it lands
   better as a surprise at judging.
2. Go live: restart the server so the four Agentverse profiles pick up the badges, list one real
   photo from the ASI:One app, and make one offer from a second ASI:One account. Then ask
   ASI:One "I want to sell my bike" without an @mention and see whether it picks Yardsale.
   Everything else depends on this.

**Saturday**

3. Host item agents on Agentverse. When a seller approves, create the item agent through
   Agentverse's [Hosting API](https://fetch.ai/docs/apis/agentverse/hosting) (create the agent,
   upload its code, start it), give it its floor as a secret or in its storage, and reply with
   its Agentverse profile link. An agent that spins itself up with a live Agentverse address is
   the strongest single moment in the demo. This reverses decision 3 in
   [background.md](background.md). The free plan allows 8 agents and Premium allows 25, so ask
   the Fetch team for Premium. Without it, host only the demo items on Agentverse and keep the
   rest Almanac-only. Check which Python packages hosted agents can import; the item agent only
   needs the negotiation code. *Done and run live October 24 (`hosting.py`).*
4. Set up `@yardsale` on Fetch Business as the front door, following the Fetch team's answers.
   Check the Integrations tab first, since it's the likeliest way to connect `@yardsale` to the
   Market. Time-box this to a few hours. Fallback: keep the Market as the entry point on
   Agentverse and pitch Fetch Business as the next step. *Done October 24 (see "What we set up on
   October 24" above): on the Plus trial, an OpenAPI Integration with Yardsale's HTTP API
   (`business_api.py`) lets `@yardsale` search, haggle, and buy in its own chat.*
5. Tune the Market's Agentverse profile so ASI:One finds it without an @mention. ASI:One picks
   agents by their Agentverse descriptions, so write for what buyers and sellers actually type.
   Rehearse the exact demo query, and keep the @mention as a fallback. (The badges and the
   README's agent list are done.) *Profile text and description rewritten October 24; they
   upload on the next live start.*
6. Rebuild haggling: a buyer agent holding the shopper's max, haggling with several item agents
   at once over several rounds, with neither side revealing its limit. The result comes back as
   one card showing each round and the best deal. Publish the protocol so any agent can take the
   buyer's seat. This is what makes one agent per item worth having, and it fixes lowballs
   revealing the floor. *Done October 24 (`haggle.py`, `shopper.py`), tested with fakes.*
7. Add payment: on a deal, the buyer gets a Payment Protocol request, and the chat shows a
   Visa Pay By Link (Visa Acceptance sandbox). Once it's paid, mark the item sold and record the
   commission. Fallback: a Stripe test checkout link, which loses the Visa track. *Code done
   October 24 (`payments.py`, `checkout.py`); the Market sends the request, since Yardsale is
   the merchant. Stripe test mode run live October 24; Visa still needs sandbox keys.*
8. Put closed deals on `@yardsale`'s Orders page: Pending when a price is agreed, Confirmed when
   the seller approves, Completed when it's paid. This depends on what step 4 finds, and it's the
   demo's closing moment. *October 24: every status change (pending when agreed, confirmed when
   the payment link goes out, completed when paid) is POSTed to `YARDSALE_ORDERS_WEBHOOK`.
   Fetch Business's Orders page takes orders only from Square or SumUp POS, so this is cut.*
9. Publish our own "resale comps" Actor on the Apify Store, and have the Researcher call it.
   *Written October 24 in `actors/resale-comps/`; publish with `apify push`, run it once, then
   set `YARDSALE_APIFY_ACTOR`.*
10. Add the sales carousel and the seller rules form. *Done October 24.*
11. Measure photo-to-proposal time and cut it down. *Each step's time is logged since October
    24; measure it on real photos.*

**Sunday**

12. Freeze code around 2 AM. Record the 3 to 5 minute video and share the demo chats. Finish the
    Devpost entry (text, image, video), then register through the ASI:One Submission Agent,
    which needs the Devpost and video URLs, and have every teammate join with the Team ID.
    Rehearse. Submit before 9 AM.

If time runs short, cut step 10, then step 9, then step 8. Steps 2, 3, 5, 6, and 7 are the demo
and the required pieces; keep them.

## Pitch outline (draft, 3 minutes)

The big screen shows two ASI:One chats: the seller's and the buyer's.

1. **0:00** A judge attaches a photo of an item in ASI:One. Pricing runs while we talk.
2. **0:15** The problem: moving out, stuff gets thrown away because listing and haggling are
   tedious.
3. **0:45** The gap: Fetch Business makes a business shoppable by AIs, but only if it has a
   catalog. Secondhand goods have none.
4. **1:15** The proposal arrives with the sales behind the price. The seller approves, and the
   item's own agent goes live: open its Agentverse profile.
5. **1:45** A second judge asks ASI:One for it with a max price. The haggling card shows each
   round, the judge accepts, the Visa link goes out, and the payment clears.
6. **2:15** The reveal: ask `@yardsale`, Yardsale's Fetch Business agent, for a bike with a max.
   It haggles and offers to buy in its own chat, through Yardsale's API. (The original plan was
   a sale on its Orders page, which only takes Square or SumUp POS orders.)
7. **2:35** What's real today, how Yardsale makes money, and what's next: power sellers move to
   their own Fetch Business agent; eBay and Facebook listings later.

## Questions judges will ask

- **"Isn't it your agent talking to your agent?"** The haggling protocol is published, so any
  agent can take the buyer's seat. Our buyer agent stands in for the ASI:One personal AI.
- **"Is the payment real?"** It runs on Stripe's test mode (Visa's sandbox once we have keys).
  Going live is a change of credentials. Don't call it real money.
- **"How does Yardsale make money?"** A commission on each sale, taken before the seller's
  payout. Pricing a photo is free.
- **"Why one agent per item instead of a database?"** The item agent holds the seller's floor
  itself and haggles with several buyer agents at once, so the floor never leaves it. Each one
  is a live Agentverse agent that can be found and dealt with directly, and it can move to the
  seller's own Fetch Business later. Items beyond our Agentverse slots run Almanac-only in our
  server, which handled 40 in an offline test. Without real haggling, an item agent would be a
  database row with an address, which is why the haggling rebuild matters.
- **"Does ASI:One read the photos?"** No. Claude reads photos, `asi1-mini` reads typed text, and
  ASI:One finds and routes to the agents. The README and Devpost say the same.
- **"What if ASI:One doesn't pick Yardsale?"** Use the rehearsed query. If routing misses on
  stage, @mention the Market directly.
- **"Which part is the Payment Protocol, and which is Visa?"** The Payment Protocol is the
  messages between agents: the item agent requests payment and gets the confirmation. Visa Pay By
  Link is the page where the buyer pays.
- **"Why not give each seller their own Fetch Business agent?"** Fetch Business is one agent per
  business, and casual sellers shouldn't have to set up and verify a business to sell a lamp.
  Sellers who keep listing graduate to their own.

## Questions to ask tonight

**Fetch team**

1. Can we claim `@yardsale` this weekend and connect it to our existing Market agent? Is the
   Integrations tab the way to do that?
2. If not, can its "custom inventory" connection point at our listings?
3. Can deals closed by our agents show up as orders in `@yardsale`'s Fetch Business workbench?
   How do agents like the bubble tea shop's (connected through Square) get orders there?
4. Can we get Agentverse Premium (25 agents) for the weekend, so each item agent can be hosted on
   Agentverse?
5. Does using Fetch Business count toward the Fetch track, or only Agentverse and ASI:One?
6. Does a payment link opened from the chat still count as completing the workflow inside
   ASI:One? Does ASI:One show any Payment Protocol request itself?

**Visa team**

1. What does "Reimagining Shopping" judge on?
2. Can we get access to the
   [Visa Acceptance Agent Toolkit](https://developer.visaacceptance.com/resources/Agent-Toolkit.html)
   (in pilot) or a sandbox for Pay By Link this weekend?

## Sources

- [Cal Hacks 13.0 Devpost](https://cal-hacks-13-0.devpost.com/): rules, prizes, judging criteria
- Fetch's Cal Hacks hackpack: track rules, bonus points, what to submit
- [ASI:One Interactive Cards](https://innovationlab.fetch.ai/resources/docs/interactive-cards/asi-interactive-cards)
- [Agent Payment Protocol](https://innovationlab.fetch.ai/resources/docs/agent-transaction/agent-payment-protocol)
  and Fetch's
  [Stripe example](https://innovationlab.fetch.ai/resources/docs/examples/agent-transaction/stripe-horoscope-payment-protocol)
- [Fetch Business](https://business.fetch.ai/) and its
  [commerce post](https://business.fetch.ai/blog/commerce-capabilities)
- [VentureBeat on the Fetch Business launch](https://venturebeat.com/ai/the-google-search-of-ai-agents-fetch-launches-asi-one-and-business-tier-for)
- [Fetch's AI-to-AI payment](https://www.fetch.ai/blog/world-s-first-ai-to-ai-payment-for-real-world-transactions)
- [Visa Acceptance Agent Toolkit](https://developer.visaacceptance.com/resources/Agent-Toolkit.html)
- [Agentverse Hosting API](https://fetch.ai/docs/apis/agentverse/hosting) and
  [hosted agents guide](https://docs.agentverse.ai/documentation/launch-agents/hosted-agents)
