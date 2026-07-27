# replay_pane

## Overview

Long-lived tmux pane program for **Rerun Theater** — the stream feature
that performs campaign episodes as shows. It idles with an episode listing
and performs an episode (via [replay.md](replay.md)) whenever the agent
drops a request file.

The full wiring, operator to screen:

```
operator ──POST /messages──▶ Kafka ──▶ agent.py handle_replay_request
                                            │ writes REPLAY_REQUEST_FILE (atomic)
                                            ▼
                              replay_pane.py (this program, polling)
                                            │ resolves campaign, then episode
                                            │ INSIDE library/<campaign>/
                                            ▼
                              Performer renders the show + avatar reacts
```

File-based handoff on purpose (same pattern as `agent_state.py`): the pane
never **consumes** Kafka and never executes anything from the bus. The only
thing a bus message can influence is **which pre-built, pre-validated
episode in the library plays** — episode names are resolved basename-only
inside `REPLAY_LIBRARY/<campaign>/`, so a hostile payload cannot reach
other files or another campaign's episodes.

**Campaign namespacing (2026-07-26, campaign_platform_contract.md §7).** The library layout
is now `REPLAY_LIBRARY/<campaign>/<episode>.json` (was a flat
`REPLAY_LIBRARY/<episode>.json`). `resolve_campaign(request, config)`
picks which campaign a request resolves against — `request["campaign"]` >
the worker config's own `campaign:` field > env `REPLAY_CAMPAIGN` > the
hardcoded default `"coder"` — and every episode lookup, primitive-table
load, and narration-cache read/write is scoped to that campaign from then
on. This is deliberately NOT the same precedence `message_bus.resolve()`
uses everywhere else (env normally wins over config there, e.g. for Kafka
bootstrap servers): a worker's own configured campaign is a stronger,
more specific signal than a bare environment default, so only the last
tier (env vs. the hardcoded default) actually goes through
`message_bus.resolve()` — see `resolve_campaign`'s docstring.

Episodes are loaded through `app/episode_schema.py`'s `load_episode` — the
same validator a campaign generator's build pipeline runs before writing
an episode at all (docs/campaign_platform_build.md's "same config and
same engine on both sides"). A bad episode is refused at ingest (logged
loudly, never aired) rather than crashing the pane mid-show.

**Spoken narration.** When the worker config's `voice.provider` isn't
`null`, each airing runs the per-airing narration pass first
([revoice.md](revoice.md)): the pane prints "preparing" progress while the
LLM writes spoken lines and TTS synthesizes them, then performs the
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
plus the synthesized WAV bytes and measured duration, AND the resolved
`campaign` — directly into the same `voiced_narration` table via
`app/narration_store.py` (docs/narration_store.md), reusing the same
`message_id` `publish_narration` minted so the two writes converge on one
row set regardless of which lands first. A `replay_request` with
`payload.narration: "reuse"` (docs/operator_commands.md) then has
`load_reused_show` rebuild a voiced show from the latest cached airing of
that (episode, campaign) pair — `_rebuild_scenes_from_rows` zips the
cached rows against the episode's own `scenes[]` by index (no more
`revoice.plan_scenes` regrouping — an episode's scenes ARE the unit of
performance now), cached text and WAVs reattached from the workdir —
instead of calling the LLM + TTS again. Both the save and the reuse are
best-effort against the show-must-air rule (docs/revoice.md): no
`POSTGRES_*` env, no `psycopg2`, a down database, an episode that's never
been cached, or a cached scene structure that no longer matches the
current episode all just fall back to (or skip) a fresh generation,
logged to stderr, never a crash or a stalled show. `"voice": false` skips
reuse too, same as it skips fresh narration.

**Stopping a show (`replay_stop`).** An operator `replay_stop`
(docs/operator_commands.md) reaches `app/agent.py`'s `handle_replay_stop`,
which (1) deletes `REPLAY_REQUEST_FILE` if a request is still queued but
hasn't been picked up yet — cancelling it outright — and (2) writes
`REPLAY_STOP_FILE`, which every performance path here (`perform_request`,
`perform_director_request`, `perform_follower_request`) wires into its
`Performer`'s `Pacer(should_stop=...)` before it starts performing. A show
already in flight notices within a fraction of a second (checked on every
sleep and every typed character, docs/replay.md `ReplayStopped`) and shuts
down cleanly — avatar back to idle, no crash. Each performance path clears
any stale `REPLAY_STOP_FILE` from a *previous* airing before it starts and
again after it finishes, so a stop can never bleed into a later, unrelated
episode. A director additionally tells its followers the real reason
(`replay_end` `"finished"` vs `"stopped"`) and, if the stop lands before
every follower reported ready, refuses the airing outright with reason
`"stopped"` instead of waiting out the full `ready_timeout`.

**Duet replay (multi-worker airings).** A `replay_request` whose
`payload.cast` maps at least one speaker to a worker other than the
receiving one turns this pane into a **director**: it resolves the
campaign, prepares and persists the airing exactly as above, invites the
other cast workers over the bus (the invite payload carries the resolved
`campaign` so a follower whose own config/env might otherwise pick a
different default still loads the SAME episode), waits for all of them to
confirm ready, then paces every scene with a `replay_cue` published
immediately before performing it — refusing the whole airing outright
(never falling back to solo) if the narration store, Kafka, or a follower
isn't available in time. A request whose payload instead carries
`"mode": "follow"` (written by this worker's own `app/agent.py` on
receiving a `replay_invite`, never by an operator directly) makes this
pane a **follower**: it loads the SAME persisted airing, keeps audio only
for its own cast scenes, and performs scene-by-scene as `replay_cue`
messages authorize each one. Full protocol reference, message schemas,
timeouts, and deployment requirements: [docs/duet_replay.md](duet_replay.md).

**Runtime persona assignment (campaign_platform_contract.md §8, docs/campaign_control.md).**
`main()`'s idle loop also polls the agent → pane persona relay file
(`/tmp/persona.json`, env `PERSONA_FILE`, written by `app/agent.py`'s
`write_persona_file`) — same file `app/avatar.py` polls, never Redis or
Kafka directly. When the resolved `(campaign, speaker)` identity changes,
`apply_persona_to_config` deep-merges the persona's `voice:` block onto
this pane's own config (`voice.speakers` — needed for ANY worker to direct
a multi-persona duet — is left untouched since the persona's own `voice:`
never sets it) and overlays `campaign`, so the NEXT `perform_request` picks
up the new persona's Piper model and the worker's default episode library
follows the newly assigned campaign rather than whatever was true at
container boot.

**Mid-airing guard (campaign_platform_contract.md §8).** Every performance path
(`perform_request`'s solo branch, `perform_director_request`,
`perform_follower_request`) wraps its actual `Performer.perform(...)` call
in `with _airing_flag(config, self_id):`, which sets `worker:{id}:airing`
in Redis for the duration of the performance and clears it in a `finally`
— guaranteed even if the performance raises — so a crashed show can never
leave a worker permanently un-reassignable.
`services/message-api/api.py`'s `POST /campaigns/{campaign}/start` reads
this flag (fails OPEN: absent/unreachable means "not airing") to refuse a
mid-airing reassignment with HTTP 409 unless `force: true`
(docs/message_api.md, docs/campaign_control.md). This is the ONE piece of
persona-assignment state this pane itself writes to Redis; everything else
about personas is read-only local-file polling, above.

## Signature

```python
def resolve_campaign(request, config, default_campaign="coder") -> str
def resolve_episode(library, episode, campaign) -> Path | None
def read_request(request_file) -> dict | None      # consume-once
def perform_request(request, library, worker_name, state_path,
                    default_speed=1.0, config=None) -> bool
def prepare_voice(episode, config, workdir, worker_name, speed, campaign) -> list | None
def publish_narration(show, config, episode, worker_name) -> str | None
def persist_narration(message_id, show, config, episode, worker_name, campaign) -> None
def load_reused_show(episode, episode_name, workdir, campaign) -> list | None
def load_worker_config(path) -> dict | None
def list_episodes(library, campaign) -> list[str]

# Duet replay (docs/duet_replay.md)
def resolve_self_id(config, worker_name) -> str
def perform_director_request(request, library, worker_name, state_path, self_id,
                             default_speed=1.0, config=None) -> bool
def perform_follower_request(request, library, worker_name, state_path, self_id,
                             default_speed=1.0, config=None) -> bool

# Runtime persona assignment (campaign_platform_contract.md §8, docs/campaign_control.md)
def persona_identity(persona_doc) -> tuple | None
def apply_persona_to_config(config, persona_doc) -> dict
```

## Parameters (CLI / environment)

- `--library` / `REPLAY_LIBRARY` (default `/data/replays`): episode script
  directory root — mounted `:ro` from `./replays` (the repo root on the
  deploy host) in `docker-compose.yml`. Episodes live under
  `<library>/<campaign>/<episode>.json`.
- `--campaign` (default none): overrides the worker config's own
  `campaign:` field as this pane's DEFAULT campaign for requests that
  don't specify one, and for the idle screen's episode listing. Does not
  override a request's own explicit `"campaign"` field.
- `--request-file` / `REPLAY_REQUEST_FILE` (default
  `/tmp/replay_request.json`): the agent → pane handoff file. Same value
  must be visible to `agent.py` (same container, both default it).
- `--worker-name` / `WORKER_ID` (default `worker`): persona name on
  dialogue lines when the request doesn't override it.
- `--config` / `CONFIG_PATH` (default `/config/worker.yaml`): worker config
  whose `voice` + `llm` sections drive spoken narration, and whose
  optional `campaign:` field is this pane's default campaign; missing/
  unreadable file, or `voice.provider: "null"`, means silent shows.
- `--once`: handle at most one pending request then exit (testing).
- `REPLAY_CAMPAIGN` (env): last-resort default campaign when neither a
  request nor the worker config names one. See `resolve_campaign`.
- `POSTGRES_DB` / `POSTGRES_USER` / `POSTGRES_PASSWORD` (env, required for
  narration caching), `POSTGRES_HOST` / `POSTGRES_PORT` (env, optional —
  default `localhost:5432`): read by `app/narration_store.py`
  (`available()`/`_connect()`), not by this file directly. Missing/wrong
  values just disable narration caching and reuse — the show still airs
  (docs/narration_store.md). **Duet replay also requires this on every
  cast worker**, director and followers alike (docs/duet_replay.md) —
  without it a duet refuses outright rather than degrading.
- `REPLAY_STOP_FILE` (env, default `/tmp/replay_stop.json`): agent -> pane
  stop signal written by `app/agent.py`'s `handle_replay_stop` on an
  operator `replay_stop` (docs/operator_commands.md); this pane only ever
  polls it via each performance path's `Pacer(should_stop=...)` (see
  "Stopping a show" above; docs/replay.md `ReplayStopped`). Same
  env-override + atomic-write convention as `REPLAY_REQUEST_FILE`.
- `REPLAY_CUE_FILE` (env, default `/tmp/replay_cue.json`) /
  `REPLAY_READY_FILE` (env, default `/tmp/replay_ready.json`): duet relay
  files written by `app/agent.py`'s `handle_replay_cue`/`handle_replay_end`
  and `handle_replay_ready`; this pane only ever polls them
  (`_resolve_replay_cue_file`/`_resolve_replay_ready_file`). Same
  env-override + atomic-write convention as `REPLAY_REQUEST_FILE`. See
  docs/duet_replay.md.
- `REPLAY_READY_TIMEOUT_S` (env, default `60.0`): how long a duet
  **director** waits for every invited follower's `replay_ready` before
  refusing the airing (`reason: "ready_timeout"`). Not read by followers.
- `PERSONA_FILE` (env, default `/tmp/persona.json`): agent → pane persona
  relay file written by `app/agent.py`'s `write_persona_file`; this pane
  only ever polls it, in the idle loop, alongside the request/stop/cue
  files above (campaign_platform_contract.md §8, docs/campaign_control.md).
- `REDIS_URL` (env, default `redis://redis:6379`, same resolution as
  `worker_control.resolve_redis_url`): backs the `worker:{id}:airing`
  mid-airing-guard flag this pane WRITES around every performance (see
  "Mid-airing guard" above) — the one place this pane touches Redis at
  all.

Environment variables that only ever matter to `app/agent.py`'s side of
persona assignment (not read by this pane directly, but shape what shows
up in `PERSONA_FILE`'s contents) are documented in docs/agent.md and
docs/campaign_control.md.

## Return Value

`main()` runs forever (pane lifetime). Malformed requests are consumed and
logged — never a crash loop. A failed episode (unresolvable, or refused
by `episode_schema.load_episode`) logs to stderr and returns to idle.

`publish_narration` returns the published bus message's `id` (str) on
success, or `None` when the airing was skipped (no show) or the publish
failed/was unconfigured. `persist_narration` reuses that id — or mints its
own `uuid.uuid4()` when it's `None` — so the narration cache still works
even without a Kafka config; the cache save itself is void (best-effort,
logged to stderr on failure).

## Dependencies

`app/replay.py` (Performer + `prepare_voiced_show`), `app/episode_schema.py`
(`load_episode`/`EpisodeError` — episode loading and validation, frozen
per campaign_platform_contract.md §4), `app/primitives.py` (`load_primitives` — the merged
recipe table threaded into every `Performer` this pane builds),
`app/revoice.py` (`load_narration_config` — supplies `load_episode`'s
`kinds` param on every call, so `unknown_kind` can actually reject at
ingest; docs/episode_schema.md), `app/agent_state.py` (avatar state path),
`app/message_bus.py` (`MessageProducer`/`build_message`/`resolve`, for
`publish_narration` and campaign/bus resolution), `app/narration_store.py`
(`available`/`save_airing`/`load_latest_airing` — docs/narration_store.md),
`app/build_layout.py` (`deep_merge`, for `apply_persona_to_config`'s
`voice:` overlay), `app/worker_control.py` (`resolve_redis_url`, for the
mid-airing-guard Redis client — NOT `WorkerControl` itself), `redis`,
standard library; `yaml` and (transitively, only when voice is on)
`app/tts_client.py` (`Narration`/`wav_duration`) / `app/llm_client.py`.

## Usage Examples

Operator: switch a worker into rerun mode (config-only), then request a
show — omitting `"campaign"` resolves it per `resolve_campaign` (this
worker's configured campaign, or `REPLAY_CAMPAIGN`, or `"coder"`):

```bash
# 1. worker config: layout.preset: replay   (or LAYOUT_PRESET=replay env)
# 2. queue an episode on the running worker:
curl -X POST http://localhost:8090/messages \
  -H "Content-Type: application/json" \
  -d '{"to": "coder", "type": "replay_request",
       "payload": {"episode": "sample", "speed": 1.5}}'
```

Operator: request an episode from a specific (non-default) campaign, and
replay its most recent cached narration (no LLM, no TTS) instead of
generating fresh dialogue (docs/operator_commands.md):

```bash
curl -X POST http://localhost:8090/messages \
  -H "Content-Type: application/json" \
  -d '{"to": "coder", "type": "replay_request",
       "payload": {"episode": "s03e02-idra-tavern", "campaign": "dnd",
                    "narration": "reuse"}}'
```

Operator: duet airing — the receiving worker directs, another worker
follows and voices a different speaker (full protocol + deployment
requirements: docs/duet_replay.md):

```bash
curl -X POST http://localhost:8090/messages \
  -H "Content-Type: application/json" \
  -d '{"to": "coder", "type": "replay_request",
       "payload": {"episode": "sample",
                    "cast": {"boss": "manager", "coder": "coder"}}}'
```

## Error Handling

- **Request queued but nothing ever airs, no error anywhere** — the most
  common false alarm. `handle_replay_request`/`handle_viewer_joined` in
  `agent.py` only write the request file; they don't check whether
  anything is actually polling it. If the worker didn't boot with
  `layout.preset: replay` (or `LAYOUT_PRESET=replay` env — every generic
  worker already defaults to `replay`; `WORKER_1_LAYOUT_PRESET`..
  `WORKER_8_LAYOUT_PRESET` in `.env.example` override it per worker,
  docs/blank_workers.md), this pane doesn't exist
  in its tmux layout at all (`config/layouts/coder.yaml` has no `replay`
  panel — only `config/layouts/replay.yaml` does), so the file just sits
  there forever. Confirm the target worker's layout before debugging
  anything else (docs/operator_commands.md).
- **Episode found but refused at ingest** — `episode_schema.load_episode`
  raised `EpisodeError` (unknown primitive, unknown speaker, over
  `max_scene_seconds`, a redaction hit, ...). Logged to stderr with every
  rejecting Issue; the pane returns to idle exactly like an unresolvable
  episode name. This is the "same config and same engine on both sides"
  guarantee actually mattering at runtime, not just at generator build
  time (docs/campaign_platform_build.md).
- **Wrong campaign resolved** — an episode that exists under a DIFFERENT
  campaign directory than the one `resolve_campaign` picked reports as
  simply "not found", the same as a typo'd episode name. Check the
  request's own `"campaign"` field first, then the worker config's
  `campaign:`, then `REPLAY_CAMPAIGN` — `resolve_campaign`'s precedence
  order, highest first.
- Malformed request file → consumed and discarded (logged).
- Missing library dir (or missing campaign subdirectory) → idle screen
  says so; nothing crashes.
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
- `load_reused_show` never raises: an unavailable store, an
  (episode, campaign) pair never cached, a load failure, or a cached scene
  structure that no longer matches the current episode's own `scenes[]`
  (scene count or `scene_kind` mismatch — e.g. the episode was rebuilt)
  all log to stderr and return `None`, which `perform_request` treats
  exactly like a request without `narration: "reuse"`: it falls through to
  `prepare_voice` for a fresh airing.
- **Duet replay never degrades** (docs/duet_replay.md refusal rule):
  `perform_director_request` returns `False` — never a partial/solo
  airing — if there's no Kafka producer, `narration_store.available()` is
  `False`, voice preparation fails, persisting a fresh airing fails, or
  not every invited follower publishes `replay_ready` within
  `REPLAY_READY_TIMEOUT_S`. Every case logs `duet refused: <reason>` to
  stderr; all but the no-producer case also publish `replay_end` to
  whichever followers were already invited and an `operator_reply` with
  the error. `perform_follower_request` returns `False` (never generates
  fresh narration) on a malformed invite payload, an unreachable/missing
  narration store, an airing that no longer matches the episode's own
  scenes, or a failed `replay_ready` publish.
- **The mid-airing guard flag never blocks or delays a show.**
  `_set_airing` (called from `_airing_flag`'s set-then-`finally`-clear
  pair) catches any exception from the Redis `set`/`delete` call and just
  logs it — an unreachable Redis must never prevent a performance from
  starting or finishing. The flag is ALWAYS cleared even when the
  performance itself raises, because `_airing_flag` is a context manager
  with the clear in its `finally` block, not a bare best-effort call after
  the fact — this is what makes "a crashed show can't wedge it forever"
  true rather than aspirational.
- Persona relay-file reads never raise: a missing/corrupt
  `/tmp/persona.json` is treated as "nothing new" (same `_read_json_file`
  best-effort convention as every other relay file this pane polls); a
  persona doc whose `voice` isn't a dict simply isn't merged (the
  `campaign` overlay, if present, still applies).

## Changelog

- **v2.1.0** (2026-07-26, campaign_platform_contract.md §8): Runtime persona assignment —
  `main()`'s idle loop polls the new `/tmp/persona.json` relay file (env
  `PERSONA_FILE`) alongside the existing request/stop/cue files; on a
  `(campaign, speaker)` change, `apply_persona_to_config` deep-merges the
  persona's `voice:` block onto this pane's config and overlays
  `campaign`. Every performance path now wraps its `Performer.perform(...)`
  call in the new `_airing_flag(config, self_id)` context manager, which
  sets/clears `worker:{id}:airing` in Redis (`services/message-api`'s
  `POST /campaigns/{campaign}/start` reads it for the mid-airing 409
  guard). `episode_schema.load_episode`'s new `kinds` parameter (a
  contract-defect fix — see docs/episode_schema.md) is now supplied on
  every call here via `revoice.load_narration_config(campaign)`'s keys, so
  `unknown_kind` can actually reject a bad episode at ingest through this
  pane rather than only via a direct validator call. See
  docs/campaign_control.md.

- **v2.0.0** (2026-07-26, campaign-platform build, campaign_platform_contract.md §7):
  **Campaign namespacing.** Library layout became
  `REPLAY_LIBRARY/<campaign>/<episode>.json`; `resolve_episode` gained a
  required `campaign` param, containment now applies to BOTH the episode
  name and the campaign name (basename-only, no traversal via either).
  New `resolve_campaign(request, config, default_campaign="coder")` —
  request > worker config `campaign:` > env `REPLAY_CAMPAIGN` > `"coder"`.
  Episodes are now loaded via `app/episode_schema.py`'s `load_episode`
  (validated at ingest, refused loudly on failure) instead of a bare
  `json.loads`; `app/primitives.py`'s `load_primitives(campaign)` is
  loaded once per request and threaded into both `load_episode` and every
  `Performer` this pane constructs. `persist_narration`/
  `narration_store.save_airing`/`load_latest_airing` gained a `campaign`
  parameter (docs/narration_store.md) so two campaigns sharing an episode
  filename/stem never collide on "latest airing of X". `_rebuild_scenes_
  from_rows` no longer calls `revoice.plan_scenes` — it zips cached rows
  against the episode's own `scenes[]` by index (same length + pairwise
  `scene_kind` guard, same `owns=` audio-stripping behavior as before). A
  duet director's `replay_invite` payload now carries the resolved
  `campaign` so followers always load the SAME episode regardless of
  their own config/env defaults. New `--campaign` CLI flag. See
  docs/campaign_platform_build.md.
- **v1.5.0** (2026-07-19): `replay_stop` operator command — new
  `REPLAY_STOP_FILE` relay, written by `app/agent.py`'s
  `handle_replay_stop` (cancels a still-queued request outright; signals
  an in-flight show to abort). `perform_request`/`perform_director_request`/
  `perform_follower_request` all wire it into their `Performer`'s new
  `Pacer(should_stop=...)` (docs/replay.md `ReplayStopped`), clearing any
  stale stop file before starting and after finishing. The director path
  also treats a stop that lands before the cast is ready as its own
  refusal reason (`"stopped"`, distinct from `"ready_timeout"`) and tells
  followers the real reason via `replay_end`. New `scripts/stop_replay.ps1`
  (docs/operator_commands.md).
- **v1.4.0** (2026-07-13): Duet replay — `perform_director_request` and
  `perform_follower_request` (docs/duet_replay.md): a `replay_request`
  `payload.cast` mapping any speaker to another worker turns this pane
  into a director (prepares + persists the airing exactly like solo,
  invites the other cast workers, waits for `replay_ready` from all of
  them via the new `REPLAY_READY_FILE`, paces scenes with `replay_cue`
  published from `Performer.on_scene_start`) or, on a `"mode": "follow"`
  request written by `handle_replay_invite`, a follower (loads the same
  persisted airing via `narration_store.load_airing`, keeps audio only for
  its own cast scenes, performs via `Performer.wait_for_scene` polling the
  new `REPLAY_CUE_FILE`). Duets refuse rather than degrade on any failure.
  `resolve_self_id` resolves this worker's bus identity for ownership
  matching. `_rebuild_scenes_from_rows` factored out of
  `load_reused_show`/`_load_cached_show` to also serve the follower path.
- **v1.3.0** (2026-07-12): Narration + audio caching and reuse —
  `persist_narration` upserts the full airing (text, WAV bytes, measured
  duration) into `voiced_narration` via the new `app/narration_store.py`,
  reusing `publish_narration`'s `message_id`. A `replay_request` with
  `payload.narration: "reuse"` has `load_reused_show` rebuild the show
  from the latest cached airing (scenes replanned with
  `revoice.plan_scenes`, cached text/WAVs reattached) instead of a fresh
  LLM + TTS pass, falling back to fresh generation whenever nothing usable
  is cached. Needs `POSTGRES_*` env and `psycopg2-binary` in the worker
  image. See docs/narration_store.md.
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
