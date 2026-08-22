# PLAN_v3 build — handoff

**Session:** 2026-08-21 → 2026-08-22. **Status: built, tested, running.**
Everything in `PLAN_v3.md` Parts A, B, C and the reduced Part D is implemented
and green. Nothing is committed — the whole build is in the working tree.

Read `utilities/3LayersWeeklyGeneration/PLAN_v3.md` first; it is the design of
record and was amended during this session (decisions V9–V16). This file covers
what actually got built, how it was built, and what is still open.

---

## Execution model — how this was built

Claude (Opus) was orchestrator and architect: it wrote each spec and each
pytest file FIRST, dispatched, then reviewed, fixed and verified.
**`qwen3.8:27b` on local Ollama wrote every implementation.** The specs and
tests are not documentation — they are the executable acceptance criteria that
make a 27B local model usable. Same discipline as `app/campaign/`, documented
in `docs/campaign_module_status.md`.

```bash
.venv/bin/python tools/qwen_worker/runner.py --model qwen3.8:27b preflight
.venv/bin/python tools/qwen_worker/runner.py --model qwen3.8:27b \
    run tools/qwen_worker/specs/<task>.yaml --attempts 3
# after reviewing .qwen_staging/<task_id>/ :
.venv/bin/python tools/qwen_worker/runner.py --model qwen3.8:27b \
    promote tools/qwen_worker/specs/<task>.yaml
```

### Dispatch record

| # | Spec | Target | Attempts | Notes |
|---|---|---|---|---|
| 1 | `3layers_generation_store` | `services/3layer-generator/generation_store.py` | 1 | clean |
| 2 | `3layers_pool` | `src/pool.py` | 1 (after 3 wasted on the thinking bug) | **not promoted** — hand-applied, see below |
| 3 | `3layers_segment_schema` | `src/segment_schema.py` | 3 | 2 spec defects fixed mid-flight |
| 4 | `3layers_plan_segment` | `src/plan_segment.py` | 1 (after 3 failed) | 3 model bugs → spec notes |
| 5 | `3layers_service_runner` | `services/3layer-generator/runner.py` | 1 (×3 rounds) | 2 spec ambiguities found by running the container |
| 6 | `3layers_service_api` | `services/3layer-generator/generator_api.py` | 1 (×4 rounds) | 3 rounds driven by real-container bugs |
| 7 | `3layers_dialogue` | `src/generate_segment_dialogue.py` | 3 | all failures were MY test defects |

**Roughly two thirds of all dispatch failures were defects in my own specs or
tests, not model incapacity.** That matches the ratio the campaign-module build
recorded. When a dispatch fails, suspect the spec first.

### Four harness findings worth keeping

1. **Disable thinking.** `qwen3.8:27b` returns its chain of thought in a
   separate `thinking` field that does not count as output but DOES consume
   `num_predict`. On a long spec it spent the entire budget reasoning and
   returned empty content three times — 21 minutes for nothing.
   `tools/qwen_worker/ollama_client.py` now sends `"think": false` (top level,
   not in `options`) with a fallback for models that reject the key. The same
   spec then passed in 132s. `num_ctx` 40960, `num_predict` 12288.

2. **Never promote a whole-file regeneration over existing reviewed code.** The
   harness regenerates the entire target file. For a NEW module that is right.
   For a CHANGE to a reviewed one it launders unrelated edits in behind a green
   test run: the `pool.py` output passed all 40 tests while reformatting every
   docstring and deleting load-bearing rationale, including the breaker's
   *"three failures out of three is 100% and is not evidence; aborting a
   two-day run on it would be worse than the bug."* For modifications, dispatch
   to get the implementation right, then hand-apply just the feature. The pool
   cancel hook landed as 53 insertions / 2 deletions instead of a 330-line
   rewrite.

3. **Unit tests with injected fakes cannot find wiring bugs.** Three separate
   bugs shipped through a fully green suite and were only caught by running the
   container: a `global CONFIG` omission, a `config` module/dict name collision,
   and a pack-name/pack-object conflation. Each was invisible because the tests
   substituted the very thing that was wrong. Every one now has a regression
   test — but the lesson is to run the real thing early.

4. **Watch for module-name collisions across service directories.** pytest puts
   every test directory on `sys.path`. `services/3layer-generator/tests/test_runner.py`
   shadowed `app/test_runner.py` and broke four unrelated suites at collection;
   `api.py` collided with `services/message-api/api.py`. Hence
   `test_service_runner.py` and `generator_api.py`.

---

## What was built

### Part A — Layer 2, recursive segmentation

Replaces the flat nine-chapters-per-segment split with a tree that recurses
only where content density demands it. A uniform-weight run reproduces the old
shape almost exactly (root 170 slots → 9 leaves of ~19), so nothing regressed.

- `src/segment_schema.py` — rewritten. New: `derive_target_slots`,
  `leaf_eligible`, `validate_tree_config`, `distribute_words`,
  `parse_children`, `validate_children`, `build_expand_prompt`,
  `build_leaf_prompt`; `merge_brief` now walks a tree; `validate_slots` gained
  an optional `node` for the per-leaf density check. Chapter-era names are gone
  with no shims.
- `src/plan_segment.py` — rewritten. Knob interlock at startup, node-granular
  resume from `tree.yaml`, one continuous `ThreadPoolExecutor` drain,
  forced-leaf degradation, per-node checkpointing, `needs_rebrief`, plus the
  new `progress` / `cancel_check` keyword args.
- `config/generation.yaml` — `segment.tree` block replaces
  `chapters_per_segment`; `dialogue.breaker` added.

**The two invariants to preserve:** children's `target_words` sum to the
parent's EXACTLY (a 3% per-level leak loses a fifth of the week by depth four),
and the tree always terminates by forcing a leaf rather than dropping a node.

### Part B — `services/3layer-generator/`

- `generation_store.py` — Postgres. `generation_jobs` + `generation_artifacts`,
  following `app/episode_store.py`'s shape. Every mutation touches only its own
  columns, which is what makes a progress write unable to clobber a concurrent
  cancel.
- `runner.py` — the dispatcher, written as callable functions (`boot`,
  `dispatch_once`, `run_forever`) rather than a bare thread so it is testable
  without sleeping. Boot reconciles orphaned `running` rows and rehydrates
  artifacts missing from disk.
- `generator_api.py` — FastAPI. `/healthz`, `/jobs` (POST/GET), `/jobs/{id}`,
  `/jobs/{id}/cancel`, `/preview`, `/config`.
- `Dockerfile`, `requirements.txt`, compose entries for `3layer-generator` and
  `generator-postgres`.
- DDL mirrored into `docs/sql/02_create_tables.sql`.

### Part C — Layer 3, dialogue

`src/generate_segment_dialogue.py` — the local `generate_take` (NOT
`batch_generate._generate_take`, which overwrites `loop` with the take number
and blanks `carry`), the pool wiring with one improviser per worker thread, and
the hard neutral-take guard.

### Cross-cutting

`pool.run_pool` gained a keyword-only `cancel_check`, polled on ONE dedicated
thread rather than per worker — in production it is a database query, and
per-worker polling would multiply it by `concurrency` for no added fidelity.

---

## Verification

```bash
docker compose up -d generator-postgres
# Export ONLY these four. Do NOT `set -a; . ./.env` — sourcing the whole file
# puts OLLAMA/model vars in the environment and breaks 18 unrelated tests.
export POSTGRES_HOST=127.0.0.1 POSTGRES_PORT=5455
export POSTGRES_DB=$(grep '^GENERATOR_POSTGRES_DB=' .env | cut -d= -f2-)
export POSTGRES_USER=$(grep '^GENERATOR_POSTGRES_USER=' .env | cut -d= -f2-)
export POSTGRES_PASSWORD=$(grep '^GENERATOR_POSTGRES_PASSWORD=' .env | cut -d= -f2-)

.venv/bin/python -m pytest -q --ignore=tests/test_campaign_ambient.py
```

**Current: 1627 passed, 13 failed.** All 13 failures are pre-existing
(issue #18) and were proven so by stashing every change and re-running. The
suites built this session are **532 tests, all passing**.

The service runs:

```bash
docker compose up -d 3layer-generator
curl -s localhost:8092/healthz                       # {"status":"ok"}
curl -s localhost:8092/config | head -c 80           # output.dir == /data/output
curl -s -X POST localhost:8092/jobs -H 'Content-Type: application/json' \
     -d '{"stage":"arc","profile":"light"}'
```

Verified end to end against the real container: job submitted over HTTP,
claimed by the dispatcher, artifact written to the bind mount AND mirrored into
Postgres, job row moved to a terminal status, cancel on an arc job returning
202 with the not-interruptible note.

---

## Open — read this before continuing

**Blocking a real generation run:**

- **#19 — Ollama is bound to loopback.** The host systemd unit pins
  `OLLAMA_HOST=127.0.0.1:11434`, so the container cannot reach it.
  `extra_hosts` makes the name resolve but nothing listens there. Rebinding
  exposes an unauthenticated GPU endpoint, so it was left as a deliberate
  operator decision. **This is the one thing standing between the service and a
  real run.**
- **#20 — a run that plans nothing still reports `completed`.** This is what
  made #19 hard to see. Each component behaves as specified; the emergent
  result is a green job with an empty arc plan. The runner should fail a job
  whose output is empty.

**Also open:**

- **#18 — `app/campaign/ambient.py` was never committed**, so 13
  `test_campaign_validator.py` failures and one collection error exist at HEAD.
  Predates this build. Its spec exists; one dispatch should fix it.
- **#21 — generated files are root-owned** on the bind mount.
- **N1 concurrency benchmark** (PLAN_v3 V5) is still manual and still
  outstanding. It sets `dialogue.concurrency`, `num_ctx`, and Ollama's
  `NUM_PARALLEL`. Do it before the first full dialogue run.
- **Deferred by V6/V7:** `docs/` pages per function, the `PLAN.md` rewrite, the
  D19 design-decisions entry, and Loki pushes.
- **Deferred by V9/V10/V12/V15:** replacing the file-write mirror with direct DB
  writes, normalized per-node artifact tables, the n8n watchdog workflow, and
  the `generator-postgres` → mafober backup mirror.
- `test_plan_segment.py` passes 3/3 in isolation but failed once inside the
  full suite — an ordering interaction worth a look.

**Nothing is committed.** The branch is `main`; `git status` shows the full
change set.
EOF
