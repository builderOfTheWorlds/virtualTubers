# vllm-agents runbook (build plan P0.1 / P0.2)

One vLLM server, one model, shared by every table agent (GM + seats).
Deployment: `~/environments/argyreServer/deployments/vllm-agents/`
(compose + `.env.example`; the real `.env` is chmod 600 and holds `VLLM_API_KEY`).
Worker wiring: `docker-compose.table.yml` (P0.5).

## OOM rule (read first)

The GB10 has ~121 GiB unified memory shared by GPU and CPU. On 2026-09-29,
vllm-hermes3-70b next to an Ollama-loaded qwen3.8:27b crashed the box.

- Run ONE LLM server at a time.
- Hermes background tasks on `provider: auto` can load Ollama models on their
  own. Only the operator can reroute them, so confirm none are scheduled before a long run.
- `restart: "no"` is deliberate: after a reboot the server must not come back
  next to something else.

## Cold start

```bash
# 1. Nothing else may hold the GPU / unified memory
docker ps --format '{{.Names}}' | grep -E 'vllm|ktransformers'   # must be empty
curl -s localhost:11434/api/ps                                   # {"models":[]}
sudo systemctl stop ollama                                       # belt and braces
free -g                                                          # note "available"

# 2. Up
cd ~/environments/argyreServer/deployments/vllm-agents
docker compose up -d
docker logs -f vllm-agents        # wait for "Application startup complete"

# 3. Health (the key is read from .env by name, never echoed)
set -a; . ./.env; set +a
curl -s -H "Authorization: Bearer $VLLM_API_KEY" localhost:8092/v1/models | python3 -m json.tool
free -g                            # plan target: >= 15 GB available with the show stack up
```

One-line health check:

```bash
(cd ~/environments/argyreServer/deployments/vllm-agents && set -a && . ./.env && set +a && curl -sf -H "Authorization: Bearer $VLLM_API_KEY" localhost:8092/v1/models >/dev/null && echo vllm-agents OK || echo vllm-agents DOWN)
```

## Switching benchmark candidates (P1.2)

Edit `VLLM_MODEL` and, if needed, `VLLM_REASONING_PARSER` in `.env`. Keep
`VLLM_SERVED_NAME=table-agents` so the workers' `LLM_MODEL` never changes. Then
`docker compose up -d --force-recreate`. The HF cache is shared with
`vllm-qwen3.8-27b/hf-cache`, so pull new weights into that cache.

## Stop

```bash
cd ~/environments/argyreServer/deployments/vllm-agents && docker compose down
sudo systemctl start ollama        # only if Ollama work is next
```

## Worker side

```bash
# .env of virtualTubers must define VLLM_API_KEY (same value as the server's).
docker compose -f docker-compose.yml -f docker-compose.table.yml up -d
```

Caveat: with reasoning ON, `max_tokens` covers reasoning AND content. A worker
config with a small `llm.max_tokens` can spend it all on thinking. `VLLMClient`
then raises `LLMError(... finish_reason='length' ...)` instead of returning an
empty line. Table handlers (P3) set per-call THINK/SPEAK budgets.

## Cold-run record (2026-10-07, Qwen3.8-27B BF16, gpu-mem-util 0.55)

Ollama couldn't be stopped without sudo, but it had no models loaded;
`scripts/vllm_mem_watch.py` ran alongside (floor 12 GiB, auto-unloads Ollama models).

| t | event | MemAvailable |
|---|---|---|
| 09:35:38 | `docker compose up -d` | 91.2 GiB |
| 09:36:17 | engine reserves its pool | 37.1 GiB |
| 09:41:07 | weights loaded: 50.22 GiB in 295 s | 33.6 GiB (min) |
| 09:42:37 | KV cache 14.05 GiB = 200,021 tokens; max concurrency at 32k = 6.10x | |
| 09:42:58 | `Application startup complete` (7 m 20 s from cold) | 27.7 GiB |
| idle, later | | 24–25 GiB |

`scripts/vllm_agents_smoke.py` (reads the key from `.env` itself):

```
GET /v1/models without key -> 401
GET /v1/models with key    -> 200
  id=table-agents root=Qwen/Qwen3.8-27B max_model_len=32768
client: VLLMClient(base_url='http://localhost:8092', model='table-agents', api_key=set)
{"wall_s": 67.3, "first_reasoning_s": 0.6, "first_content_s": 58.4, "reasoning_chars": 590, "content_chars": 92}
CONTENT: Oh—oh, that's the sound of something ancient deciding it's not done with us yet, isn't it?
```

Notes:
- ~3 tok/s per stream for BF16 27B. This is the bandwidth ceiling (~54 GB of weights read per token).
- The log warns `deep_gemm` ImportError (undefined c10 symbol). Harmless for BF16; check it for FP8 candidates.
- vLLM reports up to 35.47 GiB of KV is possible. Raise `VLLM_GPU_MEM_UTIL` only after measuring with the show stack (TTS + ffmpeg) up.
- Content can start with `\n\n` (after `</think>`); callers strip.
