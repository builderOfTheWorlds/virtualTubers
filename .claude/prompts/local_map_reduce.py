#!/usr/bin/env python3
"""Map-reduce review on the LOCAL vLLM: one file per call (no tool loop), then a synthesis call.

    python3 .claude/prompts/local_map_reduce.py --out .claude/prompts/tile_memory_report.md
"""
import argparse
import json
import pathlib
import subprocess
import urllib.request

REPO = pathlib.Path(__file__).resolve().parents[2]
BASE = "http://localhost:8092/v1"
FILES = ["app/tile_pane.py", "app/tile_avatar.py", "app/avatar_providers/codec_avatar.py",
         "app/gpu_render_worker.py", "app/replay.py", "app/narration_store.py", "app/audio_player.py",
         "app/gaze.py", "app/relay_io.py", "app/agent_state.py"]
CONTEXT = ("Context: a long-running Python 3.10 process (tile_pane.py, one per seat, 8 per container) grows to ~4-5 GB "
           "private anonymous heap over 24 h (~1.8 GB/day), mostly one glibc brk heap. It renders a 3D head at 12 fps "
           "through a GPU subprocess + shared memory, reads narration WAV bytes from Postgres, keeps a bounded deque of dialogue.")
MAP_Q = ("Review ONLY the file below for memory growth. List up to 8 findings, each as: `line N: <what accumulates or "
         "allocates> — <unbounded leak | per-tick temporary (fragmentation) | bounded/harmless>`. Cover: collections that "
         "only grow, caches without eviction, retained bytes/arrays/surfaces, per-frame full-frame temporaries, "
         "threads/processes/files/sockets/DB connections created repeatedly and not closed, state re-read into growing "
         "structures. If the file has none, say 'none'. Max 200 words. Cite only lines you can see.")


def key():
    return subprocess.run(["docker", "exec", "vllm-qwen3.8-27b", "printenv", "VLLM_API_KEY"],
                          capture_output=True, text=True, timeout=15).stdout.strip()


def ask(prompt, max_tokens=1200):
    body = json.dumps({"model": "qwen3.8-27b", "temperature": 0.2, "max_tokens": max_tokens,
                       "messages": [{"role": "user", "content": prompt}],
                       "chat_template_kwargs": {"enable_thinking": False}}).encode()
    req = urllib.request.Request(BASE + "/chat/completions", body,
                                 {"Content-Type": "application/json", "Authorization": "Bearer " + key()})
    with urllib.request.urlopen(req, timeout=600) as r:
        return json.load(r)["choices"][0]["message"]["content"]


def numbered(path):
    lines = (REPO / path).read_text(errors="replace").splitlines()
    return "\n".join(f"{i}|{l}" for i, l in enumerate(lines, 1))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    out = pathlib.Path(a.out)
    findings = {}
    for f in FILES:
        try:
            findings[f] = ask(f"{CONTEXT}\n\n{MAP_Q}\n\nFILE {f}:\n{numbered(f)}")
        except Exception as exc:  # noqa: BLE001
            findings[f] = f"(failed: {type(exc).__name__}: {exc})"
        print("mapped", f, flush=True)
        out.with_suffix(".map.json").write_text(json.dumps(findings, indent=1))
    joined = "\n\n".join(f"## {f}\n{t}" for f, t in findings.items())
    report = ask(f"{CONTEXT}\n\nPer-file findings from a first pass:\n\n{joined}\n\nSynthesize a final markdown report: "
                 "(1) ranked likely causes of the growth with file:line, labelled leak vs fragmentation; (2) the minimal "
                 "fix for each; (3) one experiment to confirm in production. Drop findings that are marked harmless. "
                 "Be honest about what is speculation.", max_tokens=2500)
    out.write_text(report)
    print("report ->", out)


if __name__ == "__main__":
    main()
