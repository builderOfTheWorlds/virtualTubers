# app/live_pane.py — live office transcript on the roundtable

## Overview

`app/live_pane.py` puts the ashiorid_office show's **live** conversation on
the roundtable (build plan OB-32, `agent_dnd_architecture.md` W2 / §7.1).
Each office handler's spoken line appears on the speaker's tile, voiced, in
the order the lines were committed. The Party Member's tile (`tuber_7`)
never shows text; its head follows whoever is speaking.

It **extends the existing relay-file convention** (docs/relay_io.md,
`roundtable_stream_design.md` §4) instead of adding a parallel path. The
hops are the same ones a recorded replay uses. Only the feed changes: a
live spool of committed lines instead of an episode.

```
seat container (tuber_N)            roundtable container (TILE_RELAY_DIR)
─────────────────────────           ─────────────────────────────────────────────
office handler speaks a line
  publish_office_line ──bus──► agent.py  handle_office_line
     office_line → "roundtable"        └─► <relay>/live/<ns>-<id>.json   (spool)
                                     replay_pane.py main loop (director)
                                       LiveDirector.drain_once
                                         TTS (voice.speakers[tuber_N])
                                         └─► <relay>/<tuber_N>.live.json
                                     tile_pane.py --slot tuber_N
                                       live_pane.handle_live_once
                                         Performer + voice gate + stage writer
                                         └─► <relay>/stage.json → every head turns
party member (tuber_7)
  observer_idle_tick ──bus──► agent.py  handle_observer_pose
     observer_pose → broadcast         └─► <relay>/tuber_7.pose.json
                                     tile_pane.py --slot tuber_7
                                       ObserverGaze: stage.json speaker, else pose
```

Rules the design fixed and this module keeps:

- **No pane consumes Kafka.** The roundtable's own `agent.py` turns bus
  messages into files, as it already does for `replay_request`.
- **The voice gate is unchanged.** A live line plays through the same
  `replay.Performer._perform_scene` a replay tile uses, with the gate from
  `replay_pane.build_voice_gate`. It holds the same seat files, so a live
  line and a replay line can never overlap.
- **The refusal path is unchanged.** `perform_director_request` and its
  `refuse()` are not touched. A live line whose TTS fails still airs, as
  text held for its estimated length.
- **Recorded and live airings are mutually exclusive.** A pending
  `replay_request` makes the drain yield, and the replay airs next. Queued
  lines wait, and are dropped once older than `max_age_s`.
- **The director is the clock.** It hands one line, waits for the tile to
  claim it, then waits out the line's duration plus `line_gap_s` before the
  next. So lines air in commit order, even though the voice gate's flock is
  not FIFO.

### Relay files added (all under `TILE_RELAY_DIR`, default `/tmp/tiles`)

| File | Writer | Reader | Shape |
|---|---|---|---|
| `live/<20-digit ns>-<8 hex>.json` | roundtable agent (`handle_office_line`) | director (`LiveDirector`, consume) | `{type: office_line, line_id, seat, text, emotion, to?, correlation_id, sent_at, received_at}` |
| `<slot>.live.json` | director (`LiveDirector.hand`) | tile (`handle_live_once`, consume) | `{type: live_line, line_id, speaker, name, text, emotion, audio_path, duration, to?, correlation_id, created_at}` |
| `live_audio/<line_id>.wav` | director (TTS) | tile (plays, then deletes) | WAV; the director prunes files older than 10 min |
| `<observer>.pose.json` | roundtable agent (`handle_observer_pose`) | observer tile head (`ObserverGaze`) | `{seat, pose, gaze_target, at}` |

All writes go through `relay_io.atomic_write_json`, and all claims through
`relay_io.consume_json`, so the race rules in docs/relay_io.md hold.

### Bus message added

`office_line`, sent by a seat to `"roundtable"`:
`{seat, text, emotion, to?}`, with the office chain's `correlation_id`.
The roundtable accepts it only when the sender **is** the seat, the seat
is one of `tuber_0..tuber_7`, it is not the observer seat, and the text is
not empty.

### Where lines are published (`app/agent_handlers/office.py`)

There is one `live_pane.publish_office_line(...)` call right after each
spoken line's `_state(...)` write: `issue_directive`; `handle_directive`
(the Tech Lead's acknowledgement, the Analyst's plan, the Marketing and
Office Manager reports); `handle_functional_plan`; `handle_technical_plan`;
`handle_status_report`; `handle_phase_change` (when narrated);
`office_manager_idle_tick` (when narrated); `handle_test_request` (the
Tester's narration); `engineer_handoff` (the Engineer's narration); and
`tech_lead_after_test_passed`. The Party Member never publishes.

## Signature

```python
# seat side
publish_office_line(worker_id, agent_config, producer, line, emotion=None,
                    correlation_id=None, to=None) -> dict | None
# roundtable agent side (dispatched via agent_handlers/live_transcript.py)
handle_office_line(worker_id, agent_config, msg, relay_dir=None) -> str | None
handle_observer_pose(worker_id, agent_config, msg, relay_dir=None) -> dict | None
# director side
class LiveDirector:
    def __init__(self, config, relay_dir=None, tts=None, clock=time.time,
                 sleep=time.sleep, mono=time.monotonic)
    @classmethod
    def from_config(cls, config, relay_dir=None) -> "LiveDirector | None"
    def drain_once(self, should_yield=None, limit=None) -> int
# tile side
handle_live_once(slot, relay_dir, state_path=None, config=None, out=None) -> bool
draw_idle(slot, state_path=None, config=None, out=None) -> list[str]
make_gaze_source(slot, stage_path, config=None) -> callable
class ObserverGaze:
    def __init__(self, slot, stage_path, pose_path, controller=None,
                 clock=time.time, mono=time.monotonic)
    def sample(self) -> ((yaw, pitch), mouth_open)
```

## Parameters

Roundtable config, `agent.live` (config/workers/office/roundtable.yaml).
The feed runs only when all three gates hold: the `TILE_RELAY_DIR` env var
is set, `agent.role` is `roundtable`, and `enabled` is true.

| Key | Type | Default | Meaning |
|---|---|---|---|
| `enabled` | bool | false (block absent) | Turns the live feed on. The dev-team roundtable has no block. |
| `observer_slot` | str | `tuber_7` | The seat that never gets text. |
| `max_age_s` | float | 120 | A queued line older than this is dropped. |
| `consume_timeout_s` | float | 20 | A line the tile hasn't claimed by then is withdrawn (e.g. the tile is airing a replay). |
| `line_gap_s` | float | 0.5 | Pause between two committed lines. |
| `spool_max` | int | 200 | Maximum queued lines; the oldest are dropped first. |

Seat config, `agent.office.live_transcript` (bool, default false): the seat
publishes its spoken lines. It is off by default, so the existing office
handler tests (which assert on every message sent) are unaffected.

## Return Value

- `publish_office_line` returns the sent message, or None (off, Party
  Member, empty line, or a send failure).
- `handle_office_line` returns the spool path, or None.
  `handle_observer_pose` returns the written pose dict, or None.
- `LiveDirector.from_config` returns None when the feed is off.
  `drain_once` returns the number of lines the tiles claimed.
- `handle_live_once` returns True when this tile performed a line. It
  returns False when idle, when the feed is off, for the observer seat (its
  file is claimed and discarded), and when the file is malformed.

## Dependencies

- `app/relay_io.py`: atomic writes, consume, and cleanup.
- `app/gaze.py`: seat geometry, the stage file, `GazeController`,
  `StageGaze`.
- `app/tile_pane.py`: `TileRenderer`, `render_tile`, `make_stage_writer`,
  `write_tile_state`.
- `app/replay_pane.py`: `build_voice_gate` and the stop file.
  `app/replay.py`: `Performer`, `Pacer`.
- `app/tts_client.py`: `build_tts_client`, `Narration`.
- `app/message_bus.py`: `build_message`. `app/office/roles.py`: `SEAT`.
- Wiring (small edits): `app/replay_pane.py main()` builds the director;
  `app/tile_pane.py main()` polls live lines and picks the gaze source;
  `app/agent_handlers/__init__.py` registers `office_line` and
  `observer_pose`.

## Usage Examples

Enable a seat, then watch a line reach the roundtable:

```yaml
# config/workers/office/tech_lead.yaml (per speaking seat, NOT party_member)
agent:
  office:
    live_transcript: true
```

```bash
# inside worker-roundtable
ls /tmp/tiles/live/            # queued lines (empty when the director keeps up)
cat /tmp/tiles/tuber_1.live.json   # the line tuber_1's tile is about to speak
docker logs virtualtubers-worker-roundtable-1 2>&1 | grep 'event=live_line_handed'
```

Drive the pipeline in-process (what tests/test_live_pane.py does):

```python
import live_pane
cfg = {"agent": {"role": "roundtable", "live": {"enabled": True}}}
live_pane.handle_office_line("roundtable", cfg["agent"], office_line_msg)
director = live_pane.LiveDirector(cfg, relay_dir="/tmp/tiles", tts=None)
director.drain_once()   # hands each line to <relay>/<seat>.live.json in order
```

## Error Handling

Nothing here raises into its caller. Every failure is logged as a
structured `event=... key=value` line on stderr and degrades:

| Situation | Behaviour |
|---|---|
| Refused `office_line` (spoofed sender, unknown seat, observer, empty) | `event=office_line_refused reason=...`; nothing is spooled. |
| Spool write fails | `event=office_line_spool_failed` (ERROR); the line is lost. |
| TTS fails | `event=live_tts_failed` (WARN); the line airs as text, held for its estimated length. |
| Tile doesn't claim the line | `event=live_line_withdrawn` (WARN); the file and the WAV are removed. |
| Stale or invalid spool entry | `event=live_line_dropped reason=stale|invalid`. |
| Tile perform error | `event=live_line_failed` (ERROR); the tile returns to its loop. |
| Operator `replay_stop` mid-line | `event=live_line_stopped`; the voice-gate seat is released. |
| A seat's publish fails | `event=office_line_publish_failed` (ERROR) in that seat's log; the office chain carries on. |

## Known gaps

- **The seats are not yet opted in.** `agent.office.live_transcript: true`
  must be added to the seven speaking seat configs
  (`config/workers/office/{ceo,tech_lead,analyst,engineer,tester,marketing,office_manager}.yaml`).
  Those files are outside OB-32's ownership, so this is a follow-up.
- **The day runner's end-of-day wrap-up line** (`app/office/day_runner.py`,
  OB-30) is not published yet.
- **The Party Member's own channel** still rotates its gaze.
  `agent.office.observer.stage_path` can't see the roundtable's
  `stage.json`, because there is no shared volume. The roundtable *tile*
  follows the live speaker directly.
- **The replay path does not guard the observer.** A recorded episode that
  casts lines to `tuber_7` still shows them on its tile.

## Changelog

- v1.0.0 (2026-09-28) — Created (OB-32). Live office lines are spooled by
  the roundtable agent, voiced by the director, and performed on the
  speaker's tile through the existing voice gate and stage writer. The
  observer tile gets a gaze-only head (`ObserverGaze`).
