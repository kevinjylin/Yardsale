#!/usr/bin/env python3
"""Draw Yardsale's card art as SVG, then render each one to JPEG with headless Chrome.

    uv run python scripts/make_card_art.py              # write assets/src/*.svg and render JPEGs
    uv run python scripts/make_card_art.py --svg-only   # write the SVG sources only
    uv run python scripts/make_card_art.py --only sell  # redraw one card (repeat --only for more)

Every image is a flat illustration built from the shapes below: a gradient sky with faint stars,
one big glass tile in the middle holding the subject, two small glass bubbles at the sides
holding related objects, and a few sparkles. No image contains text, letters, numbers, or logos
(a coin's dollar sign is drawn as a shape), so each reads in light and dark themes and in any
language. Each card has its own gradient, grouped by meaning: selling is warm orange and coral,
buying teal, other sites blue, money green, warnings amber, information indigo, and the menu a
sunset. Cards are 1200x675 (16:9). Rendering needs Chrome or Chromium, plus macOS ``sips`` or
ImageMagick to write JPEG (55-80KB each, so cards load fast).

The script draws exactly the names in ``CARD_NAMES`` (the art names in ``yardsale/cards.py``).
A full run (without ``--only``) also removes any other ``.jpg`` in ``assets/cards`` and ``.svg``
in ``assets/src``, and touches nothing else. Agent avatars live elsewhere and aren't drawn here.

To use other art (for example from an image model), save a 1200x675 JPEG under the same name in
``assets/cards``. ``assets/PROMPTS.md`` has the subject of each one. Cards pick images by name,
so no code changes; push to ``main`` for ASI:One to load them.
"""

from __future__ import annotations

import argparse
import math
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable
from functools import partial
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "assets" / "src"
CARDS = ROOT / "assets" / "cards"
CHROME = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "google-chrome",
    "chromium",
)
WIDTH, HEIGHT = 1200, 675
JPEG_QUALITY = "74"  # keeps each card about 55-80KB

# The card art names yardsale/cards.py uses. Nothing else is drawn.
CARD_NAMES = (
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
)

# Each card's sky: top-left color, optional middle color, bottom-right color, and a glow.
Sky = tuple[str, str | None, str, str]
SKIES: dict[str, Sky] = {
    # The menu: a yard sale at sunset.
    "menu": ("#3f1f78", "#e0567c", "#ffb057", "#ffe08a"),
    # Selling: warm orange and coral.
    "sell": ("#7a2a1c", None, "#ff7a4d", "#ffd5a8"),
    "pricing": ("#6e2d14", None, "#f28c38", "#ffe1a6"),
    "proposal": ("#74213a", None, "#ff6f6f", "#ffc9b0"),
    "live": ("#7a3318", None, "#ff8f5a", "#ffe0b8"),
    "listings": ("#6b2430", None, "#f47b5a", "#ffd9b0"),
    "item": ("#6e3a1c", None, "#e8955a", "#ffe4c0"),
    # Buying: teal.
    "buy": ("#0b4650", None, "#1fb3a5", "#aaf7e8"),
    "haggle": ("#0f3f5a", None, "#22a7a8", "#b3f5ef"),
    "watch": ("#0c4a4f", None, "#2bb8b0", "#c6fff4"),
    "orders": ("#0e4a46", None, "#27b495", "#c0f7dc"),
    # Other sites: blue.
    "deals": ("#102e6b", None, "#3a7fe0", "#a8dcff"),
    "craigslist": ("#1a2a73", None, "#5872e6", "#c2d4ff"),
    "ebay": ("#0f3a6e", None, "#2f95d6", "#a5ecff"),
    # Money and payment: green.
    "deal": ("#0f4a3c", None, "#2aa86f", "#d0ffa8"),
    "pay": ("#0d4038", None, "#1f9c75", "#b5f5d0"),
    "paid": ("#0f4a3c", None, "#24a36b", "#c9ff9e"),
    # Warnings: amber. Information and campus: indigo.
    "warning": ("#5c2a0c", None, "#e8952f", "#ffe08a"),
    "info": ("#1b2160", None, "#4b52d6", "#aab6ff"),
    "campus": ("#24205e", None, "#5a5fd8", "#ffd9a0"),
}

INK = "#2a2140"
INK_SOFT = "#4b406c"
WHITE = "#ffffff"
CREAM = "#fff4e2"
GOLD = "#ffcf4a"
GOLD_DARK = "#e0a52a"
CORAL = "#ff6f61"
CORAL_DARK = "#d9483d"
ORANGE = "#ff9a52"
PEACH = "#ffb36b"
MINT = "#4fe0b0"
GREEN = "#22b573"
TEAL = "#21b3a5"
SKY = "#6cc4ff"
BLUE = "#3d6bea"
LILAC = "#b9a6ff"
PINK = "#ff8fc7"
WOOD = "#c98a5a"
WOOD_DARK = "#8a5a3a"
KRAFT = "#dba06a"
KRAFT_DARK = "#b97b45"
KRAFT_LIGHT = "#f0c48f"
TAPE = "#f7dcae"
SKIN = "#ffd3a8"
SKIN_DARK = "#eba878"
LEAF = "#3fb59a"
TAG = "#fff1d6"
TAG_MARK = "#f0c88c"


# ---------------------------------------------------------------------------
# Primitives
# ---------------------------------------------------------------------------


def rect(x: float, y: float, w: float, h: float, fill: str, r: float = 0, extra: str = "") -> str:
    return f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{r}" fill="{fill}" {extra}/>'


def circle(cx: float, cy: float, r: float, fill: str, extra: str = "") -> str:
    return f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="{fill}" {extra}/>'


def ellipse(cx: float, cy: float, rx: float, ry: float, fill: str, extra: str = "") -> str:
    return f'<ellipse cx="{cx}" cy="{cy}" rx="{rx}" ry="{ry}" fill="{fill}" {extra}/>'


def path(d: str, fill: str, extra: str = "") -> str:
    return f'<path d="{d}" fill="{fill}" {extra}/>'


def line(
    x1: float, y1: float, x2: float, y2: float, stroke: str, width: float, extra: str = ""
) -> str:
    return (
        f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{stroke}" '
        f'stroke-width="{width}" stroke-linecap="round" {extra}/>'
    )


def stroke(d: str, color: str, width: float, extra: str = "") -> str:
    """An open path drawn as a rounded line."""
    return path(
        d,
        "none",
        f'stroke="{color}" stroke-width="{width}" stroke-linecap="round" '
        f'stroke-linejoin="round" {extra}',
    )


def group(
    content: list[str], x: float = 0, y: float = 0, scale: float = 1.0, rotate: float = 0.0
) -> str:
    return (
        f'<g transform="translate({x} {y}) rotate({rotate}) scale({scale})">{"".join(content)}</g>'
    )


def sparkle(x: float, y: float, size: float, fill: str = WHITE, opacity: float = 0.9) -> str:
    s = size
    return path(
        f"M{x} {y - s} Q{x + s * 0.18} {y - s * 0.18} {x + s} {y} "
        f"Q{x + s * 0.18} {y + s * 0.18} {x} {y + s} Q{x - s * 0.18} {y + s * 0.18} {x - s} {y} "
        f"Q{x - s * 0.18} {y - s * 0.18} {x} {y - s}Z",
        fill,
        f'opacity="{opacity}"',
    )


def shine(x: float, y: float, w: float, h: float, r: float) -> str:
    """A soft highlight across the top of a shape."""
    return rect(x + w * 0.08, y + h * 0.08, w * 0.84, h * 0.18, WHITE, r * 0.6, 'opacity="0.22"')


def check(points: str, color: str, width: float) -> str:
    return stroke(points, color, width)


def check_badge(cx: float, cy: float, r: float, fill: str = MINT) -> list[str]:
    """A round badge with a white check mark."""
    return [
        circle(cx, cy, r, fill),
        check(
            f"M{cx - r * 0.45} {cy + r * 0.02} L{cx - r * 0.12} {cy + r * 0.34} "
            f"L{cx + r * 0.48} {cy - r * 0.3}",
            WHITE,
            r * 0.24,
        ),
    ]


def arrow(x: float, y1: float, y2: float, color: str, width: float = 16) -> str:
    """A vertical arrow from y1 to y2, with a rounded chevron head at y2."""
    head = width * 1.6 * (1 if y2 > y1 else -1)
    return line(x, y1, x, y2, color, width) + check(
        f"M{x - abs(head)} {y2 - head} L{x} {y2} L{x + abs(head)} {y2 - head}", color, width
    )


def star(cx: float, cy: float, r: float, fill: str = GOLD) -> str:
    """A five-point star with softened corners."""
    points = []
    for i in range(10):
        radius = r if i % 2 == 0 else r * 0.48
        angle = -math.pi / 2 + i * math.pi / 5
        points.append(f"{cx + radius * math.cos(angle):.1f} {cy + radius * math.sin(angle):.1f}")
    return path(
        "M" + " L".join(points) + "Z",
        fill,
        f'stroke="{fill}" stroke-width="{r * 0.14:.1f}" stroke-linejoin="round"',
    )


def tag(
    x: float,
    y: float,
    scale: float = 1.0,
    rotate: float = 0.0,
    color: str = TAG,
    mark: str = TAG_MARK,
    marks: bool = True,
) -> str:
    """A price tag with its hole at (x, y), pointing along the rotated x axis.

    Unrotated it is 164 wide and 76 tall, hole at the left. Rotate about 90 to hang it down.
    """
    body = [
        path("M-18 0 L18 -38 L132 -38 Q146 -38 146 -24 L146 24 Q146 38 132 38 L18 38Z", color),
        circle(12, 0, 10, mark),
        circle(12, 0, 5, INK, 'opacity="0.45"'),
    ]
    if marks:
        body += [rect(44, -16, 76, 12, mark, 6), rect(44, 4, 50, 12, mark, 6)]
    return group(body, x, y, scale, rotate)


def string(x1: float, y1: float, x2: float, y2: float, width: float = 4) -> str:
    """A tag's string, sagging a little between two points."""
    mx, my = (x1 + x2) / 2, max(y1, y2) + abs(x2 - x1) * 0.15
    return stroke(f"M{x1} {y1} Q{mx} {my} {x2} {y2}", INK_SOFT, width)


def coin(cx: float, cy: float, r: float) -> list[str]:
    """A gold coin with a rim and a dollar-sign shape."""
    dollar = [
        stroke(
            "M30 -30 Q24 -48 0 -48 Q-32 -48 -32 -24 Q-32 -4 0 0 Q32 4 32 26 "
            "Q32 50 0 50 Q-26 50 -32 30",
            GOLD_DARK,
            15,
        ),
        line(0, -66, 0, 68, GOLD_DARK, 15),
    ]
    return [
        circle(cx, cy + r * 0.1, r, GOLD_DARK),
        circle(cx, cy, r, GOLD),
        circle(cx, cy, r * 0.8, "none", f'stroke="#ffe08a" stroke-width="{r * 0.07:.1f}"'),
        group(dollar, cx, cy, r / 100),
        path(
            f"M{cx - r * 0.62} {cy - r * 0.3} Q{cx - r * 0.5} {cy - r * 0.62} "
            f"{cx - r * 0.18} {cy - r * 0.68}",
            "none",
            f'stroke="{WHITE}" stroke-width="{r * 0.09:.1f}" stroke-linecap="round" opacity="0.55"',
        ),
    ]


def magnifier(cx: float, cy: float, r: float, ring: str = INK, handle: str = INK) -> list[str]:
    """A magnifying glass with its lens at (cx, cy) and the handle down to the right."""
    d = r * 0.72
    return [
        line(cx + d, cy + d, cx + d + r * 0.62, cy + d + r * 0.62, handle, r * 0.42),
        circle(cx, cy, r, "#e6f7ff", 'opacity="0.55"'),
        circle(cx, cy, r, "none", f'stroke="{ring}" stroke-width="{r * 0.3:.1f}"'),
        path(
            f"M{cx - r * 0.55} {cy - r * 0.1} Q{cx - r * 0.5} {cy - r * 0.5} {cx - r * 0.1} "
            f"{cy - r * 0.56}",
            "none",
            f'stroke="{WHITE}" stroke-width="{r * 0.14:.1f}" stroke-linecap="round" opacity="0.8"',
        ),
    ]


# ---------------------------------------------------------------------------
# Objects, each drawn in a 400x400 box
# ---------------------------------------------------------------------------


def obj_tag() -> list[str]:
    return [
        string(84, 243, 38, 150, 6),
        tag(84, 243, 1.9, -18, GOLD, GOLD_DARK),
        sparkle(330, 300, 16, WHITE),
    ]


def obj_tag_down() -> list[str]:
    """A price tag with a down arrow: a lower price."""
    return [
        string(84, 243, 38, 150, 6),
        tag(84, 243, 1.9, -18, GOLD, GOLD_DARK, marks=False),
        arrow(222, 148, 252, GREEN, 22),
    ]


def obj_coin() -> list[str]:
    return [*coin(200, 194, 132), sparkle(340, 90, 16, WHITE)]


def obj_coins() -> list[str]:
    """A small stack of coins with one standing in front."""
    stack = []
    for i in range(4):
        y = 290 - i * 34
        stack += [
            ellipse(150, y + 12, 86, 30, GOLD_DARK),
            ellipse(150, y, 86, 30, GOLD),
            ellipse(150, y, 60, 18, "none", 'stroke="#ffe08a" stroke-width="6"'),
        ]
    return [*stack, *coin(270, 250, 86)]


def obj_chat() -> list[str]:
    return [
        rect(60, 90, 280, 190, WHITE, 70),
        path("M120 266 L92 340 L184 274Z", WHITE),
        circle(140, 185, 20, CORAL),
        circle(200, 185, 20, GOLD),
        circle(260, 185, 20, TEAL),
    ]


def obj_photo() -> list[str]:
    """An instant photo print of a little landscape."""
    return [
        group(
            [
                rect(-112, -136, 224, 272, WHITE, 16),
                rect(-92, -116, 184, 176, "#8fd3ff", 8),
                circle(44, -72, 22, GOLD),
                path("M-92 60 L-92 20 L-34 -36 L4 2 L34 -24 L92 30 L92 60Z", LEAF),
                path("M-92 60 L-92 38 L-40 8 L10 42 L50 26 L92 50 L92 60Z", "#2f9a80"),
                rect(-60, 84, 120, 14, "#efe9f5", 7),
            ],
            200,
            204,
            1.0,
            -8,
        )
    ]


def obj_star() -> list[str]:
    return [
        circle(200, 210, 150, WHITE, 'opacity="0.12"'),
        star(200, 214, 140, GOLD),
        star(186, 196, 52, "#ffe28a"),
        sparkle(342, 92, 18, WHITE),
        sparkle(66, 320, 12, WHITE),
    ]


def obj_camera(tagged: bool = True) -> list[str]:
    parts = [
        rect(150, 94, 100, 50, INK, 16),
        rect(64, 126, 272, 196, INK, 38),
        rect(64, 182, 272, 86, INK_SOFT),
        rect(92, 106, 40, 24, CORAL, 8),
        rect(266, 148, 46, 24, GOLD, 8),
        circle(200, 224, 82, "#f4f1ff"),
        circle(200, 224, 64, INK),
        circle(200, 224, 46, "#5fb6ff"),
        circle(200, 224, 22, "#3a8fe0"),
        circle(184, 206, 12, WHITE, 'opacity="0.85"'),
        shine(64, 126, 272, 196, 38),
    ]
    if tagged:
        parts += [
            rect(326, 144, 18, 28, INK_SOFT, 6),
            string(340, 162, 356, 210, 5),
            tag(356, 210, 0.62, 98, GOLD, GOLD_DARK),
        ]
    return parts


def obj_bag() -> list[str]:
    """A shopping bag with a magnifying glass."""
    return [
        stroke("M146 156 Q146 76 200 76 Q254 76 254 156", "#b8452f", 18),
        path("M98 150 L302 150 L322 336 Q324 352 308 352 L92 352 Q76 352 78 336Z", CORAL),
        path("M98 150 L302 150 L305 186 L95 186Z", "#ff9b7d"),
        circle(146, 168, 9, "#b8452f"),
        circle(254, 168, 9, "#b8452f"),
        *magnifier(268, 268, 62, WHITE, INK),
    ]


def obj_chart() -> list[str]:
    """A small bar chart under a magnifying glass: agents researching prices."""
    bars = []
    for i, (height, color) in enumerate(((78, PEACH), (130, CORAL), (100, GOLD), (166, MINT))):
        bars.append(rect(84 + i * 54, 296 - height, 40, height, color, 10))
    return [
        rect(54, 90, 264, 238, WHITE, 30),
        *bars,
        line(78, 304, 294, 304, "#e4ddef", 8),
        *magnifier(286, 150, 60, INK, INK),
    ]


def obj_clipboard() -> list[str]:
    """A clipboard holding a proposal: a price tag and a check mark."""
    return [
        rect(90, 68, 220, 296, WOOD, 26),
        rect(110, 104, 180, 242, WHITE, 14),
        rect(156, 50, 88, 46, INK_SOFT, 14),
        circle(200, 64, 9, WOOD),
        rect(134, 128, 124, 14, "#e6dff0", 7),
        rect(134, 154, 88, 14, "#e6dff0", 7),
        tag(144, 230, 0.66, -10, GOLD, GOLD_DARK),
        *check_badge(270, 308, 46),
    ]


def obj_robot() -> list[str]:
    """A friendly little item agent with an antenna, holding up a price tag."""
    return [
        line(200, 72, 200, 42, WHITE, 10),
        circle(200, 34, 15, GOLD),
        line(146, 300, 96, 250, "#ffd9c2", 24),
        line(254, 300, 312, 274, "#ffd9c2", 24),
        circle(94, 246, 17, "#ffe9d6"),
        circle(314, 272, 17, "#ffe9d6"),
        rect(130, 266, 140, 100, "#ffe9d6", 40),
        circle(200, 316, 30, CORAL),
        circle(200, 316, 13, "#ffb3a8"),
        rect(60, 140, 26, 60, "#ffcfb3", 13),
        rect(314, 140, 26, 60, "#ffcfb3", 13),
        rect(80, 70, 240, 190, "#fff6ec", 70),
        rect(108, 108, 184, 112, INK, 50),
        circle(160, 162, 20, MINT),
        circle(240, 162, 20, MINT),
        circle(166, 156, 6, WHITE, 'opacity="0.8"'),
        circle(246, 156, 6, WHITE, 'opacity="0.8"'),
        stroke("M178 194 Q200 210 222 194", MINT, 8),
        shine(80, 70, 240, 190, 70),
        string(318, 280, 338, 298, 4),
        tag(338, 298, 0.48, 82, GOLD, GOLD_DARK),
    ]


def obj_signal() -> list[str]:
    """A wireless signal: an agent that's live and reachable."""
    arcs = []
    for radius, opacity in ((76, 1.0), (140, 0.85), (204, 0.7)):
        left, right, top = 200 - radius * 0.7071, 200 + radius * 0.7071, 300 - radius * 0.7071
        arcs.append(
            stroke(
                f"M{left:.1f} {top:.1f} A{radius} {radius} 0 0 1 {right:.1f} {top:.1f}",
                WHITE,
                30,
                f'opacity="{opacity}"',
            )
        )
    return [*arcs, circle(200, 300, 28, GOLD)]


def obj_haggle() -> list[str]:
    """Two speech bubbles facing each other, with a coin and price arrows between them."""
    return [
        rect(26, 62, 158, 118, WHITE, 46),
        path("M118 172 L184 214 L156 168Z", WHITE),
        *[circle(70 + i * 35, 121, 13, CORAL) for i in range(3)],
        rect(216, 62, 158, 118, "#c9f4ee", 46),
        path("M282 172 L216 214 L244 168Z", "#c9f4ee"),
        *[circle(260 + i * 35, 121, 13, TEAL) for i in range(3)],
        *coin(200, 292, 64),
        arrow(98, 344, 244, MINT, 18),
        arrow(302, 244, 344, CORAL, 18),
    ]


def band(x1: float, y1: float, x2: float, y2: float, half: float, fill: str) -> str:
    """A straight band of the given half-width from one point to another, square at both ends."""
    length = math.hypot(x2 - x1, y2 - y1)
    nx, ny = -(y2 - y1) / length * half, (x2 - x1) / length * half
    return path(
        f"M{x1 + nx:.1f} {y1 + ny:.1f} L{x2 + nx:.1f} {y2 + ny:.1f} "
        f"L{x2 - nx:.1f} {y2 - ny:.1f} L{x1 - nx:.1f} {y1 - ny:.1f}Z",
        fill,
    )


def obj_handshake() -> list[str]:
    """Two hands shaking, sleeves in two colors."""
    fingers = []
    for i in range(4):
        y = 202 + i * 19
        tip = 236 - abs(i - 1.2) * 7
        fingers += [line(150, y, tip, y, "#d99466", 21), line(150, y, tip - 1, y, SKIN, 15)]
    parts = [
        circle(36, 332, 37, BLUE),
        band(36, 332, 130, 258, 37, BLUE),
        circle(364, 332, 37, ORANGE),
        band(364, 332, 270, 258, 37, ORANGE),
        # The right hand, reaching left under the left hand's fingers.
        path(
            "M278 262 L278 214 Q274 188 244 186 L196 188 Q172 190 172 214 L172 262 "
            "Q174 286 204 286 L246 286 Q266 284 278 262Z",
            SKIN_DARK,
        ),
        # The left hand, its fingers wrapping over the right one.
        path("M126 238 Q130 196 166 192 L176 192 L176 266 L156 268 Q132 266 126 250Z", SKIN),
        *fingers,
        # The right thumb across the top.
        line(252, 202, 194, 186, "#d99466", 25),
        line(252, 202, 194, 186, SKIN_DARK, 20),
        band(116, 266, 150, 238, 41, WHITE),
        band(284, 266, 250, 238, 41, WHITE),
        sparkle(200, 118, 24, GOLD, 1.0),
        sparkle(146, 136, 13, WHITE),
        sparkle(258, 132, 15, WHITE),
    ]
    return [group(parts, 0, -24)]


def obj_card_lock() -> list[str]:
    """A payment card with a padlock: a secure test-mode payment."""
    dots = [circle(84 + g * 52 + d * 11, 226, 4.5, "#c9d2ff") for g in range(4) for d in range(4)]
    return [
        group(
            [
                rect(38, 106, 268, 172, "#2d3a8c", 26),
                rect(38, 106, 268, 172, "#4a5fd6", 26, 'opacity="0.35"'),
                rect(70, 140, 54, 42, GOLD, 9),
                line(70, 161, 124, 161, GOLD_DARK, 3),
                line(97, 140, 97, 182, GOLD_DARK, 3),
                *dots,
                rect(70, 248, 92, 12, "#6f80e6", 6),
                circle(252, 150, 18, CORAL, 'opacity="0.9"'),
                circle(272, 150, 18, GOLD, 'opacity="0.9"'),
            ],
            0,
            20,
            1.0,
            -8,
        ),
        stroke("M244 236 L244 202 Q244 160 290 160 Q336 160 336 202 L336 236", WHITE, 22),
        rect(226, 226, 128, 106, GOLD, 24),
        shine(226, 226, 128, 106, 24),
        circle(290, 270, 14, INK),
        rect(284, 274, 12, 30, INK, 6),
    ]


def obj_receipt() -> list[str]:
    zigzag = " ".join(f"L{300 - i * 20} {352 if i % 2 else 338}" for i in range(11))
    return [
        path(f"M100 48 L300 48 {zigzag}Z", WHITE),
        *[rect(132, 96 + i * 40, 150 if i % 2 else 104, 16, "#d6d2f5", 8) for i in range(4)],
        *check_badge(282, 292, 66, "#2fd48a"),
        sparkle(352, 82, 18, GOLD),
    ]


def obj_storefront() -> list[str]:
    """A little shop shelf under a striped awning, each item with a price tag."""
    awning = []
    for i in range(6):
        color = CORAL if i % 2 == 0 else WHITE
        x = 44 + i * 52
        awning += [rect(x, 66, 52, 56, color), circle(x + 26, 122, 26, color)]
    return [
        rect(60, 112, 280, 254, CREAM, 18),
        rect(60, 300, 280, 66, "#f6e3c6", 0),
        rect(48, 360, 304, 14, WOOD, 7),
        *awning,
        rect(34, 52, 332, 22, CORAL_DARK, 11),
        # Shelf with a lamp, a mug, and books.
        rect(76, 240, 248, 12, WOOD, 6),
        rect(92, 228, 40, 12, INK, 6),
        rect(108, 180, 8, 50, INK, 4),
        path("M84 186 L140 186 L128 148 L96 148Z", GOLD),
        rect(164, 196, 44, 44, SKY, 10),
        circle(212, 216, 12, "none", f'stroke="{SKY}" stroke-width="8"'),
        rect(238, 222, 80, 18, CORAL, 5),
        rect(246, 204, 66, 18, TEAL, 5),
        rect(240, 186, 74, 18, LILAC, 5),
        # Under the shelf: a box and a plant.
        rect(104, 300, 86, 60, KRAFT, 6),
        rect(140, 300, 14, 60, TAPE),
        path("M246 360 L298 360 L306 322 L238 322Z", CORAL),
        ellipse(256, 300, 16, 30, LEAF, 'transform="rotate(-25 256 300)"'),
        ellipse(288, 300, 16, 30, LEAF, 'transform="rotate(25 288 300)"'),
        ellipse(272, 292, 14, 32, "#2f9a80"),
        # Tags.
        string(140, 186, 150, 200, 3),
        tag(150, 200, 0.3, 72, GOLD, GOLD_DARK),
        string(318, 204, 328, 214, 3),
        tag(328, 214, 0.3, 80, GOLD, GOLD_DARK),
        tag(160, 336, 0.32, -12, TAG, TAG_MARK),
    ]


def obj_parcel(checked: bool = True) -> list[str]:
    parts = [
        path("M70 162 L150 112 L330 112 L250 162Z", KRAFT_LIGHT),
        path("M70 162 L250 162 L250 344 L70 344Z", KRAFT),
        path("M250 162 L330 112 L330 294 L250 344Z", KRAFT_DARK),
        path("M144 162 L224 112 L256 112 L176 162Z", TAPE),
        rect(144, 162, 32, 70, TAPE),
        rect(96, 262, 72, 50, WHITE, 6),
        rect(106, 274, 50, 8, "#e4ddef", 4),
        rect(106, 290, 34, 8, "#e4ddef", 4),
    ]
    if checked:
        parts += check_badge(300, 300, 56)
    return parts


def obj_open_box() -> list[str]:
    """A cardboard box with its flaps open and a price tag on the front."""
    return [
        path("M110 134 L290 134 L280 84 L120 84Z", KRAFT_DARK),
        path("M84 162 L316 162 L290 134 L110 134Z", "#8f5a2c"),
        path("M84 162 L110 134 L52 104 L26 138Z", KRAFT_LIGHT),
        path("M316 162 L290 134 L348 104 L374 138Z", KRAFT_LIGHT),
        rect(84, 160, 232, 190, KRAFT, 10),
        rect(84, 160, 232, 16, KRAFT_DARK),
        rect(186, 176, 28, 64, TAPE),
        string(250, 186, 262, 222, 4),
        tag(262, 222, 0.62, 76, GOLD, GOLD_DARK),
    ]


def obj_globe() -> list[str]:
    """A globe with price tags pinned on it: deals on other sites."""
    land = "#62d39a"

    def pinned(x: float, y: float, rotate: float, color: str, mark: str) -> list[str]:
        return [
            tag(x, y, 0.5, rotate, color, mark),
            circle(x + 2, y + 4, 10, INK, 'opacity="0.25"'),
            circle(x, y, 10, CORAL_DARK),
            circle(x - 3, y - 3, 3.5, WHITE, 'opacity="0.8"'),
        ]

    return [
        circle(200, 210, 148, "#4aa3ff"),
        path(
            "M110 120 Q150 92 194 110 Q218 126 202 156 Q188 178 160 172 Q148 198 122 200 "
            "Q96 188 100 158 Q102 136 110 120Z",
            land,
        ),
        path(
            "M236 200 Q266 186 294 206 Q318 230 302 266 Q288 300 254 306 Q232 290 238 260 "
            "Q222 232 236 200Z",
            land,
        ),
        path("M100 248 Q124 240 138 260 Q140 284 118 290 Q96 278 100 248Z", land),
        ellipse(200, 210, 64, 148, "none", f'stroke="{WHITE}" stroke-width="5" opacity="0.3"'),
        line(52, 210, 348, 210, WHITE, 5, 'opacity="0.3"'),
        stroke("M82 167 A125 125 0 0 1 157 92", WHITE, 10, 'opacity="0.45"'),
        *pinned(130, 132, 28, GOLD, GOLD_DARK),
        *pinned(262, 120, -20, TAG, TAG_MARK),
        *pinned(240, 236, 40, CORAL, CORAL_DARK),
    ]


def obj_bell() -> list[str]:
    return [
        circle(0, -118, 16, GOLD_DARK),
        path(
            "M-88 60 Q-74 40 -74 -10 Q-74 -102 0 -108 Q74 -102 74 -10 Q74 40 88 60Z",
            GOLD,
        ),
        path(
            "M-50 -20 Q-50 -76 -6 -84",
            "none",
            f'stroke="{WHITE}" stroke-width="12" stroke-linecap="round" opacity="0.5"',
        ),
        rect(-100, 52, 200, 26, "#f2b632", 13),
        circle(0, 94, 20, GOLD_DARK),
    ]


def obj_bell_binoculars() -> list[str]:
    """A bell ringing with binoculars beside it: a standing watch for new finds."""
    waves = [
        stroke("M42 120 Q24 150 42 180", WHITE, 10, 'opacity="0.8"'),
        stroke("M18 104 Q-6 150 18 196", WHITE, 10, 'opacity="0.5"'),
        stroke("M254 120 Q272 150 254 180", WHITE, 10, 'opacity="0.8"'),
    ]
    binoculars = [
        rect(-60, -78, 42, 28, INK_SOFT, 9),
        rect(18, -78, 42, 28, INK_SOFT, 9),
        rect(-66, -56, 54, 96, INK, 22),
        rect(12, -56, 54, 96, INK, 22),
        rect(-16, -36, 32, 36, INK_SOFT, 8),
        circle(-39, 44, 36, INK),
        circle(39, 44, 36, INK),
        circle(-39, 44, 24, "#5fb6ff"),
        circle(39, 44, 24, "#5fb6ff"),
        circle(-46, 36, 7, WHITE, 'opacity="0.85"'),
        circle(32, 36, 7, WHITE, 'opacity="0.85"'),
    ]
    return [
        *waves,
        group(obj_bell(), 148, 178, 1.0, -12),
        group(binoculars, 292, 262, 1.0, 8),
    ]


def obj_note_envelope() -> list[str]:
    """A note pinned up, with an envelope: a post that buyers answer by email."""
    note = [
        rect(-112, -132, 224, 254, "#fff6d8", 10),
        *[rect(-82, -82 + i * 32, 164 if i % 2 == 0 else 120, 13, "#f0dca0", 6) for i in range(4)],
        tag(-70, 70, 0.4, -10, CORAL, CORAL_DARK),
        circle(2, -128, 20, CORAL_DARK, 'opacity="0.25"'),
        circle(0, -132, 18, CORAL),
        circle(-6, -138, 6, WHITE, 'opacity="0.7"'),
    ]
    envelope = [
        rect(-96, -62, 192, 124, WHITE, 14),
        stroke("M-96 54 L-24 -2", "#e2dbef", 7),
        stroke("M96 54 L24 -2", "#e2dbef", 7),
        stroke("M-90 -54 L0 12 L90 -54", "#cfc5e3", 9),
    ]
    return [
        group(note, 170, 190, 1.0, -6),
        group(envelope, 276, 290, 1.0, 6),
    ]


def obj_cart_plug() -> list[str]:
    """A shopping cart plugged in: connecting a seller's own marketplace account."""
    bars = [line(x, 156, x, 234, "#d6d0ec", 6) for x in (132, 172, 212, 252)]
    return [
        stroke("M341 196 C342 262 300 262 262 262", CORAL, 10),
        path("M84 148 L292 148 L270 240 L104 240Z", WHITE),
        *bars,
        line(96, 194, 280, 194, "#d6d0ec", 6),
        stroke("M28 116 L66 116 L106 262 L264 262", INK, 16),
        line(124, 262, 124, 284, INK, 10),
        line(246, 262, 246, 284, INK, 10),
        circle(124, 302, 22, INK),
        circle(246, 302, 22, INK),
        circle(124, 302, 8, "#d6d0ec"),
        circle(246, 302, 8, "#d6d0ec"),
        rect(304, 52, 74, 64, WHITE, 18),
        rect(326, 70, 9, 24, INK, 4),
        rect(348, 70, 9, 24, INK, 4),
        rect(326, 124, 9, 26, "#c9c4d8", 3),
        rect(348, 124, 9, 26, "#c9c4d8", 3),
        rect(312, 146, 58, 52, GOLD, 14),
        shine(312, 146, 58, 52, 14),
        sparkle(286, 104, 13, WHITE),
        sparkle(392, 140, 10, WHITE),
    ]


def obj_link() -> list[str]:
    return [
        group(
            [rect(0, 0, 150, 76, "none", 38, f'stroke="{WHITE}" stroke-width="24"')],
            70,
            150,
            1.0,
            -30,
        ),
        group(
            [rect(0, 0, 150, 76, "none", 38, f'stroke="{GOLD}" stroke-width="24"')],
            170,
            196,
            1.0,
            -30,
        ),
    ]


def obj_info() -> list[str]:
    return [
        circle(200, 200, 146, WHITE, 'opacity="0.25"'),
        circle(200, 200, 126, WHITE),
        circle(200, 132, 22, "#4b52d6"),
        rect(178, 174, 44, 118, "#4b52d6", 22),
        sparkle(340, 80, 16, GOLD),
    ]


def obj_shield() -> list[str]:
    return [
        path("M200 50 L330 100 L330 200 Q330 300 200 360 Q70 300 70 200 L70 100Z", "#e9f4ff"),
        path("M200 82 L300 120 L300 200 Q300 278 200 326 Q100 278 100 200 L100 120Z", SKY),
        check("M150 205 L188 242 L256 168", WHITE, 22),
    ]


def obj_warning() -> list[str]:
    """A warning triangle with a shield in front."""
    return [
        path(
            "M184 62 L336 322 Q346 344 322 344 L46 344 Q22 344 32 322 L170 62 Q177 50 184 62Z", GOLD
        ),
        rect(163, 140, 28, 118, INK, 14),
        circle(177, 294, 18, INK),
        group(obj_shield(), 236, 196, 0.44),
    ]


def obj_pin() -> list[str]:
    """A map pin: where to meet."""
    return [
        ellipse(200, 340, 70, 18, INK, 'opacity="0.25"'),
        path("M200 344 Q100 230 100 160 Q100 60 200 60 Q300 60 300 160 Q300 230 200 344Z", CORAL),
        circle(200, 160, 44, WHITE),
        path(
            "M138 104 Q160 78 196 74",
            "none",
            f'stroke="{WHITE}" stroke-width="12" stroke-linecap="round" opacity="0.4"',
        ),
    ]


def obj_clock() -> list[str]:
    """A clock face with no numbers: checking again every few hours."""
    ticks = [
        line(
            200 + math.cos(i * math.pi / 6) * 104,
            200 + math.sin(i * math.pi / 6) * 104,
            200 + math.cos(i * math.pi / 6) * 120,
            200 + math.sin(i * math.pi / 6) * 120,
            "#c9c4e0",
            10,
        )
        for i in range(12)
    ]
    return [
        circle(200, 200, 150, TEAL),
        circle(200, 200, 132, WHITE),
        *ticks,
        line(200, 200, 200, 120, INK, 14),
        line(200, 200, 256, 232, INK, 14),
        circle(200, 200, 14, CORAL),
    ]


def obj_campus() -> list[str]:
    """A campus hall with a clock tower, and a backpack in front."""
    windows = [rect(74 + i * 52, 284, 30, 44, "#5a5fd8", 6) for i in range(5) if i != 2]
    backpack = [
        stroke("M-18 -84 Q0 -104 18 -84", "#17988c", 10),
        rect(-56, -86, 112, 150, TEAL, 40),
        path("M-56 -40 Q-56 -86 0 -86 Q56 -86 56 -40 L56 -24 L-56 -24Z", "#17988c"),
        rect(-10, -34, 20, 22, GOLD, 5),
        rect(-38, 8, 76, 46, "#17988c", 16),
        line(-22, 22, 22, 22, "#7ae0d4", 5),
    ]
    return [
        rect(50, 244, 260, 116, CREAM, 14),
        rect(50, 244, 260, 22, "#f2d2ae", 10),
        *windows,
        path("M156 360 L156 304 Q180 280 204 304 L204 360Z", "#3f3f9e"),
        rect(140, 118, 80, 136, "#fff8ef", 10),
        path("M128 126 L180 54 L232 126Z", CORAL),
        circle(180, 172, 27, WHITE, 'stroke="#3f3f9e" stroke-width="7"'),
        line(180, 172, 180, 155, INK, 6),
        line(180, 172, 193, 179, INK, 6),
        rect(162, 214, 36, 40, "#5a5fd8", 8),
        path("M38 248 L180 200 L322 248Z", "#ff8f6b"),
        group(backpack, 314, 288, 1.0, 6),
        sparkle(330, 90, 18, GOLD),
        sparkle(66, 120, 12),
    ]


def bunting(x1: float, x2: float, y: float, sag: float, count: int) -> list[str]:
    """A string of little triangle flags hanging between two points."""

    def at(t: float) -> tuple[float, float]:
        mid = (x1 + x2) / 2
        return (
            (1 - t) ** 2 * x1 + 2 * (1 - t) * t * mid + t**2 * x2,
            (1 - t) ** 2 * y + 2 * (1 - t) * t * (y + sag * 2) + t**2 * y,
        )

    colors = (CORAL, GOLD, TEAL, LILAC, SKY)
    parts = [stroke(f"M{x1} {y} Q{(x1 + x2) / 2} {y + sag * 2} {x2} {y}", WHITE, 4)]
    for i in range(count):
        t = (i + 0.5) / count
        (ax, ay), (bx, by) = at(t - 0.4 / count), at(t + 0.4 / count)
        length = math.hypot(bx - ax, by - ay)
        nx, ny = -(by - ay) / length, (bx - ax) / length
        tip_x, tip_y = (ax + bx) / 2 + nx * 40, (ay + by) / 2 + ny * 40
        parts.append(
            path(f"M{ax:.1f} {ay:.1f} L{bx:.1f} {by:.1f} L{tip_x:.1f} {tip_y:.1f}Z", colors[i % 5])
        )
    return parts


def obj_yard_sale() -> list[str]:
    """A yard sale table with a lamp, a box, and books, a bike beside it, and bunting above.

    Drawn in a 580x400 box.
    """
    scallops = [circle(58 + i * 36, 312, 18, CORAL) for i in range(10)]
    parts = [
        *bunting(14, 566, 58, 26, 11),
        # Table.
        rect(64, 300, 14, 92, WOOD_DARK, 6),
        rect(364, 300, 14, 92, WOOD_DARK, 6),
        rect(40, 262, 362, 50, CORAL),
        *scallops,
        *[circle(58 + i * 36, 284, 5, WHITE, 'opacity="0.7"') for i in range(10)],
        rect(28, 246, 386, 20, WOOD, 8),
        # Lamp.
        circle(94, 154, 64, GOLD, 'opacity="0.18"'),
        rect(66, 234, 56, 12, INK, 6),
        rect(90, 160, 8, 76, INK, 4),
        path("M56 168 L132 168 L118 112 L70 112Z", GOLD),
        # Box.
        rect(148, 166, 104, 80, KRAFT, 8),
        path("M148 166 L252 166 L240 148 L160 148Z", KRAFT_LIGHT),
        rect(190, 166, 20, 80, TAPE),
        # Books.
        rect(272, 226, 112, 20, TEAL, 5),
        rect(370, 230, 8, 12, WHITE, 2),
        rect(280, 206, 98, 20, CORAL_DARK, 5),
        rect(364, 210, 8, 12, WHITE, 2),
        rect(276, 186, 106, 20, LILAC, 5),
        rect(368, 190, 8, 12, WHITE, 2),
        # Bike.
        circle(440, 344, 42, "none", f'stroke="{INK}" stroke-width="10"'),
        circle(530, 344, 42, "none", f'stroke="{INK}" stroke-width="10"'),
        circle(440, 344, 7, INK),
        circle(530, 344, 7, INK),
        stroke(
            "M440 344 L482 344 L468 282 Z M468 282 L522 284 L482 344 M522 284 L530 344",
            "#2a8fd8",
            10,
        ),
        rect(452, 266, 32, 11, INK, 5),
        line(468, 282, 466, 274, INK, 7),
        line(522, 284, 518, 260, INK, 8),
        line(506, 258, 532, 258, INK, 8),
        # Tags.
        string(58, 168, 48, 188, 3),
        tag(48, 188, 0.42, 100, TAG, TAG_MARK),
        string(236, 166, 228, 190, 3),
        tag(228, 190, 0.42, 30, GOLD, GOLD_DARK),
        string(384, 196, 398, 210, 3),
        tag(398, 210, 0.42, 84, TAG, TAG_MARK),
        string(532, 258, 546, 272, 3),
        tag(546, 272, 0.4, 92, GOLD, GOLD_DARK),
    ]
    return [group(parts, -6, -26)]


# ---------------------------------------------------------------------------
# Scenes
# ---------------------------------------------------------------------------


def _defs(uid: str, sky: Sky) -> str:
    top, middle, bottom, glow = sky
    mid = f'<stop offset="0.5" stop-color="{middle}"/>' if middle else ""
    return (
        f'<defs><linearGradient id="sky{uid}" x1="0" y1="0" x2="1" y2="1">'
        f'<stop offset="0" stop-color="{top}"/>{mid}<stop offset="1" stop-color="{bottom}"/>'
        f'</linearGradient><radialGradient id="glow{uid}" cx="0.5" cy="0.45" r="0.5">'
        f'<stop offset="0" stop-color="{glow}" stop-opacity="0.7"/>'
        f'<stop offset="1" stop-color="{glow}" stop-opacity="0"/></radialGradient>'
        f'<linearGradient id="tile{uid}" x1="0" y1="0" x2="0" y2="1">'
        f'<stop offset="0" stop-color="#ffffff" stop-opacity="0.30"/>'
        f'<stop offset="1" stop-color="#ffffff" stop-opacity="0.08"/></linearGradient></defs>'
    )


def _stars(width: int, height: int, seed: int) -> str:
    out = []
    for i in range(18):
        x = (seed * 97 + i * 211) % width
        y = (seed * 61 + i * 137) % int(height * 0.55)
        out.append(circle(x, y, 2 + (i % 3), WHITE, f'opacity="{0.2 + (i % 4) * 0.1:.2f}"'))
    return "".join(out)


def _bubble(uid: str, x: float, y: float, radius: float, drawing: list[str]) -> str:
    """A floating glass bubble holding a smaller related object."""
    scale = radius * 1.35 / 400
    return circle(
        x, y, radius, f"url(#tile{uid})", f'stroke="{WHITE}" stroke-opacity="0.35" stroke-width="3"'
    ) + group(drawing, x - 200 * scale, y - 200 * scale, scale)


def scene(
    uid: str,
    sky: Sky,
    subject: list[str],
    satellites: list[list[str]],
    *,
    box_width: float = 400,
    width: int = WIDTH,
    height: int = HEIGHT,
) -> str:
    """A gradient sky, a soft glow, a glass tile with the subject, and two side bubbles.

    The subject is drawn in a box_width x 400 box; a wider one gets a wider tile.
    """
    seed = sum(map(ord, uid))
    size = height * 0.84
    cx, cy = width / 2, height / 2
    parts = [
        _defs(uid, sky),
        rect(0, 0, width, height, f"url(#sky{uid})"),
        _stars(width, height, seed),
        circle(cx, cy, size * 0.95, f"url(#glow{uid})"),
        path(
            f"M0 {height * 0.8} Q{width * 0.3} {height * 0.68} {width * 0.6} {height * 0.78} "
            f"T{width} {height * 0.74} L{width} {height} L0 {height}Z",
            WHITE,
            'opacity="0.10"',
        ),
    ]
    spots = (
        (width * 0.165, height * 0.40, height * 0.23),
        (width * 0.835, height * 0.60, height * 0.23),
    )
    for (x, y, radius), drawing in zip(spots, satellites, strict=True):
        parts.append(_bubble(uid, x, y, radius, drawing))
    tile_w = size if box_width <= 400 else min(width * 0.54, size * box_width / 400)
    parts.append(
        rect(
            cx - tile_w / 2,
            cy - size / 2,
            tile_w,
            size,
            f"url(#tile{uid})",
            size * 0.2,
            f'stroke="{WHITE}" stroke-opacity="0.35" stroke-width="3"',
        )
    )
    scale = min(size / 400 * 0.86, tile_w * 0.9 / box_width)
    parts.append(group(subject, cx - box_width / 2 * scale, cy - 200 * scale, scale))
    for i in range(5):
        angle = seed + i * 1.3
        x, y = cx + math.cos(angle) * width * 0.36, cy + math.sin(angle) * height * 0.42
        # Keep sparkles off the side bubbles' drawings.
        if any(math.hypot(x - bx, y - by) < radius * 0.8 for bx, by, radius in spots):
            continue
        parts.append(sparkle(x, y, 9 + i * 3, WHITE, 0.75))
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}">{"".join(parts)}</svg>'
    )


Draw = Callable[[], list[str]]

# name: (subject, left bubble, right bubble). Each name is assets/cards/<name>.jpg and an art
# name in yardsale/cards.py. The menu's subject is wide (580x400); the rest are 400x400.
CARD_SCENES: dict[str, tuple[Draw, Draw, Draw]] = {
    "menu": (obj_yard_sale, obj_tag, obj_chat),
    "sell": (obj_camera, obj_photo, obj_coin),
    "buy": (obj_bag, obj_tag_down, obj_star),
    "pricing": (obj_chart, partial(obj_camera, tagged=False), obj_tag),
    "proposal": (obj_clipboard, obj_photo, obj_chart),
    "live": (obj_robot, obj_chat, obj_signal),
    "haggle": (obj_haggle, obj_coin, obj_handshake),
    "deal": (obj_handshake, obj_coins, obj_tag),
    "pay": (obj_card_lock, obj_shield, obj_receipt),
    "paid": (obj_receipt, obj_card_lock, obj_coin),
    "listings": (obj_storefront, obj_open_box, obj_chat),
    "orders": (obj_parcel, obj_bag, obj_receipt),
    "deals": (obj_globe, partial(magnifier, 170, 170, 92), obj_tag_down),
    "watch": (obj_bell_binoculars, obj_clock, obj_tag_down),
    "craigslist": (obj_note_envelope, obj_tag, obj_chat),
    "ebay": (obj_cart_plug, obj_link, obj_tag),
    "info": (obj_info, obj_chat, obj_star),
    "warning": (obj_warning, obj_tag, obj_info),
    "campus": (obj_campus, obj_pin, obj_chat),
    "item": (obj_open_box, obj_tag, obj_coin),
}
WIDE = {"menu": 580}


def check_names() -> None:
    """Fail loudly if the drawings drift from the names the cards use."""
    for table, label in ((CARD_SCENES, "scenes"), (SKIES, "skies")):
        if sorted(table) != sorted(CARD_NAMES):
            missing = sorted(set(CARD_NAMES) - set(table))
            extra = sorted(set(table) - set(CARD_NAMES))
            raise SystemExit(f"card {label} drifted: missing {missing}, extra {extra}")


def write_svgs(names: list[str]) -> list[tuple[Path, Path]]:
    SRC.mkdir(parents=True, exist_ok=True)
    jobs = []
    for name in names:
        subject, left, right = CARD_SCENES[name]
        svg = SRC / f"{name}.svg"
        svg.write_text(
            scene(name, SKIES[name], subject(), [left(), right()], box_width=WIDE.get(name, 400))
        )
        jobs.append((svg, CARDS / f"{name}.jpg"))
    return jobs


def remove_stale() -> None:
    """Remove art in assets/cards and assets/src that no card uses any more."""
    for folder, suffix in ((SRC, ".svg"), (CARDS, ".jpg")):
        if not folder.exists():
            continue
        for stale in folder.glob(f"*{suffix}"):
            if stale.stem not in CARD_NAMES:
                stale.unlink()
                print(f"removed {stale.relative_to(ROOT)}", file=sys.stderr)


def chrome() -> str:
    for candidate in CHROME:
        found = shutil.which(candidate) or (candidate if Path(candidate).exists() else None)
        if found:
            return found
    raise SystemExit("Install Google Chrome or Chromium to render images (or pass --svg-only)")


def to_jpeg(png: Path, jpg: Path) -> None:
    if shutil.which("sips"):
        command = [
            "sips",
            "-s",
            "format",
            "jpeg",
            "-s",
            "formatOptions",
            JPEG_QUALITY,
            str(png),
            "--out",
            str(jpg),
        ]
    elif shutil.which("magick"):
        command = ["magick", str(png), "-quality", JPEG_QUALITY, str(jpg)]
    else:
        raise SystemExit("Install ImageMagick (or use macOS) to write JPEG")
    subprocess.run(command, check=True, capture_output=True, timeout=60)


def render(jobs: list[tuple[Path, Path]]) -> None:
    binary = chrome()
    CARDS.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as scratch:
        for svg, jpg in jobs:
            png = Path(scratch) / f"{svg.stem}.png"
            page = Path(scratch) / f"{svg.stem}.html"
            page.write_text(
                "<!doctype html><html><body style='margin:0;overflow:hidden'>"
                f"{svg.read_text()}</body></html>"
            )
            subprocess.run(
                [
                    binary,
                    "--headless=new",
                    "--disable-gpu",
                    "--hide-scrollbars",
                    "--force-device-scale-factor=1",
                    f"--user-data-dir={scratch}/profile",
                    f"--window-size={WIDTH},{HEIGHT}",
                    f"--screenshot={png}",
                    page.as_uri(),
                ],
                check=True,
                capture_output=True,
                timeout=60,
            )
            to_jpeg(png, jpg)
            print(f"{jpg.relative_to(ROOT)}")


def main() -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--svg-only", action="store_true", help="write the SVG sources only")
    parser.add_argument(
        "--only", action="append", choices=CARD_NAMES, help="draw just this card (repeatable)"
    )
    args = parser.parse_args()
    check_names()
    names = args.only or list(CARD_NAMES)
    jobs = write_svgs(names)
    if not args.only:
        remove_stale()
    if not args.svg_only:
        render(jobs)
    print(f"{len(jobs)} images", file=sys.stderr)


if __name__ == "__main__":
    main()
