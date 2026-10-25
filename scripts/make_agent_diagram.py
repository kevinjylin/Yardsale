#!/usr/bin/env python3
"""Render the source-grounded agent map: python scripts/make_agent_diagram.py.

Uses only the standard library and an installed Chrome/Chromium. No service calls.
"""

import shutil
import subprocess
import tempfile
from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WIDTH, HEIGHT = 1800, 1250
OUT = ROOT / "docs" / "agent-structure.svg"
INK, MUTED = "#edf5ff", "#a4b6cb"
CYAN, GREEN, GOLD, PURPLE = "#5ee4ed", "#73e2b0", "#ffc476", "#b8a3ff"
parts: list[str] = []


def text(x, y, value, size=20, color=INK, weight=400, **attrs):
    extra = " ".join(f'{k.replace("_", "-")}="{v}"' for k, v in attrs.items())
    parts.append(
        f'<text x="{x}" y="{y}" font-size="{size}" fill="{color}" '
        f'font-weight="{weight}" {extra}>{escape(value)}</text>'
    )


def box(x, y, w, h, fill="#11243a", stroke="#29435e", radius=18, dash=False):
    parts.append(
        f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{radius}" '
        f'fill="{fill}" stroke="{stroke}" stroke-width="1.5" '
        + ('stroke-dasharray="7 6" ' if dash else "")
        + "/>"
    )


def wire(path, color=CYAN, both=False):
    marker = {CYAN: "cyan", GREEN: "green", GOLD: "gold", PURPLE: "purple"}[color]
    parts.append(
        f'<path d="{path}" fill="none" stroke="{color}" stroke-width="2" '
        f'marker-end="url(#{marker})" ' + (f'marker-start="url(#{marker})" ' if both else "") + "/>"
    )


def card(x, y, w, h, number, title, handle, lines, color=GREEN):
    box(x, y, w, h)
    box(x + 18, y + 18, 32, 28, fill="#1b3449", stroke="none", radius=8)
    text(x + 34, y + 38, number, 15, color, 700, text_anchor="middle")
    text(x + 64, y + 39, title, 23, INK, 650)
    text(x + 20, y + 68, handle, 15, color, 500)
    for i, line in enumerate(lines):
        text(x + 20, y + 97 + 24 * i, line, 18, MUTED)


def draw():
    parts.clear()
    parts.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{HEIGHT}" '
        f'viewBox="0 0 {WIDTH} {HEIGHT}" role="img" aria-labelledby="title desc">'
    )
    parts.append(
        '<title id="title">Yardsale agent architecture</title><desc id="desc">'
        "Seven core Fetch.ai agents and one item agent per approved listing. "
        "The orchestrator coordinates identification, parallel sold-price research "
        "and live market scouting, appraisal, deal finding, and buyer-side negotiation. "
        "Item agents enforce a private seller floor. Checkout and Craigslist email "
        "negotiation are orchestrator functions.</desc>"
    )
    parts.append(
        '<defs><radialGradient id="glow"><stop stop-color="#143d57"/>'
        '<stop offset="1" stop-color="#081423"/></radialGradient>'
    )
    for name, color in [("cyan", CYAN), ("green", GREEN), ("gold", GOLD), ("purple", PURPLE)]:
        parts.append(
            f'<marker id="{name}" viewBox="0 0 10 10" refX="9" refY="5" '
            'markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
            f'<path d="M0 0L10 5L0 10Z" fill="{color}"/></marker>'
        )
    parts.append(
        "</defs><style>text{font-family:Inter,-apple-system,BlinkMacSystemFont,"
        '"Segoe UI",sans-serif}</style>'
    )
    box(0, 0, WIDTH, HEIGHT, fill="#081423", stroke="none", radius=0)
    parts.append('<ellipse cx="900" cy="310" rx="900" ry="380" fill="url(#glow)"/>')
    text(64, 58, "YARDSALE  /  AGENT ARCHITECTURE", 17, CYAN, 650, letter_spacing="3")
    text(64, 115, "One marketplace. A team of agents.", 46, INK, 700)
    text(64, 151, "7 core agents + an agent for every approved item", 23, MUTED)
    text(1736, 57, "FETCH.AI  ·  AGENTVERSE  ·  ASI:ONE", 16, MUTED, 600, text_anchor="end")

    for x, title, sub in [
        (64, "ASI:One", "Seller photos · buyer searches · chat cards"),
        (632, "@yardsale on Fetch Business", "Search, price checks and negotiation tools"),
        (1200, "Craigslist email", "Buyer replies and seller outreach"),
    ]:
        box(x, 192, 536, 94, fill="#0e2033")
        text(x + 24, 229, title, 23, INK, 650)
        text(x + 24, 261, sub, 19, MUTED)
    wire("M332 286 V306 H900 V328")
    wire("M900 286 V328")
    wire("M1468 286 V306 H900 V328")

    box(64, 332, 1672, 108, fill="#15394c", stroke=CYAN, radius=20)
    text(92, 372, "01", 18, CYAN, 700)
    text(138, 374, "Yardsale Orchestrator", 31, INK, 700)
    text(
        138,
        411,
        "@ys-orchestrator  ·  Routes work, owns approvals and maintains workflow state",
        20,
        MUTED,
    )
    text(1708, 373, "asi1-mini → text intent", 20, CYAN, 600, text_anchor="end")
    text(1708, 410, "SQLite → listings, offers, deals and payments", 18, MUTED, text_anchor="end")
    wire("M544 440 V488", GREEN)
    wire("M1396 440 V488", PURPLE)
    text(566, 472, "Coordinates pricing", 17, GREEN)
    text(1418, 472, "Coordinates buying", 17, PURPLE)

    box(64, 492, 960, 426, fill="#0c211f", stroke="#244b43", radius=22)
    text(88, 531, "SELL  /  FROM PHOTO TO PROPOSAL", 18, GREEN, 650, letter_spacing="1.6")
    card(
        88,
        626,
        232,
        149,
        "02",
        "Identifier",
        "@ys-identifier",
        ["Claude reads the photo", "Brand, model, condition"],
    )
    card(365, 556, 290, 130, "03", "Researcher", "@ys-researcher", ["Apify · eBay sold prices"])
    card(365, 736, 290, 130, "04", "Market Scout", "@ys-scout", ["Apify · live asking prices"])
    card(
        700,
        556,
        300,
        154,
        "05",
        "Appraiser",
        "@ys-appraiser",
        ["Claude matches sold comps", "Code calculates prices"],
    )
    wire("M320 680 H342 V621 H363", GREEN)
    wire("M342 680 V801 H363", GREEN)
    text(385, 719, "RUN IN PARALLEL", 14, GREEN, 600, letter_spacing="1.3")
    wire("M655 621 H698", GREEN)
    box(700, 756, 300, 130, fill="#17372d", stroke=GREEN, dash=True)
    text(720, 790, "Seller reviews proposal", 22, INK, 650)
    text(720, 820, "Price + market advice", 19, MUTED)
    text(720, 850, "Approve, edit or cancel", 19, MUTED)
    wire("M850 710 V754", GREEN)
    wire("M655 801 H698", GREEN)
    text(
        88, 899, "Workflow order shown; the orchestrator sends each specialist request.", 16, MUTED
    )

    box(1056, 492, 680, 426, fill="#1b1d35", stroke="#41395f", radius=22)
    text(1080, 531, "BUY  /  DISCOVER AND NEGOTIATE", 18, PURPLE, 650, letter_spacing="1.6")
    card(
        1080,
        556,
        632,
        154,
        "06",
        "Deal Finder",
        "@ys-dealfinder",
        ["Apify searches external marketplaces", "Code ranks deals against eBay sold prices"],
        PURPLE,
    )
    card(
        1080,
        748,
        632,
        138,
        "07",
        "Shopper",
        "@ys-shopper",
        ["On request: haggles with item agents within buyer budget"],
        PURPLE,
    )
    text(1080, 734, "Separate paths: external deals or Yardsale item negotiation", 16, MUTED)

    wire("M850 886 V980", GOLD)
    text(873, 955, "On approval", 17, GOLD)
    wire("M1080 817 H1040 V1055 H1026", PURPLE, both=True)

    box(64, 984, 960, 150, fill="#30291f", stroke=GOLD, radius=20)
    box(88, 1010, 58, 58, fill="#4b3b26", stroke="#745936", radius=14)
    text(117, 1049, "N", 27, GOLD, 700, text_anchor="middle")
    text(168, 1026, "Item agents", 30, INK, 700)
    text(168, 1062, "One per approved listing · negotiates offers and counters", 21, MUTED)
    text(168, 1096, "Enforces the private seller floor · runs in Bureau or on Agentverse", 19, GOLD)

    box(1056, 984, 680, 150, fill="#0e2033", stroke="#6b819a", dash=True)
    text(1080, 1018, "INSIDE THE ORCHESTRATOR", 16, CYAN, 650, letter_spacing="1.4")
    text(1080, 1053, "Checkout", 22, INK, 650)
    text(1223, 1053, "Stripe test / Visa sandbox", 19, MUTED)
    text(1080, 1087, "Email negotiator", 22, INK, 650)
    text(1282, 1087, "Craigslist seller outreach", 19, MUTED)
    text(1080, 1114, "Buyer-approved outreach; never above the buyer's max", 17, MUTED)

    text(64, 1177, "TRANSPORT", 14, CYAN, 700, letter_spacing="2")
    text(
        204,
        1177,
        "7 core uAgents in one Bureau · Agentverse mailboxes in live mode · "
        "local fallback for service requests",
        18,
        MUTED,
    )
    text(64, 1214, "MARKET DATA", 14, GREEN, 700, letter_spacing="2")
    text(204, 1214, "eBay · Craigslist · OfferUp · Facebook Marketplace (optional)", 18, MUTED)
    text(
        1736,
        1214,
        "01-07 = core agents  ·  N = item agents  ·  Dashed = functions",
        16,
        MUTED,
        text_anchor="end",
    )
    parts.append("</svg>")
    OUT.write_text("\n".join(parts) + "\n")


def render():
    chrome = (
        shutil.which("chromium")
        or shutil.which("google-chrome")
        or "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    )
    with tempfile.TemporaryDirectory(prefix="yardsale-diagram-") as scratch:
        page = Path(scratch) / "diagram.html"
        page.write_text(
            '<!doctype html><html><body style="margin:0;overflow:hidden">'
            + OUT.read_text()
            + "</body></html>"
        )
        subprocess.run(
            [
                chrome,
                "--headless=new",
                "--disable-gpu",
                "--hide-scrollbars",
                "--force-device-scale-factor=2",
                f"--user-data-dir={scratch}/profile",
                f"--window-size={WIDTH},{HEIGHT}",
                f"--screenshot={OUT.with_suffix('.png')}",
                page.as_uri(),
            ],
            check=True,
            capture_output=True,
            timeout=60,
        )


if __name__ == "__main__":
    draw()
    render()
    print(OUT)
    print(OUT.with_suffix(".png"))
