# PLAN_v3 — Build the 3-layer generator as an API service

Supersedes `PLAN_v2.md` (which superseded PLAN.md's Layer 2/3 sections).

**Same three deliverables as v2, one change of shape:**

| Part | v2 | v3 |
|---|---|---|
| A — Layer 2 recursive tree | `src/segment_schema.py` + `src/plan_segment.py` rewrite | **Unchanged** (v2 Part A is the text of record, referenced inline below) |
| B — entry point | `main.py` CLI + `--test-mode` | **`services/3layer-generator/` FastAPI service** — the interface for the management GUI |
| C — Layer 3 dialogue | `src/generate_segment_dialogue.py` | **Unchanged**, plus it gains a `progress_cb` parameter the API consumes |
| D — cross-cutting | docs, log levels, Loki | **Reduced**: log levels only. Docs and Loki deferred (see Decisions) |

**Revised 2026-08-21** (V9–V14 below): generated artifacts and job state both land in
**Postgres**, not files; `pool.run_pool` gains a cancellation hook; orphaned jobs are
reconciled at boot. Implementation is dispatched to a local `qwen3.8:27b` (see
§Execution model).

---

## Locked decisions (this session, 2026-08-20)

| # | Decision | Rationale |
|---|---|---|
| V1 | **API, not CLI.** New FastAPI + uvicorn service under `services/`, same shape as `message-api` (own `api.py`, `requirements.txt`, `Dockerfile`, compose entry). | The project already standardized on FastAPI for every service. A GUI will drive this; exposing it as HTTP now avoids ripping out a CLI. |
| V2 | ~~File-based job state~~ — **SUPERSEDED by V9 on 2026-08-21.** Job records and generated artifacts both live in **Postgres**. | The original rationale ("let it just be logged to a file for now") was overtaken by the requirement that generated output be queryable by the management GUI. See V9–V11. |
| V3 | **Sequential job execution.** One dispatcher thread, oldest-first, one job at a time. Cancellation is cooperative (flag polled at unit boundaries). | Parallel jobs would compound on the shared Ollama pool (the real bottleneck anyway) and make the breaker / neutral-take guard / progress file ambiguous. The operator's lever is Ollama's own `NUM_PARALLEL`, not job count. |
| V4 | **Bind mounts** for `campaigns/` (ro), the utility's `config/` (ro) and `output/` (rw) into the container. | "It should be mounted in the local dir to preserve data after the container restarts." |
| V5 | **N1 concurrency benchmark stays manual** (v2 build-order step 4), run by the operator between B and C. | Not part of this build. The pool sizes are read from `generation.yaml` until it lands; Part C must not hard-code them. |
| V6 | **Doc hygiene deferred**: no `docs/` pages, no `PLAN.md` rewrite, no design-decisions D19 entry, no issues-tracker updates this build. | Kept out per session decision. The v2 "Docs to update" section moves to the follow-up. |
| V7 | **Loki deferred.** The `generation_jobs` row — `status`, `error`, `progress`, `heartbeat_at` — is the audit trail for this build. | Loki adds a second sink for what the job table already records queryably. Revisit once the GUI exists. |
| V8 | **The v2 CLI flags map to API surface** exactly as follows — no behavioral change, only transport: | |
|     | `--stage` → `POST /jobs {stage: arc\|segment\|dialogue\|all}` | |
|     | `--pack / --config` → service env (one pack per service for now; `POST /jobs` still accepts `pack` for future multi-pack) | |
|     | `--segments ID,…` → `POST /jobs {segments: [...]}` | |
|     | `--model-profile NAME` → `POST /jobs {profile: NAME}` + `config.validate_profile_for_stages` call (issue #10) | |
|     | `--test-mode` → `POST /jobs {test_mode: true, segments: [<one id>]}` | |
|     | `--rebrief` → `POST /jobs {rebrief: true}` (selects briefs with `needs_rebrief: true`) | |
|     | `--dry-run` (segment) → `POST /preview` (returns the tree that *would* be planned, no job row, no LLM) | |
|     | `--stingers` → `POST /jobs` with `stingers: true` rejected with 400 "requires events.yaml, not yet built" | |

## Locked decisions (revision session, 2026-08-21)

Five findings from the code review of this plan forced these. Each names the finding it settles.

| # | Decision | Rationale |
|---|---|---|
| V9 | **Postgres mirrors the filesystem; it does not replace it.** `plan_arc` / `plan_segment` / `generate_segment_dialogue` keep writing YAML to `out_path` exactly as today. After each artifact completes, the service reads the file and upserts it into `generation_artifacts`. Resume stays filesystem-based (`brief.yaml` existence). | Settles review finding 1 (container output path). A direct-to-DB rewrite would touch `plan_arc.py`, `plan_segment.py`, `config.py`'s path helpers and `worklist.py` plus their whole test suites — a large scope increase that also invalidates the v2 Part A spec already written. Mirroring keeps *Explicitly unchanged* true and the existing suite green. Full replacement → follow-up. |
| V10 | **Artifacts are one JSONB document per artifact**, unique on `(pack, kind, segment_id)`, `kind ∈ {arc_plan, brief, tree, dialogue}`. | Part A's node model is still in flux, and there is no migration framework in this repo — normalizing per-node tables now means migrating them after Part A lands. |
| V11 | **`pool.run_pool` gains a keyword-only `cancel_check=None`.** An admitted exception to *Explicitly unchanged*. | Settles review finding 3. Part C claimed cancellation used "the pool's existing `stop_event` path", but `stop_event` is created locally at `pool.py:230` and only the breaker can set it. Cancellation is otherwise unbuildable. |
| V12 | **n8n is a watchdog, not an orchestrator.** The in-process sequential runner (V3) keeps owning sequencing. n8n on mafober runs a cron workflow against `GET /jobs`, alerts on stale `heartbeat_at`, and may call `POST /jobs/{id}/cancel`. | Settles review finding 5. n8n is a workflow engine, not a process supervisor — it cannot terminate a thread inside the container, only drive the API. So the in-process hook (V11) is required regardless, and moving sequencing into a GUI-configured workflow would take it out of version control. |
| V13 | **`preview` requires an existing arc plan**, gated by `preview.require_arc_plan` (default `true`). | Settles review finding 7. Root `target_slots` comes from `arc_plan.yaml`; it cannot be derived from config alone. |
| V15 | **A dedicated LOCAL Postgres owns generation data**, not the shared mafober instance. New compose service `generator-postgres` on host port 5455 with its own named volume. Mirroring it to mafober for backup is a later, separate service. | Generation output is a distinct dataset with a distinct backup story from the live show's `messages` / `replay_episodes` / `container_logs`. Keeping it local also means a GPU-hours run does not depend on the LAN, and it keeps an unreviewed generated schema off the production database. The rest of the stack keeps pointing at mafober — unchanged. |
| V16 | **Storytelling model is `hermes3:70b`; the code model is `qwen3.8:27b`.** Unchanged and already configured — `hermes3:70b` is the `heavy` profile and the `active_model` for all three layers in `generation.yaml`. | Two different roles that are easy to conflate. hermes writes the show at runtime; qwen writes this service's implementation now. Every take already in `campaigns/ashiorid/generated/` carries `model: hermes3:70b`. |
| V14 | **Implementation is dispatched to local `qwen3.8:27b`** through `tools/qwen_worker/`, under the spec-and-tests-first discipline already used for `app/campaign/`. | The established execution model for this repo (`docs/campaign_module_status.md`). See §Execution model. |

---

## Part A — Layer 2 (recursive segmentation)

Unchanged from `PLAN_v2.md` §Part A (lines 22–438). The text of record there specifies:

- **Node model** (v2:88–104): single record shape for every node below the segment root; `node_id` scheme `f"{parent_id}-n{order}"` (v2:113–120) so `worklist.take_path` keeps working.
- **Recursion trigger** (v2:122–145): leaf when `target_slots <= max_leaf_slots`; branch otherwise, with weight-based distribution; forced leaves on `max_depth` or expand-exhaustion (decision 1: degrade, don't drop).
- **Config** (v2:147–183): `segment.tree: {max_leaf_slots, max_children, max_depth, min_node_words, leaf_density_floor}` + `validate_tree_config` knob interlock that raises.
- **`segment_schema.py`** (v2:185–253): function inventory with exact signatures and behaviors.
- **`plan_segment.py`** (v2:255–296): continuous work queue on one `ThreadPoolExecutor`, branch/leaf tasks, per-node checkpoints to `tree.yaml`, segment-level checks.
- **M1–M6** (v2:298–381): parallelism precondition, prompt-content recovery (legal lore, spine beat text, `target_words` stop condition), roll points, word-based density, `needs_rebrief`, single `tree.yaml` checkpoint.
- **Build workflow** (v2:383–406): tests first, then qwen-worker spec rewrite + regeneration with `qwen3.8:27b`.
- **Tests** (v2:408–438): the required new test bodies, and the untouched Layer 1 / worklist suite as the brief-contract oracle.

**Two additions for v3.** The real signature is `plan_segment(pack, arc_segment, config, llm, vocab, out_path)` — v3 amends it to:

```python
def plan_segment(pack, arc_segment, config, llm, vocab, out_path,
                 *, progress=None, cancel_check=None) -> Optional[Dict]:
```

- `progress(done: int, total: int, node: dict) -> None` — called after each node completes (branch or leaf). Note `total` **grows as branching resolves**: it is not known until the tree is fully expanded, so the GUI must tolerate an increasing denominator.
- `cancel_check() -> bool` — polled in the work-queue dispatch loop **between node submissions**, not from inside the callback. A `progress` callback that raises would be captured in its `ThreadPoolExecutor` future and never reach the caller; only a check on the dispatching thread can actually stop the run. When it returns `True`, stop submitting, let in-flight nodes drain, checkpoint what exists, and return the partial tree.

Both default to `None`, so every existing caller and test continues to work.

---

## Part B — `services/3layer-generator/` (new FastAPI service)

### Files

| File | Role |
|---|---|
| `services/3layer-generator/api.py` | FastAPI app, endpoints below |
| `services/3layer-generator/generation_store.py` | Postgres store: job rows + artifact documents. Replaces the v3-draft `job_store.py`. |
| `services/3layer-generator/runner.py` | Sequential dispatcher thread; executes one job at a time |
| `services/3layer-generator/requirements.txt` | `fastapi==0.141.1`, `starlette==1.6.0`, `uvicorn>=0.29`, `psycopg2-binary>=2.9`, `pyyaml>=6.0` |
| `services/3layer-generator/Dockerfile` | `python:3.12-slim`; COPYs the deps listed below; `CMD ["uvicorn","api:app","--host","0.0.0.0","--port","8000"]` |
| `docker-compose.yml` | new entry `3layer-generator` (spec below) |

### Store (`generation_store.py`)

Follows [`app/episode_store.py`](../../app/episode_store.py) exactly — module-level `CREATE_TABLE_SQL`, `available()`, `_connect()` with `connect_timeout=5` and `autocommit=True`, and `ensure_schema()` called once at app boot. The DB is the **external** Postgres on mafober (`192.168.1.120:5432`, per `PROJECT_CLAUDE.md`), the same instance `message-api` uses.

```sql
CREATE TABLE IF NOT EXISTS generation_jobs (
    id               TEXT PRIMARY KEY,
    pack             TEXT NOT NULL,
    stage            TEXT NOT NULL,
    profile          TEXT NOT NULL DEFAULT '',
    status           TEXT NOT NULL,          -- queued|running|completed|failed|cancelled
    params           JSONB NOT NULL DEFAULT '{}'::jsonb,
    progress         JSONB,
    result           JSONB,
    error            TEXT,
    cancel_requested BOOLEAN NOT NULL DEFAULT FALSE,
    submitted_by     TEXT NOT NULL DEFAULT 'api',
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at       TIMESTAMPTZ,
    finished_at      TIMESTAMPTZ,
    heartbeat_at     TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_generation_jobs_status_created
    ON generation_jobs (status, created_at);

CREATE TABLE IF NOT EXISTS generation_artifacts (
    id          BIGSERIAL PRIMARY KEY,
    pack        TEXT NOT NULL,
    kind        TEXT NOT NULL,               -- arc_plan|brief|tree|dialogue
    segment_id  TEXT NOT NULL DEFAULT '',    -- '' for arc_plan
    content     JSONB NOT NULL,
    job_id      TEXT,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (pack, kind, segment_id)
);
```

A job row, populated:

```json
{
  "id": "job_20260821T1432_0f2c9a", "pack": "ashiorid", "stage": "segment",
  "profile": "heavy", "status": "running",
  "params":   {"segments": ["seg-01"], "test_mode": false, "rebrief": false},
  "progress": {"done": 4, "total": 9, "last_node": "seg-01-n2", "updated_at": "…"},
  "result":   {"nodes": 9, "leaves": 8, "forced": 1, "slots": 171, "skipped": 1,
               "duration_s": 1104, "artifacts": ["brief:seg-01", "tree:seg-01"]},
  "error": null, "cancel_requested": false, "submitted_by": "api",
  "created_at": "…", "started_at": "…", "finished_at": null, "heartbeat_at": "…"
}
```

Functions: `ensure_schema()`, `submit(record) -> job_id`, `get(id)`, `list(pack=None, stage=None, status=None)`, `update_progress(id, progress)` (also sets `heartbeat_at = now()`), `finish(id, status, result=None, error=None)`, `request_cancel(id)`, `is_cancelled(id)`, `reconcile_orphans()`, `upsert_artifact(pack, kind, segment_id, content, job_id)`, `load_artifact(pack, kind, segment_id)`, `list_artifacts(pack)`.

**Cancellation is race-free by construction.** `request_cancel` is `UPDATE generation_jobs SET cancel_requested = TRUE WHERE id = %s AND status IN ('queued','running')`, and `update_progress` writes only the `progress` and `heartbeat_at` columns. Two column-level `UPDATE`s on one row cannot clobber each other, so the read-modify-write race the file-based draft had (read record → mutate dict → rewrite whole file) simply does not arise. No lock and no sentinel file.

**There is no migration framework in this repo** — `episode_store.py` says so in its own header. Both `CREATE TABLE` statements must be mirrored verbatim into [`docs/sql/02_create_tables.sql`](../../docs/sql/02_create_tables.sql). (`docs/database_schema.md` is the third sync target but falls under the V6 doc deferral — it is on the follow-up list.)

### Output path and the working directory

`output.dir` in `generation.yaml` is a **repo-relative host path** (`utilities/3LayersWeeklyGeneration/output`) and `config/` is mounted `:ro`, so it cannot be corrected in place. Left alone, `config.output_root()` resolves it against the container's CWD and artifacts land inside the image layer rather than on the mount.

Resolution: after `config.load_config()`, the service **overlays the value in memory** —

```python
cfg = config.load_config(os.environ["GENERATOR_CONFIG"])
cfg["output"]["dir"] = os.environ["OUTPUT_DIR"]        # /data/output
```

— before passing `cfg` to any layer function. No change to `config.py`, no write to the read-only mount, and `OUTPUT_DIR` finally has a consumer. The working directory stays on the V4 bind mount so it survives restarts; Postgres is the durable store, the filesystem is the layer functions' scratch.

**Rehydration.** At boot, after `reconcile_orphans()`, any artifact present in `generation_artifacts` but missing from the working directory is written back out as YAML. This is what keeps the filesystem-based resume rule (V9) correct on a fresh or wiped volume.

### Runner (`runner.py`)

**Boot sequence** (FastAPI `lifespan`, before the dispatcher thread starts):

1. `generation_store.ensure_schema()`
2. `generation_store.reconcile_orphans()` —
   `UPDATE generation_jobs SET status='failed', finished_at=now(), error='interrupted by service restart' WHERE status='running'`.
   A container restart mid-job otherwise leaves a row `running` forever: the dispatcher only ever picks up `queued`, so nothing would ever clear it and `GET /jobs?status=running` would lie to the GUI indefinitely. `queued` rows are deliberately left alone — the dispatcher picks them up normally.
3. Rehydrate any DB artifact missing from the working directory.

Then one thread. Loop:

```
while True:
    job = oldest job on disk with status == "queued"
    if none: sleep(1); continue
    mark running (started_at)
    try:
        execute(job)          # stage dispatch below
        mark completed (result, finished_at)
    except JobCancelled:
        mark cancelled
    except Exception as exc:
        mark failed (error=repr(exc), finished_at)
```

`execute(job)` — the stage dispatch. Each branch resolves the profile via `config.resolve_profile(config, layer, job.profile or None)` and builds one `concurrent_llm.from_profile(...)` client; the same client is passed down through the layer functions (they already take `llm`).

- **`stage: arc`** — `plan_arc(pack, config, llm, vocab, out_path=config.arc_plan_path(...))`.
- **`stage: segment`** — for each segment (from `job.params.segments`, or from `arc_plan.yaml` minus those with an existing `brief.yaml`, or from briefs marked `needs_rebrief` when `rebrief: true`), call `plan_segment(pack, seg, config, llm, vocab, out_path, progress=_progress_cb(job))`.
- **`stage: dialogue`** — `generate_segment_dialogue(pack, segment_ids, config, llm, out_root, progress=_progress_cb(job))` (Part C entry).
- **`stage: all`** — arc → segment → dialogue, sequentially in one job.

`_progress_cb(job)` wraps `generation_store.update_progress(job.id, {…})`, which also refreshes `heartbeat_at` so the n8n watchdog (V12) can tell a slow job from a wedged one.

After each artifact is written to disk, the runner reads it back and calls `generation_store.upsert_artifact(pack, kind, segment_id, content, job.id)` — this is the V9 mirror. `result.artifacts` records `kind:segment_id` keys, not filesystem paths.

#### Cancellation, per stage

The three stages have genuinely different interruption points; the draft only specified `dialogue`.

| Stage | Mechanism |
|---|---|
| `arc` | **Not interruptible.** `plan_arc` is a single LLM call with no hook. `POST /jobs/{id}/cancel` on an arc job returns **202** with `{"note": "arc stage is not interruptible; cancellation will be honored before the next stage"}`, sets the flag, and the runner honors it at the next stage boundary. |
| `segment` | `plan_segment(..., cancel_check=...)` polls in its dispatch loop between node submissions (Part A). In-flight nodes drain, the tree is checkpointed, the partial result is returned. |
| `dialogue` | `run_pool(..., cancel_check=...)` (V11) sets `stop_event` and drains, returning partial `PoolStats`. |
| `all` | The flag is checked at each stage boundary in addition to the per-stage mechanism above. |

A cancelled job ends `status=cancelled` with whatever `result` was accumulated — a cancel is a partial success, not a failure.

#### Process supervision (V12)

n8n on mafober runs a cron workflow that polls `GET /jobs?status=running`, alerts when `heartbeat_at` is older than a threshold, and may call `POST /jobs/{id}/cancel`. It does **not** dispatch jobs, own sequencing, or terminate anything in-container — it drives this API and nothing else. The workflow itself is not built in this repo; only the `heartbeat_at` column and the endpoints it depends on are.

### Endpoints (`api.py`)

All `def` (sync), same style as `message-api`.

| Endpoint | Behavior |
|---|---|
| `GET /healthz` | `{"status": "ok"}` |
| `POST /jobs` | Body: `{pack?, stage, profile?, segments?, dry_run?, test_mode?, rebrief?}`. Validates: `stage` known, `pack` on the ro mount, `profile` (if given) resolves for every stage in scope — `config.validate_profile_for_stages` (issue #10). For `test_mode: true`, require exactly one segment. For `stingers: true`, return 400 "requires events.yaml, not yet built". Creates job `status=queued`, returns `{id, status}`. |
| `GET /jobs?pack=&stage=&status=` | Job records, newest first. Metadata only — never scans generated output. |
| `GET /jobs/{id}` | Full record. 404 if absent. |
| `POST /jobs/{id}/cancel` | Sets `cancel_requested`. 404 not found; 409 already `completed`/`failed`/`cancelled`; **202 + note** on an arc-stage job (not interruptible — see above); 200 otherwise. Runner picks it up at the next unit boundary. |
| `POST /preview` | Body: `{pack?, stage: "segment", profile?, target_slots?}`. In-process: loads config + budget arithmetic, returns the tree shape that *would* be planned — root `target_slots`, `expected_children = ceil(root_slots / max_leaf_slots)` capped at `max_children`, `expected_max_depth = max_depth`, per-node `word` budget at each expected level. **No LLM call, no job row.** Root `target_slots` comes from `arc_plan.yaml`, so when it is absent: **409** `{"error": "arc plan not found for pack <p>; run stage=arc first"}` while `preview.require_arc_plan` is `true` (the default, V13). With that knob `false`, the body must supply `target_slots` or the call is a 400. 400 if config can't be loaded. |
| `GET /config?pack=` | The loaded `generation.yaml` dict with the resolved profile for the active layer. For GUI display only. 400 on bad pack. |

Error mapping: 400 bad body / validation, 404 job not found, 409 cancel on terminal job, 503 Ollama unreachable (only surfaces as `status=failed` + `error` in the job record; the HTTP response is already the job id at submit time).

### Dockerfile

`python:3.12-slim`. COPY in, in order: `app/llm_client.py`, `app/campaign/pack.py` + `app/campaign/improviser.py` + `app/campaign/batch_generate.py` (the three `app/` modules Part C imports), `utilities/3LayersWeeklyGeneration/src/*.py`, `services/3layer-generator/{api.py, generation_store.py, runner.py, requirements.txt}`. Install from `requirements.txt` (`fastapi==0.141.1 starlette==1.6.0 uvicorn>=0.29 psycopg2-binary>=2.9 pyyaml>=6.0`). Entrypoint `uvicorn api:app --host 0.0.0.0 --port 8000`.

**Copy everything flat into `/app`, as `message-api`'s Dockerfile does, and set no `PYTHONPATH` at all.** There are zero basename collisions between `app/` + `app/campaign/` and the utility's `src/` (verified 2026-08-21), so a flat layout makes `from config import ...`, `from llm_client import ...` and `from improviser import ...` all resolve with no path manipulation. The `sitecustomize` block the draft suggested is unnecessary and would be a runtime-only failure mode.

### Compose entry

```yaml
  3layer-generator:
    image: virtualtubers-3layer-generator:latest
    pull_policy: never
    environment:
      PACK_PATH: /data/packs/ashiorid
      OUTPUT_DIR: /data/output
      GENERATOR_CONFIG: /data/config/generation.yaml
      OLLAMA_BASE_URL: ${OLLAMA_BASE_URL:-http://host.docker.internal:11434}
      # V15 — the generator's OWN local database, not mafober.
      POSTGRES_HOST: generator-postgres
      POSTGRES_PORT: 5432
      POSTGRES_DB: ${GENERATOR_POSTGRES_DB:-generation}
      POSTGRES_USER: ${GENERATOR_POSTGRES_USER:-generation}
      POSTGRES_PASSWORD: ${GENERATOR_POSTGRES_PASSWORD:?GENERATOR_POSTGRES_PASSWORD must be set in .env}
    depends_on:
      - generator-postgres
    volumes:
      - ./campaigns:/data/packs:ro
      - ./utilities/3LayersWeeklyGeneration/output:/data/output
      - ./utilities/3LayersWeeklyGeneration/config:/data/config:ro
    ports:
      - "8092:8000"
    restart: unless-stopped
```

And the database it depends on:

```yaml
  generator-postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_DB: ${GENERATOR_POSTGRES_DB:-generation}
      POSTGRES_USER: ${GENERATOR_POSTGRES_USER:-generation}
      POSTGRES_PASSWORD: ${GENERATOR_POSTGRES_PASSWORD:?GENERATOR_POSTGRES_PASSWORD must be set in .env}
    ports:
      - "5455:5432"
    volumes:
      - generator-postgres-data:/var/lib/postgresql/data
    restart: unless-stopped
```

with `generator-postgres-data` added to the top-level `volumes:` block.

**This is deliberately NOT the bundled `postgres` service**, which is gated behind the `local-postgres` profile and which this deployment does not use — the rest of the stack points at mafober (`192.168.1.120:5432`) and keeps doing so. Port 5455 is exposed on the host so the test suite and `psql` can reach it directly.

`depends_on: [generator-postgres]` is real here, unlike for Ollama: Ollama is reached over the network and a dead one is a job-level failure (`status=failed` with a readable `error`), not a boot failure. `ensure_schema()` at boot is still best-effort — log and continue if the DB is not up yet, exactly as `message-api` treats `episode_store.ensure_schema()` — because `depends_on` waits for the container to start, not for Postgres to accept connections.

**Backup to mafober is a follow-up**, not this build: a small mirror service that periodically copies `generation_jobs` / `generation_artifacts` into the mafober instance.

### Tests — `services/3layer-generator/tests/`

`test_api.py` uses `fastapi.testclient.TestClient` against a **fake store object** injected in place of `generation_store` — the API tests must not need a live DB. `test_generation_store.py` covers the SQL and is skipped via `pytest.mark.skipif(not generation_store.available())`, matching how the repo already guards its other Postgres-backed modules. Fake `pack`, fake `llm` (a no-op `complete()`), fake `vocab` throughout.

- `POST /jobs` validates: unknown stage → 400; unknown profile → 400 with the `validate_profile_for_stages` message; `test_mode` without exactly one segment → 400; `stingers: true` → 400 with the "requires events.yaml" message.
- `POST /jobs` happy path → row `status=queued`, dispatcher picks it up and marks it `completed` with a `result` summary (fake layer functions).
- `GET /jobs` filters by `pack`/`stage`/`status`. `GET /jobs/{id}` 404 on missing.
- `POST /jobs/{id}/cancel` — 409 on a `completed` job; **202 + not-interruptible note on an arc-stage job**; happy path sets the flag and the dispatcher honors it → final status `cancelled` with a partial `result`.
- `POST /preview` returns `expected_children` / `expected_max_depth` / root `target_slots` from the fixture config, and **makes zero LLM calls** (assert the fake `llm.complete` was never invoked). **409 when no arc plan exists and `require_arc_plan` is true**; 400 when the knob is false and `target_slots` is absent.
- `GET /config` returns the loaded config with the resolved profile for the active layer.
- **The output overlay**: `cfg["output"]["dir"]` equals `OUTPUT_DIR` after service config load — the regression guard for review finding 1.

Store tests (`test_generation_store.py`):

- `reconcile_orphans()` flips an orphaned `running` row to `failed` and leaves `queued` rows untouched.
- **`request_cancel` concurrent with `update_progress` — the flag survives.** This is the finding-4 regression test; it is the whole reason the store is column-level.
- `upsert_artifact` is idempotent on `(pack, kind, segment_id)` and bumps `updated_at`.
- `finish()` sets `finished_at` and is a no-op on an already-terminal row.

## Part C — Layer 3 (dialogue)

Unchanged from `PLAN_v2.md` §Part C (lines 492–608): the local `generate_take` function (loop from brief, conditions via carry, `recent` reset, never raises), the `pool.run_pool` wiring with one `LLMImproviser` per worker thread, the `dialogue.breaker:` config block, the **hard** neutral-take guard (non-zero exit), spine-slot exclusion, and `tests/test_generate_segment_dialogue.py`.

**Two additions for v3.**

1. `generate_segment_dialogue(pack, segment_ids, config, llm, out_root, *, progress=None, cancel_check=None)`. `progress(done, total, segment_id)` feeds the job row; `cancel_check() -> bool` is passed straight through to `run_pool`.

2. **`pool.run_pool` gains a keyword-only `cancel_check=None` (V11).** The draft claimed this hook already existed; it does not. `stop_event` is created at `pool.py:230`, local to `run_pool`, and is set only by the circuit breaker (`pool.py:153`, `pool.py:308`) — nothing outside the function can reach it, and the public signature (`units, *, worker_factory, generate, writer, concurrency, max_attempts, breaker`) has no way to pass one in. The change:

   - Poll `cancel_check()` at the two existing `stop_event.is_set()` checkpoints (`pool.py:266`, `pool.py:277`) and in the worker loop (`pool.py:122`).
   - On `True`, `stop_event.set()` and **break** — reusing the breaker's existing drain path verbatim (`pool.py:308`). Do not raise.
   - Return partial `PoolStats`, so a cancelled run still reports what it wrote.
   - Default `None` means never cancel, so every existing caller and the whole `test_pool.py` suite are unaffected.

   Note this must **not** be routed through `generate` raising: `run_pool` treats an exception from `generate` as a failed attempt and retries it `max_attempts` times, which turns a cancel into a slow retry storm.

**Issue #14 (neutral-take guard) in the API context:** the guard failing is a `status=failed` job with `error: "neutral-take guard: slot <id> has no conditions-{} take"` — not a 5xx on the HTTP response, because by the time the guard runs the job has already been submitted and the operator is watching `GET /jobs/{id}`.

---

## Part D — cross-cutting (reduced)

Scope: **log-level pass only** on the new tree code (Part A) and the new service (Part B). The existing modules are already close; the new code must not regress it.

- TRACE on function entry/exit. DEBUG at every branch/leaf classification and around every LLM call. INFO on segment completion and job completion. ERROR inside every handler.
- Each job record on disk is the audit trail (V7). The dispatcher writes a human-readable `error` field from the exception message — not just a traceback.

Deferred to a follow-up (moved out of scope by V6/V7): `docs/` pages for the new functions, `PLAN.md` rewrite, design-decisions D19 entry, issues-tracker updates (#3/#6/#9/#10/#11 status), Loki pushes.

---

## Explicitly unchanged (from v2, still true)

`plan_arc.py`, `arc_schema.py`, `worklist.py`, `config.py`, `vocabulary.py`, `concurrent_llm.py` — no code changes.

**`pool.py` is the one admitted exception (V11):** it gains a keyword-only `cancel_check=None` parameter and three poll sites, as specified in Part C. Nothing else in it changes, and its default preserves every existing behavior.

`config.py` staying unchanged is what the in-memory `output.dir` overlay buys — see §Output path. `plan_segment()`'s existing signature is preserved (the new `progress` kwarg is keyword-only with default `None`, so every existing caller and test continues to work). New config surface: `segment.tree` (Part A) and `dialogue.breaker` (Part C) only. Nothing under `app/` is modified — Part C imports from it as it stands.

---

## Execution model (V14)

Same discipline as `app/campaign/`, documented in [`docs/campaign_module_status.md`](../../docs/campaign_module_status.md). **Claude (Opus) is orchestrator and architect: it writes the spec and the pytest file first, dispatches, then reviews, fixes and verifies. `qwen3.8:27b` on local Ollama writes the implementations.** The specs and tests are not documentation — they are the executable acceptance criteria that make a 27B local model usable.

```bash
.venv/bin/python tools/qwen_worker/runner.py --model qwen3.8:27b preflight
.venv/bin/python tools/qwen_worker/runner.py --model qwen3.8:27b run tools/qwen_worker/specs/<task>.yaml --attempts 3
# after human review of .qwen_staging/<task_id>/ :
.venv/bin/python tools/qwen_worker/runner.py --model qwen3.8:27b promote tools/qwen_worker/specs/<task>.yaml
```

The subcommand is positional; there is no `--spec` flag. Output stages to `.qwen_staging/<task_id>/` and enters the tree only on `promote`.

### The ratchet

| Finding on review | Action |
|---|---|
| **Behavioural gap** | Encode as a new test + a spec note, then regenerate. Never hand-patch behaviour. |
| **Hygiene** (unused imports, dead branches, whitespace) | Hand-patch. |
| **Prose / architecture** | Hand-write. qwen does not write this plan or the show. |

### Two harness findings from this build

**1. Disable thinking.** `qwen3.8:27b` returns its chain of thought in a separate
`thinking` field that does not count as output but *does* consume `num_predict`. On the
`3layers_pool` spec it spent the entire budget reasoning and returned an empty `content`
three times — 21 minutes for nothing. `tools/qwen_worker/ollama_client.py` now sends
`"think": false` (top-level, not in `options`) and falls back to omitting the key for
models that reject it. Same spec then passed in 132s. `num_ctx` 40960, `num_predict` 12288.

**2. Never promote a whole-file regeneration over existing reviewed code.** The harness
regenerates the entire target file. For a *new* module that is exactly right. For a
*change* to a reviewed one it launders unrelated edits in behind a green test run: the
`3layers_pool` output passed all 40 tests while silently reformatting every docstring and
deleting load-bearing rationale — including the breaker's "three failures out of three is
100% and is not evidence; aborting a two-day run on it would be worse than the bug".

So for modifications: dispatch to get the implementation *right*, then read the staged
diff and **hand-apply just the feature** to the original. The pool cancel hook went in as
53 insertions / 2 deletions instead of a 330-line rewrite. Generated code also tends to
leak spec instructions into comments ("ADDITIVE ONLY", "implement exactly this") — another
thing hand-application strips.

**3. Unit tests with injected fakes cannot find wiring bugs.** Three separate bugs shipped
through a fully green suite and were caught only by running the container: a `global CONFIG`
omission in the lifespan (endpoints saw None while the dispatcher worked), a `config`
module/dict name collision in `build_default_context`, and a pack-name/pack-object
conflation in the stage dispatch. Each was invisible because the tests substituted the very
thing that was wrong. All three now have regression tests, but the lesson is to build the
image and run a real job early, not at the end.

**4. Watch for module-name collisions across service directories.** pytest puts every test
directory on `sys.path`. `services/3layer-generator/tests/test_runner.py` shadowed
`app/test_runner.py` and broke four unrelated suites at collection; `api.py` collided with
`services/message-api/api.py` and bound the tests to the wrong module. Hence the names
`test_service_runner.py` and `generator_api.py`.

Review every dispatch against the known qwen failure modes in `campaign_module_status.md` §"Known qwen failure modes" — notably `except (SomeError, Exception)` compounds, missing `encoding="utf-8"`, omitted logging, and reading mutated state after the call that mutated it. **When a dispatch fails, suspect the spec first** — three of five historical dispatch failures were spec defects, not model incapacity.

### Dispatch queue

| Order | Spec | Target | Notes |
|---|---|---|---|
| 1 | `3layers_segment_schema.yaml` (rewrite) | `src/segment_schema.py` | Pure functions — the recursive node model, Part A. |
| 2 | `3layers_plan_segment.yaml` (rewrite) | `src/plan_segment.py` | Orchestrator + `progress` / `cancel_check`. |
| 3 | `3layers_pool_cancel.yaml` (new) | `src/pool.py` | V11 hook only. Small, surgical, but behavioural — so it goes through the harness, not a hand-patch. |
| 4 | `3layers_generation_store.yaml` (new) | `services/3layer-generator/generation_store.py` | SQL + psycopg2; `episode_store.py` is the context file. |
| 5 | `3layers_service_runner.yaml` (new) | `services/3layer-generator/runner.py` | Dispatcher, boot reconciliation, rehydration, per-stage cancel. |
| 6 | `3layers_service_api.yaml` (new) | `services/3layer-generator/api.py` | Endpoints + the `output.dir` overlay. |
| 7 | `3layers_dialogue.yaml` (new) | `src/generate_segment_dialogue.py` | Part C. After the manual N1 benchmark. |

Dockerfile, `requirements.txt`, the compose entry and `docs/sql/02_create_tables.sql` are hand-written — they are configuration, not implementation, and are too small to be worth a dispatch cycle.

---

## Build order

1. **A** — Layer 2 tree: `config/generation.yaml` block, hand-written tests, spec rewrites, qwen-worker regeneration (`--model qwen3.8:27b`), promote. `pytest -q` green at repo root; Layer 1 / worklist tests passing unmodified confirms the `brief.yaml` contract held.
2. **B** — `services/3layer-generator/`: `generation_store.py` (+ mirror the DDL into `docs/sql/02_create_tables.sql`), `runner.py`, `api.py`, tests, Dockerfile, compose entry. First point at which anything is runnable. Verify with `curl` against the running container.
3. **N1 benchmark (manual)** — run by the operator. Sets `dialogue.concurrency`, `num_ctx`, Ollama `NUM_PARALLEL`. Do this before C, not after.
4. **C** — Layer 3, verified on two real segments from the Part A output. Confirm manifest and resume behave under concurrency before any full run.
5. **D** — log-level pass, alongside 1–4 rather than after all of them.

---

## Verification

```bash
# Part A
.venv/bin/python -m pytest utilities/3LayersWeeklyGeneration/tests/test_segment_schema.py \
    utilities/3LayersWeeklyGeneration/tests/test_plan_segment.py -q

# Part B — needs the local generator database up (V15).
docker compose up -d generator-postgres
# Export ONLY these four. Do NOT `set -a; . ./.env` — sourcing the whole file
# puts OLLAMA/model vars into the environment and breaks 18 unrelated tests in
# tests/test_llm_client.py and tests/test_replay.py, which assert on defaults.
export POSTGRES_HOST=127.0.0.1 POSTGRES_PORT=5455
export POSTGRES_DB=$(grep '^GENERATOR_POSTGRES_DB=' .env | cut -d= -f2-)
export POSTGRES_USER=$(grep '^GENERATOR_POSTGRES_USER=' .env | cut -d= -f2-)
export POSTGRES_PASSWORD=$(grep '^GENERATOR_POSTGRES_PASSWORD=' .env | cut -d= -f2-)
.venv/bin/python -m pytest services/3layer-generator/tests/ -q

# Part C
.venv/bin/python -m pytest utilities/3LayersWeeklyGeneration/tests/test_generate_segment_dialogue.py -q

# Full regression — Layer 1 + worklist + everything else must stay green
.venv/bin/python -m pytest -q
```

Server-side concurrency must match the config or the pool just queues inside Ollama:
`OLLAMA_CONTEXT_LENGTH=8192 OLLAMA_NUM_PARALLEL=8`.
`segment.concurrency` must be `<= OLLAMA_NUM_PARALLEL`; `dialogue.concurrency` must equal it.

### API smoke test (Part B)

```bash
# Submit a segment-stage job for one segment
curl -s -X POST http://localhost:8092/jobs \
  -H 'Content-Type: application/json' \
  -d '{"stage":"segment","segments":["seg-01"],"profile":"heavy"}'

# Watch it
curl -s http://localhost:8092/jobs?status=running | python -m json.tool

# Cancel it (if it's still running)
curl -s -X POST http://localhost:8092/jobs/<id>/cancel

# Dry-run preview (no LLM)
curl -s -X POST http://localhost:8092/preview \
  -H 'Content-Type: application/json' -d '{"stage":"segment"}' | python -m json.tool
```

---

## Build outcome

Built, tested and running as of 2026-08-22 — see
[`.claude/prompts/three_layers_v3_build_handoff.md`](../../.claude/prompts/three_layers_v3_build_handoff.md)
for the dispatch record, the verification commands, and the open issues (#18–#21).
532 new tests, all passing; the full suite sits at its pre-existing baseline.

---

## Follow-up (after this build, separate task)

- N1 concurrency benchmark (manual).
- `docs/` pages for every new/changed function (v2's CLAUDE.md template).
- `PLAN.md` rewrite: Layer 2/3 sections, config block, file-tree, tests bullets, budget derivation.
- `three_layers_design_decisions.md`: D19 (recursive segmentation, `roll_at` resolution, local take function).
- `three_layers_generation_issues.md`: #3/#6/#9/#10/#11 status.
- Loki pushes (`app: three_layers`, `op: plan_segment` / `generate_dialogue`).
- GUI endpoints for the management panel (SSE progress stream, batch job cancellation, per-segment artifact browser).
- **Replace the file writes with direct DB writes** (retires the V9 mirror): `plan_arc`/`plan_segment`/`generate_segment_dialogue` write to `generation_artifacts` instead of `out_path`, and resume queries the DB instead of stat-ing `brief.yaml`. Touches `config.py`'s path helpers and `worklist.py`.
- **Normalized per-node artifact tables** (retires V10), once Part A's node model has settled.
- **The n8n watchdog workflow itself** (V12) — cron, stale-heartbeat alert, cancel action.
- `docs/database_schema.md` entry for `generation_jobs` and `generation_artifacts` (the third sync target alongside the store module and `docs/sql/02_create_tables.sql`).
- **Mirror service: `generator-postgres` → mafober** (V15). Periodic copy of both tables to the shared instance for backup. Needs a decision on direction-of-truth (the local DB stays authoritative) and on whether it ships as a cron container or an n8n workflow alongside the V12 watchdog.
