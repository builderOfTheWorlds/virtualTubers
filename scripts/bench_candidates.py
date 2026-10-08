#!/usr/bin/env python3
"""scripts/bench_candidates.py: benchmark candidate models for the live agent table, unattended.

Per candidate: download (docker + hf, into the shared vllm-agents HF cache) ->
swap vllm-agents to it (.env edit, recreate; fall back through linear backends
if the engine fails to start) -> smoke (decode tok/s, reasoning/content split)
-> table_round (two_pass, N=4 and N=7, budgeted, 2 repeats) -> in-process slice
(scripts/table_slice_local.py: 2 scenes, forced retake) -> record -> optionally
delete the weights. The next download is prefetched while a candidate runs.

Safety: one LLM server at a time (vllm-agents only); scripts/vllm_mem_watch.py must
be running (12 GiB floor). Never prints keys. Disk: a download waits until free
space >= size + DISK_FLOOR_GB.

    .venv/bin/python scripts/bench_candidates.py [--only name,...] [--skip-download]
Results: utilities/benchmarker/output/candidates/<name>/{record.json, *.log}
         utilities/benchmarker/output/candidates/REPORT.md (rebuilt after each candidate)
"""
import argparse
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.request

REPO = pathlib.Path(__file__).resolve().parents[1]
DEPLOY = pathlib.Path.home() / "environments/argyreServer/deployments/vllm-agents"
HF_CACHE = pathlib.Path.home() / "environments/argyreServer/deployments/vllm-qwen3.8-27b/hf-cache"
OUT = REPO / "utilities/benchmarker/output/candidates"
IMAGE = "vllm-qwen3.8-27b:local"
PY = str(REPO / ".venv/bin/python")
DISK_FLOOR_GB = 15
BASE = "http://localhost:8092"

# name, repo, size GB, parser, linear backends to try, extra env, delete weights after
CANDIDATES = [
    dict(name="qwen3.6-35b-a3b-fp8", repo="Qwen/Qwen3.6-35B-A3B-FP8", gb=37.5, parser="qwen3",
         backends=["triton", "auto"], delete=True),
    dict(name="gpt-oss-20b", repo="openai/gpt-oss-20b", gb=13.8, parser="openai_gptoss",
         backends=["auto", "triton", "marlin"], exclude=["original/*", "metal/*"], delete=True),
    # deepseek_r1 filed the whole answer as reasoning (no </think> -> empty content); qwen3
    # treats a missing tag as content (2026-10-08 first run).
    dict(name="hermes-4-14b-fp8", repo="NousResearch/Hermes-4-14B-FP8", gb=16.3, parser="qwen3",
         backends=["auto", "triton", "torch"], delete=True),
    dict(name="gemma-4-26b-a4b-fp8", repo="RedHatAI/gemma-4-26B-A4B-it-FP8-dynamic", gb=28.6,
         parser="gemma4", backends=["triton", "auto", "torch"], delete=True),
    dict(name="nemotron-3.5-lightning-30b-a3b-nvfp4", repo="nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-NVFP4",
         gb=21.6, parser="nemotron_v3", backends=["auto", "marlin", "flashinfer_cutlass"], delete=True),
    dict(name="glm-4.7-flash-fp8", repo="unsloth/GLM-4.7-Flash-FP8-Dynamic", gb=32.5, parser="glm47",
         backends=["triton", "auto", "torch"], delete=True),
    # already on disk; re-run under the current repetition guards for a fair comparison
    dict(name="qwen3.8-27b-fp8", repo="Qwen/Qwen3.8-27B-FP8", gb=0, parser="qwen3",
         backends=["triton"], delete=False, have=True),
    dict(name="qwen3-30b-a3b-thinking-2507-fp8", repo="Qwen/Qwen3-30B-A3B-Thinking-2507-FP8", gb=0,
         parser="deepseek_r1", backends=["triton"], delete=False, have=True),
]


def log(msg, fh=None):
    line = f"{time.strftime('%H:%M:%S')} {msg}"
    print(line, flush=True)
    if fh:
        fh.write(line + "\n")
        fh.flush()


def sh(cmd, timeout=None, cwd=None):
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout, cwd=cwd)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def free_gb():
    return shutil.disk_usage("/").free / 1e9


def api_key():
    for line in (DEPLOY / ".env").read_text().splitlines():
        if line.startswith("VLLM_API_KEY="):
            return line.split("=", 1)[1].strip()
    return ""


# ── downloads ────────────────────────────────────────────────────────────────

def download(c, fh):
    if c.get("have"):
        return True
    while free_gb() < c["gb"] + DISK_FLOOR_GB:
        log(f"[dl {c['name']}] waiting for disk: free={free_gb():.0f}GB need={c['gb'] + DISK_FLOOR_GB:.0f}GB", fh)
        time.sleep(60)
    excl = " ".join(f"--exclude '{p}'" for p in c.get("exclude", []))
    cmd = (f"docker run --rm -v {HF_CACHE}:/root/.cache/huggingface -e HF_HOME=/root/.cache/huggingface "
           f"--entrypoint sh {IMAGE} -c \"hf download {c['repo']} {excl} >/dev/null 2>&1 && echo DL_OK\"")
    t = time.time()
    rc, out = sh(cmd, timeout=6 * 3600)
    ok = "DL_OK" in out
    log(f"[dl {c['name']}] {'ok' if ok else 'FAILED'} in {time.time() - t:.0f}s free={free_gb():.0f}GB", fh)
    return ok


def delete_weights(c, fh):
    py = ("from huggingface_hub import scan_cache_dir as s; c=s(); "
          f"h=[r.commit_hash for repo in c.repos if repo.repo_id=='{c['repo']}' for r in repo.revisions]; "
          "s().delete_revisions(*h).execute() if h else None; print('DELETED', len(h))")
    rc, out = sh(f"docker run --rm -v {HF_CACHE}:/root/.cache/huggingface -e HF_HOME=/root/.cache/huggingface "
                 f"--entrypoint python3 {IMAGE} -c \"{py}\"", timeout=600)
    log(f"[del {c['name']}] {out.strip().splitlines()[-1] if out.strip() else rc} free={free_gb():.0f}GB", fh)


# ── vLLM swap ────────────────────────────────────────────────────────────────

def set_env(c, backend):
    p = DEPLOY / ".env"
    lines = p.read_text().splitlines()
    want = {"VLLM_MODEL": c["repo"], "VLLM_REASONING_PARSER": c["parser"], "VLLM_LINEAR_BACKEND": backend}
    seen = set()
    for i, line in enumerate(lines):
        k = line.split("=", 1)[0]
        if k in want:
            lines[i] = f"{k}={want[k]}"
            seen.add(k)
    lines += [f"{k}={v}" for k, v in want.items() if k not in seen]
    p.write_text("\n".join(lines) + "\n")


def wait_ready(timeout_s=1500):
    t = time.time()
    while time.time() - t < timeout_s:
        rc, st = sh("docker inspect -f '{{.State.Status}}' vllm-agents")
        if st.strip() != "running":
            return False, f"container {st.strip()}"
        rc, logs = sh("docker logs vllm-agents 2>&1 | tail -400")
        if "Application startup complete" in logs:
            return True, f"{time.time() - t:.0f}s"
        if re.search(r"Engine core initialization failed|RuntimeError: Engine", logs):
            return False, "engine init failed"
        time.sleep(15)
    return False, "timeout"


def start_model(c, fh):
    sh("docker compose down", cwd=DEPLOY, timeout=300)
    for backend in c["backends"]:
        set_env(c, backend)
        rc, out = sh("docker compose up --detach --force-recreate", cwd=DEPLOY, timeout=300)
        ok, why = wait_ready()
        _, tail = sh("docker logs vllm-agents 2>&1 | grep -E 'Selected|KV cache size|Model loading took|"
                     "Maximum concurrency|Error|error' | grep -v WARNING | tail -12")
        if not ok:   # keep the root cause before `compose down` removes the container
            _, err = sh("docker logs vllm-agents 2>&1 | grep -v WARNING | tail -40")
            tail = (tail or "") + "\n--- failure tail ---\n" + err
        log(f"[up {c['name']}] backend={backend} ready={ok} ({why})\n{tail}", fh)
        if ok:
            return backend, tail
        last_tail = tail
        sh("docker compose down", cwd=DEPLOY, timeout=300)
    return None, locals().get("last_tail", "")


# ── measurements ─────────────────────────────────────────────────────────────

def smoke():
    key = api_key()
    body = {"model": "table-agents", "max_tokens": 512, "stream": True, "temperature": 0.6,
            "thinking_token_budget": 256,
            "messages": [{"role": "system", "content": "You are Chadwick, a nervous halfling bard."},
                         {"role": "user", "content": "GM: The vault door groans open. One spoken line, max 25 words."}]}
    req = urllib.request.Request(f"{BASE}/v1/chat/completions", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"})
    t0 = time.time()
    first = None
    reasoning, content, usage = [], [], {}
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            for raw in r:
                line = raw.decode().strip()
                if not line.startswith("data:") or line.endswith("[DONE]"):
                    continue
                ev = json.loads(line[5:])
                if ev.get("usage"):
                    usage = ev["usage"]
                for ch in ev.get("choices", []):
                    d = ch.get("delta") or {}
                    piece_r = d.get("reasoning_content") or d.get("reasoning") or ""
                    piece_c = d.get("content") or ""
                    if (piece_r or piece_c) and first is None:
                        first = time.time() - t0
                    reasoning.append(piece_r)
                    content.append(piece_c)
    except Exception as exc:  # noqa: BLE001
        return {"error": f"{type(exc).__name__}: {exc}"[:300]}
    wall = time.time() - t0
    r_txt, c_txt = "".join(reasoning), "".join(content)
    n = len(re.findall(r"\S+", r_txt + c_txt)) * 1.3        # rough token estimate
    return {"wall_s": round(wall, 1), "ttft_s": round(first or -1, 2), "approx_tok_s": round(n / max(wall, 1e-6), 1),
            "reasoning_chars": len(r_txt), "content": c_txt.strip()[:300],
            "split_ok": bool(c_txt.strip()) and "<think" not in c_txt and "</think" not in c_txt}


def run_table_round(c, d, fh):
    cmd = (f"{PY} utilities/benchmarker/lib/table_round.py --base-url {BASE} --model table-agents "
           f"--env-file {DEPLOY}/.env --seats 4,7 --shapes two_pass --repeats 2 "
           "--budget-reasoning-gm 384 --budget-reasoning-think 256 --budget-reasoning-speak 48 "
           f"--budget-reasoning-seat 256 --budget-reasoning-adjudication 64 --tag cand_{c['name']}")
    rc, out = sh(cmd, timeout=3600, cwd=REPO)
    (d / "table_round.log").write_text(out)
    rows = [json.loads(m) for m in re.findall(r"\[table_round\] (\{.*\})", out)]
    log(f"[round {c['name']}] rc={rc} rows={len(rows)}", fh)
    return rows


def run_slice(c, d, fh):
    rc, out = sh(f"{PY} scripts/table_slice_local.py --scenes 2 --retake-once --timeout 1500",
                 timeout=1800, cwd=REPO)
    (d / "slice.log").write_text(out)
    m = re.search(r"-> (\S+table_slice_\S+\.json)", out)
    rec = {"rc": rc, "warnings": len(re.findall(r" WARNING | ERROR ", out))}
    if m and pathlib.Path(m.group(1)).is_file():
        data = json.loads(pathlib.Path(m.group(1)).read_text())
        msgs = data["messages"]
        retakes = [x["payload"]["reason"].split(":")[0] for x in msgs if x["type"] == "retake"]
        rec.update({
            "wall_s": data["wall_s"], "file": m.group(1),
            "scenes_resolved": sum(1 for x in msgs if x["type"] == "scene_resolve"),
            "rounds": sum(1 for x in msgs if x["type"] == "adjudication"),
            "retakes": {k: retakes.count(k) for k in sorted(set(retakes))},
            "overrules": sum(1 for x in msgs if x["type"] == "gm_overrule"),
            "stuck": sum(1 for x in msgs if x["type"] == "scene_stuck"),
            "gm_failures": len(re.findall(r"arbiter GM \w+ failed", out)),
            "lines": [(x["type"], x["payload"].get("text", "")) for x in msgs
                      if x["type"] in ("scene_direction", "character_reply")][:16],
            "verdicts": [(x["payload"].get("verdict"), x["payload"].get("notes", "")[:140])
                         for x in msgs if x["type"] == "adjudication"][:6],
        })
    log(f"[slice {c['name']}] rc={rc} resolved={rec.get('scenes_resolved')} retakes={rec.get('retakes')}", fh)
    return rec


def mem_min_since(t0):
    p = pathlib.Path.home() / ".hermes/cache/scratch/vllm_mem_watch.log"
    vals = []
    for line in p.read_text().splitlines()[-2000:]:
        m = re.search(r"avail=([\d.]+)GiB", line)
        if m:
            vals.append(float(m.group(1)))
    return min(vals[-400:]) if vals else None


# ── report ───────────────────────────────────────────────────────────────────

def summarize_rounds(rows, n):
    rs = [r for r in rows if r.get("n_seats") == n]
    if not rs:
        return {}
    pick = lambda k: [r[k] for r in rs if r.get(k) is not None]
    avg = lambda xs: round(sum(xs) / len(xs), 1) if xs else None
    return {"wall_s": avg(pick("wall_s")), "speak_p50_s": avg(pick("speak_p50_s")),
            "think_pass_s": avg(pick("think_pass_wall_s")), "gm_direction_s": avg(pick("gm_direction_s")),
            "failed": sum(pick("n_failed")), "capped": sum(pick("n_budget_capped"))}


def build_report():
    recs = []
    for d in sorted(OUT.iterdir()) if OUT.is_dir() else []:
        f = d / "record.json"
        if f.is_file():
            recs.append(json.loads(f.read_text()))
    lines = ["# Live agent table: candidate model benchmark", "",
             f"Generated {time.strftime('%Y-%m-%d %H:%M')} by scripts/bench_candidates.py. GB10, vLLM 0.30, "
             "gpu-mem-util 0.55, reasoning budgets GM 384 / THINK 256 / SPEAK 48 / adjudication 64.",
             "Gate (P1.4 proposal): SPEAK p50 <= 15 s, THINK p95 <= 60 s, D&D round (N=4) <= 180 s, office (N=7) <= 300 s.",
             "", "| model | status | backend | load | tok/s | N=4 round | N=7 round | SPEAK p50 | THINK pass | "
             "failed/capped | slice wall | retakes | GM fails | stuck |", "|" + "---|" * 14]
    for r in recs:
        r4, r7, s, sl = r.get("round_n4", {}), r.get("round_n7", {}), r.get("smoke", {}), r.get("slice", {})
        lines.append(f"| {r['name']} | {r['status']} | {r.get('backend')} | {r.get('load')} | {s.get('approx_tok_s')} | "
                     f"{r4.get('wall_s')} | {r7.get('wall_s')} | {r4.get('speak_p50_s')} | {r4.get('think_pass_s')} | "
                     f"{r4.get('failed', '-')}/{r4.get('capped', '-')} + {r7.get('failed', '-')}/{r7.get('capped', '-')} | "
                     f"{sl.get('wall_s')} | {sl.get('retakes')} | {sl.get('gm_failures')} | {sl.get('stuck')} |")
    for r in recs:
        lines += ["", f"## {r['name']} ({r['repo']})", "",
                  f"status={r['status']} backend={r.get('backend')} min MemAvailable={r.get('mem_min_gib')} GiB",
                  "", f"smoke: {json.dumps(r.get('smoke', {}))[:500]}", ""]
        sl = r.get("slice", {})
        for t, txt in sl.get("lines", []):
            who = "GM " if t == "scene_direction" else "CHADWICK"
            lines.append(f"- **{who}** {str(txt).strip()[:300]}")
        for v, notes in sl.get("verdicts", []):
            lines.append(f"- _adjudication_ {v}: {notes}")
        if r.get("startup_log"):
            lines += ["", "```", r["startup_log"][-1500:], "```"]
    (OUT / "REPORT.md").write_text("\n".join(lines) + "\n")


# ── main ─────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="")
    ap.add_argument("--skip-download", action="store_true")
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    todo = [c for c in CANDIDATES if not args.only or c["name"] in args.only.split(",")]
    fh = open(OUT / "run.log", "a")
    log(f"=== bench_candidates start: {[c['name'] for c in todo]} free={free_gb():.0f}GB", fh)
    # "[p]ython3" so the grep cannot match its own command line
    if "vllm_mem_watch.py" not in sh("ps -eo cmd | grep '[p]ython3 .*vllm_mem_watch.py'")[1]:
        log("ABORT: scripts/vllm_mem_watch.py is not running", fh)
        return 2

    dl_done = {}

    def prefetch(c):
        dl_done[c["name"]] = download(c, fh) if not args.skip_download else True

    threads = {}
    if todo:
        threads[todo[0]["name"]] = threading.Thread(target=prefetch, args=(todo[0],))
        threads[todo[0]["name"]].start()
    for i, c in enumerate(todo):
        threads[c["name"]].join()
        if i + 1 < len(todo):                      # prefetch the next one while this one runs
            nxt = todo[i + 1]
            threads[nxt["name"]] = threading.Thread(target=prefetch, args=(nxt,))
            threads[nxt["name"]].start()
        d = OUT / c["name"]
        d.mkdir(exist_ok=True)
        rec = {"name": c["name"], "repo": c["repo"], "status": "pending", "started": time.strftime("%H:%M:%S")}
        t0 = time.time()
        if not dl_done.get(c["name"]):
            rec["status"] = "download failed"
        else:
            backend, startup = start_model(c, fh)
            rec["backend"], rec["startup_log"] = backend, startup
            if not backend:
                rec["status"] = "engine failed to start"          # startup_log has the failure tail
            else:
                rec["load"] = re.search(r"Model loading took ([\d.]+) GiB", startup or "") and \
                    re.search(r"Model loading took ([\d.]+) GiB", startup).group(1) + " GiB"
                rec["smoke"] = smoke()
                log(f"[smoke {c['name']}] {rec['smoke']}", fh)
                rows = run_table_round(c, d, fh)
                rec["round_rows"] = rows
                rec["round_n4"], rec["round_n7"] = summarize_rounds(rows, 4), summarize_rounds(rows, 7)
                rec["slice"] = run_slice(c, d, fh)
                rec["status"] = "ok" if rec["slice"].get("scenes_resolved") else "slice did not resolve"
        rec["mem_min_gib"] = mem_min_since(t0)
        rec["elapsed_min"] = round((time.time() - t0) / 60, 1)
        (d / "record.json").write_text(json.dumps(rec, indent=1, default=str))
        build_report()
        log(f"=== {c['name']}: {rec['status']} in {rec['elapsed_min']} min", fh)
        if c.get("delete"):
            sh("docker compose down", cwd=DEPLOY, timeout=300)
            delete_weights(c, fh)
    sh("docker compose down", cwd=DEPLOY, timeout=300)
    log("=== bench_candidates done (vllm-agents left DOWN)", fh)
    return 0


if __name__ == "__main__":
    sys.exit(main())
