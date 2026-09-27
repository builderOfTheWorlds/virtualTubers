# Character v4: Build Status

The tracker for `docs/charcterProfileGenerationNotes/character_v4_build_playbook.md`.
The build orchestrator (qwen3.8:27b) updates it after every WP, blocked run
or question. Newest notes go at the bottom of each section.

Plan: `docs/charcterProfileGenerationNotes/character_generator_updater_v4.md`
Decisions: `docs/charcterProfileGenerationNotes/character_v4_decisions.md`

## Pre-build facts (planner, 2026-09-25)

- Plan validation: `.claude/prompts/character_v4_plan_validation.py` passes
  33/33 on pgserver 0.1.4 (PG16 + pgvector).
- Recall probe: `.claude/prompts/character_v4_recall_probe.py` passes (real
  nomic-embed-text).
- gx10 Ollama models: qwen3.8:27b, qwen3-coder:30b, llama3.1:8b,
  gemma4:12b-it-q4_K_M, nomic-embed-text, and others.
- The project `.venv` is Windows (`.venv/Scripts/python.exe`, Python
  3.11.16). It has no `tzdata`, so WP-03 adds it.
- pgserver 0.1.4 has no Linux aarch64 wheel. On gx10, use `CHARACTER_TEST_DSN`.

## Baseline (WP-00)

| Host | Date | passed | failed | errors | Command |
|---|---|---|---|---|---|
| dev PC (Windows) | 2026-09-26 | 2826 | 56 | 86 | `python -m pytest -q --timeout 300` |

**Baseline caveat (environmental, not a regression):**
- 86 errors: 54 are `psycopg2.OperationalError: connection refused` to
  `localhost:5432` (3layer-generator DB tests want a live local Postgres, which
  is not running on the dev PC); 32 were the benchmarker/3layer `runner.py`
  sys.path collision, fixed separately in 45b2f5d. The playbook anticipates this: integration
  tests from WP-03 onward use pgserver or `CHARACTER_TEST_DSN`, not localhost.
- 56 failures are in files WP-00 does not touch (3LayersWeeklyGeneration
  test_config, 3layer-generator test_api/service_runner, voice_gate, tile_pane,
  character_schema).
- Proof WP-00 introduced none: the change is confined to `tools/qwen_worker/`
  (sandbox.py, runner.py, ollama_client.py) + one new test file; no other
  suite imports from `tools/qwen_worker`, and the `runner`-name sys.path
  collision with the benchmarker was removed (the test loads harness modules
  by file path, no sys.path mutation). A targeted pair run of the new tests +
  the benchmarker suite passes 33/33.
- The stale handover reference was 2551/42/36 (recorded a different date,
  with local Postgres up and fewer tests). **Future "still green" checks
  compare against this 2826/56/86 baseline**, not the handover number.

## Operator steps

| Step | Status | Notes |
|---|---|---|
| OP-1 stop stale phase-2 runs | todo | ask the user first |
| OP-2 deploy character-profile-db | todo | |
| OP-3 Kafka retention | todo | |
| OP-4 backups + restore drill | todo | after WP-02 |
| OP-5 gx10 test DB (optional) | todo | |

## Work packages

Status values: todo, tests-written, running, passed, promoted, gated,
BLOCKED.

| WP | Status | Spec(s) | Runs / model | Gate result | Gate host |
|---|---|---|---|---|---|
| WP-00 harness on Windows | gated | (by hand) | n/a | preflight OK twice; baseline 2826/56/86 recorded | dev PC (Windows) |
| WP-01 reader role | gated | local vLLM subagent (qwen3.8-27b) + hand fix | n/a | run_tests.sh 58/0; pytest db_scripts 1/1; compose config OK; stub-psql dry run both branches exit 0. Subagent indented the heredoc `SQL` terminators (script would not parse) -- fixed by hand, added bash -n regression checks. Subagent wall time ~7.5 h. | dev PC (Windows) |
| WP-02 backup.sh | todo | | | | |
| WP-03 config + clock + shapes | todo | | | | |
| WP-04 db + migrations | todo | | | | |
| WP-05 stores | todo | | | | |
| WP-06 jobs + CLI + container | todo | | | | |
| **Phase 1 gate** | | | | | |
| WP-07 clean | todo | | | | |
| WP-08 tag | todo | | | | |
| WP-09 cast | todo | | | | |
| WP-10 load source | todo | | | | |
| WP-11 llm / embeddings / node names | todo | | | | |
| WP-12 timeline | todo | | | | |
| WP-13 backstory | todo | | | | |
| WP-14 baseline + export + initialize | todo | | | | |
| **Phase 2 gate** | | | | | |
| WP-15 contracts + attribution | todo | | | | |
| WP-16 ingest | todo | | | | |
| WP-17 messages M1/M2 + logger | todo | | | | |
| WP-18 compaction | todo | | | | |
| WP-19 summaries + daily job | todo | | | | |
| WP-20 fragments + weekly reset | todo | | | | |
| WP-21 testctl | todo | | | | |
| WP-22 brief | todo | | | | |
| WP-23 recall + harness | todo | | | | |
| WP-24 live driver + story jobs | todo | | | | |
| WP-25 e2e two weeks | todo | | | | |
| **Phase 3 gate** | | | | | |
| WP-26 docs | todo | | | | |

## Test list → test name mapping

### WP-00

- T00.1 -> test_venv_python_finds_posix
- T00.2 -> test_venv_python_finds_windows
- T00.3 -> test_venv_python_missing_raises
- T00.4 -> test_build_sandbox_copies_deploy_and_skips_har
- T00.5 -> test_env_vars_override_ollama_defaults

## Measurements

- Stage 1 model calibration (WP-07): model | guard pass % | s/chapter | chosen
- Stage 2 speaker accuracy (WP-08):
- Recall thresholds after tuning (WP-23):

## Test corrections (each needs a plan citation)

- WP-00 T00.5 (2026-09-26): the WP's parenthetical "(monkeypatch plus a
  reload)" assumed `ollama_client` was importable as a top-level module from
  `tests/`. It is not (the package is a script-style dir added to sys.path
  only by the runner itself, and the benchmark's conftest puts a *different*
  `runner.py` — `utilities/benchmarker/lib/` — on sys.path, so a naive
  `sys.path.insert(0, tools/qwen_worker)` in the test file breaks collection
  of the whole suite: `from runner import Runner` in
  `utilities/tests/benchmark/test_benchmarker.py` would resolve to the
  harness's `runner.py`). Test loads the harness modules by explicit file
  path via `importlib` under unique aliases, no sys.path mutation. The plan
  §4 WP-00 requirement "the env vars override the ollama_client defaults"
  is satisfied exactly; this is a test-side import-mechanics correction, not
  a behaviour change. No plan citation conflict: WP-00's change list
  ("defaults come from the env vars QWEN_WORKER_BASE_URL and
  QWEN_WORKER_MODEL when set") is implemented as specified.

## Questions for the user

1. **vLLM subagent wiring (blocks WP-03, not WP-00).** The
   `local-vllm-subagent` skill documents driving `qwen3.8-27b` via
   `hermes chat -q "..." -m qwen-vllm --oneshot -Q` (vLLM OpenAI endpoint at
   http://192.168.1.23:8092/v1, 131072 ctx, qwen3_xml tool parser). But the
   playbook's WP loop uses `tools/qwen_worker/runner.py run <spec>` against
   Ollama's `/api/chat`. Two ways to "utilize the vllm subagents":
   (a) keep the Ollama runner exactly as the playbook specifies (Ollama
   `qwen3.8:27b` on :11434 works — preflight confirmed), and use the vLLM
   subagent (via `hermes chat -m qwen-vllm`) for *adjacent* work: reviewing
   staged files, drafting spec notes, docs (WP-26), summarizing transcripts
   after failures — free 27B tokens alongside the paid cloud model; or
   (b) add a vLLM/OpenAI-compatible transport to the harness (a
   `vllm_client.py` + `--transport openai` flag on the runner) so specs run
   against :8092 directly. (a) keeps the playbook's hard rule "never run two
   harness jobs at once" trivially and the §2.2 model fallback story intact;
   (b) is more invasive (WP-00 scope is the three harness files; adding a
   fourth file goes past the WP-00 change list). Recommendation: (a) for
   now, revisit (b) only if Ollama throughput becomes the bottleneck. Which
   way do you want it? Measured since: a trivial one-shot takes ~20 s, but
   an agentic task (WP-01, 4 small files) took ~7.5 h at ~3.6 tok/s, and
   its output needed a hand fix. Use it for background work only.

2. **Commit per WP?** Playbook §1.7: one conventional commit per WP, only if
   you say "commit per WP" in this session. Say the word and I'll commit
   WP-00 (stage only its files: the 3 harness files + the test file; the
   other dirty files in the tree — .env.example, send_test_message.ps1,
   services/campaign-manager/*, services/control-panel/* — predate this
   session and will be left out).

## Small local choices

- WP-00 (2026-09-26): `venv_python` returns a `Path` (not str) and
  `run_pytest` stringifies it; preflight reports the resolved path. The
  `FileNotFoundError` message names both candidate paths, satisfying
  "clear error".
- WP-00 (2026-09-26): `*.har` added to COPY_IGNORE globally (not scoped to
  deploy/), matching the WP's "no *.har in the sandbox" intent; currently
  exactly one .har exists in the repo (docs gogh capture), so no behaviour
  change for existing copies.
- WP-00 (2026-09-26): preflight's venv line prints "OK   venv python at
  <path>" (three spaces, not the old ".venv python at") — cosmetic, keeps
  the two-line OK/FAIL contract the gate checks.
