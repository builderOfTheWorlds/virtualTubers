#!/usr/bin/env python3
"""Smoke test vllm-agents with the worker VLLMClient (plan P0.2/P0.3).

Reads VLLM_API_KEY from the deployment .env itself (never prints it), lists
/v1/models, then makes one streamed call and shows the reasoning/content split.
Usage: .venv/bin/python scripts/vllm_agents_smoke.py [--env PATH] [--base-url URL]
"""
import argparse
import json
import os
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "app"))

import httpx  # noqa: E402

from llm_client import build_llm_client  # noqa: E402

DEFAULT_ENV = pathlib.Path.home() / "environments/argyreServer/deployments/vllm-agents/.env"


def load_key(env_path):
    for line in pathlib.Path(env_path).read_text().splitlines():
        if line.startswith("VLLM_API_KEY="):
            return line.split("=", 1)[1].strip() or None
    return None


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--env", default=str(DEFAULT_ENV))
    p.add_argument("--base-url", default="http://localhost:8092")
    p.add_argument("--model", default="table-agents")
    args = p.parse_args()

    key = os.environ.get("VLLM_API_KEY") or load_key(args.env)
    os.environ["VLLM_API_KEY"] = key or ""
    print("key loaded:", bool(key))

    r = httpx.get(f"{args.base_url}/v1/models", timeout=10)
    print("GET /v1/models without key ->", r.status_code)
    r = httpx.get(f"{args.base_url}/v1/models", headers={"Authorization": f"Bearer {key}"}, timeout=10)
    print("GET /v1/models with key    ->", r.status_code)
    for m in r.json().get("data", []):
        print(f"  id={m['id']} root={m.get('root')} max_model_len={m.get('max_model_len')}")

    client = build_llm_client({"llm": {"provider": "vllm", "base_url": args.base_url,
                                       "model": args.model, "max_tokens": 1024,
                                       "temperature": 0.6}})
    print("client:", repr(client))
    first = {}
    t0 = time.time()

    def on_r(chunk):
        first.setdefault("reasoning", time.time() - t0)

    def on_c(chunk):
        first.setdefault("content", time.time() - t0)

    reasoning, content = client.complete_stream(
        "You are Chadwick, a nervous halfling bard in a D&D party.",
        [{"role": "user", "content": "GM: The vault door groans open and cold air spills out. "
                                     "Reply with one spoken line, max 25 words."}],
        on_reasoning=on_r, on_content=on_c)
    wall = time.time() - t0
    print(json.dumps({"wall_s": round(wall, 1),
                      "first_reasoning_s": round(first.get("reasoning", -1), 1),
                      "first_content_s": round(first.get("content", -1), 1),
                      "reasoning_chars": len(reasoning), "content_chars": len(content)}))
    print("REASONING (head):", reasoning[:300].replace("\n", " "))
    print("CONTENT:", content)
    return 0


if __name__ == "__main__":
    sys.exit(main())
