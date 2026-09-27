"""
recorder.py — groups rendered bars into mood segments and writes them to
Postgres (music_store.py) off the audio thread.

A segment closes when the target mood/intensity/source changes, or after
`max_segment_s` of continuous music (so a four-hour peaceful scene still
lands in the DB in bounded chunks). Each closed segment is encoded
(Opus-in-Ogg via ffmpeg when available, else 16-bit WAV) and queued; a
single daemon thread drains the queue. A DB failure is logged and the
segment dropped — recording must never stall or crash the music.
"""
import io
import logging
import queue
import shutil
import subprocess
import threading
import wave

import numpy as np

log = logging.getLogger("music.recorder")


def encode_audio(audio, sample_rate, fmt="opus"):
    """float32 (n,2) -> (bytes, format_name). Falls back to WAV when ffmpeg
    is missing or fails."""
    pcm = (np.clip(audio, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()
    if fmt == "opus" and shutil.which("ffmpeg"):
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "s16le",
               "-ar", str(sample_rate), "-ac", "2", "-i", "pipe:0",
               "-c:a", "libopus", "-b:a", "96k", "-f", "ogg", "pipe:1"]
        try:
            proc = subprocess.run(cmd, input=pcm, capture_output=True, timeout=60)
            if proc.returncode == 0 and proc.stdout:
                return proc.stdout, "ogg/opus"
            log.warning("opus encode failed rc=%s err=%s — falling back to wav",
                        proc.returncode, proc.stderr[-200:])
        except (OSError, subprocess.SubprocessError) as exc:
            log.warning("opus encode error=%s — falling back to wav", exc)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm)
    return buf.getvalue(), "wav"


class MusicRecorder:
    def __init__(self, store, engine, worker_id="", campaign="", episode="",
                 max_segment_s=60.0, audio_format="opus", record_audio=True):
        from music.theme import theme_to_dict
        self.store = store
        self.engine = engine
        self.max_segment_s = float(max_segment_s)
        self.audio_format = audio_format
        self.record_audio = record_audio
        self._theme_dict = theme_to_dict(engine.theme)
        self._meta = {"worker_id": worker_id, "campaign": campaign, "episode": episode}
        self._session_id = None
        self._seq = 0
        self._bars = []
        self._key = None
        self._source = ""
        self._scene_id = ""
        self._queue = queue.Queue(maxsize=64)
        self._thread = None
        self.errors = 0
        self.saved = 0

    # ── lifecycle ──────────────────────────────────────────────────────────
    def start(self):
        """Create the schema/theme/session rows. Raises on DB failure (the
        caller decides whether to run unrecorded)."""
        from music.engine import ENGINE_VERSION
        self.store.ensure_schema()
        self.store.upsert_theme(self._theme_dict, campaign=self._meta["campaign"])
        self._session_id = self.store.start_session(
            self._theme_dict, ENGINE_VERSION, self.engine.sr, **self._meta)
        self._thread = threading.Thread(target=self._drain, name="music-recorder", daemon=True)
        self._thread.start()
        return self._session_id

    @property
    def session_id(self):
        return self._session_id

    def set_context(self, source="", scene_id=""):
        """Why the current mood is playing (e.g. 'scene', 'gm_override')."""
        self._source, self._scene_id = str(source or ""), str(scene_id or "")

    def add_bar(self, bar):
        key = (bar.target_mood, round(bar.intensity, 3), self._source, self._scene_id)
        if self._bars and (key != self._key or self._elapsed() >= self.max_segment_s):
            self._close_segment()
        self._key = key
        self._bars.append(bar)

    def close(self, timeout=30.0):
        """Flush the open segment, wait for the writer, end the session."""
        if self._bars:
            self._close_segment()
        if self._thread:
            self._queue.put(None)
            self._thread.join(timeout)
        if self._session_id is not None:
            try:
                self.store.end_session(self._session_id)
            except Exception as exc:  # noqa: BLE001 — best-effort
                log.error("music end_session failed id=%s err=%s", self._session_id, exc)
                self.errors += 1
        log.info("music recorder closed session=%s saved=%d errors=%d",
                 self._session_id, self.saved, self.errors)

    # ── internals ──────────────────────────────────────────────────────────
    def _elapsed(self):
        return sum(b.duration_s for b in self._bars)

    def _close_segment(self):
        bars, self._bars = self._bars, []
        first = bars[0]
        mood, intensity, source, scene_id = self._key
        row = {
            "session_id": self._session_id, "seq": self._seq, "mood": mood,
            "intensity": float(intensity), "source": source, "scene_id": scene_id,
            "bar_start": first.index, "bar_count": len(bars),
            "start_s": first.start_s, "duration_s": round(sum(b.duration_s for b in bars), 4),
            "params": [dict(b.params.to_dict(), bar=b.index, chord_degree=b.chord_degree,
                            motif=b.motif) for b in bars],
            "notes": [dict(n, bar=b.index) for b in bars for n in b.notes],
        }
        audio = np.concatenate([b.audio for b in bars]) if self.record_audio else None
        self._seq += 1
        try:
            self._queue.put_nowait((row, audio))
        except queue.Full:
            self.errors += 1
            log.error("music recorder queue full — dropping segment seq=%s", row["seq"])

    def _drain(self):
        while True:
            item = self._queue.get()
            if item is None:
                return
            row, audio = item
            try:
                if audio is not None:
                    row["audio"], row["audio_format"] = encode_audio(
                        audio, self.engine.sr, self.audio_format)
                else:
                    row["audio"], row["audio_format"] = None, ""
                self.store.save_segment(row)
                self.saved += 1
            except Exception as exc:  # noqa: BLE001 — never kill the music
                self.errors += 1
                log.error("music segment save failed session=%s seq=%s err=%s",
                          row.get("session_id"), row.get("seq"), exc)
