"""The ASI:One chat's screens, as Interactive Cards.

Every reply the Market sends in the chat is a short line of text and one card: the menu, the
sell and buy screens, a proposal, a live listing, search results, an item, an offer, a haggle,
a payment, the seller's listings, the buyer's orders, watches, and deals on other sites. Each
card has a picture and buttons, and every card but the menu has a Main menu button, so no
screen is a dead end. Typed commands still do everything the buttons do (commands.py), and the
text above each card names them, for clients that don't show cards and for ASI:One's planner.

Pictures are 16:9 art from assets/cards in this public repository (YARDSALE_CARD_ART, empty to
turn them off), or a real photo where there is one: the closest eBay sale of the same product
for a listing, the listing's own photo for a deal on another site. These photos are links to
eBay's and the sites' own image servers; Yardsale never re-hosts them.

Card text is plain, so links in it can't be tapped. Links go in the text above the card.

This module only lays cards out. What they say (prices, where they came from, the seller's
floor shown only to that seller) is decided in chat.py, which passes the words in.
"""

from __future__ import annotations

import os
import re
from collections.abc import Sequence
from typing import Any
from uuid import uuid4

from uagents_core.contrib.protocols.chat import MetadataContent
from uagents_core.contrib.protocols.chat.cards import (
    BadgeNode,
    ButtonAction,
    ButtonNode,
    CarouselBadge,
    CarouselCardPayload,
    CarouselItem,
    CtaAction,
    CustomCardPayload,
    DetailCardPayload,
    DetailSummaryRow,
    DividerNode,
    GroupNode,
    HeadingNode,
    ImageNode,
    InputNode,
    InputOption,
    ListNode,
    SectionNode,
    TextNode,
    create_card_content,
)
from uagents_core.contrib.protocols.chat.cards import ListItem as CardListItem

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
DRAWER_WIDTH = 560  # px; ASI:One clamps it to 320-800
MAX_IMAGE_URL = 400  # an outside photo link longer than this isn't shown
MAX_TEXT = 400  # characters of one text element
CAROUSEL_ITEMS = 10

Node = Any  # one element of a custom card's tree
Selection = dict[str, Any]
Action = tuple[str, Selection]

MAIN_MENU: Action = ("Main menu", {"action": "menu"})
SELL: Action = ("Sell something", {"action": "sell"})
BUY: Action = ("Find a deal", {"action": "buy"})
LISTINGS: Action = ("My listings", {"action": "listings"})
ORDERS: Action = ("My orders", {"action": "orders"})
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


# --- Pieces --------------------------------------------------------------------------------------


def plain(value: str) -> str:
    """Card text can't be tapped, so markdown links show as their words."""
    value = _MARKDOWN_LINK.sub(lambda m: m.group(1), value)
    value = " ".join(value.split())
    return value if len(value) <= MAX_TEXT else value[: MAX_TEXT - 1].rstrip() + "…"


def text(value: str, style: str = "body") -> TextNode:
    return TextNode(type="text", value=plain(value), style=style)


def muted(value: str) -> TextNode:
    return text(value, "muted")


def heading(value: str, level: int = 3) -> HeadingNode:
    return HeadingNode(type="heading", value=plain(value)[:90], level=level)


def badge(label: str, variant: str = "info") -> BadgeNode:
    return BadgeNode(type="badge", label=label[:40], variant=variant)


def picture(src: str | None, alt: str) -> list[Node]:
    if not src:
        return []
    return [ImageNode(type="image", src=src, alt=plain(alt)[:80], aspect_ratio="16:9")]


def column(children: Sequence[Node], gap: int | None = None) -> GroupNode:
    return GroupNode(type="group", direction="column", gap=gap, children=list(children))


def beside(pictured: list[Node], lines: list[Node]) -> list[Node]:
    """A picture in a row beside some lines, so it takes half the card, not all of it."""
    if not pictured or not lines:
        return [*pictured, *lines]
    return [GroupNode(type="group", direction="row", gap=12, children=[*pictured, column(lines)])]


def button(label: str, selection: Selection, primary: bool = False) -> ButtonNode:
    return ButtonNode(
        type="button", label=label[:40], primary=primary, action=ButtonAction(selection=selection)
    )


def buttons(actions: Sequence[Action], primary: bool = True) -> list[Node]:
    """A row of buttons, the first one primary unless `primary` is False."""
    if not actions:
        return []
    row = [
        button(label, selection, primary=primary and index == 0)
        for index, (label, selection) in enumerate(actions)
    ]
    return [GroupNode(type="group", direction="row", gap=8, children=row)]


def with_menu(actions: Sequence[Action]) -> list[Action]:
    """The actions, then Main menu unless one of them already opens it."""
    found = list(actions)
    if all(selection != MAIN_MENU[1] for _, selection in found):
        found.append(MAIN_MENU)
    return found


def rows(pairs: Sequence[tuple[str, str]]) -> list[Node]:
    """Label-over-value pairs, for custom cards."""
    return [column([muted(label), text(value)]) for label, value in pairs if value]


def steps(lines: Sequence[str]) -> ListNode:
    return ListNode(type="list", items=[CardListItem(children=[text(line)]) for line in lines])


def custom(title: str, children: list[Node], subtitle: str | None = None) -> MetadataContent:
    root = SectionNode(
        type="section",
        title=plain(title)[:90],
        subtitle=plain(subtitle)[:160] if subtitle else None,
        children=children,
    )
    return create_card_content(
        CustomCardPayload(root=root), card_id=uuid4(), preferred_drawer_width_px=DRAWER_WIDTH
    )


def screen(
    title: str,
    art_name: str,
    lead: list[Node],
    body: list[Node] | None = None,
    actions: Sequence[Action] = (),
    *,
    picture_url: str | None = None,
    subtitle: str | None = None,
    more_actions: Sequence[Action] = (),
) -> MetadataContent:
    """The usual card: a picture beside the lead lines, then the body, then buttons, the last
    row ending in Main menu."""
    pictured = picture(picture_url or art(art_name), title)
    children = [*beside(pictured, lead), *(body or [])]
    if more_actions:
        children += buttons(actions)
        children += buttons(with_menu(more_actions), primary=False)
    else:
        children += buttons(with_menu(actions), primary=bool(actions))
    return custom(title, children, subtitle)


def carousel(
    title: str, subtitle: str | None, items: list[CarouselItem], *, menu: bool = True
) -> MetadataContent:
    items = items[: CAROUSEL_ITEMS - (1 if menu else 0)]
    if menu:
        items.append(menu_item())
    return create_card_content(
        CarouselCardPayload(
            title=plain(title)[:90],
            subtitle=plain(subtitle)[:160] if subtitle else None,
            items=items,
        ),
        card_id=uuid4(),
        preferred_drawer_width_px=DRAWER_WIDTH,
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
        badges=[CarouselBadge(label=label[:40], variant=variant) for label, variant in badges]
        or None,
        primary_cta=cta(action, primary=True),
    )


def menu_item() -> CarouselItem:
    """Main menu as a carousel's last item: a carousel has no buttons of its own."""
    return item("main-menu", "Main menu", MAIN_MENU, image=art("menu"), subtitle="Sell or buy")


def detail(
    title: str, hero: str | None, pairs: Sequence[tuple[str, str]], actions: Sequence[Action]
) -> MetadataContent:
    ctas = [cta(action, primary=index == 0) for index, action in enumerate(with_menu(actions))]
    return create_card_content(
        DetailCardPayload(
            title=plain(title)[:90],
            hero_image=hero,
            summary_rows=[
                DetailSummaryRow(label=label, value=plain(value)) for label, value in pairs if value
            ]
            or None,
            ctas=ctas,
        ),
        card_id=uuid4(),
        preferred_drawer_width_px=DRAWER_WIDTH,
    )


def field(
    name: str,
    label: str,
    kind: str = "number",
    placeholder: str | None = None,
    required: bool = False,
    options: Sequence[tuple[str, str]] = (),
) -> InputNode:
    return InputNode(
        type="input",
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


def notice(text_shown: str, actions: Sequence[Action] = (), title: str = "") -> MetadataContent:
    """The card for a short message: its first sentence beside a picture, then what to do next.
    The whole message is in the text above, so the card doesn't repeat it."""
    trouble = bool(_TROUBLE.search(text_shown))
    lead: list[Node] = [badge("Needs a look", "warning")] if trouble else []
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
    body: list[Node] = [steps(list(lines))] if lines else []
    return screen("Your buyers", "craigslist", [text(about)], body, [*actions, LISTINGS])


# --- The menu, selling, buying -------------------------------------------------------------------


def _tile(title: str, about: str, status: tuple[str, str] | None, action: Action) -> GroupNode:
    children: list[Node] = [heading(title)]
    if status:
        children.append(badge(*status))
    children += [muted(about), button(action[0], action[1], primary=True)]
    return column(children, gap=4)


def menu(
    *,
    live: int = 0,
    waiting: int = 0,
    to_pay: int = 0,
    orders: int = 0,
    watching: int = 0,
    ebay: bool = False,
    school: str = "",
) -> MetadataContent:
    """The home screen: what Yardsale does, and a tile for everything it can do."""
    intro: list[Node] = [
        text(
            "Sell your stuff or find a deal. Agents do the pricing, the listing, and the haggling."
        ),
    ]
    if school:
        intro.append(badge(f"{school} students only", "info"))
    if waiting:
        intro.append(badge(f"{waiting} waiting for your approval", "warning"))
    listings_status = (
        (f"{live} for sale", "success") if live else (("None yet", "info") if not waiting else None)
    )
    orders_status = (
        (f"{to_pay} to pay", "warning")
        if to_pay
        else ((f"{orders} order{'s' if orders != 1 else ''}", "info") if orders else None)
    )
    tiles = [
        _tile(
            "Sell something",
            "Attach photos. Agents price each one from eBay sales and what similar ones ask "
            "nearby.",
            ("Free pricing", "success"),
            ("Start selling", {"action": "sell"}),
        ),
        _tile(
            "Find a deal",
            "Say what you want and your max. Agents check Yardsale, eBay, Craigslist, OfferUp, and "
            "Facebook Marketplace.",
            ("Every site", "info"),
            ("Find a deal", {"action": "buy"}),
        ),
        _tile(
            "My listings",
            "What you're selling, its agent, and what sold.",
            listings_status,
            ("Open my listings", LISTINGS[1]),
        ),
        _tile(
            "My orders",
            "What you bought, and paying for it.",
            orders_status,
            ("Open my orders", ORDERS[1]),
        ),
        _tile(
            "Watches",
            "Standing searches of other sites, every 6 hours for a week.",
            (f"{watching} running", "info") if watching else None,
            ("Open watches", WATCHES[1]),
        ),
        _tile(
            "Haggle on Craigslist",
            "Paste a post's reply address, and my agent emails the seller within your max.",
            ("By email", "info"),
            ("How it works", {"action": "negotiate_help"}),
        ),
    ]
    if ebay:
        tiles.append(
            _tile(
                "Your eBay",
                "Connect your own eBay, and what you list here goes up there too.",
                None,
                ("Connect eBay", {"action": "ebay_connect"}),
            )
        )
    children = beside(picture(art("menu"), "Yardsale"), intro)
    for start in range(0, len(tiles), 2):
        children.append(
            GroupNode(type="group", direction="row", gap=12, children=tiles[start : start + 2])
        )
    children.append(
        muted(
            "Claude reads your photos and ASI:One reads what you type. Buyers never see your "
            "lowest price, and sellers never see your max."
        )
    )
    return custom("Yardsale", children, "Your agents for buying and selling used stuff")


def sell(waiting: int = 0) -> MetadataContent:
    lead: list[Node] = [
        badge("Nothing is listed until you approve", "info"),
        text("Attach a photo of what you're selling, right here in the chat.", "emphasis"),
    ]
    body: list[Node] = [
        steps(
            [
                "1. Attach photos, up to 8 at once, one item each.",
                '2. Add your minimum if you like: "sell this, at least $40".',
                "3. Claude identifies it. Recent eBay sales and what similar ones ask nearby set "
                "the price, and you see the sales behind it.",
                "4. Approve it, and the item gets its own agent that haggles with every buyer "
                "and never goes below your minimum.",
            ]
        )
    ]
    actions: list[Action] = [LISTINGS]
    if waiting:
        actions.insert(0, ("Review what's waiting", {"action": "review"}))
    return screen("Sell something", "sell", lead, body, actions)


def buy(query: str = "", budget_cents: int | None = None) -> MetadataContent:
    """A form: what they want and their max."""
    lead: list[Node] = [
        text("Tell me what you want and the most you'd pay.", "emphasis"),
        muted(
            "I check Yardsale first, then eBay, Craigslist, OfferUp, and Facebook Marketplace, "
            "and score every listing against what it really sells for."
        ),
    ]
    body: list[Node] = [
        field(
            "query",
            "What are you looking for?",
            "text",
            placeholder=query or "mini fridge",
            required=True,
        ),
        field(
            "max",
            "Your max ($). Sellers never see it.",
            placeholder=f"{budget_cents / 100:.0f}" if budget_cents else "60",
        ),
    ]
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
        badge("Agents at work", "info"),
        text(f"Got {got}. Pricing takes about a minute.", "emphasis"),
    ]
    body: list[Node] = [steps([f"✓ Got {got}", *(f"• {step}" for step in PRICING_STEPS)])]
    if count > 1:
        body.append(muted("Four at a time. Each listing comes here as it's ready."))
    return screen(
        "Pricing your stuff", "pricing", lead, body, [("Check on it", {"action": "status"})]
    )


def searching(sites: str) -> MetadataContent:
    lead: list[Node] = [badge("Agents at work", "info"), text(f"Searching {sites}.", "emphasis")]
    body = [muted("Each listing is scored against what the thing really sells for on eBay.")]
    return screen("Finding deals", "deals", lead, body)


def working() -> MetadataContent:
    return screen("Working on it", "pricing", [badge("Agents at work", "info")])


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
    lead: list[Node] = []
    appraisal = listing.appraisal
    if appraisal is not None and appraisal.asking_cents is not None:
        used = len(appraisal.matches)
        lead.append(
            badge(
                f"{used} eBay sale{'s' if used != 1 else ''}, {appraisal.confidence} confidence",
                "success" if appraisal.confidence == "high" else "info",
            )
        )
    else:
        lead.append(badge("Your price", "info"))
    if listing.item is not None:
        lead.append(badge(f"{listing.item.condition} condition", "info"))
    lead += [text(asking, "emphasis"), text(lowest)]
    body: list[Node] = []
    if basis:
        body.append(muted(basis))
    body += [muted(line) for line in market]
    if feedback:
        body += [badge("Check your price", "warning"), text(feedback)]
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
        more_actions=[MAIN_MENU],
    )


def prices_form(listing: Listing) -> MetadataContent:
    """The seller's own prices: asking and lowest."""
    lead: list[Node] = [
        text(f"Now asking {usd(listing.asking_cents)}, lowest {usd(listing.floor_cents)}."),
        muted("Leave one empty to keep it. Buyers never see your lowest price."),
    ]
    body: list[Node] = [
        field("asking", "Asking price ($)", placeholder=f"{listing.asking_cents / 100:.0f}"),
        field(
            "lowest", "Lowest price you'll take ($)", placeholder=f"{listing.floor_cents / 100:.0f}"
        ),
    ]
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
        badge("Needs your price", "warning"),
        text(f"This looks like {title}.", "emphasis"),
        muted("No recent eBay sales were close enough to suggest a price."),
    ]
    body: list[Node] = [muted(hint)] if hint else []
    body += [
        field("asking", "Asking price ($)", placeholder="40", required=True),
        field("lowest", "Lowest price you'll take ($)", placeholder="30"),
    ]
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


def listed(listing: Listing, notes: Sequence[str] = (), craigslist: bool = True) -> MetadataContent:
    """A listing just went live: its agent, and the Craigslist post offer."""
    ids = {"item_id": listing.item_id}
    lead: list[Node] = [badge("For sale", "success")]
    if listing.host == AGENTVERSE:
        lead.append(badge("Own agent on Agentverse", "info"))
    lead += [
        text(f"For sale at {usd(listing.asking_cents)}.", "emphasis"),
        text(
            "Its own agent answers buyers for you. It haggles with reasons from the market and "
            f"never goes below your {usd(listing.floor_cents)} minimum, which buyers never see."
        ),
    ]
    body = rows(
        [("Its agent", agent_name(listing)), ("Buyers find it on", "ASI:One and @yardsale")]
    )
    body += [muted(note) for note in notes]
    actions: list[Action] = [LISTINGS, ("Sell another", SELL[1])]
    if craigslist:
        body.append(
            text(
                "Want it on Craigslist too? I'll write the post, and buyers' emails go straight to "
                "its agent."
            )
        )
        return screen(
            f"Your {listing.title} is live",
            "live",
            lead,
            body,
            [
                ("Write my Craigslist post", {"action": "craigslist", **ids}),
                ("Not now", {"action": "no_craigslist", **ids}),
            ],
            picture_url=listing_picture(listing),
            more_actions=actions,
        )
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
        badge("Ready to paste", "success"),
        text("Copy the post in the message above into Craigslist.", "emphasis"),
    ]
    body = rows(
        [
            ("Title (use it exactly)", title),
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
    return screen("Your Craigslist post", "craigslist", lead, body, [LISTINGS])


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
    """A seller's numbered photos still waiting: tap one to review it, or approve them all."""
    list_items = []
    for n, line, listing in entries:
        selection: Selection = (
            {"action": "review", "item_id": listing.item_id}
            if listing is not None
            else {"action": "price_form", "n": n}
        )
        list_items.append(
            CardListItem(
                children=[text(line), muted("Tap to review" if listing else "Tap to set a price")],
                action=ButtonAction(selection=selection),
            )
        )
    lead: list[Node] = [text(head, "emphasis"), muted("Numbers stay the same as you go.")]
    body: list[Node] = [ListNode(type="list", items=list_items)] if list_items else []
    actions: list[Action] = []
    if any(listing is not None for _, _, listing in entries):
        actions.append(("Approve all", {"action": "approve_all"}))
    return screen("Waiting for you", "proposal", lead, body, [*actions, LISTINGS])


def batch_done(lines: Sequence[str], any_waiting: bool) -> MetadataContent:
    lead: list[Node] = [badge("All priced", "success"), text(lines[0], "emphasis")]
    body: list[Node] = [steps(list(lines[1:]))] if len(lines) > 1 else []
    actions: list[Action] = [("Approve all", {"action": "approve_all"})] if any_waiting else []
    return screen("Your photos", "proposal", lead, body, [*actions, LISTINGS])


def priced_photo_failed(message: str) -> MetadataContent:
    lead: list[Node] = [badge("Couldn't price it", "warning"), text(first_sentence(message))]
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
        badge("Can't be undone", "warning"),
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
    for listing in found:
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
        badge("Agreed", "success") if agreed else badge("Counter-offer", "warning"),
        text(f"Asking price: {usd(listing.asking_cents)}"),
        text(f"{'Agreed price' if agreed else 'Counter-offer'}: {usd(cents)}", "emphasis"),
    ]
    body: list[Node] = []
    if note:
        body.append(muted(f"The seller's agent: {note}"))
    body.append(muted("Good for 24 hours."))
    actions: list[Action] = [
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
        more_actions=[MAIN_MENU],
    )


STATUS_TEXT = {
    "agreed": "agreed",
    "counter": "their price is within your max",
    "over_max": "wouldn't come down to your max",
    "unavailable": "no longer for sale",
    "no_answer": "its agent didn't answer",
}


def _round_text(offer_cents: int, reply_cents: int, agreed: bool) -> str:
    return f"you {usd(offer_cents)} → " + ("agreed" if agreed else usd(reply_cents))


def _outcome(n: int, outcome: HaggleOutcome, best: bool, image: str | None) -> list[Node]:
    lines: list[Node] = [
        heading(f"{n}. {outcome.title}"),
        muted(f"Asking {usd(outcome.asking_cents)}"),
    ]
    if outcome.price_cents is not None:
        label = "Agreed" if outcome.status == "agreed" else "Within your max"
        lines.append(badge(f"{label}: {usd(outcome.price_cents)}", "success"))
        if best:
            lines.append(badge("Best deal", "info"))
    else:
        lines.append(badge(STATUS_TEXT.get(outcome.status, outcome.status), "warning"))
    children = beside(picture(image, outcome.title), lines)
    if outcome.rounds:
        children.append(
            steps(
                [
                    f"Round {i}: " + _round_text(r.offer_cents, r.reply_cents, r.agreed)
                    for i, r in enumerate(outcome.rounds, 1)
                ]
            )
        )
    if outcome.price_cents is not None:
        children.append(
            button(
                f"Buy for {usd(outcome.price_cents)}",
                {"action": "accept", "item_id": outcome.item_id, "cents": outcome.price_cents},
                primary=best,
            )
        )
    return [column(children, gap=4)]


def haggle(result: HaggleResult, pictures: dict[str, str | None]) -> MetadataContent | None:
    """Every round with every seller's agent, and a Buy button for each price within the max."""
    if not result.outcomes:
        return None
    children: list[Node] = []
    for n, outcome in enumerate(result.outcomes, 1):
        if children:
            children.append(DividerNode(type="divider"))
        best = outcome.item_id == result.best_item_id
        children += _outcome(n, outcome, best, pictures.get(outcome.item_id))
    children.append(DividerNode(type="divider"))
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
) -> MetadataContent:
    lead: list[Node] = [
        badge("Test mode: no real money", "warning"),
        text(f"{usd(price_cents)} for the {title}.", "emphasis"),
        text("Open the payment link in the message above, then tap I've paid."),
    ]
    body = rows(
        [
            ("Order", deal_id),
            ("Pay with", provider),
            ("Pay within", "24 hours"),
            ("Meet at", meet),
        ]
    )
    return screen(
        f"Pay {usd(price_cents)} for the {title}",
        "pay",
        lead,
        body,
        [("I've paid", {"action": "paid", "deal_id": deal_id}), ORDERS],
        picture_url=image or art("pay"),
    )


def payment_status(
    title: str, price_cents: int, deal_id: str, status: str, paid: bool
) -> MetadataContent:
    lead: list[Node] = [
        badge("Paid", "success") if paid else badge(status, "warning"),
        text(f"{usd(price_cents)} for the {title}.", "emphasis"),
        muted(f"Order {deal_id}"),
    ]
    actions: list[Action] = (
        [ORDERS, BUY]
        if paid
        else [
            ("Check again", {"action": "paid", "deal_id": deal_id}),
            ORDERS,
        ]
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
    items.append(
        item("find", "Find another deal", BUY, image=art("buy"), subtitle="Every site at once")
    )
    return carousel("Your orders", "Tap one to pay or check it", items)


def watches(entries: Sequence[str], news: int, sites: str) -> MetadataContent:
    lead: list[Node] = [
        text(
            f"I check {sites} every 6 hours and tell you what's new when you message me.",
        )
    ]
    if news:
        lead.insert(0, badge(f"{news} new", "success"))
    body: list[Node] = []
    for n, line in enumerate(entries, 1):
        body.append(
            GroupNode(
                type="group",
                direction="row",
                gap=8,
                children=[text(f"{n}. {line}"), button("Stop", {"action": "unwatch", "n": n})],
            )
        )
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
    for n, deal in enumerate(found):
        badges = [(str(deal["site"]), "info")]
        if deal.get("flag"):
            badges.append((str(deal["flag"]), "warning"))
        items.append(
            item(
                str(deal["id"]),
                str(deal["title"]),
                ("Get the link", {"action": "deal", "n": n}),
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
    pairs = [
        ("Price", str(deal["price"])),
        ("Site", str(deal["site"])),
        ("Why it's a deal", str(deal.get("why") or "")),
        ("Where", str(deal.get("location") or "")),
        ("Condition", str(deal.get("condition") or "")),
        ("Link", "In the message above"),
    ]
    actions: list[Action] = []
    if deal.get("source") == "craigslist":
        actions.append(("Have my agent haggle", {"action": "negotiate_help"}))
    actions += [("Back to deals", {"action": "deals"}), ("Watch for more", {"action": "watch"})]
    return detail(str(deal["title"]), photo(deal.get("image")) or art("deals"), pairs, actions)


def negotiate_help() -> MetadataContent:
    lead: list[Node] = [
        badge("By email", "info"),
        text("My agent can haggle with a Craigslist seller for you, within your max.", "emphasis"),
    ]
    body: list[Node] = [
        steps(
            [
                "1. Open the post on Craigslist and tap Reply.",
                "2. Copy the email address it shows, like abc123@sale.craigslist.org.",
                "3. Send it here with the title and your max: negotiate abc123@sale.craigslist.org "
                '"Trek FX 2 bike" max $250.',
            ]
        ),
        muted(
            "Every email says it's from your Yardsale assistant, an AI agent, and never shows "
            "your max."
        ),
    ]
    return screen(
        "Haggle on Craigslist",
        "craigslist",
        lead,
        body,
        [("My negotiations", {"action": "negotiations"}), BUY],
    )
