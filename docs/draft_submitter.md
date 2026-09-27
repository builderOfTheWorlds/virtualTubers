# services/3layer-generator/draft_submitter.py

## Overview

Opt-in bridge from the offline 3-layer generator to the Rerun Theater
**review queue**.

Before this module, generated content reached the stream only when someone
hand-ran a builder script (`.claude/prompts/build_generated_episode.py`,
`build_campaign_episode.py`, ...) or clicked Publish in the campaign manager
(`POST /publish/{run}/upload` on the generator API). With
`AUTO_SUBMIT_DRAFTS=true`, the generator's dispatcher
(`runner.dispatch_once`) hands every successfully written `episode.json` to
a `DraftSubmitter`, which POSTs it to message-api as
`POST /replays?status=draft&uploaded_by=3layer-generator`.

A **draft never airs** (docs/episode_store.md, "Review status"): it passes
the full `episode_validator` gate and is stored, but every worker read path
ignores it until an operator approves it in the control panel's **Drafts
awaiting review** list (docs/control_panel.md). So turning this on removes
the manual copy step without letting unreviewed content onto a stream.

Two invariants it keeps:

- **message-api stays the only writer of `replay_episodes`.** The submit
  goes over HTTP; the generator never connects to the app database (and its
  own generator Postgres on :5455 is not involved).
- **A submit failure never fails the job.** The episode is on disk either
  way and the manual upload path still works. `DraftSubmitter.__call__`
  never raises; every outcome is a dict the runner stores on the job's
  `result.auto_submit`.

Default **off**: `from_env()` returns `None` unless `AUTO_SUBMIT_DRAFTS` is
truthy, and `runner.Context.submit_draft=None` means "never submit".

## Signature

```python
DEFAULT_MESSAGE_API_URL = "http://127.0.0.1:8090"
DEFAULT_TIMEOUT_S = 60.0
DEFAULT_UPLOADED_BY = "3layer-generator"

class DraftSubmitter:
    def __init__(self, message_api_url: str = DEFAULT_MESSAGE_API_URL,
                 timeout_s: float = DEFAULT_TIMEOUT_S,
                 uploaded_by: str = DEFAULT_UPLOADED_BY,
                 http_post=None) -> None
    url: str                                  # property: <message_api_url>/replays
    def __call__(self, episode_path, name: str | None) -> dict

def from_env(env: Mapping[str, str] | None = None) -> DraftSubmitter | None
```

Runner side (`services/3layer-generator/runner.py`):

```python
@dataclasses.dataclass
class Context:
    ...
    submit_draft: object = None    # callable (episode_path, name) -> dict, or None = off

def _auto_submit_draft(ctx, job_id, result) -> None   # records result["auto_submit"]
```

## Parameters

Environment (read by `from_env()`, wired in `docker-compose.yml`'s
`3layer-generator` service and `.env.example`):

| Variable | Default | Meaning |
|---|---|---|
| `AUTO_SUBMIT_DRAFTS` | `false` | `1`/`true`/`yes`/`on` (case-insensitive) enables auto-submit. Anything else, or unset, leaves it off. |
| `MESSAGE_API_URL` | `http://127.0.0.1:8090` | message-api base URL. Already used by the generator's manual `POST /publish/{run}/upload` proxy. The service uses host networking, so loopback + the published 8090 port is correct. |
| `AUTO_SUBMIT_TIMEOUT_S` | `60` | HTTP timeout. message-api dry-run renders every upload, so this is generous. A non-numeric or non-positive value falls back to 60 with a warning. |

`DraftSubmitter(...)`:

- `message_api_url` (str) — trailing `/` stripped.
- `timeout_s` (float) — passed to the HTTP call.
- `uploaded_by` (str) — stored on the row as attribution.
- `http_post` (callable, optional) — `(url, content, headers, params, timeout) -> response`
  seam for tests; defaults to a one-shot `httpx.Client`.

`__call__(episode_path, name)`:

- `episode_path` — the `episode.json` the publish stage wrote
  (`result["written_to"]`).
- `name` — the library key (`result["episode_name"]`, i.e. the publish
  job's `params.episode_name` or its run name). `None` lets message-api fall
  back to the script's `source` field.

The request is always `status=draft` and **never** `overwrite=true`: an
auto-submit can't replace, or pull off air, anything already in the
library. A name clash comes back as a recorded `409`.

## Return Value

`__call__` returns a dict, stored verbatim on the job as
`result["auto_submit"]`:

```python
{"status": "submitted", "name": "ep", "url": ".../replays", "http_status": 200}
{"status": "failed", "name": "ep", "url": ".../replays", "http_status": 409,
 "error": "episode 'ep' already exists — re-send with ?overwrite=true"}
{"status": "failed", "name": "ep", "url": ".../replays",
 "error": "message-api unreachable: ConnectError: ..."}
```

`error` is message-api's `detail` (or the raw body), capped at 500 chars.

`from_env()` returns a `DraftSubmitter`, or `None` when disabled.

When it runs: `runner.dispatch_once` calls `_auto_submit_draft` only after
a job **completed** (not failed, not cancelled), only when
`ctx.submit_draft` is set, and only when the job actually built an episode
(`result["written_to"]` — today only the `publish` stage). It runs before
`store.finish(...)`, so the outcome lands on the same job row. When
auto-submit is off, `result` has no `auto_submit` key at all.

## Dependencies

- `httpx` (already in `services/3layer-generator/requirements.txt`),
  imported lazily.
- Standard library: `logging`, `os`, `pathlib`.
- message-api `POST /replays?status=draft` (docs/message_api.md v1.6.0).
- Wired by `runner.build_default_context()`; copied flat into the image by
  `services/3layer-generator/Dockerfile`.

## Usage Examples

Turn it on for the stack:

```bash
echo "AUTO_SUBMIT_DRAFTS=true" >> .env
docker compose up -d --build 3layer-generator
# Queue a publish job as usual; when it completes, check the job row:
curl -sS http://localhost:8001/jobs/<job_id> | python3 -m json.tool   # result.auto_submit
# ...then approve or delete it in the control panel (http://localhost:8091).
```

Use it directly (e.g. from a one-off script), with a fake transport in tests:

```python
import draft_submitter

sub = draft_submitter.DraftSubmitter("http://127.0.0.1:8090", timeout_s=30)
outcome = sub("utilities/3LayersWeeklyGeneration/output/run-1/episode.json",
              "ashiorid_generated_run-1")
if outcome["status"] != "submitted":
    print("not submitted:", outcome.get("http_status"), outcome["error"])
```

## Error Handling

`__call__` never raises. Each failure is logged and returned as
`{"status": "failed", ...}`:

- episode file unreadable → `error: "cannot read <path>: ..."`, no request sent (ERROR log).
- transport failure (refused, DNS, timeout; `httpx.RequestError`) → `error: "message-api unreachable: ..."` (ERROR log).
- any other exception from the transport → `error: "message-api error: ..."` (ERROR log).
- non-2xx response → `http_status` + message-api's `detail`: `400` validator rejection (leak audit, bad shape, failed dry-run render — the detail never quotes episode content), `409` name already exists, `503` Postgres unavailable (WARN log).

`runner._auto_submit_draft` additionally wraps the call in `try/except`
and coerces a non-dict return, so even a misbehaving injected submitter
only produces a recorded failure. The job's own status is decided before
auto-submit runs and is never changed by it.

## Changelog

- v1.0.0 (2026-09-27) — Initial version. `DraftSubmitter`, `from_env()`,
  `runner.Context.submit_draft` + `runner._auto_submit_draft`; env vars
  `AUTO_SUBMIT_DRAFTS` (default off), `AUTO_SUBMIT_TIMEOUT_S`, reusing
  `MESSAGE_API_URL`.
