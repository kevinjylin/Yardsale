# Yardsale

Product idea, decisions already made, and open questions:

@docs/background.md

Cal Hacks 13.0 plan (the pitch, what's missing, and the build order; submissions close Sunday,
October 25 at 9 AM PT):

@docs/calhacks-plan.md

## Commands

```bash
uv sync
uv run python -m yardsale.bureau_server             # add --offline to skip Fetch's servers
uv run python -m yardsale.bureau_server --check     # which live-mode keys are set
uv run python -m yardsale.buyer_test --count 12     # end-to-end test; needs the server running
uv run python -m yardsale.sell photo.jpg            # photo listing; needs .env keys and the server
uv run ruff format --check . && uv run ruff check . && uv run mypy yardsale
uv run pytest                                       # no keys; fails if anything leaves localhost
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
- Nothing is for sale until the seller approves it. Buyers never see a floor price, and floors
  and chat text stay out of logs.
- The server always calls the real Claude, Apify, and ASI:One. Fakes live only in `tests/`
  (`tests/fakes.py`, `tests/sim.py`). Tests must stay offline: call `offline.stay_offline()` in
  anything new that creates agents without the network.
- Agent seeds live in `.agent-data/` (gitignored). Never print or commit them.
- Live mode publishes agent entries to Fetch's public Almanac. Use `--offline` for routine checks.
