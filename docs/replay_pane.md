# replay_pane

## Overview

Long-lived tmux pane program for **Rerun Theater** — the stream feature
that re-performs past real dev sessions as shows. It idles with an episode
listing and performs an episode (via [replay.md](replay.md)) whenever the
agent drops a request file.

The full wiring, operator to screen:

```
operator ──POST /messages──▶ Kafka ──▶ agent.py handle_replay_request
                                            │ writes REPLAY_REQUEST_FILE (atomic)
                                            ▼
                              replay_pane.py (this program, polling)
                                            │ resolves episode INSIDE library
                                            ▼
                              Performer renders the show + avatar reacts
```

File-based handoff on purpose (same pattern as `agent_state.py`): the pane
never **consumes** Kafka and never executes anything from the bus. The only
thing a bus message can influence is **which pre-built, pre-redacted
episode in the library plays** — episode names are resolved basename-only
inside `REPLAY_LIBRARY`, so a hostile payload cannot reach other files.

**Spoken narration.** When the worker config's `voice.provider` isn't
`null`, each airing runs the per-airing narration pass first
([revoice.md](revoice.md)): the pane prints "preparing" progress while the
LLM writes boss/coder lines and TTS synthesizes them, then performs the
episode with audio-anchored pacing ([replay.md](replay.md)). Voice being
unconfigured, or broken at showtime, degrades to the silent performance —
an episode always airs. A request can force a silent airing with
`"voice": false` in the payload.

**Narration transcript.** The pane does **produce** to Kafka: right after a
voiced show is prepared, `publish_narration` sends one `replay_narration`
message (episode, aired-at timestamp, and every scene's speaker + spoken
text) which `message-logger` persists to Postgres's `voiced_narration`
table (docs/message_logger.md). Publishing is fire-and-forget: no
`message_bus` config, or Kafka being unreachable, just skips it silently —
never delays or blocks the show.

**Narration + audio cache, and reuse.** Right after publishing, the pane
also calls `persist_narration`, which upserts the **full** airing — text
plus the synthesized WAV bytes and measured duration — directly into the
same `voiced_narration` table via `app/narration_store.py`
(docs/narration_store.md), reusing the same `message_id` `publish_narration`
minted so the two writes converge on one row set regardless of which lands
first. A `replay_request` with `payload.narration: "reuse"`
(docs/operator_commands.md) then has `load_reused_show` rebuild a voiced
show from the latest cached airing of that episode — scenes replanned
deterministically with `revoice.plan_scenes`, cached text and WAVs
reattached from the workdir — instead of calling the LLM + TTS again. Both
the save and the reuse are best-effort against the show-must-air rule
(docs/revoice.md): no `POSTGRES_*` env, no `psycopg2`, a down database, an
episode that's never been cached, or a cached scene structure that no
longer matches the current script all just fall back to (or skip) a fresh
generation, logged to stderr, never a crash or a stalled show. `"voice":
false` skips reuse too, same as it skips fresh narration.

## Signature

```python
def resolve_episode(library, episode) -> Path | None
def read_request(request_file) -> dict | None      # consume-once
def perform_request(request, library, worker_name, state_path,
                    default_speed=1.0, config=None) -> bool
def prepare_voice(script, config, workdir, worker_name, speed) -> list | None
def publish_narration(show, config, episode, worker_name) -> str | None
def persist_narration(message_id, show, config, episode, worker_name) -> None
def load_reused_show(script, episode, workdir) -> list | None
def load_worker_config(path) -> dict | None
def list_episodes(library) -> list[str]
```

## Parameters (CLI / environment)

- `--library` / `REPLAY_LIBRARY` (default `/data/replays`): episode script
  directory — mounted `:ro` from `/opt/virtualTubers/replays` in
  `docker-compose.yml`.
- `--request-file` / `REPLAY_REQUEST_FILE` (default
  `/tmp/replay_request.json`): the agent → pane handoff file. Same value
  must be visible to `agent.py` (same container, both default it).
- `--worker-name` / `WORKER_ID` (default `worker`): persona name on
  dialogue lines when the request doesn't override it.
- `--config` / `CONFIG_PATH` (default `/config/worker.yaml`): worker config
  whose `voice` + `llm` sections drive spoken narration; missing/unreadable
  file, or `voice.provider: "null"`, means silent shows.
- `--once`: handle at most one pending request then exit (testing).
- `POSTGRES_DB` / `POSTGRES_USER` / `POSTGRES_PASSWORD` (env, required for
  narration caching), `POSTGRES_HOST` / `POSTGRES_PORT` (env, optional —
  default `localhost:5432`): read by `app/narration_store.py`
  (`available()`/`_connect()`), not by this file directly. Granted to
  `worker-coder`/`worker-manager`/`worker-tester` in `docker-compose.yml`.
  Missing/wrong values just disable narration caching and reuse — the show
  still airs (docs/narration_store.md).

## Return Value

`main()` runs forever (pane lifetime). Malformed requests are consumed and
logged — never a crash loop. A failed episode logs to stderr and returns
to idle.

`publish_narration` returns the published bus message's `id` (str) on
success, or `None` when the airing was skipped (no show) or the publish
failed/was unconfigured. `persist_narration` reuses that id — or mints its
own `uuid.uuid4()` when it's `None` — so the narration cache still works
even without a Kafka config; the cache save itself is void (best-effort,
logged to stderr on failure).

## Dependencies

`app/replay.py` (Performer + `prepare_voiced_show`), `app/agent_state.py`
(avatar state path), `app/message_bus.py` (`MessageProducer`/`build_message`,
for `publish_narration`), `app/narration_store.py` (`available`/
`save_airing`/`load_latest_airing`, for `persist_narration`/
`load_reused_show` — docs/narration_store.md), standard library; `yaml`
and (transitively, only when voice is on) `app/revoice.py` (`plan_scenes`
also used directly by `load_reused_show`) / `app/tts_client.py`
(`Narration`/`wav_duration`) / `app/llm_client.py`.

## Usage Examples

Operator: switch a worker into rerun mode (config-only), then request a show:

```bash
# 1. worker config: layout.preset: replay   (or LAYOUT_PRESET=replay env)
# 2. queue an episode on the running worker:
curl -X POST http://localhost:8090/messages \
  -H "Content-Type: application/json" \
  -d '{"to": "coder", "type": "replay_request",
       "payload": {"episode": "2026-07-02_04-27-00_6ecdde82", "speed": 1.5}}'
```

Operator: replay the same episode's most recent cached narration (no LLM,
no TTS) instead of generating fresh dialogue (docs/operator_commands.md):

```bash
curl -X POST http://localhost:8090/messages \
  -H "Content-Type: application/json" \
  -d '{"to": "coder", "type": "replay_request",
       "payload": {"episode": "2026-07-02_04-27-00_6ecdde82", "narration": "reuse"}}'
```

Build and ship the episode library (from the machine with the logs):

```bash
.venv/Scripts/python.exe scripts/build_replay_library.py \
  --logs "C:/Users/<you>/.../claudeBackupUtility/logs/claude/virtualTubers" \
  --out replays
# then sync replays/ to the host: /opt/virtualTubers/replays
```

## Error Handling

- Unknown episode → stderr report + `False`; pane returns to idle. The
  agent already confirmed queueing to the operator; check worker logs.
- Malformed request file → consumed and discarded (logged).
- Missing library dir → idle screen says so; nothing crashes.
- Avatar state write failures are non-fatal (see replay.md).
- `publish_narration` never raises: no `message_bus` config, a missing
  `bootstrap_servers`/`topic`, or a Kafka connection failure all just skip
  the publish (logged to stderr on the last one) — a transcript that
  didn't save must never cancel or delay the show itself.
- `persist_narration` never raises: `narration_store.available()` being
  `False` (no `POSTGRES_*` env, no `psycopg2`) skips the save with a
  stderr note; a save that raises inside `narration_store.save_airing`
  (DB down, query error) is caught and logged — the airing already played,
  so a caching failure must never look like a failed show.
- `load_reused_show` never raises: an unavailable store, an episode never
  cached, a load failure, or a cached scene structure that no longer
  matches the current script's `plan_scenes` output (scene count or
  `scene_kind` mismatch — e.g. the episode script was rebuilt) all log to
  stderr and return `None`, which `perform_request` treats exactly like a
  request without `narration: "reuse"`: it falls through to
  `prepare_voice` for a fresh airing.

## Changelog

- **v1.3.0** (2026-07-12): Narration + audio caching and reuse —
  `persist_narration` upserts the full airing (text, WAV bytes, measured
  duration) into `voiced_narration` via the new `app/narration_store.py`,
  reusing `publish_narration`'s `message_id`. A `replay_request` with
  `payload.narration: "reuse"` has `load_reused_show` rebuild the show
  from the latest cached airing (scenes replanned with
  `revoice.plan_scenes`, cached text/WAVs reattached) instead of a fresh
  LLM + TTS pass, falling back to fresh generation whenever nothing usable
  is cached. Needs `POSTGRES_*` env (added to `worker-coder`/
  `worker-manager`/`worker-tester` in `docker-compose.yml`) and
  `psycopg2-binary` in the worker image. See docs/narration_store.md.
- **v1.2.0** (2026-07-12): Narration transcript persistence —
  `publish_narration` sends the airing's spoken lines (text only, no
  audio) as a `replay_narration` bus message after each voiced show, for
  `message-logger` to durably unpack into Postgres's `voiced_narration`
  table (see docs/message_logger.md). Fire-and-forget: a down/unconfigured
  bus never blocks or fails the airing. +6 tests.
- **v1.1.0** (2026-07-12): Spoken narration — reads the worker config
  (`--config`/`CONFIG_PATH`), runs the per-airing revoice pass before each
  show, `"voice": false` request override, silent-show degradation on any
  voice failure. +4 tests.
- **v1.0.0** (2026-07-12): Initial version — idle screen with episode
  listing, request-file polling, traversal-safe episode resolution,
  `--once` test mode. Wired to `agent.py` `replay_request` +
  `config/panels/replay.yaml` + `config/layouts/replay.yaml`.
