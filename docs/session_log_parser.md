# session_log_parser — moved to `generators/coder/`

**This module no longer lives at `app/session_log_parser.py`.** As part of
the campaign platform build (`docs/campaign_platform_build.md`,
`campaign_platform_contract.md` §9, 2026-07-26), episode generation moved entirely out of the
platform: the platform performs a finished episode script, it does not
generate one. `app/session_log_parser.py` was **moved** (not copied — the
original is deleted) to `generators/coder/session_log_parser.py`, alongside
the new `generators/coder/build_library.py` (which replaces
`scripts/build_replay_library.py`, also moved/deleted).

**For the current reference doc, see [`generators/coder/README.md`](../generators/coder/README.md).**
It covers what `session_log_parser.py` does today (unchanged in substance:
same 17 `REDACTION_RULES`, same order, same noise filtering — see that
doc's "Safety properties preserved from the pre-move code"), how it feeds
`build_library.py`'s scene-grouping and event-to-primitive mapping, and how
the pair together turn a `claudeBackupUtility` session log into a validated
`replays/coder/<episode-id>.json` episode.

See also:

- [`generators/README.md`](../generators/README.md) — why episode
  generation lives outside `app/` at all, and the one sanctioned exception
  (a generator importing `app/episode_schema.py` to validate its own output
  before writing).
- [`docs/episode_schema.md`](episode_schema.md) — the validator every
  generator's output is checked against, on both the generator side and at
  platform ingest.
- [`docs/primitives.md`](primitives.md) — the rendering engine
  `build_library.py` maps parsed events onto (`show_command`, `show_diff`,
  etc.).
- [`docs/campaign_platform_build.md`](campaign_platform_build.md) — the
  full design doc for why this split exists.

## Why this page still exists

Older docs and changelog entries (including README.md's "Recent Changes"
log) link to `docs/session_log_parser.md` from before the move — this page
stays in place as a redirect so those links don't 404, rather than being
deleted outright.
