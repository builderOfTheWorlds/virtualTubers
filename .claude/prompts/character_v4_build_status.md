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
| | | | | | `python -m pytest -q --timeout 300` |

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
| WP-00 harness on Windows | todo | (by hand) | | | |
| WP-01 reader role | todo | | | | |
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

(One block per WP. Example: `T03.8 -> test_clock_saturday_is_old_week`)

## Measurements

- Stage 1 model calibration (WP-07): model | guard pass % | s/chapter | chosen
- Stage 2 speaker accuracy (WP-08):
- Recall thresholds after tuning (WP-23):

## Test corrections (each needs a plan citation)

## Questions for the user

## Small local choices
