# Yardsale

Product idea, decisions already made, and open questions:

@docs/background.md

## Commands

```bash
uv sync
uv run python -m yardsale.bureau_server             # add --offline to skip Fetch's servers
uv run python -m yardsale.buyer_test --count 12     # end-to-end test; needs the server running
uv run ruff format --check . && uv run ruff check . && uv run mypy yardsale
```

## Rules

- Prices are integer cents.
- No code moves money yet. A `Deal` is only an agreed price.
- Agent seeds live in `.agent-data/` (gitignored). Never print or commit them.
- Live mode publishes agent entries to Fetch's public Almanac. Use `--offline` for routine checks.
