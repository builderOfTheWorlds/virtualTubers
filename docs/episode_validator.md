# episode_validator.py

## Overview

The gate every episode passes before it enters the library. `message-api`'s
`POST /replays` (docs/message_api.md) runs this on an uploaded episode
script and refuses to store anything that fails it, so `replay_episodes`
(docs/episode_store.md) only ever holds episodes that are well-formed,
redacted, and known to render.

This is the piece the old workflow had no equivalent of. Episodes used to
reach the workers by being copied onto the deploy host, which meant a
malformed or unredacted JSON dropped into `replays/` was discovered only
when it failed — or leaked — live on stream. Four stages now run first, in
order:

1. **Shape** — the canonical key set `session_log_parser.parse_session`
   produces, plus per-event-type required fields.
2. **Name** — basename-only and character-restricted. Preserves the
   traversal-safety property `replay_pane.resolve_episode` used to get for
   free from being a filesystem lookup.
3. **Leak audit** — `session_log_parser.audit`, the *same* strict regex
   `scripts/build_replay_library.py` applies locally
   (docs/session_log_parser.md). The dev box running it is now a
   convenience; the server is the last line of defense.
4. **Dry run** — actually render the whole episode through
   `replay.Performer` into a throwaway `StringIO` with pacing disabled, and
   group it with `revoice.plan_scenes`. This is the "won't have issues
   replaying it" check: an episode that crashes the renderer is rejected
   here rather than on air.

Stage 4 is only feasible because `app/replay.py` and `app/revoice.py` are
stdlib-only at import time — their `llm_client`/`tts_client`/`yaml` imports
are lazy, inside `prepare_voiced_show`/`main` — so the renderer runs inside
the `message-api` image without dragging the whole worker stack in.

**Security:** a leak-audit failure must never echo the matched text — it is,
by construction, the secret. `audit()` returns the match, but this module
only branches on it: the `EpisodeInvalid` it raises names the *categories*
the audit covers (credential-, token-, tailnet-IP- and username-shaped
text) and tells the operator to rebuild with
`scripts/build_replay_library.py`, so the redaction runs. It never quotes
the match, the rule, or the surrounding event.
`tests/test_episode_validator.py` asserts a planted secret does not appear
in the raised message.

## Signature

```python
MIN_EVENTS = 5
MAX_BYTES = 8 * 1024 * 1024
REQUIRED_KEYS = ("source", "project", "session_id", "date", "events")
REQUIRED_EVENT_FIELDS = {
    "user_message":   ("text",),
    "assistant_text": ("text",),
    "tool_call":      ("tool",),
}
NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")

class EpisodeInvalid(ValueError): ...

def resolve_name(script, override=None) -> str

def validate_episode(script, name=None) -> dict
```

## Parameters

- `script` (dict, required) — the parsed episode JSON, exactly as
  `session_log_parser.parse_session` emits it and
  `scripts/build_replay_library.py` writes it.
- `name` / `override` (str, optional) — overrides the library key. Defaults
  to `script["source"]`, which is what `build_replay_library.py` also uses
  as the filename stem, so an unmodified episode keeps the key every other
  part of the stack already knows it by.

## Return Value

`validate_episode()` returns `{"name", "event_count", "byte_size"}` — the
resolved library key, the number of events, and the size of the serialized
script in UTF-8 bytes. `message-api` returns these to the uploader and
`episode_store` stores the latter two as denormalized columns.

`resolve_name()` returns the validated name on its own. `message-api` calls
it directly for the `{name}` path parameter of `GET`/`DELETE /replays/{name}`,
so a lookup can never be handed something the upload path would have
refused.

Both raise `EpisodeInvalid` on failure. Nothing else is returned — a
successful call means all four stages passed.

## Dependencies

- `session_log_parser.audit` — the shared leak-audit regex
  (docs/session_log_parser.md).
- `replay.Pacer` / `replay.Performer` and `revoice.plan_scenes`, imported
  lazily inside `_dry_run` so a caller doing shape-only validation doesn't
  need the renderer present.
- Standard library: `io`, `json`, `re`.

## Usage Examples

The `message-api` upload path (`services/message-api/api.py`):

```python
from episode_validator import EpisodeInvalid, validate_episode

try:
    info = validate_episode(script, name=name)
except EpisodeInvalid as exc:
    raise HTTPException(status_code=400, detail=str(exc))
created = episode_store.save_episode(info["name"], script, overwrite=overwrite)
```

Checking an episode locally before uploading a batch, without a database:

```python
import json, sys
from pathlib import Path
sys.path.insert(0, "app")
from episode_validator import EpisodeInvalid, validate_episode

for path in sorted(Path("replays").glob("*.json")):
    try:
        info = validate_episode(json.loads(path.read_text(encoding="utf-8")))
        print(f"ok    {info['name']}: {info['event_count']} events")
    except EpisodeInvalid as exc:
        print(f"BAD   {path.name}: {exc}")
```

## Error Handling

Every failure raises `EpisodeInvalid`, whose message is operator-facing (it
becomes an HTTP 400 `detail`) and never quotes episode content:

| Condition | Message reports |
|---|---|
| Not a JSON object | the type that arrived |
| Missing `source`/`project`/`session_id`/`date`/`events` | which keys |
| `events` not a list | the type that arrived |
| Fewer than `MIN_EVENTS` (5) events | the count and the minimum |
| An event that isn't an object | the index and the type |
| An event `type` outside `user_message`/`assistant_text`/`tool_call` | the index and the offending type |
| An event missing its type's required field | the index, type, and field name |
| A name that isn't `^[A-Za-z0-9._-]{1,128}$`, or no name at all | the rule, not the name's content |
| Serialized script over `MAX_BYTES` (8 MB) | the size and the limit |
| Leak audit hit | **the categories audited and how to fix it** — never the match, the rule, or the event |
| Dry-run render or `plan_scenes` raising | the exception type and message, chained via `raise … from exc` |

Two deliberate choices:

- Stage order is shape → name → size → leak → dry run. The cheap structural
  checks run first so a truncated or wrong-schema upload gets a precise
  message instead of a confusing renderer traceback.
- An unknown event `type` is rejected rather than ignored, even though
  `Performer._perform_events` would silently skip it. Storing such an
  episode would air as dead air, which is worse than refusing the upload.

`MAX_BYTES` exists because uvicorn imposes no body-size limit of its own
and every upload is held in memory and then rendered; the largest real
episode is well under 1 MB. `message-api` applies the same cap to the raw
request body first, returning `413`.

## Changelog

- **v1.0.0** (2026-08-16): Initial version. Introduced with the move of the
  episode library from `/data/replays` into Postgres — `validate_episode()`
  (shape → name → size → leak audit → dry-run render) and `resolve_name()`,
  both wired into `services/message-api/api.py`'s `/replays` routes.
