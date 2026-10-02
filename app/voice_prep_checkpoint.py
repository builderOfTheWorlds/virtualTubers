"""
voice_prep_checkpoint.py
Resumable replay voice prep (docs/voice_prep_checkpoint.md).

revoice.prepare_show() narrates + synthesizes every scene of an episode
before it airs. For a ~4600-scene episode that is hours of work, and it used
to live only in a TemporaryDirectory until the very last scene — a container
restart or crash mid-prep started from scene 1 again. This module persists
each finished scene (spoken text + WAV bytes + measured duration) to
Postgres's voice_prep_checkpoint table (app/narration_store.py — the same
already-trusted database the narration cache uses; no new volume or bind
mount) as soon as it completes, and lets a restarted prep restore every
scene whose content hash still matches instead of redoing it.

Identity:
  * prep_key   — sha256 over everything that shapes the WHOLE show's
                 audio/text: episode name, worker name, speed, the
                 sound-affecting voice config (provider, model paths,
                 speakers, rate, boss/speaker names, verbatim) and whether
                 the LLM is skipped. Transport-only settings (base_url,
                 use_cuda, cpu_threads, remote_fallback) are excluded on
                 purpose: switching a half-done prep from CPU to the GPU
                 service must still resume — same .onnx, same voice.
  * scene_hash — sha256 over one planned scene (its events/kind/speaker),
                 the target word count, tone, and symbolic voice name. An
                 edited episode only regenerates the scenes that changed.

Best-effort throughout (show-must-air rule, docs/revoice.md): any DB error
disables checkpointing for the rest of the pass with one log line, and the
prep simply carries on uncheckpointed.
"""
import hashlib
import json
import sys
from pathlib import Path

import narration_store

# voice config keys that change WHAT is said or HOW it sounds. Everything
# else (base_url, use_cuda, cpu_threads, remote_fallback, ...) only changes
# where/how fast synthesis runs.
_SOUND_KEYS = ("provider", "model_path", "voice_id", "tts_model", "rate", "speakers",
               "speaker_names", "boss_name", "verbatim")


def _log(event, **fields):
    parts = " ".join(f"{key}={value}" for key, value in fields.items())
    print(f"[voice_prep_checkpoint] event={event} {parts}".rstrip(), file=sys.stderr,
          flush=True)


def _digest(payload):
    blob = json.dumps(payload, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def compute_prep_key(episode, worker_name, voice_config, speed=1.0, skip_llm=False,
                     max_output_lines=None):
    voice_config = voice_config or {}
    sound = {key: voice_config.get(key) for key in _SOUND_KEYS}
    return _digest({
        "v": 1, "episode": episode, "worker_name": worker_name, "speed": speed,
        "skip_llm": bool(skip_llm), "max_output_lines": max_output_lines, "voice": sound,
    })


def compute_scene_hash(scene, words=None, tone=None, voice_name=None):
    planned = {key: value for key, value in scene.items()
               if key not in ("narration", "audio", "target_duration", "owned")}
    return _digest({"scene": planned, "words": words, "tone": tone, "voice_name": voice_name})


class PrepCheckpoint:
    """Per-pass checkpoint handle. `store` is a narration_store.CheckpointStore
    (or a test double with the same methods)."""

    def __init__(self, store, prep_key, episode, worker_id):
        self.store = store
        self.prep_key = prep_key
        self.episode = episode
        self.worker_id = worker_id
        self.enabled = True
        self.restored = 0
        self.saved = 0
        self._index = {}

    def _disable(self, operation, exc):
        if self.enabled:
            _log("disabled", operation=operation, error=repr(exc)[:200],
                 prep_key=self.prep_key[:12])
        self.enabled = False

    def open(self):
        """Create the table if needed and read this prep's existing scenes.
        Returns the number of checkpointed scenes found (0 when disabled)."""
        try:
            self.store.ensure_schema()
            self._index = self.store.load_index(self.prep_key)
        except Exception as exc:
            self._disable("open", exc)
            return 0
        _log("opened", prep_key=self.prep_key[:12], episode=self.episode,
             checkpointed_scenes=len(self._index))
        return len(self._index)

    def lookup(self, index, scene_hash, wav_path):
        """(text, Narration-or-None) restored from the checkpoint, writing the
        WAV to `wav_path` — or None when this scene must be (re)generated."""
        if not self.enabled:
            return None
        entry = self._index.get(index)
        if entry is None or entry["scene_hash"] != scene_hash:
            return None
        narration = None
        if entry["has_audio"]:
            try:
                audio = self.store.load_audio(self.prep_key, index)
            except Exception as exc:
                self._disable("load_audio", exc)
                return None
            if audio is None:
                return None
            from tts_client import Narration, wav_duration

            wav_path = Path(wav_path)
            wav_path.parent.mkdir(parents=True, exist_ok=True)
            wav_path.write_bytes(audio)
            duration = entry["audio_duration_s"] or wav_duration(wav_path)
            narration = Narration(audio_path=wav_path, duration=duration)
        self.restored += 1
        return entry["text"], narration

    def record(self, index, scene_hash, text, narration):
        if not self.enabled:
            return
        try:
            audio_bytes = narration.audio_path.read_bytes() if narration is not None else None
            duration = narration.duration if narration is not None else None
            self.store.save_scene(self.prep_key, index, scene_hash, self.episode,
                                  self.worker_id, text, audio_bytes, duration)
            self.saved += 1
        except Exception as exc:
            self._disable("save_scene", exc)

    def clear(self):
        """Drop this prep's rows — called once the finished airing is safely
        in voiced_narration (or never, if that save failed: the checkpoint
        then still lets the next attempt skip straight to airing)."""
        try:
            self.store.clear(self.prep_key)
            _log("cleared", prep_key=self.prep_key[:12], episode=self.episode)
        except Exception as exc:
            _log("clear_failed", error=repr(exc)[:200], prep_key=self.prep_key[:12])

    def close(self):
        try:
            self.store.close()
        except Exception:
            pass


def build_checkpoint(episode, worker_name, config, speed=1.0, worker_id=None,
                     max_output_lines=None, store_factory=None):
    """A ready-to-use PrepCheckpoint for one fresh prep pass, or None when
    checkpointing is off (voice.checkpoint: false / env
    VOICE_PREP_CHECKPOINT=0), there's no episode name, or the narration
    store isn't configured. Never raises."""
    import os

    voice_config = (config or {}).get("voice") or {}
    env = os.environ.get("VOICE_PREP_CHECKPOINT")
    setting = env if env not in (None, "") else voice_config.get("checkpoint", True)
    if str(setting).strip().lower() in ("0", "false", "no", "off") or not episode:
        return None
    try:
        if store_factory is None:
            if not narration_store.available():
                return None
            store_factory = narration_store.CheckpointStore
        skip_llm = os.environ.get("REPLAY_SKIP_LLM", "").lower() in ("1", "true", "yes")
        prep_key = compute_prep_key(episode, worker_name, voice_config, speed=speed,
                                    skip_llm=skip_llm, max_output_lines=max_output_lines)
        checkpoint = PrepCheckpoint(store_factory(), prep_key, episode,
                                    worker_id or worker_name)
    except Exception as exc:
        _log("build_failed", error=repr(exc)[:200])
        return None
    checkpoint.open()
    if not checkpoint.enabled:
        checkpoint.close()
        return None
    return checkpoint
