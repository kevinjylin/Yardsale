# Yardsale

Snap a photo of something you want to sell, and seconds later it's a live Fetch.ai agent with its
own address. The agent knows your lowest price and haggles with buyers for you. Buyers find items
by asking ASI:One something like "find me a cheap road bike."

**Status:** early prototype. One server can create a new agent for every listed item, with no cap
from Agentverse's agent limits. Each agent haggles and closes deals. A seller can attach a photo in
the ASI:One chat, and Yardsale's agents on Agentverse identify the item, find recent eBay sales,
set the price, and reply with the new item agent's address. Buying through ASI:One and payments
aren't built yet, and the chat hasn't been tried from the real ASI:One app yet.
[docs/background.md](docs/background.md) has the plan, the decisions made so far, and what's still
open.

## Run the prototype

Needs [uv](https://docs.astral.sh/uv/). Install dependencies once:

```bash
uv sync
```

Start the server in one terminal:

```bash
uv run python -m yardsale.bureau_server
```

Run the test buyer in another:

```bash
uv run python -m yardsale.buyer_test --count 12
```

The buyer lists 12 items, lowballs every item agent, accepts each counter-offer, and prints a
table with PASS/FAIL checks. It exits 0 on success. You can rerun it against the same server.

- **Live mode (default)** registers every agent on Fetch's public Almanac. This is free and needs no
  wallet. Entries point at `127.0.0.1` and expire on their own.
- **Offline mode:** add `--offline` to both commands to skip Fetch's servers entirely. Use it for
  quick checks.
- **Ports:** 8100 (server), 8101 (test buyer), and 8102 (seller). Change them with
  `YARDSALE_BUREAU_PORT`, `YARDSALE_BUYER_PORT`, and `YARDSALE_SELLER_PORT`. uAgents listens on
  `0.0.0.0`, so other devices on your network can reach these ports while the scripts run.

## Sell something from a photo

Copy `.env.example` to `.env` and add `ANTHROPIC_API_KEY`, `APIFY_TOKEN`, and `ASI_ONE_API_KEY`.
Each photo costs about $0.08 of Apify plus two Claude calls. Start the server in live mode:

```bash
uv run python -m yardsale.bureau_server
```

In live mode the Market (named "Yardsale"), Identifier, Researcher, and Appraiser use Agentverse
mailboxes. Set `AGENTVERSE_API_KEY` in `.env` and the server connects them to your account at
startup. Without it, the server logs an Inspector link for each agent: open it while signed in to
Agentverse, click Connect, and choose Mailbox. You only do this once.

**In ASI:One:** open the Yardsale agent's Agentverse profile and start a chat, or mention its
address in [ASI:One](https://asi1.ai). Attach a photo and type something like "sell this, at least
$40". Claude reads the photo, ASI:One's `asi1-mini` reads your text, and the reply gives the price,
the eBay sales behind it, and your item agent's address. If pricing takes over 40 seconds, the
first reply says it's still working and the result follows.

**From the command line:**

```bash
uv run python -m yardsale.sell photo.jpg
```

Add `--note "size M, small scratch"` to tell the agents something the photo doesn't show, and
`--ask 120 --floor 90` to set your own prices in dollars. Add `--offline` if the server runs
offline. It prints each step, the eBay sales the price is based on, and the new item agent's
address.

## Checks

```bash
uv run ruff format --check . && uv run ruff check . && uv run mypy yardsale
uv run pytest
```

The tests run the photo pipeline and the ASI:One chat through real agents in one process, with
fake Claude, Apify, and ASI:One answers, so they cost nothing and need no keys.

## How it works

All agents run inside one uAgents `Bureau`: one process, one port. A Market agent receives
listings and creates an item agent for each one while the Bureau is running. Each item agent gets
its own `agent1q...` address and its own Almanac registration. Item agents don't connect to
Agentverse, so they don't count against Agentverse's per-account agent limits. Only the Market and
the three photo agents do.

- [yardsale/bureau_server.py](yardsale/bureau_server.py): the Bureau, the Market agent, and the item
  agents. Item agents keep a private floor price, counter lowballs, and close deals. For a photo
  listing, the Market calls the three agents below in turn.
- [yardsale/identifier.py](yardsale/identifier.py), [researcher.py](yardsale/researcher.py),
  [appraiser.py](yardsale/appraiser.py): the Agentverse agents. Claude identifies the item, Apify
  finds eBay sold listings, and Claude picks which sales match before code sets the price.
  [agentverse.py](yardsale/agentverse.py) connects them to Agentverse, and
  [llm.py](yardsale/llm.py) holds the shared Claude call.
- [yardsale/chat.py](yardsale/chat.py): the Market's ASI:One chat. It finds photos in chat
  messages and replies within ASI:One's time limit. [asione.py](yardsale/asione.py) reads the
  seller's text with `asi1-mini`, and [photos.py](yardsale/photos.py) downloads and shrinks photos.
- [yardsale/sell.py](yardsale/sell.py): the seller command line tool.
- [yardsale/buyer_test.py](yardsale/buyer_test.py): a separate buyer process used as an end-to-end
  test. In live mode it can find item agents only through the Almanac, and it reaches the Market
  through its Agentverse mailbox, so connect the Market first.
- [yardsale/models.py](yardsale/models.py): message types. Prices are integer cents. A `Deal` is
  only an agreed price. **No payment is made.**
- [yardsale/offline.py](yardsale/offline.py): a local file-based stand-in for the Almanac, used
  only with `--offline`.
- [yardsale/common.py](yardsale/common.py): ports and seeds. Every agent seed is derived from one
  master secret, so the same item ID always gives the same address. The secret comes from
  `YARDSALE_MASTER_SEED`, or is generated once into `.agent-data/yardsale-bureau/master_seed`
  (gitignored, readable only by you).

The non-obvious part is adding agents to a Bureau that's already running. `Bureau.add()` connects
the new agent to the Bureau's port, and messages reach it right away. But the Bureau only starts
agents and registers them on the Almanac when it first boots. So `spawn_item_agent` does both
itself: it calls `agent.setup()`, registers the agent on the Almanac immediately, and adds it to the
Bureau's hourly re-registration batch.

## Test results (uagents 0.25.5, macOS)

| Run           | Item agents | Deals closed | Found through the Almanac  |
| ------------- | ----------- | ------------ | -------------------------- |
| Live          | 12          | 12/12        | 13/13 (Market + each item) |
| Offline       | 12          | 12/12        | not used                   |
| Offline scale | 40          | 40/40        | not used                   |

40 agents is past both the free plan's 8 and Premium's 25.

The photo pipeline and the ASI:One chat pass `pytest` with fake Claude, Apify, and ASI:One
answers. In live mode the Market publishes the Chat Protocol and the three photo agents publish
theirs. A full run with real keys, a real photo, and the real ASI:One app hasn't been done yet. The
Live row above predates the Market's Agentverse mailbox.
