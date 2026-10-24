# Background and decisions

This page condenses the planning chat and the prototype session from October 22, 2026.

## The idea

1. A seller snaps a photo of an item.
2. About 20 seconds later, the item is a live agent with its own address. It knows the seller's
   secret lowest price.
3. A buyer asks ASI:One something like "find me a cheap road bike." ASI:One reaches Yardsale, and
   Yardsale connects the buyer to the matching item agents.
4. Item agents counter lowballs and close deals. The seller keeps the money.

**Demo moment:** the item agent's address appears right after the photo upload. That's the visual
proof judges will remember.

## Decisions

1. **One `@yardsale` handle on Fetch Business. Sellers don't get their own.** Fetch Business isn't a
   marketplace. It gives a business one verified agent handle, like registering a domain name
   (`@AlaskaAirlines`, `@Disney`). There are no item listings or marketplace feed, and the
   workbench is that business's back office for orders and conversations. Yardsale is the business,
   so it claims `@yardsale`. Making casual sellers verify a business would kill the snap-a-photo
   demo.
2. **One uAgent per listed item, owned by Yardsale.** An agent's identity is a keypair made from its
   seed, and our server generates the seeds, so Yardsale owns every item agent. A buyer's ASI:One
   account owns nothing. Each item agent records its seller's ID and payout details, so when a deal
   closes the money goes to the seller, not to Yardsale. Pitch: "Yardsale manages the agents,
   sellers keep the money."
3. **Item agents stay off Agentverse.** Agentverse's agent limits only count agents connected to
   your Agentverse account. The chat cited 8 hosted plus 8 mailbox agents on the free plan, and 25
   each on Premium; these figures haven't been checked against our account. Item agents run in our
   own server and register only on the Almanac, so there's no cap on them.
4. **Graduation pitch:** power sellers can later move to their own Fetch Business agent that they
   own.

## Architecture

| Agent                       | Runs where                                  | Agentverse slots       |
| --------------------------- | ------------------------------------------- | ---------------------- |
| `@yardsale`                 | Fetch Business. ASI:One finds it.           | Unknown (mentor Q)     |
| Market agent                | Our Bureau server, plus an Agentverse mailbox (planned) so ASI:One can reach it | 1 |
| Item agents (one per item)  | Our Bureau server, Almanac only             | 0, at any count        |
| Buyers                      | ASI:One acts for them                       | 0                      |

Flow: ASI:One → `@yardsale` → Market agent → item agent. The Market relays replies back.

## What the prototype proved

- Item agents can be created inside a Bureau that's already running, all on one port. Each gets its
  own address and Almanac registration.
- 12 item agents registered on the real Almanac. A separate buyer process found every one through
  it and closed 12 of 12 deals. An offline run did 40 of 40.
- Almanac registration through the Almanac API is free and needs no wallet or faucet.
- Catch: `Bureau.add()` alone doesn't start or register a late-joining agent. `spawn_item_agent`
  in [yardsale/bureau_server.py](../yardsale/bureau_server.py) handles both.

## Not built or unknown

- **Public address.** Agents are reachable only at `127.0.0.1`. ASI:One needs the server on a hosted
  machine or behind a tunnel such as ngrok, with that public URL as the Bureau endpoint.
- **ASI:One connection.** The Market speaks a custom protocol. It needs the Chat Protocol and an
  Agentverse mailbox so ASI:One can reach it. This is the next step, because it decides whether the
  whole flow works end to end. It uses one Agentverse slot.
- **`@yardsale` handing off to our agents.** Unknown whether a Fetch Business agent can pass buyers
  to an outside uAgent address. Fallback: run everything on Agentverse and pitch Fetch Business as
  the graduation path for power sellers.
- **Photo to listing.** Turning a photo into a title and price isn't built at all, and it's the
  first thing judges see.
- **Payments.** None yet. A closed deal is only an agreed price.
- **Persistence.** Listings live in memory, so a restart forgets them. Seeds come from item IDs, so
  re-creating stored items on boot would bring back the same addresses.

## Fetch Business to-dos from the chat

These were planned in the chat; whether they're done wasn't recorded.

1. Set up `@yardsale` on Fetch Business: name, description, and a knowledge blurb about how
   Yardsale works.
2. Check whether it can hand off to an outside uAgent address, or at least point to the Market
   agent.
3. Test that ASI:One can find `@yardsale`.
4. Check the Agentverse profile page to confirm whether the "premium" from a past hackathon is
   Agentverse Premium or ASI:One premium.

## Questions for a Fetch mentor

- Can deals closed by outside agents show up as orders in `@yardsale`'s Fetch Business workbench?
- Can a Fetch Business agent hand off to an outside uAgent address?
- Does a Fetch Business agent count against the Agentverse agent limit?
