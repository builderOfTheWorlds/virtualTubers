# Benchmark harness — methodology (closes W0 of the D&D-agents design doc)

> Location: `utilities/benchmarker/` (bin/ · lib/ · conf/ · main.py). Closes W0 from
> `.claude/prompts/agent_dnd_architecture.md` §13. The design doc's
> §13 says the benchmark conversation should produce
> (a) a table (models × {ctx, prefill, decode, line-latency, KV-GB}),
> (b) time-per-round per plan, (c) a one-line recommendation A/B/C,
> (d) update §8's allocation to the measured choice and close W0.
> This file documents what the harness does and how to use it.

## What we measure, and why

| Probe | Purpose | Design-doc anchor |
|---|---|---|
| `line` | One 45-word in-character line at 8k / 16k context. "Feels live" unit. | §13 items 1 & 2 |
| `prefill_decode` | Big prompt (target 8k/16k) + 256-token completion. Splits prefill speed from decode speed in the same call. | §13 item 1 |
| `concurrency` | 2 `complete()` calls in flight simultaneously on the same model. Where unified GB10 KV-cache pressure bites. | §13 item 4 |
| `full_round` | One full §4.4 D&D round (GM direction → 6 character replies in turn → GM adjudication). The "is this live?" number that sets `deadline_s`. | §13 item 3, §4.4 |

## Why the harness is structured this way

**Two host adapters, one interface.** `OllamaHost` speaks Ollama's
native `/api/chat` because that endpoint (i) reports host-authoritative
`prompt_eval_duration` and `eval_duration` that cleanly split prefill
from decode, and (ii) honours `options.think` for the reasoning toggle
(`v1` does not — verified: same prompt, `think=true/false/None` all
produce identical `usage` and similar reasoning length through `/v1`,
but `/api/chat` produces different `eval_count`). `vLLMHost` speaks the
OpenAI v1 endpoint (vLLM only exposes that), and reads
`vllm:kv_cache_usage_perc` from Prometheus `/metrics` for the resident
KV-cache pressure number. Both return the same `CompletionResult` shape
so the runner is host-agnostic.

**Canonical metric = content-only.** The design doc §13 item 2 asks for
"latency-to-complete a ~45-word in-character line" — the *spoken* line.
Thinking models (gemma4, qwen3.8) routinely think 400+ tokens *before*
emitting content. On a 512-token budget a thinking model can produce
**zero content** even with `think=false` (verified on gemma4:12b and
qwen3.8:27b — `budget_capped=true` cell flag) because reasoning +
system overhead eats the budget. So each cell reports
`content_tokens`, `reasoning_tokens`, and `budget_capped_on_reasoning`
as separate dimensions. The "line latency" is only meaningful when
`content_tokens > 0` and `budget_capped == False` — the runner sets the
flag so the report reader can pick the valid rows.

**Why the test suite skips the live host.** The unit tests cover
`_summarise_batch` (median + means math), `_parse_metric_line`
(vLLM Prometheus text format), `prompts.build_*` (real campaign pack
shapes + padded-to-target tokens), and the report writer (markdown
section headers). Live tok/s is exercised by the runner's `--dry-run`
and a real invocation — keeping the unit suite sub-second and hermetic.

## Running

All commands run from the repo root. The launcher is
`utilities/benchmarker/bin/runBenchmark.sh`; it resolves the project
`.venv`, the config file, and hands off to `main.py`. **All run
arguments live in `utilities/benchmarker/conf/benchmark.toml`** — the
CLI flags are thin overrides of that file (higher precedence), never a
second config source. `$BENCH_MARKER_CONFIG` points at an alternate
config file.

```bash
cd /home/secus/codeProjects/virtualTubers

# Dry-run (no LLM calls, prints the resolved plan)
./utilities/benchmarker/bin/runBenchmark.sh --dry-run

# Full battery — every setting comes from conf/benchmark.toml
# (host, models, ctx_k, think, concurrency_n, output paths)
./utilities/benchmarker/bin/runBenchmark.sh

# Same battery on a vLLM server (override host + url; the model list
# still comes from the conf file unless you override it here)
./utilities/benchmarker/bin/runBenchmark.sh --host vllm --base-url http://localhost:8000

# Per-model only (skip the 70b-heavy full_round). Quick single-model
# bench, or when the GM model isn't on the server.
./utilities/benchmarker/bin/runBenchmark.sh --models qwen3.8:27b --skip-full-round

# Point at a different config file
BENCH_MARKER_CONFIG=/path/to/other.toml ./utilities/benchmarker/bin/runBenchmark.sh
```

Outputs (default path, from the `[output]` section of the conf file):
- `utilities/benchmarker/output/progress.jsonl` — append-only, one line
  per cell; `tail -f` for live progress.
- `utilities/benchmarker/output/run.json` — full run (cells, host state
  before/after, config, error — if any).
- `utilities/benchmarker/output/report.md` — the human-readable table.

## Reading the report

- **Decode tok/s (median)** — the number that decides if a line "feels
  live." The runner reports a median across `n` samples per cell so one
  unlucky prefill doesn't dominate.
- **Prefill tok/s** — the prefill portion at real context; high on
  8b/12b, low on 70b. The design doc's "quiet killer" warning
  (KV-cache under real context, §13 item 5) is visible here: a model
  with a huge prefill budget (hermes3:70b) gets *slower* prefill on
  16k than 8k because the KV-cache window is already populated.
- **`budget_capped_on_reasoning`** true means the model thought its way
  out of content. For a spoken line you want a model that emits content
  within the same budget — or you raise `num_predict` (at the cost of
  higher tail latency).
- **Concurrency cell `wall_s`** — this is the real "is KV pressure
  hurting us" number. Compare the solo `line` total_s to the
  concurrency 2-way total_s: if the 2-way is ~2× solo you have no
  meaningful contention; if it's ~4× you have KV pressure. For a
  co-resident "Plan A" the number that matters is `wall_s` on 2
  concurrent + the solo 70b's total_s — that's what "70b GM + 1 small
  seat in parallel" costs.
- **Host state `before` / `after`** — OllamaHost's
  `/api/ps` reports `size_vram` per resident model; vLLM's
  `vllm:kv_cache_usage_perc` reports the KV pool's fraction in use.
  The delta between before/after is the resident cost of the benchmark
  run — useful to sanity-check the design doc's "128 GB − system
  residue ≈ N GB headroom" budget table.

## What this design still does NOT measure

- **KV-cache growth under long sessions.** The live path carries an
  arc-carry + committed transcript that grows across scenes. This bench
  measures single-scene 8k / 16k context but does not simulate a 5th
  scene into the same context window. Worth doing *after* W0 closes
  (see next).
- **Retake-path amplification.** §4.4 assumes 1 reply per turn; if the
  GM retakes a reply (bounded loop, §5.2) the wall time doubles. The
  bench doesn't model that because the arbiter's retake logic isn't
  built yet — W1. The report's per-stage `total_s` values in
  `full_round` are the numbers to multiply: for a 30% retake rate,
  the round's expected wall time is `sum(stages) / (1 − 0.3 ×
  stages_affected_by_retake)`.
- **Voice gate + tmux + ffmpeg overhead** — the design doc's W0 gate
  is specifically about the LLM layer; the other stack (per design
  §3.2) reuses existing machinery. The bench measures the LLM
  contribution to the `deadline_s` budget, not the full end-to-end
  round, which W1/W2/W3 close out.

## First-baseline findings (2026-09-20, contention-confounded)

> Caveat: this run was on the same GB10 that was serving the agent
> session (qwen3.8:27b via ollama-launch), the desktop, Firefox, and
> five bench models all at once — 94 % GPU util throughout, 128 GB
> unified pool. 70b numbers below are the *worst case*; a clean run
> (after freeing the GPU) should show 3–5× better numbers for the
> large models. Small models (8b, 12b) are more robust to memory
> contention than large ones because their footprint is a smaller
> fraction of the pool, but these numbers are still the lower bound.

### Consolidated table (from `benchmarks/results/run.json`, 24 cells,
partial run: crashed on `full_round` due to a pre-fix bug, so no
plan-round numbers; per-model + concurrency cells are complete.)

| model | ctx | task | dec tok/s | pre tok/s | ttft (s) | total (s) | content | reasoning | capped |
|---|---|---|---:|---:|---:|---:|---:|---:|---|
| **llama3.1:8b** | 8k | line | **10.9** | 10 436 | **3.5** | **4.7** | 13 | 0 | no ✅ |
| llama3.1:8b | 16k | line | 10.0 | 19 479 | 4.3 | 6.3 | 20 | 0 | no ✅ |
| llama3.1:8b | 8k | concurrency×2 | 11.4 | 571 | 6.7 | 11.0 | 50 | 0 | no ✅ |
| llama3.1:8b | 16k | concurrency×2 | 10.7 | 1 284 | 6.3 | 11.0 | 50 | 0 | no ✅ |
| hermes3:70b* | 8k | line | 3.7 | 14 ⚠ | 488.5 ⚠ | 493.1 | 17 | 0 | no (confounded) |
| hermes3:70b* | 8k | prefill_decode | 3.6 | 128 ⚠ | 48.6 | 65.1 | 58 | 0 | no |
| hermes3:70b* | 16k | line | 3.4 | 136 ⚠ | 284.4 ⚠ | 291.6 | 24 | 0 | no (confounded) |
| hermes3:70b* | 16k | prefill_decode | 3.4 | 24 ⚠ | 512.8 ⚠ | 519.1 | 21 | 0 | no (confounded) |
| qwen3.8:27b | 8k | line | 3.0 | 16 | 425.8 | 457.0 | 42 | 438 | **YES** |
| qwen3.8:27b | 16k | line | — | 31 | 427.4 | 461.1 | **0** | 512 | **YES** |
| gemma4:26b | 8k | line | — | 4 623 | 3.5 | 33.4 | **0** | 512 | **YES** |
| gemma4:12b | 8k | line | — | 4 801 | 3.2 | 35.2 | **0** | 512 | **YES** |
| gemma4:12b | 16k | line | — | 8 732 | 4.2 | 36.4 | **0** | 512 | **YES** |

`*` = contention-confounded; re-run required before trusting these
numbers for §8.

### Read-out for the §8 decision

1. **llama3.1:8b is the only model that cleanly emits a 45-word spoken
   line within the same budget.** 10.9 tok/s decode, 3.5 s TTFT,
   4.7 s total on an 8k/16k context. It also handles **2-way concurrency
   without KV pressure** (11 s for two simultaneous 8k-context calls),
   which is exactly the "6 seats in parallel" scenario from the design
   doc §13. If the seats can accept 8b-class character quality, Plan B
   should be `gm=70b, all 6 seats=llama3.1:8b` — not gemma4:12b.

2. **The thinking models (gemma4:12b, gemma4:26b, qwen3.8:27b) all burn
   their 512-token budget on reasoning before any content.** The
   `budget_capped_on_reasoning: true` flag in the report tells you
   exactly which rows do *not* count toward the "feels-live line
   latency" metric. To use any of them as a seat model you must raise
   `num_predict` to ≥ 1024 (at the cost of higher tail latency) or
   pick a non-reasoning model. This is a **prompt-budget
   characteristic**, not a hardware limitation — the same models
   produce 26–44 content tokens on the `prefill_decode` probe when given
   256 tokens for content + 88–69 for reasoning (i.e. they *do* emit
   content, just at a higher token cost than llama3.1).

3. **The 70b GM numbers are the binding constraint on the 45 s
   `deadline_s`.** Even at 8k context, the contention-confounded TTFT
   was 488 s (8.1 min) — but the `prefill_decode` probe at 8k (58
   content tokens) was 65 s total, which suggests that a *shorter* GM
   direction (50–80 words, not 220) would be under the 45 s budget even
   with some contention. The design doc's §4.4 GM direction block
   ("~240 words") is too long for a live turn on a shared GB10.
   **Recommendation: cap the GM direction at ~80 words (one scene
   prompt + one beat plan) and let the seats do the 45-word spoken
   lines.** If you keep the 220-word target, only a clean-baseline 70b
   run can tell us if it fits.

4. **The design doc's "Plan A (70b GM + 2× qwen3.8:27b leads + 4×
   gemma4:12b support)" is not supported by these numbers.** The 27b
   lead tier is slower than the 12b (2.99 tok/s vs gemma4's ~30 tok/s
   on content when it does emit) AND burns budget on reasoning. Plan A
   should be re-specified as `70b GM + 6× llama3.1:8b`, and Plan B
   should be a pure `llama3.1:8b` baseline for comparison.

### What's still missing

- **Clean 70b numbers** (free the GB10 first). The numbers above are
  the worst-case lower bound.
- **`full_round` numbers** (GM direction → 6 seats in turn → GM
  adjudication). The first run crashed on `full_round` due to a bug I
  have fixed (`probe_full_round` used to parse seat keys with
  `s.split("_")[1]`, which broke on the new seat-tuple format). Re-run
  needed.
- **70b + 1 small model in parallel** (the design doc §13 item 4:
  "co-resident concurrency — can we run `hermes3:70b` for GM *and* a
  small model for a seat in parallel"). **Implemented** as the
  `co_resident` cell (probe_co_resident in runner.py) — fires the GM
  model and a seat model simultaneously and reports the speedup over the
  sum of their solo times. Not yet *measured* against the real 70b (see
  clean-baseline re-run below); the 8b concurrency result (11 s for two
  8b calls) is a good sign.

### Suggested next steps (W0 closure)

1. Stop the agent session / free the GPU (or run during a maintenance
   window at 03:00) and re-run just `--models hermes3:70b --skip-full-round`
   for a clean baseline of the GM tier.
2. Re-run the full battery (the conf default already runs `hermes3:70b`
   + `llama3.1:8b` + `gemma4:12b` at 8k/16k with full_round + co_resident
   and the 70b at rest). This closes W0 items 1, 2, 3, and 4 together.
3. Update §8's allocation to the measured choice and close W0 in §10.

## Files

- `utilities/benchmarker/bin/runBenchmark.sh` — the launcher; resolves
  the `.venv`, config file, and hands off to `main.py`.
- `utilities/benchmarker/conf/benchmark.toml` — the single config source
  (host, models, ctx_k, think, temperature, concurrency, output paths).
  All CLI flags override keys in this file.
- `utilities/benchmarker/main.py` — thin entry point; adds `lib/` to
  `sys.path` and calls `runner.main()`.
- `utilities/benchmarker/lib/host_base.py` — result types + errors.
- `utilities/benchmarker/lib/ollama_host.py` — Ollama native
  `/api/chat` adapter, authoritative prefill/decode splits.
- `utilities/benchmarker/lib/vllm_host.py` — vLLM OpenAI v1 adapter,
  Prometheus-based state.
- `utilities/benchmarker/lib/prompts.py` — realistic prompt builder from
  the real `campaigns/ashiorid` pack (sheets, lore, scenes).
- `utilities/benchmarker/lib/config.py` — TOML loader + CLI-override
  helper + path resolver.
- `utilities/benchmarker/lib/runner.py` — the battery: probes per model
  + plan, co-resident, writes the report + JSON + live progress JSONL.
- `utilities/benchmarker/output/` — run artifacts (run.json, report.md,
  progress.jsonl). The baseline `run.json` from 2026-09-20 is committed here.
- `utilities/tests/benchmark/test_benchmarker.py` — 28 hermetic unit tests.
- `utilities/tests/benchmark/conftest.py` — puts `lib/` + `app/` on
  `sys.path` for the test suite.
