# Cal Hacks 13.0 plan

Written on the night of Friday, October 23, 2026. Cal Hacks 13.0 runs October 23–25 in San
Francisco, and **submissions close Sunday, October 25 at 9 AM PT**. This page covers the event's
rules, Fetch's track rules, the pitch, what Fetch Business offers, how far the code is from the
pitch, and the build order. [background.md](background.md) has the product decisions and how the
code works.

## The event

From the [Cal Hacks 13.0 Devpost](https://cal-hacks-13-0.devpost.com/):

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
| At least one agent registered on Agentverse | Yes: the Market, Identifier, Researcher, and Appraiser are active mailbox agents on Agentverse (checked October 23). |
| Implements the Agent Chat Protocol | Yes, the Market. |
| Discoverable and directly usable through ASI:One | Usable by @mention. Unknown whether ASI:One picks Yardsale without one. |
| Meaningful tool use or multi-agent orchestration | Yes: three pricing agents, Apify, Claude, an agent per item. |
| The main workflow happens entirely inside an ASI:One conversation, with no custom frontend | Selling and buying happen in the chat. Payment doesn't exist yet. |
| Public GitHub repo with instructions to run or test it | The repo is public and the README has run instructions. |

**Bonus points:**

| Bonus | How Yardsale gets it |
| --- | --- |
| Effective multi-agent collaboration | The pricing agents; a buyer agent haggling with several item agents at once (to build). |
| Payment Protocol with a credible monetization model | Item agents request payment through the Payment Protocol; Yardsale keeps a commission on each sale (to build). |
| Interactive Cards | Approve card today; sales carousel, seller rules form, haggling summary, and payment card (to build). |
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
  come first. Fetch Business is the extra that sets the pitch apart.

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

Unknown, so ask the Fetch team: whether `@yardsale` can connect to our own Market agent, and
whether its "custom inventory" connection could read our listings.

## The demo, step by step

Every step happens inside one ASI:One conversation, and every sponsor is one step:

| Step | What happens | Sponsor | Built? |
| --- | --- | --- | --- |
| 1. Snap | A seller attaches a photo in ASI:One. Claude identifies it. | Claude | Yes |
| 2. Price | Our own "resale comps" Actor finds recent eBay sales. Code sets the price. | Apify | Uses a third-party Actor today |
| 3. Approve | Cards show the sales behind the price and a form for the seller's rules. The seller approves. | Fetch (Interactive Cards) | Approve card yes; sales carousel and rules form no |
| 4. Live | The item's agent goes live under `@yardsale`. | Fetch Business | Agent yes; `@yardsale` no |
| 5. Found | A buyer asks ASI:One, which finds Yardsale. | Fetch (ASI:One) | Search yes; not reachable from ASI:One without the server running live |
| 6. Haggle | The buyer says their max. A buyer agent haggles with the matching item agents over several rounds, neither side revealing its limit, and a card shows every round and the best deal. | Fetch (uAgents) | One counter-offer only; no buyer agent |
| 7. Pay | The buyer accepts. The item agent sends a Payment Protocol request, and the chat shows a Visa Pay By Link. Once it's paid, the item is sold and Yardsale's commission is recorded. | Visa, Fetch (Payment Protocol) | No |

## How close the code is

State of main after PR #1 merged on Friday, October 23 at 11:23 PM PT:

| Piece | Status |
| --- | --- |
| Photo → price from sold listings | Built. With real keys, a photo of Air Jordan 4s was priced from 20 matching eBay sales on October 23. |
| One agent per item, with its own address | Built. Registered on the Almanac, not Agentverse. |
| Seller approval, cards | Built. The Approve card showed in the real ASI:One app; whether a tap reaches the Market is unverified. |
| Buyer search and offers in the chat | Built, and tested only with fake services. |
| Haggling within the seller's limits | One counter per offer, and lowballs reveal the floor (below). |
| Buyer agent holding the shopper's max | Not built. |
| `@yardsale` on Fetch Business | Not set up. |
| Payment Protocol, Visa, commission | Not built. The protocol is installed (`uagents_core` 0.4.12, `uagents_core.contrib.protocols.payment`). |
| Our own Apify Actor | Not built. The Researcher calls `caffein.dev/ebay-sold-listings`. |
| Innovation Lab badges, agent addresses in the README | Done in the repo. Agentverse shows the badges after each agent reconnects. |
| Public URL | `YARDSALE_PUBLIC_URL` exists but hasn't been used. ASI:One reaches the Market through its Agentverse mailbox, and item agents run in the same server, so the chat flow may not need it; outside agents do. |

The installed cards module has carousel, detail, form, review, and custom cards, so the sales
carousel, rules form, and haggling card need no new library. A tapped button comes back as JSON
text after an @mention, or as plain prose when ASI:One's planner relays it, so both have to be
read.

## Known problems

- **Lowballs reveal the floor.** A counter is the higher of the floor and the halfway point
  between the offer and the asking price
  ([pricing.py](../yardsale/pricing.py), `counter_price`). With appraised prices the floor is at
  least 70% of the asking price, so any offer under about 40% of asking is countered at exactly
  the floor. Example: asking $300, floor $210. An offer of $1 gets a $210 counter, which the
  buyer can accept. The haggling rebuild in the build order should open well above the floor
  and move toward it over several rounds.
- **"20 seconds" is unproven.** The eBay search alone can take a minute. Measure it before
  promising a number on stage.
- **"Agentverse address".** Item agents register on the Almanac, not Agentverse. On stage, say
  "its own agent address" unless that changes.

## Build order

**Friday night**

1. Ask the Fetch and Visa teams the questions below.
2. Go live: restart the server so the four Agentverse profiles pick up the badges, list one real
   photo from the ASI:One app, and make one offer from a second ASI:One account. Then ask
   ASI:One "I want to sell my bike" without an @mention and see whether it picks Yardsale.
   Everything else depends on this.

**Saturday**

3. Tune the Market's Agentverse profile so ASI:One finds it without an @mention. (The badges and
   the README's agent list are done.)
4. Set up `@yardsale` on Fetch Business, following the Fetch team's answers.
5. Rebuild haggling: a buyer agent holding the shopper's max, haggling with several item agents
   at once over several rounds, with neither side revealing its limit. The result comes back as
   one card showing each round and the best deal. Publish the protocol so any agent can take the
   buyer's seat.
6. Add payment: on a deal, the item agent sends a Payment Protocol request, and the chat shows a
   Visa Pay By Link (Visa Acceptance sandbox). Once it's paid, mark the item sold and record the
   commission. Fallback: a Stripe test checkout link, which loses the Visa track.
7. Publish our own "resale comps" Actor on the Apify Store, and have the Researcher call it.
8. Add the sales carousel and the seller rules form.
9. Measure photo-to-proposal time and cut it down.

**Sunday**

10. Freeze code around 2 AM. Record the 3 to 5 minute video and share the demo chats. Finish the
    Devpost entry (text, image, video), then register through the ASI:One Submission Agent,
    which needs the Devpost and video URLs, and have every teammate join with the Team ID.
    Rehearse. Submit before 9 AM.

If time runs short, cut step 8, then step 7. Steps 2, 3, 5, and 6 are the demo and the required
pieces; keep them.

## Pitch outline (draft, 3 minutes)

The big screen shows two ASI:One chats: the seller's and the buyer's.

1. **0:00** A judge attaches a photo of an item in ASI:One. Pricing runs while we talk.
2. **0:15** The problem: moving out, stuff gets thrown away because listing and haggling are
   tedious.
3. **0:45** The gap: Fetch Business makes a business shoppable by AIs, but only if it has a
   catalog. Secondhand goods have none.
4. **1:15** The proposal arrives: the sales behind the price, approve, and the agent's address.
5. **1:45** A second judge asks ASI:One for it with a max price. The haggling card shows each
   round, the judge accepts, the Visa link goes out, and the payment clears.
6. **2:30** What's real today, how Yardsale makes money, and what's next: power sellers move to
   their own Fetch Business agent; eBay and Facebook listings later.

## Questions judges will ask

- **"Isn't it your agent talking to your agent?"** The haggling protocol is published, so any
  agent can take the buyer's seat. Our buyer agent stands in for the ASI:One personal AI.
- **"Is the payment real?"** It runs on Visa's API in sandbox. Going live is a change of
  credentials. Don't call it real money.
- **"How does Yardsale make money?"** A commission on each sale, taken before the seller's
  payout. Pricing a photo is free.
- **"Why one agent per item instead of a database?"** Each item can be found and dealt with
  directly, keeps its own private limit, and can move to the seller's own Fetch Business later.
  Item agents don't count against Agentverse's agent limit, and 40 of them ran in one server in
  an offline test.

## Questions to ask tonight

**Fetch team**

1. Can we claim `@yardsale` this weekend and connect it to our existing Market agent?
2. If not, can its "custom inventory" connection point at our listings?
3. Does using Fetch Business count toward the Fetch track, or only Agentverse and ASI:One?
4. Does a payment link opened from the chat still count as completing the workflow inside
   ASI:One? Does ASI:One show any Payment Protocol request itself?
5. Can deals closed by our agents show up as orders in `@yardsale`'s Fetch Business workbench?

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
