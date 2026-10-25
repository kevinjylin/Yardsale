"""The ASI:One chat's screens, as Interactive Cards.

Every reply the Market sends in the chat is a short line of text and one card: the menu, the
sell and buy screens, a proposal, a live listing, search results, an item, an offer, a haggle,
a payment, the seller's listings, the buyer's orders, watches, and deals on other sites. Each
card has a picture and buttons, and every card but the menu has a Main menu button, so no
screen is a dead end. Typed commands still do everything the buttons do (commands.py), and the
text above each card names them, for clients that don't show cards and for ASI:One's planner.

The layout follows how ASI:One's web app draws cards (card_schema.py has its rules). In a row,
only buttons, pictures, inputs, and short text or badges sit side by side; anything else takes
the whole line. So "side by side" here means picture tiles, buttons two to a row, and two
fields to a row, and facts and steps go in boxes. The menu's two products are picture tiles.

Pictures are art from assets/cards in this public repository (YARDSALE_CARD_ART, empty to turn
them off), shown as short 3:1 banners, or a real photo at 16:9 where there is one: the closest
eBay sale of the same product for a listing, the listing's own photo for a deal on another site.
These photos are links to eBay's and the sites' own image servers; Yardsale never re-hosts them.

Card text can't be tapped, but a button can open a link: paying, a deal on another site, an
item agent's Agentverse profile. The same links stay in the text above the card.

Cards that only show things (the menu, carousels, a deal) stay tappable after a tap
(`resubmittable`); cards that act (approve, buy, pay, take down) lock after one tap, which is
ASI:One's default.

This module only lays cards out. What they say (prices, where they came from, the seller's
floor shown only to that seller) is decided in chat.py, which passes the words in.
"""

from __future__ import annotations

import os
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal, cast
from uuid import uuid4

from uagents_core.contrib.protocols.chat import MetadataContent

from yardsale import advice
from yardsale.avatars import profile_link
from yardsale.card_schema import (
    BadgeNode,
    ButtonAction,
    ButtonNode,
    CarouselBadge,
    CarouselCardPayload,
    CarouselItem,
    CtaAction,
    CustomCardPayload,
    DividerNode,
    GroupNode,
    HeadingNode,
    ImageNode,
    ImageSpec,
    InputNode,
    InputOption,
    ListItem,
    ListNode,
    SectionNode,
    TextNode,
    create_card_content,
)
from yardsale.models import HaggleOutcome, HaggleResult
from yardsale.pricing import usd
from yardsale.store import AGENTVERSE, Listing

# Card art is served from this public repository; YARDSALE_CARD_ART= (empty) turns it off.
ART_BASE = "https://raw.githubusercontent.com/kevinjylin/Yardsale/main/assets/cards/"
# Every name is assets/cards/<name>.jpg (1200x675), drawn by scripts/make_card_art.py.
ART = frozenset(
    {
        "menu",
        "sell",
        "buy",
        "pricing",
        "proposal",
        "live",
        "haggle",
        "deal",
        "pay",
        "paid",
        "listings",
        "orders",
        "deals",
        "watch",
        "craigslist",
        "ebay",
        "info",
        "warning",
        "campus",
        "item",
    }
)
DRAWER_WIDTH = 560  # px; ASI:One shows a card at most this wide (it clamps to 320-800)
BANNER = "3:1"  # card art, cropped to a strip: the drawing is centered
PHOTO = "16:9"  # an item's own photo
PER_ROW = 2  # buttons and fields side by side
MAX_IMAGE_URL = 400  # an outside photo link longer than this isn't shown
MAX_LINK = 2000  # a button's link longer than this isn't offered
MAX_TEXT = 400  # characters of one text element
CAROUSEL_ITEMS = 10
SLIDE_FROM = 3  # a carousel this long shows one item at a time
CRAIGSLIST_POSTING = "https://post.craigslist.org/"

Node = Any  # one element of a custom card's tree
Selection = dict[str, Any]
Action = tuple[str, Selection]
Variant = Literal["info", "success", "warning"]
Style = Literal["body", "muted", "emphasis"]
FieldKind = Literal["text", "number", "email", "select", "checkbox"]


def variant_of(value: str) -> Variant:
    """A badge color by name; anything unknown is info."""
    return cast(Variant, value) if value in ("info", "success", "warning") else "info"


@dataclass(frozen=True)
class Link:
    """A button that opens a page instead of messaging the Market."""

    label: str
    url: str


Button = Action | Link

MAIN_MENU: Action = ("Main menu", {"action": "menu"})
SELL: Action = ("Sell something", {"action": "sell"})
BUY: Action = ("Find a deal", {"action": "buy"})
LISTINGS: Action = ("My listings", {"action": "listings"})
ORDERS: Action = ("My orders", {"action": "orders"})
MEET: Action = ("Where to meet", {"action": "meet"})
WATCHES: Action = ("My watches", {"action": "watches"})

_MARKDOWN_LINK = re.compile(r"!?\[([^\]]*)\]\((https?://[^\s)]+)\)")


# --- Pictures ------------------------------------------------------------------------------------


def art_base() -> str:
    base = os.getenv("YARDSALE_CARD_ART", ART_BASE).strip()
    return base if base.startswith("https://") else ""


def art(name: str) -> str | None:
    """The public link to one card picture, or None when pictures are off."""
    if name not in ART:
        raise KeyError(f"unknown card art: {name}")
    base = art_base()
    return f"{base.rstrip('/')}/{name}.jpg" if base else None


def is_art(url: str) -> bool:
    base = art_base()
    return bool(base) and url.startswith(base.rstrip("/"))


def photo(url: str | None) -> str | None:
    """An outside photo (an eBay sale's, a deal's), if it's a short https link and pictures
    are on. The viewer's ASI:One loads it; Yardsale never fetches it."""
    if url and url.startswith("https://") and len(url) <= MAX_IMAGE_URL and art_base():
        return url
    return None


def sale_photo(listing: Listing) -> str | None:
    """A photo of the same product from the eBay sales behind the price, if any."""
    if listing.appraisal is None:
        return None
    matches = sorted(listing.appraisal.matches, key=lambda match: not match.same_product)
    return next((url for m in matches if (url := photo(m.comp.image_url))), None)


def listing_picture(listing: Listing) -> str | None:
    return sale_photo(listing) or art("item")


def picture(src: str | None, alt: str, ratio: str | None = None) -> list[Node]:
    """A picture across the card: card art as a short banner, a photo at 16:9."""
    if not src:
        return []
    shape = ratio or (BANNER if is_art(src) else PHOTO)
    return [ImageNode(src=src, alt=plain(alt)[:80], aspect_ratio=shape)]


def link(url: str | None) -> str | None:
    """A link a button may open: http or https, and not absurdly long."""
    if url and re.match(r"^https?://\S+$", url) and len(url) <= MAX_LINK:
        return url
    return None


# --- Pieces --------------------------------------------------------------------------------------


def plain(value: str) -> str:
    """Card text can't be tapped, so markdown links show as their words."""
    value = _MARKDOWN_LINK.sub(lambda m: m.group(1), value)
    value = " ".join(value.split())
    return value if len(value) <= MAX_TEXT else value[: MAX_TEXT - 1].rstrip() + "…"


def text(value: str, style: Style = "body") -> TextNode:
    return TextNode(value=plain(value), style=style)


def muted(value: str) -> TextNode:
    return text(value, "muted")


def heading(value: str, level: Literal[1, 2, 3] = 3) -> HeadingNode:
    return HeadingNode(value=plain(value)[:90], level=level)


def badge(label: str, variant: str = "info") -> BadgeNode:
    return BadgeNode(label=label[:40], variant=variant_of(variant))


def row(children: Sequence[Node], gap: int = 8) -> GroupNode:
    return GroupNode(direction="row", gap=gap, children=list(children))


def column(children: Sequence[Node], gap: int | None = None) -> GroupNode:
    return GroupNode(direction="column", gap=gap, children=list(children))


def box(children: Sequence[Node], gap: int = 6) -> GroupNode:
    """A shaded box, for facts and steps."""
    return GroupNode(direction="column", gap=gap, style="inset", children=list(children))


def badges(found: Sequence[BadgeNode]) -> list[Node]:
    """Badges in one row."""
    return [row(found, gap=6)] if found else []


def button(label: str, selection: Selection, primary: bool = False) -> ButtonNode:
    return ButtonNode(label=label[:40], primary=primary, action=ButtonAction(selection=selection))


def link_button(label: str, url: str, primary: bool = False) -> ButtonNode:
    return ButtonNode(label=label[:40], primary=primary, action=ButtonAction(redirect=url))


def tile(label: str, selection: Selection, art_name: str, primary: bool = False) -> ButtonNode:
    """A picture tile: the drawing on top and the label under it. A plain button when
    pictures are off."""
    src = art(art_name)
    if not src:
        return button(label, selection, primary)
    return ButtonNode(
        label=label[:40],
        image=ImageSpec(src=src, alt=label[:80], aspect_ratio=PHOTO),
        primary=primary,
        action=ButtonAction(selection=selection),
    )


def side_by_side(nodes: Sequence[Node], per_row: int = PER_ROW, gap: int = 8) -> list[Node]:
    """Buttons, tiles, or fields, a few to a row."""
    return [row(nodes[i : i + per_row], gap) for i in range(0, len(nodes), per_row)]


def as_button(action: Button, primary: bool) -> ButtonNode | None:
    if isinstance(action, Link):
        url = link(action.url)
        return link_button(action.label, url, primary) if url else None
    label, selection = action
    return button(label, selection, primary)


def buttons(actions: Sequence[Button], primary: bool = True) -> list[Node]:
    """Buttons two to a row, the first one primary unless `primary` is False. A link that
    can't be opened is left out."""
    shown = [found for found in (as_button(a, False) for a in actions) if found is not None]
    if primary and shown:
        shown[0] = shown[0].model_copy(update={"primary": True})
    return side_by_side(shown)


def with_menu(actions: Sequence[Button]) -> list[Button]:
    """The actions, then Main menu unless one of them already opens it."""
    found = list(actions)
    if all(isinstance(a, Link) or a[1] != MAIN_MENU[1] for a in found):
        found.append(MAIN_MENU)
    return found


def facts(pairs: Sequence[tuple[str, str]]) -> list[Node]:
    """Label-and-value pairs in a box."""
    shown = [row([muted(label), text(value)]) for label, value in pairs if value]
    return [box(shown)] if shown else []


def steps(lines: Sequence[str]) -> list[Node]:
    """Lines in a box, one under the other."""
    return [box([text(line) for line in lines])] if lines else []


def custom(
    title: str, children: list[Node], subtitle: str | None = None, *, resubmittable: bool = False
) -> MetadataContent:
    root = SectionNode(
        title=plain(title)[:90],
        subtitle=plain(subtitle)[:160] if subtitle else None,
        children=children,
    )
    return create_card_content(
        CustomCardPayload(root=root),
        card_id=uuid4(),
        preferred_drawer_width_px=DRAWER_WIDTH,
        resubmittable=resubmittable,
    )


def screen(
    title: str,
    art_name: str,
    lead: list[Node],
    body: list[Node] | None = None,
    actions: Sequence[Button] = (),
    *,
    picture_url: str | None = None,
    subtitle: str | None = None,
    resubmittable: bool = False,
) -> MetadataContent:
    """The usual card: a picture, the lead lines, the body, then buttons two to a row, ending
    in Main menu. The first action is the primary button."""
    children = [*picture(picture_url or art(art_name), title), *lead, *(body or [])]
    children += buttons(with_menu(actions), primary=bool(actions))
    return custom(title, children, subtitle, resubmittable=resubmittable)


def carousel(
    title: str, subtitle: str | None, items: list[CarouselItem], *, menu: bool = True
) -> MetadataContent:
    """Items with a photo each, and Main menu as a button under them. ASI:One stacks a
    carousel's items one under another, so from SLIDE_FROM items on it shows one at a time
    with arrows and "1 / 6" instead. Carousels only show things, so they stay tappable: open
    one item, then another."""
    shown = items[:CAROUSEL_ITEMS]
    return create_card_content(
        CarouselCardPayload(
            title=plain(title)[:90],
            subtitle=plain(subtitle)[:160] if subtitle else None,
            style="slide" if len(shown) >= SLIDE_FROM else None,
            items=shown,
            root_cta=cta(MAIN_MENU) if menu else None,
        ),
        card_id=uuid4(),
        preferred_drawer_width_px=DRAWER_WIDTH,
        resubmittable=True,
    )


def cta(action: Action, primary: bool = False) -> CtaAction:
    return CtaAction(label=action[0][:40], selection=action[1], primary=primary)


def item(
    id: str,
    title: str,
    action: Action,
    *,
    image: str | None = None,
    subtitle: str | None = None,
    secondary: str | None = None,
    badges: Sequence[tuple[str, str]] = (),
) -> CarouselItem:
    return CarouselItem(
        id=id[:80],
        image=image,
        title=plain(title)[:80],
        subtitle=plain(subtitle)[:100] if subtitle else None,
        secondary_text=plain(secondary)[:140] if secondary else None,
        badges=[
            CarouselBadge(label=label[:40], variant=variant_of(variant))
            for label, variant in badges
        ]
        or None,
        primary_cta=cta(action, primary=True),
    )


def detail(
    title: str, hero: str | None, pairs: Sequence[tuple[str, str]], actions: Sequence[Action]
) -> MetadataContent:
    """One thing up close: its photo, its facts in a box, and buttons two to a row. ASI:One's
    own detail card puts every button in one line, which runs off a phone's screen with more
    than three, so this is a custom card."""
    return screen(title, "item", [], facts(pairs), actions, picture_url=hero)


def field(
    name: str,
    label: str,
    kind: FieldKind = "number",
    placeholder: str | None = None,
    required: bool = False,
    options: Sequence[tuple[str, str]] = (),
) -> InputNode:
    return InputNode(
        name=name,
        kind=kind,
        label=label,
        required=required,
        placeholder=placeholder,
        options=[InputOption(value=value, label=text) for value, text in options] or None,
    )


def first_sentence(value: str) -> str:
    """The first sentence of a message, for a notice card's heading."""
    flat = plain(value)
    found = re.match(r"(.+?[.!?])(?:\s|$)", flat)
    sentence = found.group(1) if found else flat
    return sentence if len(sentence) <= 120 else sentence[:119].rstrip() + "…"


_TROUBLE = re.compile(
    r"\b(sorry|couldn't|can't|isn't|aren't|wasn't|didn't|wouldn't|no longer|not open|expired|"
    r"went wrong|first[,.:]|pick a number|which one)\b",
    re.IGNORECASE,
)


def notice(text_shown: str, actions: Sequence[Button] = (), title: str = "") -> MetadataContent:
    """The card for a short message: its first sentence under a picture, then what to do next.
    The whole message is in the text above, so the card doesn't repeat it."""
    trouble = bool(_TROUBLE.search(text_shown))
    lead: list[Node] = badges([badge("Needs a look", "warning")] if trouble else [])
    lead.append(text(first_sentence(text_shown), "emphasis"))
    return screen(title or "Yardsale", "warning" if trouble else "info", lead, actions=actions)


SOMETHING_ELSE: Action = ("Something else", MAIN_MENU[1])


def question(asked: str, actions: Sequence[Action]) -> MetadataContent:
    """A short clarifying question, with a button for each likely meaning and Something else."""
    return screen(
        "Did you mean…", "info", [text(asked, "emphasis")], actions=[*actions, SOMETHING_ELSE]
    )


def buyers(about: str, lines: Sequence[str], actions: Sequence[Action]) -> MetadataContent:
    """A seller's buyers: each listing's Craigslist post and who's emailing about it."""
    return screen("Your buyers", "craigslist", [text(about)], steps(lines), [*actions, LISTINGS])


# --- The menu, selling, buying -------------------------------------------------------------------


def menu(
    *,
    live: int = 0,
    waiting: int = 0,
    to_pay: int = 0,
    orders: int = 0,
    watching: int = 0,
    ebay: bool = False,
    school: str = "",
    meet: bool = False,
) -> MetadataContent:
    """The home screen: the two products as picture tiles side by side, then everything else
    as buttons two to a row, each with its status in the label. A button that needs the
    person (photos to approve, an order to pay) is the highlighted one. `meet` adds the campus
    meeting spots, for a conversation that has given a school."""
    flags = [badge(f"{school} students only")] if school else []
    children: list[Node] = [
        text(
            "Sell your stuff or find a deal. Agents do the pricing, the listing, and the haggling."
        ),
        *badges(flags),
        row([tile("Sell something", SELL[1], "sell"), tile("Find a deal", BUY[1], "buy")], gap=12),
        muted(
            "Pricing is free, from eBay sales and what similar ones ask nearby. Deals come from "
            "Yardsale, eBay, Craigslist, OfferUp, and Facebook Marketplace."
        ),
    ]
    more: list[ButtonNode] = []
    if waiting:
        more.append(button(f"Approve · {waiting} waiting", {"action": "review"}, primary=True))
    more.append(button(f"My listings · {live} for sale" if live else "My listings", LISTINGS[1]))
    orders_label = (
        f"My orders · {to_pay} to pay"
        if to_pay
        else f"My orders · {orders}"
        if orders
        else "My orders"
    )
    more.append(button(orders_label, ORDERS[1], primary=bool(to_pay)))
    more.append(button(f"Watches · {watching} running" if watching else "Watches", WATCHES[1]))
    more.append(button("Haggle on Craigslist", {"action": "negotiate_help"}))
    if ebay:
        more.append(button("Your eBay", {"action": "ebay_connect"}))
    if meet:
        more.append(button(*MEET))
    children += [DividerNode(), *side_by_side(more)]
    children.append(
        muted(
            "Claude reads your photos and ASI:One reads what you type. Buyers never see your "
            "lowest price, and sellers never see your max."
        )
    )
    return custom(
        "Yardsale", children, "Your agents for buying and selling used stuff", resubmittable=True
    )


def sell(waiting: int = 0) -> MetadataContent:
    lead: list[Node] = [
        *badges([badge("Nothing is listed until you approve")]),
        text("Attach a photo of what you're selling, right here in the chat.", "emphasis"),
    ]
    body = steps(
        [
            "1. Attach photos, up to 8 at once, one item each.",
            '2. Add your minimum if you like: "sell this, at least $40".',
            "3. Claude identifies it. Recent eBay sales and what similar ones ask nearby set the "
            "price, and you see the sales behind it.",
            "4. Approve it, and the item gets its own agent that haggles with every buyer and "
            "never goes below your minimum.",
        ]
    )
    actions: list[Button] = [LISTINGS]
    if waiting:
        actions.insert(0, ("Review what's waiting", {"action": "review"}))
    return screen("Sell something", "sell", lead, body, actions)


def buy(query: str = "", budget_cents: int | None = None) -> MetadataContent:
    """A form: what they want and their max, side by side."""
    lead: list[Node] = [
        text("Tell me what you want and the most you'd pay.", "emphasis"),
        muted(
            "I check Yardsale first, then eBay, Craigslist, OfferUp, and Facebook Marketplace, "
            "and score every listing against what it really sells for."
        ),
    ]
    body = side_by_side(
        [
            field(
                "query", "What you want", "text", placeholder=query or "mini fridge", required=True
            ),
            field(
                "max",
                "Your max ($)",
                placeholder=f"{budget_cents / 100:.0f}" if budget_cents else "60",
            ),
        ]
    )
    body.append(muted("Sellers never see your max."))
    return screen("Find a deal", "buy", lead, body, [("Find it", {"action": "find"})])


PRICING_STEPS = (
    "Identifier: Claude reads the photo",
    "Researcher: eBay sales of the same thing, last 90 days",
    "Market Scout: what similar ones ask nearby right now",
    "Appraiser: Claude keeps the same-product sales, and code sets the price",
)


def pricing(count: int = 1) -> MetadataContent:
    """While a photo is priced: which agents are working on it."""
    got = "your photo" if count == 1 else f"your {count} photos"
    lead: list[Node] = [
        *badges([badge("Agents at work")]),
        text(f"Got {got}. Pricing takes about a minute.", "emphasis"),
    ]
    body = steps([f"✓ Got {got}", *(f"• {step}" for step in PRICING_STEPS)])
    if count > 1:
        body.append(muted("Four at a time. Each listing comes here as it's ready."))
    return screen(
        "Pricing your stuff", "pricing", lead, body, [("Check on it", {"action": "status"})]
    )


def searching(sites: str) -> MetadataContent:
    lead: list[Node] = [*badges([badge("Agents at work")]), text(f"Searching {sites}.", "emphasis")]
    body = [muted("Each listing is scored against what the thing really sells for on eBay.")]
    return screen("Finding deals", "deals", lead, body)


def working() -> MetadataContent:
    return screen("Working on it", "pricing", badges([badge("Agents at work")]))


# --- Selling: proposals, listings ----------------------------------------------------------------


def proposal(
    listing: Listing,
    n: int | None,
    *,
    asking: str,
    lowest: str,
    basis: str,
    market: Sequence[str],
    feedback: str,
) -> MetadataContent:
    """A suggested listing, waiting for the seller: the prices and where they came from."""
    ids = {"item_id": listing.item_id}
    found: list[BadgeNode] = []
    appraisal = listing.appraisal
    if appraisal is not None and appraisal.asking_cents is not None:
        used = len(appraisal.matches)
        found.append(
            badge(
                f"{used} eBay sale{'s' if used != 1 else ''}, {appraisal.confidence} confidence",
                "success" if appraisal.confidence == "high" else "info",
            )
        )
    else:
        found.append(badge("Your price"))
    if listing.item is not None:
        found.append(badge(f"{listing.item.condition} condition"))
    lead: list[Node] = [*badges(found), text(asking, "emphasis"), text(lowest)]
    where = [muted(line) for line in [basis, *market] if line]
    body: list[Node] = [box(where)] if where else []
    if feedback:
        verdict = (
            badge("Price looks right", "success")
            if advice.IN_LINE in feedback
            else badge("Check your price", "warning")
        )
        body.append(box([*badges([verdict]), text(feedback)]))
    number = f"#{n}: " if n is not None else ""
    return screen(
        f"{number}List your {listing.title}?",
        "proposal",
        lead,
        body,
        [
            ("Approve & list", {"action": "approve", **ids}),
            ("Change prices", {"action": "edit", **ids}),
            ("Cancel", {"action": "cancel", **ids}),
        ],
        picture_url=listing_picture(listing),
    )


def prices_form(listing: Listing) -> MetadataContent:
    """The seller's own prices: asking and lowest, side by side."""
    lead: list[Node] = [
        text(f"Now asking {usd(listing.asking_cents)}, lowest {usd(listing.floor_cents)}."),
        muted("Leave one empty to keep it. Buyers never see your lowest price."),
    ]
    body = side_by_side(
        [
            field("asking", "Asking ($)", placeholder=f"{listing.asking_cents / 100:.0f}"),
            field("lowest", "Lowest ($)", placeholder=f"{listing.floor_cents / 100:.0f}"),
        ]
    )
    return screen(
        f"Your prices for the {listing.title}",
        "proposal",
        lead,
        body,
        [
            ("Save prices", {"action": "rules", "item_id": listing.item_id}),
            ("Back", {"action": "review", "item_id": listing.item_id}),
        ],
        picture_url=listing_picture(listing),
    )


def price_needed(n: int | None, title: str, hint: str) -> MetadataContent:
    """A photo identified, but with no sales close enough: the seller's price makes it a
    proposal."""
    ids: Selection = {"n": n} if n is not None else {}
    lead: list[Node] = [
        *badges([badge("Needs your price", "warning")]),
        text(f"This looks like {title}.", "emphasis"),
        muted("No recent eBay sales were close enough to suggest a price."),
    ]
    body: list[Node] = [muted(hint)] if hint else []
    body += side_by_side(
        [
            field("asking", "Asking ($)", placeholder="40", required=True),
            field("lowest", "Lowest ($)", placeholder="30"),
        ]
    )
    return screen(
        f"#{n}: Set a price" if n is not None else "Set a price",
        "proposal",
        lead,
        body,
        [("Save my price", {"action": "set_price", **ids}), ("Cancel", {"action": "drop", **ids})],
    )


def agent_name(listing: Listing) -> str:
    if listing.host == AGENTVERSE and listing.handle:
        return f"@{listing.handle} on Agentverse"
    address = listing.address or ""
    short = f"{address[:14]}…{address[-6:]}" if len(address) > 24 else address
    return f"{short} on Agentverse" if listing.host == AGENTVERSE else short


def agent_link(listing: Listing) -> list[Button]:
    """A button to the item agent's Agentverse profile, if it's hosted there."""
    if listing.host == AGENTVERSE and listing.address:
        return [Link("See its agent", profile_link(listing.address))]
    return []


def listed(listing: Listing, notes: Sequence[str] = (), craigslist: bool = True) -> MetadataContent:
    """A listing just went live: its agent, and the Craigslist post offer."""
    ids = {"item_id": listing.item_id}
    found = [badge("For sale", "success")]
    if listing.host == AGENTVERSE:
        found.append(badge("Own agent on Agentverse"))
    lead: list[Node] = [
        *badges(found),
        text(f"For sale at {usd(listing.asking_cents)}.", "emphasis"),
        text(
            "Its own agent answers buyers for you. It haggles with reasons from the market and "
            f"never goes below your {usd(listing.floor_cents)} minimum, which buyers never see."
        ),
    ]
    body = facts([("Its agent", agent_name(listing)), ("Buyers find it on", "ASI:One, @yardsale")])
    body += [muted(note) for note in notes]
    actions: list[Button] = []
    if craigslist:
        body.append(
            text(
                "Want it on Craigslist too? I'll write the post, and buyers' emails go straight to "
                "its agent."
            )
        )
        actions += [
            ("Write my Craigslist post", {"action": "craigslist", **ids}),
            ("Not now", {"action": "no_craigslist", **ids}),
        ]
    actions += [*agent_link(listing), LISTINGS, ("Sell another", SELL[1])]
    return screen(
        f"Your {listing.title} is live",
        "live",
        lead,
        body,
        actions,
        picture_url=listing_picture(listing),
    )


def craigslist_post(listing: Listing, title: str, email: str | None) -> MetadataContent:
    lead: list[Node] = [
        *badges([badge("Ready to paste", "success")]),
        text("Copy the post in the message above into Craigslist.", "emphasis"),
    ]
    body = facts(
        [
            ("Title", f"{title} (use it exactly)"),
            ("Price", f"${-(-listing.asking_cents // 100)}"),
            ("Contact email", email or "Email replies are off on this server"),
        ]
    )
    body.append(
        muted(
            "Craigslist doesn't allow automatic posting, so you paste it. Buyers' emails reach "
            "your item's agent, which answers them with the same rules."
        )
    )
    return screen(
        "Your Craigslist post",
        "craigslist",
        lead,
        body,
        [Link("Open Craigslist", CRAIGSLIST_POSTING), LISTINGS],
    )


COMPS_SHOWN = 6  # eBay sales in a proposal's carousel


def comps(listing: Listing) -> MetadataContent | None:
    """The eBay sales behind a proposal's price, with their photos. It goes in the same message
    as the proposal card, which has the Approve and Main menu buttons, so it has neither."""
    appraisal = listing.appraisal
    if appraisal is None or not appraisal.matches:
        return None
    items = []
    for n, match in enumerate(appraisal.matches[:COMPS_SHOWN]):
        sale = match.comp
        items.append(
            item(
                f"{listing.item_id}-{n}",
                sale.title,
                ("Why this sale?", {"action": "comp", "item_id": listing.item_id, "n": n}),
                image=photo(sale.image_url),
                subtitle=f"{usd(sale.price_cents)}, sold {sale.sold_at}",
                secondary=match.reason or None,
                badges=[("Same product", "success") if match.same_product else ("Similar", "info")],
            )
        )
    return carousel("The eBay sales behind this price", appraisal.basis or None, items, menu=False)


def waiting(entries: Sequence[tuple[int, str, Listing | None]], head: str) -> MetadataContent:
    """A seller's numbered photos still waiting: tap one to review it, or approve them all.
    It stays tappable, so the seller can open one, then the next."""
    list_items = []
    for n, line, listing in entries:
        selection: Selection = (
            {"action": "review", "item_id": listing.item_id}
            if listing is not None
            else {"action": "price_form", "n": n}
        )
        list_items.append(
            ListItem(
                children=[text(line), muted("Tap to review" if listing else "Tap to set a price")],
                action=ButtonAction(selection=selection),
            )
        )
    lead: list[Node] = [text(head, "emphasis"), muted("Numbers stay the same as you go.")]
    body: list[Node] = [ListNode(items=list_items)] if list_items else []
    actions: list[Button] = []
    if any(listing is not None for _, _, listing in entries):
        actions.append(("Approve all", {"action": "approve_all"}))
    return screen(
        "Waiting for you", "proposal", lead, body, [*actions, LISTINGS], resubmittable=True
    )


def batch_done(lines: Sequence[str], any_waiting: bool) -> MetadataContent:
    lead: list[Node] = [*badges([badge("All priced", "success")]), text(lines[0], "emphasis")]
    actions: list[Button] = [("Approve all", {"action": "approve_all"})] if any_waiting else []
    return screen("Your photos", "proposal", lead, steps(lines[1:]), [*actions, LISTINGS])


def priced_photo_failed(message: str) -> MetadataContent:
    lead: list[Node] = [
        *badges([badge("Couldn't price it", "warning")]),
        text(first_sentence(message)),
    ]
    return screen(
        "That photo didn't work", "warning", lead, actions=[("How selling works", SELL[1])]
    )


def my_listings(entries: Sequence[tuple[Listing, str, str, Action]]) -> MetadataContent:
    """Everything a seller has: each listing with its state and what to do with it."""
    items = [
        item(
            listing.item_id,
            listing.title,
            action,
            image=listing_picture(listing),
            subtitle=f"{usd(listing.asking_cents)}, {status}",
            badges=[(status.split(",")[0].capitalize(), variant)],
        )
        for listing, status, variant, action in entries
    ]
    items = items[: CAROUSEL_ITEMS - 1]
    items.append(
        item(
            "sell-another",
            "Sell something else",
            ("Start selling", SELL[1]),
            image=art("sell"),
            subtitle="Attach a photo",
        )
    )
    return carousel("Your listings", "Tap one to manage it", items)


def manage(
    listing: Listing, pairs: Sequence[tuple[str, str]], actions: Sequence[Action]
) -> MetadataContent:
    return detail(listing.title, listing_picture(listing), pairs, [*actions, LISTINGS])


def confirm_unlist(listing: Listing) -> MetadataContent:
    ids = {"item_id": listing.item_id}
    gone = (
        "Its agent on Agentverse is deleted."
        if listing.host == AGENTVERSE
        else "Its agent turns down new offers."
    )
    lead: list[Node] = [
        *badges([badge("Can't be undone", "warning")]),
        text(f"Take down your {listing.title}?", "emphasis"),
        muted(f"It stops being for sale on Yardsale and eBay. {gone}"),
    ]
    return screen(
        "Take it down?",
        "warning",
        lead,
        actions=[
            ("Yes, take it down", {"action": "unlist_yes", **ids}),
            ("Keep it", {"action": "manage", **ids}),
        ],
        picture_url=listing_picture(listing),
    )


# --- Buying --------------------------------------------------------------------------------------


def results(found: Sequence[Listing], owner: str, query: str) -> MetadataContent:
    items = []
    for listing in found[: CAROUSEL_ITEMS - 1]:
        badges = [(listing.item.condition, "info")] if listing.item else []
        if listing.owner == owner:
            badges.append(("Yours", "warning"))
        items.append(
            item(
                listing.item_id,
                listing.title,
                ("View", {"action": "pick", "item_id": listing.item_id}),
                image=listing_picture(listing),
                subtitle=usd(listing.asking_cents),
                secondary=listing.market_line or None,
                badges=badges,
            )
        )
    if any(listing.owner != owner for listing in found):
        items.append(
            item(
                "haggle",
                "Haggle for me",
                ("Set my max", {"action": "haggle_form"}),
                image=art("haggle"),
                subtitle="My agent haggles with all of these at once",
            )
        )
    about = f'For "{query}" on Yardsale' if query else "On Yardsale"
    return carousel("For sale", about, items)


def item_detail(listing: Listing, mine: bool) -> MetadataContent:
    ids = {"item_id": listing.item_id}
    pairs: list[tuple[str, str]] = [("Asking price", usd(listing.asking_cents))]
    if listing.item is not None:
        pairs.append(("Condition", listing.item.condition))
        if listing.item.details:
            pairs.append(("Details", "; ".join(listing.item.details)))
    if listing.market_line:
        pairs.append(("The market", listing.market_line))
    if sale_photo(listing):
        pairs.append(("Photo", "A recent eBay sale of the same product"))
    pairs.append(("Its agent", agent_name(listing)))
    if mine:
        pairs.append(("Yours", "This is your own listing."))
        return detail(listing.title, listing_picture(listing), pairs, [LISTINGS])
    return detail(
        listing.title,
        listing_picture(listing),
        pairs,
        [
            (f"Buy for {usd(listing.asking_cents)}", {"action": "buy_now", **ids}),
            ("Make an offer", {"action": "offer_form", **ids}),
            ("Haggle for me", {"action": "haggle_form", **ids}),
            ("Back to results", {"action": "results"}),
        ],
    )


def offer_form(listing: Listing) -> MetadataContent:
    ids = {"item_id": listing.item_id}
    lead: list[Node] = [
        text(f"Asking {usd(listing.asking_cents)}.", "emphasis"),
        muted("The seller's agent answers right away. It never shows the seller's lowest price."),
    ]
    body: list[Node] = [
        field(
            "amount",
            "Your offer ($)",
            placeholder=f"{listing.asking_cents * 8 // 1000:.0f}",
            required=True,
        )
    ]
    return screen(
        f"Make an offer on the {listing.title}",
        "haggle",
        lead,
        body,
        [("Send offer", {"action": "offer", **ids}), ("Back", {"action": "pick", **ids})],
        picture_url=listing_picture(listing),
    )


def max_form(count: int, item_id: str = "", budget_cents: int | None = None) -> MetadataContent:
    sellers = "the seller's agent" if count == 1 else f"{count} seller agents at once"
    lead: list[Node] = [
        text("What's the most you'd pay?", "emphasis"),
        muted(
            f"My buyer agent haggles with {sellers}. It never offers more than your max, and "
            "never tells the sellers."
        ),
    ]
    body: list[Node] = [
        field(
            "max",
            "Your max ($)",
            placeholder=f"{budget_cents / 100:.0f}" if budget_cents else "250",
            required=True,
        )
    ]
    selection: Selection = {"action": "haggle_max"}
    if item_id:
        selection["item_id"] = item_id
    return screen("Haggle for me", "haggle", lead, body, [("Start haggling", selection)])


def counter(listing: Listing, cents: int, note: str, agreed: bool) -> MetadataContent:
    ids = {"item_id": listing.item_id}
    lead: list[Node] = [
        *badges([badge("Agreed", "success") if agreed else badge("Counter-offer", "warning")]),
        text(f"{'Agreed price' if agreed else 'Counter-offer'}: {usd(cents)}", "emphasis"),
        muted(f"Asking price: {usd(listing.asking_cents)}. Good for 24 hours."),
    ]
    body: list[Node] = [box([muted("The seller's agent"), text(note)])] if note else []
    actions: list[Button] = [
        (f"Accept {usd(cents)}", {"action": "accept", **ids, "cents": cents}),
    ]
    if not agreed:
        actions.append(("Counter", {"action": "offer_form", **ids}))
    actions.append(("No thanks", {"action": "decline", **ids}))
    return screen(
        f"Buy the {listing.title} for {usd(cents)}?",
        "deal" if agreed else "haggle",
        lead,
        body,
        actions,
        picture_url=listing_picture(listing),
    )


STATUS_TEXT = {
    "agreed": "agreed",
    "counter": "their price is within your max",
    "over_max": "wouldn't come down to your max",
    "unavailable": "no longer for sale",
    "no_answer": "its agent didn't answer",
}


def _rounds(outcome: HaggleOutcome) -> str:
    """Every round on one line: "you $221 → $270, you $250 → agreed"."""
    return ", ".join(
        f"you {usd(r.offer_cents)} → " + ("agreed" if r.agreed else usd(r.reply_cents))
        for r in outcome.rounds
    )


def _outcome(n: int, outcome: HaggleOutcome, best: bool) -> GroupNode:
    """One seller's haggle, in its own box."""
    found: list[BadgeNode] = []
    if outcome.price_cents is not None:
        label = "Agreed" if outcome.status == "agreed" else "Within your max"
        found.append(badge(f"{label}: {usd(outcome.price_cents)}", "success"))
        if best:
            found.append(badge("Best deal"))
    else:
        status = STATUS_TEXT.get(outcome.status, outcome.status)
        found.append(badge(status[:1].upper() + status[1:], "warning"))
    lines: list[Node] = [heading(f"{n}. {outcome.title}"), *badges(found)]
    rounds = _rounds(outcome)
    lines.append(
        muted(f"Asking {usd(outcome.asking_cents)}" + (f". Rounds: {rounds}" if rounds else ""))
    )
    if outcome.price_cents is not None:
        lines.append(
            button(
                f"Buy for {usd(outcome.price_cents)}",
                {"action": "accept", "item_id": outcome.item_id, "cents": outcome.price_cents},
                primary=best,
            )
        )
    return box(lines)


def haggle(result: HaggleResult) -> MetadataContent | None:
    """Every round with every seller's agent, and a Buy button for each price within the max."""
    if not result.outcomes:
        return None
    children: list[Node] = [
        *picture(art("haggle"), "Haggling"),
        *(
            _outcome(n, outcome, outcome.item_id == result.best_item_id)
            for n, outcome in enumerate(result.outcomes, 1)
        ),
    ]
    children += buttons(
        [("Try a higher max", {"action": "haggle_form"}), BUY, MAIN_MENU], primary=False
    )
    count = len(result.outcomes)
    return custom(
        f"Haggled with {count} seller{'s' if count != 1 else ''} at once",
        children,
        f"Your max ({usd(result.max_cents)}) stayed private, and so did their lowest prices.",
    )


def payment(
    deal_id: str,
    price_cents: int,
    title: str,
    provider: str,
    image: str | None,
    meet: str = "",
    url: str | None = None,
) -> MetadataContent:
    """A deal to pay: the Pay button opens the payment link."""
    pay = link(url)
    lead: list[Node] = [
        *badges([badge("Test mode: no real money", "warning")]),
        text(f"{usd(price_cents)} for the {title}.", "emphasis"),
        text(
            "Tap Pay to open the payment page, then tap I've paid."
            if pay
            else "Open the payment link in the message above, then tap I've paid."
        ),
    ]
    body = facts(
        [
            ("Order", deal_id),
            ("Pay with", provider),
            ("Pay within", "24 hours"),
            ("Meet at", meet),
        ]
    )
    actions: list[Button] = [Link(f"Pay {usd(price_cents)}", pay)] if pay else []
    actions += [("I've paid", {"action": "paid", "deal_id": deal_id}), ORDERS]
    if meet:
        actions.append(MEET)
    return screen(
        f"Pay {usd(price_cents)} for the {title}",
        "pay",
        lead,
        body,
        actions,
        picture_url=image or art("pay"),
    )


def meetup(school: str, places: Sequence[tuple[str, str, str]]) -> MetadataContent:
    """Where two students at one school can hand an item over: the best spot first, then a
    couple of others. `places` are (name with hours, kind, map link). Tapping a spot opens its
    map."""
    lead: list[Node] = [
        *badges([badge("On campus")]),
        text(f"Public spots at {school} to hand an item over, best first.", "emphasis"),
    ]
    spots = []
    for n, (name, kind, map_url) in enumerate(places, 1):
        lines: list[Node] = [text(f"{n}. {name}")]
        if kind:
            lines.append(muted(kind.capitalize()))
        opens = link(map_url)
        spots.append(
            ListItem(children=lines, action=ButtonAction(redirect=opens) if opens else None)
        )
    body: list[Node] = [ListNode(items=spots)] if spots else []
    body.append(
        muted(
            "Tap a spot for its map. Agree a time in the chat; nobody has to say where they live."
        )
    )
    return screen("Where to meet", "campus", lead, body, [BUY, ORDERS], resubmittable=True)


def payment_status(
    title: str, price_cents: int, deal_id: str, status: str, paid: bool
) -> MetadataContent:
    lead: list[Node] = [
        *badges([badge("Paid", "success") if paid else badge(status, "warning")]),
        text(f"{usd(price_cents)} for the {title}.", "emphasis"),
        muted(f"Order {deal_id}"),
    ]
    actions: list[Button] = (
        [ORDERS, BUY] if paid else [("Check again", {"action": "paid", "deal_id": deal_id}), ORDERS]
    )
    return screen(
        "Payment received" if paid else "Payment", "paid" if paid else "pay", lead, actions=actions
    )


def orders(entries: Sequence[tuple[str, str, int, str, str, str | None]]) -> MetadataContent:
    """The buyer's orders: (deal ID, title, price, status, badge variant, picture)."""
    items = [
        item(
            deal_id,
            title,
            (
                "Pay or check" if variant == "warning" else "Details",
                {"action": "paid", "deal_id": deal_id},
            ),
            image=image or art("orders"),
            subtitle=f"{usd(price)}, {status}",
            badges=[(status.split(",")[0].capitalize(), variant)],
        )
        for deal_id, title, price, status, variant, image in entries
    ]
    items = items[: CAROUSEL_ITEMS - 1]
    items.append(
        item("find", "Find another deal", BUY, image=art("buy"), subtitle="Every site at once")
    )
    return carousel("Your orders", "Tap one to pay or check it", items)


def watches(entries: Sequence[str], news: int, sites: str) -> MetadataContent:
    """Standing searches, each with its own Stop button. Numbers shift once one stops, so the
    card locks after a tap, and the reply shows the new list."""
    lead: list[Node] = [
        *badges([badge(f"{news} new", "success")] if news else []),
        text(f"I check {sites} every 6 hours and tell you what's new when you message me."),
    ]
    body: list[Node] = steps([f"{n}. {line}" for n, line in enumerate(entries, 1)])
    stops = [
        button(f"Stop #{n}", {"action": "unwatch", "n": n}) for n in range(1, len(entries) + 1)
    ]
    body += side_by_side(stops)
    if not entries:
        body.append(
            muted(
                'Search for something, then tap "Watch for more", or say "watch for a '
                'road bike under $300".'
            )
        )
    return screen("Your watches", "watch", lead, body, [BUY])


def deals(found: Sequence[dict[str, Any]], subtitle: str) -> MetadataContent | None:
    """The best deals on other sites: tap one for its link."""
    if not found:
        return None
    items = []
    for n, deal in enumerate(found[: CAROUSEL_ITEMS - 1]):
        badges = [(str(deal["site"]), "info")]
        if deal.get("flag"):
            badges.append((str(deal["flag"]), "warning"))
        items.append(
            item(
                str(deal["id"]),
                str(deal["title"]),
                ("Get the link", {"action": "deal", "n": n, "id": str(deal["id"])[:80]}),
                image=photo(deal.get("image")),
                subtitle=str(deal["price"]),
                secondary=str(deal.get("why") or deal.get("location") or "") or None,
                badges=badges,
            )
        )
    items.append(
        item(
            "watch",
            "Watch for more",
            ("Watch for more", {"action": "watch"}),
            image=art("watch"),
            subtitle="I'll keep checking for a week",
        )
    )
    return carousel("On other sites", subtitle, items)


def deal_detail(deal: dict[str, Any]) -> MetadataContent:
    """One deal on another site, with a button that opens it there."""
    site = str(deal["site"])
    found = [badge(site)]
    if deal.get("flag"):
        found.append(badge(str(deal["flag"]), "warning"))
    lead: list[Node] = [*badges(found), text(str(deal["price"]), "emphasis")]
    if deal.get("why"):
        lead.append(text(str(deal["why"]).capitalize()))
    body = facts(
        [
            ("Where", str(deal.get("location") or "")),
            ("Condition", str(deal.get("condition") or "")),
        ]
    )
    actions: list[Button] = []
    if link(str(deal.get("url") or "")):
        actions.append(Link(f"Open on {site}", str(deal["url"])))
    if deal.get("source") == "craigslist":
        actions.append(("Haggle for me", {"action": "negotiate_help"}))
    actions += [("Back to deals", {"action": "deals"}), ("Watch for more", {"action": "watch"})]
    return screen(
        str(deal["title"]),
        "deals",
        lead,
        body,
        actions,
        picture_url=photo(deal.get("image")),
        resubmittable=True,
    )


def negotiate_help() -> MetadataContent:
    lead: list[Node] = [
        *badges([badge("By email")]),
        text("My agent can haggle with a Craigslist seller for you, within your max.", "emphasis"),
    ]
    body = steps(
        [
            "1. Open the post on Craigslist and tap Reply.",
            "2. Copy the email address it shows, like abc123@sale.craigslist.org.",
            "3. Send it here with the title and your max: negotiate abc123@sale.craigslist.org "
            '"Trek FX 2 bike" max $250.',
        ]
    )
    body.append(
        muted(
            "Every email says it's from your Yardsale assistant, an AI agent, and never shows "
            "your max."
        )
    )
    return screen(
        "Haggle on Craigslist",
        "craigslist",
        lead,
        body,
        [("My negotiations", {"action": "negotiations"}), BUY],
    )
