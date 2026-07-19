"""
tts_client.py
Provider-switchable text-to-speech, mirroring llm_client.py's pattern: read
the worker config's `voice` section, get back a client with one method.
Adapted from the autoVideo project's src/tts/narrator.py (local-first Piper
default, optional cloud engines), self-contained for this repo.

Each backend renders text to a WAV file; `synthesize()` returns the path
plus the audio's MEASURED duration in seconds — the anchor value replay.py's
audio-synchronized pacing is built on (the visuals stretch/compress to match
the spoken line, never the other way around).

Two-voice support: the config may define per-speaker overrides
(`voice.speakers.boss`, `voice.speakers.coder`, ...); `synthesize(...,
speaker="boss")` merges that speaker's settings over the base voice config,
so the boss and the coder can use different Piper models or cloud voice IDs.
"""
import os
import shutil
import subprocess
import wave
from dataclasses import dataclass
from pathlib import Path


class TTSError(RuntimeError):
    pass


@dataclass
class Narration:
    """One synthesized spoken line: where the WAV is and how long it plays."""
    audio_path: Path
    duration: float  # seconds, measured from the rendered audio


# ── Duration measurement ──────────────────────────────────────────────────────

def wav_duration(path):
    """Measured duration of a WAV file in seconds.

    Reads the WAV header directly (stdlib `wave`) — no ffprobe dependency in
    the common path; falls back to ffprobe for anything wave can't parse
    (e.g. float PCM some engines emit).
    """
    path = Path(path)
    try:
        with wave.open(str(path), "rb") as wav:
            rate = wav.getframerate()
            return wav.getnframes() / rate if rate else 0.0
    except (wave.Error, EOFError):
        pass
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        raise TTSError(f"cannot measure duration of {path} (bad WAV, no ffprobe)")
    proc = subprocess.run(
        [ffprobe, "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True, timeout=30,
    )
    if proc.returncode != 0:
        raise TTSError(f"ffprobe failed for {path}: {proc.stderr.strip()}")
    return float(proc.stdout.strip())


# ── Backends: (text, out_wav, voice_cfg) -> None, write a WAV ─────────────────

# Loaded PiperVoice instances, cached by resolved model path and kept for
# this worker process's entire lifetime. Piper's own CLI reloads the .onnx
# model from disk and reinitializes an ONNX Runtime session on every single
# invocation; going through the Python API directly and caching the loaded
# voice means that cost is paid once per model, ever, instead of once per
# scene — a 6-scene episode used to load the model 6 times, now it loads it
# once the first time this worker ever narrates, then reuses it for every
# replay for as long as the container runs.
_LOCAL_VOICES = {}


def _load_local_voice(model_path):
    key = str(Path(model_path).resolve())
    voice = _LOCAL_VOICES.get(key)
    if voice is None:
        try:
            from piper.voice import PiperVoice
        except ImportError as exc:
            raise TTSError(
                "piper-tts package not installed (`pip install piper-tts`)"
            ) from exc
        voice = PiperVoice.load(key)
        _LOCAL_VOICES[key] = voice
    return voice


def _piper_local(text, out_wav, voice_cfg):
    from piper.config import SynthesisConfig

    model = voice_cfg.get("model_path")
    if not model or not Path(model).exists():
        raise TTSError(
            f"Piper voice model not found at {model!r} — set voice.model_path to a "
            ".onnx file (see scripts/download_voices.py)"
        )
    voice = _load_local_voice(model)
    rate = float(voice_cfg.get("rate") or 1.0)
    # Piper's length_scale is inverse of speaking rate (bigger = slower).
    syn_config = SynthesisConfig(length_scale=(1.0 / rate) if rate else None)
    with wave.open(str(out_wav), "wb") as wav_file:
        # Placeholder header: synthesize_wav only sets the real
        # nchannels/sampwidth/framerate once its first audio chunk comes
        # back, so if it raises before that (a synthesis failure), closing
        # this still-headerless Wave_write on the way out of the `with`
        # block raises its OWN wave.Error ("# channels not specified") that
        # masks the real exception. Values here are overwritten by
        # synthesize_wav on success; they only matter for a clean close on
        # failure.
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(22050)
        voice.synthesize_wav(text, wav_file, syn_config=syn_config)


def _piper_remote(text, out_wav, voice_cfg, base_url):
    """POST to a Piper HTTP server's own `/synthesize` endpoint — the server
    that ships with piper-tts (`python -m piper.http_server`), no custom
    server code needed. This is how synthesis moves off this container onto
    a separate (potentially more powerful) machine: point voice.base_url (or
    the TTS_BASE_URL env override) at it. `voice` in the request body is the
    model's filename stem, so one remote server whose data dir holds every
    persona's .onnx can serve all of them by name from a single process."""
    import httpx

    model = voice_cfg.get("model_path")
    payload = {"text": text}
    if model:
        payload["voice"] = Path(model).stem
    rate = float(voice_cfg.get("rate") or 1.0)
    if rate:
        payload["length_scale"] = 1.0 / rate
    response = httpx.post(f"{base_url.rstrip('/')}/synthesize", json=payload, timeout=60)
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise TTSError(
            f"remote piper request failed: {exc.response.status_code} {exc.response.text}"
        ) from exc
    Path(out_wav).write_bytes(response.content)


def _piper(text, out_wav, voice_cfg):
    """Piper (local, free). Voices: https://huggingface.co/rhasspy/piper-voices
    Local mode (default) keeps one loaded PiperVoice per model resident in
    this process (see _load_local_voice). Set voice.base_url (or env
    TTS_BASE_URL) to synthesize against a remote piper.http_server instead —
    see _piper_remote."""
    base_url = voice_cfg.get("base_url")
    if base_url:
        _piper_remote(text, out_wav, voice_cfg, base_url)
    else:
        _piper_local(text, out_wav, voice_cfg)


def _openai(text, out_wav, voice_cfg):
    """OpenAI TTS (cloud). Needs OPENAI_API_KEY. voice.voice_id picks the voice."""
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise TTSError("openai package not installed (`pip install openai`)") from exc
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        raise TTSError("OPENAI_API_KEY not set")
    client = OpenAI(api_key=key)
    response = client.audio.speech.create(
        model=voice_cfg.get("tts_model") or "tts-1",
        voice=voice_cfg.get("voice_id") or "alloy",
        input=text,
        response_format="wav",
    )
    response.stream_to_file(str(out_wav))


def _elevenlabs(text, out_wav, voice_cfg):
    """ElevenLabs (cloud). Needs ELEVEN_API_KEY. voice.voice_id is the dashboard ID."""
    try:
        from elevenlabs.client import ElevenLabs
    except ImportError as exc:
        raise TTSError("elevenlabs package not installed") from exc
    key = os.environ.get("ELEVEN_API_KEY")
    if not key:
        raise TTSError("ELEVEN_API_KEY not set")
    client = ElevenLabs(api_key=key)
    audio = client.text_to_speech.convert(
        voice_id=voice_cfg.get("voice_id") or "Rachel",
        text=text,
        output_format="pcm_24000",
    )
    pcm = b"".join(audio)
    # Wrap the raw 24kHz mono s16 PCM in a WAV container ourselves — no
    # ffmpeg round-trip needed for a header.
    with wave.open(str(out_wav), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(24000)
        wav.writeframes(pcm)


FAKE_WORDS_PER_SECOND = 2.5  # matches revoice.py's own pacing estimate
FAKE_MIN_DURATION_S = 0.5


def _fake(text, out_wav, voice_cfg):
    """Testing-only: skip real synthesis entirely and write a silent WAV
    sized to roughly how long the line would take to read. No subprocess,
    no model load, no network — so a replay's narration-prep pass costs
    ~nothing per scene, while `target_duration`-based pacing and the duet
    cue/watchdog timings (docs/duet_replay.md) still see a realistic,
    proportional duration instead of a degenerate near-zero one. Never
    selected by default — opt in with `voice.provider: fake` or the
    `TTS_PROVIDER=fake` env override."""
    duration = max(len(text.split()) / FAKE_WORDS_PER_SECOND, FAKE_MIN_DURATION_S)
    rate = 16000
    with wave.open(str(out_wav), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(b"\x00\x00" * int(duration * rate))


_BACKENDS = {
    "piper": _piper,
    "kokoro": _piper,  # alias: same local .onnx setup until kokoro is wired
    "openai": _openai,
    "elevenlabs": _elevenlabs,
    "fake": _fake,
}


# ── Client ────────────────────────────────────────────────────────────────────

class TTSClient:
    def __init__(self, voice_config):
        self.config = dict(voice_config)
        self.provider = self.config.get("provider")
        if self.provider not in _BACKENDS:
            raise TTSError(
                f"unknown voice.provider: {self.provider!r} (expected one of "
                f"{sorted(_BACKENDS)}, or 'null' for silent)"
            )
        self._backend = _BACKENDS[self.provider]

    def voice_for(self, speaker):
        """Base voice config with the named speaker's overrides merged in."""
        merged = {k: v for k, v in self.config.items() if k != "speakers"}
        overrides = (self.config.get("speakers") or {}).get(speaker) or {}
        merged.update(overrides)
        return merged

    def synthesize(self, text, out_wav, speaker="coder"):
        """Render `text` as `speaker` to a WAV; return Narration with the
        measured duration. Raises TTSError on empty text or backend failure."""
        if not text or not text.strip():
            raise TTSError("narration text is empty")
        out_wav = Path(out_wav)
        out_wav.parent.mkdir(parents=True, exist_ok=True)
        self._backend(text, out_wav, self.voice_for(speaker))
        if not out_wav.exists():
            raise TTSError(f"TTS backend {self.provider!r} produced no output")
        return Narration(audio_path=out_wav, duration=wav_duration(out_wav))


def build_tts_client(config):
    """Build a TTSClient from a full worker config (or just its `voice`
    section). Returns None when voice is disabled (`provider: "null"`,
    missing, or empty) — callers treat None as "perform silently". Env var
    TTS_BASE_URL overrides voice.base_url — pointing Piper synthesis at a
    remote piper.http_server instead of running it in this container (see
    _piper_remote)."""
    voice_config = config.get("voice", config) or {}
    provider = os.environ.get("TTS_PROVIDER") or voice_config.get("provider")
    if not provider or str(provider).lower() in ("null", "none", "off"):
        return None
    base_url = os.environ.get("TTS_BASE_URL") or voice_config.get("base_url")
    return TTSClient({**voice_config, "provider": provider, "base_url": base_url})
