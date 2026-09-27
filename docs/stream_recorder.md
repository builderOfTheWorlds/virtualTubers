# stream_recorder

## Overview

Saving a replay airing to a video file. The recording is the **exact
encoded stream the broadcaster sends to the streaming RTMP endpoint** —
no second capture, no re-encode, no extra GPU/CPU encode load.

Pipeline:

```
x11grab + pulse ─► ffmpeg (h264_nvenc / libx264, CBR) ─► tee ─┬─► [flv]            rtmp://… (Twitch, unchanged)
                                                               ├─► [flv onfail=ignore] local preview (if enabled)
                                                               └─► [mpegts onfail=ignore] udp://127.0.0.1:23000   (recording tap)
                                                                                          │
                          only during a recorded airing:  ffmpeg -i udp://… -c copy -fs <cap> ─► /data/recordings/<id>/<worker>.mp4
```

- **Tap** (`app/stream_supervisor.py`, `--record-tap-url`): the broadcaster's
  output always carries a container-local UDP MPEG-TS leg when
  `RECORDING_TAP_ENABLED=1` (default, `startup.sh`). With nobody listening
  the datagrams are dropped by the kernel. `onfail=ignore` means a tap
  problem can never take the live stream down.
- **Recorder** (`app/stream_recorder.py`): `replay_pane` wraps *only the
  performance* (after voice prep) in `stream_recorder.recording(...)`,
  which starts a stream-copy ffmpeg into fragmented MP4 and stops it with
  SIGINT when the show ends / is stopped / crashes. Fragmented MP4 means
  even a killed or capped recorder leaves a playable file.
- **Budget** (docs/recording_budget.md): the Play is refused before anything
  starts if the estimate doesn't fit the 5 GB cap.

Operator flow (control panel → Rerun Theater):

1. Pick **Save to file**: `off` (default) / `roundtable` (the combined
   channel, 1 stream) / `all 7 streams`, then **Play**.
2. The panel calls message-api `POST /recordings` with the episode and
   streams. Over budget → red banner with the reason, **nothing airs**.
3. Admitted → the banner shows the recording id, estimated size/length and
   budget left; `replay_request.payload.record = {recording_id, max_bytes}`
   is sent only to the recorded workers.
4. The **Recordings** list below the library shows usage vs the 5 GB limit,
   download links (streamed through message-api) and Delete.

Guarantees:

- A recorder only writes into a directory message-api reserved, and only if
  its worker id is one of the reservation's `streams` (a duet follower handed
  the director's `record` doesn't add an unbudgeted file).
- The file size is capped by ffmpeg `-fs`, set `FS_MARGIN_BYTES` (3 MB,
  about 2 GOP-fragments at the stream bitrate) below the reserved cap because
  `-fs` is checked between fragments and overshoots by up to one. Measured on
  the worker image: 6 MB cap → 3.4 MB file.
- Every recording failure is logged (`[stream_recorder] ERROR event=…`) and
  the airing continues unrecorded.

## Signature

```python
parse_record_request(record) -> tuple[str, int] | None
recording_path(recording_id, worker_id, root=None) -> Path
effective_fs_limit(max_bytes) -> int
build_recorder_cmd(tap_url, output_path, max_bytes) -> list[str]

class StreamRecorder:
    def __init__(self, recording_id, max_bytes, worker_id, tap_url=None,
                 root=None, popen=subprocess.Popen, sleep=time.sleep, clock=time.monotonic)
    def start(self) -> bool
    def stop(self) -> int          # final size in bytes
    def mark_done(self, size) -> None

@contextmanager
def recording(record, worker_id, **kwargs) -> Iterator[StreamRecorder | None]

# app/stream_supervisor.py
build_ffmpeg_cmd(..., record_tap_url=None)
```

## Parameters

| Name | Where | Default | Notes |
|---|---|---|---|
| `RECORDING_TAP_ENABLED` | worker env / startup.sh | `1` | `0` removes the tap leg from the broadcaster entirely. |
| `RECORDING_TAP_URL` | worker env | `udp://127.0.0.1:23000` | Must match between broadcaster and recorder (same container). |
| `RECORDINGS_DIR` | worker + message-api env | `/data/recordings` | Compose binds host `./recordings` there. |
| `record` | `replay_request.payload` | absent | `{recording_id, max_bytes}`; anything else → airs unrecorded. |
| `max_bytes` | int | — | Clamped to the reservation's `max_bytes_per_stream`. |

## Return Value

`start()` is True when the recorder is running (first bytes seen or still
alive after `START_WAIT_S`), False when it could not start (no reservation,
stream not reserved, ffmpeg missing/died). `stop()` returns the saved size;
an empty file is deleted and returns 0. Both always leave the `.done` marker.

## Dependencies

- `ffmpeg` in the worker image (already present for the broadcaster).
- `app/recording_budget.py` (paths, reservations, done markers).
- Callers: `app/replay_pane.py` (`perform_request`), `app/agent_handlers/replay_relay.py`
  (forwards `record` from the bus into the relay file).

## Usage Examples

Record one airing from the shell (same as the control panel does):

```bash
REC=$(curl -s -X POST http://localhost:8000/recordings \
  -H 'Content-Type: application/json' \
  -d '{"episode":"my_episode","streams":["roundtable"]}')
ID=$(echo "$REC" | jq -r .recording_id); CAP=$(echo "$REC" | jq -r .max_bytes_per_stream)
curl -s -X POST http://localhost:8000/messages -H 'Content-Type: application/json' \
  -d "{\"to\":\"roundtable\",\"type\":\"replay_request\",\"payload\":{\"episode\":\"my_episode\",\"record\":{\"recording_id\":\"$ID\",\"max_bytes\":$CAP}}}"
# file appears at ./recordings/$ID/roundtable.mp4 on the host
```

In code:

```python
with stream_recorder.recording(request.get("record"), self_id):
    completed = performer.perform(script, show=show)
```

## Error Handling

| Log event | Meaning | Airing |
|---|---|---|
| `invalid_record_request` | malformed `record` payload | continues unrecorded |
| `no_reservation` / `stream_not_budgeted` | not admitted by message-api | continues unrecorded |
| `no_bytes_yet` (WARN) | tap silent — `RECORDING_TAP_ENABLED=0`? | keeps waiting |
| `recorder_exited_early` / `start_failed` | ffmpeg died / missing | continues unrecorded |
| `size_cap_reached` (WARN) | airing ran longer than estimated; file truncated at cap | unaffected |
| `empty_recording` | no bytes captured; file removed | unaffected |
| `stop_timeout` | recorder ignored SIGINT; killed (file stays playable) | unaffected |

Verified on the worker image (ffmpeg 4.4): real `build_ffmpeg_cmd` tap +
`StreamRecorder` started mid-stream → valid h264+aac MP4, decodes with zero
errors, primary output unaffected (full 40 s).

## Changelog

- v1.0.0 (2026-09-27) — Initial version: zero-re-encode UDP tap, stream-copy
  recorder, per-stream `-fs` cap with fragment margin.
