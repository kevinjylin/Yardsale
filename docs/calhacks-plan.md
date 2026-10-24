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
| Payment Protocol with a credible monetization model | The Market sends agent buyers a Payment Protocol `RequestPayment` with the payment link; Yardsale keeps a 5% commission on each sale (code done October 24, test mode, not yet run against Visa or Stripe). |
| Interactive Cards | Approve card (seen in ASI:One); sales carousel, seller prices form, haggling card, and payment card (code done October 24, not yet seen in ASI:One). |
| Reliability and recovery from failed tool calls | Failed pricing steps return a reason, a resent message gets the same answer, and restarts bring every active item agent back. |
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

**One line:** Fetch Business for things without a barcode. `@yardsale` is a verified Fetch
Business whose whole inventory is agents. A photo becomes a catalog entry priced from real sales,
and each item gets its own agent that haggles within the seller's limits and checks out with Visa.

**The gap it fills.** Fetch Business turns a business into an agent that customers' AIs can find,
talk to, and buy from. It gets products and prices from a catalog such as Shopify or Square. That
works when every product has a catalog entry and a fixed price. Used, one-of-a-kind items have
neither: the price has to be found from recent sales and then negotiated. Yardsale does both.

**Why it fits what Fetch is building.** One demo shows three things Fetch is pushing:

1. Discovery through Fetch Business.
2. A customer's AI and a business's AI working out a deal.
3. AI payments. Fetch's first AI-to-AI payment, in December 2025, used Visa.

**The marketplace problem.** A two-sided marketplace needs buyers. Yardsale doesn't have to build
that crowd: every ASI:One user is a potential buyer, and Fetch Business is how their AI finds
`@yardsale`.

**How Yardsale makes money.** A commission on each sale, taken from the payment before the
seller's payout (for example 5%, so sellers keep 95%). Pricing a photo stays free, since that's
what brings sellers in.

**Who it's for.** Sellers: students moving out, with more stuff than time. Buyers: bargain
hunters, who shop by asking ASI:One.

**Pitch as first written** (condensed): snap a photo of something you don't use, and it becomes
an agent that prices itself from real sold listings, gets found by buyers' agents in ASI:One,
haggles within your limits, and gets paid. Past shopping projects (Shopybara, VShop AI, Revi)
were buyer-side helpers that stopped before the purchase. Agent Santa negotiated between agents
but faked payment. DIAL haggled with humans by phone. Yardsale makes the inventory itself agents,
with real payment. The demo seeds inventory and uses judges as buyers. Posting to eBay or
Facebook is future work.

**How to frame it:**

- **Two layers.** `@yardsale` on Fetch Business is the front door: the verified agent ASI:One
  finds, and the only one buyers and sellers talk to. The uAgents are the specialists behind it:
  the Identifier, Researcher, and Appraiser price items, item agents hold floors and haggle, and
  the payment step settles. The business agent is the product, and the uAgents are the power
  behind it. The Cal Hacks event agent is itself a Fetch Business agent, so this is the setup
  Fetch is pushing.
- **Fetch Business is the reveal, not the premise.** Open with the seller's problem and the
  haggling. The `@yardsale` Orders page with a real sale on it comes near the end, as the
  surprise.
- **The long tail of Fetch Business.** Suggested line: "Fetch Business gives brands a verified
  agent. Yardsale gives everyone else one, for each thing they own."
- **Used next to new.** When someone asks ASI:One for a standing desk, brand agents offer new
  ones and Yardsale adds used ones to the same search. That makes ASI:One a better place to shop
  without competing with the brands Fetch is signing up.
- **Trust.** Fetch Business launched to stop knock-off agents. Secondhand selling has the same
  problem, worse. Every item agent sits under one verified `@yardsale`, with its photo and the
  sales behind its price attached.
- **A funnel into Fetch Business.** Anyone who keeps listing (a reseller, a thrift shop, a club
  doing a move-out sale) outgrows per-item agents and claims their own Fetch Business agent.
  Yardsale brings Fetch Business new customers.
- **One handle, not one per seller.** Fetch Business is built around one agent per business, not
  per item. Making every casual seller set up and verify a business would kill the snap-a-photo
  moment, and if Fetch Business did the selling, Yardsale would add nothing.
- **Be exact about models.** Claude reads photos, `asi1-mini` reads typed text, and ASI:One finds
  and routes to the agents. ASI:One's models don't take images, Fetch judges know it, and they
  check the repo and Devpost against what the pitch claims. Never imply ASI:One reads the photos.
- **Don't dwell on outside services.** Relying on Apify and Claude is fine, as long as the whole
  flow works inside ASI:One and can be demoed there. Show the flow; explain the services when
  asked.
- **The agent count.** The submission asks how many agents there are, and a typical entry has
  about ten plus an orchestrator. Ours is four service agents plus one per listed item, each live
  on Agentverse with its own profile. Don't add agents just to raise the number.

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
- The sidebar has Home, Claim Your Agent, Profile & Instructions, Business Information, Socials
  Library, Workflows & Tasks, Agent Messages, Agent Network, Orders, and Integrations.
- The Orders page lists each order with a status (Pending, Confirmed, Cancelled, Completed,
  Rejected) and can be searched by notes. Its URL names the agent:
  `business.fetch.ai/workbench/orders?agentId=...`.
- A bubble tea shop's ordering agent, connected through Square, has put real orders on that page,
  each with the drink, its options, the customer, and the time. How an outside agent adds orders
  isn't documented. The Integrations tab is the first place to look.
- A used-item sale maps onto those statuses: Pending when a price is agreed, Confirmed when the
  seller approves, Completed when it's paid.

Unknown, so ask the Fetch team: whether `@yardsale` can connect to our own Market agent (likely
through Integrations), whether its "custom inventory" connection could read our listings, and how
an outside agent adds orders.

## The demo, step by step

Every step happens inside one ASI:One conversation, and every sponsor is one step:

| Step | What happens | Sponsor | Built? |
| --- | --- | --- | --- |
| 1. Snap | A seller attaches a photo in ASI:One. Claude identifies it. | Claude | Yes |
| 2. Price | Our own "resale comps" Actor finds recent eBay sales. Code sets the price. | Apify | Actor pushed but blocked by eBay (403); the Researcher still calls a third-party Actor. Apify credit is nearly used up |
| 3. Approve | Cards show the sales behind the price and a form for the seller's rules. The seller approves. | Fetch (Interactive Cards) | Yes (carousel and form not yet seen in ASI:One) |
| 4. Live | The item's agent goes live on Agentverse under `@yardsale`, with its own profile page. | Fetch Business, Agentverse | Hosting yes, run live October 24; `@yardsale` no |
| 5. Found | A buyer asks ASI:One, which finds Yardsale. | Fetch (ASI:One) | Yes, without an @mention, through ASI:One's planner (October 24) |
| 6. Haggle | The buyer says their max. A buyer agent haggles with the matching item agents over several rounds, neither side revealing its limit, and a card shows every round and the best deal. | Fetch (uAgents) | Yes, live with hosted item agents, ending in a recorded deal |
| 7. Pay | The buyer accepts. The chat shows a Visa Pay By Link, and an agent buyer gets a Payment Protocol request. Once it's paid, the item is sold and Yardsale's commission is recorded. | Visa, Fetch (Payment Protocol) | Code yes; needs Visa sandbox or Stripe test keys |
| 8. Order | The sale shows up on `@yardsale`'s Fetch Business Orders page. | Fetch Business | Orders webhook yes; the Fetch Business side is unknown |

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
| `@yardsale` on Fetch Business | Not set up. Every order status change can go to `YARDSALE_ORDERS_WEBHOOK`. |
| Payment Protocol, Visa, commission | Built for test mode: Visa Pay By Link sandbox or Stripe test checkout, Payment Protocol for agent buyers, 5% commission. Not yet run against Visa or Stripe. |
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
- **Apify credit is nearly gone.** The free plan's $5 for October 14 – November 13 was $4.70
  used by October 24, which is 3 or 4 more photos. Ask the Apify team for credit before the
  demo, and don't price test photos.
- **Accepting through ASI:One's planner** failed until October 24: the planner rewrites
  "accept" as a sentence. Fixed; see [background.md](background.md).

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
   Agentverse and pitch Fetch Business as the next step.
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
   the merchant. Needs sandbox keys to try.*
8. Put closed deals on `@yardsale`'s Orders page: Pending when a price is agreed, Confirmed when
   the seller approves, Completed when it's paid. This depends on what step 4 finds, and it's the
   demo's closing moment. *October 24: every status change (pending when agreed, confirmed when
   the payment link goes out, completed when paid) is POSTed to `YARDSALE_ORDERS_WEBHOOK`;
   connecting it to Fetch Business is still unknown.*
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
6. **2:15** Open `@yardsale`'s Fetch Business workbench: the sale is there as an order.
7. **2:35** What's real today, how Yardsale makes money, and what's next: power sellers move to
   their own Fetch Business agent; eBay and Facebook listings later.

## Questions judges will ask

- **"Isn't it your agent talking to your agent?"** The haggling protocol is published, so any
  agent can take the buyer's seat. Our buyer agent stands in for the ASI:One personal AI.
- **"Is the payment real?"** It runs on Visa's API in sandbox. Going live is a change of
  credentials. Don't call it real money.
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
