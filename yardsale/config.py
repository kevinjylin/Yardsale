"""Settings from the environment (.env), and a readiness check for going live.

The check says which keys are set, never what they are.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from yardsale.common import BUREAU_PORT, DATA_DIR, endpoint

# live: Almanac + Agentverse. offline: no Fetch servers, real Claude/Apify. demo: no network.
MODES = ("live", "offline", "demo")


class ConfigError(ValueError):
    pass


def public_url() -> str | None:
    """YARDSALE_PUBLIC_URL as a /submit endpoint, or None when it isn't set.

    This is the address item agents advertise on the Almanac, such as an ngrok or cloud URL that
    forwards to this machine's Bureau port. Without it they advertise 127.0.0.1.
    """
    raw = os.getenv("YARDSALE_PUBLIC_URL", "").strip()
    if not raw:
        return None
    parsed = urlparse(raw)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ConfigError("YARDSALE_PUBLIC_URL must be a full URL, like https://example.ngrok.app")
    if parsed.query or parsed.fragment:
        raise ConfigError("YARDSALE_PUBLIC_URL can't have a query string or #fragment")
    path = parsed.path.rstrip("/")
    if not path.endswith("/submit"):
        path += "/submit"
    return f"{parsed.scheme}://{parsed.netloc}{path}"


def bureau_endpoint(mode: str) -> str:
    """Where agents in this Bureau say they can be reached."""
    if mode == "live":
        return public_url() or endpoint(BUREAU_PORT)
    return endpoint(BUREAU_PORT)


def db_path(mode: str) -> Path:
    """One database per mode, so offline and demo listings never get registered in live mode."""
    if mode not in MODES:
        raise ConfigError(f"unknown mode {mode!r}")
    override = os.getenv("YARDSALE_DB", "").strip()
    return Path(override) if override else DATA_DIR / f"listings-{mode}.sqlite3"


@dataclass
class Check:
    name: str
    ok: bool
    detail: str
    required: bool  # for this mode; optional checks only warn


def _is_set(name: str) -> bool:
    return bool(os.getenv(name, "").strip())


def readiness(mode: str) -> list[Check]:
    if mode == "demo":
        return [Check("Credentials", True, "none needed: every service is a local fake", True)]
    checks = [
        Check(
            "ANTHROPIC_API_KEY",
            _is_set("ANTHROPIC_API_KEY") or _is_set("ANTHROPIC_AUTH_TOKEN"),
            "Claude identifies photos and picks matching eBay sales",
            True,
        ),
        Check("APIFY_TOKEN", _is_set("APIFY_TOKEN"), "eBay sold-listings search", True),
        Check(
            "ASI_ONE_API_KEY",
            _is_set("ASI_ONE_API_KEY"),
            "reads typed chat messages; without it a simpler keyword reader is used",
            False,
        ),
    ]
    if mode == "live":
        try:
            url = public_url()
            checks.append(
                Check(
                    "YARDSALE_PUBLIC_URL",
                    url is not None,
                    f"item agents advertise {url}"
                    if url
                    else "not set: item agents advertise 127.0.0.1, which only this machine "
                    "can reach",
                    False,
                )
            )
        except ConfigError as ex:
            checks.append(Check("YARDSALE_PUBLIC_URL", False, str(ex), True))
        checks.append(
            Check(
                "AGENTVERSE_API_KEY",
                _is_set("AGENTVERSE_API_KEY"),
                "connects the 4 mailbox agents at startup; without it, use the Inspector links "
                "the server logs",
                False,
            )
        )
    return checks


def report(mode: str) -> tuple[str, bool]:
    """A printable checklist and whether every required check passed."""
    checks = readiness(mode)
    lines = [f"Yardsale readiness ({mode} mode):"]
    for check in checks:
        mark = "ok  " if check.ok else ("MISSING" if check.required else "warn")
        lines.append(f"  [{mark}] {check.name}: {check.detail}")
    if mode == "live":
        lines += [
            "  Manual steps no key can do (see README, 'Going live'):",
            "  - connect each mailbox agent to Agentverse once (Inspector link or API key)",
            "  - send a real photo from the ASI:One app and confirm the link downloads",
            "  - set up @yardsale on Fetch Business and test that ASI:One finds it",
        ]
    return "\n".join(lines), all(c.ok for c in checks if c.required)
