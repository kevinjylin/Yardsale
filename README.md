# Yardsale

![tag:innovationlab](https://img.shields.io/badge/innovationlab-3D8BD3)
![tag:hackathon](https://img.shields.io/badge/hackathon-5F43F1)

Yardsale is two products built on Fetch.ai agents.

**For sellers.** Built for a student moving out right before finals, who can't answer 200 DMs
while selling everything in their dorm. Take a photo of each thing and say your minimum ("sell
this, at least $40"). Yardsale identifies it, prices it from recent eBay sales, and suggests a
listing. Once you approve, the item gets its own agent that knows your minimum. The listing is
on ASI:One and `@yardsale`, and Yardsale writes a Craigslist post for you to paste. The item's
agent haggles with every buyer, in ASI:One or by email from the Craigslist post, and never goes
below your minimum or shows it.

**For buyers.** Say what you want and the most you'll pay, in ASI:One or to `@yardsale` on Fetch
Business ("find me a hybrid bike, haggle for me, max $320"). Yardsale's buyer agent haggles with
every matching item agent at once, over several rounds, without either side revealing its limit,
and you pay through a link. When nothing on Yardsale matches, the deal finder searches eBay,
Craigslist, and OfferUp for the best deals under your max, and can email a Craigslist seller for
you and negotiate the price.

Yardsale keeps a commission on the sales it collects payment for; the seller gets the rest.

**Status:** prototype, built at Cal Hacks 13.0.

- **Built and run live** (October 23–24): photos from the real ASI:One app priced with real
  Claude and Apify keys; seller approval; item agents, some hosted on Agentverse; haggling and
  buying from the ASI:One app, through ASI:One's planner (which found Yardsale without an
  @mention), and through `@yardsale` on Fetch Business; and a Stripe test payment end to end.
- **Built, tested only with fakes:** the item agent answering Craigslist buyers by email
  ([Craigslist by email](#craigslist-by-email)).
- **In progress:** several photos in one message; the deal finder in the chat and `@yardsale`
  (the eBay and Craigslist searches through Apify are written, OfferUp isn't); and emailing
  Craigslist sellers for a buyer.
- **Also built, not on the main path:** listing on the seller's own eBay, waiting on eBay
  developer keys ([Selling on the seller's own eBay](#selling-on-the-sellers-own-ebay)).

Claude reads the photos and `asi1-mini` reads typed text; ASI:One finds and routes to the agents.
Payments are test mode only (Stripe test keys, or Visa's sandbox, which hasn't been run): live
keys are refused. Tests use fake Claude, Apify, ASI:One, and payment answers; the server always
uses the real services. [docs/background.md](docs/background.md) has the decisions made so far
and what's still open, and [docs/calhacks-plan.md](docs/calhacks-plan.md) has the Cal Hacks plan
and the pitch.

## Agents

Yardsale was started during [Cal Hacks 13.0](https://cal-hacks-13-0.devpost.com/), October 23–25,
2026. These agents are Agentverse mailbox agents and run in one Bureau server:

| Agent | Handle | Address | Does |
| --- | --- | --- | --- |
| [Yardsale Orchestrator](https://agentverse.ai/agents/details/agent1qtm5uclxjx5ye4fhtjcqcc2d4vh5aucary75dn90lwq7808uyzvt2std9nj/profile) (the Market) | `@ys-orchestrator` | `agent1qtm5uclxjx5ye4fhtjcqcc2d4vh5aucary75dn90lwq7808uyzvt2std9nj` | Talks to sellers and buyers in ASI:One through the Agent Chat Protocol. Runs pricing, proposals, search, and offers, and starts an item agent for each approved listing. |
| [yardsale-identifier](https://agentverse.ai/agents/details/agent1q2efftklrnnu6z95xu43lxj4gk8jn94hjhap3n90mvfrf8t8n29mz97x60k/profile) | `@ys-identifier` | `agent1q2efftklrnnu6z95xu43lxj4gk8jn94hjhap3n90mvfrf8t8n29mz97x60k` | Claude works out what's in a photo and writes an eBay search for it. |
| [yardsale-researcher](https://agentverse.ai/agents/details/agent1q0eltxwa77247ajr0ckhdmxgjks3r0r07k5ugypdsrg87zlau38rywefvm2/profile) | `@ys-researcher` | `agent1q0eltxwa77247ajr0ckhdmxgjks3r0r07k5ugypdsrg87zlau38rywefvm2` | Finds what that product sold for on eBay in the last 90 days, through Apify. |
| [yardsale-appraiser](https://agentverse.ai/agents/details/agent1q04thzahn3fg54reqd26xlaqq2nqzr42x4992sx0xhvwjfrkz3dq6wdxx5k/profile) | `@ys-appraiser` | `agent1q04thzahn3fg54reqd26xlaqq2nqzr42x4992sx0xhvwjfrkz3dq6wdxx5k` | Claude picks which sales are the same product; code sets the asking price and the floor. |
| [yardsale-shopper](https://agentverse.ai/agents/details/agent1qgf2ezv39kw6gtxjdcv9lltyxgldvk6xr7uxzlfe9gk2wpcyv8fa2y7lnqs/profile) | `@ys-shopper` | `agent1qgf2ezv39kw6gtxjdcv9lltyxgldvk6xr7uxzlfe9gk2wpcyv8fa2y7lnqs` | The buyer agent: haggles with several item agents at once for a shopper, never above their max. New; it connects to Agentverse the next time the server starts live. |

Every approved listing also gets its own **item agent**, which holds the seller's private floor
and haggles with buyers. By default item agents run in the Bureau and register on Fetch's Almanac
only, so there's no limit on how many there are. With `YARDSALE_HOST_ITEMS` set, the first few are
created on Agentverse instead, each with its own profile page
([Hosting on Agentverse](#hosting-on-agentverse)), and deleted from Agentverse once the item is
sold or taken down. The chat gives each one's address when the seller approves.

**Try it in ASI:One:** open [asi1.ai](https://asi1.ai), start a chat with the Yardsale
Orchestrator (from its Agentverse profile, or by mentioning `@ys-orchestrator` or its address),
attach a photo, and type something like "sell this, at least $40". To buy, ask for something
like "find me a road bike, haggle for me, max $300". The agents answer only while the server is
running.

**To run it yourself** you need these accounts and keys (put them in `.env`; see
[.env.example](.env.example)):

- [Anthropic API key](https://console.anthropic.com/) (`ANTHROPIC_API_KEY`): Claude reads the
  photos and picks matching sales.
- [Apify token](https://console.apify.com/) (`APIFY_TOKEN`): eBay sold listings through the
  [caffein.dev/ebay-sold-listings](https://apify.com/caffein.dev/ebay-sold-listings) Actor. About
  $0.08 per photo.
- [ASI:One API key](https://asi1.ai/developer) (`ASI_ONE_API_KEY`, recommended): `asi1-mini` reads what
  people type. Without it, a simpler keyword reader is used.
- [Agentverse API key](https://agentverse.ai/) (`AGENTVERSE_API_KEY`, optional): connects the
  five agents' mailboxes at startup and uploads their profile READMEs from
  [yardsale/readmes/](yardsale/readmes/). With `YARDSALE_HOST_ITEMS`, it also hosts item agents.
- Payment links (optional; without them a deal is an agreed price only): a
  [Visa Acceptance sandbox](https://developer.visaacceptance.com/) account for Pay By Link
  (`VISA_MERCHANT_ID`, `VISA_KEY_ID`, `VISA_SHARED_SECRET`), or a
  [Stripe](https://dashboard.stripe.com/test/apikeys) test key (`STRIPE_SECRET_KEY=sk_test_...`).
- `YARDSALE_API_KEY` (optional): turns on the HTTP API that `@yardsale` on Fetch Business calls.
  It's any random string of 24 or more characters; see [`@yardsale` on Fetch
  Business](#yardsale-on-fetch-business).

The addresses above come from this deployment's master seed. A server started with a different
seed gets different addresses.

## Run it locally

Needs [uv](https://docs.astral.sh/uv/). Install once:

```bash
uv sync
```

The server always uses the real Claude, Apify, and ASI:One. `--offline` only keeps it off Fetch's
Almanac and Agentverse. Start it in one terminal:

```bash
uv run python -m yardsale.bureau_server --offline
```

Then, in another:

```bash
uv run python -m yardsale.buyer_test --count 12 --offline   # 12 listings, 12 deals; no keys
uv run python -m yardsale.sell photo.jpg --offline          # needs .env keys; spends Apify/Claude
```

`buyer_test` lists items with their prices already set, so it needs no keys. A photo listing
needs `ANTHROPIC_API_KEY` and `APIFY_TOKEN` (see [.env.example](.env.example)). Stop the server
and start it again: it restores every active listing's agent at the same address
(`Item agents restored: N` in the log).

Agents created offline get real uAgents addresses, but only this machine knows them: nothing is
registered on the Almanac or Agentverse, and replies say so.

## Checks

```bash
uv run ruff format --check . && uv run ruff check . && uv run mypy yardsale
uv run pytest
```

The tests (about 290, a few seconds) run the real agents in one process with fake providers.
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
2. **Haggle for me.** With a max ("haggle for me, max $320", or `haggle $320` after a search), the
   Market sends the buyer agent (the Shopper) the matching items asking up to about 1.4 times the
   max, five at most. The Shopper haggles with all of them at once and the reply, with a card,
   shows every round for every item and the best price. `accept` buys the best one, `accept 2`
   another, or tap Buy on the card.
3. **Or haggle yourself.** Pick a result by number for details, then offer (`offer $250 on 2`).
   Each offer gets an answer from the item agent: agreed, or a counter.
4. `accept` (or `yes`) buys at the latest counter or agreed price, exactly, within 24 hours.
5. **Pay.** The reply has a payment link (Visa Pay By Link or Stripe, test mode) and a card with
   "I've paid". `paid` checks with the provider; `my orders` lists purchases. The server also
   checks pending payments every minute. Without payment keys, the deal is an agreed price only.

**The haggling rules** ([haggle.py](yardsale/haggle.py)), the same wherever an item agent runs:

- An item agent agrees to any offer at or above the seller's floor. An agreement isn't a sale:
  the buyer closes it with `accept`, so a buyer agent can haggle with several items and buy one.
- Below the floor it counters, keeping 60% of the remaining distance each round, but its counters
  stop short of the floor by a private gap (15 to 35% of the asking-to-floor spread, set by a
  secret for each item). So no counter shows the floor, even to someone who knows these rules.
- An offer under half the asking price is a lowball, and the agent doesn't come down for it.
- Counters never go back up, and once an agent has agreed a price with a buyer, that buyer's
  lower offers get the same price back, so agreements can't be used to probe for the floor.
- The buyer agent opens at 65% of the asking price, meets each counter halfway, makes its last
  offer at the max, and gives up after 6 rounds. It never offers above the max, and the max
  isn't logged.

Any agent can take the buyer's seat with the published protocol, **YardsaleHaggle 1.0.0**
(`Offer` → `Counter` with `agreed`, then `AcceptCounter` → `Deal`). An agent buyer that closes a
deal gets a `RequestPayment` from the Market through Fetch's Agent Payment Protocol, with the
payment link, and answers `CommitPayment` once it has paid. The
[Shopper's profile](yardsale/readmes/shopper.md) documents the messages.

Buyers never see a floor: not in search results, details, cards, or the agents' descriptions,
and a budget filters on the asking price only. Floors and buyers' maxes stay out of server logs,
and message text isn't logged at all. Offers above the asking price are refused in chat, and
sellers can't buy their own items. Known limit: a buyer who keeps making new identities could
still narrow down a floor, as on any marketplace with automatic offers.

## Payments

Yardsale is the merchant for every sale ([payments.py](yardsale/payments.py),
[checkout.py](yardsale/checkout.py)). When a deal closes, the Market makes one payment link for
it, and the deal goes `not_collected → pending → paid` (or `expired` when the provider says the
link expired, or `rejected` when an agent buyer declines). The provider decides whether a payment happened; a buyer saying so never marks a
deal paid. When it's paid, the deal records Yardsale's commission (`YARDSALE_COMMISSION_PERCENT`,
5% by default, rounded half up to the cent) and the seller's payout, and `my listings` shows the
seller what they get. Payouts to sellers aren't automated.

- **Visa Pay By Link** (Visa Acceptance sandbox, `apitest.visaacceptance.com` only). Requests are
  signed with Visa's HTTP Signature (HMAC-SHA256). Payment is checked by searching transactions
  for the deal ID. Not yet run against Visa.
- **Stripe Checkout**, test keys only. Run live on October 24: a $295 deal was paid with
  Stripe's test card and marked paid by the server's check within a minute.

Each order's status change can also be POSTed to `YARDSALE_ORDERS_WEBHOOK`, signed with
`YARDSALE_ORDERS_SECRET` ([orders.py](yardsale/orders.py)). It's meant for an order list such as
`@yardsale`'s Orders page on Fetch Business, though that page takes orders only from a
connected Square or SumUp POS account.

## Selling on the seller's own eBay

A seller can also put approved listings on their own eBay account
([ebay_seller.py](yardsale/ebay_seller.py)). **Not yet run against eBay**, sandbox or
production; it's tested only against a local stand-in.

- In the chat, `connect ebay` (optionally with a ZIP code: `connect ebay 94110`) gives a link to
  eBay's own consent page. Sellers sign in on eBay; Yardsale never asks for or stores an eBay
  password. eBay sends them back to `/ebay/callback` on the HTTP API, and the next chat reply says
  eBay is connected. `ebay zip 94110` sets the pickup ZIP code, `list on ebay` retries or lists
  older listings, and `disconnect ebay` ends the seller's eBay listings and deletes the tokens.
- On `approve`, the listing also goes up on eBay through the Trading API (photo upload, a
  suggested category, `AddFixedPriceItem`): the asking price, Best Offer on, local pickup, no
  returns. The floor is never sent to eBay, so eBay's automatic accept and decline prices stay
  unset. If eBay fails, the Yardsale listing is still live and the reply says why.
- Every minute the Market answers new Best Offers with the same haggling rules as item agents
  (`haggle.seller_move`): at or above the floor it accepts (a sale), below it counters with the
  agent's note as the message the buyer reads. eBay allows 5 counters per side, so after 5 the
  agent declines and says its last price stands. A sale on eBay marks the Yardsale listing sold;
  a sale or take-down on Yardsale ends the eBay listing.

Setup: one eBay developer keyset for Yardsale (`EBAY_CLIENT_ID`, `EBAY_CLIENT_SECRET`,
`EBAY_RUNAME`; sandbox unless `YARDSALE_EBAY_ENV=production`), plus `YARDSALE_API_KEY` and a
tunnel, since the callback is on the HTTP API. In eBay's developer portal (User Tokens), set the
RuName's auth accepted URL (and declined URL) to `<tunnel URL>/ebay/callback`; a new quick
tunnel URL means updating it. Scopes requested: `api_scope`, `sell.inventory`, `sell.account`.

## Craigslist by email

A listing's own agent can also haggle with people who find it on Craigslist
([craigslist_mail.py](yardsale/craigslist_mail.py)). The item's agent negotiates; email is only
the channel. Craigslist bans automated posting and scraping, so Yardsale never touches
craigslist.org: the seller posts by hand, and buyers' messages reach Yardsale's inbox through
Craigslist's email relay.

1. After approving a listing, the seller says `craigslist` (or `craigslist 2`, numbered as in
   `my listings`) in the ASI:One chat. The reply is a ready-to-paste title, price, and
   description, and the email to post with. The title is how a buyer's email is matched to the
   listing, so it has to be pasted exactly.
2. The seller posts it on Craigslist with Yardsale's inbox (`YARDSALE_MAIL_ADDRESS`) as the
   posting's email.
3. A buyer replies to the post. Craigslist's relay forwards it to the inbox from an anonymised
   `@...craigslist.org` address.
4. Every 10 seconds the Market reads new mail from Craigslist addresses (IMAP, without marking
   other mail read), and sends the item's agent, in the server or hosted on Agentverse, the same
   `Offer` or `AcceptCounter` a chat buyer's offer becomes, with `buyer_ref` `email-<hash of the
   relay address>`. A dollar amount is an offer ("would you take $250?", "can you do 280");
   "deal", "I'll take it", or "yes" while a price is on the table accepts it; anything else gets
   the asking price. No price is worked out outside the item agent.
5. The item agent's answer goes back through the relay (SMTP), in the buyer's thread: a
   counter that never shows the floor, an agreement, or, once the buyer accepts, the deal with
   its payment link (test mode). Closing uses the same conditional update as every sale, so two
   buyers can't both buy. If the item agent doesn't answer in time, the buyer gets "let me check
   and get back to you" once, and the email is tried again on the next check.
6. The Market can't message the seller's ASI:One chat on its own, so `my listings` shows how many
   Craigslist buyers have emailed, the highest offer, and a sale with its payment link.

Logs name a buyer by a short hash. Email addresses, message text, prices, and the mail password
never appear in them, and the password is only ever read from `.env`.

**Setting it up before the demo:**

1. Use a Gmail account made for this. Turn on 2-Step Verification, then create an app password
   at [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords). IMAP is on
   by default for Gmail accounts.
2. In `.env`: `YARDSALE_MAIL_ADDRESS=<that Gmail address>` and `YARDSALE_MAIL_PASSWORD=<the app
   password>` (spaces are fine). `YARDSALE_IMAP_HOST` and `YARDSALE_SMTP_HOST` default to
   Gmail's.
3. Check the login (it reads the inbox and logs in to SMTP; it sends nothing):
   `uv run python -m yardsale.craigslist_mail --check`
4. Start the server. Its log says `Craigslist email: answering buyers every 10 s`.
5. List the item in ASI:One, approve it, say `craigslist`, and post the reply's title, price, and
   description on Craigslist with that Gmail address. Craigslist emails a link to confirm the
   post first.
6. From a phone, reply to the post through Craigslist with an offer.

**Unverified:** none of this has been run against real Craigslist. Unknown: whether the relay
accepts replies only from the email the post was made with (Yardsale replies from that same
address, so it should), whether relayed subjects keep the title as typed (matching ignores
case and punctuation), how long the relay takes, what the relayed message body contains, and
whether the relay strips links. If it strips the payment link, set `YARDSALE_MAIL_LINKS=0`: the
reply then gives the amount and asks for a phone number, and the seller texts the link shown
under `my listings`.

## `@yardsale` on Fetch Business

`@yardsale` ([asi1.ai/ai/yardsale](https://asi1.ai/ai/yardsale)) is Yardsale's Fetch Business
agent. It can't message our agents, but it calls Yardsale's HTTP API
([business_api.py](yardsale/business_api.py)) through an OpenAPI Integration, so a buyer can
search, haggle, and buy in its chat. Sellers still go to `@ys-orchestrator`, because a photo has
to reach Claude.

| Call | Tool | Does |
| --- | --- | --- |
| `GET /listings?q=&max_usd=` | `searchListings` | Active listings, with asking prices only |
| `POST /haggles` `{"looking_for", "max_usd"}` | `startHaggle` | The buyer agent haggles with every match at once. Answers within 25 s with the result, or `status: "running"` |
| `GET /haggles/{haggle_id}` | `getHaggle` | The result; waits up to 25 s while it's running |
| `POST /haggles/{haggle_id}/accept` `{"item_id"?}` | `acceptDeal` | Buys the best price (or the named item's) and returns the payment link |
| `GET /deals/{deal_id}` | `getDeal` | Whether it's paid |
| `GET /openapi.json` | | The OpenAPI 3.0 description Fetch reads; no key needed |

Every call except `/openapi.json` needs the key in `X-Yardsale-Key` (or
`Authorization: Bearer`). The API never shows a floor, and its log line for each call has the
method, the route pattern, the status, and the time, never a buyer's max.

To connect it (needs a paid Fetch Business plan; the Plus trial worked):

1. Set `YARDSALE_API_KEY` in `.env`, for example from
   `python3 -c "import secrets; print(secrets.token_urlsafe(32))"`, and start the server. It
   listens on `127.0.0.1:8110` (`YARDSALE_API_PORT`).
2. Make it public:

   ```bash
   cloudflared tunnel --no-autoupdate --protocol http2 --url http://127.0.0.1:8110
   ```

   It prints an `https://<words>.trycloudflare.com` URL, which changes every time it restarts.
3. In the Fetch Business workbench, pick the agent, open Integrations, and under OpenAPI
   Integration click Create Business Tool: From URL `<tunnel URL>/openapi.json`, API Key in a
   Header named `X-Yardsale-Key`, and the key as the Agent Credential. Fetch makes one tool per
   call and asks the buyer to confirm before `startHaggle` and `acceptDeal`.
4. Add a skill that says when to call each tool. Ours is in
   [calhacks-plan.md](docs/calhacks-plan.md#what-fetch-business-offers).

## Hosting on Agentverse

With `AGENTVERSE_API_KEY` set and `YARDSALE_HOST_ITEMS=N` in live mode, approving a listing
creates its item agent on Agentverse through the Hosting API ([hosting.py](yardsale/hosting.py)),
up to N at once; past that, or if Agentverse fails, the agent runs in the Bureau as before. A
hosted agent gets a profile page (title, asking price, how to make an offer, never the floor),
its floor and private gap as an Agentverse secret, and code built from
[hosted_item_template.py](yardsale/hosted_item_template.py) with [haggle.py](yardsale/haggle.py)
and the message classes pasted in, so it haggles by exactly the same rules. When it sells, it
tells the Market (`ItemSold`), which records the deal and handles payment.

When a hosted item is sold or the seller takes it down, the Market deletes its agent from
Agentverse, which frees the slot. If Agentverse can't be reached, the Market tries again every
minute and when the server starts, and the agent keeps counting toward `YARDSALE_HOST_ITEMS`
until it's gone. The listing and its deal stay in the database. Any live start with
`AGENTVERSE_API_KEY` set does this, even with `YARDSALE_HOST_ITEMS=0`.

```bash
uv run python -m yardsale.hosting --list      # Yardsale's hosted item agents
uv run python -m yardsale.hosting --cleanup   # delete the ones whose listing isn't active
```

`--cleanup` does the same deletes by hand, for when the server isn't running.

Agentverse's agent limits (reportedly 8 on the free plan and 25 on Premium; not checked against
our account) count the five mailbox agents too. Hosting was run live on October 24: a hosted
agent starts in about 3 seconds and answers a haggle round through Agentverse in a few seconds.
Agentverse shows only 32 characters of a name, so each hosted agent is named after its item.

Each hosted agent also gets a handle made from its title, cut at a word to Agentverse's 20
characters ("Trek FX 3 hybrid bike, medium" becomes `@trek-fx-3-hybrid`; Agentverse suggests
another if that one is taken), and the seller's reply names it. With `YARDSALE_ITEM_AVATARS=1`
(and `ASI_ONE_API_KEY` and `APIFY_TOKEN`), ASI:One also draws a picture of the item in the
background (never from the seller's photo or prices). The prompt names only the kind of thing,
such as "hybrid bike", and how it looks, because the image model writes any brand, model, or
size it's given onto the picture. The picture is saved in a public Apify key-value store,
`yardsale-agent-pictures`, and set as both the agent's header and profile picture. Both are
extras: if either fails, the item is listed anyway.

## Agent pictures

Every agent's profile picture is drawn by ASI:One's image model (`POST /v1/image/generate`,
model `asi1-mini`) in one shared style ([avatars.py](yardsale/avatars.py)). An Agentverse
profile holds only a picture's URL. Agentverse's own picture upload takes a signed-in browser
session and refuses the API key, so:

- The service agents' pictures are in [yardsale/readmes/avatars/](yardsale/readmes/avatars/).
  Each one is uploaded by hand on its agent's Agentverse profile page (hover the picture), and
  `--sync` saves the resulting URLs in `urls.json`. Each agent sends its URL whenever it
  connects, so a restart keeps it.
- `yardsale.jpg` there is `@yardsale`'s, uploaded by hand in Fetch Business's Brand Studio.
- Hosted item agents' pictures go to the Apify store above, with `YARDSALE_ITEM_AVATARS=1`.

```bash
uv run python -m yardsale.avatars --draw       # draw missing pictures (calls ASI:One)
uv run python -m yardsale.avatars --sync       # save the pictures set on Agentverse
uv run python -m yardsale.avatars --items      # handles (and pictures) for hosted item agents
uv run python -m yardsale.avatars --draw --redraw shopper   # draw one again
uv run python -m yardsale.avatars --items --redraw specialized-sirrus   # an item again
```

## Resale Comps Actor

[actors/resale-comps/](actors/resale-comps/) is an Apify Actor written for Yardsale: recent eBay
sold prices for one item, with lots, bundles, parts, and empty boxes screened out, plus the price
spread. Its input and output match the `caffein.dev/ebay-sold-listings` Actor the Researcher uses
today, so switching is one setting once it's published:
`YARDSALE_APIFY_ACTOR=<username>~resale-comps`. Its parser is tested here. On Apify, eBay
answered 403 to every request through Apify's default proxy, so it doesn't find sales yet.

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
| Market ("Yardsale Orchestrator") | ASI:One chat for sellers and buyers, proposals, item agent lifecycle, checkout | mailbox |
| Identifier | Claude names the item in a photo | mailbox |
| Researcher | Apify finds eBay sold listings | mailbox |
| Appraiser | Claude matches sales; code sets the price | mailbox |
| Shopper | the buyer agent: haggles with several item agents at once | mailbox |
| Item agents, one per listing | hold the private floor, haggle, close deals | none (Almanac only), or hosted on Agentverse |

Item agents in the Bureau don't connect to Agentverse, so there's no cap on them from
Agentverse's agent limits. Their seeds come from one master secret and the item ID, so an item always has the same
address. The master secret comes from `YARDSALE_MASTER_SEED` or is generated once into
`.agent-data/yardsale-bureau/master_seed` (gitignored, readable only by you).

The non-obvious part is adding agents to a Bureau that's already running. `Bureau.add()` connects
the new agent to the Bureau's port, and messages reach it right away. But the Bureau only starts
agents and registers them on the Almanac when it first boots. So `start_item_agent` does both
itself, and adds the agent to the Bureau's hourly re-registration batch.

**State** lives in SQLite ([store.py](yardsale/store.py)), one file per mode
(`.agent-data/yardsale-bureau/listings-{live,offline}.sqlite3`, mode 0600), so offline
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
- [pricing.py](yardsale/pricing.py), [haggle.py](yardsale/haggle.py),
  [negotiation.py](yardsale/negotiation.py), [search.py](yardsale/search.py),
  [store.py](yardsale/store.py): money rules, haggling rules for both sides, how an item agent
  answers, buyer search, and saved state.
- [shopper.py](yardsale/shopper.py): the buyer agent.
- [payments.py](yardsale/payments.py), [checkout.py](yardsale/checkout.py),
  [orders.py](yardsale/orders.py): payment links, the Agent Payment Protocol, commission, and the
  orders webhook.
- [hosting.py](yardsale/hosting.py), [hosted_item_template.py](yardsale/hosted_item_template.py):
  item agents hosted on Agentverse.
- [photos.py](yardsale/photos.py): downloads (public addresses only, checked again after every
  redirect and at connect time; 25 MB cap), rotation, HEIC, and a 50-megapixel limit.
- [craigslist_mail.py](yardsale/craigslist_mail.py): Craigslist buyers' emails, passed to the
  item agents and answered through Craigslist's relay.
- [business_api.py](yardsale/business_api.py): the HTTP API and OpenAPI description that
  `@yardsale` on Fetch Business calls.
- [config.py](yardsale/config.py): settings, the public endpoint, and the readiness check.
- [agentverse.py](yardsale/agentverse.py): Agentverse profiles and mailbox connection.
- [offline.py](yardsale/offline.py): offline mode's local stand-in for the Almanac, and the switch
  that stops uAgents from calling Fetch's ledger and Almanac when agents start.
- [tests/fakes.py](tests/fakes.py), [tests/sim.py](tests/sim.py): fake Claude, Apify, ASI:One,
  and photo downloads, and the in-process harness. Only the tests use them.
- [sell.py](yardsale/sell.py), [buyer_test.py](yardsale/buyer_test.py): command line seller and
  end-to-end buyer test.
- [models.py](yardsale/models.py): agent messages and the `YardsaleHaggle` protocol spec.
- [actors/resale-comps/](actors/resale-comps/): the Resale Comps Apify Actor.

## Going live

These need accounts, keys, or a public machine. Steps 1 and 3 to 6 were done on October 23, and
7, 9, and 10 on October 24; 2 and 8 haven't been verified. Run `uv run python -m yardsale.bureau_server --check` to see which
keys are set; it never prints values.

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
   photo, and check the server log shows it priced. On October 23 uploads came as Cloudinary links
   with `mime_type` `"image"` and downloaded without authentication. Unverified: whether the
   planner routes "sell this" to Yardsale without an @mention.
6. **Sessions.** Confirm that one ASI:One chat keeps one session ID across messages. Approval and
   haggling rely on it to recognize the same person. One chat kept one session ID on October 23.
7. **Fetch Business.** Done on October 24: `@yardsale` ([asi1.ai/ai/yardsale](https://asi1.ai/ai/yardsale))
   searches, haggles, and buys through Yardsale's HTTP API on the Plus trial; see
   [`@yardsale` on Fetch Business](#yardsale-on-fetch-business). Nothing else depends on it.
8. **Live buyer test.** `buyer_test` without `--offline` reaches the Market through its
   Agentverse mailbox, so connect it first.
9. **Payments.** Set Visa sandbox keys or a Stripe test key, close a deal from the chat, pay
   with a test card, and say "paid". Done with Stripe on October 24; Visa is unverified.
10. **Hosting.** Set `YARDSALE_HOST_ITEMS=1`, approve a listing, and check that its Agentverse
    profile appears and that an offer from the chat gets an answer. Done on October 24.

## Test results

| Run | Result |
| --- | --- |
| Live Almanac, 12 item agents, separate buyer process | 12/12 deals, 13/13 found on the Almanac (before the Market had a mailbox) |
| Offline, 40 item agents | 40/40 deals |
| Offline server with fake services (a mode since removed), `buyer_test --count 12` | 12/12 deals |
| Offline server with fake services (a mode since removed), `sell --yes`, then restart | listed, then restored at the same address |
| Live, real ASI:One app, real Claude and Apify keys | photos priced; a photo of Air Jordan 4s priced from 20 eBay sales of the same product; the Approve card showed in ASI:One |
| Live, `@yardsale` on Fetch Business through the HTTP API and a Cloudflare tunnel (October 24) | searched and haggled with a hosted item agent ($221, then $270 agreed, 22 s); an accepted $283 deal got a Stripe test link in 6 s |
