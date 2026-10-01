"""Probe vLLM streaming SSE shape for a plan-style prompt.

Reproduces what concurrent_llm.OpenAICompatClient.complete_streaming sees:
a thinking-enabled Qwen3.8-27B completion under a max_tokens cap. Prints
every SSE event's delta keys and a sample, the final finish_reason, and the
usage split (reasoning vs content) so we can see whether the budget is
exhausted by chain-of-thought before any content token arrives.
"""
import json
import os
import time
import urllib.request

# Bearer key from the environment (VLLM_HERMES_API_KEY), never inline.
KEY = os.environ.get("VLLM_HERMES_API_KEY", "")
URL = "http://127.0.0.1:8092/v1/chat/completions"

SYSTEM = "You plan narrative segments for an office-simulation campaign. " \
         "Reply with ONLY a YAML document, no prose, no code fences."
USER = ("Plan the arc for one 6-hour segment of an office workday. "
        "Keys: id (slug), title, beats (list of 6 strings, each <= 12 words).")


def probe(label, max_tokens, extra):
    body = {
        "model": "qwen3.8-27b",
        "messages": [{"role": "system", "content": SYSTEM},
                     {"role": "user", "content": USER}],
        "temperature": 0.7,
        "max_tokens": max_tokens,
        "stream": True,
    }
    body.update(extra)
    req = urllib.request.Request(URL, json.dumps(body).encode(),
                                 {"Content-Type": "application/json",
                                  "Authorization": f"Bearer {KEY}"})
    t0 = time.time()
    events = []
    with urllib.request.urlopen(req, timeout=900) as r:
        for raw in r:
            line = raw.decode("utf-8").strip()
            if line.startswith("data:"):
                payload = line[5:].strip()
                if payload == "[DONE]":
                    break
                events.append(json.loads(payload))
    dt = time.time() - t0

    print(f"== {label} ==")
    print(f"elapsed={dt:.1f}s events={len(events)}")
    if not events:
        print("(no SSE events at all)")
        return
    first = events[0]["choices"][0]["delta"]
    last = events[-1]["choices"][0]
    print("first delta keys:", sorted(first.keys()))
    print("last event keys:", sorted(last.keys()))
    print("finish_reason:", last.get("finish_reason"))
    usage = events[-1].get("usage")
    print("usage:", json.dumps(usage) if usage else None)
    n_reasoning = sum(1 for e in events
                      if e["choices"][0]["delta"].get("reasoning"))
    n_content = sum(1 for e in events
                    if e["choices"][0]["delta"].get("content"))
    print(f"deltas with reasoning: {n_reasoning} | with content: {n_content}")
    content = "".join(e["choices"][0]["delta"].get("content") or ""
                      for e in events)
    print(f"content_chars={len(content)} first_80={content[:80]!r}")
    if n_reasoning:
        first_reasoning = next(e["choices"][0]["delta"]["reasoning"]
                               for e in events
                               if e["choices"][0]["delta"].get("reasoning"))
        print(f"reasoning sample: {first_reasoning[:100]!r}")
    print()


# 1) thinking ON (default) under the 1024 budget the arc profile uses
probe("thinking ON, max_tokens=1024", 1024, {})

# 2) thinking ON with a 4096 budget — does content ever arrive, and what
#    is the reasoning/content split? (The proposed fix.)
probe("thinking ON, max_tokens=4096", 4096, {})

# 3) thinking OFF — the W0 benchmark condition (apples-to-apples with Ollama)
probe("thinking OFF, max_tokens=1024", 1024,
      {"chat_template_kwargs": {"enable_thinking": False}})
