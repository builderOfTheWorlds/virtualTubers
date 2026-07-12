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
import sys
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

def _piper(text, out_wav, voice_cfg):
    """Piper (local, free). Needs the `piper` CLI + a .onnx voice model
    (voice.model_path). Voices: https://huggingface.co/rhasspy/piper-voices"""
    exe_name = "piper.exe" if os.name == "nt" else "piper"
    venv_piper = Path(sys.executable).parent / exe_name
    piper_exe = str(venv_piper) if venv_piper.exists() else shutil.which("piper")
    if piper_exe is None:
        raise TTSError(
            "piper CLI not found — `pip install piper-tts` and set voice.model_path"
        )
    model = voice_cfg.get("model_path")
    if not model or not Path(model).exists():
        raise TTSError(
            f"Piper voice model not found at {model!r} — set voice.model_path to a "
            ".onnx file (see scripts/download_voices.py)"
        )
    # stdout -> DEVNULL: piper writes audio to --output_file but prints
    # progress to stdout; capturing it can deadlock on a full pipe buffer.
    proc = subprocess.run(
        [piper_exe, "--model", str(model), "--output_file", str(out_wav)],
        input=text, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        text=True, timeout=300,
    )
    if proc.returncode != 0:
        raise TTSError(f"piper failed (exit {proc.returncode}): {proc.stderr.strip()}")


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


_BACKENDS = {
    "piper": _piper,
    "kokoro": _piper,  # alias: same local .onnx setup until kokoro is wired
    "openai": _openai,
    "elevenlabs": _elevenlabs,
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
    missing, or empty) — callers treat None as "perform silently"."""
    voice_config = config.get("voice", config) or {}
    provider = os.environ.get("TTS_PROVIDER") or voice_config.get("provider")
    if not provider or str(provider).lower() in ("null", "none", "off"):
        return None
    return TTSClient({**voice_config, "provider": provider})
