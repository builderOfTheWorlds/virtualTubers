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
import time
import urllib.error
import urllib.request

log = logging.getLogger("qwen_worker.ollama")

DEFAULT_BASE_URL = "http://localhost:11434"
DEFAULT_MODEL = "qwen3-coder:30b"

# A 30B model writing a whole module needs room; qwen3-coder:30b advertises a
# 262144-token context. num_ctx is what actually gets allocated per request, so
# it is set explicitly rather than left to Ollama's (much smaller) default.
DEFAULT_NUM_CTX = 32768
DEFAULT_NUM_PREDICT = 8192

# Local generation of a full module is slow — minutes, not seconds.
DEFAULT_TIMEOUT_S = 1800


class OllamaError(RuntimeError):
    """Raised when the model cannot be reached or returns an unusable reply."""


def chat(system_prompt, user_prompt, model=DEFAULT_MODEL,
         base_url=DEFAULT_BASE_URL, temperature=0.1,
         num_ctx=DEFAULT_NUM_CTX, num_predict=DEFAULT_NUM_PREDICT,
         timeout=DEFAULT_TIMEOUT_S):
    """Send one non-streaming chat completion and return the reply text.

    temperature defaults low (0.1): this is code generation against an
    executable spec, not creative writing — determinism is worth more than
    variety, and it materially cuts the retry rate on a local model.

    Raises OllamaError on transport failure, a non-200, malformed JSON, or an
    empty completion, so the caller's retry loop sees one exception type.
    """
    log.debug("chat request model=%s num_ctx=%d num_predict=%d chars_in=%d",
              model, num_ctx, num_predict, len(system_prompt) + len(user_prompt))

    payload = {
        "model": model,
        "stream": False,
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

    request = urllib.request.Request(
        f"{base_url.rstrip('/')}/api/chat",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
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
