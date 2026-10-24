# eBay setup for "Connect eBay"

Written Saturday, October 24, 2026. This page lists the steps a person has to take on eBay's
developer site before Yardsale can list a seller's approved items on eBay with Best Offer and
counter offers through the API. It covers the developer account, keysets, test users, the RuName
(eBay's name for an OAuth redirect), OAuth scopes, and what slows down production.

The plan: a seller grants Yardsale access through eBay's OAuth user consent (Authorization Code
grant). Yardsale publishes the item with the Inventory API with Best Offer turned on, reads offers
with the Trading API's `GetBestOffers`, and counters with `RespondToBestOffer`. The callback is
`<tunnel>/ebay/callback`, and the tunnel is a Cloudflare quick tunnel whose URL changes when it
restarts.

Sources are linked in each section. eBay's own pages come first. Anything from eBay's community
forum or from a third party's docs is marked **anecdotal** or **third party**. Some
`developer.ebay.com/api-docs/static/...` pages refuse automated fetches; their copies on
`edp.ebay.com` were used instead.

## Fastest path for the demo

1. **Check whether anyone on the team already has an approved eBay developer account.** Approval
   is "approximately 1 business day", and a sign-up on Saturday probably isn't reviewed until
   Monday. An existing account skips the only step we can't speed up. If nobody has one, sign up
   now anyway.
2. **Demo in the Sandbox.** Sandbox keys work as soon as the account is approved. They don't need
   the account deletion endpoint, and the "real seller" is a `TESTUSER_` seller.
3. **Opt in to business policies with the API.** Call the Account API's `optInToProgram` with
   `SELLING_POLICY_MANAGEMENT` for the sandbox seller before creating policies (anecdotal fix for a
   known Sandbox problem).
4. **Make buyer offers with `PlaceOffer`, not a phone.** eBay says the Sandbox doesn't support
   mobile web, and PlaceOffer is the documented way for a sandbox buyer to make a Best Offer.
5. **Point the RuName at the current tunnel** and edit it whenever the tunnel restarts, or keep the
   tunnel running from setup to the end of judging.
6. **Production on Sunday morning only if** the account is approved, the production keyset is
   active (account deletion done), and a teammate's own eBay seller account is ready to sell.
   Treat production as a stretch.

## 1. Join the eBay Developers Program

Steps, from [Get started with eBay APIs](https://developer.ebay.com/develop/guides-v2/get-started-with-ebay-apis):

1. Register at [developer.ebay.com](https://developer.ebay.com/). eBay asks: "Please use a
   business email address to register the developer account." Whether a `.edu` address counts is
   unknown.
2. Verify the email, accept the API License Agreement, and submit.
3. Wait for approval: "It will take approximately 1 business day for account approval."

**Faster path.** None found. eBay publishes no expedited review, and nothing official says
whether weekend sign-ups are reviewed. "Business day" suggests Monday for a Saturday sign-up.

**What others report (anecdotal):**

- One developer waited "a full week" with no update after being told one business day
  ([forum](https://community.ebay.com/t5/Traditional-APIs-Selling/Developer-account-taking-a-long-time-to-get-approved/td-p/34172363)).
- Several sign-ups were auto-rejected, some twice, citing eligibility and risk controls, with
  slow answers from support
  ([forum](https://community.ebay.com/forum/ebay-developers-program-57950/topic/developer-account-registration-auto-rejected-appeal-also-auto-denied-requesting-manual-review-465594/),
  [forum](https://community.ebay.com/forum/ebay-developers-program-57950/topic/established-seller-developer-program-registration-auto-rejected-twice-requesting-manual-review-469862/),
  [forum](https://community.ebay.com/forum/ebay-developers-program-57950/topic/developer-account-declined-on-riskeligibility-review-likely-account-linkage-timing-issue-rneldner-466680/)).

Nothing below can start until the account is approved.

## 2. Keysets and Marketplace Account Deletion

**Keysets.** Sign in, open **Application Keys**, and create a keyset for Sandbox and one for
Production. Each keyset has an App ID (client ID), a Cert ID (client secret), and a Dev ID. Keys
and tokens from one environment don't work in the other
([get started](https://developer.ebay.com/develop/guides-v2/get-started-with-ebay-apis),
[token request](https://edp.ebay.com/api-docs/static/oauth-auth-code-grant-request.html)).

**Account deletion requirement.** Every app must either subscribe to eBay's account deletion
notifications or opt out
([Marketplace Account Deletion](https://developer.ebay.com/marketplace-account-deletion),
[guide](https://developer.ebay.com/develop/guides/marketplace-user-account-deletion.md)). New
developers "must subscribe to or opt out ... before they make their first production API call",
and only then "the keyset/App ID is activated". So **the production keyset stays disabled until
this is done.** The Sandbox keyset isn't held up by it.

| Choice | What it takes | Fits Yardsale? |
| --- | --- | --- |
| Subscribe | An HTTPS endpoint (no `localhost`, no internal IP) that answers GET and POST, plus a verification token of 32–80 characters (letters, digits, `_`, `-`). eBay sends a GET with `challenge_code`; reply `200` with `Content-Type: application/json` and `{"challengeResponse": sha256_hex(challengeCode + verificationToken + endpoint)}`. Then acknowledge each notification with 200, 201, 202, or 204, check its `x-ebay-signature`, and delete that user's data. | Yes. Yardsale stores sellers' tokens and listing IDs. |
| Opt out | On the same page, turn on "Not persisting eBay data", pick a reason, and submit. Only for an app "that is not persisting any eBay data". eBay warns that wrong information "may result in penalties or having their account disabled." | No, since Yardsale stores eBay data. |

Notes:

- The endpoint URL is part of the hash, so a new tunnel URL means saving the endpoint again and
  passing the challenge again.
- If notifications go unacknowledged for 24 hours, eBay marks the endpoint down and emails the
  developer, who then has 30 days to fix it before being marked non-compliant. A quick tunnel
  that's off overnight would trip this; a stable host (a Cloudflare named tunnel on our own
  domain, or a small Cloudflare Worker that answers the challenge) avoids it.
- Build the JSON reply with a JSON library. eBay warns that a hand-written string can pick up a
  byte order mark and fail.
- Anecdotal: one developer's endpoint answered correctly but the portal still said "endpoint
  validation failed" and the keyset stayed disabled
  ([forum](https://community.ebay.com/t5/Token-Messaging-Sandbox-related/Production-keyset-is-still-disabled-after-correct-webhook/m-p/35391018)).

## 3. Sandbox test users and Best Offers

**Test users.** Go to **Develop > Tools > eBay Sandbox > Create Test Users**. Every username
starts with `TESTUSER_`. Make at least one seller and one buyer, since a seller can't buy their own
listing. Save each username and password
([get started](https://developer.ebay.com/develop/guides-v2/get-started-with-ebay-apis)).

**Is Best Offer supported in the Sandbox?** Yes, for the API. `GetBestOffers` documents a Sandbox
test: two test users, the seller lists a fixed-price item with Best Offer on, the buyer makes an
offer with `PlaceOffer`, and both call `GetBestOffers`
([GetBestOffers](https://developer.ebay.com/devzone/xml/docs/reference/ebay/GetBestOffers.html)).
`PlaceOffer` "can be used to make a Best Offer" and "You can test PlaceOffer in the Sandbox"
([PlaceOffer](https://developer.ebay.com/DevZone/XML/docs/reference/ebay/PlaceOffer.html)).
`RespondToBestOffer`'s page has no Sandbox note either way.

**How the sandbox buyer makes an offer:**

- **Documented:** call Trading `PlaceOffer` as the buyer test user, with a user token for that
  buyer. The User Tokens page in the developer portal can make one by signing in as the test user.
  The portal's API Explorer is the quickest way to send the call without code (check that it lists
  Trading calls).
- **From sandbox.ebay.com in a browser:** not documented for offers. eBay's Sandbox page says the
  site is reachable through "the WebSite interface", and an old tutorial places bids there
  ([Buying an item](https://developer.ebay.com/DevZone/xml/docs/HowTo/XML_ListingAndSelling/ListingAndSelling_buying.html)),
  but nothing confirms a Make Offer button.
- **From a phone:** eBay's list of unsupported Sandbox features says "Mobile apps and Mobile Web is
  not supported on Sandbox"
  ([KB 684](https://developer.ebay.com/support/knowledge-base/684)). A phone browser asking for
  the desktop site might work; untested.
- Anecdotal: `PlaceOffer` in the Sandbox has returned "Item not found" (231) for a valid
  fixed-price listing, and Sandbox buyers can't add a payment method or address
  ([forum](https://community.ebay.com/t5/Traditional-APIs-Selling/Sandbox-TradingAPI-PlaceOffer-API-quot-Item-Not-Found-quot-Error/m-p/34898259)).

**Sandbox problems to expect (anecdotal):**

- New sandbox sellers aren't opted in to business policies, and the Sandbox policy page sends you
  to the production site. Calling `optInToProgram` with `SELLING_POLICY_MANAGEMENT` fixed it
  ([forum](https://community.ebay.com/t5/RESTful-Sell-APIs-Account/Cannot-Opt-In-New-Sandbox-User-to-Business-Policies-Catch-22/td-p/35376198)).
- `publishOffer` in the Sandbox has failed with "Invalid <ShippingPackage>" (same thread) and with
  system error 25001
  ([forum](https://community.ebay.com/forum/account-inventory-catalog-and-compliance-57960/topic/error-when-publishing-offer-in-sandbox-468362/)).
  Error 25007 means the fulfillment policy has bad data, such as a wrong carrier or service code
  ([publishOffer](https://developer.ebay.com/api-docs/sell/inventory/resources/offer/methods/publishOffer)).
  If publishing keeps failing in the Sandbox, Trading `AddFixedPriceItem` with
  `BestOfferDetails.BestOfferEnabled` is a fallback for the demo.
- No emails are sent for Sandbox orders, and the Sandbox has no billing
  ([KB 684](https://developer.ebay.com/support/knowledge-base/684)).

## 4. The RuName (OAuth redirect)

eBay's `redirect_uri` is a RuName, a name for a saved redirect setup, not a URL. Each keyset has
its own, one for Sandbox and one for Production
([redirect URI](https://edp.ebay.com/api-docs/static/oauth-redirect-uri.html),
[KB 753](https://developer.ebay.com/support/knowledge-base/753)).

**Creating it** ([redirect URI](https://edp.ebay.com/api-docs/static/oauth-redirect-uri.html),
[KB 1198](https://developer.ebay.com/support/knowledge-base/1198)):

1. **Application Keys**, then **User Tokens** next to the keyset's Client ID.
2. Open **Get a Token from eBay via Your Application** and add a RuName (eBay redirect URL). The
   first time, confirm the legal contact address.
3. Fill in:
   - **Display Title**: shown at the top of eBay's consent page ("Yardsale").
   - **Privacy Policy URL**: needed for user tokens. A page on the repo or the tunnel works.
   - **Auth Accepted URL**: `https://<tunnel>/ebay/callback`.
   - **Auth Declined URL**: for example `https://<tunnel>/ebay/declined`.
   - **OAuth Enabled**: tick it, or the RuName only works for the older Auth'n'Auth flow
     (third party: [CData](https://cdn.cdata.com/help/ASK/sync/eBay-Source.html),
     [Apideck](https://developers.apideck.com/connectors/ebay/docs/application_owner+oauth_credentials)).
4. Save. Copy the RuName string (it is not the URL) into Yardsale's config.

**HTTPS.** Required: "This URL must support SSL and must use the HTTPS protocol"
([Getting tokens](https://developer.ebay.com/devzone/xml/docs/howto/tokens/gettingtokens.html)).
Third-party guides add that `localhost` isn't accepted. Quick tunnel URLs are HTTPS, so they
qualify.

**Changing it when the tunnel changes.** The URLs are edited on the same User Tokens page and
saved. eBay doesn't say whether a change takes effect at once; test it once. Third-party guides
say the accept URL must match exactly, down to a trailing slash. Ways to avoid editing it on every
restart:

- Keep one tunnel running from setup through judging.
- Use a Cloudflare named tunnel on a domain we own, so the hostname doesn't change.
- Point the Accept URL at a fixed HTTPS page that forwards `code` and `state` to the current
  tunnel. It must only forward to our own tunnel host, or it becomes an open redirect.

**The flow** ([consent request](https://edp.ebay.com/api-docs/static/oauth-consent-request.html),
[token request](https://edp.ebay.com/api-docs/static/oauth-auth-code-grant-request.html)):

| Step | Sandbox | Production |
| --- | --- | --- |
| Send the seller to | `https://auth.sandbox.ebay.com/oauth2/authorize` | `https://auth.ebay.com/oauth2/authorize` |
| Exchange the code at | `https://api.sandbox.ebay.com/identity/v1/oauth2/token` | `https://api.ebay.com/identity/v1/oauth2/token` |

- Authorize query: `client_id`, `redirect_uri=<RuName>`, `response_type=code`, `scope` (space
  separated, URL-encoded), and `state`, which eBay "highly recommends" for CSRF checks.
  `prompt=login` forces a fresh sign-in.
- The callback gets `code`, `state`, and `expires_in` (299 seconds in eBay's example). The code is
  single-use; don't store it.
- Token request: `POST`, `Authorization: Basic base64(client_id:client_secret)`,
  `grant_type=authorization_code`, `code`, `redirect_uri=<RuName>`.
- The access token lasts 7,200 seconds (2 hours). The refresh token's example lifetime is
  47,304,000 seconds (about 18 months). A refresh token can be revoked, for example when the seller
  changes their password.

## 5. Scopes and APIs

A keyset's scopes are listed under its **OAuth Scopes** link on the Application Keys page, and
Sandbox and Production can differ. The sample request on the User Tokens page has the full
`scope` value to copy
([scopes](https://edp.ebay.com/api-docs/static/oauth-scopes.html)).

| Job | API and calls | Scope |
| --- | --- | --- |
| Create the item, the offer, and publish it | Inventory API: `createOrReplaceInventoryItem`, `createOffer`, `publishOffer` | `https://api.ebay.com/oauth/api_scope/sell.inventory` (from the Inventory API's spec) |
| Merchant location | Inventory API: `createInventoryLocation` (every offer needs one) | `sell.inventory` |
| Payment, return, and fulfillment policies; business policies opt-in | Account API: `createPaymentPolicy`, `createReturnPolicy`, `createFulfillmentPolicy`, `optInToProgram` | `https://api.ebay.com/oauth/api_scope/sell.account` |
| Turn on Best Offer | Inventory API offer: `listingPolicies.bestOfferTerms.bestOfferEnabled = true`, optional `autoAcceptPrice` and `autoDeclinePrice` | `sell.inventory` |
| Read offers | Trading API: `GetBestOffers` | See below |
| Accept, decline, or counter | Trading API: `RespondToBestOffer` | See below |
| See the order after a sale (optional) | Fulfillment API | `https://api.ebay.com/oauth/api_scope/sell.fulfillment` |

Sources: the Inventory API spec
([inventory_api.json](https://developer.ebay.com/develop/api/spec/inventory_api.json)),
[publishOffer](https://developer.ebay.com/api-docs/sell/inventory/resources/offer/methods/publishOffer),
and the [Other APIs guide](https://developer.ebay.com/develop/guides/other-apis-guide.md), which
assigns Best Offer responses to the Trading API.

**Best Offer details from the Inventory API spec:**

- Best Offer isn't available for multi-variation listings, and only in categories that support
  it.
- `autoAcceptPrice` and `autoDeclinePrice` must be below the Buy It Now price. `autoDeclinePrice`
  maps well onto a seller's floor: eBay declines anything at or below it without asking Yardsale.

**Why the Trading API and not the Negotiation API.** The Negotiation API only sends
seller-started discount offers to buyers who watch an item or have it in their cart
(`findEligibleItems`, `sendOfferToInterestedBuyers`). It can't answer a buyer's Best Offer, and
says "counter-offers are not supported in this release"
([Negotiation API](https://developer.ebay.com/develop/api/sell/negotiation_api.md)).

**Trading API calls with OAuth.** eBay says OAuth user tokens work with the Trading API, and the
token must carry the scope each call needs
([Traditional APIs](https://edp.ebay.com/api-docs/static/oauth-trad-apis.html)). The
`GetBestOffers` and `RespondToBestOffer` pages don't list a scope. A developer support reply says
the base scope `https://api.ebay.com/oauth/api_scope` is enough for Trading calls, and the token
goes in the `X-EBAY-API-IAF-TOKEN` header in place of `RequesterCredentials` (anecdotal,
[forum](https://community.ebay.com/t5/Token-Messaging-Sandbox-related/What-OAuth-Scopes-Are-Needed-For-The-TradingAPI-Messaging-APIs/td-p/35025773)).
An app token from the client credentials grant can't make Trading calls. Ask for the base scope
plus `sell.inventory` and `sell.account`, then confirm with one Sandbox call.

**Counter offers** ([RespondToBestOffer](https://developer.ebay.com/devzone/xml/docs/reference/ebay/RespondToBestOffer.html)):

- `Action` is `Accept`, `Decline`, or `Counter`. Accept or counter one offer per call; decline
  several at once.
- A counter needs `CounterOfferPrice` (the total for the quantity) and `CounterOfferQuantity`. The
  price can't exceed the Buy It Now price for a single item.
- `SellerResponse` (up to 250 characters) lets the seller "provide more details to the buyer about
  the action being taken." `GetBestOffers` returns it as `SellerMessage` (up to 500 bytes), "a
  comment when making a counter offer", and the buyer can call `GetBestOffers` too. The pages don't
  say where eBay's website shows it to the buyer. The buyer's own comment comes back as
  `BuyerMessage`.
- Counters must never reveal the floor, so `SellerResponse` must not mention it either.
- Whether these Trading calls work on listings made with the Inventory API isn't stated anywhere
  we found. Test it in the Sandbox first.

**eBay's offer timing on the live site**
([eBay help](https://www.ebay.com/help/selling/listings/selling-buy-now/adding-best-offer-listing?id=4144)):
a buyer's offer expires after 24 hours without an answer, each counter offer expires after 96
hours, and each side can send at most 5 counter offers. Yardsale's haggling runs up to 6 rounds,
so eBay's limit of 5 counters is the one that applies on eBay.

## 6. Production

What can stop a new app from acting on real sellers' accounts right away:

| Hurdle | Status |
| --- | --- |
| Developer account approval | About 1 business day; see section 1. |
| Production keyset activation | Disabled until account deletion is subscribed or opted out; see section 2. |
| Production RuName | Its own RuName, with the legal contact address confirmed. |
| Application Growth Check | Needed only "to use restricted APIs in Production" or for higher call limits ([Growth Check](https://www.edp.ebay.com/grow/application-growth-check), [get started](https://developer.ebay.com/develop/guides-v2/get-started-with-ebay-apis)). We found nothing saying the Inventory, Account, or Trading Best Offer calls are restricted. How long a check takes isn't stated. |
| Call limits | Inventory API: 2 million calls a day by default (from eBay's [Sell API call limits](https://developer.ebay.com/develop/api/sell/api_call_limits), seen in search results; the page refuses automated fetches). Trading API: 5,000 a day by default (anecdotal, developer support on the forum). Plenty for a demo. Check remaining calls with the Developer Analytics API's `getRateLimits`. |
| User consent | No documented cap on how many sellers a new app may connect, and no review step found. Each seller must agree to every scope requested. |
| The seller's own account | The seller needs an eBay account that can already sell (unverified for the API, but listing calls are expected to fail otherwise), and business policies, which the Account API can create. |
| `PlaceOffer` in production | Restricted to eBay Partner Network affiliates, and "eBay is no longer accepting new applications" ([PlaceOffer](https://developer.ebay.com/DevZone/XML/docs/reference/ebay/PlaceOffer.html)). Real buyers make offers on ebay.com themselves; Yardsale can't place them. |

## Open questions

- Are weekend developer sign-ups reviewed before Monday?
- Does saving a RuName's Accept URL take effect at once?
- Do `GetBestOffers` and `RespondToBestOffer` work on Inventory API listings?
- Does sandbox.ebay.com show a Make Offer button in a desktop browser?
- Where on ebay.com does the buyer see the seller's counter offer message?
