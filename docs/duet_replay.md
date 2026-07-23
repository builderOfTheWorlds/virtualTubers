# Duet Replay — multi-worker Rerun Theater

## Overview

Today's Rerun Theater performs a whole episode solo: one worker speaks
every voice (boss + coder) on its own stream. **Duet replay** lets a
`replay_request` carry `payload.cast` — a `{speaker: worker_id}` map — so
several workers perform the SAME episode together, each on its own Twitch
channel, in lockstep: every stream renders the full visuals for every
scene, but each worker only plays audio for the scene(s) whose speaker was
cast to it.

The worker whose agent received the `replay_request` becomes the
**director**. It prepares the airing exactly like a solo show (LLM +
TTS for every speaker), persists it to Postgres via
[narration_store.py](narration_store.md) so the others can load the exact
same audio/text, invites the other cast workers over the bus, and only
starts performing once every one of them confirms it's ready. From there
the director paces the whole cast scene-by-scene with a cue message
published immediately before it performs each scene. **Followers** load
the same persisted airing, keep only the audio for scenes cast to them,
and perform scene-by-scene as cues arrive.

Unlike solo shows, **a duet never degrades** — if anything on the director
path fails (no narration store, no Kafka, voice prep failure, or a
follower that never shows up), the airing does not happen at all. Solo
requests (no `payload.cast`, or a cast whose values are all this worker's
own id) are completely unaffected — see `docs/replay.md` and
`docs/replay_pane.md` for that path.

Source: `app/replay.py` (`Performer` hooks + scene pacing),
`app/replay_pane.py` (`perform_director_request` /
`perform_follower_request` / dispatch in `perform_request`), `app/agent.py`
(the four relay handlers), `app/narration_store.py` (`load_airing`,
`message_id` on `load_latest_airing` rows).

## Terminology

- **airing_id** — the `narration_store` `message_id` of the persisted
  airing (the same id as the `replay_narration` bus message, when one was
  published — i.e. for a fresh, non-reused airing).
- **director** — the worker whose agent received the `replay_request`.
  Always renders full visuals; owns every speaker not explicitly cast to
  someone else.
- **follower** — any worker_id appearing in `cast`'s values that is not
  the director.
- **owned scene** — a scene whose `cast.get(speaker, director_id)` equals
  this worker's own bus worker id. An owned scene is the only kind this
  worker plays audio for and shows the "speaking" avatar/bubble on; every
  other scene still renders full visuals with the avatar "listening".
- **self id** — the bus identity used for ownership matching:
  `config.message_bus.worker_id`, else the `WORKER_ID` env var, else the
  pane's `--worker-name` (`replay_pane.resolve_self_id`).

## The 4 bus message types

All built with `message_bus.build_message(from_, to, type_, payload)` and
published via `MessageProducer.send` — envelopes are the usual
`{"id", "from", "to", "type", "payload", "timestamp"}` dicts.

### 1. `replay_invite` — director → each follower

```json
{"airing_id": "...", "episode": "2026-07-02_04-27-00_6ecdde82",
 "cast": {"boss": "manager", "coder": "coder"},
 "speed": 1.0, "worker_name": "KODI-7", "director": "coder"}
```

Sent once per follower, addressed to that follower's worker id.
`worker_name` is the persona display name the director's `Performer` uses,
so dialogue lines are labeled identically on every stream. `director` is
the director's own bus worker id — required because the follower's relay
file only carries this payload (not the bus envelope), and the follower
needs it to address `replay_ready` back.

### 2. `replay_ready` — follower → director

```json
{"airing_id": "..."}
```

Sender identity is the message envelope `from`, not a payload field.
Addressed to the `director` id carried in the invite (falls back to
`"operator"` if a follower request file was somehow written without one).

### 3. `replay_cue` — director → each follower

```json
{"airing_id": "...", "scene_index": 4}
```

Published once per follower, immediately **before** the director performs
that scene (`Performer.on_scene_start`).

### 4. `replay_end` — director → each invited follower

```json
{"airing_id": "...", "reason": "finished"}
```

`reason` is `"finished"` (normal end, after the last scene), `"stopped"`
(an operator `replay_stop` — docs/operator_commands.md — fired either
mid-show or while the director was still waiting on `replay_ready`), or
`"ready_timeout"` / `"aborted"` (refusal — see below). Only ever reaches
followers that were actually invited: refusals that happen *before*
invites go out (no Kafka producer, narration store unavailable, voice prep
failure, persist failure) have nobody to send it to yet, so in those cases
it's simply never sent — `ready_timeout` and `stopped` are the cases where
followers really are already waiting and get told to stop.

## The 3 relay files

Security invariant (unchanged by this feature): panes **produce** to Kafka
but never **consume** — every inbound duet message lands in an `app/agent.py`
handler, which relays it into a small local JSON file that
`app/replay_pane.py` polls. All writes are atomic (`<path>.tmp` +
`os.replace`, same pattern as `agent_state.py`).

### Request file — `REPLAY_REQUEST_FILE` (default `/tmp/replay_request.json`)

Existing file, two new uses:

- An operator/director request may now carry `"cast"` (validated by
  `agent._is_valid_cast`: a non-empty dict of non-empty string keys and
  values) — forwarded verbatim.
- `handle_replay_invite` writes a **follower request**: the invite
  payload's fields (`airing_id`, `episode`, `cast`, `speed`,
  `worker_name`, `director`) plus `"mode": "follow"`. Absent `mode` ⇒ not
  a follower request (solo/director dispatch instead — see
  `replay_pane.perform_request`).
- The existing "don't clobber a pending request" rule applies to invites
  too: if the file already exists, the invite is dropped (logged) — the
  director's own `replay_ready` wait will then time out and refuse.

### Cue file — `REPLAY_CUE_FILE` (default `/tmp/replay_cue.json`)

Written by `handle_replay_cue` / `handle_replay_end`. Overwrite-latest —
no history is kept, and it's written even when this worker doesn't
currently know about a local show.

```json
{"airing_id": "...", "type": "cue", "scene_index": 4}
{"airing_id": "...", "type": "end", "reason": "finished"}
```

### Ready file — `REPLAY_READY_FILE` (default `/tmp/replay_ready.json`)

Written by `handle_replay_ready`.

```json
{"airing_id": "...", "workers": ["manager", "tester"]}
```

Union/replace rule: if the existing file has the **same** `airing_id`, the
sender (from the message envelope `from`) is unioned into `workers`;
different, missing, or corrupt content is replaced with a fresh
single-sender entry for the new airing.

**The director deletes this file itself before it starts waiting** (same
stale-state-hygiene convention as `REPLAY_CUE_FILE`/`REPLAY_STOP_FILE`,
right below), for a reason specific to reused airings: a `narration:
"reuse"` request replays the SAME cached airing_id every time it's
requested. Without the delete, a `ready_file` left over from a PREVIOUS
performance of that airing_id already lists every follower as ready — the
union/replace rule above treats it as still valid since the airing_id
matches — so the director's wait loop would see "ready" on its very first
read and start cueing scenes before this run's followers have actually
loaded their own audio. A follower still catches up visually via the cue
ratchet's `catch_up_to` fast-forward, but a scene it "catches up" through
plays no audio (`Performer.perform`/`_perform_scene` call
`playback.stop()` instead of letting it finish) — so a follower whose own
speaking scene lands early in the episode can lose its voice for that
entire performance with no error anywhere. Fixed 2026-07 (`app/
replay_pane.py`); see `tests/test_replay_pane.py`'s
`test_director_ignores_stale_ready_file_from_earlier_reused_airing`.

## Flow: prepare → persist → invite → ready → cue ratchet → end

```
operator ──POST /messages (cast: {...})──▶ Kafka ──▶ director agent.py
                                              handle_replay_request
                                              │ writes REPLAY_REQUEST_FILE (cast included)
                                              ▼
                          director's replay_pane.py: perform_director_request
                                              │ 1. prepare (fresh LLM+TTS, or narration:"reuse")
                                              │ 2. persist via narration_store.save_airing
                                              │    (fresh) or recover airing_id from the
                                              │    reused rows' message_id (reuse)
                                              │ 3. annotate every scene: owned + target_duration
                                              │ 4. publish replay_invite to each follower
                                              ▼
                          each follower's agent.py: handle_replay_invite
                                              │ writes REPLAY_REQUEST_FILE {"mode": "follow", ...}
                                              ▼
                          follower's replay_pane.py: perform_follower_request
                                              │ loads the SAME airing via narration_store.load_airing
                                              │ (never generates fresh narration)
                                              │ keeps WAVs only for scenes cast to itself
                                              │ publishes replay_ready to the director
                                              ▼
                          director polls REPLAY_READY_FILE until every follower is
                          present or REPLAY_READY_TIMEOUT_S elapses (refuse on timeout)
                                              │
                                              ▼
                          Performer.perform() loop, both sides:
                            director: on_scene_start(i) publishes replay_cue to each
                              follower immediately before performing scene i
                            follower: wait_for_scene(i) blocks on the cue file (ratchet)
                                              │
                                              ▼
                          director publishes replay_end (reason "finished") after the
                          last scene; each follower's Performer returns to idle too
```

Both director and follower reuse `_rebuild_scenes_from_rows` (shared with
solo `narration: "reuse"`) to turn cached Postgres rows back into a scene
list against the current episode script — a scene-count or `scene_kind`
mismatch (a rebuilt/changed episode script) is treated as "nothing usable"
and refuses (director) or fails to follow (follower).

**Director still publishes `replay_narration` and persists exactly like a
solo show — but only on a fresh airing.** A `narration: "reuse"` director
skips both (the airing is already in Postgres) and recovers `airing_id`
from `rows[0]["message_id"]`.

## Refusal rule (duets never degrade)

If ANY of the following fail on the director path, the airing does **not**
happen at all — no solo fallback:

| Failure | Detected | `replay_end` reason |
|---|---|---|
| No Kafka producer (unconfigured / unreachable) | before anything else | n/a — no producer to send with, log only |
| `narration_store.available()` is `False` | before prepare | `"aborted"` |
| Voice preparation failed/disabled | during prepare | `"aborted"` |
| Persisting a fresh airing failed (`persist_narration` → `None`) | during prepare | `"aborted"` |
| Not every invited follower published `replay_ready` within the timeout | after invites | `"ready_timeout"` |
| Operator `replay_stop` (docs/operator_commands.md) received before the cast was ready | after invites, during ready-wait | `"stopped"` |

Every refusal path: logs `[replay_pane] duet refused: <reason>` to
stderr, and — **when a Kafka producer exists** — publishes `replay_end`
(best-effort) to whichever followers were already invited (empty for
every reason except `ready_timeout`, since the others happen before
invites go out) and an `operator_reply` `{"error": "<reason>"}` addressed
to `"operator"`. The one exception is the very first check: if the
director has no Kafka producer at all, there is nothing to publish either
message with — the refusal is stderr-log-only, and the operator only
learns about it from the worker's container logs (it already got the
normal "queued" `operator_reply` when the request was first accepted,
since `agent.py` doesn't distinguish solo from duet at queue time).

Solo shows (no `cast`, or a `cast` whose values are all this worker's own
id) keep today's show-must-air degradation completely unchanged — see
`docs/revoice.md`.

## Cue ratchet & fast-forward rule

The cue file only ever holds the **latest** cue (no history), and an
agent's tick loop (`agent_config.tick_rate_ms`, default `5000`) batches
messages, so a follower can observe skips. Rule: a cue for scene `J`
authorizes performing **all** scenes `<= J`.

`Performer.wait_for_scene(i)` (the follower's hook, polled every
`REPLAY_CUE_POLL_INTERVAL_S` = 0.25s):

- cue with matching `airing_id` and `scene_index >= i` → returns that
  `scene_index` (`J >= i`).
- `type: "end"` with matching `airing_id` → returns `-1` (stop the show;
  `Performer.perform` prints an "interrupted" line, sets the avatar back
  to idle, and returns cleanly).
- `REPLAY_STOP_FILE` exists → returns `-1` immediately, same as `"end"`.
  This is checked directly (not routed through the director) so an
  operator `replay_stop` sent straight to a follower stops it even without
  the director's own `replay_end` relay arriving — see
  docs/operator_commands.md and docs/replay_pane.md.
- mismatched `airing_id`, or file missing/corrupt → keep waiting.
- watchdog timeout (see below) → returns `-1`.

Once a follower's own `Performer` is mid-scene (past `wait_for_scene`, now
inside `_perform_scene`), the same `REPLAY_STOP_FILE` is also wired into
its `Pacer(should_stop=...)` (docs/replay.md `ReplayStopped`) — so a stop
that lands mid-typing doesn't have to wait for the next `wait_for_scene`
poll either.

`Performer.perform()`'s scene loop then performs scene `i` normally; if
`J - i >= 2` (this follower is **2 or more scenes behind** the director's
cue), it performs every scene from `i+1` up to `min(J, last_scene)` with
`pacer.enabled = False` — a burst with no sleeps/typing delay — to catch
back up, then resumes normal paced performance.

## Timeouts & watchdogs

| Stage | Env var | Default | Notes |
|---|---|---|---|
| Director waits for every `replay_ready` | `REPLAY_READY_TIMEOUT_S` | `60.0`s | Polled every `REPLAY_READY_POLL_INTERVAL_S` (0.25s, not overridable). Times out → refuse (`reason: "ready_timeout"`). |
| Follower waits for the FIRST cue (scene 0) | — | `REPLAY_FIRST_CUE_TIMEOUT_S` = `120.0`s (fixed) | Generous flat allowance — the director is still preparing/inviting/annotating before it ever performs scene 0. |
| Follower waits for each SUBSEQUENT cue | — | `max(REPLAY_WATCHDOG_MIN_S, previous_scene.target_duration + REPLAY_WATCHDOG_GRACE_S)` = `max(45.0, prev + 30.0)`s | Bounded by roughly how long the director should still be busy performing the previous scene, floored so a near-zero-duration scene doesn't trip a hair-trigger watchdog. |
| Cue file poll interval | — | `REPLAY_CUE_POLL_INTERVAL_S` = `0.25`s (fixed) | Same file the ratchet reads. |

None of these constants are environment-overridable except
`REPLAY_READY_TIMEOUT_S` — the others are module-level constants in
`app/replay_pane.py`.

## Ownership & uncast-speaker defaulting

- Real recorded session scripts (parsed by `session_log_parser.py`) stay
  2-speaker-only forever — always exactly `"boss"` and `"coder"` — since a
  recorded session is inherently one human and one assistant; that's a
  property of the parser, not a limit of this protocol. Hand-authored
  episode scripts, however, can now tag events with any of up to 6
  personas via the optional per-event `"speaker"` override in
  `revoice.plan_scenes` (docs/revoice.md) — landed and working
  end-to-end: `replays/sample.json` (1 `boss` scene + 5 distinct
  `coder_talk` scenes, one per persona) plus `scripts/worker3.json`..
  `scripts/worker6.json`, whose `cast` dicts map `tester`/`coder-native`/
  `coder-opencode`/`coder-aider` to their own real worker ids instead of
  idle placeholder followers. On-screen display names for those four
  personas come from `config/workers/coder.yaml`'s `voice.speaker_names`
  block (`tester` → `TESS-3`, `coder-native` → `NYX-1`, `coder-opencode`
  → `OKO-2`, `coder-aider` → `ADA-3`), pulled from `config`'s `voice:`
  section the same way `boss_name`/`worker_name` already were — once by
  `replay.prepare_voiced_show` (feeds `revoice.prepare_show`'s narration
  prompts) and once at each of the three `Performer(...)` construction
  sites in `app/replay_pane.py` (feeds the on-screen dialogue label).
- Director side: `owned = cast.get(speaker, self_id) == self_id` — any
  speaker **not present** in `cast` defaults to the director.
- Follower side: `owned = cast.get(speaker) == self_id` (no default) — a
  speaker not present in `cast` is never owned by a follower, consistent
  with defaulting to the director.
- A scene that isn't owned still renders full visuals and prints the `♪`
  narration line on every stream; only the avatar's "speaking" state and
  bubble, and the WAV playback, are gated by ownership. An un-owned scene
  sets the avatar to `"idle"` / `"listening to the show"` instead, and
  paces to `target_duration` (the owner's measured audio duration) rather
  than to real audio, using the identical scale-clamp formula
  (`docs/replay.md`).
- If `cast`'s values are **all** equal to the requesting worker's own id,
  there is nobody to duet with — `replay_pane.perform_request` treats it
  as a plain solo request (`any(worker_id != self_id for worker_id in
  cast.values())` is `False`).

## Voice resolution: the director's config decides every speaker's audio

Every cast member's WAV is synthesized **once**, by the director, from the
director's own `voice.speakers` map (`app/tts_client.py`'s
`TTSClient.voice_for(speaker)`) during `prepare_voice`/`prepare_show` —
followers never resynthesize, they only ever play back audio the director
already generated (`_rebuild_scenes_from_rows`'s `owns` predicate gates
which cached scenes get copied into a follower's workdir). Practically:

- **Named personas (`tester`/`coder-native`/`coder-opencode`/`coder-aider`)
  resolve correctly no matter who directs**, as long as every worker's
  `config/workers/*.yaml` defines an explicit `voice.speakers` entry for
  each one — which they all do as of 2026-07-20 (docs/tts_client.md's
  changelog). Before that fix, `voice_for()`'s fallback-to-base-voice
  behavior for any speaker id with no override meant every persona except
  `boss` (the only one with an explicit override) sounded like whichever
  worker happened to direct — this is exactly why testing the 6-persona
  `replays/sample.json` fixture once showed "the manager sounds different,
  but everyone else is the same voice."
- **`"coder"` is different on purpose — `speakers.coder` is left empty**
  so a worker sounds like *itself* when narrating its own "coder" lines in
  a solo real-session replay (falls back to that worker's own distinct
  `model_path`; see docs/tts_client.md). The tradeoff shows up in duets:
  `"coder"`'s voice is always the **director's** own base voice, not
  necessarily the voice of whichever worker is actually cast into that
  role.
  - For a **real recorded session** (always exactly `boss`/`coder`),
    `"coder"` is inherently self-referential — whoever performs that role
    IS "the coder" for that show. **Convention: always address the
    `replay_request` to the worker cast as `"coder"`** (`to ==
    cast["coder"]`) — every example in this doc and every duet preset in
    `scripts/send_test_message.ps1` already follows this. Do that and the
    director's own (correct, distinct) voice is used automatically;
    address the request to a *different* worker (e.g. `manager`) with the
    same cast and the "coder" lines come out in that other worker's voice
    instead.
  - For a **hand-authored multi-persona episode where `"coder"` names a
    specific persona** (`replays/sample.json`: `"coder"` == KODI-7,
    distinct from `coder-native`/`coder-opencode`/`coder-aider`), the same
    rule applies even more strictly: direct from `"coder"` (KODI-7)
    specifically, or its lines speak in whichever other worker directed
    instead of KODI-7's own voice.
  - Directing from the worker cast as `"coder"` is a **calling
    convention**, not something the code enforces — nothing rejects a
    request addressed to `manager` with `coder` cast elsewhere. If you'd
    rather not rely on operators/scripts remembering this, the robust fix
    is routing TTS by the cast worker_id instead of the fixed role name,
    which hasn't been done — see docs/tts_client.md's "Multi-persona
    episodes are a separate case" for how far the current fix goes.

## Deployment requirements

- **Worker image rebuild.** Duet replay ships in `app/replay.py`,
  `app/replay_pane.py`, and `app/agent.py` — a normal code-change
  rebuild + redeploy (see the README's
  [Deploy / redeploy](../README.md#deploy--redeploy-after-a-code-change)
  section). It introduces no new Python dependency: `app/narration_store.py`
  and `psycopg2-binary` were already added when narration caching landed
  (`docs/narration_store.md`).
- **Every cast worker (director AND every follower) needs:**
  - `LAYOUT_PRESET=replay` (or `layout.preset: replay` in its config) so
    its replay pane actually runs — a worker with a different layout
    preset silently never picks up the queued request file at all.
  - The `POSTGRES_*` env vars (`POSTGRES_HOST`/`PORT`/`DB`/`USER`/
    `PASSWORD`) — both the director (to persist/reuse) and every follower
    (to `load_airing`) need `narration_store.available()` to be `True`.
  - Reachable Kafka (`KAFKA_BOOTSTRAP_SERVERS`/`KAFKA_TOPIC`) — already
    standard on every worker.
  - The episode library mounted at `REPLAY_LIBRARY`
    (default `/data/replays`) — followers rebuild scenes against the same
    episode script the director used, so the library must be in sync
    across every cast worker's host mount.
- **All six coder-role workers in `docker-compose.yml` have all of the
  above** (`LAYOUT_PRESET` override env, `POSTGRES_*`, and the
  `/data/replays` mount): `worker-coder`, `worker-manager`,
  `worker-tester`, and the three A/B coding-backend workers
  (`worker-coder-native`, `worker-coder-opencode`, `worker-coder-aider`,
  overridden via `CODER_NATIVE_LAYOUT_PRESET` /
  `CODER_OPENCODE_LAYOUT_PRESET` / `CODER_AIDER_LAYOUT_PRESET`). Any of
  them can join a duet or use solo narration reuse/caching once its
  `*_LAYOUT_PRESET` stack env is set to `replay`.
- **Cue relay latency is bounded by the receiving worker's agent tick
  rate** (`agent.tick_rate_ms`, default `5000`ms) and does **not**
  accumulate scene-over-scene: each cue publish is independent (the ratchet
  always reads the freshest cue, not a queue), so a follower's per-scene
  lag stays roughly constant rather than growing over a long episode.
- Each cast worker streams to its own independent Twitch channel with its
  own broadcast delay — this feature only targets **scene-level**
  synchronization (same scene at roughly the same time across streams,
  via cues + the fast-forward catch-up rule), not frame-accurate sync.

## Example: request a duet airing

Coder narrates the `coder` speaker's lines; manager (a follower) narrates
the `boss` speaker's lines. Both streams show the whole episode; each only
speaks its own half:

```bash
curl -X POST http://localhost:8090/messages \
  -H "Content-Type: application/json" \
  -d '{"to": "coder", "type": "replay_request",
       "payload": {"episode": "2026-07-02_04-27-00_6ecdde82",
                    "cast": {"boss": "manager", "coder": "coder"}}}'
```

`coder` becomes the director (it received the request). The Kafka feed
pane shows `replay_invite` → `manager`, then `replay_ready` ← `manager`,
then a `replay_cue` per scene to `manager`, and finally `replay_end`
(`reason: "finished"`) once the episode is done.

## Security invariants

- **Panes never consume Kafka.** All 4 new message types, like every
  existing one, are handled entirely in `app/agent.py`; `app/replay_pane.py`
  only ever reads local JSON relay files it polls, never a Kafka client.
- **Episode resolution is unchanged: basename-only, inside
  `REPLAY_LIBRARY`.** A `cast` payload can only choose *which workers*
  join the show and *which speaker* each one voices — it can never
  influence *which file* gets loaded. `replay_pane.resolve_episode` strips
  path components before ever touching the filesystem, same as solo.
- A malformed follower request (missing `airing_id`/`episode`, or `cast`
  not a dict) is rejected by `perform_follower_request` with a stderr log
  and no show — never a crash.

## Debugging: "only the director performs, nobody else joins"

Before suspecting the duet protocol itself, check what actually landed on
the bus — query the Postgres `messages` table (or the Kafka feed pane) for
the request's `type`. Two known causes that look identical from the
outside (director airs fine, no other stream reacts) but aren't duet bugs
at all:

- **The request was sent as `viewer_joined`, not `replay_request`.**
  `handle_viewer_joined` (docs/agent.md, docs/twitch_presence.md) forwards
  `episode`/`voice`/`narration` but silently drops `payload.cast` — no
  error, no log line. The episode airs solo on whichever worker received
  it. Only a `replay_request` sent directly starts a duet.
- **`scripts/send_test_message.ps1` sent a stale preset.** The script is a
  library of commented-out `$To`/`$Type`/`$Payload` blocks; if a preset's
  `$Payload` was edited but its `$To`/`$Type` lines were left commented,
  a leftover value from an earlier dot-sourced run (e.g. VSCode F5) is
  used instead. See docs/operator_commands.md.

## Debugging: "some personas all sound like the same voice"

Not a duet bug either — see "Voice resolution" above. Two known causes:

- **A speaker id has no `voice.speakers` entry on the director.** Fixed
  as of 2026-07-20 for the six standard personas (every
  `config/workers/*.yaml` now defines all of them), but a NEW persona
  added later needs the same treatment or it'll silently inherit whichever
  worker directs' base voice instead of failing loudly.
- **The request was directed from the wrong worker.** `"coder"`'s voice
  always comes from the director's own base voice, not the worker cast
  into that role — see "Voice resolution" above for the addressing
  convention this depends on.

If the bus genuinely shows `replay_invite` going out with no matching
`replay_ready` coming back, then it's a real refusal — check the
director's container logs for `duet refused: ready_timeout` and confirm
the follower has `LAYOUT_PRESET=replay` + reachable Postgres (see
Deployment requirements above).

## See also

- `docs/replay.md` — the `Performer` class, `on_scene_start`/
  `wait_for_scene` hooks, scene pacing (`owned`/`target_duration`).
- `docs/replay_pane.md` — director/follower request handling, dispatch.
- `docs/agent.md` — the four relay handlers, `cast` validation.
- `docs/narration_store.md` — `load_airing`, `message_id` on
  `load_latest_airing` rows.
- `docs/operator_commands.md` — the `cast` field on `replay_request`.
- `docs/revoice.md` — the narration pass every duet airing's audio and
  text ultimately comes from (unchanged by this feature).
- `docs/tts_client.md` — `voice.speakers`/`voice.model_path` resolution;
  the per-worker distinct voices and multi-persona fallback fix referenced
  in "Voice resolution" above.

## Changelog

- **v1.1.0** (2026-07-20): Documented (no protocol change) how voice
  resolution actually works in a duet — the director's own
  `voice.speakers` map voices every cast member, never the cast worker's
  own config. Fixed the six standard personas to each carry an explicit
  `voice.speakers` entry in every `config/workers/*.yaml`, so a
  multi-persona duet (`replays/sample.json`) no longer has every persona
  except `boss` collapse to the director's own voice. Documented the
  remaining gap and its workaround: `"coder"` stays a self-referential
  empty override (for solo replays to sound like the directing worker), so
  a duet's `"coder"` role still needs to be directed from the worker
  actually cast as `"coder"` — see "Voice resolution" above.
- **v1.0.0** (2026-07-13): Initial version — director/follower duet
  replay: `replay_invite`/`replay_ready`/`replay_cue`/`replay_end` bus
  types, `REPLAY_CUE_FILE`/`REPLAY_READY_FILE` relay files, cue ratchet +
  fast-forward, refusal-only (no solo degradation), owned/target_duration
  scene pacing.
