"""Settings from the environment (.env), and a readiness check for going live.

The check says which keys are set, never what they are.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from yardsale.common import BUREAU_PORT, DATA_DIR, endpoint

# live: Almanac + Agentverse. offline: no Fetch servers, real Claude/Apify.
MODES = ("live", "offline")


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
    """One database per mode, so offline listings never get registered in live mode."""
    if mode not in MODES:
        raise ConfigError(f"unknown mode {mode!r}")
    override = os.getenv("YARDSALE_DB", "").strip()
    return Path(override) if override else DATA_DIR / f"listings-{mode}.sqlite3"


EBAY_ENVIRONMENTS = ("sandbox", "production")


@dataclass(frozen=True)
class EbaySettings:
    """Yardsale's one eBay developer keyset. Sellers connect their own accounts to it."""

    client_id: str
    client_secret: str = field(repr=False)
    runame: str  # eBay's name for the redirect URL; its accept URL is <API URL>/ebay/callback
    env: str = "sandbox"


def ebay_settings() -> EbaySettings | None:
    """EBAY_CLIENT_ID, EBAY_CLIENT_SECRET, and EBAY_RUNAME, or None when none are set.

    The sandbox is the default. Production is used only when YARDSALE_EBAY_ENV=production, and a
    keyset made for one (eBay puts -SBX- or -PRD- in the App ID) is refused for the other.
    """
    client_id = os.getenv("EBAY_CLIENT_ID", "").strip()
    secret = os.getenv("EBAY_CLIENT_SECRET", "").strip()
    runame = os.getenv("EBAY_RUNAME", "").strip()
    env = os.getenv("YARDSALE_EBAY_ENV", "").strip().lower() or "sandbox"
    if env not in EBAY_ENVIRONMENTS:
        raise ConfigError("YARDSALE_EBAY_ENV must be sandbox or production")
    if not (client_id or secret or runame):
        return None
    missing = [
        name
        for name, value in (
            ("EBAY_CLIENT_ID", client_id),
            ("EBAY_CLIENT_SECRET", secret),
            ("EBAY_RUNAME", runame),
        )
        if not value
    ]
    if missing:
        raise ConfigError(f"eBay selling also needs {', '.join(missing)}")
    if env == "sandbox" and "-PRD-" in client_id:
        raise ConfigError(
            "EBAY_CLIENT_ID is a production keyset; set YARDSALE_EBAY_ENV=production to use it"
        )
    if env == "production" and "-SBX-" in client_id:
        raise ConfigError("EBAY_CLIENT_ID is a sandbox keyset, but YARDSALE_EBAY_ENV=production")
    return EbaySettings(client_id, secret, runame, env)


@dataclass
class Check:
    name: str
    ok: bool
    detail: str
    required: bool  # for this mode; optional checks only warn


def _is_set(name: str) -> bool:
    return bool(os.getenv(name, "").strip())


def readiness(mode: str) -> list[Check]:
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
    checks.append(_payments_check())
    checks.append(_business_api_check())
    checks.append(_ebay_check())
    checks.append(_mail_check())
    checks.append(_campus_check())
    checks.append(_facebook_check())
    checks.append(
        Check(
            "YARDSALE_ORDERS_WEBHOOK",
            _is_set("YARDSALE_ORDERS_WEBHOOK"),
            "each order's status is sent here"
            if _is_set("YARDSALE_ORDERS_WEBHOOK")
            else "not set: orders are only kept in the listings database",
            False,
        )
    )
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
                "connects the 5 mailbox agents at startup; without it, use the Inspector links "
                "the server logs",
                False,
            )
        )
        checks.append(_hosting_check())
    return checks


def _payments_check() -> Check:
    from yardsale.payments import PaymentConfigError, provider_from_env

    try:
        provider = provider_from_env()
    except PaymentConfigError as ex:
        return Check("payments", False, str(ex), True)
    if provider is None:
        return Check(
            "payments",
            False,
            "no Visa or Stripe keys: deals close at an agreed price with no payment link",
            False,
        )
    return Check("payments", True, f"buyers get a {provider.label} link for each deal", False)


def _business_api_check() -> Check:
    from yardsale.business_api import ApiConfigError, api_key, api_port, haggle_tools_enabled

    try:
        key, port = api_key(), api_port()
    except ApiConfigError as ex:
        return Check("YARDSALE_API_KEY", False, str(ex), True)
    if key is None:
        return Check(
            "YARDSALE_API_KEY",
            False,
            "not set: no HTTP API for @yardsale's Fetch Business OpenAPI integration",
            False,
        )
    tools = (
        "with the haggle and deal tools (YARDSALE_API_HAGGLE)"
        if haggle_tools_enabled()
        else "without the haggle and deal tools (YARDSALE_API_HAGGLE=1 adds agent-to-agent buying)"
    )
    return Check(
        "YARDSALE_API_KEY",
        True,
        f"@yardsale's HTTP API listens on 127.0.0.1:{port}, {tools}; tunnel it to make it public",
        False,
    )


def _ebay_check() -> Check:
    try:
        settings = ebay_settings()
    except ConfigError as ex:
        return Check("EBAY_CLIENT_ID", False, str(ex), True)
    if settings is None:
        return Check(
            "EBAY_CLIENT_ID", False, "not set: sellers can't also list on their own eBay", False
        )
    if not _is_set("YARDSALE_API_KEY"):
        return Check(
            "EBAY_CLIENT_ID",
            False,
            "set, but eBay's sign-in returns to /ebay/callback on the HTTP API, which needs "
            "YARDSALE_API_KEY",
            False,
        )
    return Check(
        "EBAY_CLIENT_ID",
        True,
        f"sellers can connect eBay ({settings.env}); the RuName's accept URL must be "
        "<public API URL>/ebay/callback",
        False,
    )


def _mail_check() -> Check:
    from yardsale.craigslist_mail import MailConfigError, settings_from_env

    try:
        mail = settings_from_env()
    except MailConfigError as ex:
        return Check("YARDSALE_MAIL_ADDRESS", False, str(ex), True)
    if mail is None:
        return Check(
            "YARDSALE_MAIL_ADDRESS",
            False,
            "not set: nobody answers Craigslist buyers' emails",
            False,
        )
    return Check(
        "YARDSALE_MAIL_ADDRESS",
        True,
        f"item agents answer Craigslist buyers' emails through {mail.imap_host}",
        False,
    )


def _campus_check() -> Check:
    from yardsale.onboarding import campus_mode

    if not campus_mode():
        return Check(
            "YARDSALE_CAMPUS",
            False,
            "not set: no school sign-in; anyone can buy and sell with anyone",
            False,
        )
    if not _is_set("GOOGLE_MAPS_API_KEY"):
        return Check(
            "YARDSALE_CAMPUS",
            True,
            "on: new chats start with the school sign-in; GOOGLE_MAPS_API_KEY isn't set, so "
            "campus meeting spots get no map link",
            False,
        )
    return Check(
        "YARDSALE_CAMPUS",
        True,
        "on: new chats start with the school sign-in; meeting spots get a Google Maps link",
        False,
    )


def _facebook_check() -> Check:
    from yardsale.facebook import enabled

    if not enabled():
        return Check(
            "YARDSALE_FACEBOOK",
            False,
            "not set: buyer searches and the Market Scout skip Facebook Marketplace",
            False,
        )
    return Check(
        "YARDSALE_FACEBOOK",
        True,
        "on: searches include Facebook Marketplace (the priciest site per search)",
        False,
    )


def _hosting_check() -> Check:
    from yardsale.hosting import hosted_limit

    limit = hosted_limit()
    if limit == 0:
        return Check(
            "YARDSALE_HOST_ITEMS",
            False,
            "0: every item agent runs in this server and registers on the Almanac only",
            False,
        )
    if not _is_set("AGENTVERSE_API_KEY"):
        return Check(
            "YARDSALE_HOST_ITEMS",
            False,
            f"{limit}, but hosting on Agentverse needs AGENTVERSE_API_KEY",
            False,
        )
    return Check(
        "YARDSALE_HOST_ITEMS",
        True,
        f"up to {limit} item agents are hosted on Agentverse; the rest run in this server",
        False,
    )


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
