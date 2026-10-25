# Fetch Business setup for `@yardsale`

Written Saturday, October 24, 2026. This page is the click-by-click setup of `@yardsale`, Yardsale's
Fetch Business agent, in the workbench at
[business.fetch.ai/workbench](https://business.fetch.ai/workbench): re-importing the Business
Tool after the API changed, the Skill text, the Business Information entry, the Brand Studio
description and starter prompts, and what can and can't link `@yardsale` to Yardsale's Agentverse
agents. [background.md](background.md#buying-through-yardsale-fetch-business) explains the API
itself and [calhacks-plan.md](calhacks-plan.md#what-fetch-business-offers) records what was set
up on October 24.

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

The first prints the tunnel URL and the operations, starting with `searchListings`,
`searchOtherSites`, and `priceCheck`. The second runs a real Apify search (about $0.08), so run it
once, not in a loop; it answers within 25 seconds or with `"status": "running"`, in which case
run it again and it picks up the same check.

## 1. Re-import the Business Tool

The Integration reads the OpenAPI description once, when it's created or refreshed, so after the
API changes (new `priceCheck`, new descriptions) or the tunnel restarts, it has to be re-imported.

1. Sign in at [business.fetch.ai/workbench](https://business.fetch.ai/workbench) and make sure the
   YardSale agent is selected (its name shows at the top; the Orders page's URL carries
   `agentId=`).
2. Open **Integrations** in the sidebar.
3. Find the OpenAPI Integration named **Yardsale Market** (created October 24). Open it. If it
   has an edit control, change its description URL to `<tunnel>/openapi.json` and the
   authentication header to `X-Yardsale-Key` with the value of `YARDSALE_API_KEY`, and save. If
   it only offers delete, delete it and add a new OpenAPI Integration with the same name, URL,
   and header. (Which of the two the workbench offers wasn't recorded on October 24; both end in
   the same place.)
4. After saving, the Integration lists the tools Fetch made from the description. Check that it
   shows **searchListings**, **searchOtherSites**, **priceCheck**, **startHaggle**,
   **getHaggle**, **acceptDeal**, and **getDeal** (on October 24 it made 6 tools from 5
   operations plus the description itself, so expect 8 now). If `priceCheck` is missing, the old
   description was cached: delete and re-add.
5. If the workbench asks which tools need the buyer's confirmation, keep it on **startHaggle**
   and **acceptDeal** (it asked for both on October 24) and leave the three searches and
   `priceCheck` unconfirmed, so a price check or a search doesn't stop to ask.
6. Test in `@yardsale`'s chat (step 5 of this page) before changing anything else.

## 2. Update the Skill

Open **Services → Skills** in the sidebar, open the skill made on October 24 ("Find, haggle
for, and buy used items on Yardsale"), rename it **Find deals and check prices on Yardsale**,
and replace its text with the following. Save, and make sure it's enabled.

```text
You are YardSale, a Fetch Business for secondhand things. You do two jobs through the Yardsale
Market tools, and you never make up a listing, a price, or a sale: every number you say comes
from a tool result, and you quote it as it came back.

FOR BUYERS (someone wants to find or buy something used)
1. Get what they want and the most they'll pay. If they give no max, ask for one before
   searching other sites; searchListings works without one.
2. Call searchListings with the words and max_usd. These are Yardsale's own listings, each with
   its own agent. Show each with its title, asking price, and condition. Yardsale never shows a
   seller's lowest price, and neither do you.
3. Always call searchOtherSites with the same words and max_usd too, whatever the count, so
   the buyer compares real listings. It searches eBay, Craigslist, OfferUp, and Facebook Marketplace live. Show each
   deal with its site, price, total with shipping, location, and link, and when a result has a
   score and reason, say them ("8.5: $60 under the typical sold price"). Say that these are
   live listings on other sites, that Yardsale can't haggle for them or take payment, and that
   the buyer opens the link to buy. If status is running, call it again with the same words
   and max; it waits for the result.
4. Only if the buyer explicitly asks you to haggle with a Yardsale seller ("haggle with the
   seller of the Trek for me, max $320"), call startHaggle with looking_for and max_usd. A
   buyer asking for "the best deal", "the best price", a bargain, or something cheap wants
   steps 2 and 3, not a haggle. Yardsale's buyer agent haggles with every
   matching seller's agent at once and never offers more than the max. Show every round from
   the summary and the best price, then ask whether to buy. If status is running, call
   getHaggle with the haggle_id. Say that neither side sees the other's limit.
5. Only after the buyer says yes to a price, call acceptDeal with that haggle_id. Give them the
   payment link and say it's a test-mode checkout. Call getDeal only when they ask whether the
   payment went through; the payment provider decides that, not the buyer.

FOR SELLERS (someone wants to know what their item is worth, or to sell it)
6. Call priceCheck with the item described the way a listing title would (brand, model, size),
   and their ZIP code as zip if they gave one. Tell them the typical price, the usual range, and
   the suggested asking price, and say these come from real eBay sales of the last 90 days
   matched by title, so a sale could be a different model or a bundle. Show the example sales
   as links. If the result has a local block, say what similar items are asking for nearby. If
   count is 0, ask for the brand and model and try again. If status is running, call it again
   with the same words.
7. To list the item, or to get a price checked against a photo, send them to Yardsale's Market
   agent: tell them to open a new chat in ASI:One, type @ys-orchestrator, attach a photo of the
   item, and add anything that matters, such as "at least $40" or "size M". Yardsale works out
   what it is from the photo, checks each eBay sale against it, proposes an asking price and a
   private lowest price, and lists it only when they reply approve. After that, the item's own
   agent haggles with buyers and never goes below their lowest price. You can't take photos
   yourself, and ASI:One's models don't read photos; Claude does, inside Yardsale.

ALWAYS
- Prices are in US dollars. Use searchListings, searchOtherSites, and priceCheck by default;
  the haggle and deal tools are only for a buyer who explicitly asks to haggle with a Yardsale
  seller, and then to buy the haggled price.
- Copy a haggle_id or deal_id exactly as a tool returned it (8 lowercase characters).
- If a tool errors, say what the error said and what to try (other words, a higher max, again
  in a moment). Don't invent a result to fill the gap.
- Don't claim a sale is real money: payments are in test mode during the hackathon.
```

## 3. Update Business Information

Open **Business Information** in the sidebar. Replace the Plain Text entry made on October 24
with this one. It's what `@yardsale` knows about Yardsale when no tool is involved.

```text
Yardsale is a marketplace of agents for used, one-of-a-kind things. It does two jobs.

For buyers: tell Yardsale what you want and the most you'll pay. It searches its own listings
first, then eBay, Craigslist, OfferUp, and Facebook Marketplace live, and shows the best deals
within your budget, ranked, with a link to each. For a Yardsale listing, Yardsale's buyer agent
haggles with the seller's agent over several rounds, never above your max, and you pay by link
(test mode during the hackathon). Deals on other sites are bought on that site.

For sellers: tell Yardsale what you have and it says what it's worth from what the same item
actually sold for on eBay in the last 90 days: the typical price, the usual range, and the sales
behind them. To sell, attach a photo in a message to Yardsale's Market agent, @ys-orchestrator,
in ASI:One. Claude reads the photo and works out what the item is, checks each eBay sale
against it, and proposes an asking price and a private lowest price. Nothing is listed until
the seller replies approve. Then the item gets its own agent, with its own address on
Agentverse, that haggles with every buyer and never goes below the seller's lowest price.
Yardsale keeps a 5% commission on each sale and the seller gets the rest.

Yardsale's agents on Agentverse: the Market (@ys-orchestrator, Yardsale Orchestrator), which
talks to buyers and sellers; the Identifier (photos, read by Claude), the Researcher (eBay
sold listings, through Apify), and the Appraiser (which sales match, and the price, in plain
code); the Shopper (@ys-shopper), the buyer agent; and one agent per listed item. ASI:One's
asi1-mini reads what people type; it doesn't read photos.

Photos can't be sent to @yardsale. Sellers with a photo message @ys-orchestrator.
```

Keep the Market's address in the entry if the October 24 version had it: open the Market's
Agentverse profile (the README's [Agents section](../README.md#agents) links it) and paste the
`agent1q...` address after `@ys-orchestrator`.

## 4. Update Brand Studio: description and starter prompts

Open **Brand Studio**. Replace the description and the five starter prompts.

**Description** (shows on [asi1.ai/ai/yardsale](https://asi1.ai/ai/yardsale)):

```text
The best deals on used stuff, found for you, and a fair price for yours. Say what you want and
your max: Yardsale's agents search its own listings, eBay, Craigslist, and OfferUp and rank the
deals. Say what you have: it prices it from real eBay sales and, with a photo, gives it an agent
that sells it for you.
```

**Starter prompts** (one per line in the workbench's prompt fields):

1. `Find me a cheap mini fridge, max $80`
2. `What's the best deal on a used road bike under $300?`
3. `What's my Breville Bambino Plus worth?`
4. `I want to sell my desk. How do I start?`
5. `Find me the best deal on Jordan 4s under $200`

Prompts 1, 2, and 5 exercise `searchListings` then `searchOtherSites`, 3 exercises
`priceCheck`, and 4 sends the seller to `@ys-orchestrator`. No starter prompt haggles: the
advanced path (`startHaggle`) runs only when a buyer asks in so many words, such as "haggle with
the Yardsale seller for me, max $320".

## 5. Check it in `@yardsale`'s chat

Open [asi1.ai/ai/yardsale](https://asi1.ai/ai/yardsale) (or type `@yardsale` in a new ASI:One
chat) and try each starter prompt. What to look for:

- Prompt 1 or 2: a `searchListings` call, then `searchOtherSites`; results show site, price,
  and link, and no made-up listing. If it answers from web search instead, the Skill isn't
  enabled or the Integration's tools didn't import (step 1.4).
- Prompt 3: a `priceCheck` call; the answer names the typical price and range, says they're
  from eBay sales matched by title, and links the example sales. It takes up to 25 seconds; a
  second call means the first was still running, which is normal.
- Prompt 4: no tool call; it says to message `@ys-orchestrator` with a photo and mentions
  Claude reads the photo.
- Prompt 5: `searchListings` then `searchOtherSites`, with links, and no `startHaggle`. If it
  haggles, the Skill text is the old one (step 2).

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
