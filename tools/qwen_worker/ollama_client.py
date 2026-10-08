#!/usr/bin/env python3
"""
ollama_client.py
Thin client over the local Ollama `/api/chat` endpoint, used to drive a local
coding model (default `qwen3-coder:30b`) as an implementation worker.

Deliberately dependency-free (urllib only, same convention as
projectManager/src/loki_push.py) so the harness runs from the project .venv
without adding a package for one HTTP POST.
"""
import json
import logging
import os
import time
import urllib.error
import urllib.request

log = logging.getLogger("qwen_worker.ollama")

# Defaults are overridable from the environment so a `runner.py preflight` /
# `run` on the Windows dev PC can reach the model server on the gx10 box
# without a CLI flag. The env is read at import time; the runner and the
# sandboxed pytest are separate processes, so each picks up the live values.
DEFAULT_BASE_URL = os.environ.get("QWEN_WORKER_BASE_URL", "http://localhost:11434")
DEFAULT_MODEL = os.environ.get("QWEN_WORKER_MODEL", "qwen3-coder:30b")
# Backend: "ollama" (/api/chat, default) or "openai" (/v1/chat/completions, e.g. the
# local vLLM server). For openai the Bearer key comes from $VLLM_API_KEY, or from a
# `VLLM_API_KEY=...` line in the file named by $QWEN_WORKER_API_KEY_FILE (so the key
# never passes through a shell). The key is never logged.
API = os.environ.get("QWEN_WORKER_API", "ollama")
API_KEY_FILE = os.environ.get("QWEN_WORKER_API_KEY_FILE")

# A 30B model writing a whole module needs room; qwen3-coder:30b advertises a
# 262144-token context. num_ctx is what actually gets allocated per request, so
# it is set explicitly rather than left to Ollama's (much smaller) default.
DEFAULT_NUM_CTX = 40960
DEFAULT_NUM_PREDICT = 12288

# Local generation of a full module is slow — minutes, not seconds.
DEFAULT_TIMEOUT_S = 1800


def _api_key():
    key = os.environ.get("VLLM_API_KEY")
    if not key and API_KEY_FILE:
        try:
            with open(API_KEY_FILE, encoding="utf-8") as handle:
                for line in handle:
                    if line.startswith("VLLM_API_KEY="):
                        key = line.split("=", 1)[1].strip()
                        break
        except OSError as exc:
            log.error("cannot read QWEN_WORKER_API_KEY_FILE: %s", type(exc).__name__)
    return key or None


def _openai_headers():
    headers = {"Content-Type": "application/json"}
    key = _api_key()
    if key:
        headers["Authorization"] = f"Bearer {key}"
    return headers


def _chat_openai(system_prompt, user_prompt, model, base_url, temperature, num_predict,
                 timeout, think):
    payload = {
        "model": model, "stream": False, "temperature": temperature, "max_tokens": num_predict,
        "messages": [{"role": "system", "content": system_prompt},
                     {"role": "user", "content": user_prompt}],
        "chat_template_kwargs": {"enable_thinking": bool(think)},
    }
    request = urllib.request.Request(f"{base_url.rstrip('/')}/v1/chat/completions",
                                     data=json.dumps(payload).encode("utf-8"),
                                     headers=_openai_headers(), method="POST")
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        log.error("openai-compatible HTTP %s: %s", exc.code, detail)
        raise OllamaError(f"openai-compatible server returned HTTP {exc.code}: {detail}") from exc
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        log.error("openai-compatible server unreachable at %s: %s", base_url, exc)
        raise OllamaError(f"openai-compatible server unreachable at {base_url}: {exc}") from exc
    try:
        data = json.loads(body)
        choice = data["choices"][0]
    except (json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
        raise OllamaError(f"openai-compatible server returned a malformed body: {exc}") from exc
    content = (choice.get("message") or {}).get("content") or ""
    if not content.strip():
        raise OllamaError(f"empty completion (finish_reason={choice.get('finish_reason')!r})")
    log.info("chat complete in %.1fs chars_out=%d completion_tokens=%s",
             time.monotonic() - started, len(content), (data.get("usage") or {}).get("completion_tokens"))
    return content


class OllamaError(RuntimeError):
    """Raised when the model cannot be reached or returns an unusable reply."""


def chat(system_prompt, user_prompt, model=DEFAULT_MODEL,
         base_url=DEFAULT_BASE_URL, temperature=0.1,
         num_ctx=DEFAULT_NUM_CTX, num_predict=DEFAULT_NUM_PREDICT,
         timeout=DEFAULT_TIMEOUT_S, think=False):
    """Send one non-streaming chat completion and return the reply text.

    temperature defaults low (0.1): this is code generation against an
    executable spec, not creative writing — determinism is worth more than
    variety, and it materially cuts the retry rate on a local model.

    think defaults to False. Reasoning models (qwen3.8:27b and the rest of the
    qwen3 family) return their chain of thought in a separate `thinking` field
    that does NOT count as output here, but DOES consume num_predict. On a long
    spec the model spends the entire budget reasoning and returns an empty
    `content`: three consecutive 400-second attempts on the 3layers_pool task
    produced nothing at all that way. The spec IS the reasoning for this
    harness — it states the design decisions and their rationale explicitly —
    so thinking buys little and costs the whole completion.

    Raises OllamaError on transport failure, a non-200, malformed JSON, or an
    empty completion, so the caller's retry loop sees one exception type.
    """
    log.debug("chat request api=%s model=%s num_ctx=%d num_predict=%d chars_in=%d",
              API, model, num_ctx, num_predict, len(system_prompt) + len(user_prompt))
    if API == "openai":
        return _chat_openai(system_prompt, user_prompt, model, base_url, temperature,
                            num_predict, timeout, think)

    payload = {
        "model": model,
        "stream": False,
        # Top-level, not inside options — Ollama reads it there.
        "think": think,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "options": {
            "temperature": temperature,
            "num_ctx": num_ctx,
            "num_predict": num_predict,
        },
    }

    def _post(body_payload):
        request = urllib.request.Request(
            f"{base_url.rstrip('/')}/api/chat",
            data=json.dumps(body_payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read().decode("utf-8")

    started = time.monotonic()
    try:
        try:
            body = _post(payload)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            # Non-reasoning models (qwen3-coder:30b and the campaign specs'
            # other targets) reject the `think` key outright. Drop it and retry
            # once rather than making this client model-specific.
            if "think" in detail.lower():
                log.debug("model %s rejects the think key; retrying without it", model)
                payload.pop("think", None)
                body = _post(payload)
            else:
                raise
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        log.error("ollama HTTP %s: %s", exc.code, detail)
        raise OllamaError(f"ollama returned HTTP {exc.code}: {detail}") from exc
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        log.error("ollama unreachable at %s: %s", base_url, exc)
        raise OllamaError(f"ollama unreachable at {base_url}: {exc}") from exc

    elapsed = time.monotonic() - started

    try:
        data = json.loads(body)
    except json.JSONDecodeError as exc:
        log.error("ollama returned non-JSON body (%d bytes)", len(body))
        raise OllamaError(f"ollama returned non-JSON body: {exc}") from exc

    content = data.get("message", {}).get("content", "")
    if not content.strip():
        log.error("ollama returned an empty completion after %.1fs", elapsed)
        raise OllamaError("ollama returned an empty completion")

    log.info("chat complete in %.1fs chars_out=%d eval_count=%s",
             elapsed, len(content), data.get("eval_count"))
    return content


def is_available(base_url=DEFAULT_BASE_URL, model=DEFAULT_MODEL, timeout=5):
    """Best-effort preflight: is Ollama up and is `model` actually pulled?

    Returns (ok, detail). Never raises — the caller turns this into a clean
    error message instead of a stack trace mid-run.
    """
    if API == "openai":
        try:
            request = urllib.request.Request(f"{base_url.rstrip('/')}/v1/models",
                                             headers=_openai_headers())
            with urllib.request.urlopen(request, timeout=timeout) as response:
                ids = [m.get("id") for m in json.loads(response.read().decode("utf-8")).get("data", [])]
        except Exception as exc:  # noqa: BLE001 - preflight never propagates
            return False, f"openai-compatible server unreachable at {base_url}: {type(exc).__name__}"
        if model not in ids:
            return False, f"model {model!r} not served at {base_url} (serving: {ids})"
        return True, f"openai-compatible server at {base_url} serves {model}"
    try:
        with urllib.request.urlopen(f"{base_url.rstrip('/')}/api/tags",
                                    timeout=timeout) as response:
            tags = json.loads(response.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001 - preflight never propagates
        log.warning("ollama preflight failed: %s", exc)
        return False, f"ollama unreachable at {base_url}: {exc}"

    names = [entry.get("name") for entry in tags.get("models", [])]
    if model not in names:
        log.warning("model %s not pulled; available=%s", model, names)
        return False, f"model {model!r} not found. Available: {', '.join(names) or 'none'}"

    return True, f"{model} ready at {base_url}"
