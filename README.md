# Yardsale

Snap a photo of something you want to sell, and seconds later it's a live Fetch.ai agent with its
own address. The agent knows your lowest price and haggles with buyers for you. Buyers find items
by asking ASI:One something like "find me a cheap road bike."

**Status:** early prototype. One server can create a new agent for every listed item, with no cap
from Agentverse's agent limits. Each agent haggles and closes deals. The ASI:One connection, photo
upload, and payments aren't built yet. [docs/background.md](docs/background.md) has the plan, the
decisions made so far, and what's still open.

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
- **Ports:** 8100 (server) and 8101 (test buyer). Change them with `YARDSALE_BUREAU_PORT` and
  `YARDSALE_BUYER_PORT`. uAgents listens on `0.0.0.0`, so other devices on your network can reach
  these ports while the scripts run.

Checks:

```bash
uv run ruff format --check . && uv run ruff check . && uv run mypy yardsale
```

## How it works

All agents run inside one uAgents `Bureau`: one process, one port. A Market agent receives
listings and creates an item agent for each one while the Bureau is running. Each item agent gets
its own `agent1q...` address and its own Almanac registration. None of them connects to Agentverse,
so they don't count against Agentverse's per-account agent limits.

- [yardsale/bureau_server.py](yardsale/bureau_server.py): the Bureau, the Market agent, and the item
  agents. Item agents keep a private floor price, counter lowballs, and close deals.
- [yardsale/buyer_test.py](yardsale/buyer_test.py): a separate buyer process used as an end-to-end
  test. In live mode it can find item agents only through the Almanac.
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

## Test results (October 22, 2026, uagents 0.25.5, macOS)

| Run           | Item agents | Deals closed | Found through the Almanac  |
| ------------- | ----------- | ------------ | -------------------------- |
| Live          | 12          | 12/12        | 13/13 (Market + each item) |
| Offline       | 12          | 12/12        | not used                   |
| Offline scale | 40          | 40/40        | not used                   |

40 agents is past both the free plan's 8 and Premium's 25.
