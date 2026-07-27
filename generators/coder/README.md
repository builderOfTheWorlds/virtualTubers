# generators/coder/ — the coder campaign's episode generator

Turns a `claudeBackupUtility` session log directory (`conversation.md` +
`tool_NNN_<Tool>.md` detail files) into a validated coder-campaign episode
(`replays/coder/<episode-id>.json`) for the "Rerun Theater" replay pane.

Moved out of the platform from `app/session_log_parser.py` and
`scripts/build_replay_library.py` (`../../docs/campaign_platform_contract.md` §9 / `docs/
campaign_platform_build.md`) — see `generators/README.md` for why generators
live outside `app/` at all, and for the one sanctioned exception (importing
`app/episode_schema.py` to validate before writing).

## Files

| File | Role |
|---|---|
| `session_log_parser.py` | Parses `conversation.md` into a flat, fully-redacted list of raw events (`user_message` / `assistant_text` / `tool_call`). Unchanged in substance from its pre-move version — same 17 `REDACTION_RULES`, same order, same noise filtering. |
| `build_library.py` | Groups those raw events into campaign-platform **scenes**, maps each event to a coder primitive's `render[]` entry, validates the result, and writes `replays/coder/<episode-id>.json`. This is the new code — the old script only ever wrote the flat pre-schema shape. |

## Usage

```bash
.venv/Scripts/python.exe generators/coder/build_library.py \
    --logs "path/to/logs/claude/virtualTubers" --out replays/coder
```

| Flag | Default | Meaning |
|---|---|---|
| `--logs` | *(required)* | Directory of `<timestamp>_<id>` session log subdirectories. |
| `--out` | `replays/coder` | Episode library output directory. |
| `--min-events` | `5` | Skip sessions producing fewer than this many parsed events — nothing watchable in them. |
| `--worker-name` | `KODI-7` | On-screen coder display name, baked into every `show_coder_line` payload's `name` field (matches `app/replay.py`'s own default). |

Exits `0` when every session either wrote successfully or was skipped as
too-thin; exits `1` (and writes nothing for the offending session) if any
session fails to parse, fails the `LEAK_AUDIT` re-scan, or fails
`episode_schema.validate_episode`. Prints one line per session
(`ok` / `FAIL` / `LEAK` / `INVALID`) plus a summary line, matching the
original script's console-output shape.

## What it does, in order, per session

1. **Parse** — `session_log_parser.parse_session(session_dir)` returns the
   old flat shape: `{source, project, session_id, date, events: [...]}`,
   fully redacted. Sessions with fewer than `--min-events` events are
   skipped before anything else runs.
2. **Group into scenes** — `events_to_scenes()` (the moved-and-adapted
   `app/revoice.py::plan_scenes`, deleted from the platform, recovered from
   git history) walks the flat event list:
   - one `user_message` → one `kind: boss` scene
   - one `assistant_text` → one `kind: coder_talk` scene
   - a run of consecutive `tool_call`s by the same speaker → one
     `kind: coder_work` scene, capped at `MAX_SCENE_EVENTS` (8, the old
     `plan_scenes` constant) as a first-pass grouping heuristic
   - unknown event types are dropped, exactly as `plan_scenes` did
3. **Split oversized work scenes against the real budget** —
   `_split_oversized_work_scene()` estimates each `coder_work` scene's
   screen time with `app/primitives.py::estimate_scene_seconds` (the same
   function the platform's own validator uses for
   `limits.max_scene_seconds`) and recursively halves the event chunk until
   every resulting scene fits that budget. This is deliberately **not** a
   second hardcoded cap — it reads `config/validation.yaml`'s own
   `limits.max_scene_seconds` at startup, so the generator's cap can never
   drift out of sync with what the validator will actually enforce.
4. **Map events to primitives** — `event_to_render(event, worker_name)`
   implements the seven-case table from `../../docs/campaign_platform_contract.md` §9b:

   | Raw event | Primitive | Payload |
   |---|---|---|
   | `user_message` | `show_boss_message` | `{text}` |
   | `assistant_text` | `show_coder_line` | `{text, name}` |
   | `tool_call` (Bash/PowerShell) | `show_command` | `{command, output?, error?}` |
   | `tool_call` (Edit) | `show_diff` | `{file, hunks, error?}` |
   | `tool_call` (Write) | `show_write` | `{file, content, error?}` |
   | `tool_call` (Read) | `show_read` | `{file, error?}` |
   | `tool_call` (anything else) | `show_tool` | `{tool, summary, error?}` |

   `hunks` is every pre-edit line prefixed `"- "`, then every post-edit line
   prefixed `"+ "`, concatenated — **not** a real unified diff; this is what
   lets `app/primitives.py`'s `diff` behavior reproduce the old
   scroll-then-type split from one field. `show_tool`'s `summary` is
   pre-truncated to 100 characters here (the recipe layer does not truncate
   template tokens itself). Every field comes from
   `session_log_parser`'s already-redacted, already-inlined `detail` dict —
   nothing is ever copied from the raw event wholesale, so the leftover
   `detail_file` pointer key never has a chance to reach the episode.
5. **Assemble the episode** — `build_episode()` wraps the scene list in
   `meta` (`schema: 1`, `campaign: "coder"`, `id` = the session directory
   name, `title`, `created`) and a top-level `cast` (every speaker actually
   used across the scenes — a sibling of `meta`, never nested inside it).
6. **Validate before writing** — `normalize_episode()` then
   `episode_schema.validate_episode(..., kinds=load_narration_kinds())`
   against the real `config/campaigns/coder/primitives.yaml`,
   `config/validation.yaml`, and `config/campaigns/coder/narration.yaml`
   (for `unknown_kind`). Any reject-level `Issue` fails that session; warn-
   level issues are printed but don't block the write.
7. **`LEAK_AUDIT` re-scan** — before validation, the fully serialized
   episode JSON is re-scanned with the same stricter, standalone regex the
   pre-move `scripts/build_replay_library.py` used (shorter fixed-length
   prefixes than `session_log_parser`'s own `REDACTION_RULES`, tolerant of
   JSON string-escaping) — a second, independent check that a leak surviving
   `session_log_parser.redact()` still gets caught before the episode ever
   touches disk. A match refuses the write and fails the whole run.
8. **Write** — `replays/coder/<session-directory-name>.json`.

## Text fields: `text` and `fallback`

- `boss`/`coder_talk` scenes: `text` is the event's own (redacted) line
  verbatim; `fallback` is left empty, since `config/campaigns/coder/
  narration.yaml`'s `fallback_template: "{text}"` for both kinds reconstructs
  the same content at showtime if narration ever needs a fallback.
- `coder_work` scenes: both `text` and `fallback` are set to the same
  deterministic one-line summary of the chunk's tool actions (`"Okay —
  running pytest -x, then editing app/login.py."`), built by
  `_summarize_work_events()` — recovered in spirit from the deleted
  `app/revoice.py::fallback_narration()`. This is a self-contained, always-
  speakable last resort that doesn't depend on `render_summary()` correctly
  reconstructing something meaningful from the render entries at showtime.

## Safety properties preserved from the pre-move code

- All 17 `REDACTION_RULES` in `session_log_parser.py`, byte-for-byte, in the
  same order (specific token shapes first, generic patterns last, the
  literal-username catch-all dead last).
- Private LAN IPs (RFC1918, loopback, link-local) stay readable; public and
  CGNAT/tailnet (`100.64.0.0/10`) addresses are redacted.
- The separate, stricter `LEAK_AUDIT` re-scan over the fully serialized
  episode, with refuse-to-write + nonzero-exit behavior on any match.

## Tests

`tests/test_coder_generator.py` (moved from `tests/test_session_log_parser.py`)
covers all of the above: every original redaction/parsing test, the
event-to-primitive mapping for all seven cases, sidecar inlining, the
`LEAK_AUDIT` refusal path, scene-grouping/budget-splitting behavior, and an
end-to-end pass through `episode_schema.validate_episode` under the real
coder campaign config.
