# Demo video script

For the Cal Hacks 13.0 video (3 to 5 minutes; sponsors judge from it alone) and the live pitch.
Submissions close **Sunday, October 25 at 9 AM PT**. Record each part separately and cut them
together; nothing has to run live in one take.

## Before recording (about 30 minutes)

**One machine runs the server.** Every agent's identity comes from the shared master seed, so
two running servers steal each other's messages. Stop the server on every other laptop first.

1. Update and check the code:
   ```bash
   git pull
   git log --oneline -1   # 0752ac6 or newer
   uv sync
   ```
2. In `.env`, next to the usual keys (Claude, Apify, ASI:One, Agentverse, Stripe test, the
   Yardsale API key, the mail address and app password):
   - `YARDSALE_CAMPUS=1` and `GOOGLE_MAPS_API_KEY` set, for school sign-up and meeting spots.
   - `YARDSALE_HOST_ITEMS` **removed or 0**. Agentverse's mailboxes have been answering 503,
     and hosted item agents' replies get lost; item agents in the server answer in
     milliseconds. Use screenshots of a hosted item's Agentverse profile instead (one exists:
     `@thermoflask-double`).
3. Start it, from a Claude Code shell or any terminal:
   ```bash
   env -u ANTHROPIC_BASE_URL uv run python -m yardsale.bureau_server --check
   env -u ANTHROPIC_BASE_URL uv run python -m yardsale.bureau_server
   ```
   Wait for `Craigslist email: answering buyers every 10 s`.
4. `@yardsale` needs the tunnel:
   ```bash
   cloudflared tunnel --no-autoupdate --protocol http2 --url http://127.0.0.1:8110
   ```
   If the URL changed since the last run, re-create the Business Tool with the new
   `/openapi.json` ([fetch-business.md](fetch-business.md)). Check it answers:
   `curl <tunnel>/openapi.json`.
5. Accounts and screens:
   - **Seller:** a phone or browser in ASI:One, a fresh chat with `@ys-orchestrator`, signed up
     with a `@ucr.edu` email (or any school email). Then type `where should we meet` once: it
     should list three campus spots with map links. If it says there's no map yet, wait a
     minute (the first student from a school starts the campus lookup) and ask again.
   - **Craigslist buyer:** any email account that is **not** Yardsale's inbox.
   - **Deal buyer:** `@yardsale` open in ASI:One.
6. Pick a real item worth $40 to $150 (a lamp, a backpack, a desk chair, a textbook). The
   numbers read better than a $11 bottle, and a counter-offer has room to move.

## Rehearse the Craigslist part first (15 minutes)

Craigslist needs a real post, and its email relay takes a minute or two. Do this before the
camera rolls:

1. Price and approve the item in the seller chat (Scene 1), then say `craigslist`.
2. Post it on craigslist.org exactly as given: the title as written, email as the contact
   method with Yardsale's inbox, and Craigslist chat off. Confirm the post from the email
   Craigslist sends to the inbox. The server reads the confirmation and logs
   `Craigslist confirmed the post of …`.
3. Paste the live post's link into the seller chat. It answers "that Craigslist post is your …".
4. From the buyer's email, tap Reply on the post and ask "is this still available?". The agent
   should answer with the asking price within about 20 seconds of the email landing.

If any of that doesn't happen, check the server log before recording (see "If something goes
wrong").

## The script

Timings are for the cut video. Narration is in quotes; keep it short and let the screens talk.

### 0:00 The problem (20 s)

Title card or a cluttered dorm room.

> "It's finals week and you're moving out. The mini fridge, the desk, the lamp: it all has to
> go. Post it and two hundred messages come in: is this still available, would you take ten
> bucks. You can't answer them and study. Yardsale's agents do it for you."

### 0:20 Scene 1: sell with a photo (50 s)

Seller screen, ASI:One, `@ys-orchestrator`.

1. Attach the photo and type: `sell this, at least $<minimum>`.
2. Show the progress reply, then the proposal (cut the wait; pricing takes under a minute).
   Point at: the item Claude identified, the asking price from **recent eBay sales**, what
   similar ones are **listed for nearby right now**, the "check your price" line, and the sales
   carousel.

> "Claude reads the photo. Our Researcher agent pulls what it actually sold for on eBay in the
> last 90 days, and the Market Scout checks what it's asking right now on Craigslist, OfferUp,
> and Facebook, all through Apify. The price comes from code, not a guess, and if my minimum is
> way off, it tells me."

3. Tap **Approve**. Show the reply with the item's own agent.

> "Now the item has its own agent. It knows my minimum, and nobody else ever sees it."

### 1:10 Scene 2: the agent answers the DMs (60 s)

1. Seller says `craigslist`: show the ready-to-paste post. Cut to the live Craigslist post.

> "Craigslist doesn't allow bots to post, so I paste it. Buyers' emails go to the item's
> agent."

2. Buyer's email: reply to the post with a low offer, under the minimum but at least half the
   asking price (for a $60 ask and $45 minimum, try "would you take $35?").
3. Show the agent's answer in the thread: a counter, with a reason from the market, never the
   minimum.

> "It haggles like I would: it cites what these actually sell for and never goes below my
> minimum."

4. Buyer replies `deal, I'll take it`. Show the payment link email, open it, pay with
   Stripe's test card `4242 4242 4242 4242`.
5. Seller chat: `my listings`. Show it sold to a Craigslist buyer, with the payout after 5%.
   The server checks Stripe every minute, so if it still shows unpaid, wait a minute and ask
   again.

> "I answered zero messages."

### 2:10 Scene 3: meet on campus (25 s)

Seller chat: `where should we meet`.

Show the three spots on campus, best first, each with a Google Maps link. Tap one.

> "Yardsale is for students at the same school. When it's time to hand it over, it suggests a
> busy public spot on your campus. Nobody has to say where they live, and it never suggests a
> dorm. Claude picks the spots and Google Maps places them."

### 2:35 Scene 4: buyers find the best deal (50 s)

`@yardsale` in ASI:One (Yardsale's Fetch Business agent):

`find me the best deal on a mini fridge under $60`

Show Yardsale's own listings checked first, then the best five from eBay, Craigslist, OfferUp,
and Facebook Marketplace, each with its score against the typical eBay sold price and a
one-line reason. Tap through to one listing.

> "Buyers say what they want and their max. Our Deal Finder searches every marketplace at once
> and scores each listing against what that item really sells for, so you see why it's a deal,
> not just a price."

### 3:25 How it's built (35 s)

Show the architecture diagram (`docs/architecture.png`) or the Agentverse profiles.

> "Seven agents on Agentverse: the Orchestrator, Identifier, Researcher, Market Scout,
> Appraiser, Deal Finder, and Shopper, plus one agent per item for sale. `@yardsale` on Fetch
> Business is the front door. Claude reads photos, ASI:One reads what you type and routes to
> the agents, Apify brings the market data, and Stripe test mode takes payment. Yardsale keeps
> 5% of each sale; sellers keep the rest."

### 4:00 Close (10 s)

> "You can't answer 200 DMs during finals. Your agents can. Yardsale."

## Say it exactly right

Judges check the repo against the video.

- **Models:** Claude reads photos; ASI:One's models read typed text only. Never say ASI:One
  reads the photo.
- **Craigslist:** the seller posts by hand (Craigslist bans automated posting). Yardsale never
  scrapes Craigslist or eBay pages; listings come from Apify Actors.
- **Payment:** Stripe test mode. Don't call it real money.
- **Pricing time:** "under a minute", not 20 seconds.
- **Agent-to-agent buying** (the Shopper haggling with item agents) is built but not pitched:
  leave it out, or give it one line.

## If something goes wrong

Look in the server log:

| Log line | Meaning | What to do |
| --- | --- | --- |
| `Craigslist email from buyer … : holding` | The item's agent didn't answer the offer in time; the buyer got "let me check". | You're on old code or hosting is on. Pull, remove `YARDSALE_HOST_ITEMS`, restart. The email is retried on its own. |
| `Hosted item … didn't answer; answering with its rules here` | A hosted agent went quiet and the server answered for it. | Fine; it just took 25 seconds. Cut the wait. |
| `Craigslist email not matched to a post` | The email didn't name a known post. | Paste the post's link into the seller chat; the email is retried. |
| `Couldn't read the Craigslist inbox` | Gmail login failed. | Check the mail address and app password in `.env`. |
| `Failed to retrieve messages: 503` | Agentverse is having trouble. | ASI:One replies may lag. Record that part again later. |

Only one Craigslist relay email per buyer arrives at a time, and Gmail can take a minute. If
the email part runs long, cut between the offer and the agent's answer.

If Scene 2 won't work in time, record Scenes 1, 3, and 4, and show the Craigslist answer from
earlier screenshots (the first email answered at the asking price) while saying the agent
haggles to a payment link.

## After recording

1. Upload the video and create the Devpost entry (image, repo link, video).
2. Share the selling and buying chats in ASI:One (search icon, then **Share chat**).
3. Register through the Cal Hacks ASI:One Submission Agent with the Devpost and video URLs;
   every teammate joins with the Team ID. Steps are in
   [calhacks-plan.md](calhacks-plan.md#fetchs-track-rules).
