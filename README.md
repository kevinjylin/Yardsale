# Yardsale

Snap a photo of something you want to sell. Yardsale identifies it, prices it from recent eBay
sales, and suggests a listing. Once you approve, the item becomes a live Fetch.ai agent with its
own address. The agent knows your lowest price and haggles with buyers for you. Buyers ask for
what they want ("find me a cheap road bike"), and make offers in the same chat.

**Status:** prototype, complete end to end offline. Selling with approval, buying with offers and
counter-offers, saved state, and restarts work and are tested with fake Claude, Apify, and ASI:One
answers. Nothing has run yet with real keys, a real photo, or the real ASI:One app (see
[Going live](#going-live)). No code moves money: a deal is an agreed price.
[docs/background.md](docs/background.md) has the plan, the decisions made so far, and what's still
open.

## Try it without keys

Needs [uv](https://docs.astral.sh/uv/). Install once:

```bash
uv sync
```

**The scripted demo** runs everything in one process: a seller's photo becomes a proposal, they
edit and approve it, an item agent appears, two buyers haggle and one wins, the server restarts
with the same agent addresses, and three failure cases. It takes about a second and exits 0 when
all 27 checks pass:

```bash
uv run python -m yardsale.demo             # add --verbose for the agents' logs
```

**The real server with fake services** behaves like the real thing on localhost, with no keys
and no network. Start it in one terminal:

```bash
uv run python -m yardsale.bureau_server --offline --fakes
```

Then, in another:

```bash
uv run python -m yardsale.sell demo/photos/bike.jpg --offline        # asks you to approve
uv run python -m yardsale.sell demo/photos/mug.jpg --offline --yes   # no close sales: refused
uv run python -m yardsale.buyer_test --count 12 --offline            # 12 listings, 12 deals
```

Stop the server and start it again: it restores every active listing's agent at the same address
(`Item agents restored: N` in the log). The fake services recognize the four photos in
[demo/photos/](demo/photos/) by color (bike, espresso machine, a mug with no close sales, and a
camera whose eBay search fails). Regenerate them with `uv run python -m yardsale.fakes demo/photos`.

Agents created offline get real uAgents addresses, but only this machine knows them: nothing is
registered on the Almanac or Agentverse, and replies say so.

## Checks

```bash
uv run ruff format --check . && uv run ruff check . && uv run mypy yardsale
uv run pytest
```

The tests (about 210, a few seconds) run the real agents in one process with fake providers.
They fail if any test looks up or connects to anything but localhost, so they can never spend
money or reach Fetch's servers.

## How selling works

1. **Photo.** The seller attaches a photo in ASI:One and can type anything, like "sell this, at
   least $40, small dent". `asi1-mini` reads the text into prices and a note (without
   `ASI_ONE_API_KEY`, a keyword reader does). The Market downloads the photo, turns it upright,
   and shrinks it to a 1280 px JPEG.
2. **Pricing.** The Identifier (Claude) names the item and writes an eBay search. The Researcher
   (Apify) finds up to 20 sold listings from the last 90 days. The Appraiser (Claude) marks which
   are the same product, and code sets the price from those ([pricing rules](#pricing-rules)).
3. **Proposal.** The reply shows the item, the asking and lowest prices and where each came from,
   and the three closest sales as links. It's saved as `proposed`; nothing is for sale yet.
4. **Approval.** The seller replies `approve`, changes a price (`ask $360`, `lowest $280`, or both),
   or `cancel`. With no close sales, there's no suggestion; the seller's own price makes the
   proposal instead.
5. **Live.** Approving starts the item agent, and the reply gives its address. `my listings` shows
   the seller's listings and sales; `unlist` takes one down.

Proposals, search results, and counter-offers also carry Interactive Cards from the installed
`uagents_core` chat cards module: Approve or Cancel, Details, Accept. A tap does exactly what the
typed command does, with the same checks. Whether ASI:One shows the cards is unverified, so the
text always works on its own.

Pricing can take over a minute, and ASI:One's planner gives up after about 60 seconds. So the
Market acknowledges every message at once and replies within 40 seconds: with the result, or with
"still working" followed by the result as a second message.

## How buying works

1. The buyer says what they want ("find me a cheap road bike under $300"). The Market searches
   active listings by word, cheapest first among equal matches. No paid service is involved.
2. They pick a result by number for details, or offer straight away (`offer $250 on 2`).
3. The Market passes the offer to the item agent. At or above the floor, the deal closes at the
   offer. Below it, the agent counters halfway to the asking price, never below the floor.
4. `accept` (or `yes`) takes the buyer's latest counter-offer, at its exact price, within 24
   hours. A newer counter replaces an older one.

Buyers never see a floor: not in search results, details, or the agents' descriptions, and a
budget filters on the asking price only. Floors also stay out of server logs, and message text
isn't logged at all. Offers above the asking price are refused in chat, and sellers can't buy
their own items.

## Pricing rules

Plain code in [pricing.py](yardsale/pricing.py) and `appraise()` in
[appraiser.py](yardsale/appraiser.py), all integer cents:

- 3 or more same-product sales: price from those only. Confidence is high with 5 or more, medium
  with 3 or 4. Same product means the same brand, model, and price-relevant variant; lots,
  bundles, parts, broken items, and accessories don't count.
- Fewer than 3: similar items (similarity of 0.6 or more) are added, and confidence is low.
- Neither: no suggested price. The seller has to give one.
- Accepted Best Offer sales show the list price, not what was paid, so they're left out whenever
  the chosen sales include any others, and the reply says how many were left out.
- Asking is the median. The floor is the higher of the lower quartile and 70% of the median,
  never above asking. Quartiles use the "exclusive" method (Python's `statistics.quantiles`
  default); with fewer than 4 sales the lower quartile is the lowest sale.
- Rounding: $1 steps under $20, $5 under $100, $10 from $100, picked from the unrounded amount.
  Halves round up ($22.50 becomes $25), and the result is at least one step.
- A price the seller gives always wins. Given only an asking price, the floor is the suggested
  floor if it's lower, or else 70% of the asking price. Given only a floor, the asking price is
  the suggestion if it's higher, or else the floor. A floor above the asking price is refused.
- Valid prices are $0.01 to $1,000,000.

## How it works

All agents run inside one uAgents `Bureau`: one process, one port (8100).

| Agent | Does | Agentverse |
| --- | --- | --- |
| Market ("Yardsale") | ASI:One chat for sellers and buyers, proposals, item agent lifecycle | mailbox |
| Identifier | Claude names the item in a photo | mailbox |
| Researcher | Apify finds eBay sold listings | mailbox |
| Appraiser | Claude matches sales; code sets the price | mailbox |
| Item agents, one per listing | hold the private floor, counter, close deals | none (Almanac only) |

Item agents don't connect to Agentverse, so there's no cap on them from Agentverse's agent
limits. Their seeds come from one master secret and the item ID, so an item always has the same
address. The master secret comes from `YARDSALE_MASTER_SEED` or is generated once into
`.agent-data/yardsale-bureau/master_seed` (gitignored, readable only by you).

The non-obvious part is adding agents to a Bureau that's already running. `Bureau.add()` connects
the new agent to the Bureau's port, and messages reach it right away. But the Bureau only starts
agents and registers them on the Almanac when it first boots. So `start_item_agent` does both
itself, and adds the agent to the Bureau's hourly re-registration batch.

**State** lives in SQLite ([store.py](yardsale/store.py)), one file per mode
(`.agent-data/yardsale-bureau/listings-{live,offline,demo}.sqlite3`, mode 0600), so demo
listings can never be registered in live mode. Listings move `proposed → active → sold`, or to
`canceled`; each change is a conditional update in a transaction, so an item sells at most once
however many buyers race. Counter-offers, deals, and each chat conversation's state are saved
too. On start, the Bureau re-creates the agent of every active listing. Approval starts the agent
first and marks the listing active second, so a crash in between leaves a proposal to approve
again, never a live listing without an agent.

**Who's who.** In the chat, one ASI:One session is one conversation. It owns its proposals and
listings, and only it can approve, change, cancel, or see their floors. The session ID is stored
only as a hash and never shown to anyone. Agents that call the Market directly are identified by
their signed address. Item agents take `buyer_ref` (a chat buyer) only from the Market, so another
agent can't accept a chat buyer's counter-offer.

### Code

- [bureau_server.py](yardsale/bureau_server.py): the Bureau, the Market's agent messages, item
  agents, and startup. `--check` prints what live mode still needs.
- [chat.py](yardsale/chat.py): the ASI:One chat for sellers and buyers.
  [commands.py](yardsale/commands.py) reads commands like `approve` and `offer $250` without a
  model, so no misreading can approve a listing or close a deal. [asione.py](yardsale/asione.py)
  reads everything else with `asi1-mini`, or with keywords when that's unavailable.
- [identifier.py](yardsale/identifier.py), [researcher.py](yardsale/researcher.py),
  [appraiser.py](yardsale/appraiser.py): the three pricing agents. [llm.py](yardsale/llm.py) holds
  the shared Claude call.
- [pricing.py](yardsale/pricing.py), [negotiation.py](yardsale/negotiation.py),
  [search.py](yardsale/search.py), [store.py](yardsale/store.py): money rules, haggling rules,
  buyer search, and saved state.
- [photos.py](yardsale/photos.py): downloads (public addresses only, checked again after every
  redirect and at connect time; 25 MB cap), rotation, HEIC, and a 50-megapixel limit.
- [config.py](yardsale/config.py): settings, the public endpoint, and the readiness check.
- [agentverse.py](yardsale/agentverse.py): Agentverse profiles and mailbox connection.
- [offline.py](yardsale/offline.py): offline mode's local stand-in for the Almanac, and the switch
  that stops uAgents from calling Fetch's ledger and Almanac when agents start.
- [fakes.py](yardsale/fakes.py), [sim.py](yardsale/sim.py), [demo.py](yardsale/demo.py): fake
  providers, the in-process harness, and the scripted demo.
- [sell.py](yardsale/sell.py), [buyer_test.py](yardsale/buyer_test.py): command line seller and
  end-to-end buyer test.
- [models.py](yardsale/models.py): agent messages. A `Deal` carries
  `payment_status="not_collected"`.

## Going live

These need accounts, keys, or a public machine, so they haven't been done or verified. Run
`uv run python -m yardsale.bureau_server --check` to see which keys are set; it never prints
values.

1. **Keys.** Copy `.env.example` to `.env` and set `ANTHROPIC_API_KEY` and `APIFY_TOKEN`
   (required for photos; each photo costs about $0.08 of Apify plus two Claude calls), and
   `ASI_ONE_API_KEY` (recommended).
2. **Public endpoint.** Item agents are reached at the Bureau's endpoint. Run the server on a
   public machine or behind a tunnel (for example `ngrok http 8100`) and set `YARDSALE_PUBLIC_URL`
   to that URL. Without it, item agents advertise `127.0.0.1`.
3. **Start live:** `uv run python -m yardsale.bureau_server`. This registers agents on Fetch's
   public Almanac (free, no wallet).
4. **Mailboxes.** Connect the Market, Identifier, Researcher, and Appraiser to Agentverse once:
   set `AGENTVERSE_API_KEY`, or open each Inspector link the server logs while signed in, click
   Connect, and choose Mailbox. Addresses don't change, so this is one-time.
5. **ASI:One photo delivery.** From the real ASI:One app, chat with the Yardsale agent, attach a
   photo, and check the server log shows it priced. Unverified: whether ASI:One's upload links
   download without authentication, and whether the planner routes "sell this" to Yardsale
   without an @mention.
6. **Sessions.** Confirm that one ASI:One chat keeps one session ID across messages. Approval and
   haggling rely on it to recognize the same person.
7. **Fetch Business.** Set up `@yardsale`, and test whether ASI:One finds it and whether it can
   hand off to the Market's address. No code depends on this; it's unverified.
8. **Live buyer test.** `buyer_test` without `--offline` reaches the Market through its
   Agentverse mailbox, so connect it first.

Payments aren't built. A deal records the agreed price and `payment_status="not_collected"`.

## Test results

| Run | Result |
| --- | --- |
| Live Almanac, 12 item agents, separate buyer process | 12/12 deals, 13/13 found on the Almanac (before the Market had a mailbox) |
| Offline, 40 item agents | 40/40 deals |
| Offline `--fakes` server, `buyer_test --count 12` | 12/12 deals |
| Offline `--fakes` server, `sell --yes`, then restart | listed, then restored at the same address |
| `yardsale.demo` | 27/27 checks |
