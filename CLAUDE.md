# Yardsale

Product idea, decisions already made, and open questions:

@docs/background.md

## Commands

```bash
uv sync
uv run python -m yardsale.bureau_server             # add --offline to skip Fetch's servers
uv run python -m yardsale.buyer_test --count 12     # end-to-end test; needs the server running
uv run python -m yardsale.sell photo.jpg            # photo listing; needs .env keys and the server
uv run ruff format --check . && uv run ruff check . && uv run mypy yardsale
uv run pytest                                       # no keys or network needed
```

Claude Code sets `ANTHROPIC_BASE_URL` for itself. Start the server with
`env -u ANTHROPIC_BASE_URL uv run python -m yardsale.bureau_server` when running it from a Claude
Code shell.

## Rules

- Prices are integer cents.
- The Appraiser's price comes from code (`appraise()`), not from Claude. Claude only judges which
  eBay sales match.
- Claude reads photos; ASI:One (`asi1-mini`) only reads typed text. ASI:One's docs don't list image
  input.
- ASI:One's planner gives up on a reply after about 60 seconds. Chat replies must go out within
  `chat.REPLY_WITHIN` (40 s); slower work is sent as a second message.
- `sell`, chat photo listings, and ASI:One calls spend real money or quota (Apify, Claude, ASI:One).
  Ask before running them.
- No code moves money yet. A `Deal` is only an agreed price.
- Agent seeds live in `.agent-data/` (gitignored). Never print or commit them.
- Live mode publishes agent entries to Fetch's public Almanac. Use `--offline` for routine checks.
