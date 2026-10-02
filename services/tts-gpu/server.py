"""
tts-gpu/server.py
Shared GPU Piper synthesis service for every virtualTubers worker
(docs/tts_gpu_service.md).

Why a separate service instead of `use_cuda` inside each worker: the worker
image is Ubuntu 22.04 and replay_pane.py runs on its system python3.10, but
onnxruntime-gpu only publishes aarch64 (GB10) wheels for cp311+. Moving TTS
here also means ONE CUDA context (~1 GB of the box's unified memory) for the
whole stack instead of one per worker.

Wire-compatible with piper's own `python -m piper.http_server` on the one
endpoint workers use (POST /synthesize, JSON {text, voice, length_scale,
noise_scale, noise_w_scale, speaker_id} -> audio/wav bytes), so
app/tts_client.py's _piper_remote needs no special casing. `voice` is the
model's filename stem, looked up under --data-dir (the repo's ./voices,
mounted read-only) — the SAME .onnx files the workers use on CPU, so the
voices sound the same.

Extra endpoint: GET /health -> {"ok": true, "providers": [...], "voices": [...]}
— `providers` tells you whether CUDA is actually active (it falls back to CPU
with a loud log line rather than refusing to serve).

Stdlib http.server only (ThreadingHTTPServer): no Flask dev server in the
hot path. Synthesis is serialized with one lock — a line takes ~0.1 s on the
GPU, and serializing bounds activation memory on a box whose memory is shared
with the LLMs.
"""
import argparse
import io
import json
import logging
import threading
import time
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

LOG = logging.getLogger("tts_gpu")


def _cuda_requested_and_usable(want_cuda):
    """Load the pip-installed CUDA/cuDNN libs (onnxruntime-gpu[cuda,cudnn]
    wheels ship them under site-packages/nvidia/*, which ORT does NOT find
    on its own — without this every Conv fails with 'dlopen failed for
    libcudnn.so') and report whether CUDAExecutionProvider is available."""
    import onnxruntime

    if not want_cuda:
        return False
    if hasattr(onnxruntime, "preload_dlls"):
        try:
            onnxruntime.preload_dlls()
        except Exception as exc:  # pragma: no cover - depends on host libs
            LOG.warning("event=preload_dlls_failed error=%r", exc)
    available = onnxruntime.get_available_providers()
    if "CUDAExecutionProvider" not in available:
        LOG.warning("event=cuda_unavailable providers=%s fallback=cpu", available)
        return False
    return True


class VoiceBank:
    """Lazily loaded PiperVoice per model stem, kept for the process lifetime."""

    def __init__(self, data_dir, use_cuda):
        self.data_dir = Path(data_dir)
        self.use_cuda = use_cuda
        self._voices = {}
        self._load_lock = threading.Lock()
        self.synth_lock = threading.Lock()

    def stems(self):
        return sorted(p.name[: -len(".onnx")] for p in self.data_dir.glob("*.onnx"))

    def get(self, stem):
        voice = self._voices.get(stem)
        if voice is not None:
            return voice
        with self._load_lock:
            voice = self._voices.get(stem)
            if voice is not None:
                return voice
            model = self.data_dir / f"{stem}.onnx"
            if not model.exists():
                raise KeyError(stem)
            from piper.voice import PiperVoice

            started = time.monotonic()
            voice = PiperVoice.load(str(model), use_cuda=self.use_cuda)
            # Warm up: the first CUDA run pays cuDNN algo selection.
            self._synth(voice, "Warm up.", None, None, None, None)
            LOG.info("event=voice_loaded voice=%s providers=%s load_s=%.2f", stem,
                     voice.session.get_providers(), time.monotonic() - started)
            self._voices[stem] = voice
            return voice

    @staticmethod
    def _synth(voice, text, length_scale, noise_scale, noise_w_scale, speaker_id):
        from piper.config import SynthesisConfig

        syn_config = SynthesisConfig(
            speaker_id=speaker_id, length_scale=length_scale,
            noise_scale=noise_scale, noise_w_scale=noise_w_scale,
        )
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wav_file:
            # Placeholder header so a synthesis failure surfaces as itself,
            # not as wave's "# channels not specified" on close (same guard
            # as app/tts_client.py _piper_local).
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(voice.config.sample_rate)
            voice.synthesize_wav(text, wav_file, syn_config=syn_config)
        return buf.getvalue()

    def synthesize(self, stem, text, length_scale=None, noise_scale=None,
                   noise_w_scale=None, speaker_id=None):
        voice = self.get(stem)
        with self.synth_lock:
            return self._synth(voice, text, length_scale, noise_scale,
                               noise_w_scale, speaker_id)


def _optional_float(value):
    return None if value is None else float(value)


def make_handler(bank, default_stem):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, format, *args):  # noqa: A002  # route through logging, quietly
            LOG.debug("http " + format, *args)

        def _send(self, status, body, content_type):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status, payload):
            self._send(status, json.dumps(payload).encode(), "application/json")

        def do_GET(self):
            if self.path.rstrip("/") in ("/health", ""):
                providers = sorted({p for v in bank._voices.values()
                                    for p in v.session.get_providers()})
                self._json(200, {"ok": True, "cuda": bank.use_cuda,
                                 "providers": providers,
                                 "loaded": sorted(bank._voices),
                                 "voices": bank.stems()})
            else:
                self._json(404, {"error": "not found"})

        def do_POST(self):
            if self.path.rstrip("/") != "/synthesize":
                self._json(404, {"error": "not found"})
                return
            try:
                length = int(self.headers.get("Content-Length") or 0)
                data = json.loads(self.rfile.read(length) or b"{}")
                text = str(data.get("text") or "").strip()
                if not text:
                    self._json(400, {"error": "No text provided"})
                    return
                stem = str(data.get("voice") or default_stem)
                started = time.monotonic()
                audio = bank.synthesize(
                    stem, text,
                    length_scale=_optional_float(data.get("length_scale")),
                    noise_scale=_optional_float(data.get("noise_scale")),
                    noise_w_scale=_optional_float(data.get("noise_w_scale")),
                    speaker_id=data.get("speaker_id"),
                )
                LOG.debug("event=synthesized voice=%s chars=%d seconds=%.3f",
                          stem, len(text), time.monotonic() - started)
                self._send(200, audio, "audio/wav")
            except KeyError as exc:
                LOG.warning("event=unknown_voice voice=%s", exc)
                self._json(404, {"error": f"unknown voice {exc}"})
            except Exception as exc:
                LOG.error("event=synthesis_failed error=%r", exc)
                self._json(500, {"error": str(exc)})

    return Handler


def main(argv=None):
    parser = argparse.ArgumentParser(description="Shared GPU Piper TTS service")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--data-dir", default="/data/voices")
    parser.add_argument("--default-voice", default="en_US-lessac-low")
    parser.add_argument("--cpu", action="store_true", help="force CPU (no CUDA)")
    parser.add_argument("--preload", default="",
                        help="comma-separated voice stems to load at startup ('all' = every .onnx)")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")

    use_cuda = _cuda_requested_and_usable(not args.cpu)
    bank = VoiceBank(args.data_dir, use_cuda)
    LOG.info("event=startup provider=%s data_dir=%s voices=%d",
             "CUDAExecutionProvider" if use_cuda else "CPUExecutionProvider",
             args.data_dir, len(bank.stems()))
    preload = bank.stems() if args.preload == "all" else [
        s.strip() for s in args.preload.split(",") if s.strip()]
    for stem in preload:
        bank.get(stem)
    server = ThreadingHTTPServer((args.host, args.port), make_handler(bank, args.default_voice))
    LOG.info("event=listening host=%s port=%d", args.host, args.port)
    server.serve_forever()


if __name__ == "__main__":
    main()
