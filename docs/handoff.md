# Yardsale: run and test it on your machine

Written Saturday night, October 24, 2026, for a teammate taking over the live test. It's meant to be
handed to an LLM coding assistant as its starting context. Submissions close **Sunday, October 25 at
9 AM PT**.

Everything is on `main` at `8a97be9` (github.com/kevinjylin/Yardsale). The other docs go deeper:
[CLAUDE.md](../CLAUDE.md) (rules), [background.md](background.md) (how it works),
[calhacks-plan.md](calhacks-plan.md) (pitch, demo script, judge questions),
[fetch-business.md](fetch-business.md) (the `@yardsale` workbench steps), and
[ebay-setup.md](ebay-setup.md).

## Instructions for the assistant

- Read `CLAUDE.md` first and follow it. In short: prices are integer cents; payments are test mode
  only; tests must stay offline (`uv run pytest` fails if anything leaves localhost); never print or
  commit `.env` values or the master seed; ask the person before anything that spends money
  (photo pricing, Apify searches, ASI:One calls) or creates and deletes real Agentverse agents.
- Only **one machine** may run the live server at a time. The agents' identities come from a shared
  master seed; two servers with the same seed fight over the same Agentverse mailboxes. The previous
  machine's server is stopped as of this writing.
- Start the server with `env -u ANTHROPIC_BASE_URL ...` when running from a Claude Code shell (Claude
  Code sets that variable for itself).

## What Yardsale is

Two products, built on Fetch.ai agents and Apify market data:

- **Sellers:** photograph items (up to 8 per message) and set a minimum. Claude identifies the item,
  the Researcher prices it from eBay sold listings (via Apify), and the Market Scout adds what similar
  items are listed for right now on Craigslist, OfferUp, Facebook Marketplace, and eBay. If the
  seller's price is far off (more than 35% from the market), the proposal says so plainly with the
  numbers. On approval, the item gets its own agent that holds the minimum and haggles with buyers,
  citing the market but never revealing the floor. The seller can paste a ready-made Craigslist post;
  buyers' emails through Craigslist's relay are answered by the item's agent.
- **Buyers:** say what you want and your max. Yardsale's own listings come first; when nothing fits,
  the Deal Finder agent searches eBay, Craigslist, OfferUp, and Facebook Marketplace (Apify), scores
  each listing against the eBay sold price, and explains why it's a deal. For a Craigslist post, the
  agent can email the seller and negotiate within the buyer's max.
- **Front door:** `@yardsale` on Fetch Business (OpenAPI tools: `searchListings`, `searchOtherSites`,
  `priceCheck`), and `@ys-orchestrator` in ASI:One for sellers with photos.

Agent-to-agent buying on ASI:One (the Shopper haggling with item agents) is built but **not pitched**:
a Fetch judge found it too ambitious. Leave the code; don't demo it.

## The agents (all Agentverse mailbox agents)

| Agent | Handle | Address |
| --- | --- | --- |
| Market / Orchestrator | `@ys-orchestrator` | `agent1qtm5uclxjx5ye4fhtjcqcc2d4vh5aucary75dn90lwq7808uyzvt2std9nj` |
| Identifier | `@ys-identifier` | `agent1q2efftklrnnu6z95xu43lxj4gk8jn94hjhap3n90mvfrf8t8n29mz97x60k` |
| Researcher | `@ys-researcher` | `agent1q0eltxwa77247ajr0ckhdmxgjks3r0r07k5ugypdsrg87zlau38rywefvm2` |
| Appraiser | `@ys-appraiser` | `agent1q04thzahn3fg54reqd26xlaqq2nqzr42x4992sx0xhvwjfrkz3dq6wdxx5k` |
| Market Scout | `@ys-scout` | `agent1qt80yx8uhmx63zmjg22gaa958llaq24qpfp67xq0x0karj4n55fdq5aswtq` |
| Deal Finder | `@ys-dealfinder` | `agent1q0n2cws3nm9lrg4332jfl90asxvmrr0zmnzn69yhptaw2y6jds92wtqyjkd` |
| Shopper (not pitched) | `@ys-shopper` | `agent1qgf2ezv39kw6gtxjdcv9lltyxgldvk6xr7uxzlfe9gk2wpcyv8fa2y7lnqs` |

If the server starts with **different** addresses, the master seed is wrong (see setup step 3).

## Setup

1. **Code and tools.** Python via `uv`, and `cloudflared` for the public URL:

   ```bash
   git clone https://github.com/kevinjylin/Yardsale.git && cd Yardsale
   git checkout main && git pull
   uv sync
   brew install cloudflared
   ```

2. **`.env`** (copy `.env.example`, gitignored). Get the values privately from Joshua, never through a
   group chat or a repo:

   | Key | What it's for |
   | --- | --- |
   | `ANTHROPIC_API_KEY` | Claude reads photos and judges which eBay sales match |
   | `APIFY_TOKEN` | eBay sold comps and the four live-listing sources ($200 credit) |
   | `ASI_ONE_API_KEY` | `asi1-mini` reads what sellers type |
   | `AGENTVERSE_API_KEY` | connects the 7 agents' mailboxes at startup |
   | `YARDSALE_MASTER_SEED` | the agents' identities (see step 3) |
   | `YARDSALE_API_KEY` | `@yardsale`'s key for the HTTP API (`X-Yardsale-Key` header) |
   | `STRIPE_SECRET_KEY` + `YARDSALE_PAYMENTS=stripe` | test-mode payment links (`sk_test_...` only) |
   | `YARDSALE_MAIL_ADDRESS=yardsaleagent@gmail.com` + `YARDSALE_MAIL_PASSWORD` | Craigslist email: a Gmail **app password**, not the account password |
   | `YARDSALE_ITEM_AVATARS=1` | optional; only matters with hosted item agents |

   Optional: `YARDSALE_HOST_ITEMS=2` (item agents get their own Agentverse profile page; creates and
   deletes real agents), `YARDSALE_FACEBOOK=1` (Facebook Marketplace in the deal finder; costs the most
   per search), `YARDSALE_ZIP` (local asking prices). Leave `YARDSALE_CAMPUS` unset: it turns on
   the student-only flow, which blocks anyone who hasn't given a school email from listing
   or searching.

3. **The master seed.** It isn't in `.env` on the machine that created it; it's the file
   `.agent-data/yardsale-bureau/master_seed`. Put its contents in `.env` as `YARDSALE_MASTER_SEED=...`.
   Check the addresses before going live (offline, free):

   ```bash
   uv run python -c "from uagents_core.identity import Identity; from yardsale.common import derive_seed; print(Identity.from_seed(derive_seed('market'), 0).address)"
   ```

   It must print `agent1qtm5uclx...2std9nj`.

4. **Check readiness and the mail login** (no paid calls; the mail check sends nothing):

   ```bash
   uv run python -m yardsale.bureau_server --check
   uv run python -m yardsale.craigslist_mail --check
   ```

## Run it

Make sure nobody else's server is running, then, in one terminal:

```bash
env -u ANTHROPIC_BASE_URL uv run python -m yardsale.bureau_server
```

A good start logs `connected to Agentverse` for all 7 agents, `Payments: Stripe (test mode)`, and
`Craigslist email: answering buyers every 10 s`.

In a second terminal, the public URL for `@yardsale`:

```bash
cloudflared tunnel --no-autoupdate --protocol http2 --url http://127.0.0.1:8110
```

Copy the `https://....trycloudflare.com` URL it prints, and check it (should list `searchListings`,
`searchOtherSites`, `priceCheck` first):

```bash
curl https://YOUR-URL.trycloudflare.com/openapi.json
```

**Every new tunnel URL must be re-entered in Fetch Business:** business.fetch.ai/workbench →
Integrations → "Yardsale Market" OpenAPI integration → URL `https://YOUR-URL.trycloudflare.com/openapi.json`,
header `X-Yardsale-Key` = `YARDSALE_API_KEY`. The Skill, Business Information, and starter-prompt text
are in [fetch-business.md](fetch-business.md). Keep the tunnel running through the demo.

## Test checklist

Costs: a photo is about $0.20 (Apify + two Claude calls, plus a few cents for the Scout's sources);
a deal search about $0.10 ($0.16 with Facebook).

1. **Seller, in ASI:One** (asi1.ai, message `@ys-orchestrator`): attach a photo and type "sell this,
   at least $20" for something worth much more.
   - Expect within about a minute: the item, the eBay sold price and the 3 closest sales, local asking
     prices from the Scout, a fair range, and a plain warning that $20 is far under the market.
   - Say `lowest 150` (or something sane), then `approve`. The reply gives the item agent's address.
2. **Craigslist post:** say `craigslist`. Post the title, price, and description it gives on
   craigslist.org by hand (Craigslist bans automated posting), with yardsaleagent@gmail.com as the
   email, and click the confirmation link Craigslist emails. Paste the **title exactly**: it's how
   buyers' emails are matched to the item.
3. **Buyer on Craigslist, from a phone:** reply to the post with a lowball ("would you take $40?"),
   then a closer offer, then "deal". Expect counters that cite the market and never show the floor,
   and a Stripe test link at the end (card `4242 4242 4242 4242`, any future date, any CVC). Then
   `my listings` in the seller's chat should show it sold.
   - If the payment link never arrives, Craigslist's relay may strip links: set
     `YARDSALE_MAIL_LINKS=0` and restart.
4. **`@yardsale`** (asi1.ai/ai/yardsale), after the Fetch Business re-import:
   - "What's my Breville Bambino Plus worth?" → `priceCheck`.
   - "Best deal on a road bike under $300" → `searchOtherSites`, with a score and a reason per deal.
5. **Deal finder in the ASI:One chat:** "find me a mini fridge under $60" → nothing on Yardsale, then
   scored deals from other sites. `watch for it` saves a standing search.
6. **Negotiate with a Craigslist seller for a buyer:** tap Reply on any Craigslist post, copy its relay
   address, and in the chat say
   `negotiate abc123@sale.craigslist.org "Trek FX 2" max $250`. The agent emails the seller as the
   buyer's AI assistant; `negotiations` shows progress.

## Useful commands

```bash
uv run ruff format --check . && uv run ruff check . && uv run mypy yardsale
uv run pytest                                                  # offline, no keys
uv run python -m yardsale.elsewhere --probe "road bike" --max 300 --zip 94103   # paid, ~$0.10
uv run python -m yardsale.hosting --list                       # hosted item agents
```

## Known issues and what's unverified

- **Unverified live:** a photo priced with the Scout in the pipeline; the price warning and
  market-citing counters in the real ASI:One app; the full Craigslist round trip (does the relay keep
  the title in the subject, keep our Message-IDs, strip payment links, how long it takes);
  `@yardsale` calling `priceCheck` and `searchOtherSites`; the OfferUp result cap and the corrected
  quality fields since their fixes. The one live deal-finder probe parsed all four sources.
- **Facebook and Craigslist** listings don't carry condition or listing date (the actors only give
  those with paid per-listing detail options).
- **Craigslist search vs. its terms:** the deal finder reads Craigslist through a third-party Apify
  actor, and Craigslist's terms ban scraping. A team decision, noted in the PR.
- **eBay selling** on the seller's own account (`ebay_seller.py`) is built but needs eBay developer
  keys; see [ebay-setup.md](ebay-setup.md). Not part of the demo.
- **Deploying off a laptop:** a Fly.io setup exists on Joshua's local branch `deploy-host` (not
  pushed, image build unverified).

## Troubleshooting

- **"Handle is already taken" at startup, and new addresses:** the master seed is missing or wrong.
  Stop, fix `YARDSALE_MASTER_SEED`, restart.
- **`Failed to retrieve messages: 503`** from an agent now and then: Agentverse's side; it retries.
- **`Couldn't read the Craigslist inbox (abort)`** once: Gmail dropped an idle connection; it
  reconnects on the next check.
- **"certificate is not valid for agentverse.ai":** the network is intercepting HTTPS (venue Wi-Fi
  sign-in page). Use a phone hotspot.
- **Gmail app passwords "not available for your account":** turn on 2-Step Verification with a phone
  number or authenticator app (a passkey alone doesn't count).
- **ASI:One shows no reply:** reload the chat page; slow replies sometimes only appear after a reload.
- **Planner relays "accept" as prose:** handled (`commands.accepts_deal`).
