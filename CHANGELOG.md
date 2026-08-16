# Changelog

Newest entries first. Moved out of `README.md` on 2026-08-16 to keep the
README itself to a quick orientation/quick-start — see `README.md` for the
current state of the project and links to detailed docs.

**The Rerun Theater episode library moved from the filesystem into Postgres,
and nothing reaches it now without being validated first** — an episode used
to be a JSON file: `scripts/build_replay_library.py` wrote `replays/*.json`
on a dev box, an operator hand-synced that folder to the deploy host, and all
six workers bind-mounted it read-only at `/data/replays`. Nothing between "a
file appeared in `replays/`" and "it plays on a live Twitch stream" ever
checked the file, and adding an episode meant filesystem access to the host —
so a malformed or unredacted script was discovered only when it failed, or
leaked, on air. Episodes are now uploaded to the new **`POST /replays`** on
message-api and stored in a `replay_episodes` table; the workers read
straight from Postgres and the six mounts are gone.

- `app/episode_validator.py` (new) — the gate, four stages in order: shape
  (the exact key set `session_log_parser.parse_session` emits, plus
  per-event-type required fields) → name (basename-only,
  `^[A-Za-z0-9._-]{1,128}$`) → leak audit → **dry-run render**, which
  actually performs the whole episode through `replay.Performer` into a
  throwaway buffer with pacing off and groups it with `revoice.plan_scenes`.
  That last stage is the "won't have issues replaying it" check — an episode
  that crashes the renderer is rejected at upload instead of on stream.
- The leak audit is no longer a local-only step: `LEAK_AUDIT`/`audit()` moved
  out of `scripts/build_replay_library.py` into `app/session_log_parser.py`,
  so the same regex now guards both the local build and the server. A
  leak-audit rejection reports the categories audited and tells you to
  rebuild — **never the matched text**, which is by construction the secret.
- `app/episode_store.py` (new) — the data-access module, modelled on
  `app/narration_store.py`: lazy `psycopg2`, one connection per call, 5s
  connect timeout, and it *raises* on DB failure so each caller picks its own
  degradation. `app/replay_pane.py` and `app/agent.py` read it directly.
- `name` is the primary key and keeps the exact string the old filename stem
  had, so the `voiced_narration` narration cache and the duet protocol keep
  matching with **no migration**.
- Trade-off accepted: with no filesystem fallback, a Postgres outage means no
  reruns. Every read path degrades visibly instead of crashing — the pane's
  idle screen gained a third state, "episode store unreachable", distinct
  from "library empty", and a `viewer_joined` still greets the viewer, just
  without a show.
- `--library`/`REPLAY_LIBRARY`, `DEFAULT_LIBRARY` and `DEFAULT_REPLAY_LIBRARY`
  are gone. See [docs/episode_store.md](docs/episode_store.md),
  [docs/episode_validator.md](docs/episode_validator.md),
  [docs/message_api.md](docs/message_api.md) and
  [docs/replay_pane.md](docs/replay_pane.md).

**The avatar now visibly talks while there's text on screen** — every code
path that shows a speech bubble already paired it with the right expression
(`speaking`/`happy`/`frustrated`), but the `builtin` static-box provider had
zero frame animation, so the mouth was one fixed glyph for the bubble's whole
duration no matter what. `avatar_providers/builtin.py`'s `DEFAULT_EXPRESSIONS`
gained an optional `talk_mouth` entry per expression, alternated with the
normal `mouth` once per tick (~0.5s) whenever `render_tick` gets non-empty
`bubble_lines` — a worker's `avatar.expressions.<name>` config override can
add/omit this the same way it already overrides `eyes`/`mouth`. The
`ascii_avatar` provider already did this (its `speaking` state cycles 4 real
mouth-open frames every 0.1s via `_SPEAK_FRAMES`); this brings `builtin` to
parity. See [docs/avatar_providers.md](docs/avatar_providers.md).

**Fixed: `docker build` could hang/get canceled transferring a bloated build
context** — no `.dockerignore` existed, and Docker never reads `.gitignore`,
so `docker build -t vtube-worker:latest .` (and every `services/*/Dockerfile`
build in `install.sh`, which all use `.` as their context) shipped the
**entire** repo checkout to the daemon on every build — including `.git/`,
and, on the deploy host (`C:\Users\matt\PycharmProjects\virtualTubers`), `voices/` (Piper `.onnx`
models fetched by `install.sh` itself) and `replays/` (the episode library) —
several hundred MB of nothing any Dockerfile ever `COPY`s. A build context
that large is slow enough to transfer that it can get killed by an idle/SSH
timeout before the actual build even starts (`ERROR: failed to build: ...
Canceled: context canceled`, observed mid-"load build context"). New
`.dockerignore` excludes those plus the usual caches/venvs/IDE cruft.
Separately, the vendored `repos/ascii-avatar/assets/` (110MB of PNGs backing
upstream frame sets this project deliberately never loads — see
[repos/README.md](repos/README.md)) was deleted from the snapshot, since it
was dead weight in that same context and in every worker image. Both are
config/vendored-content changes only — rerun `install.sh` on the host to
rebuild with the now much smaller context.

**Fixed: the animated `ascii_avatar` face was cut off in every worker's tmux
pane, and entirely unusable on the three A/B coding-backend workers** — the
avatar pane's height is carved out of its column by the `filetree` pane's
split percentage in `config/layouts/*.yaml` (`docs/build_layout.md`). That
split had only ever been tuned for the old static `builtin` face (a handful
of lines): `coder.yaml`/`tester.yaml`/`manager.yaml` gave `filetree` 50% of
the column (avatar the other 50%), which still clipped the animated face's
~20-row frame plus its speech bubble and status bar to its top half — and
`config/layouts/replay.yaml` (the layout `worker-coder-native`,
`worker-coder-opencode`, and `worker-coder-aider` all default to, per
`docker-compose.yml`'s `LAYOUT_PRESET=replay` default) had never been updated
at all and was still giving `filetree` 81%, squeezing avatar to ~19% of the
column — effectively not rendering. All four presets now give `filetree`
just 20% (avatar ~80%) — config-only, no rebuild needed, just a container
restart to re-run `build_layout.py`. See [docs/build_layout.md](docs/build_layout.md)
for the worked-example table (now kept in sync) and
[docs/avatar_providers.md](docs/avatar_providers.md) for the provider itself.

**Each of the 6 workers now has its own distinct voice** — previously every
worker's `voice.model_path` pointed at the same `en_US-lessac-low.onnx`, so
KODI-7, MAX-1, TESS-3, NYX-1, OKO-2, and ADA-3 all sounded identical.
`config/workers/*.yaml` now gives each persona a distinct Piper voice —
KODI-7 lessac, MAX-1 bryce, TESS-3 kathleen, NYX-1 danny, OKO-2 joe, ADA-3
kristin — added to `scripts/download_voices.py`'s catalog so `./install.sh`
fetches them automatically. `boss` stays `ryan` for every worker (the
shared narrative voice). Config-only, no code change or image rebuild
needed — just redeploy and let `install.sh` pull the new models.

- **Solo shows**: `voice.speakers.coder` is empty by default, so it falls
  back to the worker's own `model_path` — the directing worker always
  narrates its own "coder" lines in its own voice.
- **Multi-persona duets** (e.g. `replays/sample.json`'s 6-way mic-check
  fixture, driven by `scripts/send_test_message.ps1`) tag lines with the
  literal persona id (`tester`, `coder-native`, `coder-opencode`,
  `coder-aider`), not just `boss`/`coder`. The duet **director** voices
  every cast member from its own `voice.speakers` map, and an id with no
  entry silently falls back to the director's base voice — which is why
  the bug initially looked like "only the boss sounds different, everyone
  else is identical" (only `boss` had an override). Fixed by giving
  `voice.speakers` an explicit entry for all four extra personas in every
  worker's config, so any worker can direct a multi-persona show with each
  cast member in its own voice.
- **Remaining caveat**: for a **real, single-session** episode (only ever
  `boss`/`coder` speaker ids) performed as a duet, the director's
  `voice.speakers.coder` still decides the "coder" role's audio for
  whichever physical worker is cast there — it does not automatically
  become that worker's own voice, since the role name (not the cast
  worker id) is what's looked up. Workaround: always address the
  `replay_request` to the worker cast as `"coder"` (see the
  [Duets](docs/usage.md#duets-multiple-workers-same-episode) section below).

See [docs/tts_client.md](docs/tts_client.md) and
[docs/duet_replay.md](docs/duet_replay.md#voice-resolution-the-directors-config-decides-every-speakers-audio).

**Fixed: the three A/B coding-backend workers could never show Rerun Theater,
no matter what** — `worker-coder-native`, `worker-coder-opencode`, and
`worker-coder-aider` shipped in `docker-compose.yml` with no `LAYOUT_PRESET`
override env, no `POSTGRES_*` env, and no `/data/replays`/`/data/voices`
volume mounts — unlike `worker-coder`/`worker-manager`/`worker-tester`, which
had all three from the start. That meant those workers always ran their
normal `coder` editor layout; updating stream keys, `ANTHROPIC_API_KEY`, or
anything else in the stack env could never switch them into the replay pane,
because there was no override path wired up to flip. `docker-compose.yml` now
gives all three the same `LAYOUT_PRESET`/`POSTGRES_*`/volume-mount treatment
as `worker-coder`, via new `CODER_NATIVE_LAYOUT_PRESET` /
`CODER_OPENCODE_LAYOUT_PRESET` / `CODER_AIDER_LAYOUT_PRESET` stack env vars —
**and, for now, all three default to `replay` (Rerun Theater) rather than
`coder`** (set one to `coder` to put that worker back in its normal editor
pane). Needs a worker image rebuild (no new dependency) + redeploy,
same as any other compose change. See the updated "Deployment requirements"
in [docs/duet_replay.md](docs/duet_replay.md) and the new second bullet in
[docs/replay_pane.md](docs/replay_pane.md#error-handling)'s error-handling
section for the full before/after.

**`.env.example` now lists every env var `docker-compose.yml` actually reads** —
it had drifted behind the compose file: `CODER_LAYOUT_PRESET` /
`MANAGER_LAYOUT_PRESET` / `TESTER_LAYOUT_PRESET`, `TWITCH_CHANNEL_MAP`,
`PRESENCE_COOLDOWN_S`, `PRESENCE_IGNORE_USERS`, and `LOG_RETENTION_DAYS`
were already documented in the [env var table](docs/deployment.md#required-environment-variables-env)
below and read by the compose file, but missing from the template itself —
so a `.env` copy-pasted from `.env.example` silently ran every
worker's default layout with no way to know a `LAYOUT_PRESET` override was
even possible. This is also the #1 cause of "I sent a `replay_request` and
nothing happened, no error anywhere": the request file gets written just
fine, but nothing is polling it unless that worker booted with
`layout.preset: replay` — see the new callout in
[docs/replay_pane.md](docs/replay_pane.md#error-handling).

**Rerun Theater can now perform as a duet — multiple workers airing the
SAME episode together, each voicing a different speaker** — a
`replay_request` may now carry `payload.cast`, a `{speaker: worker_id}`
map:

- The worker that receives the request becomes the **director**: it
  prepares the airing exactly like a solo show (LLM + TTS for every
  speaker), persists it to Postgres via `app/narration_store.py`, invites
  every other cast worker over the bus (`replay_invite`), waits for all of
  them to confirm ready (`replay_ready`), then paces the whole cast
  scene-by-scene with a `replay_cue` published immediately before
  performing each scene.
- Each invited **follower** loads the SAME persisted airing (never
  generates its own narration), keeps audio only for the scene(s) cast to
  it, and performs the full episode on its own stream — visuals for
  every scene, speaking only its own lines, the avatar "listening" on
  everyone else's.
- **Duets never degrade to solo**: an unreachable narration store/Kafka,
  a voice-prep failure, or a follower that doesn't show up in time
  (`REPLAY_READY_TIMEOUT_S`, default 60s) refuses the whole airing outright
  (`replay_end` + an `operator_reply` error) rather than airing partially.
  Solo requests (no `cast`) are completely unaffected.
- `app/agent.py` gained four any-role relay handlers
  (`replay_invite`/`replay_ready`/`replay_cue`/`replay_end`) that write to
  two new local files (`REPLAY_CUE_FILE`, `REPLAY_READY_FILE`) — panes
  still never consume Kafka directly.
- Every cast worker (director and followers) needs `LAYOUT_PRESET=replay`,
  the `POSTGRES_*` env vars, and reachable Kafka; all six coder-role
  workers (`worker-coder`/`worker-manager`/`worker-tester` and the three
  A/B coding-backend workers) have these wired in `docker-compose.yml` —
  set that worker's `*_LAYOUT_PRESET` stack env to `replay` to enable it.
  Needs a worker image rebuild (no new dependency). See
  [docs/duet_replay.md](docs/duet_replay.md) for the full protocol
  (message schemas, timeouts, ownership rules) and
  [Duets](docs/usage.md#duets-multiple-workers-same-episode) below.

**Avatar rendering is now a pluggable provider layer** — `app/avatar.py`
is a thin dispatcher now, not a renderer:

- `app/avatar_providers/` (new) — `AvatarProvider` contract
  (`render_tick(expression, bubble_lines)` + `tick_interval_s`), a registry
  (`builtin` | `ascii_avatar`), and `load_provider()`, which picks a
  provider via `AVATAR_PROVIDER` env > worker config `avatar.provider` >
  `builtin` default. The original static ASCII box face moved verbatim
  into `avatar_providers/builtin.py` — still the default and the
  always-available fallback.
- `repos/ascii-avatar/` (new) — a vendored MIT snapshot
  (`repos/README.md` has the pinned commit) driving a new `ascii_avatar`
  provider: an animated face via the vendored renderer/animation stack
  (forced to its dependency-light "cyberpunk" frame set), with our 7
  expressions mapped onto its 5 states (`avatar.expression_map` to
  override). Only its rendering code is used — its event bus, MCP bridge,
  and TTS/voice modules are never imported.
- **Safe by construction**: an unknown provider name or ANY exception
  while constructing the configured provider (bad config, missing
  vendored repo, terminal init failure) is logged and falls back to
  `builtin` — the avatar pane's only job is to stay up.
- Switching providers is config-only (`avatar.provider` in a worker's
  config, or `AVATAR_PROVIDER` env for a no-config-edit override) — no
  code change needed. `docker-compose.yml` gives every worker its own
  stack env var (`CODER_AVATAR_PROVIDER`, `MANAGER_AVATAR_PROVIDER`,
  `TESTER_AVATAR_PROVIDER`, etc. — see `.env.example`), so a stack
  redeploy can flip a single worker's avatar without editing any config
  file. The `Dockerfile` gained `COPY repos/ /repos/`, so the **first**
  switch to `ascii_avatar` needs a worker image rebuild + redeploy
  to get the vendored repo into the image; after that, flipping
  between providers needs no rebuild. Full write-up of what changed and
  why: [docs/avatar_provider_integration.md](docs/avatar_provider_integration.md)
  (see also [docs/avatar_providers.md](docs/avatar_providers.md) and
  [docs/avatar.md](docs/avatar.md) for API-level reference).

**A viewer starting to watch on Twitch now starts a rerun** — a new
`services/twitch-presence/` service watches each worker's Twitch chat
(anonymous IRC read — no OAuth token or Twitch app needed) and, when a
viewer joins a channel, POSTs a `viewer_joined` message to `message-api`
addressed to that channel's worker. The agent's new `handle_viewer_joined`
(any role) queues a Rerun Theater episode — picked at random from the
worker's library — for its replay pane (needs `LAYOUT_PRESET=replay`,
docs/replay_pane.md), then greets the viewer with an LLM-written
in-character welcome introducing the show (console + avatar speech
bubble) — narration-only, deliberately nothing back on the bus, so a burst
of arrivals never becomes bus traffic. The rerun is queued before the LLM
call (a dead LLM can't stop the show), and a pending operator-queued
replay request is never overwritten. Per-viewer greeting
cooldown (`PRESENCE_COOLDOWN_S`, default 1h) and a built-in bot ignore list
stop rejoin/bot spam. Configure with one stack env var —
`TWITCH_CHANNEL_MAP=mycoderchannel:coder,mymanagerchannel:manager` — the
service idles harmlessly until it's set. Caveat: Twitch has no true
"started watching" event; the chat JOIN (fired automatically by the web
player for logged-in viewers, but batched by Twitch and absent for
logged-out viewers) is the closest per-user signal. `install.sh` builds the
new `virtualtubers-twitch-presence:latest` image. See
[docs/twitch_presence.md](docs/twitch_presence.md).

**Fixed: narration audio never actually reached the stream** —
`app/stream_supervisor.py`'s ffmpeg command captured a synthesized silent
audio track (`anullsrc`) unconditionally, never the PulseAudio `vout` sink
that `audio_player.py`'s `paplay` plays Rerun Theater's narration into.
Every other part of the voice pipeline (LLM lines, Piper synthesis, the
`voiced_narration` transcript table) could work perfectly and the stream
would still be silent. `build_ffmpeg_cmd` now captures `vout.monitor`
(`-f pulse -i vout.monitor`) whenever `pulse_monitor_available()` confirms
Pulse is actually up, falling back to the old silent track only if it
isn't — same soft-degradation contract as the rest of the feature: an
audio problem mutes the show, never cancels it.

That fix then surfaced a second, deeper bug it had been quietly hiding:
PulseAudio's `--system` mode gates every client (`pactl`, `paplay`,
ffmpeg's `-f pulse` input) on membership in the `pulse-access` group,
which the container's `root` user was never added to — every Pulse call
was silently failing with "Access denied" the whole time (masked by a
`2>/dev/null || true` in `startup.sh` and `DEVNULL` in
`audio_player.py`). Fixed with `RUN usermod -aG pulse-access root` in the
Dockerfile; `startup.sh`'s sink creation now logs success/failure instead
of hiding it. See [docs/stream_supervisor.md](docs/stream_supervisor.md).

**Rerun Theater episodes are now SPOKEN — two-voice narration, synced to the
screen** — the planned persona re-voicing layer landed, with TTS on top:

- `app/revoice.py` (new) — per-airing narration pass: groups an episode's
  events into scenes (boss message / coder talk / coder work), asks the
  local LLM for a fresh spoken line per scene — sized to the scene's
  estimated screen time (~2.5 words/sec), so a long console scroll gets
  enough narration to talk over all of it — then synthesizes each line.
  Every airing of the same episode gets new dialogue. LLM down → template
  lines from the redacted script; the show always airs.
- `app/tts_client.py` (new) — provider-switchable TTS (same pattern as
  `llm_client.py`): local **Piper** (default, free), OpenAI, or ElevenLabs;
  adapted from the autoVideo project. Returns each WAV's *measured*
  duration. Two voices via `voice.speakers` config — the boss and the coder
  speak with different models. `app/audio_player.py` (new) plays into the
  container's PulseAudio sink, which ffmpeg already captures onto the
  stream.
- `app/replay.py` — audio-anchored pacing: each voiced scene's typing/
  scrolling speed is scaled so the visuals and the spoken line finish
  together (clamped 0.4–3.0×; visuals done early → the scene holds for the
  voice). Spoken lines also render as dim `♪` text for muted viewers, and
  drive the avatar's speech bubble. `replay_pane.py` reads the worker
  config and runs the pass before each show; `"voice": false` in a
  `replay_request` forces a silent airing.
- Setup: `./install.sh` now fetches the Piper voice models straight into
  `voices/` on the deployment host (compose mounts it `:ro` at
  `/data/voices`) — no manual download/sync step needed there anymore; set
  the worker's `voice.provider: piper`. Worker image rebuild required
  (`piper-tts` added to requirements). See
  [docs/revoice.md](docs/revoice.md), [docs/tts_client.md](docs/tts_client.md),
  and [docs/audio_player.md](docs/audio_player.md).
- **Narration is now durably saved** — the synthesized audio itself is
  never kept (regenerated fresh every airing, then deleted with the temp
  workdir), but `replay_pane.py` publishes each airing's spoken transcript
  (episode, timestamp, every scene's speaker + text) as a `replay_narration`
  bus message; `message-logger` unpacks it into a new Postgres
  `voiced_narration` table, one row per scene. Fire-and-forget — a down or
  unconfigured message bus just skips saving, never blocks the show. See
  [docs/message_logger.md](docs/message_logger.md).

**Rerun Theater — workers can re-perform past real dev sessions as shows** —
saved Claude Code session logs become replayable stream content:

- `app/session_log_parser.py` (new) — parses a `claudeBackupUtility` session
  log into a canonical, **redacted** episode script (passwords/credential
  values, public+tailnet IPs — private LAN IPs stay readable — usernames,
  key-shaped tokens, emails scrubbed before anything can reach a broadcast
  pane). `scripts/build_replay_library.py` batch-builds the episode library;
  it refuses to write any episode that fails the leak audit.
- `app/replay.py` (new) — performs a script as a paced, colorized show:
  boss messages, typed narration, `$ command` + recorded output, edits as
  red/green diffs. **Display-only** — recorded commands are rendered, never
  executed. Drives the existing avatar via `agent_state.py`.
- `app/replay_pane.py` (new) — "Rerun Theater" pane: idles with the episode
  listing, performs an episode when the agent drops the request file.
- Operator wiring: send `{"type": "replay_request", "payload": {"episode":
  "<name>"}}` via message-api (docs/operator_commands.md); `agent.py`
  queues it (any role). Episode names resolve basename-only inside the
  library — bus payloads can't reach anything that isn't already in it.
- Config-only mode switch: `layout.preset: replay` (or
  `LAYOUT_PRESET=replay`) swaps the editor pane for the theater
  (`config/panels/replay.yaml`, `config/layouts/replay.yaml`).
- Episode library: build locally, then upload to `POST /replays` — it lives
  in Postgres now, not on the host filesystem (see the top Recent Changes
  entry). Persona re-voicing (unique shows per airing via the local LLM) is
  the planned next layer. See [docs/replay_pane.md](docs/replay_pane.md),
  [docs/replay.md](docs/replay.md), and
  [docs/session_log_parser.md](docs/session_log_parser.md).

**Workers can now be turned on/off via an API — no stack redeploy needed** —
each worker (agent + Twitch stream) can be paused and resumed in place, in
the same container:

- `app/worker_control.py` (new) — a Redis-backed `worker:{id}:enabled` flag,
  checked by `app/agent.py`'s tick loop (pauses task/message processing when
  disabled) and by the new `app/stream_supervisor.py` (stops/starts the
  ffmpeg broadcaster when disabled — the Twitch channel actually goes
  offline). Reads fail open (Redis down or key unset → enabled), so a
  control-plane hiccup never silently kills a live stream; writes do not
  fail open, so the operator finds out if a toggle didn't take effect.
- `services/message-api` gained `GET /workers/{id}`, `POST /workers/{id}/enable`,
  and `POST /workers/{id}/disable` — the intended integration point for a
  planned web GUI worker manager. See
  [Turning a worker on/off](docs/usage.md#turning-a-worker-onoff-no-redeploy) below.
- `startup.sh` no longer runs `ffmpeg` as its final foreground command —
  ffmpeg used to *be* the container's long-lived process, so killing it to
  honor a "disable" would have killed the whole container. It now runs
  `stream_supervisor.py`, which starts/stops ffmpeg as a child process
  instead (and, as a side effect, auto-restarts it if it ever crashes on its
  own).
- Landing this needs one worker-image rebuild + redeploy (like any
  code change); every toggle after that is just an HTTP call — see
  [docs/worker_control.md](docs/worker_control.md) and
  [docs/stream_supervisor.md](docs/stream_supervisor.md).

**Coders now write REAL code — swappable coding backends, A/B-tested live** —
the biggest Phase-1 gap is closed: a coder worker can actually edit files, commit,
and have its work really tested, via a config-selected backend
(`coding_backend.provider` in the worker config — same provider-switch pattern
as `llm.provider`):

- Three new coder workers run the SAME task through different tools, each in
  its own workspace volume seeded from a tiny `sandbox/` project (one seeded
  bug, suite goes green when fixed): **NYX-1** (`coder-native`, our own
  minimal LLM loop), **OKO-2** (`coder-opencode`, OpenCode CLI), **ADA-3**
  (`coder-aider`, aider). Send the same `task_assignment` to each via
  `message-api` and compare.
- The tester now **really runs pytest** against read-only mounts of each
  coder's workspace — real `test_passed`/`bug_report` verdicts with failing
  test IDs in the repro; the weighted-random stub survives only for
  workspaces it can't reach. The manager re-delegates fixes to the
  *originating* coder (`coder_id` travels the whole loop).
- Every run is published as a `coding_run_report` bus message and unpacked
  by `message-logger` into a new `coding_backend_runs` Postgres table:
  `SELECT backend, success, duration_s FROM coding_backend_runs;`
- Commits are local-only for now (per-persona authorship via
  `app/git_client.py`); push/PR no-op gracefully until `GIT_SERVER_URL`
  points at the (separately planned) local git server.
- Worker image grew Node 18 + OpenCode + aider (isolated venv) — rebuild
  required: `docker build -t vtube-worker:latest .`

See [docs/coding_backend.md](docs/coding_backend.md),
[docs/git_client.md](docs/git_client.md),
[docs/test_runner.md](docs/test_runner.md),
[docs/workspace_setup.md](docs/workspace_setup.md), and
[sandbox/README.md](sandbox/README.md) for task ideas.

**Container logs now ship to Postgres too** — `services/log-shipper/` (new)
follows the stdout/stderr of every container in this project's docker-compose
stack (discovered via a read-only Docker socket mount) and inserts each line
into a `container_logs` table, alongside the existing `messages` table from
`message-logger`. This means all of this project's container logs — workers,
`message-logger`, `message-api`, etc. — can be reviewed with a single SQL
query instead of `docker logs` per container. Ships new lines only; no
historical backfill. **All containers redact Twitch stream keys, API tokens,
passwords, and other sensitive credentials before logging** — see
`stream_supervisor.py`'s `redact_stream_key()` for the mechanism. See
[docs/log_shipper.md](docs/log_shipper.md) for details, including security
notes on credential redaction and Docker socket access.

Postgres access also moved off the shared `mafober` role/database onto a
project-dedicated `virtualtubers` role/database — see
[docs/sql/](docs/sql/) for the one-time `CREATE ROLE`/`CREATE DATABASE`/
`CREATE TABLE` setup scripts and how to run them. `.env.example` and
`docker-compose.yml`'s Postgres defaults were updated to match.

**Workers now collaborate as a team — coder → tester → manager → operator** —
`app/agent.py` dispatches all 8 message types from the concept doc (§3.4) via a
`MESSAGE_HANDLERS` table, not just `task_assignment`:

- The coder still replies `task_complete`, but now also hands its commit to the
  tester (`commit_notification`); the tester "runs the suite" (a weighted-random
  stub for now — no real test execution yet) and reports `test_passed` or
  `bug_report` to the manager.
- The manager reports back to the operator with a new `manager_report` message
  type (payload `report_type: milestone | blocker | escalation`) — celebrating
  passing suites, escalating blockers and stuck bugs.
- The bug ↔ fix loop is bounded: a `retry_count` travels in the message payloads
  around the whole loop, and after 3 retries (`MAX_BUG_RETRIES`) the manager
  escalates to the operator instead of re-delegating another fix.
- Any worker answers a direct `operator_message` (message-api's default type)
  with a new `operator_reply` type addressed to `operator`; `retest_request` is
  an operator lever via `message-api` (nothing sends it automatically yet).
- Handlers are role-gated on the worker config's `agent.role` — a message type
  arriving at the wrong role logs and no-ops. The Kafka feed pane highlights the
  new traffic (`bug_report` red, `test_passed` green, `manager_report` cyan,
  `operator_reply` blue). See [docs/agent.md](docs/agent.md) for details.
- Full reference of what the operator can send (`task_assignment`,
  `operator_message`, plus manual/debug injections for every stage of the
  pipeline) is now written up in
  [docs/operator_commands.md](docs/operator_commands.md).

**Workers can now act as agents — LLM-driven task narration** — `app/agent.py` is no
longer a heartbeat-only stub:

- `app/llm_client.py` (new) — provider-switchable LLM client (`llm.provider: ollama | claude`
  in a worker's config, or `LLM_PROVIDER` env override). Ollama goes through a plain
  `httpx` call to `/api/chat`; Claude goes through the official `anthropic` SDK,
  which reads credentials from `ANTHROPIC_API_KEY` — never from the config file.
- `app/agent.py` — on receiving a `task_assignment` message, calls the LLM with
  the worker's `agent.system_prompt` and the task, then replies on the bus with
  `task_complete` (or `clarification_request` if the LLM call fails) — the
  narration shows up in the worker's console output and the Kafka feed pane.
- To send a worker an instruction, POST a `task_assignment` to `message-api`
  (see [Inter-agent messaging](docs/usage.md#inter-agent-messaging-kafka) below) — no new
  endpoint needed, this is the same `message-api` used for test injection.
- `requirements.txt` gained `anthropic`; `.env.example` gained `ANTHROPIC_API_KEY`
  (only required when a worker's config sets `llm.provider: claude`).
- This does not yet write files, run commands, or touch the shared repo — see
  [docs/VTuber_AI_Dev_Team_Concept.md](docs/VTuber_AI_Dev_Team_Concept.md) Phase 1
  for what's next. See [docs/agent.md](docs/agent.md) and
  [docs/llm_client.md](docs/llm_client.md) for details.

**Config-driven modular tmux panels + rich Kafka message feed** — the worker's
tmux layout is no longer hardcoded in `startup.sh`; it is now declarative,
ConfigMap-friendly config:

- `app/build_layout.py` (new) — layout engine. Resolves a worker's chosen preset
  (`config/layouts/<preset>.yaml`) against reusable panel-type defaults
  (`config/panels/*.yaml`), writes each pane's resolved config to `/tmp/panes/<id>.yaml`,
  and emits the tmux command sequence; `startup.sh` now just runs
  `eval "$(python3 /app/build_layout.py --config "$CONFIG_PATH")"`
- `config/panels/{kafka_feed,avatar,filetree,editor,htop}.yaml` (new) — panel-type
  defaults; `config/layouts/{coder,tester,manager}.yaml` (new) — per-role composition presets
- `config/worker.yaml` + `config/workers/*.yaml` now select a preset via
  `layout.preset` (the dead `layout.variant` block was removed); `LAYOUT_PRESET` env overrides
- `app/tail_bus.py` — rewritten into a rich, filterable feed: colorized sender,
  aligned columns, TYPE highlighting, truncated payloads, and heartbeat filtering
  (the per-tick flood arrives as type `status_update`, hidden by default)
- Reorder/resize/disable any pane with a **config-only** change — no `startup.sh`
  or image rebuild

See [docs/layout_system.md](docs/layout_system.md), [docs/panels.md](docs/panels.md),
[docs/build_layout.md](docs/build_layout.md), and [docs/message_bus_feed.md](docs/message_bus_feed.md).

**Kafka message bus + Postgres logging + HTTP test-injection API** — the inter-agent message bus moved from a plain file (`/data/world-state/messages/bus.log`) to Kafka:

- `app/message_bus.py` (new) — shared envelope/producer/consumer helper used by agents and the new services
- `app/agent.py` — now actually parses its mounted config (previously ignored it — every worker silently ran as `worker_id: "worker"`), publishes heartbeats as real Kafka messages, and has a minimal `perceive()` that prints messages addressed to it
- `app/tail_bus.py` (new) — replaces the `tail -f bus.log` tmux pane with a live Kafka consumer
- `services/message-logger/` (new) — durably logs every bus message to Postgres
- `services/message-api/` (new) — `POST /messages` on port `8090` for injecting test messages onto the bus, see [docs/message_api.md](docs/message_api.md)
- `config/*.yaml` gained a `message_bus` section (bootstrap servers, topic, worker ID); `docker-compose.yml` gained the two new services plus `WORKER_ID`/`KAFKA_*` env vars per worker
- Fixed a pre-existing YAML syntax bug (`frustrated:{` missing a space) in all three role configs that would have broken the new config-parsing on startup

See [docs/message_bus.md](docs/message_bus.md), [docs/message_logger.md](docs/message_logger.md), and [docs/message_api.md](docs/message_api.md) for details.
