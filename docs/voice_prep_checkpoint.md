# voice_prep_checkpoint.py

## Overview

Makes a fresh voiced replay's prep (LLM line + TTS per scene, in
`revoice.prepare_show`) resumable. Before this, prep wrote WAVs into a
`TemporaryDirectory` and nothing was persisted until every scene was done —
a ~4600-scene episode (hours of prep) restarted from scene 1 after any
container restart or crash.

Now each finished scene (spoken text + WAV bytes + measured duration) is
upserted to Postgres's `voice_prep_checkpoint` table as soon as it
completes, through `narration_store.CheckpointStore` — the same
already-trusted Postgres connection the narration cache uses (POSTGRES_*
env), no new volume or bind mount. A restarted prep for the same
episode/config restores every scene whose content hash still matches and
only generates the rest.

Once the finished airing is saved to `voiced_narration`
(`persist_narration` succeeded), its checkpoint rows are deleted. If that
save fails, the rows are kept, so the next attempt restores everything and
goes straight to airing. Abandoned checkpoints are pruned after 14 days
(`CHECKPOINT_TTL_DAYS`) whenever a new prep opens.

Why a separate table rather than writing partial rows into
`voiced_narration`: `load_latest_airing` would treat a half-done airing as
the newest cached one, which breaks `narration: "reuse"`.

## Signature

```python
compute_prep_key(episode, worker_name, voice_config, speed=1.0,
                 skip_llm=False, max_output_lines=None) -> str
compute_scene_hash(scene, words=None, tone=None, voice_name=None) -> str

class PrepCheckpoint:
    def __init__(self, store, prep_key, episode, worker_id)
    def open(self) -> int                       # scenes already checkpointed
    def lookup(self, index, scene_hash, wav_path) -> (text, Narration|None) | None
    def record(self, index, scene_hash, text, narration) -> None
    def clear(self) -> None
    def close(self) -> None

build_checkpoint(episode, worker_name, config, speed=1.0, worker_id=None,
                 max_output_lines=None, store_factory=None) -> PrepCheckpoint | None
```

Integration points: `revoice.prepare_show(..., checkpoint=None)`,
`replay.prepare_voiced_show(..., checkpoint=None)`,
`replay_pane.prepare_voice(..., checkpoint=None)`,
`replay_pane.open_prep_checkpoint()` / `finish_prep_checkpoint()` (solo and
duet-director fresh-prep paths; duet followers and `reuse` hits never prep).

## Parameters

- **prep_key** — sha256 of episode, worker name, speed, `REPLAY_SKIP_LLM`,
  and the sound-affecting voice keys (`provider`, `model_path`, `voice_id`,
  `tts_model`, `rate`, `speakers`, `speaker_names`, `boss_name`,
  `verbatim`). Transport-only keys (`base_url`, `use_cuda`, `cpu_threads`,
  `remote_fallback`) are excluded on purpose: switching a half-done prep
  from CPU to the GPU service still resumes.
- **scene_hash** — sha256 of the planned scene (events/kind/speaker), target
  word count, role tone, symbolic voice name. An edited episode only
  regenerates the changed scenes.
- Config: `voice.checkpoint` (default `true`); env `VOICE_PREP_CHECKPOINT=0`
  disables.

## Return Value

`build_checkpoint` returns `None` when disabled, when the narration store is
unavailable, or when opening the table fails — prep then runs exactly as
before.

## Dependencies

`narration_store` (`CheckpointStore`, `_connect`, psycopg2), `tts_client`
(`Narration`, `wav_duration`), stdlib `hashlib`/`json`.

Table (created idempotently on first use; also in
`docs/sql/02_create_tables.sql`):

```sql
CREATE TABLE IF NOT EXISTS voice_prep_checkpoint (
    prep_key TEXT, scene_index INTEGER, scene_hash TEXT, episode TEXT,
    worker_id TEXT, text TEXT, audio BYTEA, audio_duration_s DOUBLE PRECISION,
    updated_at TIMESTAMPTZ DEFAULT now(), PRIMARY KEY (prep_key, scene_index));
```

## Usage Examples

What `replay_pane.perform_request` does for a fresh solo airing:

```python
checkpoint = open_prep_checkpoint(episode_name, config, name, speed)
persisted = None
try:
    show = prepare_voice(script, config, workdir, name, speed, checkpoint=checkpoint)
    message_id = publish_narration(show, config, episode_name, name)
    persisted = persist_narration(message_id, show, config, episode_name, name)
finally:
    finish_prep_checkpoint(checkpoint, persisted)   # clears rows only if persisted
```

Restart behavior in the logs:

```
[voice_prep_checkpoint] event=opened prep_key=48eeba865264 episode=cyber_police_day1 checkpointed_scenes=2310
[replay_pane] preparing: scene 1/4600: restored boss_talk line from checkpoint
...
[replay_pane] preparing: scene 2311/4600: writing coder_talk line (~42w)
[replay_pane] resumed prep: 2310 scenes restored from checkpoint, 2290 newly prepared
```

The `scene i/N:` prefix is unchanged, so the control panel's progress bar
(`_PREP_RE` in services/control-panel/panel.py) keeps working.

## Error Handling

Best-effort (show-must-air rule): any DB error in `open`/`lookup`/`record`
disables checkpointing for the rest of the pass with ONE
`event=disabled operation=...` log line; prep continues uncheckpointed.
`clear` failures are logged (`event=clear_failed`) and swallowed. A scene
checkpointed silent because its TTS failed is regenerated on resume (not
restored as silent forever).

Memory: restore reads metadata for all scenes but fetches WAV bytes one
scene at a time, so resuming a multi-GB episode never holds all audio in
RAM. (The pre-existing `save_airing` at the end still reads each WAV once.)

## Changelog

- **v1.0.0** (2026-10-02): Initial version. Verified against a real
  postgres:16 (crash after 3/8 scenes → resume restored 3, synthesized 5,
  then cleared) plus 23 unit tests.
