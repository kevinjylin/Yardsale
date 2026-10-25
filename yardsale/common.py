"""Shared paths, ports, settings, and identity helpers."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import secrets
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from uagents.resolver import AlmanacApiResolver

from yardsale.models import Appraisal, ItemDetails, MarketView

ROOT = Path(__file__).resolve().parents[1]
# API keys and settings come from .env (gitignored); see .env.example.
load_dotenv(ROOT / ".env")

# .agent-data/ is gitignored, so the master seed and local state never get committed.
DATA_DIR = ROOT / ".agent-data" / "yardsale-bureau"
MASTER_SEED_FILE = DATA_DIR / "master_seed"
MARKET_FILE = DATA_DIR / "market.json"

BUREAU_PORT = int(os.getenv("YARDSALE_BUREAU_PORT", "8100"))
BUYER_PORT = int(os.getenv("YARDSALE_BUYER_PORT", "8101"))
SELLER_PORT = int(os.getenv("YARDSALE_SELLER_PORT", "8102"))


def endpoint(port: int) -> str:
    return f"http://127.0.0.1:{port}/submit"


def _master_seed() -> str:
    """Return YARDSALE_MASTER_SEED, or a random one created once and kept in .agent-data/."""
    from_env = os.getenv("YARDSALE_MASTER_SEED", "").strip()
    if from_env:
        return from_env
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(MASTER_SEED_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return MASTER_SEED_FILE.read_text().strip()
    with os.fdopen(fd, "w") as handle:
        handle.write(secrets.token_urlsafe(48))
    return MASTER_SEED_FILE.read_text().strip()


def derive_seed(label: str) -> str:
    """Deterministic per-agent seed: the same label always gives the same agent address."""
    return hmac.new(
        _master_seed().encode(), f"yardsale:{label}".encode(), hashlib.sha256
    ).hexdigest()


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(path)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text())


def dollars(cents: int) -> str:
    return f"${cents / 100:,.2f}"


async def wait_until(check: Callable[[], Awaitable[bool]], what: str) -> None:
    for _ in range(60):
        if await check():
            return
        await asyncio.sleep(1)
    raise TimeoutError(f"timed out after 60s waiting for {what}")


async def almanac_endpoints(address: str) -> list[str]:
    _, endpoints = await AlmanacApiResolver().resolve(address)
    return endpoints


class PipelineError(RuntimeError):
    """A photo listing failed; the message is shown to the seller."""


class UnpricedError(PipelineError):
    """The photo was identified, but nothing sold recently was close enough to price it and the
    seller gave no price. Keeps what was found, so a price sent next can finish the listing."""

    def __init__(
        self,
        message: str,
        item: ItemDetails,
        appraisal: Appraisal,
        market: MarketView | None = None,
    ) -> None:
        super().__init__(message)
        self.item = item
        self.appraisal = appraisal
        self.market = market  # live asking prices, if the Scout found any: kept for the proposal
