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
| At least one agent registered on Agentverse | Yes: the Market, Identifier, Researcher, Appraiser, Shopper, Market Scout, and Deal Finder are mailbox agents on Agentverse (seven, as of October 24), and item agents can be hosted there. |
| Implements the Agent Chat Protocol | Yes, the Market. |
| Discoverable and directly usable through ASI:One | Buying: yes, without an @mention (ASI:One's planner picked the Market on October 24). Selling: by @mention; the planner tried to price items itself. |
| Meaningful tool use or multi-agent orchestration | Yes: three pricing agents, Apify, Claude, an agent per item, and a buyer agent haggling with several item agents at once. |
| The main workflow happens entirely inside an ASI:One conversation, with no custom frontend | Selling, searching for deals, and buying happen in the chat; payment is a link from the chat. |
| Public GitHub repo with instructions to run or test it | The repo is public and the README has run instructions. |

**Bonus points:**

| Bonus | How Yardsale gets it |
| --- | --- |
| Effective multi-agent collaboration | The pricing agents; a buyer agent haggling with several item agents at once over a published protocol (code done October 24). |
| Payment Protocol with a credible monetization model | The Market sends agent buyers a Payment Protocol `RequestPayment` with the payment link; Yardsale keeps a 5% commission on each sale (test mode; a Stripe test payment went through end to end on October 24; Visa not yet run). |
| Interactive Cards | Every reply is a card since October 25: a home menu, proposals, forms, carousels, and buttons that open links. Seen in the ASI:One app: the Approve card (October 23), and the campus cards, email form, proposal card, sales carousel, and menu (October 25). Redesigned the same day for how ASI:One's web app lays cards out ([background.md](background.md#how-asione-lays-out-a-card)); checked with ASI:One's own card component, not yet seen in the app. |
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

Decided October 24, after talking with a Fetch judge: Yardsale is two products, one for sellers
and one for buyers, and both run on market data from Apify. The first buyer product was
agent-to-agent purchases on ASI:One (Yardsale's buyer agent haggling with our own item agents
and buying). The judge called it too ambitious for the weekend with no point to prove, and a
buyer gets more from a finder that covers the whole market than from haggling over Yardsale's
own few listings. That code stays and works (it ran live October 24), but it isn't pitched.

**One line:** Photograph your stuff, set your minimums, and agents price it, list it, and haggle
for you. Say what you want and your max, and agents find the best deals on every site, scored
against what the thing really sells for.

**Product 1: sellers.** A student is moving out of their apartment right before finals.
Everything in the dorm has to go, and posting it all brings 200+ DMs to answer ("is this still
available?", "would you take $10?") while they study, and it's worse when they're flying home
out of state. With Yardsale they photograph each item (up to 8 in one message) and say their
minimum ("sell this, at least $40"). Claude identifies each one. The Researcher finds what it
sold for on eBay in the last 90 days, and the Market Scout finds what the same thing is asking
right now on Craigslist, OfferUp, and Facebook Marketplace nearby, so the price fits the market
and the neighborhood. If the seller's minimum is far from what it sells for, Yardsale tells them
so, with the numbers, before anything is listed. Once they approve, the item gets its own agent
that knows the minimum. The listing is on ASI:One and `@yardsale`, and Yardsale writes a
Craigslist post for the seller to paste. Every buyer, in ASI:One or by email from the Craigslist
post, haggles with the item's agent, which gives a reason with each counter, never goes below
the minimum, and never shows it.

**Product 2: buyers.** The buyer says what they want and the most they'll pay, in ASI:One or to
`@yardsale`. Yardsale's own listings come first. Then the Deal Finder searches eBay, Craigslist,
OfferUp, and Facebook Marketplace at once (through Apify Actors), scores each listing against
what that item actually sells for (eBay sold comps, the same data that prices sellers' items),
ranks them by price and quality, and says why each one is a good deal. For a Craigslist post the
buyer picks, the agent emails the seller and negotiates within the max.

**What's built** (October 24, night; "tested offline" means it runs in the tests with fake
Apify, Claude, and ASI:One answers and hasn't been run live yet):

| Piece | Product | Status |
| --- | --- | --- |
| Photo → item identified (Claude) and priced from eBay sold listings (Apify) | Sellers | Built. Run live from the ASI:One app October 23–24; one photo took 36 seconds. |
| Up to 8 photos in one message, numbered proposals | Sellers | Built, tested with fakes |
| Minimum read from the seller's own words (`asi1-mini`) | Sellers | Built |
| Market Scout: live asking prices (Craigslist, OfferUp, Facebook Marketplace, eBay) beside the sold comps | Sellers | Built ([scout.py](../yardsale/scout.py)), tested offline. Runs alongside the Researcher; the price still comes from sold comps, and the asking prices set the fair range and the advice. The agent (`@ys-scout`) is on Agentverse since October 24; no photo has been priced live with it yet. |
| Telling the seller when their minimum is far off | Sellers | Built ([advice.py](../yardsale/advice.py)), tested offline: a price 35% or more off what it sells for gets a plain warning with the numbers and a suggested price; the seller's price still wins. |
| Approve → item agent with its own address | Sellers | Built. Hosted on Agentverse with its own profile, live October 24. |
| Craigslist post text (`craigslist`), pasted by the seller | Sellers | Built |
| Item agent haggles with Craigslist buyers by email, payment link on a deal | Sellers | Built; tested with a fake inbox only, not yet run against real Craigslist or Gmail |
| Counters with reasons | Sellers | Built, tested offline: each counter cites the market line the Market worked out from sold and asking prices ("Similar ones sold for about $340 on eBay recently"), never the floor. |
| Search on eBay, Craigslist, OfferUp, and Facebook Marketplace, best five under the max | Buyers | Built ([elsewhere.py](../yardsale/elsewhere.py)): in the chat, and in `@yardsale` as `searchOtherSites`. eBay and Craigslist tried on real results; OfferUp and Facebook parse the Actors' documented output. Not yet run from the chat with real keys. |
| Deal Finder agent: score against eBay sold comps, "why it's a deal" | Buyers | Built ([dealfinder.py](../yardsale/dealfinder.py), [deals.py](../yardsale/deals.py)), tested offline: price against the typical sold price, then quality, then fit, with a one-line reason each. One live probe October 24: all four sources parsed; OfferUp ignored the result cap and bike parts ranked too high (fixes in progress). The agent (`@ys-dealfinder`) is on Agentverse since October 24. |
| Standing searches (`watch for it`, every 6 hours for a week) | Buyers | Built |
| Agent emails a Craigslist seller and negotiates for the buyer | Buyers | Built ([craigslist_buyer.py](../yardsale/craigslist_buyer.py)); tested through a fake relay, both agents haggling through it to a deal |
| `@yardsale` on Fetch Business with OpenAPI tools | Both | Built, live October 24 (Plus trial, Cloudflare tunnel): `searchListings`, `searchOtherSites`, and the haggle and buy calls. `priceCheck` (October 24) and `startNegotiation` with `getNegotiation` (October 25: the Craigslist email negotiation, so the buyer product finishes in one chat) are built and tested offline, and the Business Tool was re-created with them on October 25 ([fetch-business.md](fetch-business.md)). The haggle and buy calls are off unless `YARDSALE_API_HAGGLE=1`. |

**Also built, not pitched:**

- **Agent-to-agent buying on ASI:One**: the Shopper (buyer agent) haggles with every matching
  item agent at once over the published `YardsaleHaggle` protocol, with a card showing every
  round; `accept` buys; the buyer gets a payment link and an agent buyer a Payment Protocol
  request. Live October 24 from the ASI:One app, through ASI:One's planner with no @mention,
  and through `@yardsale` (`startHaggle`, `acceptDeal`; since October 25 in its API only with
  `YARDSALE_API_HAGGLE=1`). Dropped from the pitch, not the code,
  and out of the default flow: "best deal", "best price", and planner prose are searches, and
  the Shopper runs only on an explicit request after a search ("haggle with the seller on 1,
  max $300").
- **Payment links and the 5% commission**: Stripe test checkout went through end to end
  October 24. Still used for Craigslist buyers who pay through the link. Visa Pay By Link is
  built for Visa's sandbox and never run (no keys).
- **Selling on the seller's own eBay** ([ebay_seller.py](../yardsale/ebay_seller.py)): `connect
  ebay`, list on approve, and Best Offers answered with the item agent's rules. Tested only
  against a local stand-in, and blocked on eBay developer keys
  ([ebay-setup.md](ebay-setup.md)).
- **Orders webhook**: every order status change can be POSTed out, but Fetch Business's Orders
  page takes orders only from Square or SumUp POS.
- **Our own resale comps Actor**: pushed to Apify, but eBay answers 403 through Apify's proxy.

**Why it fits what Fetch is building.** The challenge is "From Intent to Action", and both
products end in action: a listing that sells itself, or an offer emailed to a real seller. The
judge said that what they look for is agents on Agentverse linked to a Fetch Business agent, and
that's the shape of Yardsale: several agents on Agentverse, each with its own profile and job
(the Orchestrator, Identifier, Researcher, Appraiser, the Market Scout and Deal Finder in
progress, and an agent per listed item), with `@yardsale` on Fetch Business as the front door
that calls them through OpenAPI tools (search listings, search other sites, price check).

**Why it fits Apify.** Apify is the data under both products: eBay sold comps price every
item and score every deal, and four live-listing Actors (eBay, Craigslist, OfferUp, Facebook
Marketplace) feed the Market Scout and the Deal Finder. The team has $200 of Apify credit. Our
own Actor, `resale-comps`, is pushed but blocked by eBay's 403.

**The marketplace problem.** A new marketplace has no buyers and no listings. Yardsale doesn't
wait for either. A seller's Craigslist post brings Craigslist's buyers to the item agent, and the
Deal Finder is useful on day one with nothing listed, because it searches where listings already
are.

**How Yardsale makes money.** A commission on each sale Yardsale collects payment for (ASI:One
and Craigslist buyers who pay through the link), taken before the seller's payout: 5%, so
sellers keep 95%. Pricing a photo is free, since that's what brings sellers in. The Deal Finder
has no fee yet; one search costs Yardsale about $0.03 to $0.05 per site on Apify (Facebook
$0.05 to $0.12), and results are cached for 10 minutes.

**Who it's for.** Sellers: students moving out, with more stuff than time. Buyers: students
moving in and bargain hunters, who shop by asking ASI:One.

**Earlier work.** Past shopping projects (Shopybara, VShop AI, Revi) were buyer-side helpers
that stopped before the purchase. Agent Santa negotiated between agents but faked payment. DIAL
haggled with humans by phone. Yardsale covers both sides with the same market data: sellers'
items price and haggle themselves, and buyers get every site's listings scored against what the
thing really sells for, with an agent that emails the seller.

**How to frame it:**

- **Two products, one story.** The seller moving out and the buyer looking for a deal. Don't
  present agent-to-agent buying, eBay selling, Visa, or the Orders webhook as products.
- **The agent answers the DMs.** Suggested line: "You can't answer 200 DMs during finals. Your
  agents can."
- **Data, not just a chat.** Every price Yardsale gives has sold comps behind it, and the seller
  and the buyer both see them. That's what separates it from a scraper and from a chatbot.
- **One agent per item, many channels.** ASI:One, `@yardsale`, and Craigslist email are just
  where buyers come from. The same item agent answers all of them with the same rules, so the
  minimum never leaves it.
- **`@yardsale` is the front door.** It's Yardsale's Fetch Business agent, and it reaches the
  agents through Yardsale's API. Sellers' photos go to `@ys-orchestrator`, because a photo has
  to reach Claude. Fetch Business is the reveal, not the premise.
- **The long tail of Fetch Business.** Suggested line: "Fetch Business gives brands a verified
  agent. Yardsale gives everyone else one, for each thing they own."
- **Be exact about models.** Claude reads photos, `asi1-mini` reads typed text, and ASI:One finds
  and routes to the agents. ASI:One's models don't take images, Fetch judges know it, and they
  check the repo and Devpost against what the pitch claims. Never imply ASI:One reads the photos.
- **Be exact about Craigslist.** Craigslist bans automated posting, so the seller posts by hand;
  Yardsale writes the text. Buyers' emails reach Yardsale through Craigslist's relay. Yardsale
  doesn't fetch eBay, Craigslist, OfferUp, or Facebook pages itself: listings come from Apify
  Actors.
- **Be exact about what's been run live.** The Market Scout, the seller price feedback, deal
  scores, counters that cite comps, and `priceCheck` are built and tested offline; the Scout and
  Deal Finder agents are on Agentverse, and the Deal Finder has had one live probe. Show only
  what's worked live by the freeze.
- **Don't dwell on outside services.** Relying on Apify and Claude is fine, as long as the flow
  works inside ASI:One and can be demoed there. Show the flow; explain the services when asked.
- **The agent count.** The submission asks how many agents there are, and a typical entry has
  about ten plus an orchestrator. Ours is seven service agents on Agentverse (the Market,
  Identifier, Researcher, Appraiser, Shopper, Market Scout, and Deal Finder, all mailbox agents
  as of October 24) plus one per listed item. Don't add agents just to raise the number.

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
- Business Information: a Plain Text entry explaining that buying happens with `@yardsale`'s
  tools and that sellers go to the Market agent, `@ys-orchestrator`, with its address.
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
  plain 200 (not 202), and `getHaggle` waits for the result.
- That evening haggles failed again: item agents' replies went to a second server sharing our
  seed (see "Only one live server per master seed" below). Before that was found, the waits
  were cut to 10 seconds on a guess that Fetch drops slow tool calls. That made things worse:
  haggles took just over 10 seconds, and `@yardsale` doesn't reliably follow a `running` answer
  with `getHaggle` (it called `startHaggle` again, sent IDs that didn't exist, or checked its
  memory instead). The waits are back at 25 seconds, so most haggles finish in one call.
- Once a haggle agreed at $270, "lock it in" sent the buyer to `@ys-orchestrator`, which can't
  see haggles made through the API and answered "Make an offer first". The Business Information
  entry still said buying happens with `@ys-orchestrator`, and the skill's internal name was
  `send-buyers-and-sellers-to-yardsale-s-market-agent`. Both were rewritten: buying happens with
  `@yardsale`'s tools and only sellers go to `@ys-orchestrator`. The skill (now
  `find-haggle-and-buy-used-items-on-yardsale`) says to call `acceptDeal` on a yes and never
  send a buyer elsewhere to pay, and to follow `running` with `getHaggle` using the exact ID,
  never `startHaggle` again.

So `@yardsale` is now a working front door for buyers. Sellers still go to `@ys-orchestrator`,
because a photo has to reach Claude.

Orders: with the trial, the apps (Stripe, Square, SumUp, SumUp POS, E-commerce, Google
Calendar) became toggles that connect an outside account, and orders come from Square or SumUp
POS. Nothing lets an outside API add orders, so Yardsale's sales don't reach the Orders page.

Looked at more closely later on October 24, on the trial of the highest plan:

- The Orders page reads Fetch's own order list (`GET /v2/agents/<agent id>/orders`). The
  workbench can list orders and change an order's status, but nothing in it creates one.
- Fetch's text for the Square app calls it Food Ordering: the agent browses the Square catalog,
  places an order, and the buyer pays through Square. So an order most likely appears when
  `@yardsale` itself orders through its Square tool. An order that Yardsale's server makes in
  Square directly probably wouldn't appear; that's untested.
- The Square toggle always connects a live Square account (it sends `is_sandbox: false`).
  Asked directly with `is_sandbox: true`, Fetch's server returned a Square sandbox sign-in link
  asking for catalog, order, and payment access. Square's sandbox answered that the seller test
  account has to be launched from the Square Developer Console first. Nothing was connected.
- With a sandbox seller test account launched, the same link failed: Square's sandbox answered
  "Unable to find client by that `client_id`". Fetch's sandbox link carries its live Square app
  ID (`sq0idp-...`), and sandbox app IDs look like `sandbox-sq0idb-...`, so Fetch's sandbox
  option isn't set up. Only a live Square account connects, which would mean real payments, and
  Yardsale stays in test mode. The Square route is dropped unless Fetch fixes this. Worth
  telling the Fetch team: their Square sandbox sign-in uses the live app ID.

Still to ask the Fetch team: how an outside agent or API adds orders, and whether a Business Tool
can point at a URL that changes (each quick tunnel gets a new one).

## The demo, step by step

Two phones on the big screen: a friend's (the seller) and the presenter's (the buyer). Steps 1–7
are the seller product, and 8–10 are the buyer's Deal Finder in `@yardsale`. Selling happens in
ASI:One; the Craigslist email is a second channel the same item agent answers.

| Step | Phone | What happens | Sponsor | Built? |
| --- | --- | --- | --- | --- |
| 1. Snap | Seller | The friend attaches a photo in ASI:One (`@ys-orchestrator`) and types "sell this, at least $40". Claude identifies the item; `asi1-mini` reads the minimum. | Claude, Fetch (ASI:One) | Yes, up to 8 photos per message |
| 2. Price | Seller | The Researcher finds eBay sold listings and the Market Scout finds live local asking prices, both through Apify. Code sets the asking price and floor. | Apify | Sold listings yes (`caffein.dev/ebay-sold-listings`; our own Actor is blocked by eBay). Market Scout built, tested offline; not yet run live in the pipeline. |
| 3. Feedback | Seller | If the $40 minimum is far under what it sells for, the reply says so with the numbers before anything is listed, and the friend can change it. | | Built, tested offline (35% or more off the market) |
| 4. Approve | Seller | The proposal card shows the sales behind the price. The friend approves, and the item's agent goes live with its address and, when hosted, an Agentverse profile. | Fetch (Interactive Cards, Agentverse) | Yes. Approve card seen in ASI:One; hosting run live October 24. |
| 5. Post | Seller | `craigslist` gives the post text. The friend has already pasted it on Craigslist with Yardsale's inbox as the email; show the live post. | | Yes; posted by hand. Not yet tried on real Craigslist. |
| 6. Email | Buyer | From the presenter's phone, reply to the Craigslist post with a low offer. | | Yes; relay unverified |
| 7. Haggle back | Buyer | The item agent answers in the email thread with a counter and a reason, never showing the floor. The presenter replies "deal", gets a payment link, and pays with Stripe's test card. The friend's `my listings` shows the item sold and the payout after 5%. | Fetch (uAgents), Stripe (test mode) | Yes; tested with a fake inbox only. Counters cite the market line ("Similar ones sold for about $X on eBay recently"), tested offline. |
| 8. Ask | Buyer | Ask `@yardsale`: "find me the best deal on a mini fridge, max $60". It searches Yardsale's listings (none match) and the other sites, and haggles with no one. | Fetch Business | Yes: `searchListings`, then `searchOtherSites`; no `startHaggle` |
| 9. Find | Buyer | The Deal Finder lists the best eBay, Craigslist, OfferUp, and Facebook Marketplace results under $60, each with a score against what mini fridges sell for and a line on why it's a deal. | Apify | Built: the Deal Finder agent scores against sold comps with a reason each. One live probe: all four sources parsed, OfferUp ignored the result cap, bike parts ranked too high (fixes in progress). Not yet run from `@yardsale`. |
| 10. Negotiate | Buyer | The presenter picks a Craigslist post, taps Reply on it, and pastes the relay address into the chat. The agent emails the seller an offer and negotiates within the max. | | Built; tested through a fake relay. In `@yardsale` since October 25 (`startNegotiation`, once the Business Tool is re-imported) and in `@ys-orchestrator`'s chat. |

Before the pitch:

- Make the Craigslist post at least an hour early. Craigslist emails a confirmation link first,
  and how long the relay takes to deliver a buyer's email is unknown.
- Rehearse steps 5–7 and 10 on real Craigslist and time them. If the relay is slow, keep a
  recording of them as the fallback.
- Run the demo search (step 9) with real keys the day before, after the OfferUp cap and
  parts-ranking fixes land, and check the scores and reasons read right. Results are cached for
  10 minutes, so run it again right before the pitch.
- Price one real photo end to end with the Scout in the pipeline, and time it: the Scout adds
  Apify runs to the 36 seconds a photo took before it.
- Keep agent-to-agent buying out of the video, or give it one line under "also built". The
  video is judged on its own, so it has to show the two products end to end.

## How close the code is

State of the `seller-channels` branch on Saturday, October 24, night (live runs noted; "tested
offline" means it runs in the tests with fake services and hasn't been run live yet):

| Piece | Status |
| --- | --- |
| Photo → price from sold listings | Built. With real keys, a photo of Air Jordan 4s was priced from 20 matching eBay sales on October 23. Each step's time is logged. Up to 8 photos per message. |
| Market Scout (live asking prices beside the sold comps) | Built ([scout.py](../yardsale/scout.py)), tested offline. The Market asks it and the Researcher at once after the Identifier; asking prices set the fair range, the market line counters cite, and the seller's warning, never the price itself. On Agentverse as `@ys-scout` since October 24; not yet run live in the pipeline. |
| Seller price feedback ("your minimum is far under what it sells for") | Built ([advice.py](../yardsale/advice.py), `pricing.price_verdict`), tested offline: 35% or more off the market gets a plain warning with the numbers and a suggested price. |
| One agent per item, with its own address | Built. Registered on the Almanac by default. |
| Item agents hosted on Agentverse | Built: `YARDSALE_HOST_ITEMS=N` hosts up to N through the Hosting API, falling back to the server. Run live October 24: two hosted test bikes got public profiles, haggled, and sold. |
| Seller approval, cards | Built: Approve card, sales carousel, prices form, and a card on every reply. The Approve card showed in the real ASI:One app on October 23, and the proposal card, sales carousel, and menu on October 25. That day's layout redesign hasn't been seen in the app yet. |
| Craigslist post text; item agent answers Craigslist buyers by email | Built ([craigslist_mail.py](../yardsale/craigslist_mail.py)); tested with a fake inbox and a fake relay only. Not yet run against real Craigslist or Gmail. |
| Haggling within the seller's limits | Built: several rounds, agree-then-accept, counters stop a private gap above the floor, and each counter cites the market line (`haggle.with_reason`), never the floor. Tested offline. |
| Deal Finder: eBay, Craigslist, OfferUp, Facebook Marketplace under the max | Built ([dealfinder.py](../yardsale/dealfinder.py), [elsewhere.py](../yardsale/elsewhere.py), [sources.py](../yardsale/sources.py)) as its own agent, in the chat and in `@yardsale` as `searchOtherSites`, with a carousel card and `watch for it`. One live probe October 24: all four sources parsed; OfferUp ignored the result cap (fix in progress). On Agentverse as `@ys-dealfinder` since October 24. |
| Deal scores against sold comps, "why it's a deal" | Built ([deals.py](../yardsale/deals.py)), tested offline: price against the typical eBay sold price, then quality (condition, photos, seller rating, title, age), then fit; parts, repairs, lots, and accessories sink. In the live probe, bike parts still ranked too high (fix in progress). |
| Agent emails a Craigslist seller for the buyer | Built ([craigslist_buyer.py](../yardsale/craigslist_buyer.py)): `negotiate <relay address> "<title>" max $250 asking $300`; at most 4 offers, the max never emailed. Tested through a fake relay, including Yardsale's buyer and seller agents haggling through it. |
| `@yardsale` on Fetch Business | Set up October 24: a description, a knowledge entry, a skill, and (on the Plus trial) an OpenAPI Integration with Yardsale's HTTP API. Live October 24 through a Cloudflare quick tunnel. Tools: `searchListings`, `searchOtherSites`, `priceCheck` (built October 24, tested offline), `startNegotiation` and `getNegotiation` (October 25, tested offline), and only with `YARDSALE_API_HAGGLE=1` `startHaggle`, `getHaggle`, `acceptDeal`, `getDeal`. The Business Tool was re-created on October 25 with `searchListings`, `searchOtherSites`, `priceCheck`, `startNegotiation`, and `getNegotiation` (until then it still had the October 24 tools), and the skill, Business Information, starter prompts, and description were updated in the workbench the same day. The steps are in [fetch-business.md](fetch-business.md). Sellers' photos still go to `@ys-orchestrator`. |
| Agent-to-agent buying (the Shopper, haggling card, Payment Protocol) | Built and live October 24, in ASI:One and through `@yardsale`. Not pitched. |
| Payment links, commission | Built for test mode: Visa Pay By Link sandbox or Stripe test checkout, 5% commission. Stripe run live October 24: a $295 deal was paid with a test card and recorded with its commission. Visa not yet run (no sandbox keys). |
| Selling on the seller's own eBay | Built ([ebay_seller.py](../yardsale/ebay_seller.py)); tested against a local stand-in only; needs eBay developer keys. |
| Our own Apify Actor | Written in `actors/resale-comps/` and pushed to Apify (private). eBay answers 403 through Apify's default proxy, so it finds no sales yet. Switching is `YARDSALE_APIFY_ACTOR`. |
| Innovation Lab badges, agent addresses in the README | Done in the repo. Agentverse shows the badges after each agent reconnects. |
| Public URL | `YARDSALE_PUBLIC_URL` exists but hasn't been used. ASI:One reaches the Market through its Agentverse mailbox, and item agents run in the same server or on Agentverse, so the chat flow doesn't need it; outside agents do. |

ASI:One's cards come in five kinds: carousel, detail, form, review, and custom. The installed
`uagents_core` cards module rejects fields ASI:One's web app reads (picture buttons, link
buttons, boxed groups, a carousel's bottom button, and `resubmittable`), so since October 25
Yardsale builds cards with its own [card_schema.py](../yardsale/card_schema.py). A tapped
button comes back as JSON text after an @mention, or as plain prose when ASI:One's planner
relays it, so both have to be read.

## Known problems

- **Most of the pitch is tested offline only.** The Market Scout, the seller price feedback,
  deal scores, counters that cite comps, and `priceCheck` are on the branch and pass the tests
  with fake services. The Scout and Deal Finder agents are on Agentverse, but no photo has been
  priced with the Scout in the pipeline and no seller has seen the warning or a cited counter
  in the real app. At the code freeze, cut from the pitch whatever hasn't worked live.
- **The Deal Finder's one live probe found two problems.** All four sources parsed real Actor
  results, but OfferUp ignored the result cap (a bigger paid run than asked for) and bike parts
  ranked too high for a bike search. Fixes are in progress; run the demo search end to end once
  they land, and the day before.
- **`@yardsale` had no search or price tools until October 25.** Its Business Tool still had
  the six tools imported on October 24 (`searchListings`, the four haggle and deal calls, and
  the description), so `searchOtherSites` and `priceCheck` had never reached it, and once the
  haggle calls were gated off it could only search Yardsale's listings. On October 25 it was
  deleted and re-created from the tunnel's `/openapi.json` with the API key, and now has
  `searchListings`, `searchOtherSites`, `priceCheck`, `startNegotiation`, `getNegotiation`,
  and the description, with confirmation on `startNegotiation`
  ([fetch-business.md](fetch-business.md)). The skill, Business Information, starter prompts,
  and description were updated the same day. Checked from ASI:One: a `getNegotiation` call
  for a made-up ref reached the server with the key (404, no 401). A search or price check
  there is a paid Apify run and hasn't been tried with the new tools.
- **Craigslist's relay is unverified.** Nothing has been run against real Craigslist: whether
  the relay accepts replies from the posting email, keeps the subject, how long it takes, and
  whether it strips links (if it does, `YARDSALE_MAIL_LINKS=0` gives the amount and asks for a
  phone number). Rehearse early and keep a recording.
- **A seller's minimum far under the appraisal got no warning** until October 24: "at least
  $40" on shoes appraised at $190 was taken as is, and a buyer's opening offer of $123 was
  agreed at once. The seller feedback ([advice.py](../yardsale/advice.py)) now warns at 35% or
  more off the market; it's tested offline and hasn't been seen in the real ASI:One app.
- **Lowballs revealed the floor** until the October 24 rebuild: a $1 offer used to get a
  counter at exactly the floor. Now counters stop a private gap above it, and lowballs get no
  concession. A buyer making many identities could still narrow a floor down.
- **"20 seconds" is wrong.** A photo took 36 seconds on October 24 (identify 11 s, eBay 12 s,
  appraise 14 s). Say "under a minute" on stage. The Market Scout adds Apify runs to that.
- **"Agentverse address".** Only hosted item agents have an Agentverse profile. Hosting worked
  live on October 24, so run the demo with `YARDSALE_HOST_ITEMS` set.
- **Apify credit.** The team has $200 (October 24): about $0.08 a photo, $0.03 to $0.05 per site
  per Deal Finder search (Facebook $0.05 to $0.12), so rehearsing with real photos and searches
  is fine. Searches are cached for 10 minutes.
- **Selling needs the @mention.** ASI:One's planner found the Market for buying without one
  (October 24), but asked to sell a lamp it offered to price it itself. Sellers @mention
  `@ys-orchestrator`.
- **Wi-Fi that intercepts HTTPS cuts the server off.** On the morning of October 24, every
  agent's mailbox check failed for a long stretch with "certificate is not valid for
  agentverse.ai" (a network answering for Agentverse with its own certificate, as a sign-in
  page on public Wi-Fi does). While that lasts, ASI:One's messages wait in the mailboxes and
  the chat gets no answer. The server recovered by itself once the network was fixed, but a
  restart during a dropped connection crashed it until a fix the same day. On stage, use a phone
  hotspot or a network already signed in, and check the server log before starting.
- **`@yardsale` depends on the tunnel.** Its Business Tool calls Yardsale's API through a
  Cloudflare quick tunnel, which gets a new URL every time `cloudflared` restarts; the Business
  Tool then has to be pointed at the new URL. Keep the tunnel running from before the demo to
  after it, and check `curl <tunnel>/openapi.json` first. Every new tool (`priceCheck`, `startNegotiation`) means
  re-importing the OpenAPI description in the workbench.
  - A quick tunnel that stays disconnected for a while is deleted by Cloudflare ("Tunnel not
    found"). On October 24 the Wi-Fi dropped at 5:07 PM, and by 5:32 PM the tunnel was gone.
    `cloudflared` kept running and retrying, so restart it by hand.
  - Fetch Business can't change an integration's URL: the workbench has no field for it, and
    its update call accepts only the name, description, and on/off switch. Delete the
    integration and create it again with the new spec URL (Integrations, Create Business Tool,
    with YardSale selected at the top), then paste `YARDSALE_API_KEY`. Re-created with the name
    Yardsale Market, the tools keep their names (`YardsaleMarket_...`) and the skill still works.
- **The Orders page can't show our sales.** On the Plus trial, orders come only from a connected
  Square or SumUp POS account. A Square sandbox account can't get around it: Fetch's sandbox
  sign-in uses its live Square app ID, which Square's sandbox rejects (see "What we set up on
  October 24").
- **Only one live server per master seed.** Every agent's address comes from the master seed,
  so two live servers with the same seed run the same agents, and each mailbox message goes to
  whichever server reads it first. On the evening of October 24, a second server running
  `seller-channels` with the same seed took the buyer agent's mail: item agents' replies never
  reached the server that sent the offers, and every haggle ended "the item's agent didn't
  answer". Once it stopped, a haggle agreed in 6 seconds. Run a second copy with `--offline`, or
  with its own `.agent-data/` and no `YARDSALE_MASTER_SEED`.
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

The big screen shows two phones: the friend's (seller) and the presenter's (buyer). Steps are
from "The demo, step by step" above.

1. **0:00** The problem. It's the week before finals and you're moving out: a mini fridge, a
   desk, a bike, a lamp. You post them and 200 DMs come in. You can't answer them and study, and
   if you're flying home out of state, whatever's left goes in the dumpster.
2. **0:20** Product 1, live. The friend attaches a photo in ASI:One: "sell this, at least $40".
   Pricing runs while we talk (under a minute): Claude identifies it, Apify finds what it sold
   for on eBay and what it's asking nearby, and code sets the price.
3. **0:45** The proposal arrives with the sales behind the price, and a note if $40 is far under
   what it sells for. The friend approves, and the item gets its own agent that knows the
   minimum: show its address or Agentverse profile. `craigslist` gives the post, already live on
   Craigslist.
4. **1:05** The presenter emails the Craigslist post from their own phone: "would you take
   $25?" The item's agent answers in the thread with a counter and why, never showing the
   minimum. Reply "deal", get the payment link, pay with the test card. The friend's `my
   listings` shows it sold. "The seller answered zero DMs."
5. **1:45** Product 2. The presenter asks `@yardsale`, Yardsale's Fetch Business agent: "find me
   a mini fridge, max $60". It checks Yardsale's listings first, then the Deal Finder searches
   eBay, Craigslist, OfferUp, and Facebook Marketplace.
6. **2:00** The deals come back scored against what mini fridges actually sell for, best first,
   each with why it's a deal. The presenter picks a Craigslist post, and the agent emails its
   seller an offer and negotiates. (Show only what's on the branch: if scores aren't in, show
   the ranked list.)
7. **2:30** What's real, how Yardsale makes money, and what's next: agents live on Agentverse
   behind `@yardsale` on Fetch Business, every price from Apify sold comps, Stripe test payments
   with a 5% commission; next, listing on the seller's own eBay once eBay approves our
   developer keys.

## Questions judges will ask

- **"Why drop agent-to-agent buying?"** A Fetch judge told us on October 24 that agents buying
  from our own agents on ASI:One was too ambitious for the weekend and proved nothing a buyer
  wants. A buyer gets more from a finder that covers eBay, Craigslist, OfferUp, and Facebook
  Marketplace and scores every listing against sold prices than from haggling over Yardsale's
  own few listings. The code is still there and ran live, but the pitch is the two products.
- **"Isn't this just a scraper?"** The listings come from Apify Actors; what the agents add is
  on top of them: a price for every item from eBay sold comps, a score for every deal against
  those comps with the reason, an item agent that haggles within the seller's limits and gives
  reasons, feedback to a seller whose minimum is off, and an agent that emails a Craigslist
  seller. All of it inside ASI:One and `@yardsale`, with nothing to install.
- **"Isn't it your agent talking to your agent?"** Not in what we show: the person emailing
  from Craigslist is the buyer, and the Craigslist seller the agent emails is a person. Between
  agents, the haggling protocol is published, so any agent can take the buyer's seat.
- **"Do you post to Craigslist automatically?"** No. Craigslist bans automated posting, so the
  seller pastes a post Yardsale writes. Buyers' replies reach Yardsale's inbox through
  Craigslist's own email relay, and the item agent answers them.
- **"Are you scraping eBay and Craigslist?"** Yardsale doesn't fetch their pages itself. Sold
  prices come from the Apify Actor `caffein.dev/ebay-sold-listings`, and the Deal Finder's
  listings from `charitable_trousseau/ebay-search-scraper`, `logiover/craigslist-scraper`,
  `igolaizola/offerup-scraper`, and `apify/facebook-marketplace-scraper`.
- **"How do you score a deal?"** Against what the same item sold for on eBay in the last 90
  days, the comps that price sellers' items too: price against that typical sold price first
  (shipping included), then quality (condition, photos, seller rating, title, how recently it
  was posted), then how well the title fits the search. Parts, repairs, lots, and accessories
  sink, and something far under the market is flagged to check. Plain code, no model call
  ([deals.py](../yardsale/deals.py)).
- **"Will Craigslist sellers mind an agent emailing them?"** Every email says it's from the
  buyer's Yardsale assistant, an AI agent. It's one thread per post the buyer picked, at most
  four offers, and the buyer pastes the reply address themselves, so Yardsale never collects it.
- **"Why not list it on eBay for the seller?"** Built: the seller connects their own eBay and the
  item agent's rules answer Best Offers. It's tested only against a local stand-in and waits on
  eBay approving our developer account.
- **"Is the payment real?"** It runs on Stripe's test mode (Visa's sandbox is built but hasn't
  been run). Going live is a change of credentials. Don't call it real money. Craigslist deals
  found by the Deal Finder are paid in person, not through Yardsale.
- **"How does Yardsale make money?"** 5% of each sale Yardsale collects payment for, taken before
  the seller's payout. Pricing a photo is free, and the Deal Finder has no fee yet.
- **"Why one agent per item instead of a database?"** The item agent holds the seller's minimum
  and haggles with every buyer, from ASI:One or Craigslist email, so the minimum never leaves
  it. Each one is an agent that can be found and dealt with directly, and hosted ones have
  their own Agentverse profile. Items beyond our Agentverse slots run in our server, which
  handled 40 in an offline test.
- **"Does ASI:One read the photos?"** No. Claude reads photos, `asi1-mini` reads typed text, and
  ASI:One finds and routes to the agents. The README and Devpost say the same.
- **"How is this linked to Fetch Business?"** `@yardsale` is Yardsale's Fetch Business agent
  (Plus trial). It calls Yardsale's HTTP API through an OpenAPI Integration, one tool per call
  (search listings, search other sites, price check, negotiate with a Craigslist seller by
  email), and the API runs the same agents the ASI:One chat does. Sellers' photos go to `@ys-orchestrator`, because a photo has
  to reach Claude.
- **"What if ASI:One doesn't pick Yardsale?"** Use the rehearsed query. If routing misses on
  stage, @mention the Market (`@ys-orchestrator`) or ask `@yardsale`.
- **"Why not give each seller their own Fetch Business agent?"** Fetch Business is one agent per
  business, and a student shouldn't have to set up and verify a business to sell a lamp. Sellers
  who keep listing can graduate to their own.

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
