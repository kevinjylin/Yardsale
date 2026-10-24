"""Claude calls shared by the Identifier and the Appraiser."""

from __future__ import annotations

import os
from typing import Any, TypeVar, cast

import anthropic
from anthropic.types.beta import BetaMessageParam, BetaOutputConfigParam
from pydantic import BaseModel

MODEL = os.getenv("YARDSALE_CLAUDE_MODEL", "claude-opus-5-5")
EFFORT = os.getenv("YARDSALE_CLAUDE_EFFORT", "medium")

T = TypeVar("T", bound=BaseModel)

_client: anthropic.AsyncAnthropic | None = None


class LLMError(RuntimeError):
    pass


def client() -> anthropic.AsyncAnthropic:
    global _client
    if _client is None:
        if not (os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN")):
            raise LLMError("ANTHROPIC_API_KEY is not set on the server (see .env.example)")
        _client = anthropic.AsyncAnthropic(timeout=120.0)
    return _client


def image_block(image_b64: str, media_type: str) -> dict[str, Any]:
    return {
        "type": "image",
        "source": {"type": "base64", "media_type": media_type, "data": image_b64},
    }


def url_image_block(url: str) -> dict[str, Any]:
    return {"type": "image", "source": {"type": "url", "url": url}}


async def ask(system: str, content: list[dict[str, Any]], output: type[T]) -> T:
    """One Claude request whose answer is parsed into `output`."""
    response = await client().beta.messages.parse(
        model=MODEL,
        max_tokens=16000,
        system=system,
        messages=cast("list[BetaMessageParam]", [{"role": "user", "content": content}]),
        output_config=cast("BetaOutputConfigParam", {"effort": EFFORT}),
        output_format=output,
        # If a safety classifier declines, Anthropic retries on its recommended fallback model.
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
    )
    if response.stop_reason == "refusal":
        raise LLMError("Claude declined this request")
    if response.parsed_output is None:
        raise LLMError(f"Claude returned no usable answer (stop reason: {response.stop_reason})")
    return response.parsed_output
