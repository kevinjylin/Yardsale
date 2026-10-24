# Background and decisions

This page records Yardsale's product decisions and prototype behavior during Cal Hacks 13.0,
including seller approval, haggling, payment, hosting, and saved state.

The Cal Hacks 13.0 plan (the pitch, what Fetch Business offers, what's missing, and the build
order) is in [calhacks-plan.md](calhacks-plan.md).

**Two products (decided October 24).** Sellers photograph their things and set minimums; each
item gets an agent that lists it (on ASI:One, on `@yardsale`, and in a Craigslist post the
seller pastes by hand) and haggles with every buyer automatically, never below the minimum.
Buyers either find, haggle for, and buy Yardsale listings in ASI:One or through `@yardsale`, or,
when nothing on Yardsale fits, use the deal finder: it searches eBay, Craigslist, and OfferUp
under the buyer's max and can email a Craigslist seller to negotiate for them. Selling on the
seller's own eBay is built but secondary. What's built and what's in progress is in
[calhacks-plan.md](calhacks-plan.md#the-pitch).

## The idea

1. A seller snaps a photo of an item.
2. About 20 seconds later, Yardsale suggests a listing. Once the seller approves, the item is a
   live agent with its own address. It knows the seller's secret lowest price.
3. A buyer asks ASI:One something like "find me a cheap road bike, max $300." ASI:One reaches
   Yardsale, and Yardsale's buyer agent haggles with every matching item agent at once.
4. Neither side reveals its limit. The buyer accepts the best price and pays through a link.
   Yardsale keeps a commission and the seller gets the rest.

**Demo moment:** the item agent's address appears right after the seller approves the listing.
That's the visual proof judges will remember.

## Decisions

1. **One `@yardsale` handle on Fetch Business. Sellers don't get their own.** Fetch Business isn't a
   marketplace. It gives a business one verified agent handle, like registering a domain name
   (`@AlaskaAirlines`, `@Disney`). There are no item listings or marketplace feed, and the
   workbench is that business's back office for orders and conversations. Yardsale is the business,
   so it claims `@yardsale`. Making casual sellers verify a business would kill the snap-a-photo
   demo.
2. **One uAgent per listed item, owned by Yardsale.** An agent's identity is a keypair made from its
   seed, and our server generates the seeds, so Yardsale owns every item agent. A buyer's ASI:One
   account owns nothing. Each listing records its seller's ID. Yardsale is the merchant: the buyer
   pays Yardsale, which keeps a commission (5% by default) and owes the seller the rest. Pitch:
   "Yardsale manages the agents, sellers keep the money." Payouts to sellers aren't automated.
3. **Item agents run in our server by default; a few can be hosted on Agentverse.** Agentverse's
   agent limits only count agents connected to your Agentverse account. The chat cited 8 hosted
   plus 8 mailbox agents on the free plan, and 25 each on Premium; these figures haven't been
   checked against our account. Item agents in our own server register only on the Almanac, so
   there's no cap on them. Hosting gives an item its own Agentverse profile page, which is what
   Fetch's track rewards, so `YARDSALE_HOST_ITEMS=N` hosts up to N item agents there (through
   the Hosting API) and runs the rest in the server. A hosted agent is deleted once its item is
   sold or canceled, which frees its slot.
4. **Graduation pitch:** power sellers can later move to their own Fetch Business agent that they
   own.
5. **Photo work is split across three agents on Agentverse.** The Identifier, Researcher, and
   Appraiser each do one job, and each has its own Agentverse profile, so there's more than one
   Yardsale agent on Agentverse. With the Market and the buyer agent they take 5 mailbox slots in
   total, plus any hosted item agents.
6. **Sellers use the ASI:One chat. Claude reads photos, ASI:One reads text.** A seller attaches a
   photo in ASI:One, so the ASI:One app is our phone upload page. ASI:One's docs don't list image
   input for its models, so Claude does the photo work, and ASI:One's `asi1-mini` reads what the
   seller typed. This copies Maestro, the LA Hacks 2026 Fetch track winner
   ([repo](https://github.com/RogueL90/LA-Hacks-AI-Hackathon)): its agents send video frames to
   Claude and use `asi1-mini` only to read typed messages.
7. **Haggling is two steps: agree, then accept.** An item agent agrees to any offer at or above
   its floor, but nothing is sold until the buyer accepts. So a buyer agent can haggle with
   several items at once and buy one. Below the floor, counters come down each round but stop a
   private gap short of the floor, so a counter never shows it. The protocol is published
   (`YardsaleHaggle` 1.0.0), so any agent can take the buyer's seat.
8. **Payment links, test mode only.** Fetch's own Stripe example says plain ASI:One chat often
   won't show an embedded checkout, so the chat gets a payment link (Visa Pay By Link, or Stripe
   Checkout as the fallback) and agent buyers get the same link in a Payment Protocol
   `RequestPayment`. Live keys and Visa's production host are refused.

## Architecture

| Agent                       | Runs where                                  | Agentverse slots       |
| --------------------------- | ------------------------------------------- | ---------------------- |
| `@yardsale`                 | Fetch Business (a personal agent, Plus trial). Calls Yardsale's HTTP API to search, haggle, and buy; sends sellers to the Market. | Unknown (mentor Q) |
| Market agent ("Yardsale Orchestrator", `@ys-orchestrator`) | Our Bureau server, Agentverse mailbox so ASI:One can reach it. Speaks the Chat Protocol. | 1 |
| Identifier                  | Our Bureau server, Agentverse mailbox       | 1                      |
| Researcher                  | Our Bureau server, Agentverse mailbox       | 1                      |
| Appraiser                   | Our Bureau server, Agentverse mailbox       | 1                      |
| Shopper (the buyer agent, `@ys-shopper`) | Our Bureau server, Agentverse mailbox | 1             |
| Item agents (one per item)  | Our Bureau server, Almanac only; or hosted on Agentverse (`YARDSALE_HOST_ITEMS`) | 0 in the server; 1 each hosted |
| Buyers                      | ASI:One acts for them                       | 0                      |

Buyer flow: ASI:One → Market agent (found by ASI:One) → Shopper → item agents, all at once. Or
ASI:One → `@yardsale` → Yardsale's HTTP API → Shopper → item agents. The Market relays the result back, then passes the buyer's acceptance to the
chosen item agent and sends a payment link.

Seller flow: ASI:One chat (photo + text) → Market → Identifier → Researcher → Appraiser →
Market → proposal in the chat → seller approves → new item agent → address in the chat. The next
sections cover it.

## Selling in the ASI:One chat

Code: [yardsale/chat.py](../yardsale/chat.py) and [yardsale/asione.py](../yardsale/asione.py).

1. The seller attaches a photo and types anything, like "sell this, at least $40, small dent".
2. ASI:One uploads the photo to Agentverse storage and sends the Market a `ResourceContent`
   whose `resource_id` is the stored asset. Only the agent the file was sent to can read it, so
   the Market signs the download with its own identity (`photos.download_from_storage`), the
   way Fetch's PDF summariser example does. When the planner relays a message, the photo can
   arrive as a plain web link instead, in the resource or in the text; those are downloaded
   directly. The Market fixes the rotation and shrinks the photo to a 1280 px JPEG. HEIC works.
3. `asi1-mini` reads the text into an intent (sell, buy, other), an asking price, a lowest
   price, a note such as "size M", and for buyers a search and a budget. It uses a strict JSON
   schema. If ASI:One is down or `ASI_ONE_API_KEY` isn't set, a keyword reader
   (`read_locally`) does the same job more roughly.
4. The photo goes through the pipeline below. The reply is a proposal: the item, the asking and
   lowest prices and where each came from, and the three closest eBay sales as links. Nothing is
   listed yet.
5. The seller replies `approve`, changes a price (`ask $360`, `lowest $280`), or `cancel`.
   [commands.py](../yardsale/commands.py) reads these with patterns, not a model, so a misread
   can't approve anything. Approving starts the item agent and the reply gives its address. If
   no sales were close enough, the reply asks for the seller's price, and that makes the
   proposal.

Rules learned from Maestro's live testing:

- **Reply within about 60 seconds.** ASI:One's planner stops waiting after that, and pricing can
  take longer. The Market replies within 40 seconds: with the result if it's ready, or with
  "still working" and then the result as a second message.
- **Acknowledge every message first** (`ChatAcknowledgement`), before any slow work.
- **The planner repeats earlier links** in later messages, so each conversation remembers the
  photos it has already priced. A photo that failed can be sent again.
- **One conversation is one session**, not one sender. The planner relays many users from one
  address, so a conversation is identified by a hash of its session ID (`asi1:<hash>`). It owns
  its proposals and listings; no other conversation can approve, change, or cancel them, or see
  their floors.
- **The first reply carries `{"attachments": "true"}` metadata**, which tells ASI:One the agent
  accepts uploads. A session that opens with a `StartSessionContent`-only message gets just that
  metadata and no text, as in Fetch's own examples.
- **A repeated photo that's still being priced** gets "I'm still pricing that photo" instead of
  the help text.

Only the first photo in a message is used; the others count as seen, so a repeated link doesn't
price them later. A photo link counts whatever its `mime_type` says, since ASI:One doesn't always
send a full `image/...`, and each message's attachments are logged (URI, type, metadata keys, never
the text). A message ASI:One sends twice (same message ID) gets
the same answer again instead of being acted on twice.

## Buying in the ASI:One chat

Code: [chat.py](../yardsale/chat.py), [shopper.py](../yardsale/shopper.py),
[haggle.py](../yardsale/haggle.py), [negotiation.py](../yardsale/negotiation.py), and
[checkout.py](../yardsale/checkout.py).

1. The buyer asks for something ("find me a cheap road bike under $300"). The Market searches
   active listings by word ([search.py](../yardsale/search.py)), with no paid service. A budget
   filters on the asking price only, so results never hint at a floor.
2. With a max and "haggle" ("haggle for me, max $320"), the Market sends up to five matching
   items asking at most about 1.4 times the max to the Shopper. The Shopper haggles with all of
   them at once, up to 6 rounds each, and the reply lists every round for every item with the
   best price, plus a card with a Buy button for each. Without a max, the Market asks for one.
3. Or the buyer haggles by hand: picks a result by number and offers (`offer $250 on 2`).
4. Offers reach item agents with the conversation as `buyer_ref`. Item agents accept `buyer_ref`
   only from the Market and the Shopper, so no other agent can act as a chat buyer.
5. An offer at or above the floor gets an agreement at that price. A lower one gets a counter
   that comes down each round but stops a private gap above the floor; an offer under half the
   asking price moves nothing ([haggle.py](../yardsale/haggle.py)).
6. `accept` (or `accept 2`, or a Buy button) buys at the exact agreed or countered price, within
   24 hours. Two buyers can't both buy: closing a deal is a conditional update that only one can
   win.
7. The deal gets a payment link (Visa Pay By Link or Stripe, test mode). `paid` asks the
   provider; the server also checks every minute. Paid deals record the commission and the
   seller's payout, which the seller sees under `my listings`.

## Buying through `@yardsale` (Fetch Business)

Code: [business_api.py](../yardsale/business_api.py).

`@yardsale` can't message our agents, but a Fetch Business agent on a paid plan can call an
HTTP API described by OpenAPI (an "OpenAPI Integration", which Fetch calls a Business Tool).
So the server also runs a small API on `127.0.0.1:8110` when `YARDSALE_API_KEY` is set. It
uses the same Market backend as the chat:

- `GET /listings` searches active listings (asking prices only).
- `POST /haggles` sends the Shopper to haggle with every match at once. It answers within 25
  seconds with the result, or with `status: "running"` and a `haggle_id`.
- `GET /haggles/{haggle_id}` returns the result, waiting up to 25 seconds while it's running.
- `POST /haggles/{haggle_id}/accept` buys the best price (or a named item's) and returns the
  payment link.
- `GET /deals/{deal_id}` says whether it's paid.
- `GET /openapi.json` is the description Fetch reads. It needs no key; everything else needs
  the key in `X-Yardsale-Key` (or `Authorization: Bearer`).

Each haggle is its own buyer (`buyer_ref` `api-<haggle id>`), so its prices and deals are
separate from chat buyers', and the API only shows deals made through it. Floors never appear,
and the log has one line per call (the method, the route pattern, the status, and the time),
never a buyer's max or a whole haggle ID. A running haggle answers with a plain 200, because a
tool runner may treat 202 as a failure.

Haggle IDs are 8 lowercase characters without look-alikes (no 0, o, 1, l, or i), and capitals or
spaces around them are ignored. On October 24, `@yardsale` sent a mistyped 24-character ID to
`acceptDeal`, got a 404, and told the buyer the haggle "expired". The 404 now says to copy the
ID exactly.

It runs only on this machine, so a tunnel makes it public:
`cloudflared tunnel --no-autoupdate --protocol http2 --url http://127.0.0.1:8110` (TCP, since
the default QUIC connection dropped on venue Wi-Fi). A quick tunnel gets a new URL each time it
starts. Live on October 24, `@yardsale` searched, haggled, and accepted through it; see
[calhacks-plan.md](calhacks-plan.md#what-fetch-business-offers).

## Selling on the seller's own eBay

Code: [ebay_seller.py](../yardsale/ebay_seller.py). Added October 24; **not yet run against
eBay** (no keys), only against a local stand-in in the tests.

Sellers aren't developers, so Yardsale holds one eBay developer keyset and each seller grants
access on eBay's consent page (OAuth Authorization Code grant). Yardsale never sees an eBay
password. `connect ebay` gives the link; its `state` is a random one-time token (stored hashed,
15 minutes), not the conversation ID. eBay redirects to `GET /ebay/callback` on the HTTP API
(no API key; the state is checked instead), the code becomes an access and refresh token stored
per conversation, and the next chat reply says eBay is connected. The chat can't message a
conversation unprompted, so the notice waits for the seller's next message.

On `approve` the listing also goes up on eBay. The Trading API is used rather than the Inventory
API: only the Trading API answers a buyer's Best Offer (`GetBestOffers`, `RespondToBestOffer`;
the Negotiation API has no counters), it's undocumented whether those calls work on Inventory
API listings, and `AddFixedPriceItem` needs no business-policy opt-in, policies, or merchant
location first. The listing is local pickup, no returns, Best Offer on, at the asking price,
with the seller's photo (saved in the store for this) and an eBay-suggested category. The seller
gives a ZIP code (`ebay zip 94110`). The floor never goes to eBay: no automatic accept or decline
price.

Every minute the Market answers new Best Offers with `haggle.seller_move` and the listing's floor
and private gap, keeping each eBay buyer's haggle like any other buyer's. At or above the floor
it accepts, which is a sale on eBay, and the Yardsale listing is marked sold. Below, it counters
with the agent's note as the seller message; after eBay's limit of 5 counters it declines and
says its last price stands. A purchase on eBay (Buy It Now, or a buyer taking a counter) marks
the Yardsale listing sold; a sale or take-down on Yardsale ends the eBay listing. The buyer pays
through eBay, so these deals have no Yardsale payment link or commission.

Unverified: every eBay call; whether `LocalPickup` and `ReturnsNotAccepted` inline details are
accepted for sellers opted into business policies; whether Used (3000) is a valid condition in
every suggested category; the multipart photo upload's format; and the scopes the Trading API
needs from a user token. Two sales can still race: an offer accepted on eBay in the same moment
as a Yardsale buyer's acceptance.

## Buying through Craigslist email

Code: [craigslist_mail.py](../yardsale/craigslist_mail.py). Added October 24 as a backup for the
live demo: the item's agent negotiates; email is the channel.

Craigslist bans automated posting and scraping, so the seller posts by hand. `craigslist` in the
ASI:One chat gives a title, price, and description to paste, and the email to post with
(`YARDSALE_MAIL_ADDRESS`, a Gmail account with an app password). A buyer who answers the post
writes through Craigslist's relay. Every 10 seconds the Market reads new mail from Craigslist
addresses, matches its subject to a posted title, and sends the item's agent (in the server or
hosted on Agentverse) the same `Offer` or `AcceptCounter` a chat buyer's offer becomes, with
`buyer_ref` `email-<hash of the relay address>`. The item agent's counter, agreement, or deal
becomes the email reply, sent through the relay in the buyer's thread with a payment link once
they accept. A dollar amount is an offer, and "deal", "I'll take it", or "yes" while a price is
on the table accepts it; both are read with patterns, not a model. If the item agent doesn't
answer in time, the buyer gets "let me check" once and the email is tried again.

The Market can't start a message in a seller's ASI:One chat (it keeps only a hash of the
session), so `my listings` shows Craigslist buyers and sales. Setup steps are in the
[README](../README.md#craigslist-by-email).

## Saved state

[store.py](../yardsale/store.py) keeps listings (and whether each agent runs in the server or on
Agentverse), proposals, each buyer's haggle with each item (rounds, latest price, agreed or not),
deals with their payment state, and chat conversation state in SQLite, one file per mode. Columns
added since the first version are added to older databases when the server starts. Listings move `proposed → active → sold`, or to
`canceled`. On start, the Bureau re-creates the agent of every active listing; seeds come from
the item ID, so addresses don't change. Approval starts the agent before marking the listing
active, so an interrupted approval leaves a proposal, never a live listing without an agent. A
photo still being priced when the server stops is lost; the seller is told to send it again.

## Photo to listing

Photos come from the ASI:One chat or from `uv run python -m yardsale.sell photo.jpg`. Either way,
they come with an optional note and the seller's own asking or lowest price.

| Step | Agent | Does | Code |
| ---- | ----- | ---- | ---- |
| 1 | Identifier | Claude looks at the photo and returns brand, model, condition, details, and an eBay search query. It flags generic items, like an unbranded mug. | [identifier.py](../yardsale/identifier.py) |
| 2 | Researcher | Searches eBay **sold** listings from the last 90 days through the Apify actor `caffein.dev/ebay-sold-listings`, up to 20 results. It returns each sale's title, price, condition, date, link, and photo. | [researcher.py](../yardsale/researcher.py) |
| 3 | Appraiser | Claude compares the seller's photo with each sale's title and photo (up to 10 photos), and marks each sale as the same product or not, plus a 0–1 similarity score. The price is then worked out in code, not by Claude. | [appraiser.py](../yardsale/appraiser.py) |
| 4 | Market | Saves a proposal with the prices and sends it to the seller. Spawns the item agent once the seller approves. | [bureau_server.py](../yardsale/bureau_server.py) |

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
  $10 above that. Halves round up (the first prototype used Python's `round()`, which rounds
  halves to even, so a $105 floor became $100; it's now $110). The math uses exact fractions.
- A price the seller gives always wins over the appraisal. The first prototype could raise a
  seller's asking price to the appraised floor; now the floor drops below the seller's price
  instead. [README](../README.md#pricing-rules) has the full rules.

**Agent messages.** The Market tags each request with an ID and waits for the reply with the same
ID: up to 180 s for the Identifier and 240 s each for the Researcher and Appraiser. If a service
fails or times out, the seller gets a `ListingRejected` with the reason, for example "APIFY_TOKEN
is not set". Agents other than the chat send `ListPhoto`, get `Proposed`, and answer with
`ApproveListing` or `CancelListing`; only the agent that sent the photo can. All three services handle requests concurrently, so several photos can be in
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

- **Public address.** `YARDSALE_PUBLIC_URL` sets the URL item agents advertise. It's untested:
  the server needs a hosted machine or a tunnel such as ngrok. (`@yardsale`'s HTTP API was made
  public with a Cloudflare quick tunnel on October 24; the Bureau's port hasn't been.)
- **ASI:One end to end.** The Market speaks the Chat Protocol, publishes it to Agentverse, and
  has a mailbox, and tests cover selling and buying with fakes. On October 23 photos sent from
  the real ASI:One app were priced: one was listed before seller approval was added, and one
  became a proposal whose Approve card showed in ASI:One. Uploads came as Cloudinary links with
  `mime_type` `"image"` and downloaded without authentication, and one chat kept one session ID
  across messages. ASI:One's chat page sometimes shows a slow reply only after a reload.
  On October 24, from a second ASI:One chat, the buyer agent haggled for a listed item and the
  buyer accepted; the deal was recorded. Through ASI:One's planner API (`planner_mode`), "find
  me a used hybrid bike ... haggle the price down, my max is $320", with no @mention, picked the
  Market as "the best-fit agent for buying secondhand and haggling", haggled with a hosted item
  agent to $296, and bought it when told to accept. The planner doesn't pass "accept" on word for
  word: it wrote "The buyer accepts the negotiated deal ... Please proceed to complete the
  purchase", which the Market first read as a new search. While a price is waiting, the Market
  now reads such prose as accepting it (`commands.accepts_deal`). Selling is different: asked to
  sell a lamp and sneakers, the planner offered to research prices itself and asked for photos,
  without looking for an agent. Use the @mention for selling.
  Unknown: whether tapping a card in the web app reaches the Market.
- **Real keys.** On October 23, with real Claude and Apify keys, a photo of Air Jordan 4s was
  identified and priced from 20 eBay sales of the same product. On October 24 the same kind of
  photo took 36 seconds: identify 11 s, eBay research 12 s, appraise 14 s. Matching quality for
  other kinds of items is unproven.
- **A seller's lowest price far under the appraisal.** On October 24 a seller set "at least
  $40" on shoes appraised at $190 (lowest $180), and the buyer agent's opening offer of $123 was
  agreed at once. That follows the rules, but nothing warned the seller.
- **Apify credit.** The team has $200 of Apify credit (October 24), about 2,500 photos at
  $0.08 each.
- **Probing the floor with new identities.** One buyer can't find a floor from counters or
  agreements, but someone making many identities could still narrow it down by offering just
  above and below, as on any marketplace with automatic offers.
- **Live buyer test needs the Market connected.** In live mode the Market's Almanac entry points
  at its Agentverse mailbox, so `buyer_test` without `--offline` only reaches it once it's
  connected to Agentverse.
- **`@yardsale` and our agents.** It can't message `@ys-orchestrator`, but on the Plus trial
  (started October 24) it calls Yardsale's HTTP API, so buyers can search, haggle, and buy in
  its chat. Sellers still go to `@ys-orchestrator`, since a photo has to reach Claude. Its
  Orders page takes orders only from Square or SumUp POS, so our sales don't show there. The
  API is public only through a quick tunnel, whose URL changes when it restarts. Details are in
  [calhacks-plan.md](calhacks-plan.md#what-fetch-business-offers).
- **Craigslist by email.** Tested only with a fake inbox; never run against real Craigslist
  or Gmail. Unknown: whether the relay takes replies only from the posting's own email (we reply
  from it), whether relayed subjects keep the title (matching ignores case and punctuation),
  what the relayed body contains (a quoted price in it could be read as an offer), relay
  delays, and whether links are stripped (if so, `YARDSALE_MAIL_LINKS=0` asks the buyer for a
  phone number and the seller texts the link from `my listings`).
- **Telling sellers about a sale.** The seller sees a sale under "my listings"; Yardsale doesn't
  message them when it happens.
- **Interactive Cards in the real app.** Proposals, the eBay sales behind a price (a carousel),
  the seller's prices form, search results, counter-offers, the haggling result, and payment
  carry cards built with the installed `uagents_core.contrib.protocols.chat.cards` module
  (metadata only, no protocol digest change). Only the Approve card has been seen in ASI:One's
  web app; ASI:One's planner API received the haggling card as a card for the buyer to act on.
  Every reply also works as plain text, and typed commands do the same things.
- **Payments.** Built for test mode. On October 24, a hosted test bike was haggled to $295 from a chat, and the Stripe test checkout was paid with Stripe's test card. Within a minute the server's Stripe check marked the deal paid, recorded a $14.75 commission and a $280.25 payout, and marked the listing sold. Visa hasn't been run (no sandbox keys):
  its "paid" check searches transactions for the deal ID, and whether Pay By Link payments are
  filed under it is unverified. Payouts to sellers aren't automated.
- **Hosting on Agentverse.** Run live on October 24: the Hosting API calls (create, secret,
  storage, code, start) all worked, the generated code compiled, and the agent registered on
  the Almanac about 3 seconds after starting. Two test bikes were hosted; each got a public
  profile with its README and badges, read its floor, haggled three rounds with the buyer agent
  in about 20 seconds, and reported its sale to the Market. Agentverse shows only the first 32
  characters of an agent's name, so hosted agents are named after the item, cut at a word.
- **Agent handles and pictures.** On October 24 every service agent got a picture drawn by
  ASI:One (`POST /v1/image/generate` with model `asi1-mini`; it answered with
  `{"data": [{"b64_json": ...}]}`), and a hosted Specialized Sirrus got the handle
  `@specialized-sirrus` through `PUT /v2/agents/{address}` and a drawn picture. Agentverse's
  picture upload (`/api/agent-avatar`) refused the API key, so these pictures were uploaded by
  hand on each profile page. The first drawing of the bike had made-up numbers and a tag on it,
  so the item prompt now forbids labels and measurements. Later the same day, with
  `YARDSALE_ITEM_AVATARS=1`, `--items --redraw specialized-sirrus` drew the bike, saved it in a
  public Apify key-value store, and set it in about 8 seconds. Two things were learned. A
  hosted agent has two picture fields: the hosting record's shows in Agentverse's header, and
  the profile's shows on the profile page, so the server sets both. And ASI:One wrote the
  title's words on the frame ("Specialized", "Sirrus 2.0", "M"), so item prompts now name only
  the kind of thing ("hybrid bike") and drop details with names or numbers; the next drawing
  had no text. `@yardsale`'s picture was uploaded in Fetch Business's Brand
  Studio (Edit logo, then Upload; it also offers its own AI generator) and shows on
  [asi1.ai/ai/yardsale](https://asi1.ai/ai/yardsale).
- **Our own Apify Actor.** Pushed to Apify (private) on October 24 as `resale-comps`. eBay
  answered 403 to every request through Apify's default proxy, so it returned no sales; the
  Researcher still uses `caffein.dev/ebay-sold-listings`. Getting past eBay's blocking (for
  example with residential proxies) is an open decision.
- **Sold or canceled items on the Almanac.** Their entries stay until they expire; the agent turns
  down offers, and after a restart it isn't re-created. Hosted agents are deleted from
  Agentverse instead (since October 24), retried every minute if Agentverse can't be reached.
  This is tested with a fake Agentverse only. Whether Agentverse answers 404 for an agent that's
  already gone, which the server counts as deleted, hasn't been checked.

Fixed on October 23: "offline" mode and the tests used to call Fetch's ledger and Almanac every
time an agent was created or started. `offline.stay_offline()` stops that, and the tests fail if
anything reaches beyond localhost.

## Fetch Business to-dos from the chat

These were planned in the chat. Items 1 to 3 were done on October 24.

1. Set up `@yardsale` on Fetch Business: name, description, and a knowledge blurb about how
   Yardsale works. *Done, plus a skill and starter prompts.*
2. Check whether it can hand off to an outside uAgent address, or at least point to the Market
   agent. *It can't message the Market; on the Plus trial it calls Yardsale's HTTP API instead.*
3. Test that ASI:One can find `@yardsale`. *Its page is [asi1.ai/ai/yardsale](https://asi1.ai/ai/yardsale),
   and it answers there.*
4. Check the Agentverse profile page to confirm whether the "premium" from a past hackathon is
   Agentverse Premium or ASI:One premium.

## Questions for a Fetch mentor

- Can deals closed by outside agents show up as orders in `@yardsale`'s Fetch Business workbench?
- Can a Fetch Business agent hand off to an outside uAgent address? (Ours says it can't; it
  calls our HTTP API instead.)
- Does a Fetch Business agent count against the Agentverse agent limit?

## Code to borrow from Maestro

Maestro ([LA Hacks repo](https://github.com/RogueL90/LA-Hacks-AI-Hackathon)) has a working
example of Interactive Cards in the real ASI:One app, and of the Payment Protocol with Stripe
test mode (Yardsale now has its own). It uses
uagents 0.26.1; Yardsale is pinned below 0.26 because it relies on Bureau and agent internals
tested on 0.25.5.
