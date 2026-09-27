# recording_budget

## Overview

`app/recording_budget.py` owns the storage side of replay recordings: the
**hard 5 GB cap** on all saved recordings together, the **size estimate**
that is checked *before* a recorded airing is allowed to start, and the
**reservations** that keep concurrent recordings under the cap.

It is pure stdlib (plus `revoice`/`replay` pacing helpers) — message-api
imports it for the `/recordings` endpoints, and `app/stream_recorder.py`
imports its path/marker helpers. No Postgres/Redis/Kafka.

How the cap is enforced end to end:

1. **Estimate** — `estimate_recording()` predicts the airing's wall-clock
   length from the same pacing model the performer uses
   (`revoice.plan_scenes` / `scene_visual_seconds`), taking the longer of
   visual pacing and spoken-line length per scene, x `DURATION_SAFETY`
   (1.25) + `FIXED_OVERHEAD_S` (15 s). Bytes = seconds x stream bitrate
   (4500 k video + 128 k audio, CBR) x `CONTAINER_OVERHEAD` (1.05) x number
   of streams recorded. ~2.2 GB per stream-hour.
2. **Admission** — `check_budget()` refuses when the estimate exceeds
   `limit - used`. Nothing is written on refusal.
3. **Per-stream hard cap** — an admitted recording gets
   `max_bytes_per_stream = min(estimate x CAP_HEADROOM (1.5), remaining / streams)`.
   The recorder passes this to ffmpeg `-fs` (minus a one-fragment margin,
   see docs/stream_recorder.md), so no file can outgrow it even if the
   estimate is wrong.
4. **Reservation** — `reserve()` writes `<id>/.reservation.json`
   (`streams`, `max_bytes_per_stream`, `expires_at`). While live, a
   recording counts as `max(bytes on disk, streams x cap)`, so a second
   recorded Play can't be admitted into space the first may still grow
   into. Each recorder drops `.<worker>.done` when it stops; once all
   streams are done (or the reservation expires after
   airing estimate + 45 min) it counts at its real size.

Result: sum(live caps) + sum(finished sizes) <= limit.

## Signature

```python
resolve_recordings_dir() -> str
resolve_max_bytes() -> int
sanitize_recording_id(value) -> str
done_marker_name(worker_id) -> str
new_recording_id(episode, now=None) -> str
stream_bytes_per_second() -> float
estimate_scene_seconds(scene: dict, speed=1.0, line_gap_s=0.0) -> float
estimate_duration_seconds(script: dict, speed=1.0) -> float
estimate_recording(script: dict, streams=1, speed=1.0) -> dict
read_reservation(directory) -> dict | None
recording_status(directory, now=None) -> dict
budget_usage(root, now=None) -> int
list_recordings(root, now=None) -> list[dict]
check_budget(estimate: dict, used_bytes: int, limit_bytes: int) -> dict
reserve(root, episode, script, stream_ids, limit_bytes, speed=1.0, now=None) -> dict
delete_recording(root, recording_id) -> bool
format_bytes(n) -> str
format_duration(seconds) -> str
```

## Parameters

| Name | Type | Notes |
|---|---|---|
| `RECORDINGS_DIR` (env) | path | Default `/data/recordings` (host `./recordings`, bind-mounted into message-api and every worker). |
| `RECORDINGS_MAX_BYTES` (env) | int bytes | Default `5000000000` (decimal 5 GB). Invalid / <= 0 falls back to the default, never to unlimited. Set on message-api only. |
| `script` | dict | Episode script as stored by `episode_store`. `show.audio.line_gap_s` is honoured. |
| `streams` / `stream_ids` | int / list[str] | How many / which worker streams are recorded (1 = roundtable, 7 = all). |
| `speed` | float | Replay speed; only shortens the visual side — speech runs at TTS pace. |
| `now` | float epoch s | Injectable clock for tests. |

## Return Value

- `estimate_recording` → `{duration_s, bytes_per_stream, streams, total_bytes}`.
- `check_budget` / `reserve` → `{allowed, reason, estimated_bytes,
  estimated_duration_s, used_bytes, limit_bytes, remaining_bytes,
  max_bytes_per_stream, reserved_bytes}`; `reserve` adds `recording_id`
  (None when refused).
- `recording_status` → `{id, bytes, reserved_bytes, live, files[]}`.
- `budget_usage` → bytes counted against the cap right now.

## Dependencies

- `revoice` (`WORDS_PER_SECOND`, `plan_scenes`, `scene_visual_seconds`, `target_words`).
- stdlib: `json`, `os`, `re`, `shutil`, `time`, `uuid`, `pathlib`, `datetime`, `logging`.
- Consumers: `services/message-api/api.py` (`/recordings*`), `app/stream_recorder.py`.

## Usage Examples

Estimate an airing without reserving:

```bash
curl -s -X POST http://localhost:8000/recordings/estimate \
  -H 'Content-Type: application/json' \
  -d '{"episode": "my_episode", "streams": ["roundtable"]}'
# {"allowed": true, "estimated_bytes": 1830000000, "remaining_bytes": 5000000000, ...}
```

In Python:

```python
import recording_budget as rb
d = rb.reserve("/data/recordings", "ep1", script, ["roundtable"], rb.resolve_max_bytes())
if not d["allowed"]:
    print(d["reason"])  # "estimated 6.10 GB exceeds the 4.20 GB left of the 5.00 GB recordings budget"
```

## Error Handling

- Refusal is a normal return (`allowed: False` + human `reason`), not an exception.
- `reserve()` may raise `OSError` if the recordings dir can't be created —
  message-api maps it to HTTP 503.
- `sanitize_recording_id` / `delete_recording` reject traversal (`..`, `/`);
  `delete_recording` returns False rather than touch anything outside the root.
- Corrupt `.reservation.json` is treated as "no reservation" (counts at real size).

## Changelog

- v1.0.0 (2026-09-27) — Initial version: 5 GB budget, pre-start size
  estimate, reservations + done markers.
