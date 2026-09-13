# WP-6 — V3 readiness probe + V4 rehearsal + end-to-end

> Status: **PLAN (2026-09-12)**. Depends on WP-1…WP-5, which are implemented and
> test-green. This is the work package that turns "the code exists" into
> "cleared to air".
> Spec of record: `roundtable_stream_design.md` v1.1 §8.3, §8.4, §13.

---

## 0. Why this WP exists

Everything before it is componentry. WP-6 is the only package that exercises the
**real director path end to end**, and it is what §8.4 names as the gate:

> A show is "cleared to air" only after a clean V4.

It also closes the diagnostic hole that motivated the whole validation ladder.
Recall the failure shape that v1.0 of the design shipped blind:

1. a show casts a voice the GM can't synthesize,
2. `prepare_voice` is best-effort and returns `None`,
3. `perform_director_request` converts that into
   `refuse("voice preparation failed or is disabled for this worker")`,
4. the refusal contract (§10) means **nothing airs on any of the 7 channels**,
5. and the operator error never names the voice.

V1 (upload) and V2 (boot) now catch the *known* cases cheaply. V3 and V4 exist
for everything static checks cannot see: a slot that is down, a stale cache, a
scene-count mismatch, a seven-way ratchet timing problem.

---

## 1. State going in (verified, not assumed)

| WP | What landed | Evidence |
|---|---|---|
| WP-1 | `_check_show` (7 rules) in `episode_validator`, wired after the leak audit, before `_dry_run` | 69 tests; rejects `en_US-kathleen-low` at upload naming the voice |
| WP-2 | `voice_for(speaker, voice_name=None)` with registry precedence; `revoice.show_bindings` | 28 tests; `voice_for(speaker)` proven byte-identical to pre-change |
| WP-3 | `app/tile_pane.py` — local follower, per-tile cue/request/state files | 7/7 distinct state+cue+request files; `owns` isolates audio |
| WP-4 | `config/panels/tile.yaml`, `config/layouts/roundtable.yaml` | 10 panes in real tmux; each tile 58-61c × 18-19r, uniform |
| WP-5 | `worker-gm` service, `config/workers/tuber_0.yaml`, registry mounts, `.env.example` | `docker compose config` parses; 7 unique DISPLAY_NUMs (99-105) |
| seam | `config/voices.yaml`, `app/voice_registry.py`, V2 in `startup.sh` 6.5 | 9/9 voices load via real `PiperVoice.load`; failure path exits 1 loudly |

Full suite: **1155 passed / 13 failed**, the 13 all pre-existing in
`tests/test_campaign_validator.py` (baseline was 1094/13 — **+61 tests, +0 new
failures**).

### The one thing that does NOT yet exist

**Nothing writes the tiles' cue and request files.** `tile_pane.py` polls
`<relay>/<slot>.cue.json` / `.request.json`; the director currently publishes
`replay_cue` to Kafka followers only (`on_scene_start` in
`perform_director_request`). Wiring that is **task 1 below** and is the true
prerequisite for any roundtable airing. Until it lands, the GM's 7 tiles will sit
on their idle screens forever while the six character channels perform normally.

---

## 2. Task 1 — local tile fan-out in the director (the missing wire)

**File:** `app/replay_pane.py` (`perform_director_request`).

The director already has the exact hook needed — `on_scene_start(index)` fires
immediately before each scene performs, and `Performer.perform` catches its
exceptions so a publish failure can't take the show down. Today it only loops
over Kafka `followers`. Extend it to ALSO write the local relay files.

```
on_scene_start(i):
    for follower in followers:                 # unchanged: Kafka
        _safe_send(producer, build_message(..., "replay_cue",
                   {"airing_id": airing_id, "scene_index": i}))
    for slot in local_tiles:                   # NEW: local relay files
        _atomic_write_json(f"{relay_dir}/{slot}.cue.json",
                           {"airing_id": airing_id, "type": "cue", "scene_index": i})
```

Requirements:

- **Atomic writes.** Use the same temp-file + `os.replace` convention as
  `agent_state.write_state` / `agent.py`'s `_atomic_write_json`. A tile polling a
  half-written cue file must never see partial JSON. (`tile_pane`'s reader is
  already tolerant — it treats unparseable content as "nothing new yet" — but
  correctness here is cheap.)
- **Local tiles are opt-in by config**, not inferred. Only the GM has tiles. Gate
  on the relay dir being configured (`TILE_RELAY_DIR`) plus the worker's layout
  preset/role, so the six character workers keep behaving exactly as today.
- **Kick off each tile before the ratchet starts.** Each tile needs a
  `.request.json` (airing_id, episode, cast, speed) written once, up front —
  after the airing is persisted and before scene 0's cue, mirroring how
  `replay_invite` precedes cues for remote followers.
- **Stale-state hygiene first.** Delete every `<slot>.cue.json` /
  `<slot>.request.json` before the show, exactly as the director already does for
  `cue_file` / `ready_file` / `stop_file`. The design doc's §6.1 and the existing
  comments in `perform_director_request` both flag this as a repeat source of
  silent failure in this codebase.
- **Emit `type: "end"`** to every tile cue file at `replay_end`, so a tile's
  `wait_for_scene` returns -1 and it drops back to idle instead of sitting on a
  watchdog.
- **Do not** make tiles participate in the `replay_ready` gate (task 3 discusses
  the tradeoff). Tiles are in-process and local; the ready gate is for remote
  workers.

**Tests** (`tests/test_director_tile_fanout.py`): cues land in every tile's file
with the right `airing_id`/`scene_index`; writes are atomic (no partial reads
under a concurrent reader); a non-GM worker writes no relay files at all; stale
files are cleared first; `end` is written on finish AND on every refusal path.

---

## 3. Task 2 — V3 readiness probe (§8.3)

**Goal:** stop paying for a full narration+TTS pass to discover a slot is down.

Today: the director prepares everything, *then* waits up to
`REPLAY_READY_TIMEOUT_S` (default 60s) for `replay_ready`, then refuses with a
generic `ready_timeout`. With 7 slots the odds one is down are meaningfully
higher, and the wasted work is the whole show's synthesis.

**Design:** a cheap pre-flight before `prepare_voice`, reusing existing plumbing
(no new protocol shape beyond a probe/ack pair):

- Director → each cast slot: a probe message.
- Each slot answers whether it can (a) resolve the episode in its library,
  (b) reach `narration_store`, (c) pass V2 on the voices it will need.
- Timeout is short (a few seconds — these are cheap local checks, unlike the
  60s ready gate which waits on audio loading).
- Any non-answer or negative answer ⇒ refuse **before** synthesis, with an
  operator error naming the specific slot and reason
  (`"tuber_3 did not answer"`, `"tuber_5: narration store unreachable"`).

**Explicitly NOT a contract change.** §10 still holds: no degradation, no
partial airing. V3 only makes refusals *faster* and *better labelled*. A show
that would have failed still fails; it just fails in 3 seconds with a useful
message instead of 60+ with a vague one.

**Tests:** all-healthy cast passes and proceeds to synthesis; one silent slot
refuses before `prepare_voice` is ever called (assert it is not called — that is
the entire point); the operator error names the slot; a slot reporting a bad
voice refuses naming the voice.

---

## 4. Task 3 — V4 rehearsal mode (§8.4) — the clearance gate

**The one tier that exercises the real thing.** A `replay_request` variant
(`rehearse: true`) running the complete director path — narration, TTS,
persistence, invite, ready gate, cue ratchet, all 7 tiles, all 6 followers on
their own channels — with **broadcast suppressed**.

### Broadcast suppression: reuse, don't build

`stream_supervisor` already starts/stops ffmpeg based on the per-worker on/off
flag in `app/worker_control.py`, toggled via message-api
`/workers/{id}/enable|disable` with no redeploy. A rehearsal:

1. records each affected worker's current flag,
2. disables broadcast on all of them,
3. airs the show,
4. restores the **recorded** state (not a blanket enable — never turn on a
   channel the operator had deliberately off).

Step 4 must run even if the show raises or refuses: `try/finally`. A rehearsal
that leaves the whole fleet dark is a worse bug than the one it was checking for.

### Cost control: reuse the `fake` provider

`TTS_PROVIDER=fake` (`tts_client._fake`) writes a silent WAV sized to the line's
read time, so `target_duration` pacing and every cue/watchdog timing stay
realistic at zero cost. Two-pass rehearsal:

- **Pass A — structural** (`fake`): scene counts, cast/ownership math, the
  ratchet at 7-way fan-out, tile idle→perform→idle transitions. Seconds, free.
- **Pass B — voice** (real provider): the show-header → registry → `voice_for` →
  synthesis chain end to end, with audio actually produced.

### What ONLY V4 can catch

- The voice chain working with the *real* provider (V1 checks names, V2 checks
  assets; neither proves synthesis of *this show's* bindings).
- `plan_scenes` producing a scene count/kind sequence matching what followers
  rebuild — `_rebuild_scenes_from_rows` returns `None` on mismatch and the
  follower refuses. A stale `narration:"reuse"` cache is the live trigger.
- Every slot's owned-scene math across 7 channels simultaneously.
- The ratchet not tripping a watchdog at 7-way fan-out (see §5 risk).
- The §8.5 geometry being legible with real content in all 7 tiles.

**Tests:** rehearsal disables then restores broadcast flags exactly (including
on failure, via `finally`); a pre-disabled worker stays disabled afterward;
`fake` provider produces non-zero `target_duration`s; a rehearsal never publishes
to a real RTMP endpoint.

---

## 5. Task 4 — end-to-end run and the §13 checklist

Order matters; each step gates the next.

1. **Build + bring up the GM.** Confirm 7 idle tiles + show log + htop on
   display `:105`, and that the six character channels still start.
   Verify on the **rendered frame**, not logs — `replay_pane`/`tile_pane` draw
   into tmux on an X display and produce *nothing* in `docker logs`. Grab
   `:105` with `x11grab` and read the PNG. (This exact trap previously caused a
   false "the duet is broken" report.)
2. **V2 at boot.** `startup.sh` 6.5 logs `Voice registry OK: 9 voice(s) … 0
   failed` in the GM container.
3. **Upload a 7-slot show** with a `show` header; confirm V1 + dry run pass and
   `GET /replays` lists it. Then confirm the **negative**: the same show with a
   bad voice name is a 400 naming the voice.
4. **Rehearse (pass A, `fake`)** → all 7 tiles perform, 6 followers ready and
   ratchet, `replay_end` reason `finished`, and **no ffmpeg ran anywhere**
   (check `worker_control` state + absence of RTMP output — do not assume).
5. **Rehearse (pass B, real TTS)** → show log shows every line in order under
   the correct persona names from the header.
6. **Air for real.** Roundtable: all 7 tiles animate `speaking` on owned scenes
   and every speaker is **audible on that channel** (confirm on actual Twitch
   output via the GQL `UseLive` check, not container state). Each character
   channel: only its own voice; private panes unchanged.
7. **Refusal paths.** Stop `tuber_3` → V3 names it fast, nothing airs anywhere.
   Remove a voice file → V2 exits non-zero naming it; a show casting it refuses
   with the voice named.
8. **Backwards-compat.** A recorded-session (`boss`/`coder`) episode with **no**
   `show` block still uploads and airs exactly as before; an existing duet show
   still airs unchanged. This is the §7.4 contract and the v1.0 shipping path.
9. **Full suite** green against the recorded baseline: 13 failures, all in
   `test_campaign_validator.py`.

---

## 6. Risks specific to this WP

| Risk | Handling |
|---|---|
| 7-way fan-out trips a follower watchdog the 2-way duet never hit | The per-scene watchdog is bounded by the previous scene's `target_duration` + grace, which does **not** scale with cast size — but the *first-cue* timeout does matter with 7 slots preparing. Measure in pass A; `REPLAY_READY_TIMEOUT_S` is already env-tunable per container. |
| Rehearsal leaves broadcast disabled | Restore in `finally`, restore *recorded* per-worker state, and assert it in tests. Add a post-rehearsal check to the §13 list. |
| Local tile cue files race the Kafka cues, desyncing roundtable from character channels | Both are written in the same `on_scene_start` before the scene performs, so they share one clock. Scene-level sync is all the protocol promises (frame-accurate sync is explicitly deferred, §12). |
| A tile dies mid-show and its pane goes blank | Tiles degrade by contract (never raise out of the loop) and return to idle. Mid-show follower loss is already accepted duet behaviour — `on_scene_start` is fire-and-forget with no ack. |
| `narration:"reuse"` + 7 slots resurrects the stale-ready bug | The director already deletes `ready_file` before waiting precisely because a reused airing keeps its `airing_id` across replays. Task 1 must extend the same hygiene to every tile relay file. |

---

## 7. Definition of done

- [ ] Director writes per-tile request + cue + end files, atomically, with stale
      hygiene; non-GM workers write none.
- [ ] V3 refuses before synthesis with a slot-specific message.
- [ ] V4 rehearses with broadcast suppressed and flags restored via `finally`,
      in both `fake` and real-TTS passes.
- [ ] The §13 checklist passes end to end, verified on rendered frames and real
      Twitch state — not logs, not `ps`.
- [ ] Suite still 13 pre-existing failures; new tests for tasks 1-3.
- [ ] `roundtable_stream_design.md` §13 boxes ticked with evidence noted.

Only then is WP-7 (identity migration) unblocked — it is gated on WP-6 being
verified **live**, so the rename can never break a working show.
