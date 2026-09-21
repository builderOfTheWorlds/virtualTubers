# benchmarker — D&D-agents LLM benchmark utility

## Summary

One-command LLM inference benchmark for the virtualTubers campaign.
Closes W0 ("measure before deciding") from the D&D-agents design doc
(`.claude/prompts/agent_dnd_architecture.md` §13) on the GB10, across
any OpenAI-compatible or Ollama host.

The tool fires the **exact same prompt shapes** the live campaign agent
uses (from `campaigns/ashiorid`) at 8k and 16k context windows and
reports: **decode tok/s**, **prefill tok/s**, **TTFT**, **total time**,
**content tokens vs reasoning tokens** (flags budget-capped-
on-reasoning), and for the two-plan rounds, **time per full D&D round**
and **co-resident speedup** (big GM + small seat in parallel).

Two plans are tested (each is the §8 allocation):

| Plan | GM | Seats |
|---|---|---|
| **A** (tiered) | `hermes3:70b` | all 6 × `llama3.1:8b` |
| **B** (quality) | `hermes3:70b` | all 6 × `gemma4:12b-it-q4_K_M` |

## Prerequisites

- Python 3.11+ (uses stdlib `tomllib`).
- A project venv with the two runtime deps: `httpx`, `pyyaml`.
  (The repo root `.venv` is used automatically if it exists.)
- A running inference endpoint. Defaults are:
  - Ollama at `http://localhost:11434`
  - vLLM at `http://localhost:8000`

## Installation

No install step beyond the repo's existing `.venv`.

```bash
cd /home/secus/codeProjects/virtualTubers
# If the project venv doesn't yet have the deps:
.venv/bin/pip install httpx pyyaml
```

## Usage

**Full battery** (all settings from `conf/benchmark.toml`):

```bash
./utilities/benchmarker/bin/runBenchmark.sh
```

**Dry-run** (prints the resolved plan, no LLM calls):

```bash
./utilities/benchmarker/bin/runBenchmark.sh --dry-run
```

**CLI overrides** (any flag overrides the same key in the config file):

```bash
# vLLM instead of Ollama
./utilities/benchmarker/bin/runBenchmark.sh \
    --host vllm --base-url http://127.0.0.1:8000

# Quick quick-bench: one model, one context tier, skip plan-rounds
./utilities/benchmarker/bin/runBenchmark.sh \
    --models llama3.1:8b --ctx-k 8 --skip-full-round

# Different config file
BENCH_MARKER_CONFIG=/path/to/another.toml \
  ./utilities/benchmarker/bin/runBenchmark.sh
```

**Live progress** (from another shell):

```bash
tail -f utilities/benchmarker/output/progress.jsonl
```

## Configuration

The single config source is `utilities/benchmarker/conf/benchmark.toml`:

```toml
[host]
name = "ollama"                     # "ollama" | "vllm"
base_url = "http://localhost:11434"

[models]
list = ["hermes3:70b", "llama3.1:8b", "gemma4:12b-it-q4_K_M"]

[run]
ctx_k = [8, 16]
think = false                       # false = canonical "spoken line" metric
temperature = 0.7
concurrency_n = 2
skip_full_round = false

[output]
dir = "utilities/benchmarker/output"
report = "utilities/benchmarker/output/report.md"
```

Precedence (high → low):

1. CLI flag
2. `$BENCH_MARKER_CONFIG` (points at an alt TOML file)
3. The TOML file at `conf/benchmark.toml` (or wherever `--config`
   points)
4. Built-in defaults in `lib/config.py`

## Project structure

```
utilities/benchmarker/
├── bin/runBenchmark.sh          # launcher — resolves .venv, config, runs main.py
├── conf/benchmark.toml          # the single config source
├── main.py                      # thin entry point (adds lib/ to sys.path)
├── lib/
│   ├── host_base.py             # CompletionResult, errors, HostModel, HostState
│   ├── config.py                # TOML loader + CLI-override + path resolver
│   ├── prompts.py               # real-campaign prompt builder (sheets, lore, scenes)
│   ├── ollama_host.py           # Ollama native /api/chat adapter (best timing)
│   ├── vllm_host.py             # vLLM OpenAI v1 adapter + Prometheus KV-cache state
│   └── runner.py                # the battery: per-model + per-plan + co-resident
├── output/                      # run artifacts (run.json, report.md, progress.jsonl)
│   ├── .gitkeep                 # keeps the empty output dir in git
│   ├── run.json                 # the committed 2026-09-20 baseline (22 cells)
│   └── progress.jsonl           # append-only live log from the same run
└── README.md                    # this file
utilities/tests/benchmark/
├── conftest.py                  # puts lib/ + app/ on sys.path
└── test_benchmarker.py          # 28 hermetic unit tests
```

## Testing

```bash
.venv/bin/pytest utilities/tests/benchmark/ -v
```

All 28 tests pass without a live host. Live-host behavior (actual
tok/s) is exercised by the benchmark run itself, not the unit tests —
so `pytest` stays fast and hermetic.

See `[pytest]` in `pytest.ini` for the full test-path list (this suite
is one of the four `testpaths`).

## Outputs

- `utilities/benchmarker/output/run.json` — machine-readable: every
  cell + host state before/after + config + any mid-run error.
- `utilities/benchmarker/output/report.md` — human-readable, with four
  sections (per-model, co-resident, full-round, host-state).
- `utilities/benchmarker/output/progress.jsonl` — one line per cell,
  `tail -f` for live progress.

## See also

- `.claude/prompts/benchmark_methodology_dnd_agents.md` — full
  methodology + the 2026-09-20 first-baseline findings + how to read
  the report.
- `.claude/prompts/agent_dnd_architecture.md` — the design doc this
  utility validates.
- `campaigns/ashiorid/` — the real campaign pack the prompts are built from.
