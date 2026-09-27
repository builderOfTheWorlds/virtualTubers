"""
office_w0_benchmark.py — OB-07 W0 model benchmark (report only).

Measures, against the gx10 Ollama server, per model:
  (a) an in-character office dialogue line (~150 output tokens) at
      concurrency 1, 4 and 8 -> TTFT, p50/p95 latency, per-request decode
      tok/s, aggregate tok/s;
  (b) an ~800-token code-edit completion (Python fraud rule + pytest) at
      concurrency 1 and 2.
Also records model load time (warm-up request's load_duration + wall time)
and /api/ps snapshots before/after each model to observe residency
(OLLAMA_MAX_LOADED_MODELS / NUM_PARALLEL behaviour is inferred, not read).

Thinking-capable models are run with "think": false (Ollama >=0.9 flag) so
the numbers reflect visible-output latency; the flag is recorded per row.

Usage (from the repo root, git-bash):
  .venv/Scripts/python.exe .claude/prompts/office_w0_benchmark.py \
      --models qwen3.8:27b gemma4:26b --out "$TMPDIR/office_w0_results.json"

Only stdlib is used. Never pulls or deletes models.
"""
import argparse
import json
import logging
import statistics
import sys
import threading
import time
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor

DEFAULT_HOST = "http://192.168.1.23:11434"
DEFAULT_MODELS = ["qwen3.8:27b", "gemma4:26b", "gemma4:12b-it-q4_K_M",
                  "llama3.1:8b", "hermes3:70b"]
NUM_CTX = 8192

log = logging.getLogger("office_w0_benchmark")
RUN_ID = uuid.uuid4().hex[:8]

DIALOGUE_SYSTEM = (
    "You are Dana Okafor, the Tech Lead of Ashiorid, a fintech company whose "
    "product Fraud-Stop is an enterprise SaaS that banks send card transactions "
    "through to get a fraud verdict. You are dry, precise, protective of your "
    "engineers, and allergic to hype. You are in the office stand-up. Speak one "
    "in-character line of dialogue only (no stage directions, no lists), about "
    "100 words."
)
DIALOGUE_USER = (
    "CEO: \"The Northbank pilot wants velocity rules live by Friday. Can we ship "
    "it, and what's the risk if we rush?\" Reply to the CEO in the stand-up."
)
CODE_SYSTEM = "You are a senior Python engineer. Output only code in fenced blocks, no prose."
CODE_USER = (
    "Write a Python module `velocity_rule.py` for the Fraud-Stop engine with a "
    "function `evaluate_velocity(transactions: list[dict], card_id: str, now: datetime, "
    "window_s: int = 600, max_count: int = 5, max_amount: Decimal = Decimal('2000')) -> dict` "
    "that returns {'verdict': 'allow'|'review'|'block', 'reasons': [...], 'count': int, "
    "'total': Decimal}. Block when both limits are exceeded, review when one is. Validate "
    "inputs (raise ValueError), ignore other cards and transactions outside the window, "
    "use structured logging. Then write `test_velocity_rule.py` with pytest: parametrized "
    "cases for allow/review/block, window edges, other-card filtering, and invalid input."
)

TESTS = {
    "dialogue": {"system": DIALOGUE_SYSTEM, "user": DIALOGUE_USER, "num_predict": 150,
                 "levels": [1, 4, 8], "c1_repeats": 3},
    "code": {"system": CODE_SYSTEM, "user": CODE_USER, "num_predict": 800,
             "levels": [1, 2], "c1_repeats": 1},
}


def _http_json(host, path, payload=None, timeout=30):
    """GET (payload None) or POST JSON; returns decoded dict."""
    log.debug("http_call path=%s run_id=%s", path, RUN_ID)
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(host + path, data=data,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def ps_snapshot(host):
    """Return a compact list of loaded models from /api/ps."""
    try:
        models = _http_json(host, "/api/ps").get("models", [])
    except Exception as exc:  # noqa: BLE001 — snapshot is best effort
        log.error("ps_snapshot_failed error=%s run_id=%s", exc, RUN_ID)
        return [{"error": str(exc)}]
    return [{"name": m["name"], "size_gb": round(m.get("size", 0) / 1e9, 1),
             "vram_gb": round(m.get("size_vram", 0) / 1e9, 1),
             "ctx": m.get("context_length"), "expires_at": m.get("expires_at")}
            for m in models]


class PsPoller:
    """Polls /api/ps every `interval` s during a model's run to spot other
    load: foreign models appearing, or our model reloaded with another ctx."""

    def __init__(self, host, interval=5.0):
        self.host, self.interval = host, interval
        self.seen, self._stop = {}, threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        while not self._stop.is_set():
            for m in ps_snapshot(self.host):
                key = f"{m.get('name')}@ctx{m.get('ctx')}"
                self.seen[key] = self.seen.get(key, 0) + 1
            self._stop.wait(self.interval)

    def start(self):
        self._thread.start()

    def stop(self):
        self._stop.set()
        self._thread.join(timeout=10)
        return self.seen


def capabilities(host, model):
    try:
        return _http_json(host, "/api/show", {"model": model}).get("capabilities", [])
    except Exception as exc:  # noqa: BLE001
        log.error("show_failed model=%s error=%s run_id=%s", model, exc, RUN_ID)
        return []


def stream_chat(host, model, system, user, num_predict, think_off, timeout=900):
    """One streaming /api/chat call. Returns timing + Ollama final-chunk stats."""
    payload = {"model": model, "stream": True,
               "messages": [{"role": "system", "content": system},
                            {"role": "user", "content": user}],
               "options": {"num_predict": num_predict, "num_ctx": NUM_CTX,
                           "temperature": 0.7}}
    if think_off:
        payload["think"] = False
    t0 = time.perf_counter()
    first_chunk = first_token = None
    text, thinking_chars, final = [], 0, {}
    try:
        req = urllib.request.Request(host + "/api/chat", data=json.dumps(payload).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            for raw in resp:
                if not raw.strip():
                    continue
                chunk = json.loads(raw)
                now = time.perf_counter()
                if first_chunk is None:
                    first_chunk = now
                msg = chunk.get("message", {})
                if msg.get("thinking"):
                    thinking_chars += len(msg["thinking"])
                if msg.get("content"):
                    if first_token is None:
                        first_token = now
                    text.append(msg["content"])
                if chunk.get("done"):
                    final = chunk
    except Exception as exc:  # noqa: BLE001 — record and continue the benchmark
        log.error("chat_failed model=%s error=%s run_id=%s", model, exc, RUN_ID)
        return {"error": str(exc), "wall_s": time.perf_counter() - t0}
    wall = time.perf_counter() - t0
    out = "".join(text)
    ec, ed = final.get("eval_count", 0), final.get("eval_duration", 0)
    pc, pd = final.get("prompt_eval_count", 0), final.get("prompt_eval_duration", 0)
    return {
        "wall_s": round(wall, 3),
        "ttft_s": round((first_token or first_chunk or t0 + wall) - t0, 3),
        "eval_count": ec,
        "decode_tps": round(ec / (ed / 1e9), 2) if ed else None,
        "prompt_tokens": pc,
        "prefill_tps": round(pc / (pd / 1e9), 1) if pd else None,
        "load_s": round(final.get("load_duration", 0) / 1e9, 3),
        "done_reason": final.get("done_reason"),
        "think_leak": "<think>" in out,
        "thinking_chars": thinking_chars,
        "words": len(out.split()),
        "sample": out[:300],
    }


def pct(values, p):
    if not values:
        return None
    s = sorted(values)
    k = (len(s) - 1) * p / 100
    lo, hi = int(k), min(int(k) + 1, len(s) - 1)
    return round(s[lo] + (s[hi] - s[lo]) * (k - lo), 2)


def run_level(host, model, test, conc, think_off):
    """Run `conc` concurrent requests (or c1_repeats sequential ones at c=1)."""
    spec = TESTS[test]
    n_req = spec["c1_repeats"] if conc == 1 else conc
    t0 = time.perf_counter()
    if conc == 1:
        results = [stream_chat(host, model, spec["system"], spec["user"],
                               spec["num_predict"], think_off) for _ in range(n_req)]
    else:
        with ThreadPoolExecutor(max_workers=conc) as pool:
            futs = [pool.submit(stream_chat, host, model, spec["system"], spec["user"],
                                spec["num_predict"], think_off) for _ in range(n_req)]
            results = [f.result() for f in futs]
    wall = time.perf_counter() - t0
    ok = [r for r in results if "error" not in r]
    lat = [r["wall_s"] for r in ok]
    ttft = [r["ttft_s"] for r in ok]
    tokens = sum(r["eval_count"] for r in ok)
    row = {
        "model": model, "test": test, "concurrency": conc, "requests": n_req,
        "errors": len(results) - len(ok), "think_off": think_off,
        "lat_p50": pct(lat, 50), "lat_p95": pct(lat, 95),
        "ttft_p50": pct(ttft, 50), "ttft_p95": pct(ttft, 95),
        "per_req_decode_tps": round(statistics.mean(
            [r["decode_tps"] for r in ok if r["decode_tps"]]), 2) if ok else None,
        "prefill_tps": round(statistics.mean(
            [r["prefill_tps"] for r in ok if r["prefill_tps"]]), 1) if ok else None,
        "avg_tokens": round(tokens / len(ok), 1) if ok else 0,
        # at c=1 requests are sequential, so aggregate == tokens / summed wall
        "aggregate_tps": round(tokens / wall, 2) if wall else None,
        "level_wall_s": round(wall, 2),
        "think_leak": any(r.get("think_leak") for r in ok),
        "thinking_chars": sum(r.get("thinking_chars", 0) for r in ok),
        "done_reasons": sorted({str(r.get("done_reason")) for r in ok}),
        "sample": ok[0]["sample"] if ok else results[0].get("error"),
    }
    log.info("level_done model=%s test=%s c=%s p50=%s agg_tps=%s run_id=%s",
             model, test, conc, row["lat_p50"], row["aggregate_tps"], RUN_ID)
    return row


def bench_model(host, model, skip_code_c2=False):
    log.info("model_start model=%s run_id=%s", model, RUN_ID)
    caps = capabilities(host, model)
    think_off = "thinking" in caps
    entry = {"model": model, "capabilities": caps, "think_off": think_off,
             "ps_before": ps_snapshot(host)}
    t0 = time.perf_counter()
    warm = stream_chat(host, model, "Reply with OK.", "OK?", 4, think_off)
    entry["warmup_wall_s"] = round(time.perf_counter() - t0, 2)
    entry["warmup_load_s"] = warm.get("load_s")
    entry["ps_after_load"] = ps_snapshot(host)
    # Discarded primer: the first real-length request after a (re)load can
    # absorb residual load/other-client queueing, which skewed c=1 TTFT.
    stream_chat(host, model, DIALOGUE_SYSTEM, DIALOGUE_USER, 32, think_off)
    poller = PsPoller(host)
    poller.start()
    rows = []
    for test, spec in TESTS.items():
        for conc in spec["levels"]:
            if test == "code" and conc == 2 and skip_code_c2:
                continue
            rows.append(run_level(host, model, test, conc, think_off))
    entry["rows"] = rows
    entry["ps_timeline"] = poller.stop()
    entry["ps_after"] = ps_snapshot(host)
    return entry


def main():
    ap = argparse.ArgumentParser(description="OB-07 W0 Ollama benchmark for the office agents")
    ap.add_argument("--host", default=DEFAULT_HOST)
    ap.add_argument("--models", nargs="+", default=DEFAULT_MODELS)
    ap.add_argument("--skip-code-c2", nargs="*", default=[],
                    help="models for which the code c=2 level is skipped (time budget)")
    ap.add_argument("--out", required=True, help="JSON results path (appended per model)")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, stream=sys.stderr,
                        format="%(asctime)s %(levelname)s %(message)s")
    try:
        with open(args.out) as fh:
            results = json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError):
        results = {"host": args.host, "num_ctx": NUM_CTX, "models": []}
    results.setdefault("version", _http_json(args.host, "/api/version").get("version"))
    results.setdefault("ps_at_start", ps_snapshot(args.host))
    for model in args.models:
        entry = bench_model(args.host, model, model in args.skip_code_c2)
        results["models"] = [m for m in results["models"] if m["model"] != model] + [entry]
        with open(args.out, "w") as fh:
            json.dump(results, fh, indent=1)
        for r in entry["rows"]:
            print(json.dumps({k: r[k] for k in ("model", "test", "concurrency", "lat_p50",
                  "lat_p95", "ttft_p50", "per_req_decode_tps", "aggregate_tps",
                  "avg_tokens", "errors")}), flush=True)
    print("ps_end", json.dumps(ps_snapshot(args.host)))


if __name__ == "__main__":
    main()
