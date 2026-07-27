# app/campaign_control.py

## Overview

Redis-backed active-campaign + per-worker persona assignment — the
mechanism that makes switching campaigns a first-class runtime operation
instead of a config edit + redeploy (campaign_platform_contract.md §8,
docs/campaign_platform_build.md's "Blank workers + runtime persona
assignment", docs/blank_workers.md). Lives in the SAME Redis instance as
`app/worker_control.py`'s `worker:{id}:enabled` flag, with two more key
shapes:

```
campaign:active            JSON {"campaign":..., "cast": {...}, "started_at":...}
worker:{worker_id}:persona JSON {"campaign":..., "speaker":...}
```

`CampaignControl` mirrors `WorkerControl`'s shape on purpose: identical
Redis client construction (same `socket_timeout`, same
`decode_responses=True`), the same `from_config` classmethod, and the same
fail-open-on-read / propagate-on-write split (docs/worker_control.md).

**This module never reads a persona FILE.** It only ever stores
`{campaign, speaker}` pairs in Redis. Each worker resolves its own persona
DOC from its own mounted config at
`config/campaigns/<campaign>/personas.yaml` — see `app/agent.py`'s
`resolve_persona`/`_load_persona_doc` below. This split is what keeps
`services/message-api` dumb: it can assign personas to workers without
ever knowing what a persona actually contains.

### `config/campaigns/*/personas.yaml` and the fate of `config/workers/*.yaml`

`config/campaigns/coder/personas.yaml` (six speakers: `coder`/`coder-native`/
`coder-opencode`/`coder-aider`/`manager`/`tester`) and
`config/campaigns/dnd/personas.yaml` (`gm`/`thorin`/`sable`, matching
`config/campaigns/dnd/primitives.yaml` + `narration.yaml`'s cast) are the
two shipped persona tables. Each speaker entry is
`{name, title, role, system_prompt, voice: {...}, avatar: {...}}`
(campaign_platform_contract.md §8) — `app/agent.py` overlays `name`/`role`/`system_prompt`,
`app/avatar.py` rebuilds its provider from `avatar:` verbatim, and
`app/replay_pane.py` deep-merges `voice:` onto its own config.

The coder campaign's six personas were lifted **verbatim** from the six
old per-worker files at `config/workers/{coder,coder-native,coder-opencode,
coder-aider,manager,tester}.yaml` — same `agent.name`/`system_prompt`,
same `voice.model_path`, same `avatar.name`/`title`/`expressions`. Those
six files are **left on disk, unmodified** (docs/blank_workers.md already
made this call when it stopped bind-mounting them per-worker) — they are
no longer the source of truth for what a persona looks like, but remain
useful as historical/local-dev reference and as the paper trail for how
each persona was originally authored. `config/campaigns/coder/personas.yaml`
is the source of truth from here on; if the two ever drift, the personas
file wins.

### End-to-end flow

```
operator ──POST /campaigns/{campaign}/start──▶ message-api
                                                    │ 409 if any cast worker is
                                                    │ mid-airing and force≠true
                                                    │ (reads worker:{id}:airing)
                                                    ▼
                                     campaign_control.start(campaign, cast)
                                         writes campaign:active
                                         writes worker:{id}:persona per cast entry
                                                    │
                    ┌───────────────────────────────┴────────────────────────┐
                    ▼ (next tick, every worker)                              ▼
       app/agent.py: resolve_persona(campaign_control, worker_id)   (persists in Redis
            reads worker:{id}:persona, then loads THIS worker's       until /stop or a
            OWN config/campaigns/<campaign>/personas.yaml[speaker]    new /start call)
                    │ overlays agent.name/role/system_prompt
                    │ writes /tmp/persona.json (PERSONA_FILE)
                    ▼
       app/avatar.py + app/replay_pane.py POLL /tmp/persona.json
            (never touch Redis/Kafka directly — docs/duet_replay.md)
            avatar.py: rebuilds its provider from persona["avatar"]
            replay_pane.py: deep-merges persona["voice"] onto its config,
                            overlays "campaign" so the next airing's
                            default library follows the new persona
```

**No persona assigned == disabled.** A worker with no persona resolved is
treated by `app/agent.py`'s tick loop EXACTLY like `WorkerControl.is_enabled()`
returning `False` — same `write_state(..., "idle", "disabled by operator")` /
sleep / `continue` branch, not a new "blank" mode
(docs/blank_workers.md).

### What genuinely hot-swaps, and what does not

`docs/campaign_platform_build.md`'s "what hot-swaps" table is the design
intent; this is the as-shipped reality, confirmed by what actually got
built:

| Attribute | Hot-swaps? | Mechanism |
|---|---|---|
| Agent system prompt / role / name | ✅ | `app/agent.py`'s tick loop re-reads the persona every tick (`resolve_persona` + `apply_persona`) |
| Avatar name, title, expression glyphs, provider | ✅ | `app/avatar.py` polls `/tmp/persona.json` and rebuilds its provider on change |
| Piper voice model | ✅ | `app/replay_pane.py` deep-merges the persona's `voice:` block onto its config; `tts_client._LOCAL_VOICES` is already keyed by resolved path, so no unload step is needed — the NEXT airing just builds a `TTSClient` against the new path |
| Default episode/campaign library | ✅ | `apply_persona_to_config` also overlays `campaign`, so a request that doesn't name one explicitly follows the newly assigned persona, not whatever was true at container boot |
| **Stream key / RTMP target** | ❌ **not actually wired** | `startup.sh` resolves `STREAM_KEY`/`STREAM_RTMP_URL` from env **once** at container boot and passes them as fixed CLI args into `stream_supervisor.py`'s `build_ffmpeg_cmd()`. `stream_supervisor.py`'s only per-poll-tick re-check is `WorkerControl.is_enabled()` (start/stop the ffmpeg child) — it never re-resolves the stream key or RTMP url itself. Changing which stream key a worker publishes to still requires a container restart today, contrary to `docs/campaign_platform_build.md`'s optimistic ✅ for this row. |
| **Layout preset** | ❌ **not actually wired** | `app/build_layout.py` runs exactly once, invoked by `startup.sh` at container boot, to build the initial tmux session. No existing entrypoint tears down and rebuilds a running session. `docs/campaign_platform_build.md`'s own table already marks this ⚠️ ("needs a tmux session rebuild") — that rebuild mechanism was never built as part of this work; it remains a container restart today, same as the stream key. |

Both gaps share the same root cause: `startup.sh` resolves them into fixed
values once, before `app/agent.py`/`app/avatar.py`/`app/replay_pane.py`
even start — there is no relay file or Redis key for either one to poll.
Closing them would mean giving `stream_supervisor.py` a persona-aware
re-resolution (mirroring this module's relay-file pattern) and building
the "kill the tmux session, re-run `build_layout.py`" mechanism
`docs/campaign_platform_build.md` describes but does not implement.
Neither was in scope for this change.

## Signature

```python
CAMPAIGN_KEY = "campaign:active"

def persona_key(worker_id: str) -> str          # f"worker:{worker_id}:persona"

class CampaignControl:
    def __init__(self, redis_url: str, socket_timeout: int = 2)

    @classmethod
    def from_config(cls, config: dict | None = None) -> "CampaignControl"

    def get_active(self) -> dict | None
    def get_persona(self, worker_id: str) -> dict | None
    def start(self, campaign: str, cast: dict[str, str], *, force: bool = False) -> dict
    def stop(self) -> None
```

Worker-side resolution (`app/agent.py`, not this module):

```python
def resolve_persona(campaign_control, worker_id, campaigns_dir=None) -> tuple | None
def apply_persona(base_agent_config: dict, persona: dict) -> dict
def write_persona_file(campaign: str, speaker: str, persona: dict) -> dict
```

## Parameters

- `config` (dict, optional) — a loaded worker config; `resolve_redis_url`
  (imported from `worker_control.py`, not reimplemented) reads
  `config["world_state"]["redis_url"]` as a fallback, same as
  `WorkerControl.from_config`.
- `redis_url` / `socket_timeout` — identical meaning to
  `WorkerControl.__init__` (docs/worker_control.md).
- `worker_id` (str) — matches `WORKER_ID`/`message_bus.worker_id`
  (`worker-1`..`worker-8` under the generic-worker fleet,
  docs/blank_workers.md).
- `campaign` (str) — a `config/campaigns/<name>/` directory name (`coder`,
  `dnd`, ...). Any string is accepted; an unknown campaign simply means no
  worker will ever resolve a persona doc for it.
- `cast` (dict[str, str]) — `{speaker_id: worker_id}`, e.g.
  `{"coder": "worker-1", "manager": "worker-5"}`. Speaker ids must match
  keys in that campaign's `personas.yaml`.
- `force` (bool, default `False`, `start` only) — accepted to match the
  frozen call signature and the `POST /campaigns/{campaign}/start` request
  body, but **not used by `CampaignControl.start` itself**. The mid-airing
  guard it exists for reads `worker:{id}:airing` (written by
  `app/replay_pane.py`) and is enforced one layer up, in
  `services/message-api/api.py`, BEFORE `start()` is ever called — see
  docs/replay_pane.md and docs/message_api.md.

## Return Value

- `get_active()` — `{"campaign": ..., "cast": {...}, "started_at": ...}`,
  or `None` when nothing is active, Redis is unreachable, or the stored
  value is corrupt (all three fail OPEN).
- `get_persona(worker_id)` — `{"campaign": ..., "speaker": ...}`, or `None`
  under the same fail-open conditions.
- `start(campaign, cast, force=False)` — the newly active campaign dict
  (same shape as `get_active()`'s return value).
- `stop()` — `None`. Clears `campaign:active` and every persona key it
  named.
- `resolve_persona(campaign_control, worker_id, campaigns_dir=None)` —
  `(campaign, speaker, persona_doc)`, or `None` when nothing is
  assigned/resolvable (fed straight into `app/agent.py`'s "treat like
  disabled" branch).
- `apply_persona(base_agent_config, persona)` — a NEW dict: `base_agent_config`
  with `name`/`role`/`system_prompt` overlaid from `persona` wherever the
  persona provides them; `base_agent_config` itself is never mutated.
- `write_persona_file(campaign, speaker, persona)` — the dict actually
  written to `/tmp/persona.json` (`persona` plus `campaign`/`speaker`/
  `updated_at`).

## Dependencies

- `redis` (already a project dependency; same package `worker_control.py`
  uses).
- `worker_control.resolve_redis_url` — reused directly rather than
  reimplemented, so Redis URL resolution can never drift between the two
  control classes.
- Consumed by `services/message-api/api.py` (the `/campaigns/*` routes)
  and `app/agent.py` (`resolve_persona`, called every tick right after the
  existing `WorkerControl.is_enabled()` check).
- `app/agent.py`'s worker-side resolution additionally depends on
  `config/campaigns/<campaign>/personas.yaml` (its own mounted copy —
  docker-compose.yml's `./config:/config:ro` mount, docs/blank_workers.md)
  and PyYAML.

## Usage Examples

```python
from campaign_control import CampaignControl

control = CampaignControl.from_config(config)

active = control.start("coder", {
    "coder": "worker-1", "coder-native": "worker-2", "coder-opencode": "worker-3",
    "coder-aider": "worker-4", "manager": "worker-5", "tester": "worker-6",
})
# -> {"campaign": "coder", "cast": {...}, "started_at": 1785000000.0}

control.get_persona("worker-1")
# -> {"campaign": "coder", "speaker": "coder"}

control.stop()  # every one of the six workers above goes back to blank
```

```bash
# Same effect via the HTTP API (services/message-api, docs/message_api.md)
curl -X POST http://localhost:8090/campaigns/coder/start \
  -H "Content-Type: application/json" \
  -d '{"cast": {"coder": "worker-1", "manager": "worker-5", "tester": "worker-6"}}'

curl http://localhost:8090/campaigns/active

curl -X POST http://localhost:8090/campaigns/stop
```

## Error Handling

- **Reads fail open.** `get_active`/`get_persona`: Redis unreachable, a
  missing key, or a corrupt stored value all return `None` — logged as a
  `[campaign_control] WARN ...` line, never raised. This is the OPPOSITE
  polarity from `WorkerControl.is_enabled`'s own fail-open default
  (`True`): when Redis can't be reached, "assume this worker is still
  enabled" is the safe default for an already-running stream, but "assume
  this worker has a persona" is not a safe guess — an inert/blank worker
  is the safer fallback than narrating in character as a persona that
  might not (any longer) be assigned to it.
- **Writes propagate.** `start`/`stop` do **not** fail open — a
  `redis.RedisError` propagates so `services/message-api/api.py` can 503
  the operator rather than silently reporting a campaign switch that
  didn't take. `stop()` reads `campaign:active` directly (not via
  `get_active()`, which fails open) specifically so a Redis outage
  propagates through the read half of `stop()` too.
- **`start()` clears personas a worker is no longer cast to.** A worker
  named in the PREVIOUSLY active campaign's cast but not in the new one's
  worker ids has its persona key deleted, going back to blank — otherwise
  it would keep performing its old persona forever. This is the one piece
  of behavior beyond a literal "write both key kinds" the frozen
  campaign_platform_contract.md §8 signature doesn't spell out; see `start()`'s own
  docstring.
- **Worker-side resolution never crashes the tick loop.** `resolve_persona`
  / `_load_persona_doc` (`app/agent.py`) treat a missing personas.yaml, a
  malformed YAML file, or a speaker id with no entry as "no persona" —
  logged, then handled by the SAME "treat like disabled" branch as no
  Redis assignment at all.
- **The relay file write is best-effort, not retried until the next real
  change.** If `write_persona_file` raises `OSError`, `app/agent.py` logs
  it and does NOT advance its internal "last written" key — the identical
  persona will be retried on the very next tick since nothing has "changed"
  from Redis's point of view, so a transient disk hiccup self-heals within
  one tick interval.

## Changelog

- v1.0.0 (2026-07-26) — Initial version. `CampaignControl` (four frozen
  methods: `get_active`/`get_persona`/`start`/`stop`), worker-side
  `resolve_persona`/`apply_persona`/`write_persona_file` in `app/agent.py`,
  the `/tmp/persona.json` relay file polled by `app/avatar.py` and
  `app/replay_pane.py`, and the `POST /campaigns/{campaign}/start` /
  `POST /campaigns/stop` / `GET /campaigns/active` routes in
  `services/message-api/api.py`.
