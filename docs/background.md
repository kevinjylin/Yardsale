# Background and decisions

This page condenses the planning chat and the prototype session from October 22, 2026.

## The idea

1. A seller snaps a photo of an item.
2. About 20 seconds later, the item is a live agent with its own address. It knows the seller's
   secret lowest price.
3. A buyer asks ASI:One something like "find me a cheap road bike." ASI:One reaches Yardsale, and
   Yardsale connects the buyer to the matching item agents.
4. Item agents counter lowballs and close deals. The seller keeps the money.

**Demo moment:** the item agent's address appears right after the photo upload. That's the visual
proof judges will remember.

## Decisions

1. **One `@yardsale` handle on Fetch Business. Sellers don't get their own.** Fetch Business isn't a
   marketplace. It gives a business one verified agent handle, like registering a domain name
   (`@AlaskaAirlines`, `@Disney`). There are no item listings or marketplace feed, and the
   workbench is that business's back office for orders and conversations. Yardsale is the business,
   so it claims `@yardsale`. Making casual sellers verify a business would kill the snap-a-photo
   demo.
2. **One uAgent per listed item, owned by Yardsale.** An agent's identity is a keypair made from its
   seed, and our server generates the seeds, so Yardsale owns every item agent. A buyer's ASI:One
   account owns nothing. Each item agent records its seller's ID and payout details, so when a deal
   closes the money goes to the seller, not to Yardsale. Pitch: "Yardsale manages the agents,
   sellers keep the money."
3. **Item agents stay off Agentverse.** Agentverse's agent limits only count agents connected to
   your Agentverse account. The chat cited 8 hosted plus 8 mailbox agents on the free plan, and 25
   each on Premium; these figures haven't been checked against our account. Item agents run in our
   own server and register only on the Almanac, so there's no cap on them.
4. **Graduation pitch:** power sellers can later move to their own Fetch Business agent that they
   own.
5. **Photo work is split across three agents on Agentverse.** The Identifier, Researcher, and
   Appraiser each do one job, and each has its own Agentverse profile, so there's more than one
   Yardsale agent on Agentverse. With the Market they take 4 mailbox slots in total, however many
   items are listed.
6. **Sellers use the ASI:One chat. Claude reads photos, ASI:One reads text.** A seller attaches a
   photo in ASI:One, so the ASI:One app is our phone upload page. ASI:One's docs don't list image
   input for its models, so Claude does the photo work, and ASI:One's `asi1-mini` reads what the
   seller typed. This copies Maestro, the LA Hacks 2026 Fetch track winner
   ([repo](https://github.com/RogueL90/LA-Hacks-AI-Hackathon)): its agents send video frames to
   Claude and use `asi1-mini` only to read typed messages.

## Architecture

| Agent                       | Runs where                                  | Agentverse slots       |
| --------------------------- | ------------------------------------------- | ---------------------- |
| `@yardsale`                 | Fetch Business. ASI:One finds it.           | Unknown (mentor Q)     |
| Market agent ("Yardsale")   | Our Bureau server, Agentverse mailbox so ASI:One can reach it. Speaks the Chat Protocol. | 1 |
| Identifier                  | Our Bureau server, Agentverse mailbox       | 1                      |
| Researcher                  | Our Bureau server, Agentverse mailbox       | 1                      |
| Appraiser                   | Our Bureau server, Agentverse mailbox       | 1                      |
| Item agents (one per item)  | Our Bureau server, Almanac only             | 0, at any count        |
| Buyers                      | ASI:One acts for them                       | 0                      |

Buyer flow: ASI:One → `@yardsale` → Market agent → item agent. The Market relays replies back.

Seller flow: ASI:One chat (photo + text) → Market → Identifier → Researcher → Appraiser →
Market → new item agent → reply in the chat. The next sections cover it.

## Selling in the ASI:One chat

Code: [yardsale/chat.py](../yardsale/chat.py) and [yardsale/asione.py](../yardsale/asione.py).

1. The seller attaches a photo and types anything, like "sell this, at least $40, small dent".
2. ASI:One uploads the photo and sends the Market a web link to it, in a `ResourceContent` or as
   a link in the text when its planner relays the message. The Market downloads it, fixes the
   rotation, and shrinks it to a 1280 px JPEG. HEIC works.
3. `asi1-mini` reads the text into an intent (sell, buy, other), an asking price, a lowest
   price, and a note such as "size M". It uses a strict JSON schema. If ASI:One is down or
   `ASI_ONE_API_KEY` isn't set, the photo still lists at the appraised price.
4. The photo goes through the pipeline below. The reply gives the item, the asking and lowest
   prices, the three closest eBay sales as links, and the item agent's address.

Rules learned from Maestro's live testing:

- **Reply within about 60 seconds.** ASI:One's planner stops waiting after that, and pricing can
  take longer. The Market replies within 40 seconds: with the result if it's ready, or with
  "still working" and then the result as a second message.
- **Acknowledge every message first** (`ChatAcknowledgement`), before any slow work.
- **The planner repeats earlier links** in later messages, so each conversation remembers the
  photos it has already listed.
- **One conversation is one session**, not one sender. The planner relays many users from one
  address, so listings record the session as the seller ID (`asi1:session:...`).
- **The first reply carries `{"attachments": "true"}` metadata**, which tells ASI:One the agent
  accepts uploads.

Only the first photo in a message is used. Buyers asking in the chat get "not ready yet".

## Photo to listing

Photos come from the ASI:One chat or from `uv run python -m yardsale.sell photo.jpg`. Either way,
they come with an optional note and the seller's own asking or lowest price.

| Step | Agent | Does | Code |
| ---- | ----- | ---- | ---- |
| 1 | Identifier | Claude looks at the photo and returns brand, model, condition, details, and an eBay search query. It flags generic items, like an unbranded mug. | [identifier.py](../yardsale/identifier.py) |
| 2 | Researcher | Searches eBay **sold** listings from the last 90 days through the Apify actor `caffein.dev/ebay-sold-listings`, up to 20 results. It returns each sale's title, price, condition, date, link, and photo. | [researcher.py](../yardsale/researcher.py) |
| 3 | Appraiser | Claude compares the seller's photo with each sale's title and photo (up to 10 photos), and marks each sale as the same product or not, plus a 0–1 similarity score. The price is then worked out in code, not by Claude. | [appraiser.py](../yardsale/appraiser.py) |
| 4 | Market | Sends the seller the appraisal, then spawns the item agent with that price. | [bureau_server.py](../yardsale/bureau_server.py) |

**How "is this the same item?" is decided.** Claude judges each eBay sale against the photo.
"Same product" means same brand, model, and any variant or size that changes the price. Bundles,
lots, broken items, parts, and accessories don't count. Every match comes back with a one-line
reason, and the seller sees which sales the price came from.

**Pricing rules** (plain code in `appraise()`, covered by tests):

- 3 or more same-product sales: price from those only. Confidence is high with 5 or more, medium
  with 3–4.
- Fewer than 3: similar items (similarity 0.6 or higher) are added, and confidence is low.
- None of either: no price. The listing is refused unless the seller gave their own price.
- Sales that ended in an accepted Best Offer are skipped when there are others. eBay shows the
  list price for those, not what the buyer paid.
- Asking price is the median. Lowest price is the higher of the lower quartile and 70% of the
  median. Both are rounded the way a person would price: $1 steps under $20, $5 under $100, and
  $10 above that.
- A price the seller gives always wins over the appraisal.

**Agent messages.** The Market tags each request with an ID and waits for the reply with the same
ID: up to 180 s for the Identifier and 240 s each for the Researcher and Appraiser. If a service
fails or times out, the seller gets a `ListingRejected` with the reason, for example "APIFY_TOKEN
is not set". All three services handle requests concurrently, so several photos can be in
progress at once. Message types are in [models.py](../yardsale/models.py), and each agent's
Agentverse profile text is in [yardsale/readmes/](../yardsale/readmes/).

**Cost per photo:** about $0.08 of Apify (about $0.004 per sold listing, 20 listings), with a
$0.50 cap per search, plus two Claude calls (`claude-opus-5-5` at medium effort by default; change
them with `YARDSALE_CLAUDE_MODEL` and `YARDSALE_CLAUDE_EFFORT`).

**Keys** go in `.env` (copy [.env.example](../.env.example)): `ANTHROPIC_API_KEY`, `APIFY_TOKEN`,
`ASI_ONE_API_KEY`, and optionally `AGENTVERSE_API_KEY`. In live mode the server connects the four
Agentverse agents with that key. Without it, the server logs an Inspector link for each one: open
it while signed in to Agentverse, click Connect, and choose Mailbox. That only has to happen once
per agent, because agent addresses come from the master seed and don't change.

## What the prototype proved

- Item agents can be created inside a Bureau that's already running, all on one port. Each gets its
  own address and Almanac registration.
- 12 item agents registered on the real Almanac. A separate buyer process found every one through
  it and closed 12 of 12 deals. An offline run did 40 of 40.
- Almanac registration through the Almanac API is free and needs no wallet or faucet.
- Catch: `Bureau.add()` alone doesn't start or register a late-joining agent. `spawn_item_agent`
  in [yardsale/bureau_server.py](../yardsale/bureau_server.py) handles both.

## Not built or unknown

- **Public address.** Agents are reachable only at `127.0.0.1`. ASI:One needs the server on a hosted
  machine or behind a tunnel such as ngrok, with that public URL as the Bureau endpoint.
- **ASI:One end to end.** The Market speaks the Chat Protocol, publishes it to Agentverse, and
  has a mailbox, and tests cover the chat flow with fakes. Nobody has sent it a photo from the real
  ASI:One app yet, and whether ASI:One's planner routes "sell this" to it without an @mention is
  unknown.
- **Buyers in the chat.** "Find me a cheap road bike" gets "not ready yet". Search over live
  listings and haggling through chat are next on the buyer side.
- **Live buyer test needs the Market connected.** In live mode the Market's Almanac entry now
  points at its Agentverse mailbox, so `buyer_test` without `--offline` only reaches it once it's
  connected to Agentverse.
- **`@yardsale` handing off to our agents.** Unknown whether a Fetch Business agent can pass buyers
  to an outside uAgent address. Fallback: run everything on Agentverse and pitch Fetch Business as
  the graduation path for power sellers.
- **Photo to listing with real keys.** The pipeline above is built and tested offline with fake
  Claude and Apify answers, and the three agents register and publish their protocols in live mode.
  It hasn't run yet with real Claude and Apify keys or a real photo, so matching quality and
  Apify's response time are unproven.
- **Seller confirms the price.** The item agent goes live as soon as the appraisal is done. There
  is no step yet where the seller approves or edits the price first.
- **"Offline" isn't fully offline.** `--offline` skips the Almanac and Agentverse, but photo
  listings still call Claude and Apify. uAgents also checks the Fetch ledger each time an agent is
  created; without a network it logs a warning and carries on.
- **Payments.** None yet. A closed deal is only an agreed price.
- **Persistence.** Listings live in memory, so a restart forgets them. Seeds come from item IDs, so
  re-creating stored items on boot would bring back the same addresses.

## Fetch Business to-dos from the chat

These were planned in the chat; whether they're done wasn't recorded.

1. Set up `@yardsale` on Fetch Business: name, description, and a knowledge blurb about how
   Yardsale works.
2. Check whether it can hand off to an outside uAgent address, or at least point to the Market
   agent.
3. Test that ASI:One can find `@yardsale`.
4. Check the Agentverse profile page to confirm whether the "premium" from a past hackathon is
   Agentverse Premium or ASI:One premium.

## Questions for a Fetch mentor

- Can deals closed by outside agents show up as orders in `@yardsale`'s Fetch Business workbench?
- Can a Fetch Business agent hand off to an outside uAgent address?
- Does a Fetch Business agent count against the Agentverse agent limit?

## Code to borrow from Maestro

Maestro ([LA Hacks repo](https://github.com/RogueL90/LA-Hacks-AI-Hackathon)) has working
code for things Yardsale doesn't do yet: Interactive Cards (tap-to-choose replies in ASI:One,
which could let a seller approve the price) and the Payment Protocol with Stripe test mode. It
uses uagents 0.26.1; Yardsale is pinned below 0.26.
