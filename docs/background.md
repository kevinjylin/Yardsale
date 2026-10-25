# Background and decisions

This page records Yardsale's product decisions and prototype behavior during Cal Hacks 13.0,
including seller approval, haggling, payment, hosting, and saved state.

The Cal Hacks 13.0 plan (the pitch, what Fetch Business offers, what's missing, and the build
order) is in [calhacks-plan.md](calhacks-plan.md).

**Two products (decided October 24).** Sellers photograph their things and set minimums; the
agents price each item from eBay sold sales and live asking prices (the Market Scout), tell the
seller plainly when a minimum is far off, and give it an agent that lists it (on ASI:One, on
`@yardsale`, and in a Craigslist post the seller pastes by hand) and haggles with every buyer,
citing the market and never going below the minimum. Buyers say what they want and their max,
and the Deal Finder agent searches eBay, Craigslist, OfferUp, and Facebook Marketplace through
Apify, scores each listing against what it actually sells for with a reason, and can email a
Craigslist seller to negotiate for them. The Scout, the Deal Finder, the seller feedback, and
the cited counters are tested offline (the Deal Finder has had one live probe); the Scout and
Deal Finder agents are on Agentverse as mailbox agents since October 24. Agent-to-agent buying on
ASI:One (the Shopper haggling with item agents and buying) is built and ran live, but after a
Fetch judge's feedback it's no longer pitched; selling on the seller's own eBay is built but
secondary. The sections below describe the code as it is, including the parts not pitched.
What's built and what's been run live is in [calhacks-plan.md](calhacks-plan.md#the-pitch).

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
5. **Photo work is split across four agents on Agentverse.** The Identifier, Researcher,
   Market Scout, and Appraiser each do one job, and each has its own Agentverse profile, so
   there's more than one Yardsale agent on Agentverse. With the Market and the buyer agent they
   take 6 mailbox slots in total, plus any hosted item agents.
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
| `@yardsale`                 | Fetch Business (a personal agent, Plus trial). Calls Yardsale's HTTP API to search, check prices, and have the buyer agent email Craigslist sellers (haggling and buying only with `YARDSALE_API_HAGGLE=1`); sends sellers to the Market. | Unknown (mentor Q) |
| Market agent ("Yardsale Orchestrator", `@ys-orchestrator`) | Our Bureau server, Agentverse mailbox so ASI:One can reach it. Speaks the Chat Protocol. | 1 |
| Identifier                  | Our Bureau server, Agentverse mailbox       | 1                      |
| Researcher                  | Our Bureau server, Agentverse mailbox       | 1                      |
| Appraiser                   | Our Bureau server, Agentverse mailbox       | 1                      |
| Market Scout (`@ys-scout`)  | Our Bureau server, Agentverse mailbox       | 1                      |
| Shopper (the buyer agent, `@ys-shopper`) | Our Bureau server, Agentverse mailbox | 1             |
| Deal Finder (`@ys-dealfinder`) | Our Bureau server, Agentverse mailbox. Searches other sites and scores the deals. | 1 |
| Item agents (one per item)  | Our Bureau server, Almanac only; or hosted on Agentverse (`YARDSALE_HOST_ITEMS`) | 0 in the server; 1 each hosted |
| Buyers                      | ASI:One acts for them                       | 0                      |

Buyer flow: ASI:One → Market agent (found by ASI:One) → Shopper → item agents, all at once. Or
ASI:One → `@yardsale` → Yardsale's HTTP API → Shopper → item agents. The Market relays the result back, then passes the buyer's acceptance to the
chosen item agent and sends a payment link.

Seller flow: ASI:One chat (photo + text) → Market → Identifier → (Researcher and Market Scout
at once) → Appraiser → Market → proposal in the chat → seller approves → new item agent → address in the chat. The next
sections cover it.

## Every reply is a card

Code: [yardsale/cards.py](../yardsale/cards.py) (layout),
[yardsale/card_schema.py](../yardsale/card_schema.py) (what ASI:One's web app accepts), and
[yardsale/chat.py](../yardsale/chat.py) (what each card says). Since October 25, every Market
reply in the ASI:One chat is one line of text plus an Interactive Card, the way ResourcePilot
does it. The layout was redesigned later that day, after the real app showed the home menu as
one long column (see [How ASI:One lays out a card](#how-asione-lays-out-a-card)):

- **A home menu.** "menu", "help", a first hello, or the Main menu button shows the two
  products as picture tiles side by side (Sell something, Find a deal), then the rest as
  buttons two to a row: Approve when proposals are waiting, My listings, My orders, Watches,
  Haggle on Craigslist, Your eBay when eBay selling is on, and Where to meet once the
  conversation has given its school. Each label
  carries its status ("My listings · 2 for sale", "My orders · 1 to pay"). The menu can be
  tapped again after a tap.
- **No dead ends.** Every card except the menu has a Main menu button, and most have the next
  step as buttons: Approve & list, Change prices, Write my Craigslist post, View, Make an offer,
  Haggle for me, Buy at $X, I've paid, Get the link, Watch for more, Stop. Buttons come two to a
  row, the main one first and Main menu last.
- **Forms instead of typing.** Finding a deal (what and your max), changing prices,
  making an offer, and setting a haggle max are form cards. Two price fields sit side by side.
- **Facts in boxes.** Badges sit in one row. Facts (an item's agent, its price, an order
  number) are a boxed list of labels and values, and steps are a boxed numbered list. The
  haggle result shows each seller in its own box, with the rounds on one line and no picture
  per seller. An item's own page (its photo, its facts, its buttons) is built the same way
  rather than as ASI:One's detail card, which puts every button on one line: with five, they
  ran past the edge of a 380 px phone screen. A proposal's note on the seller's price has a
  "Check your price" badge when the price is far off, and "Price looks right" when it isn't.
- **Pictures.** Each screen has a drawing from [assets/cards](../assets/cards) (made by
  [scripts/make_card_art.py](../scripts/make_card_art.py); prompts in
  [assets/PROMPTS.md](../assets/PROMPTS.md)), served from the repository's public raw GitHub
  URL and shown as a short 3:1 banner above the text. A listing shows the closest same-product
  eBay sale's photo at 16:9, labeled as such on buyer cards. `YARDSALE_CARD_ART` sets the base
  URL; set it empty to send cards without pictures. New drawings only show once they're pushed
  to `main`.
- **Links open from buttons.** "Pay $X" opens the payment link, a deal's card has "Open on
  eBay" (or its own site), a hosted item's live card has "See its agent" (its Agentverse
  profile), and the Craigslist post card has "Open Craigslist". A link button sends nothing to
  the agent. Each meeting spot on campus opens its map.
- **Carousels stay tappable.** Search results, deals, your listings, orders, and the eBay sales
  can be tapped again after a tap, so opening one deal doesn't hide the others. Main menu is a
  button under the carousel, not its last item. With three or more items, a carousel shows one
  at a time, with arrows and "1 / 6" (`style: "slide"`); otherwise ASI:One stacks them, and six
  eBay sales with a photo each came to about 2,000 px. A deal's button carries the deal's ID,
  so a tap on a card from an earlier search opens that deal or says the list is out of date,
  never another deal.
- **Text still works.** Links also stay in the text line, since card text can't be clicked and
  some clients show no cards, and the text names the typed command for every button ("Tap
  Approve, or reply "approve""), because ASI:One's planner relays taps as prose. A tap does
  exactly what the typed command does, with the same checks. Floors appear only on the seller's
  own cards, never a buyer's.

The redesigned cards are built and checked two ways: each one is rendered with ASI:One's own
card component (loaded from asi1.ai), and the tests check every card against
`card_schema.py`. They haven't been seen in the real ASI:One app yet; that needs the new code on
the server and a chat.

**One message per answer.** On October 25 the real ASI:One app showed every card of a turn
twice, a stale "Pricing your stuff" card and the home menu beside a finished proposal, and
separate messages' text run together ("Got your photo…You're in…Here's my suggested
listing"). The server had sent each card once, but several messages per turn: a proposal and
then its eBay sales, an attachments-only message after the campus welcome, the photo's
"still working" and proposal while the student was signing up, and every card again for a
resent message. So an answer is now one message: its text, then its cards (a proposal's card,
then the sales carousel), with no card twice for one message. Only slow work still sends two:
"still working" with the progress card, then the result without it. Several photos and other
sites' deals still follow as their own messages.

Seen in the real ASI:One app on October 25, before that fix and before the layout redesign: the
campus cards, the email form, the proposal card, the sales carousel, and the menu.

### How ASI:One lays out a card

On October 25 the real ASI:One app showed the home menu as one long column: every tile,
picture, and button stacked, though the code put tiles two to a row. So we read asi1.ai's own
JavaScript (its card schemas and its renderer) the same day. What it showed:

- **Only buttons, pictures, and fields share a line.** In a `row` group, buttons, images, and
  inputs split the line equally, and short text and badges sit beside them. Any group, list, or
  section takes a whole line. Each menu tile was a column group, so each got its own line, and
  the "picture beside the text" layout put a full-width 16:9 picture (about 290 px tall) on top
  of every card.
- **A carousel stacks its items** one under another unless it sets `style: "slide"`, which
  shows one item at a time with arrows. ASI:One's detail card puts all its buttons in one
  line, however many there are.
- **A card locks after its first tap** unless its metadata sets `resubmittable`: every button
  and field is disabled, and in a carousel every other item's button disappears. So after
  opening one deal, another couldn't be opened.
- **The app accepts more than the Python package lets you send.** `uagents_core` 0.4.12 (the
  newest on PyPI) rejects buttons with a picture (a tile), buttons that open a link
  (`redirect`), boxed groups (`style: "inset"`), a button under a carousel (`root_cta`),
  accordions, and the `resubmittable` flag, all of which the web app reads. So Yardsale builds
  cards with its own module, `card_schema.py`, which mirrors what the app reads.
- **A card the app rejects isn't shown at all**; the message's text still is. The app checks
  each card strictly (an unknown field fails it), which is why the tests check every card
  against `card_schema.py`.
- **A tap sends a chat message**: `@<agent address>` followed by the button's selection as JSON,
  with the card's field values merged in. A link-only button opens the link in a new tab and
  sends nothing.

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
4. The photo goes through the pipeline below. The reply is a proposal, in one message: the
   item, the asking and lowest prices and where each came from, the three closest eBay sales as
   links under "Recent eBay sales:", what similar ones ask right now under "Listed for sale
   now:" (never mixed with the sales), the proposal card with the only Approve button, and a
   carousel of up to 6 of the sales behind the price ("the 6 closest of the 13 eBay sales").
   Nothing is listed yet.
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
  metadata and no text, as in Fetch's own examples; one that also has words or a photo gets the
  metadata on its answer, not as a second message.
- **A repeated photo that's still being priced** gets "I'm still pricing that photo" instead of
  the help text.

A photo link counts whatever its `mime_type` says, since ASI:One doesn't always
send a full `image/...`, and each message's attachments are logged (URI, type, metadata keys, never
the text). A message ASI:One sends twice (same message ID) gets
the same text again, without its cards (they went out the first time), instead of being acted
on twice. If the first answer wasn't delivered, the whole answer goes again.

**Campus mode** (`YARDSALE_CAMPUS=1`): a new conversation's first message is answered only with
"Are you a student?" and its card. A photo in that message is priced at once, but its proposal
is held until the student signs up: the sign-up confirmation ("You're in, at UCR") and the
proposal then come as one message, as separate paragraphs, and if pricing is still running the
confirmation says so and the proposal follows once. Words in that first message without a
photo ("find me a mini fridge under $60") are kept and answered with the sign-up confirmation,
since searches are same-school; a bare hello isn't. School names read as people
say them: "UCR", "UCLA", "MIT", "Berkeley".

### Messages the chat doesn't recognize

On October 25 a seller who had listed a ThermoFlask and posted it on Craigslist typed "someone
messaged us message them back", then "someone messaged me about the waterbottle reply to them
and haggle", and both times got the greeting and the home menu. Now:

- **A seller asking about buyers** ("someone messaged me", "any buyers?", "did anyone reply",
  "message them back", "reply to them and haggle") gets each live listing's Craigslist post and
  who's emailing, with the highest offer. The reply says the item's agent answers Craigslist
  emails by itself within seconds, so the seller doesn't need to tell it to, and that messages
  in Craigslist's own chat can't be read (ask buyers to email). Patterns in
  [commands.py](../yardsale/commands.py) catch these first; ASI:One's reader also has `buyers`
  and `status` intents ("did it sell?" shows `my listings`). Either reading only shows a view.
- **Haggling words from a seller never start the buyer agent.** "Haggle with them" from a
  seller with nothing searched shows their buyers; with search results too, it asks which.
- **Anything else unclear** gets a short question about what the conversation has going, with
  a button for the likeliest one or two (a proposal waiting, buyers for a live listing, the
  last search, deals, orders, negotiations, watches) and Something else. A bare "yes", "the
  second one", or "no" answers it (`awaiting` "choice"); anything else drops the question. The
  buttons only show things; approving and buying still take the proposal's or counter's own
  "yes" or button.
- **The greeting is for first contact only.** A conversation that already has history gets
  plain help, never "Hi, I'm Yardsale" again.

### Several photos in one message

For a student moving out, one item per message is too slow. A message can carry up to 8 photos
(`chat.MAX_PHOTOS`), one item each; more than that, and the first 8 are priced and the reply asks
for the rest in another message (the extras count as seen, so a repeated link doesn't price them
later).

- Each photo goes through the same Identifier → Researcher → Appraiser pipeline, 4 at a time
  (`chat.PHOTOS_AT_ONCE`), and becomes its own proposal, numbered in the order the photos were
  sent. Numbers continue across messages in a conversation and never shift, so `#2` stays `#2`
  after `#1` is approved or canceled.
- **Cost:** every photo is a separate paid run (Apify plus two Claude calls), so 8 photos cost 8
  times one photo. Text in a message with several photos isn't sent to ASI:One, since it can't
  say which item it's about.
- **Prices:** a price typed with several photos ("at least $40") isn't used, and the reply says
  so. The seller sets each one by number: `lowest 2 $40`, `ask 3 $60`, `#2: ask $60, lowest $45`,
  or `approve 2 at $50`. A photo with no close sales doesn't stop the others; it waits for
  `ask 3 $25`.
- **Approving:** `approve all` lists everything waiting, `approve 2` or `approve 1, 3` lists
  those, and `cancel 2` or `cancel all` drops them. With several proposals waiting, a plain
  `approve` (or a price without a number) lists them and asks which one, rather than guessing;
  with one waiting, it works as before. The Approve card on each proposal also still works.
- **Timing:** the first reply waits up to 90% of `chat.REPLY_WITHIN` (36 s) and carries every
  proposal ready by then. Each remaining one is sent as its own message as soon as it's priced,
  never before the first reply, and a last message sums up where every photo stands. Batch
  proposals don't send the eBay sales carousel (it would double the messages); the proposal text
  still links the three closest sales.

`approve all` starts item agents one after another, so with many hosted items it can take longer
than 40 seconds; the chat then says "Working on it" and sends the result after. This hasn't been
tried in the real ASI:One app yet, including how it shows several uploads in one message and
several replies in a row.

## Buying in the ASI:One chat

Code: [chat.py](../yardsale/chat.py), [shopper.py](../yardsale/shopper.py),
[haggle.py](../yardsale/haggle.py), [negotiation.py](../yardsale/negotiation.py), and
[checkout.py](../yardsale/checkout.py).

1. The buyer asks for something ("find me a cheap road bike under $300"). The Market searches
   active listings by word ([search.py](../yardsale/search.py)), with no paid service. A budget
   filters on the asking price only, so results never hint at a floor.
2. Only on an explicit request after a search ("haggle with the seller on 1, max $320", or
   "haggle with the sellers"), the Market sends that item, or the results asking at most about
   1.4 times the max, to the Shopper. Shopping words ("best deal", "best price", "get me a good
   deal") and the planner's prose are searches (Yardsale and other sites), never a haggle, and
   `asi1-mini` doesn't read a haggle request. The Shopper haggles with all of
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

## When Yardsale has nothing: other sites (the Deal Finder)

Code: [dealfinder.py](../yardsale/dealfinder.py) (the agent), [elsewhere.py](../yardsale/elsewhere.py)
(the search and cache), and [deals.py](../yardsale/deals.py) (the scoring), on Rahul's
[sources.py](../yardsale/sources.py) and [watches.py](../yardsale/watches.py). Added October 24.
The probe below was run live once that day (road bike, max $300, ZIP 94103, Facebook on): all
four sources parsed with no missing prices or links (OfferUp 3 s, eBay 7 s, Craigslist 16 s,
Facebook 20 s; 10 comps gave a $600 market from 3 same-thing sales). Every test uses fake
sources and fake comps.

After talking to a Fetch judge, agent-to-agent purchases were dropped from the pitch. The buyer
product is: say what you want and your max, and Yardsale's agents find the best-priced,
best-quality items across eBay, Craigslist, OfferUp, and Facebook Marketplace and explain why
each is a good deal. Yardsale's own listings still come first when they match.

- A chat search or haggle that finds nothing on Yardsale sends the **Deal Finder agent**
  (`yardsale-dealfinder`, `@ys-dealfinder`, its own Agentverse mailbox, the sixth) a
  `FindDeals` (query, max, ZIP) and gets `DealsFound` back. It searches every source once, at
  the same time: eBay, Craigslist, OfferUp, and Facebook Marketplace with `YARDSALE_FACEBOOK=1`.
  `search other sites`, `check ebay and craigslist`, or `search ebay for a road bike under
  $300` asks for it directly (always every source, whichever site is named).
- **The market price.** Alongside the sources, the finder runs the Researcher's eBay
  sold-listings search (the same Apify actor, 20 sales from the last 90 days) for the search
  words, once per words, cached 10 minutes. The sales whose titles carry every search word
  (or at least half, when fewer than 3 do) set the typical sold price: their median, shipping
  included, skipping lots, parts, accessories, and accepted-Best-Offer sales when there are
  others. No Claude call: keyword overlap stands in for the Appraiser's judgement, since the
  buyer side has no photo.
- **The score** (`deals.py`): price against the market first (up to 50 points at 50% or more
  under the typical sold price, negative above it; without a market price, up to 20 against
  the median of the listings that match every search word), then quality (about 20 points:
  the site's condition words, photo count, seller rating when given, a title with enough words,
  how recently it was posted), then fit (−30 per search word the title lacks; more than half
  missing is dropped). "Parts", "for repair", "broken", "lot" cost 60; accessories of the thing
  ("road bike helmet" for "road bike") cost 40, unless asked for. More than 70% under the
  market is flagged "check it's the whole item". The same item on two sites (title words 80%
  alike and totals within 3%) is shown once.
- The reply lists the five best, with links, each with a one-line reason: "$180 on Craigslist,
  42% under the $310 typical sold price, like new, 6 photos, posted 2 days ago". The header
  says what they were judged against ("typical sold price on eBay: $310, from 4 sales"). A
  carousel card shows their photos with the percentage. The reply says they aren't Yardsale
  listings, so no agent haggles for them; Craigslist sellers take offers by email.
- The search runs after the conversation's lock is released. If it takes longer than
  `REPLY_WITHIN`, the first reply says it's searching and the deals follow as a second message.
  The Market waits up to 240 s for the agent, like the Researcher.
- Each run costs about $0.03 to $0.05 per site plus about $0.08 for the comps, so results are
  cached 10 minutes per (search words, budget, ZIP) and comps per search words, and a search
  still running is joined, not started again. A search where no site answered isn't cached,
  and neither is a failed comps search. The log has counts only, never the words or the max;
  the Market's client (`DealsClient`) keeps the same interface, so the chat and the API don't
  know the search runs in another agent.
- `uv run python -m yardsale.elsewhere --probe "road bike" --max 300` runs one small paid
  search per source (10 results each, plus 10 comps; about $0.10, $0.16 with Facebook) and
  prints per-source counts, what each parser could read (prices, links, photo counts,
  conditions, dates), the market price, and the top 3 with scores, so the OfferUp and Facebook
  parsers can be checked against live actor output. Its first run found two things, both
  fixed: OfferUp ignored its result cap (85 for 10; Apify's `limit` and a cut after parsing
  bound it now), and "Road Bike Brake Levers" at 84% under the market ranked third (bike parts
  are now accessory words, and a part priced far under the market is dropped, not shown).
  What each site gives: Craigslist a photo count (`imageCount`) and a date but no condition;
  eBay a condition and seller rating but no date; Facebook search results only a primary photo
  (condition and date need its paid per-listing details). Unknown signals score as unknown.
- **Search fixes, October 24 evening** (after "no listings found" for a water bottle, and no
  Jordans). Checked against 20 live runs, recorded and re-ranked offline:
  - *Query.* Every number used to be dropped, so "air jordan 4 retro" searched "air jordan
    retro" and showed Jordan 11s and 12s; model numbers now stay. A place in the message ("in
    Berkeley", "near 94704", "sf") is read by [places.py](../yardsale/places.py) and taken out
    of the words instead of being searched for. Sizes, measurements, years, and style codes
    ("size 10", "32 oz", "FV5029-141") are left out of what the sites get (Craigslist found
    nothing for the Identifier's full Jordan query) and count as soft words in the score.
  - *Place.* Every place becomes a ZIP code (`FindDeals.zip`, `?zip=` on `searchOtherSites`).
    Craigslist then searches within `YARDSALE_CRAIGSLIST_MILES` (10) of it with a search URL
    (checked live: a 5-mile search around 94704 returned only East Bay posts), OfferUp around
    it, and Facebook in its city. Without one, `YARDSALE_ZIP`, else each site's default
    (sfbay, 94103). The reply says where: "Local sites searched around Berkeley (94704)".
  - *Fit.* A title says a word through a synonym ("Hydro Flask" is a water bottle), a compound
    ("minifridge"), or "4's" for "4". "box", "only", "light", and "seat" no longer make a whole
    item an accessory ("new in box", "light blue"); bike parts count only in a bike search; an
    accessory word after "with" or "+" is an extra. Two posts with the same title and price in
    different towns are two items (OfferUp's "Air Jordan 4 Retro" posts at the same price in
    Hayward and American Canyon had been cut to one, and two $60 "Mini Fridge" posts too). When nothing fits, the closest are shown and flagged `loose_match`.
  - *Parsing.* Craigslist's `images` arrives as a string ("['https://...']"), which hid every
    photo; it's read back as a list. Its `location` was another post's in about half the
    results, so the town comes from the post's slug. A post the actor gave no link (6 of 10 in
    one search) gets a Craigslist search link for its title instead of being dropped.
  - *Failures.* `SourceError.kind` says why a site failed: timeout, blocked (only error items,
    or an empty dataset whose run says its page loads failed), busy (Apify's 5-runs-at-once
    limit on the free plan, hit live by a probe; retried three times first), credit, setup, or
    error.
  - *Runs at once.* On October 25 a photo failed with Apify's raw "concurrent-runs-limit-exceeded"
    JSON in the seller's chat: another chat's deal search (four sites plus comps) held all 5
    runs. Every Apify run in the server now waits for a slot (`sources.apify_slot`, 5 or
    `YARDSALE_APIFY_RUNS`), one slot is kept for eBay sold-price searches, and the Researcher
    retries a refused run too and says why in plain words ("Too many price searches were
    running at once"). Runs from another machine on the same Apify account aren't counted.
    `Found.sites` (and `DealsFound.sites`) has each site's outcome: ok with listing and fit
    counts, empty, or failed with the reason. The chat says "I didn't find" only of sites that
    answered, names each failed site with why, and when every site failed says it couldn't
    search; the API's `searchOtherSites` returns `sites`, `place`, and `status: "failed"`.
- `watch for it` turns the last search (or `watch for a road bike under $300`) into a standing
  search that polls every 6 hours for a week. The Market can't message a chat later, so any
  reply mentions new finds, and `my watches` shows them (then they count as told). `stop
  watching 1` stops one. The carousel's "Watch for more" button does the same.
- `@yardsale` gets the same through `GET /other-sites` (`searchOtherSites`): each listing
  with `versus_market_pct`, `score`, `reason`, and `flags`, plus `typical_sold_usd` and
  `comps_used`.

OfferUp uses `igolaizola/offerup-scraper` (most used of the OfferUp actors: about 190 users, 5.0
rating; about $1.40 per 1,000 results), searched around `YARDSALE_OFFERUP_ZIP` (94103 by
default). Facebook uses Apify's own `apify/facebook-marketplace-scraper` (about 11,700 users;
$2.60 to $6.20 per 1,000 depending on the plan), which takes a Marketplace search URL; it drops
the seller's name. Both were run live once on October 24 and parsed every result.

## Buying through `@yardsale` (Fetch Business)

Code: [business_api.py](../yardsale/business_api.py).

`@yardsale` can't message our agents, but a Fetch Business agent on a paid plan can call an
HTTP API described by OpenAPI (an "OpenAPI Integration", which Fetch calls a Business Tool).
So the server also runs a small API on `127.0.0.1:8110` when `YARDSALE_API_KEY` is set. It
uses the same Market backend as the chat:

- `GET /listings` (`searchListings`) searches active listings (asking prices only).
- `GET /other-sites?q=&max_usd=` (`searchOtherSites`) returns the best deals on other sites,
  each with its site, price, total with shipping, and link, and a `score` and `reason` when the
  deal finder gives them (both optional, so older results and unscored listings still work). It
  answers within 25 seconds, or with `status: "running"`; the same call again joins the same
  search. `GET /listings` points to it when nothing matches.
- `GET /price-check?q=&zip=` (`priceCheck`, added October 24) is for sellers asking what
  something is worth. It asks the Researcher for eBay sales of the last 90 days matching the
  words and works out, in plain code, the typical price (median), the usual range (quartiles),
  a suggested asking price (the median rounded like a seller would), and three example sales
  nearest the typical price, with links. Accepted Best Offer sales are left out when there are
  others, as in `appraise()`. The sales are matched by title, not by photo, and the answer
  says so; a photo to `@ys-orchestrator` gets Claude's same-product check. When
  `yardsale/scout.py` is importable and has `local_prices(query, zip_code)`, its answer is
  added as a `local` block; otherwise the block is left out. Checks are cached for 10 minutes
  per query, each one being a paid Apify search, and a check still running after 25 seconds
  answers `status: "running"` and is picked up by the same call. Queries and ZIP codes stay
  out of the log.
- `POST /negotiations` (`startNegotiation`, added October 25) has Yardsale's buyer agent email
  a Craigslist seller and haggle for the buyer, the same negotiation the chat's `negotiate`
  command starts ([craigslist_buyer.py](../yardsale/craigslist_buyer.py)). The buyer taps Reply
  on the post and pastes the reply address; the body carries it with the max and, optionally,
  the title, the post's link (its slug gives the title), and the asking price. The answer is
  the negotiation's `ref` (8 characters, the "Yardsale ref" in every email), what the first
  email will do, and a `next_step` that has `@yardsale` write the ref in its reply; the max is
  never returned. `GET /negotiations/{ref}` (`getNegotiation`) says where it stands: the offers
  so far, the seller's price, and once agreed, the price and the seller's pickup note. These
  negotiations belong to the API (owner `api`), so no ASI:One chat lists them and only a caller
  with the ref sees one; a chat's negotiations are 404 here. Starting one for a post already
  being negotiated through the API returns that negotiation (`already_started`); for a post a
  chat buyer is negotiating, 409.
- `POST /haggles` (`startHaggle`), `GET /haggles/{haggle_id}` (`getHaggle`),
  `POST /haggles/{haggle_id}/accept` (`acceptDeal`), and `GET /deals/{deal_id}` (`getDeal`) are
  agent-to-agent buying, which the pitch dropped, so since October 25 they're in the API and
  its description only with `YARDSALE_API_HAGGLE=1`. Off, they answer 404 with a message that
  says so, for a Business Tool imported while they were on. With them on:
  - `startHaggle` sends the Shopper to haggle with every match at once. It answers within 25
    seconds with the result, or with `status: "running"` and a `haggle_id`.
  - `getHaggle` returns the result, waiting up to 25 seconds while it's running.
  - `acceptDeal` buys the best price (or a named item's) and returns the payment link.
  - `getDeal` says whether it's paid.
- `GET /openapi.json` is the description Fetch reads. It needs no key; everything else needs
  the key in `X-Yardsale-Key` (or `Authorization: Bearer`).

Since October 24 the description leads with the two products (deals for buyers, prices for
sellers), and since October 25 the haggle and deal operations are left out unless
`YARDSALE_API_HAGGLE=1` (marked "advanced" when they're in), so `@yardsale` searches, checks
prices, and negotiates by email; it also says, wherever photos come up, that Claude reads them and ASI:One's models
read typed text only. The Business Tool has to be re-created for `@yardsale` to see new
operations (done October 25, so it has the five above and the description);
[fetch-business.md](fetch-business.md) has the workbench steps, the Skill text, and what Fetch
Business can and can't link to our agents.

Each haggle is its own buyer (`buyer_ref` `api-<haggle id>`), so its prices and deals are
separate from chat buyers', and the API only shows deals made through it. Negotiations started
here are owned by `api` and found by their ref alone. Floors never appear,
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
addresses, matches it to a post (by the post link in Craigslist's footer; see below), and sends
the item's agent (in the server or hosted on Agentverse) the same `Offer` or `AcceptCounter` a chat buyer's offer becomes, with
`buyer_ref` `email-<hash of the relay address>`. The item agent's counter, agreement, or deal
becomes the email reply, sent through the relay in the buyer's thread with a payment link once
they accept. A dollar amount is an offer, and "deal", "I'll take it", or "yes" while a price is
on the table accepts it; both are read with patterns, not a model ("10 dollars and 50 cents" is
$10.50). If the item agent doesn't answer in time, the buyer gets "let me check" once and the
email is tried again; the seller's chat shows the waiting offer right away.

A hosted item agent that doesn't answer within 25 seconds (`HOSTED_NEGOTIATE_TIMEOUT`) is
answered for by the Market, with the same rules (`negotiation.py`) and the floor, gap, and market
line it gave the hosted agent; that buyer's haggle then stays in the Market, and a sale there
deletes the hosted agent. Seen live October 25: an emailed offer to the hosted ThermoFlask got
"let me check" and nothing after, and the seller's chat said no buyers had emailed. This applies
to chat buyers' offers too.

**What the first live post taught (October 25).** A ThermoFlask was posted with Yardsale's inbox,
and the buyer's email went unanswered: its subject was "Waterbottle", which the buyer typed, so
matching by the posted title failed, and the email was skipped until a restart with nothing in
the log. Now:

- An email is matched by the post ID in the relay's footer ("Original craigslist post: <link>",
  on every relayed email), then by the link's slug (the city, then the title's first words)
  when exactly one active post fits best, and only then by a posted title in the subject.
  The footer is dropped before the buyer's words are read, so nothing in it becomes an offer.
- Post IDs are saved from Craigslist's posting confirmation (`cl posting: <title>` from
  `robot@craigslist.org`, then marked read), from the first email matched by slug or subject,
  or from a link the seller pastes (`record_post_link`).
- An email that matches nothing stays unread and is read again once a post or post ID is
  recorded, or every 5 minutes. The log gives the reason ("post link not recorded, subject words
  1"), never the address, subject, or text.
- Craigslist's own chat can't be answered: its "new craigslist chat message" emails carry no
  text, and reading the chat would mean logging in to craigslist.org, which its terms forbid.
  The notices are counted (`chat_notices_since`) and marked read, and the post text asks the
  seller to choose email as the contact method and turn chat off.
- Each answered email keeps what the buyer wrote and what the agent replied (300 characters
  each, SQLite only, never logged) so the seller can see it (`recent_exchanges`).
- The post text puts the title, price (`$11`), and description in separate paragraphs, since
  ASI:One's chat turns single line breaks into spaces.

The Market can't start a message in a seller's ASI:One chat (it keeps only a hash of the
session), so `my listings` shows Craigslist buyers and sales. Setup steps are in the
[README](../README.md#craigslist-by-email).

## Negotiating with Craigslist sellers by email

Code: [craigslist_buyer.py](../yardsale/craigslist_buyer.py). The other direction: when what a
buyer wants is on Craigslist, Yardsale's buyer agent emails the seller and haggles, within the
buyer's max, without telling the seller the max.

**The reply address comes from the buyer.** Craigslist shows a post's relay address
(`<random>@sale.craigslist.org`) only after a reader taps Reply, and that step can ask for a
CAPTCHA. Its [terms](https://www.craigslist.org/about/terms.of.use) forbid "robots, spiders,
scripts, scrapers, crawlers", collecting users' information, and getting around anything that
controls access to the site, and the search actor's results carry no address. So Yardsale never
fetches it: the buyer taps Reply on the post
([Craigslist's steps](https://www.craigslist.org/about/help/replying_to_posts)) and pastes the
address into the chat: `negotiate abc123@sale.craigslist.org "Trek FX 2 bike" max $250 asking
$300`. The title (in quotes, or the post's link) becomes the email subject; the asking price is
optional, and without it the first email asks for it. Craigslist's
[relay help](https://www.craigslist.org/about/help/posting/features/contact-info/email/mail-relay)
says replies come back from an `@reply.craigslist.org` address and threads last up to 4 months.

`@yardsale` starts the same negotiation through its `startNegotiation` tool (the buyer pastes the
reply address into its chat, with the max) and follows it with `getNegotiation`; see
[Buying through `@yardsale`](#buying-through-yardsale-fetch-business). Those negotiations belong
to the API, so an ASI:One chat's `negotiations` doesn't show them.

1. The chat saves the negotiation (the conversation, the address, the title, and the max, which
   is never logged or emailed). The next inbox check sends the first email from Yardsale's
   inbox: is it still available, and an opening offer from `haggle.opening_offer`. Every email
   says it's from the buyer's Yardsale assistant, an AI agent, and carries a "Yardsale ref" in
   its subject and signature.
2. Each seller answer is read with patterns: a dollar amount is their price; "yes", "deal",
   "sure", or "ok" after an offer takes it; "firm" or "no" holds their price; "sold" or "no
   longer available" ends it. The next offer comes from `haggle.buyer_move`, at most 4 offers,
   always at least one price step under the max. An unclear answer is re-asked once, then left
   for the buyer.
3. Once a price is agreed, the last email says the buyer pays in person and asks for a pickup
   time and place. The seller's next email is kept for the buyer. Yardsale takes no payment for
   Craigslist deals.
4. `negotiations` (or `my orders`) in the chat shows each one's state, since the Market can't
   message the chat unprompted.

**One inbox, two sides.** The inbox check asks the buyer side first. An email answering a
Message-ID we sent as a buyer (In-Reply-To or References, kept as hashes), or naming a
negotiation's ref in its subject or quoted text, goes to the buyer agent. Everything else goes
to the seller side as before. An email the buyer agent wrote, relayed back into the inbox (our
buyer agent writing to one of Yardsale's own posts), always goes to the seller side, and the
seller agent's own replies that no negotiation claims are marked read instead of answered. So
Yardsale's two agents can haggle through Craigslist's relay, and it ends: the buyer side sends
nothing once a negotiation is agreed, stopped, or sold. A test runs that exchange through a fake
relay, with and without the relay keeping Message-IDs, to a deal ($340 max) and to no deal ($250
max, under the $300 floor). On our own post the seller agent still answers "deal" with its
payment link, as it does for any email buyer.

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
| 2b | Market Scout | At the same time as the Researcher: searches what similar items are **listed for right now** on Craigslist, OfferUp, Facebook Marketplace (with `YARDSALE_FACEBOOK=1`), and eBay, through the same Apify-backed sources buyers' searches use, scoped to `YARDSALE_ZIP` where a source takes one. It returns, per site, the count, low, median, and high asking price and the 3 closest listings with links. A failure or a slow answer is logged and the proposal goes on without it. | [scout.py](../yardsale/scout.py) |
| 3 | Appraiser | Claude compares the seller's photo with each sale's title and photo (up to 10 photos), and marks each sale as the same product or not, plus a 0–1 similarity score. The price is then worked out in code, not by Claude. | [appraiser.py](../yardsale/appraiser.py) |
| 4 | Market | Combines the sold prices and the live asking prices into a market view (`pricing.market_view`): the fair asking range, the market line item agents cite while haggling, and a plain warning when the seller's own price is 35% or more off what it sells for ([advice.py](../yardsale/advice.py)). Saves a proposal with the prices and sends it to the seller. Spawns the item agent once the seller approves. | [bureau_server.py](../yardsale/bureau_server.py) |

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
ID: up to 180 s for the Identifier and 240 s each for the Researcher, Scout, and Appraiser. If
the Identifier, Researcher, or Appraiser fails or times out, the seller gets a `ListingRejected`
with the reason, for example "APIFY_TOKEN is not set"; a Scout failure only leaves the proposal
without live asking prices. Agents other than the chat send `ListPhoto`, get `Proposed`, and answer with
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
  agreed at once. That follows the rules, but nothing warned the seller. Since October 24 the
  proposal (and every `ask $X` or `lowest $X` reply) says so plainly when a seller's price is
  35% or more off what the item sells for, with the numbers and a suggested price
  (`advice.price_feedback`); the seller's price still wins.
- **Apify credit.** The team has $200 of Apify credit (October 24), about 2,500 photos at
  $0.08 each.
- **Probing the floor with new identities.** One buyer can't find a floor from counters or
  agreements, but someone making many identities could still narrow it down by offering just
  above and below, as on any marketplace with automatic offers.
- **Live buyer test needs the Market connected.** In live mode the Market's Almanac entry points
  at its Agentverse mailbox, so `buyer_test` without `--offline` only reaches it once it's
  connected to Agentverse.
- **`@yardsale` and our agents.** It can't message `@ys-orchestrator`, but on the Plus trial
  (started October 24) it calls Yardsale's HTTP API, so buyers can search Yardsale and other
  sites, check prices, and have the buyer agent negotiate with a Craigslist seller in its chat
  (and haggle with Yardsale's own sellers and buy, with `YARDSALE_API_HAGGLE=1`). Sellers still go to `@ys-orchestrator`, since a photo has to reach Claude. Its
  Orders page takes orders only from Square or SumUp POS, so our sales don't show there. The
  API is public only through a quick tunnel, whose URL changes when it restarts. Details are in
  [calhacks-plan.md](calhacks-plan.md#what-fetch-business-offers).
- **Craigslist by email.** Tested only with a fake inbox; never run against real Craigslist
  or Gmail. Unknown: whether the relay takes replies only from the posting's own email (we reply
  from it), whether relayed subjects keep the title (matching ignores case and punctuation),
  what the relayed body contains (a quoted price in it could be read as an offer), relay
  delays, and whether links are stripped (if so, `YARDSALE_MAIL_LINKS=0` asks the buyer for a
  phone number and the seller texts the link from `my listings`).
- **Negotiating with Craigslist sellers.** Tested only with a fake inbox and relay. Unknown:
  whether the relay keeps our Message-IDs and subjects (the ref in the subject and signature is
  the fallback), whether it accepts mail from Yardsale's address for posts made by someone else,
  and how real sellers word their answers. Each email is one buyer's own reply to a post they
  chose; Yardsale never sends to an address it found itself.
- **Telling sellers about a sale.** The seller sees a sale under "my listings"; Yardsale doesn't
  message them when it happens.
- **Interactive Cards in the real app.** Every reply carries a card (see "Every reply is a
  card"), built with Yardsale's own [card_schema.py](../yardsale/card_schema.py), which mirrors
  what ASI:One's web app reads, since the installed `uagents_core` cards module rejects some of
  those fields (metadata only, no protocol digest change). Seen in ASI:One's web app: the
  Approve card on October 23, and on October 25 the campus cards, the email form, the proposal
  card, the sales carousel, and the menu, all before that day's layout redesign. The redesigned
  cards have been rendered with ASI:One's own card component and checked against its schema,
  but not yet seen in the app. ASI:One's planner API received the haggling card as a card for
  the buyer to act on. Every reply also works as plain text, and typed commands do the same
  things.
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
  hand on each profile page. The Market Scout and Deal Finder got theirs later that night the
  way hosted items do: drawn by ASI:One, saved in the public Apify picture store, and set
  through `PUT /v2/agents/{address}`. The first drawing of the bike had made-up numbers and a tag on it,
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
