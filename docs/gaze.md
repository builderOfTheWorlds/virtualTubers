# Roundtable gaze — `app/gaze.py`

## Overview

Makes the roundtable read like a table of people talking to each other:

- While a character is speaking, **every other tile's 3D head turns toward
  the speaker's tile.**
- The **speaker looks at whoever it is talking to**. One addressee: it holds
  on that tile. Several addressees, or the whole table: it **sweeps slowly**
  from one listener to the next over the length of its line, in reading
  order (top row left→right, then bottom row).
- When nobody has spoken for `STAGE_LINGER_S` (6 s), heads ease back to
  their resting 15° profile.
- The speaker's mouth follows its line's real audio envelope.

### Sync: text, voice and head movement

Everything is anchored to **the moment the voice line actually starts
playing**, not to when the scene was cued:

1. The tile that owns the line waits for its voice-gate seat
   (`docs/voice_gate.md`). Until it gets the seat, it shows no bubble and
   publishes nothing, so a line queued behind another voice can't pull
   everyone's attention early.
2. Once it has the seat, `replay.Performer._perform_scene` writes the
   speaking bubble (text), calls `play_wav` (voice), then fires
   `on_voice_start` (head movement). All three happen within a few ms of each
   other.
3. `tile_pane.make_stage_writer` (the `on_voice_start` hook) works out the
   addressees and writes `<relay-dir>/stage.json` as
   `{speaker, addressees, started_at, duration, envelope, …}`.
4. Every tile's head driver (`TileAvatarDriver`, 12 fps) reads the stage
   file through `gaze.StageGaze`. It picks a target with `gaze_target`,
   converts it to `(yaw, pitch)` with `gaze_angles`, eases toward it with
   `GazeController` (τ = 0.35 s, capped at 150°/s so heads turn instead of
   snapping), and passes the result to `TileAvatar.tick(gaze=…, mouth_open=…)`.
   From there it goes to `CodecAvatarProvider.render_tick`, then
   `GPURenderWorker` (4-tuple protocol), then `FrameSource.render_frame(gaze=…)`.

The sweep position and the mouth are both computed from
`now - started_at`, the same wall-clock origin the audio started at, so they
stay locked to the voice.

## Who is a line addressed to (`resolve_addressees`)

The first rule that matches wins:

1. **Explicit**: `addressee` / `addressees` / `to` on any event in the scene.
   The value can be a slot id (`tuber_1`), a speaker id (mapped through the
   cast), a list of either, or `"all"`.
2. **Named in the line**: a character's display name as a whole word
   ("Max-1, what do you do?"). Names come from the show header personas,
   then `voice.speaker_names`, then the `roster:` config.
3. **"Everyone" words** ("everyone", "party", "team", "you all", …): the
   whole table.
4. **The GM** with nobody named is talking to the whole table, so it sweeps.
5. **Anyone else** is answering whoever spoke last, falling back to the GM.

Episode authors who want exact control should use rule 1:

```json
{"type": "assistant_text", "speaker": "tuber_0", "text": "Roll for it.", "addressee": "tuber_1"}
{"type": "user_message", "text": "Listen up, all of you.", "to": "all"}
```

## Signatures

```python
gaze_angles(from_slot, to_slot, columns=4) -> (yaw_rad, pitch_rad)
resolve_addressees(scene, speaker_slot, cast, participants, names_by_slot,
                   previous_speaker=None, gm_slot="tuber_0") -> list[str]
gaze_target(self_slot, stage, now) -> str | None
mouth_open(self_slot, stage, now) -> float
write_stage(path, speaker_slot, addressees, duration_s, started_at=None,
            envelope=None, envelope_rate_hz=None, previous_speaker=None,
            airing_id=None) -> dict
read_stage(path) -> dict | None
GazeController(yaw, pitch, time_constant_s, max_speed).step(yaw, pitch, now) -> (yaw, pitch)
StageGaze(slot, stage_path).sample() -> ((yaw, pitch), mouth_open)
```

Angle conventions (as used by `pixel_raster.make_view_matrix`): positive yaw
turns the face toward screen **right**, and positive pitch tips it **down**.

## Tuning constants

| Name | Default | Effect |
|---|---|---|
| `GAZE_DEPTH_PX` | 700 | Virtual distance to the table. Larger values give subtler turns. |
| `MAX_YAW_RAD` / `MAX_PITCH_RAD` | 60° / 20° | Hard limits so the face never turns into pure profile. |
| `PITCH_SCALE` | 0.6 | Tones down up/down looks across rows. |
| `MIN_SWEEP_DWELL_S` | 1.4 | Shortest time spent on each listener during a sweep. |
| `STAGE_LINGER_S` | 6.0 | How long heads stay on the last speaker before returning to rest. |
| `TURN_TIME_CONSTANT_S` / `MAX_TURN_RAD_PER_S` | 0.35 s / 150°/s | How fast heads turn. |

`GRID_COLUMNS = 4` must match `config/layouts/roundtable.yaml`. This is
enforced by `tests/test_gaze.py::test_grid_matches_roundtable_layout`.

## Dependencies

Stdlib only (`json`, `math`, `os`, `re`, `time`). Consumers:
`tile_pane.py` (writer hook and head driver), `replay.py`
(`on_voice_start`), `tile_avatar.py`, `avatar_providers/codec_avatar.py`,
`gpu_render_worker.py`.

## Error handling

Nothing here raises into a pane:

- A missing or malformed stage file reads as `None`, which gives the rest pose.
- `StageGaze.sample` swallows everything and returns the last pose.
- A failing `on_voice_start` hook is logged by `Performer._voice_started`
  and the show continues.
- A malformed `gaze` passed to `render_frame` falls back to the fixed pose.

Tiles without a 3D head (ASCII faces) ignore gaze entirely.

## Changelog

- **v1.0.0** (2026-09-26): Initial look-at. Listeners face the speaker, the
  speaker faces or sweeps its addressees, the mouth follows the audio
  envelope, and everything is anchored to the voice line's real start time.
  The speaking bubble now appears when the audio starts (after the voice
  gate) instead of when the scene begins.
