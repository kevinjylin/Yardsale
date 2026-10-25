# Fetch Business setup for `@yardsale`

Written Saturday, October 24, 2026. This page is the click-by-click setup of `@yardsale`, Yardsale's
Fetch Business agent, in the workbench at
[business.fetch.ai/workbench](https://business.fetch.ai/workbench): re-importing the Business
Tool after the API changed, the Skill text, the Business Information entry, the Brand Studio
description and starter prompts, and what can and can't link `@yardsale` to Yardsale's Agentverse
agents. [background.md](background.md#buying-through-yardsale-fetch-business) explains the API
itself and [calhacks-plan.md](calhacks-plan.md#what-fetch-business-offers) records what was set
up on October 24.

Since October 25, `@yardsale` has one job: it's the buyer's storefront. Its tools are the two
searches, `priceCheck`, and `startNegotiation` with `getNegotiation` (Yardsale's buyer agent
emails a Craigslist seller for the buyer and haggles within their max). The haggle and deal
tools (agent-to-agent buying, dropped from the pitch) are left out of the API unless the server
runs with `YARDSALE_API_HAGGLE=1`, so Fetch's routing has four fewer ways to go wrong. Sellers
who want to list are sent to `@ys-orchestrator`, since a photo has to reach Claude.

Sources are linked where a claim comes from one. Fetch's own pages come first. Anything from
press or a third party is marked **press** or **third party**. The workbench has no public
documentation, so the screen names below are the ones seen on October 24 (Brand Studio,
Business Information, Services → Skills, Integrations, Sphere); a button label that wasn't
recorded is described rather than quoted.

## What Fetch Business can link today

Checked October 24, 2026.

1. **OpenAPI tools, called "Business Tools".** This is the one documented, working link between
   `@yardsale` and Yardsale: on the Plus plan, an Integration that reads an OpenAPI description
   and makes one tool per operation. It worked live on October 24 (search, haggle, accept).
   Fetch's site calls this "APIs" for "custom inventory, booking, and policy data" and lists the
   other integrations as Shopify, Stripe, Square, SumUp, SumUp POS, and Google Calendar
   ([business.fetch.ai](https://business.fetch.ai/)). Nothing on the site or its blog mentions
   OpenAPI by name; the blog's newest post is from April 27, 2026
   ([business.fetch.ai/blog](https://business.fetch.ai/blog)).
2. **Connecting an existing Agentverse agent: not documented.** The public pages don't describe
   attaching an existing uAgent or handle to a business agent. The site's sitemap has eleven
   URLs (the home page, Enterprise, the blog, and two onboarding pages) and no docs, help, or
   pricing page ([sitemap](https://business.fetch.ai/sitemap.xml)); the claim flow is behind a
   Flockx sign-in (`auth.flockx.io`), which is the platform the workbench runs on. **Press** said
   at launch that a brand claims a handle "regardless of which tools they use to build the
   underlying agent" and that the agent then "becomes discoverable to consumer AIs and other
   agents inside Agentverse"
   ([VentureBeat, November 19, 2025](https://venturebeat.com/ai/the-google-search-of-ai-agents-fetch-launches-asi-one-and-business-tier-for)).
   Whether the workbench has a "connect an existing agent" control has to be checked by signing
   in; on October 24 only the OpenAPI Integration was found. Record what Sphere and Integrations
   show (step 6 below).
3. **Handing a chat off to another agent: not documented either.** Fetch Business's own commerce
   post describes payment links, a Square catalog, and transactions "with customer AIs", not
   handoffs ([From Stripe Link to Verified Transaction, March 7,
   2026](https://business.fetch.ai/blog/commerce-capabilities)). ASI:One's planner finds agents
   by searching Agentverse "for agents matching what the task actually needs, described in
   natural language", and an API caller can name agent addresses in an `agents` list, but the
   docs give no way to hand a conversation to a named agent
   ([ASI:One docs, Planner Mode](https://docs.asi1.ai/documentation/build-with-asi-one/planner)).
   So `@yardsale` can't send a user to `@ys-orchestrator`; it can only tell them to message it.
4. **Routing by handle works for users.** Agentverse's profile docs say to copy an agent's
   handle into an ASI:One chat "and ask to talk to that Agent directly", and a custom `@handle`
   is up to 20 characters ([Agentverse docs,
   Profile](https://docs.agentverse.ai/v-2/documentation/agent-discovery/profile.md)). That's
   why every seller instruction below names `@ys-orchestrator`.
5. **What ASI:One routes on.** The README is "the main document ASI:One uses to understand what
   your Agent does"; "a clear structure and real examples improve matching and ranking", and
   keywords should be phrases users would type ([Setup
   Guide](https://docs.agentverse.ai/documentation/agent-discovery/setup-guide), [README
   Guidelines](https://docs.agentverse.ai/v-2/documentation/agent-discovery/readme-guidelines.md)).
   [yardsale/readmes/market.md](../yardsale/readmes/market.md) was rewritten for the two products
   on October 24 and uploads on the next live start.
6. **Verification.** Agentverse "currently supports Website Verification" (a DNS TXT record for a
   domain); email-domain verification and a "Fetch Verified" badge are listed as coming later
   ([Verifications](https://docs.agentverse.ai/v-2/documentation/agent-discovery/verifications.md)).
   Yardsale has no domain, so none of our agents can be verified this weekend.
7. **Plan limits, corrected.** Agentverse's Basic plan is free with 8 hosted and 8 local agents;
   Premium is $25 a month with 25 of each
   ([Subscriptions and Quotas](https://docs.agentverse.ai/v-2/documentation/advanced-usages/agentverse-subscriptions-and-quotas.md)).
   This confirms the figures [background.md](background.md#decisions) called unchecked. Fetch
   Business's own plans (the Plus trial, $75 a month after November 7) aren't published anywhere
   public that was found.
8. **New since October 23.** Nothing on Fetch Business. Agentverse's September 2026 changelog
   covers agent testing with custom questions, a Windows installer for its CLI, opening hosted
   agents in Cursor or VS Code, and geolocation on profiles
   ([docs.agentverse.ai](https://docs.agentverse.ai/llms.txt)); none of it changes the setup.

In short: the OpenAPI Business Tool is the link, and it's the one a Fetch judge said they're
looking for. Everything else below makes `@yardsale` use it well and point people at the right
Yardsale agent.

## 0. Start the server and the tunnel

On the laptop that runs Yardsale, with `.env` holding `YARDSALE_API_KEY` (24 characters or
more) and the usual keys:

```bash
env -u ANTHROPIC_BASE_URL uv run python -m yardsale.bureau_server
cloudflared tunnel --no-autoupdate --protocol http2 --url http://127.0.0.1:8110
```

`cloudflared` prints a line like `https://<words>.trycloudflare.com`; that's `<tunnel>` below. A
quick tunnel gets a new URL every time it starts, and the Business Tool has to be pointed at the
new one (step 1), so start the tunnel before the demo and leave it running until after judging.
Setting `YARDSALE_API_URL=<tunnel>` in `.env` makes the description name the public URL; without
it, the description uses the host the request came through, which is the same thing behind
Cloudflare.

Check it from any machine before touching the workbench:

```bash
curl -s <tunnel>/openapi.json | python3 -c 'import json,sys; s=json.load(sys.stdin); print(s["servers"], [o["operationId"] for p in s["paths"].values() for o in p.values()])'
curl -s -H "X-Yardsale-Key: $YARDSALE_API_KEY" "<tunnel>/price-check?q=Trek+FX+2+Disc+hybrid+bike" | python3 -m json.tool | head -20
```

The first prints the tunnel URL and the operations: `searchListings`, `searchOtherSites`,
`priceCheck`, `startNegotiation`, `getNegotiation`, and `getOpenApi`, plus `startHaggle`,
`getHaggle`, `acceptDeal`, and `getDeal` only when the server runs with `YARDSALE_API_HAGGLE=1`.
The second runs a real Apify search (about $0.08), so run it
once, not in a loop; it answers within 25 seconds or with `"status": "running"`, in which case
run it again and it picks up the same check.

## 1. Re-import the Business Tool

The Integration reads the OpenAPI description once, when it's created or refreshed, so after the
API changes (new `priceCheck` and `startNegotiation`, new descriptions) or the tunnel restarts, it has to be re-imported.

1. Sign in at [business.fetch.ai/workbench](https://business.fetch.ai/workbench) and make sure the
   YardSale agent is selected (its name shows at the top; the Orders page's URL carries
   `agentId=`).
2. Open **Integrations** in the sidebar.
3. Find the OpenAPI Integration named **Yardsale Market** (created October 24). Its page shows
   the base URL, "6 tools", the key header, an **Update Credential** button, and **Delete**; the
   Tools page (View Tools) lists the generated tools and has no refresh. The URL and the
   description can't be changed in place, so delete it and add a new OpenAPI Integration with
   the same name, `<tunnel>/openapi.json`, and the `X-Yardsale-Key` header with the value of
   `YARDSALE_API_KEY`. Checked October 25: the integration still pointed at
   `https://ultram-downloading-visible-atm.trycloudflare.com` (live, serving the new
   description) with the October 24 tools (`searchListings`, `startHaggle`, `getHaggle`,
   `acceptDeal`, `getDeal`, and the description), so `searchOtherSites` and `priceCheck` had
   never reached `@yardsale`, and the four haggle tools now answer 404. Re-created later on
   October 25 (the key pasted by hand; the rest of the form was filled from this page): Fetch
   made 6 tools, `searchListings`, `searchOtherSites`, `priceCheck`, `startNegotiation`,
   `getNegotiation`, and `getOpenApi`, and set confirmation on `startNegotiation` by itself.
   The Tools page shows each tool's confirmation but doesn't let it be changed. Checked from
   ASI:One right after: asking `@yardsale` about a made-up negotiation ref made a
   `getNegotiation` call that reached the server with the key (a 404 "not found", no 401), so
   the credential is right. A search or price check there is a paid Apify run; not tried yet.
4. After saving, the Integration lists the tools Fetch made from the description. Check that it
   shows **searchListings**, **searchOtherSites**, **priceCheck**, **startNegotiation**, and
   **getNegotiation** (on October 24 it made 6 tools from 5 operations plus the description
   itself, so expect 6 now: these five and the description; that's what it made on October 25). **startHaggle**, **getHaggle**,
   **acceptDeal**, and **getDeal** appear only when the server runs with
   `YARDSALE_API_HAGGLE=1`; they're off since October 25, because agent-to-agent buying isn't
   pitched and fewer tools route better. If `startNegotiation` is missing, the old description
   was cached: delete and re-add.
5. The workbench doesn't ask which tools need the buyer's confirmation: it marks the POST
   calls itself (`startHaggle` and `acceptDeal` on October 24, `startNegotiation` on October
   25) and leaves the searches, `priceCheck`, and `getNegotiation` unconfirmed, so a price
   check or a search doesn't stop to ask. The Tools page shows it and can't change it.
6. Test in `@yardsale`'s chat (step 5 of this page) before changing anything else.

## 2. Update the Skill

Open **Services → Skills** in the sidebar and click the pencil on the active skill (made on
October 24 as "Find, haggle for, and buy used items on Yardsale"; its internal name,
`find-haggle-and-buy-used-items-on-yardsale`, doesn't change when it's renamed). The editor has
three fields: the name, when it applies, and the steps. Done on October 25 with the text below;
after saving, the list shows the new name and a "Skill updated" toast.

**Name:** `Find deals, check prices, and negotiate on Yardsale`

**When it applies:**

```text
When a customer wants to find or buy a used item, get the best deal, know what something is
worth, sell something, have Yardsale haggle with a Craigslist seller, or asks how Yardsale's
haggling works.
```

**Steps:**

```text
Step 1: Never search the web or make up a listing, a price, a sale, or an email address. Every
number comes from a Yardsale Market tool result; quote it as it came back. Prices are US
dollars.
Step 2: Buyers: get what they want and the most they'll pay (ask for a max before searching
other sites). Call searchListings with the words and max_usd, then always searchOtherSites with
the same words and max_usd, whatever the count. Show each deal with its site, price, total with
shipping, location, link, and its reason. Say listings on other sites are bought from their
sellers and Yardsale takes no payment for them. If status is running, call it again with the
same words and max; it waits for the result.
Step 3: When the buyer picks a Craigslist post and wants Yardsale to negotiate, they need the
post's reply address: tell them to open the post, tap Reply, and paste the email address it
shows (it ends in @sale.craigslist.org). Never guess one or take one from a search result. With
the address, call startNegotiation with relay_address, max_usd, the post's title, its link if
they gave it, and asking_usd if the post shows a price. Yardsale's buyer agent emails the
seller within about 10 seconds and haggles by email, at most 4 offers, never above the max and
never telling the seller the max. Show the summary and always write the line "Negotiation ref:
<ref>" in your reply: your next message sees only the chat, not tool results, and
getNegotiation needs the ref. Say the seller answers by email in their own time.
Step 4: When the buyer asks how it's going, call getNegotiation with the ref from your earlier
reply, copied exactly (8 lowercase characters). Tell them where it stands. Once agreed, say the
price and what the seller wrote about pickup, and that they pay the seller in person: Yardsale
takes no payment and has no payment link for Craigslist deals.
Step 5: Sellers: call priceCheck with the item as a listing title would name it (brand, model,
size) and their ZIP code as zip if they gave one. Tell them the typical price, the usual range,
and the suggested asking price; say these come from real eBay sales of the last 90 days matched
by title, so a sale could be a different model or a bundle; link the example sales. If the
result has a local block, say what similar items are asking for nearby. If count is 0, ask for
the brand and model and try again. If status is running, call it again with the same words.
Step 6: To list an item, or to get each sale checked against a photo, send the seller to
Yardsale's Market agent: open a new chat in ASI:One, type @ys-orchestrator, attach a photo of
the item, and add anything that matters, such as "at least $40" or "size M". Yardsale works out
what it is from the photo, checks each eBay sale against it, proposes an asking price and a
private lowest price, and lists it only when they reply approve; then the item's own agent
haggles with buyers and never goes below their lowest price. You can't take photos, and
ASI:One's models don't read photos; Claude does, inside Yardsale.
Step 7: If a tool errors, say what the error said and what to try (other words, a higher max,
again in a moment). Don't invent a result to fill the gap. Don't claim a sale is real money:
payments are in test mode during the hackathon.
Step 8: Only if your tools include startHaggle (the server runs with YARDSALE_API_HAGGLE=1):
call it solely when the buyer explicitly asks you to haggle with a Yardsale seller ("haggle
with the seller of the Trek for me, max $320"); "the best deal", "the best price", a bargain,
or something cheap means Steps 2 and 3, never a haggle. Show every round and the best price,
always write "Haggle ID: <haggle_id>" in your reply, follow status running with getHaggle using
the exact ID (never startHaggle again), call acceptDeal only after the buyer says yes, give
them the payment link and say it's a test-mode checkout, and call getDeal only when they ask
whether it went through.
```

## 3. Update Business Information

Open **Business Information** in the sidebar and step to its **Plain Text** tab (the arrows at
the top walk through Documents, Website Scans, Research Reports, Plain Text, Fact Check). An
entry can be previewed but not edited or deleted, so add a new one with **+ Plain Text** (a
title and the text) and let it supersede the old one; the text below says so in its first
line. Done on October 25: the entry "How Yardsale works (October 25): deals, Craigslist
negotiation, price checks, and selling" sits beside the October 24 one. It's what `@yardsale`
knows about Yardsale when no tool is involved.

```text
Current as of October 25, 2026. This replaces the October 24 entry "How Yardsale works: buying
and selling used items"; where the two differ, this one is right.

Yardsale is a marketplace of agents for used, one-of-a-kind things. It does two jobs.

For buyers: tell Yardsale what you want and the most you'll pay. It searches its own listings
first, then eBay, Craigslist, OfferUp, and Facebook Marketplace live, and shows the best deals
within your budget, ranked and scored against what the item really sells for, with a link to
each. Deals on other sites are bought from their sellers. For a Craigslist post you pick, tap
Reply on it, paste the reply address it shows, and Yardsale's buyer agent emails the seller and
negotiates within your max, never telling the seller your max; you pay the seller in person at
pickup. Yardsale takes no payment for Craigslist deals. (Only when Yardsale's haggle tools are
turned on can its buyer agent also haggle with a Yardsale listing's own agent, with payment by
link in test mode; by default those tools are off.)

For sellers: tell Yardsale what you have and it says what it's worth from what the same item
actually sold for on eBay in the last 90 days: the typical price, the usual range, and the sales
behind them. To sell, attach a photo in a message to Yardsale's Market agent, @ys-orchestrator
(agent1qtm5uclxjx5ye4fhtjcqcc2d4vh5aucary75dn90lwq7808uyzvt2std9nj), in ASI:One. Claude reads
the photo and works out what the item is, checks each eBay sale against it, and proposes an
asking price and a private lowest price. Nothing is listed until the seller replies approve.
Then the item gets its own agent, with its own address on Agentverse, that haggles with every
buyer and never goes below the seller's lowest price. Yardsale keeps a 5% commission on each
sale it collects payment for, and the seller gets the rest.

Yardsale's agents on Agentverse: the Market (@ys-orchestrator, Yardsale Orchestrator), which
talks to buyers and sellers; the Identifier (photos, read by Claude), the Researcher (eBay
sold listings, through Apify), the Market Scout (@ys-scout: what similar items are asking right
now nearby), and the Appraiser (which sales match, and the price, in plain code); the Deal
Finder (@ys-dealfinder), which searches the other sites and scores each deal; the Shopper
(@ys-shopper), the buyer agent that haggles and emails Craigslist sellers; and one agent per
listed item. ASI:One's asi1-mini reads what people type; it doesn't read photos.

Photos can't be sent to @yardsale. Sellers with a photo message @ys-orchestrator.
```

## 4. Update Brand Studio: description and starter prompts

Open **Brand Studio**. Replace the description and the five starter prompts.

**Description** (shows on [asi1.ai/ai/yardsale](https://asi1.ai/ai/yardsale); the field takes
300 characters at most):

```text
The best deals on used stuff, found and negotiated for you. Say what you want and your max: Yardsale's agents search eBay, Craigslist, OfferUp, and Facebook Marketplace, score each deal against real sold prices, and email a Craigslist seller to haggle for you. Selling? Ask what yours is worth.
```

**Starter prompts.** Each has a title (shown on the agent's page, like the Market's menu tiles),
a prompt of up to 120 characters, and a picture, and each is saved with its own checkmark, apart
from the profile's Save Changes. Keep the pictures; set these titles and prompts:

| Title | Prompt |
| --- | --- |
| Find a deal | `Find me a cheap mini fridge, max $80` |
| Best deal | `What's the best deal on a used road bike under $300?` |
| Price check | `What's my Breville Bambino Plus worth?` |
| Haggle on Craigslist | `Found a Craigslist post I like. Can you haggle with the seller for me?` |
| How haggling works | `How do you haggle with a Craigslist seller without giving away my max?` |

The first two exercise `searchListings` then `searchOtherSites`, the third `priceCheck`, and
the fourth makes it explain how to get a post's reply address (no tool call until the buyer
pastes one; then `startNegotiation`). The fifth is answered from the Skill and Business
Information, with no tool. No starter prompt sends anyone to `@ys-orchestrator`: a storefront
whose first suggestion is "go elsewhere" is the least intuitive thing it can do, so sellers hear
about `@ys-orchestrator` only when they ask to list. None haggles with a Yardsale seller either:
those tools are off unless `YARDSALE_API_HAGGLE=1`.

## 5. Check it in `@yardsale`'s chat

Open [asi1.ai/ai/yardsale](https://asi1.ai/ai/yardsale) (or type `@yardsale` in a new ASI:One
chat) and try each starter prompt. What to look for:

- Prompt 1 or 2: a `searchListings` call, then `searchOtherSites`; results show site, price,
  and link, and no made-up listing. If it answers from web search instead, the Skill isn't
  enabled or the Integration's tools didn't import (step 1.4).
- Prompt 3: a `priceCheck` call; the answer names the typical price and range, says they're
  from eBay sales matched by title, and links the example sales. It takes up to 25 seconds; a
  second call means the first was still running, which is normal.
- Prompt 4 (Haggle on Craigslist): no tool call; it says to open the post, tap Reply, and paste
  the address it shows, and asks for the max. Paste a real post's reply address with a max to
  see `startNegotiation` (it emails that seller for real, from Yardsale's inbox, so use a post
  you mean to buy); then "how's my negotiation going?" should call `getNegotiation` with the
  ref from its earlier reply, and the answer must never state your max.
- Prompt 5 (How haggling works): no tool call; it explains the email haggle (at most 4 offers,
  the max never sent) from the Skill and Business Information and says nothing about
  `startHaggle` (with the haggle tools off it has none). If it offers to haggle with a Yardsale
  seller instead, the Skill text is the old one (step 2).
- "I want to sell my desk": no tool call; it says to message `@ys-orchestrator` with a photo
  and mentions Claude reads the photo.

If anything says Yardsale's "status service" had an error, the tunnel URL in the Integration is
stale (step 1) or the server is down (step 0).

## 6. Linking Yardsale's Agentverse agents

What there is to do, given the findings above:

1. **Name the agents everywhere.** The Business Information entry and the Skill name
   `@ys-orchestrator` (and `@ys-shopper`), which ASI:One users can mention directly. That's the
   only documented way for a `@yardsale` user to reach them.
2. **Look at Sphere and Integrations and write down what's there.** Sphere was in the sidebar on
   October 24 and isn't described on any public page. If it, or Integrations, offers to connect
   an existing Agentverse agent by address, connect the Market (`agent1q...` from its profile)
   and record the steps here. If it doesn't, note that too, so nobody looks twice.
3. **Keep the profiles good.** ASI:One routes on each agent's README and description, so after
   the next live start check that the Market's profile shows the new
   [market.md](../yardsale/readmes/market.md) text and both badges, and that its handle is still
   `@ys-orchestrator`.
4. **Ask the Fetch team** (still open from [calhacks-plan.md](calhacks-plan.md#questions-to-ask-tonight)):
   how a business agent hands off to, or connects, an existing Agentverse agent; whether a
   Business Tool can point at a URL that changes; and how an outside API adds orders to the
   Orders page.

## Sources

- [Fetch Business](https://business.fetch.ai/), its [blog](https://business.fetch.ai/blog), its
  [commerce post](https://business.fetch.ai/blog/commerce-capabilities) (March 7, 2026), and its
  [sitemap](https://business.fetch.ai/sitemap.xml). Checked October 24, 2026.
- [ASI:One docs: Planner Mode](https://docs.asi1.ai/documentation/build-with-asi-one/planner)
- Agentverse docs: [Setup Guide](https://docs.agentverse.ai/documentation/agent-discovery/setup-guide),
  [Profile](https://docs.agentverse.ai/v-2/documentation/agent-discovery/profile.md),
  [README Guidelines](https://docs.agentverse.ai/v-2/documentation/agent-discovery/readme-guidelines.md),
  [Verifications](https://docs.agentverse.ai/v-2/documentation/agent-discovery/verifications.md),
  [Subscriptions and Quotas](https://docs.agentverse.ai/v-2/documentation/advanced-usages/agentverse-subscriptions-and-quotas.md),
  and the [index with the September 2026 changelog](https://docs.agentverse.ai/llms.txt)
- **Press:** [VentureBeat on the Fetch Business launch, November 19,
  2025](https://venturebeat.com/ai/the-google-search-of-ai-agents-fetch-launches-asi-one-and-business-tier-for)
