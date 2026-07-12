# narration_store.py

## Overview

Direct Postgres persistence for Rerun Theater narrations. After a voiced
airing, `app/replay_pane.py` saves each scene's spoken line **and** its
synthesized WAV bytes into the `voiced_narration` table via this module —
one row per scene, including the nullable `audio` (BYTEA) and
`audio_duration_s` columns. A later `replay_request` with
`payload.narration: "reuse"` reads the latest cached airing of an episode
back through here instead of calling the LLM + TTS again
(docs/replay_pane.md, docs/operator_commands.md).

Why a direct DB write and not the Kafka → `message-logger` path the text
transcript already rides (`replay_narration` → `insert_voiced_narration`,
docs/message_logger.md): WAV audio for a whole show runs to megabytes, far
past bus message sizes. The pane therefore writes the full rows itself,
reusing the **same `message_id`** it published on Kafka — the logger's
text-only insert (`ON CONFLICT (message_id, scene_index) DO NOTHING`) and
this module's upsert (`ON CONFLICT (message_id, scene_index) DO UPDATE` on
the audio columns) converge on one row set per airing no matter which
lands first.

Everything here is best-effort, matching the show-must-air rule
(docs/revoice.md): missing `psycopg2`, missing `POSTGRES_*` env, or a down
database disable the store — `save_airing`/`load_latest_airing` are only
ever called after `available()` says yes, and callers in `replay_pane.py`
still wrap them to degrade a save/load failure into "not cached" rather
than a crashed show.

## Signature

```python
_REQUIRED_ENV = ("POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD")

def available() -> bool

def save_airing(message_id, worker_id, episode, aired_at, show) -> int

def load_latest_airing(episode) -> list[dict] | None
```

## Parameters

- `message_id` (str/UUID, required) — the id `replay_pane.publish_narration`
  minted for this airing's `replay_narration` bus message (or a fresh
  `uuid.uuid4()` when Kafka is unconfigured); the join key with the
  logger's text-only row.
- `worker_id` (str, required) — persona/worker that performed the airing.
- `episode` (str, required) — the episode key. This is the canonical
  script stem (`Path(source).stem`, e.g.
  `"2026-07-02_04-27-00_6ecdde82"`) — the same value used to resolve the
  episode file in `REPLAY_LIBRARY`.
- `aired_at` (str, required) — ISO 8601 UTC timestamp of the airing.
- `show` (list[dict], required) — the voiced show from
  `revoice.prepare_show`/`prepare_voiced_show`: each scene dict has
  `kind`, `speaker`, `narration` (text), and an `audio`
  (`tts_client.Narration` or `None` for a silent scene).

## Return Value

- `available()` — `True` when this process can reach the store:
  `psycopg2` importable **and** all of `POSTGRES_DB`/`POSTGRES_USER`/
  `POSTGRES_PASSWORD` present in the environment (workers only get these
  when `docker-compose.yml` grants them — local dev without a DB simply
  runs uncached).
- `save_airing()` — the number of scenes saved (`len(show)`). Raises on DB
  failure; callers (`replay_pane.persist_narration`) wrap this best-effort.
- `load_latest_airing()` — a list of dicts, one per scene, ordered by
  `scene_index`: `scene_index`, `scene_kind`, `speaker`, `text`, `audio`
  (`bytes` or `None` — psycopg2 hands `bytea` back as a `memoryview`, so
  this converts it), `audio_duration_s`. Returns `None` when the episode
  has never been cached with audio. Raises on DB failure; callers
  (`replay_pane.load_reused_show`) wrap this best-effort.

The "latest" row set is selected by `LOAD_SQL`: the most recent
`message_id` for that `episode` that has **at least one** scene with
`audio IS NOT NULL`, ordered `aired_at DESC, ingested_at DESC` — a silent
airing (voice off, or every scene's TTS failed) is never returned as a
reuse candidate, since there'd be nothing to play back.

## Dependencies

- `psycopg2` (imported lazily inside `available()`, `_connect()`, and
  `save_airing()` — so a worker without the package installed can still
  import this module and get a clean `available() == False`).
- Postgres table `voiced_narration` (`docs/sql/02_create_tables.sql`,
  `docs/database_schema.md`) — same table `message-logger` writes the
  text-only row into.
- Standard library: `os`.

## Usage Examples

How `app/replay_pane.py` saves a fresh airing right after performing it,
reusing the id it just published to Kafka (`persist_narration`):

```python
import narration_store
from datetime import datetime, timezone

if narration_store.available():
    n = narration_store.save_airing(
        message_id, worker_id, episode,
        aired_at=datetime.now(timezone.utc).isoformat(),
        show=show,  # the voiced show from prepare_voiced_show
    )
    print(f"cached narration ({n} scenes) for reuse")
```

How `replay_pane.load_reused_show` answers a `narration: "reuse"` request —
cached WAVs are written back out to the per-show temp workdir before being
wrapped in a `tts_client.Narration`:

```python
from pathlib import Path
import narration_store
from tts_client import Narration, wav_duration

cached = narration_store.load_latest_airing(episode)
if cached:
    for row in cached:
        if row["audio"]:
            path = Path(workdir) / f"scene_{row['scene_index']:03d}.wav"
            path.write_bytes(row["audio"])
            duration = row["audio_duration_s"] or wav_duration(path)
            narration = Narration(audio_path=path, duration=duration)
```

## Error Handling

- `available()` never raises — a missing env var or missing `psycopg2`
  just returns `False`.
- `_connect()` uses a 5s `connect_timeout` — a down database must stall a
  save/load for seconds, not minutes, since both sit in the critical path
  of the pane preparing or replaying a show.
- `save_airing()` / `load_latest_airing()` raise on any DB failure
  (connection refused, bad credentials, query error) rather than
  swallowing it — degrading a failure into "nothing cached" is the
  caller's job (`replay_pane.py` wraps both in `try`/`except` and logs to
  stderr), keeping this module's SQL failure modes visible to its tests.
- A scene with no synthesized audio (`scene.get("audio")` is `None`, e.g.
  TTS failed or voice is off) still saves a text-only row — `audio` and
  `audio_duration_s` stay `NULL`, matching `message-logger`'s text-only
  insert for the same scene.

## Changelog

- **v1.0.0** (2026-07-12): Initial version — `available()`,
  `save_airing()` (upsert on `(message_id, scene_index)`, audio columns
  only), `load_latest_airing()` (latest airing-with-audio per episode).
  Wired into `app/replay_pane.py`'s `persist_narration`/`load_reused_show`
  and the `replay_request` `payload.narration: "reuse"` path
  (docs/operator_commands.md).
