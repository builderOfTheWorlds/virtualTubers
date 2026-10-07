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

## Cold-run record

PENDING: the first cold run from this runbook. Paste the real `/v1/models`
output and the `free -g` before/after here (P0.2 is done only when this section is filled in).
