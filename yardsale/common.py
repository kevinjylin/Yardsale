"""Shared paths, ports, and identity helpers for the Yardsale Bureau prototype."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
# .agent-data/ is gitignored, so the master seed and local state never get committed.
DATA_DIR = ROOT / ".agent-data" / "yardsale-bureau"
MASTER_SEED_FILE = DATA_DIR / "master_seed"
MARKET_FILE = DATA_DIR / "market.json"

BUREAU_PORT = int(os.getenv("YARDSALE_BUREAU_PORT", "8100"))
BUYER_PORT = int(os.getenv("YARDSALE_BUYER_PORT", "8101"))


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
