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
table (docs/message_logger.md). This is the only durable record of what
was said — the synthesized WAVs themselves live in a `TemporaryDirectory`
that's deleted the moment the show ends, and get regenerated fresh (with
new dialogue) on the next airing. Publishing is fire-and-forget: no
`message_bus` config, or Kafka being unreachable, just skips it silently —
never delays or blocks the show.

## Signature

```python
def resolve_episode(library, episode) -> Path | None
def read_request(request_file) -> dict | None      # consume-once
def perform_request(request, library, worker_name, state_path,
                    default_speed=1.0, config=None) -> bool
def prepare_voice(script, config, workdir, worker_name, speed) -> list | None
def publish_narration(show, config, episode, worker_name) -> None
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

## Return Value

Runs forever (pane lifetime). Malformed requests are consumed and logged —
never a crash loop. A failed episode logs to stderr and returns to idle.

## Dependencies

`app/replay.py` (Performer + `prepare_voiced_show`), `app/agent_state.py`
(avatar state path), `app/message_bus.py` (`MessageProducer`/`build_message`,
for `publish_narration`), standard library; `yaml` and (transitively, only
when voice is on) `app/revoice.py` / `app/tts_client.py` / `app/llm_client.py`.

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

## Changelog

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
