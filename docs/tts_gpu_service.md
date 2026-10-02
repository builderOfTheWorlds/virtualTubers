# tts-gpu service (`services/tts-gpu/server.py`)

## Overview

One shared GPU Piper synthesis process for every virtualTubers worker on
argyre (NVIDIA GB10 / DGX Spark, aarch64). Workers point
`TTS_BASE_URL=http://tts-gpu:5000` at it and keep using their existing
`voice.model_path` / `voice.speakers` config — the service serves the SAME
`.onnx` files from the repo's `./voices` (mounted read-only), so no voice
changes.

Why a service rather than CUDA inside each worker: onnxruntime-gpu's aarch64
wheels are CPython 3.11+ only, and the worker's replay pane runs on Ubuntu
22.04's python3.10 — no installable wheel. A single service also holds one
CUDA context (~1.6 GiB of unified memory with all 9 voices loaded) instead
of one per worker, which matters on a box whose memory is shared with the
LLMs (see the 2026-09 OOM history).

Measured 2026-10-02 (~44-word lines): **0.13 s/line** over HTTP from a
worker container (0.10-0.15 s raw in-process), vs 0.43-0.55 s on a worker's
2 capped CPUs with the thread fix, vs 3.8-5.0 s on the old default.

## Signature

```
python /app/server.py [--host 0.0.0.0] [--port 5000] [--data-dir /data/voices]
                      [--default-voice en_US-lessac-low] [--preload all|a,b]
                      [--cpu] [--debug]
```

HTTP API (wire-compatible with `python -m piper.http_server`'s `/synthesize`,
which `app/tts_client.py::_piper_remote` already speaks):

- `POST /synthesize` — JSON `{text, voice?, length_scale?, noise_scale?,
  noise_w_scale?, speaker_id?}` → `audio/wav` bytes. `voice` = model filename
  stem (e.g. `en_US-joe-medium`).
- `GET /health` — `{"ok": true, "cuda": bool, "providers": [...],
  "loaded": [...], "voices": [...]}`.

## Parameters

- `--data-dir` — directory of `<stem>.onnx` + `<stem>.onnx.json` pairs.
- `--preload` — voices to load (and warm up) at startup; `all` = every
  `.onnx` in the data dir. Unlisted voices load lazily on first request.
- `--cpu` — force CPUExecutionProvider (debugging).
- `--default-voice` — used when a request omits `voice`.

## Return Value

`/synthesize`: 200 + WAV; 400 empty text; 404 unknown voice; 500 synthesis
error (JSON `{"error": ...}`).

## Dependencies

`python:3.12-slim-bookworm`, `piper-tts==1.8.0`,
`onnxruntime-gpu[cuda,cudnn]==1.30.0` (pulls CUDA 13 runtime, cuBLAS, cuFFT,
cuRAND, cuDNN 9 as pip wheels — no CUDA base image). The driver comes from
the NVIDIA container runtime (`gpus`/`deploy.resources.reservations.devices`).
The Dockerfile uninstalls the CPU `onnxruntime` that piper-tts pulls in, so
it cannot shadow the GPU build, and asserts CUDAExecutionProvider at build
time. Stdlib `http.server` (ThreadingHTTPServer); synthesis is serialized by
one lock (bounded GPU activation memory; a line is ~0.1 s).

## Usage Examples

Start it (once the current airing is over) and point workers at it:

```bash
docker compose --profile tts-gpu build tts-gpu
docker compose --profile tts-gpu up -d tts-gpu
curl -s http://localhost:5000/health   # only if you publish the port; else:
docker compose exec tts-gpu python -c "import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:5000/health').read())"
# .env:  TTS_BASE_URL=http://tts-gpu:5000
docker compose up -d --no-deps worker-roundtable   # recreate to pick up env
```

Standalone benchmark (what was used for the numbers above):

```bash
docker run -d --rm --name tts-gpu-bench --gpus all -p 127.0.0.1:5599:5000 \
  -v $PWD/voices:/data/voices:ro vtube-tts-gpu:latest --preload all
curl -s -X POST http://127.0.0.1:5599/synthesize \
  -d '{"text":"Hello stream.","voice":"en_US-lessac-medium"}' -o /tmp/x.wav
```

## Error Handling

- CUDA unavailable at startup → logs `event=cuda_unavailable ...
  fallback=cpu` and serves on CPU (never refuses to start).
- `preload_dlls()` failure → logged, then the provider check decides.
- Worker side: if the service is down or errors, `tts_client` falls back to
  local CPU synthesis for that line and skips the remote for 60 s
  (`voice.remote_fallback`, default on), logging `event=remote_failed`.

## Changelog

- **v1.0.0** (2026-10-02): Initial version. Verified on GB10 (sm_121, driver
  580.173, CUDA 13.0): CUDAExecutionProvider active, 0.13 s/line over HTTP,
  ~1.6 GiB RSS with 9 voices.
