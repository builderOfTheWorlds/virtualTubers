#!/usr/bin/env python3
"""Minimal tool-using agent loop for the LOCAL vLLM (qwen3.8-27b @ :8092).

Read-only by design: tools are read_file, grep, list_dir, and run_python (a
throwaway subprocess in a temp dir, 120 s cap, cwd = repo, no writes expected).
The model writes its findings via final answer, saved to --out.

    .venv/bin/python .claude/prompts/local_agent.py --task-file task.md --out report.md
"""
import argparse
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile
import urllib.request

REPO = pathlib.Path(__file__).resolve().parents[2]
BASE = os.environ.get("LOCAL_LLM_URL", "http://localhost:8092/v1")
MODEL = os.environ.get("LOCAL_LLM_MODEL", "qwen3.8-27b")
COMPOSE = pathlib.Path.home() / "environments/argyreServer/deployments/vllm-qwen3.8-27b/docker-compose.yml"
MAX_OUT = 6000


def _key():
    if os.environ.get("VLLM_API_KEY"):
        return os.environ["VLLM_API_KEY"]
    # The compose file holds an interpolation, not the key; the container has the real one.
    r = subprocess.run(["docker", "exec", "vllm-qwen3.8-27b", "printenv", "VLLM_API_KEY"],
                       capture_output=True, text=True, timeout=15)
    return r.stdout.strip()


def _safe(path):
    p = (REPO / path).resolve()
    if REPO not in p.parents and p != REPO:
        raise ValueError("path outside repo")
    return p


def t_read_file(path, start=1, end=200):
    lines = _safe(path).read_text(errors="replace").splitlines()
    return "\n".join(f"{i}|{l}" for i, l in enumerate(lines[start - 1:end], start))


def t_grep(pattern, path="app", glob="*.py"):
    r = subprocess.run(["grep", "-rnE", "--include=" + glob, pattern, str(_safe(path))],
                       capture_output=True, text=True, timeout=30)
    return r.stdout.replace(str(REPO) + "/", "")[:MAX_OUT] or "(no matches)"


def t_list_dir(path="."):
    return "\n".join(sorted(x.name + ("/" if x.is_dir() else "") for x in _safe(path).iterdir()))


def t_run_python(code):
    with tempfile.TemporaryDirectory() as d:
        f = pathlib.Path(d) / "snippet.py"
        f.write_text(code)
        py = REPO / ".venv/bin/python"
        r = subprocess.run([str(py if py.exists() else "python3"), str(f)], cwd=str(REPO / "app"),
                           capture_output=True, text=True, timeout=120)
    return (r.stdout + r.stderr)[-MAX_OUT:] or "(no output)"


TOOLS = {"read_file": t_read_file, "grep": t_grep, "list_dir": t_list_dir, "run_python": t_run_python}
SCHEMA = [
    {"type": "function", "function": {"name": "read_file", "description": "Read repo file lines (1-indexed, inclusive).",
     "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "start": {"type": "integer"}, "end": {"type": "integer"}}, "required": ["path"]}}},
    {"type": "function", "function": {"name": "grep", "description": "Extended-regex search under a repo dir.",
     "parameters": {"type": "object", "properties": {"pattern": {"type": "string"}, "path": {"type": "string"}, "glob": {"type": "string"}}, "required": ["pattern"]}}},
    {"type": "function", "function": {"name": "list_dir", "description": "List a repo directory.",
     "parameters": {"type": "object", "properties": {"path": {"type": "string"}}}}},
    {"type": "function", "function": {"name": "run_python", "description": "Run a short Python script (cwd=app/, 120s cap) to test hypotheses, e.g. tracemalloc / RSS measurements.",
     "parameters": {"type": "object", "properties": {"code": {"type": "string"}}, "required": ["code"]}}},
]


def chat(messages):
    body = json.dumps({"model": MODEL, "messages": messages, "tools": SCHEMA, "temperature": 0.3,
                       "max_tokens": 3000,
                       "chat_template_kwargs": {"enable_thinking": False}}).encode()
    req = urllib.request.Request(BASE + "/chat/completions", body,
                                 {"Content-Type": "application/json", "Authorization": "Bearer " + _key()})
    with urllib.request.urlopen(req, timeout=900) as r:
        return json.load(r)["choices"][0]["message"]


def _compact(msgs, keep=5, limit=400):
    """Keep context bounded: shrink all but the newest `keep` tool results.
    The model keeps its own notes in assistant text, which is retained."""
    tools = [i for i, m in enumerate(msgs) if m["role"] == "tool"]
    for i in tools[:-keep]:
        c = msgs[i]["content"]
        if len(c) > limit:
            msgs[i]["content"] = c[:limit] + " ...[trimmed; re-read if needed]"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--task-file", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--max-steps", type=int, default=40)
    ap.add_argument("--resume", action="store_true", help="continue from the saved .state.json")
    a = ap.parse_args()
    msgs = [{"role": "system", "content": "You are a careful senior engineer. Use the tools to gather evidence; "
             "never guess about code you have not read. When done, reply WITHOUT tool calls with a final markdown report "
             "(root cause, evidence with file:line, proposed fix, how to verify). Old tool output is trimmed to save context, so "
             "BEFORE each tool call write 1-3 lines of notes on what you learned so far (file:line facts)."},
            {"role": "user", "content": pathlib.Path(a.task_file).read_text()}]
    log = pathlib.Path(a.out).with_suffix(".log")
    state = pathlib.Path(a.out).with_suffix(".state.json")
    if a.resume and state.exists():
        msgs = json.loads(state.read_text())
    for step in range(a.max_steps):
        state.write_text(json.dumps(msgs))
        _compact(msgs)
        if step == a.max_steps - 4:
            msgs.append({"role": "user", "content": "Step budget nearly spent: stop calling tools and write the final report now."})
        m = chat(msgs)
        msgs.append({k: v for k, v in m.items() if k in ("role", "content", "tool_calls") and v})
        calls = m.get("tool_calls") or []
        if not calls:
            pathlib.Path(a.out).write_text(m.get("content") or "")
            print(f"done in {step + 1} steps -> {a.out}")
            return 0
        for c in calls:
            fn, raw = c["function"]["name"], c["function"]["arguments"]
            try:
                res = TOOLS[fn](**json.loads(raw))
            except Exception as exc:  # noqa: BLE001 — report to the model
                res = f"ERROR {type(exc).__name__}: {exc}"
            with log.open("a") as fh:
                fh.write(f"[{step}] {fn} {raw[:300]}\n  -> {str(res)[:300]!r}\n")
            msgs.append({"role": "tool", "tool_call_id": c["id"], "content": str(res)[:MAX_OUT]})
    pathlib.Path(a.out).write_text("(step limit reached)\n" + (msgs[-1].get("content") or ""))
    return 1


if __name__ == "__main__":
    sys.exit(main())
