# Character v4: Build Playbook for the Local Model

Who reads this: the build orchestrator, **qwen3.8:27b**. It runs as a Hermes
session on the gx10 Ollama, and it builds plan
`character_generator_updater_v4.md` one work package (WP) at a time.
Decisions are in `character_v4_decisions.md` (D-xx). The build tracker is
`.claude/prompts/character_v4_build_status.md`.

You are building from a finished design. **Don't redesign.** If the plan and
the code disagree, or the plan is silent, stop and follow §2.5. Don't guess.

---

## 1. Ground rules

1. **One WP at a time, in order.** Don't start WP n+1 until WP n's gate
   passes and the tracker is updated. The phase gates in plan §13 are hard
   stops.
2. **Tests first, then code.** Each WP has a numbered **test list** (§4). You
   turn it into a pytest file, check the file against the list line by line,
   run it once to see it fail for the right reason (ImportError or
   AssertionError, not a SyntaxError), and then **freeze** it. After that, the
   code is changed to fit the tests. The tests don't change to fit the code.
3. **Code is written by the harness, not by hand.** Every module in a WP goes
   through `tools/qwen_worker/runner.py run <spec>`: whole files, a sandboxed
   pytest, and failure feedback fed back in. You write the spec YAML; the
   harness writes the module. After review, you `promote`.
4. **What you may write by hand:**
   - test files, spec YAMLs and fixtures
   - docs (`docs/*.md`) and config (`config/character.yaml`)
   - SQL copied from the reference files
   - hygiene fixes on promoted code: unused imports, typos, logging lines.
     This follows `docs/campaign_module_status.md:69-73`.

   **Behaviour gaps are never hand-patched.** Add a test and a spec note,
   then re-run the harness.
5. **Hands off `app/campaign/`, `app/agent.py`, `app/message_bus.py` and
   `sourceworks/*.py`.** The only existing files you may change are listed
   below. Changing any other file is out of scope; stop and ask.
   - `services/message-logger/logger.py`, `services/message-logger/Dockerfile` (WP-17)
   - `docs/sql/02_create_tables.sql`, `docs/message_logger.md` (WP-17)
   - `deploy/character-profile-db/{docker-compose.yml,.env.example,README.md}` (WP-01, WP-02)
   - `docker-compose.yml` (WP-06, WP-16, WP-24: new services only)
   - `requirements.txt` (WP-03, adds `tzdata` only)
   - `tools/qwen_worker/{runner.py,sandbox.py,ollama_client.py}` (WP-00)
   - `pytest.ini` (WP-03, adds `tests/character` if needed; `tests` already covers it)
   - `README.md`, `CHANGELOG.md`, `docs/project_structure.md` (WP-26)
6. **Never:**
   - print or commit secrets, or open `.env` files
   - run `redeploy.sh`, deploy, or touch mafober
   - run anything against the production `messages` table (192.168.1.120:5432)
     or the production `character_profile`

   Integration tests use pgserver or `CHARACTER_TEST_DSN`, and nothing else.
7. **Git:** don't commit unless the user has said "commit per WP" in this
   build session. If they have, make one commit per WP, conventional format,
   e.g. `feat(character): WP-05 stores`, and stage only that WP's files (use
   the `scoped-git-commits` skill).
8. **Logging** follows CLAUDE.md: a module-level `log = logging.getLogger(__name__)`,
   DEBUG at branches and before and after I/O, INFO when a job completes,
   ERROR in every except block. Never log DSNs or passwords. Jobs call
   `loki_push` best-effort (copy `_push_loki` from `tools/qwen_worker/runner.py:39-54`).

## 2. The loop for one WP

```
0. Read the WP (§4), the plan sections it names, and its context files.
1. Write tests/character/test_<module>.py from the test list.
   - Unit tests: fakes from tests/character/fakes.py, no network, no DB.
   - Integration tests: @pytest.mark.integration and the `pg` fixture.
2. Check: for each numbered list item, is there a test named for it?
   Record the mapping (item -> test name) in the tracker.
3. Run: .venv/<bin>/python -m pytest tests/character/test_<module>.py -q
   Expect failures from ImportError or AttributeError only.
4. Write tools/qwen_worker/specs/character_<wp>_<module>.yaml (§3).
5. Run: python tools/qwen_worker/runner.py --model qwen3.8:27b \
        --base-url http://192.168.1.23:11434 run <spec> --attempts 3
6. PASS  -> review the staged files against the §2.3 checklist -> promote
          -> run the WP gate command -> update the tracker -> next WP.
   FAIL  -> §2.4.
```

### 2.1 Where to run

- **Dev PC (Windows, x86_64):** the harness and the tests. pgserver works
  here. After WP-00, use `.venv/Scripts/python`.
- **gx10 (aarch64, `secus@192.168.1.23`, repo `~/codeProjects/virtualTubers`):**
  Ollama lives here. pgserver has **no aarch64 wheel**, so on gx10 set
  `CHARACTER_TEST_DSN` to the test instance (OP-5) or integration tests skip.
- Either host is fine for the harness, as long as integration tests really
  run **somewhere** before the phase gate. A skip is not a pass. The tracker
  records where each gate ran.

### 2.2 Model and context budget

- Worker: `--model qwen3.8:27b`. `ollama_client.chat` already sends
  `think: false` (`tools/qwen_worker/ollama_client.py:39-53`). If a module
  fails 2 full runs on qwen3.8:27b, try one run with
  `--model qwen3-coder:30b` (installed on gx10) before splitting the WP.
  Record which model passed.
- The prompt is the goal, interface, notes, tests and context files. Keep it
  under about 60k characters:
  - at most 2 target files per spec
  - at most 3 context files, preferring small ones
  - no test file over about 400 lines

  If a WP lists more targets, split it into several specs run in order. The
  later specs list the earlier promoted modules as context.
- Every spec runs on one shared Ollama. **Never run two harness jobs at
  once**: that is how the old phase 2 runs timed each other out.

### 2.3 Review checklist before promote

- [ ] The staged file implements the spec's interface exactly: names,
      signatures, return types.
- [ ] No imports outside the stdlib, the listed packages (psycopg2, kafka,
      yaml, httpx) and `character.*`.
- [ ] No hardcoded host, port, password or model. Everything comes from
      `config.py`.
- [ ] SQL uses parameters (`%s` or `%(name)s`), never f-strings with values.
      Identifiers (partition names) go through `psycopg2.sql.Identifier`.
- [ ] Every UPDATE touches only its own columns (the rule in
      `specs/3layers_generation_store.yaml`).
- [ ] Logging is present at the levels in §1.8, with no secrets.
- [ ] `--dry-run` paths write nothing. There is a test for this in every job
      WP.
- [ ] The full suite is still green: `python -m pytest -q -x --timeout 300`.
      Compare against the baseline recorded in WP-00.

### 2.4 When the harness fails

1. Read the last transcript: `.qwen_staging/<task>/_transcripts/attempt-03.json`.
2. Decide which kind of failure it is:
   - **The spec is unclear:** add a note to the spec and re-run.
   - **The test is wrong:** the test contradicts the plan. Fix the test, log
     it in the tracker under "test corrections", and re-run. This is the only
     time a frozen test changes, and it needs a plan citation.
   - **The module is too big:** split it into two specs, e.g. pure logic
     first, then the DB layer.
   - **The model can't do it:** 2 runs on qwen3.8:27b and 1 on
     qwen3-coder:30b have all failed. Mark the WP `BLOCKED` in the tracker
     with the failing test names, then stop and ask the user. Don't
     hand-write the behaviour.
3. Keep count: at most 4 harness runs per spec before you escalate.

### 2.5 When the plan is silent or wrong

- **A small gap with an obvious local answer** (a variable name, a log
  message, an argument order): choose, and note it in the tracker.
- **Anything that changes a table, a bus contract, a config key, a CLI flag,
  an exit code or a file location:** stop. Add a "question" entry to the
  tracker, with the options and your recommendation, and ask the user. Later
  WPs that don't depend on the answer may continue.
- **A plan claim about existing code turns out false:** don't work around it.
  Record the `path:line` and what is actually there, and ask.

## 3. Spec template

Copy the shape of `tools/qwen_worker/specs/3layers_generation_store.yaml`.
The `goal` explains *why*, including the one failure the module must never
have. The `interface` lists every exported name with its exact signature and
behaviour.

```yaml
task_id: character_wp05_stores_events
goal: |
  <2-6 paragraphs: what the module is, who calls it, the rule that must not be
  broken and why (quote the plan section)>
target_files:
  - app/character/store/events.py
test_files:
  - tests/character/test_store_events.py
context_files:
  - app/character/db.py          # promoted in WP-04
  - app/character/clock.py
interface: |
  <every public name, signature, behaviour, error cases>
notes: |
  <imports allowed; logging; SQL rules; what NOT to do>
```

**Imports in `app/character/` modules:** use `from character.clock import …`
(D-23). `app/` is on `sys.path` in tests (`tests/conftest.py:5`), and entry
points insert it themselves.

## 4. Work packages

Format: **targets** (new files unless marked), **tests**, **context** (read
only), **test list**, **gate**. Test IDs are `T<wp>.<n>`. Fakes live in
`tests/character/fakes.py`, which is created in WP-03 and extended as needed.

### Phase 0: operator steps (the user does these; you prepare the commands)

| Step | What | Done when |
|---|---|---|
| OP-1 | Stop the stale phase-2 runs on the dev PC: `tasklist` for python PIDs running `phase2_llm_windowing_ollama.py`, then stop them. **Ask the user first.** | `tasklist` shows none |
| OP-2 | Deploy `deploy/character-profile-db` on CT 101 per its README. Open 5433 from the gx10 and dev PC subnets. After WP-01, set `CHARACTER_READER_PASSWORD` and re-run `init` | `install.sh --verify` passes; `psql -h 192.168.1.120 -p 5433` connects |
| OP-3 | Kafka: check the broker's disk. If there is room, set `retention.ms=1209600000` on `vtuber.messages` | the value is recorded in the tracker |
| OP-4 | After WP-02, install `backup.sh` on mafober and run `--restore-drill` | the drill exits 0 |
| OP-5 | Optional gx10 test DB (plan §6) | `CHARACTER_TEST_DSN` works from gx10 |

### Phase 1: foundation

#### WP-00 Harness on Windows; baseline

- **Targets (existing):** `tools/qwen_worker/sandbox.py`, `tools/qwen_worker/runner.py`,
  `tools/qwen_worker/ollama_client.py`
- **Tests:** `tests/test_qwen_worker_harness.py` (new)
- **Why:** `runner.py:113` and `sandbox.py:116` hardcode `.venv/bin/python`,
  which doesn't exist on Windows (`.venv/Scripts/python.exe`). The defaults
  point at `localhost` and `qwen3-coder:30b`. The sandbox doesn't copy
  `deploy/`.
- **Changes:**
  - `sandbox.venv_python(repo_root)` returns the first of these that exists:
    `.venv/bin/python`, `.venv/Scripts/python.exe`. Use it in both places.
  - Add `"deploy"` to `SANDBOX_DIRS` and `"*.har"` to `COPY_IGNORE`.
  - `ollama_client` defaults come from the env vars `QWEN_WORKER_BASE_URL`
    and `QWEN_WORKER_MODEL` when set.
- **Special case:** this WP changes the harness itself, so it is done **by
  hand**, not through the runner. Keep it small.
- **Test list:**
  1. `venv_python` finds `bin/python` when it exists (tmp_path).
  2. `venv_python` finds `Scripts/python.exe` when only that exists.
  3. `venv_python` raises a clear error when neither exists.
  4. `build_sandbox` copies `deploy/` and skips `*.har`.
  5. The env vars override the `ollama_client` defaults (monkeypatch plus a reload).
- **Gate:**
  - `runner.py preflight` prints OK twice.
  - Run the full suite once and record pass/fail/error counts in the
    tracker. That is the baseline, and the handover recorded 2551/42/36.
    Later "still green" checks compare against it.

#### WP-01 Reader role in the deploy package

- **Targets (existing):** `deploy/character-profile-db/docker-compose.yml`
  (`init` service), `.env.example`, `README.md`, `tests/run_tests.sh`
- **Context:** plan §6 (roles), D-05, `deploy/character-profile-db/README.md`
- **Test list:**
  1. `init` creates `character_reader` with LOGIN when `CHARACTER_READER_PASSWORD`
     is set, and skips it when empty. Check with `docker compose config`,
     looking at the init command text.
  2. Re-running is idempotent (`DO $$ … IF NOT EXISTS … $$`).
  3. `.env.example` documents the variable, with no value.
  4. The existing 51 `run_tests.sh` checks still pass.
- **Gate:** `bash deploy/character-profile-db/tests/run_tests.sh` passes, and
  `pytest tests/test_character_profile_db_scripts.py` passes.

#### WP-02 `backup.sh`

- **Targets:** `deploy/character-profile-db/scripts/backup.sh`; extend
  `tests/run_tests.sh` and `tests/mockbin/` (add a mock `docker` if needed; `pct` exists)
- **Context:** plan §5.1, D-12, `scripts/install.sh` (copy its logging and
  root-check style)
- **Test list** (bash, mocked `pct`, `zfs`, `docker` and `date`):
  1. `--dry-run` prints the dump, snapshot and prune commands, and writes
     nothing.
  2. A normal run calls `pct exec 101 -- docker exec … pg_dump -U postgres -Fc character_profile`
     and writes to `…/daily/character_profile_<stamp>.dump`.
  3. On a Sunday, it also copies to `weekly/`.
  4. Pruning keeps the newest 14 daily and 8 weekly files, and 14 `@auto-`
     snapshots.
  5. Pruning ignores files and snapshots that don't match the name pattern.
  6. A failed `pg_dump` exits non-zero and writes a `backup_runs` row with
     `status=failed`.
  7. `--restore-drill` creates, compares and drops `character_profile_drill`,
     and a count mismatch exits 1.
  8. Not running as root exits 1 with a message.
- **Gate:** `run_tests.sh` passes, and the README has a Backups section.

#### WP-03 Config, clock, shapes, fakes

- **Targets:** `app/character/__init__.py`, `app/character/config.py`,
  `app/character/clock.py`, `app/character/shapes.py`, `config/character.yaml`
  (by hand, copied from plan §11), `tests/character/fakes.py` (by hand),
  `tests/character/conftest.py` (by hand: the `pg` fixture), and one line in
  `requirements.txt`: `tzdata==2026.4`. Also create `requirements-dev.txt`
  with `pgserver==0.1.4; platform_machine != "aarch64"`.
- **Specs:** 2 (config+shapes; clock).
- **Context:** plan §11, D-01, D-24, D-26, and `.claude/prompts/character_v4_plan_validation.py:69-112`,
  which holds the clock reference implementation and cases.
- **Interfaces:**
  - `config.load(path=None, env=os.environ) -> CharacterConfig`. The config
    is a frozen dataclass tree. `CHARACTER_DB_HOST/PORT/NAME/USER/PASSWORD`
    override `db`, and `CHARACTER_INGEST_DB_*` fall back to the main values.
    The password comes only from the environment. Raises
    `ConfigError(key, reason)`.
  - `clock.LoopClock(epoch: date, tz: str)`:
    - `.position(ts) -> (week, day)`
    - `.week_bounds(week) -> (start, end)`, both tz-aware
    - `.day_bounds(day) -> (start, end)`
    - `.previous_day(now)`
    - `.is_week_start(now)`

    A naive datetime raises ValueError.
  - `shapes.validate(obj, shape) -> list[str]`, the error list. A shape is a
    small dict DSL: `{"type": "object", "required": [...], "properties": {...}}`,
    covering str, int, float, bool, list-of and enum.
  - `pg` fixture (conftest): use `CHARACTER_TEST_DSN` if set; else start
    pgserver in a session tmp dir; else skip. It yields a psycopg2 DSN
    string, and each test gets a fresh database, created from a template.
- **Test list:**
  1. `load()` of the repo `config/character.yaml` succeeds.
  2. An epoch that isn't a Sunday gives a ConfigError naming `loop.epoch`.
  3. Weights that don't sum to 1 (±1e-6) give a ConfigError.
  4. `unease >= surface` gives a ConfigError.
  5. An env var overrides `db.host`. The password is never in `repr(config)`.
  6. A missing password is fine at load time; `db.connect()` fails later.
  7. The ingest DSN falls back to the main values.
  8. Clock: Saturday 23:59:59 NY is week 1, and Sunday 00:00 NY is week 2.
  9. Clock: 02:00Z on a Sunday is still Saturday in NY.
  10. Clock: the DST-end week is 169 h and the DST-start week is 167 h.
  11. Clock: `week_bounds` and `position` round-trip for 59 weeks.
  12. Clock: a naive datetime raises ValueError.
  13. Clock: `previous_day` at 00:00:30 NY Sunday is Saturday.
  14. Shapes: missing required fields, wrong types, bad enum values and
      nested list items each give one error with its path.
- **Gate:** the tests pass, and the full suite is not worse than the baseline.

#### WP-04 DB and migrations

- **Targets:** `app/character/db.py`, `app/character/sql/001_init.sql`
  (copied by hand from `docs/charcterProfileGenerationNotes/v4_reference_character_profile.sql`)
- **Context:** plan §6, D-25, `app/episode_store.py` (for connection style)
- **Interface:**
  - `connect(cfg, role="main") -> connection`: autocommit off,
    `connect_timeout=5`, `application_name='character-<role>'`.
  - `transaction(conn)`: a context manager that commits, or rolls back on an
    exception.
  - `migrate(conn, sql_dir=None) -> list[str]` applies the numbered files not
    yet in `schema_migrations`. It raises `MigrationError` if the sha256 of
    an applied file has changed.
  - `advisory_lock(conn, key: str) -> bool`, using `pg_try_advisory_lock(hashtext(key))`.
- **Test list (integration):**
  1. `migrate` on an empty DB applies `001` and creates all 23 tables plus
     `schema_migrations`.
  2. A second `migrate` applies nothing.
  3. A changed file checksum raises MigrationError.
  4. `advisory_lock` succeeds for the first session and fails for a second
     one on the same key.
  5. `transaction` rolls back when the block raises.
  6. The `vector` extension is present.
  7. When the `character_reader` role exists before migrate, it can SELECT
     but not INSERT.
- **Gate:** tests pass on pgserver, or on `CHARACTER_TEST_DSN`.

#### WP-05 Stores

- **Targets** (one spec each): `app/character/store/characters.py`
  (characters, agents, baselines, backstories, active version),
  `store/weeks.py` (loop_weeks and reset_steps), `store/events.py`
  (experience_events, ingest_status), `store/knowledge.py` (daily_summaries,
  week nodes and edges, archive), `store/fragments.py` (fragments, lead-up,
  links, unlocks, recalls), `store/jobs.py` (character_jobs, character_artifacts)
- **Context:** `001_init.sql`, `db.py`, `specs/3layers_generation_store.yaml`
  (the update-own-columns rule)
- **Test list (integration, per store):**
  - characters:
    1. upsert is idempotent
    2. `set_active_baseline(slug, v)` fails for a version that doesn't exist
    3. `agents_map()` returns `{agent_id: character_id}`
  - weeks:
    4. `ensure_week(campaign, week, clock)` is idempotent
    5. `run_step(conn, campaign, week, step, fn)` locks the row with FOR
       UPDATE, skips a completed step, and records `completed_at`
    6. a raising `fn` leaves the step unrecorded
  - events:
    7. `insert_many` ignores duplicate `(message_id, character_id)` rows and
       returns the inserted count
    8. `events_for_day(character, day)` is ordered by ts
    9. `update_ingest_status` touches only its own columns
  - knowledge:
    10. `archive_week(W)` sets `archived_at_week` on nodes and edges of
        week ≤ W that aren't yet archived, for every character
    11. `current_nodes(character, week)` excludes archived ones
    12. `unarchive_week(W)` reverses 10
  - fragments:
    13. `create_fragment` with its lead-up and links is one transaction
    14. an UPDATE or DELETE without the test flag raises
    15. `dormant_for(character)` excludes unlocked fragments
    16. `unlock(fragment, week, recall_id)` is idempotent
    17. `test_delete` works inside `allow_test_mutation`
  - jobs:
    18. `start_job` / `finish_job` / `fail_job`
    19. `list_recent(n)`
- **Gate:** tests pass; the review checklist passes (update-own-columns).

#### WP-06 Jobs framework, CLI, container

- **Targets:** `app/character/jobs.py` (the framework: context, lock, exit
  codes, dry-run, jobs row, `--at`), `services/character-updater/main.py`,
  `services/character-updater/Dockerfile`, `services/character-updater/requirements.txt`,
  and the `docker-compose.yml` services `character-jobs` (profile `jobs`,
  `restart: "no"`) and `character-ingest` (added in WP-16)
- **Context:** plan §5 (the job table and exit codes), D-13,
  `services/message-logger/Dockerfile` (image style)
- **Interface:**
  - `Job` protocol: `name`, `run(ctx) -> JobResult`.
  - `JobContext` holds cfg, conn, clock, now, dry_run, log and args.
  - `run_job(job, argv) -> int` runs the job under its lock and writes the
    `character_jobs` row. Exit codes: 0 done, 1 failed, 2 nothing to do or
    locked, 3 precondition not met.
  - `main.py` registers every job. This WP ships only the `status` job
    (week, reset steps, ingest lag, last 10 jobs, fragment counts). Later
    WPs register more.
- **Test list:**
  1. An unknown job exits with argparse code 2 and usage text.
  2. `--at` sets `ctx.now`, and a naive `--at` is rejected.
  3. `--dry-run` writes no jobs row.
  4. Lock contention exits 2.
  5. A job that raises exits 1, writes a `failed` row with the error text,
     and logs at ERROR.
  6. `status` against a migrated empty DB prints week 0 or "no weeks" and
     exits 0.
  7. `main.py` works from any cwd (the sys.path insert).
- **Gate:** `python services/character-updater/main.py status --at 2026-10-05T12:00:00-04:00`
  works against pgserver. `docker compose config` is valid.
- **Phase 1 gate:** everything above, plus the full suite no worse than the
  baseline.

### Phase 2: source and generator

#### WP-07 Stage 1: clean

- **Targets:** `utilities/source_pipeline/src/cleaner.py` (pure: windows,
  guard, contractions, audit), `utilities/source_pipeline/clean.py` (the CLI:
  lock, Ollama calls, report)
- **Tests:** `utilities/source_pipeline/tests/test_cleaner.py`
- **Context:** plan §1 and §1.1, D-17, `utilities/source_pipeline/README.md`,
  `sourceworks/phase1_deterministic.py:16-66` (the table to reduce)
- **Test list:**
  1. `split_cores(words, core=350, ctx=60)`: joining the cores equals the
     input exactly. The last core may be short.
  2. Each window's left and right context comes from its neighbours, and
     nothing outside the core is returned.
  3. `guard(original, restored)` passes when only punctuation, case,
     quotes, newlines or apostrophes change (`well` → `we'll`).
  4. `guard` fails on an added, removed or reordered word.
  5. The contraction table has none of `ill well shell hell id wed shed lets its were`.
  6. "rather ill but happy" is unchanged by the deterministic pass.
  7. `dont` becomes `don't`, and `Harrys` stays (it isn't in the table).
  8. A timeout or guard failure keeps the deterministic core and records the
     reason.
  9. The audit counts repeated 20-grams. On a synthetic text with a
     duplicated overlap, the check fails.
  10. The audit fails when the word counts differ.
  11. The CLI refuses to start while another run holds the lock.
  12. The CLI skips chapters whose sha256 is already in the report, unless
      `--force` is given.
  13. `--books 1` selects only book 1 from the manifest.
- **Operator gate (after promote, on gx10, one run at a time):**
  - Calibrate models on `1_001` and `1_006` with `--model` for each of
    `llama3.1:8b`, `gemma4:12b-it-q4_K_M` and `qwen3.8:27b`. Record the guard
    pass rate and time per chapter in the tracker, then choose `clean.model`
    (D-15 note).
  - Run book 1.
  - Every chapter's audit must pass. Spot-check 20 lines of `1_006` for
    quotes.

#### WP-08 Stage 2: tag

- **Targets:** `utilities/source_pipeline/src/tagger.py`, `utilities/source_pipeline/tag.py`
- **Context:** plan §1.2, `sources/harry_potter.yaml`
- **Test list:**
  1. `"Hello," said Harry.` gives speaker harry, method rule.
  2. `Harry said, "Hello."` gives harry.
  3. An alias (`Mr. Potter`) maps to harry.
  4. The inversion `said Ron` works.
  5. A quote with no attribution gives method unknown, or llm when an LLM is
     injected.
  6. The LLM reply must match the `{index, speaker}` shape; a bad reply goes
     to unknown.
  7. Scenes split on a location change and on gaps over N sentences.
  8. A scene's `present` is the speakers plus any aliases named.
  9. The output JSONL round-trips.
- **Operator gate:** hand-check 50 lines of `1_006`; at least 90% must have
  the right speaker.

#### WP-09 Stage 3: cast

- **Targets:** `utilities/source_pipeline/src/cast_rank.py`, `cast.py`
- **Test list:**
  1. Ranking by attributed words is stable on ties (alphabetical).
  2. `entry_chapter` is the first chapter with at least `min_lines` lines at
     or after `story_start`.
  3. On a fixture where harry speaks in ch 1 once and in ch 2 five times,
     the entry is ch 2.
  4. On the real corpus (skip if absent): harry, ron, hermione and hagrid are
     in the top 6, harry's entry is ch 2, and ron and hermione enter at ch 6.
- **Gate:** `cast.json` is written, and 4 passes on the dev PC.

#### WP-10 Load source into the DB

- **Targets:** `app/character/generator/__init__.py`, `generator/load_source.py`
- **Test list (integration):**
  1. Loading fixture outputs gives row counts that match the files.
  2. A re-load with the same sha256 does nothing.
  3. A changed chapter sha256 replaces only that chapter's rows.
  4. `present` is stored as `text[]` and the GIN query works.
- **Gate:** the book-1 load on pgserver; the counts are in the tracker.

#### WP-11 LLM client, embeddings, prompt templates, node names

- **Targets:** `app/character/llm.py`, `app/character/embeddings.py`,
  `app/character/node_names.py`, `app/character/prompts/*.md` (by hand,
  following plan §10)
- **Context:** D-15, `tools/qwen_worker/ollama_client.py` (the `think` lesson),
  `app/llm_client.py:18-50`
- **Interfaces:**
  - `llm.complete_json(profile, system, user, shape) -> dict`. It calls
    `/api/chat` with `format: "json"` and `think` from the profile, validates
    with `shapes`, and retries `max_retries` times with the errors appended.
    It raises `LLMError`.
  - `embeddings.embed(texts) -> list[list[float]]`: a POST to
    `{base_url}/embeddings` (OpenAI shape, verified on gx10), in batches of
    32.
  - `node_names.check(name, age=None) -> list[str]`.
- **Test list** (the HTTP layer is faked with `httpx.MockTransport`):
  1. The request body has `format=json`, `think=false` and the profile's
     model and temperature.
  2. Invalid JSON is retried; the second attempt succeeds.
  3. A shape error is retried with the error listed in the user message.
  4. When retries run out, it raises LLMError.
  5. `embed` batches 70 texts as 3 calls, keeping order.
  6. `embed` rejects a response with the wrong count.
  7. node_names: the good examples from §10 pass.
  8. The bad examples fail, with a reason each.
  9. The regex bounds: 1 word fails, 8 words pass, 9 fail.
  10. Every template in `prompts/` contains "first person" and at least 3
      good and 3 bad examples.
- **Gate:** tests pass. One live smoke test (marked `integration`, skipped
  unless `CHARACTER_LIVE_LLM=1`) returns valid JSON from gx10.

#### WP-12 to WP-14 Timeline, backstory, baseline, export, `initialize`

- **Targets:**
  - WP-12: `generator/timeline.py`
  - WP-13: `generator/backstory.py`
  - WP-14: `generator/baseline.py`, `generator/avatar.py`, `generator/export.py`,
    and the `initialize` job in `app/character/jobs_initialize.py`
- **Context:** plan §1 stages 4–7, §4, §9, D-07, `app/campaign/pack.py:20-69`,
  `campaigns/hptest/cast/harry.yaml`, `app/character_schema.py:33-47,288-317`
- **Test list** (FakeLLM returns canned JSON; the DB is pgserver):
  1. Timeline: every event's offsets exist in its chapter, and an event with
     bad offsets is dropped and logged.
  2. Timeline: `pre_story` events have no position.
  3. Backstory: `believed` includes only events before `entry_pos` that the
     character took part in or learned of.
  4. Backstory: a claim without evidence is rejected.
  5. Backstory: `truth` is stored, but `believed_text()` never contains it.
  6. Baseline: it validates against its shape, and bad node names are
     retried and then dropped.
  7. Baseline: it writes version N+1 and moves the pointer only when
     `--activate` is given.
  8. Avatar: `map_appearance` returns `resolve_params(None)` plus the
     configured `accent_color`, and every key is in `SLIDER_DEFAULTS` or is
     `accent_color`.
  9. Export: the YAML has `character_params` inline, and
     `app/campaign/pack.py` still loads `campaigns/hptest` (use its loader).
  10. Export: unknown keys already in the file are kept.
  11. `initialize --dry-run` writes nothing.
  12. `initialize` twice: the second run is a no-op, exit 2.
  13. It seeds `character_agents` with `char:<slug>`.
- **Phase 2 gate:**
  - Book 1 is cleaned with audits passing.
  - `initialize` against pgserver with the real LLM (on gx10) produces trio
    baselines. The user reads them.
  - The `hptest` pack validates.

### Phase 3: loop runtime

#### WP-15 Bus contracts and attribution

- **Targets:** `app/character/bus_contracts.py`, `app/bus_attribution.py`
- **Context:** plan §3.2, D-04, `app/message_bus.py:33-45`
- **Test list:**
  1. The `character_say`, `scene_event` and `character_refresh` builders use
     `build_message` and the payload shapes in plan §3.2.
  2. A builder rejects an unknown `kind` or an empty text.
  3. `character_for_message` returns `payload.character` first.
  4. Otherwise it returns the slug from a `char:harry` sender.
  5. A `char-live:hp` sender gives None.
  6. Other senders give None, and a non-dict payload is handled.
- **Gate:** tests pass.

#### WP-16 Ingest consumer

- **Targets:** `app/character/ingest.py` (pure `route` plus `IngestLoop` with
  an injected consumer), the `ingest` job, and the `character-ingest` service
  in `docker-compose.yml` (`restart: unless-stopped`)
- **Context:** plan §3.3, `app/message_bus.py:60-75` (what **not** to do)
- **Test list:**
  1. `route(agent_thinking from char:harry)` gives one harry row with
     visibility `self`.
  2. An unmapped `agent_thinking` gives no rows and bumps the skip count.
  3. `character_say` with present [harry, ron, gm] gives rows for harry and
     ron only (gm isn't a character).
  4. The union of present, character and addressees is deduplicated.
  5. A type outside the allowlist is dropped before its payload is parsed.
  6. Week and day come from the body timestamp, not the Kafka time. A
     timestamp before the epoch is skipped.
  7. A malformed message is logged at ERROR and skipped; the loop continues.
  8. Offsets are committed only after the DB commit. With a fake consumer, a
     DB failure means no commit.
  9. Redelivery of the same batch inserts 0 rows.
  10. Batching flushes at 200 messages or 2 s, whichever comes first (with a
      fake clock).
  11. The consumer is built with `auto_offset_reset="earliest"`,
      `enable_auto_commit=False` and group `character-ingest`.
  12. `--backfill-from-messages --since` reads a fake cursor and routes the
      same way.
- **Gate:** tests pass. Then a manual smoke on gx10 against the real Kafka
  and the **test** DB: publish 3 fake `character_say` messages and see 3 or
  more rows.

#### WP-17 `messages` M1 and M2, logger

- **Targets:**
  - existing: `services/message-logger/logger.py`, its `Dockerfile` (one COPY
    of `app/bus_attribution.py`), `docs/sql/02_create_tables.sql`,
    `docs/message_logger.md`
  - new: `services/message-logger/migrate_partitioned.py`
- **Context:** `docs/charcterProfileGenerationNotes/v4_reference_messages.sql`,
  `.claude/prompts/character_v4_plan_validation.py:257-440`, and
  `logger.py` in full
- **Note:** `logger.py` is an existing file. The spec emits the whole file,
  so review the diff very carefully: only the M1 DDL, the `character` value,
  shape detection and partition upkeep may change.
- **Test list** (integration on pgserver; a new test file
  `tests/test_message_logger_v4.py`):
  1. The logger's startup DDL on a legacy table adds `character` and both
     indexes, and is idempotent.
  2. An insert fills `character` using `character_for_message`.
  3. The shape is `plain` before M2 and `partitioned` after.
  4. Partitioned: `ON CONFLICT (id, timestamp)` dedupes.
  5. Partitioned: `ensure_partitions` creates today through today+14 and is
     idempotent.
  6. `migrate_partitioned --dry-run` changes nothing.
  7. The migration copies every row, keeps `messages_legacy`, and a second
     run refuses.
  8. The M1 statements appear verbatim in `logger.py` and
     `docs/sql/02_create_tables.sql` (a text check).
- **Gate:**
  - Tests pass.
  - The existing message-logger tests still pass.
  - **Don't run M1 or M2 on production.** Write the operator steps into
    `docs/message_logger.md` instead.

#### WP-18 Compaction

- **Targets:** `app/character/compaction.py`
- **Context:** plan §5, D-03, the reference SQL section C, and the
  validation script's compaction checks
- **Test list (integration):**
  1. A: noisy types older than N hours are deleted; newer ones and other
     types stay.
  2. B-plain: rows older than the cutoff are moved in batches into
     `messages_archive`, which is created if missing.
  3. B-partitioned: old `messages_pYYYYMMDD` partitions are detached and
     attached to the archive; the row counts are preserved.
  4. B-partitioned: old DEFAULT rows are moved to the archive default.
  5. Partition age comes from the name, and a name not matching the pattern
     is ignored.
  6. A second run changes nothing.
  7. `--dry-run` reports the counts and changes nothing.
  8. It is skipped with exit 3 when ingest lag exceeds `max_lag_messages`.
- **Gate:** tests pass.

#### WP-19 Daily summaries and the `daily-maintenance` job

- **Targets:** `app/character/summaries.py`, `app/character/jobs_daily.py`
- **Context:** plan §5, D-21, `prompts/summary_day.md`
- **Test list:**
  1. A day with events gives one summary row plus up to 10 nodes with valid
     names.
  2. A day with no events gives a summary saying "quiet day", and no LLM call.
  3. A re-run is a no-op (the key is `(character_id, loop_day)`).
  4. If ingest hasn't passed midnight, it waits up to `catchup_wait_s` (fake
     clock), then marks the summary `partial`.
  5. It runs summaries, then compaction, in that order. If compaction exits
     3, the summaries are still committed and the job exits 3.
  6. Only `self` events of that character, plus `present` events, are
     summarised.
  7. `--character harry` limits the run to harry.
- **Gate:** tests pass.

#### WP-20 Fragments and `weekly-reset`

- **Targets:** `app/character/fragments.py` (select, create, lead-up
  extraction), `app/character/jobs_weekly.py`
- **Context:** plan §2, §5 (the weekly-reset row), D-08, D-09,
  `prompts/fragment.md`
- **Test list:**
  1. The steps run in order: close_saturday, select_fragments, archive,
     open_next, refresh.
  2. After a crash in step 3 (injected), a re-run skips steps 1–2 and
     finishes.
  3. A character without `retains_fragments` gets no fragments.
  4. A harry fragment has a gist in the first person, a lead-up of
     `lead_up_beats` beats with embeddings, and hooks.
  5. The fragment's lead-up comes from events **before** its moment,
     in order.
  6. Archive: week-W nodes are hidden from `current_nodes(W+1)`.
  7. Week W+1 is open, with bounds from the clock.
  8. Refresh publishes one `character_refresh` through a fake producer.
     With `refresh_mode: none`, nothing is published.
  9. Running before Sunday 00:00 NY exits 2 (not due), unless `--force`.
  10. `--dry-run` writes nothing and publishes nothing.
- **Gate:** tests pass.

#### WP-21 `testctl`

- **Targets:** `app/character/testctl.py`, and registration in `main.py`
- **Test list:**
  1. `reset-undo --week W` restores the state before the reset (snapshot
     the tables, run the reset, undo, compare).
  2. A delete without `--confirm <dbname>` exits 1.
  3. `fragment delete` works only through the bypass, and the trigger still
     blocks normal sessions afterwards.
  4. `unlock` and `lock` toggle the unlock row.
  5. `seed --file` inserts synthetic events with week and day from the clock.
  6. `week wipe` removes that week's rows only.
  7. `testctl.enabled: false` makes every subcommand exit 1.
  8. Every subcommand writes a `testctl:<cmd>` jobs row, except under
     `--dry-run`.
  9. `revert --to-version N` (a separate job) moves the pointer and publishes
     a refresh.
- **Gate:** tests pass.

#### WP-22 Brief

- **Targets:** `app/character/brief.py`
- **Context:** plan §8, D-08
- **Test list:**
  1. The sections come out in plan order, with their headings.
  2. It never contains `truth`, dormant gists, week numbers, or the words
     "loop", "week" or "reset".
  3. Unlocked fragments appear as feelings.
  4. It stays under `max_chars` by dropping the oldest week-knowledge lines
     first.
  5. `BriefCache`: a hit within the TTL; a miss after it (fake clock);
     `invalidate(slug)` and `invalidate_all()`.
  6. It includes the extra "a feeling surfaces: …" lines passed in by the
     caller.
- **Gate:** tests pass.

#### WP-23 Recall engine and harness

- **Targets:** `app/character/recall.py` (pure), `app/character/recall_harness.py`
  (the CLI: replay beats from a file or the DB against fragments, and print
  scores)
- **Context:** plan §7, D-18, `.claude/prompts/character_v4_recall_probe.py`
  (the reference Smith-Waterman and its measured numbers)
- **Test list** (FakeEmbed with fixed vectors):
  1. `sw_score` of an identical sequence gives a normalised value of 1
     (clipped).
  2. A shuffled sequence scores lower than an ordered one.
  3. Unrelated beats (cosine under the bias) score 0.
  4. `hooks` are case-insensitive whole words, alias aware: "Harry" matches,
     "Harrys" doesn't.
  5. Activation decays by `decay` per beat.
  6. Spread reaches linked fragments.
  7. Crossing `unease` gives one unease event per cooldown.
  8. Crossing `surface` calls the judge. A yes gives a surface event and
     `unlock`; a no gives nothing, and the cooldown still applies.
  9. Unlocked fragments are never candidates.
  10. `reset()` clears activation, which is what a refresh does.
  11. The harness replays the probe's fixture and ranks replay above
      shuffled above unrelated.
- **Gate:** tests pass. Then run the harness with real embeddings on gx10
  over the WP-25 scenario, and record the tuned thresholds in the tracker and
  in `config/character.yaml`.

#### WP-24 Live driver, and `story-start` / `story-stop`

- **Targets:** `app/character/live.py` (the driver plus `ObservingRenderer`),
  `app/character/jobs_story.py`, and the `character-live` service in
  `docker-compose.yml`
- **Context:** plan §8 (live driver), D-19, `app/campaign/runtime.py:60-170`,
  `app/campaign/improviser.py:39-120`, `app/campaign/renderer.py:1-35,60-119`,
  `app/worker_control.py:27-60`
- **Test list** (fake LLM, producer and WorkerControl; the real
  `campaigns/hptest` pack):
  1. `ObservingRenderer.render_beat` calls the parent, then publishes
     `character_say` for dialogue and `scene_event` for narration.
  2. Every improviser `observe()`s every beat.
  3. Each cast member's `system_prompt` is the brief, and it never contains
     "Loop".
  4. On `character_refresh`, the cache is invalidated, the transcripts are
     cleared, and recall is reset.
  5. When WorkerControl disables it, the scene finishes, then `story_end` is
     published and the exit code is 0.
  6. At the week's `ends_at` (fake clock) it stops the same way.
  7. A recall surface injects "a feeling surfaces: <gist>" into the next
     brief for that character only.
  8. `story-start` enables the driver and `story-stop` disables it; both are
     idempotent.
  9. No file under `app/campaign/` is modified (compare `git diff --stat`, or
     the sha256 of the files at WP start).
- **Gate:** tests pass. Then a manual run on gx10 with `--foreground`
  against the test DB and real Kafka: one scene, and check that
  `character_say` messages appear in the message-logger.

#### WP-25 End-to-end simulated fortnight

- **Targets:** `tests/character/test_e2e_two_weeks.py` (no module: this is
  the integration proof)
- **Scenario** (pgserver, FakeLLM, FakeEmbed with vectors chosen so a replay
  aligns; `--at` drives time):
  1. `initialize` for the trio, with canned baselines.
  2. Week 1: seed or feed events through `route()` for harry, ron and
     hermione; run `daily-maintenance` at each midnight.
  3. `weekly-reset` at Sunday 00:00 gives one harry fragment (dormant) and
     archives week 1.
  4. The week 2 brief has no week 1 knowledge and no fragment.
  5. Week 2 replays harry's lead-up through the recall engine, which
     surfaces it, the judge says yes, and the fragment unlocks.
  6. The brief now carries it as a feeling.
  7. `weekly-reset` week 2, then the week 3 brief still carries the feeling.
  8. `testctl reset-undo --week 2` restores the state after week 2 exactly.
  9. A re-run of any completed job exits 2 and changes nothing.
- **Phase 3 gate:** this test passes on pgserver, and the full suite is no
  worse than the baseline.

#### WP-26 Docs

- **By hand:**
  - One `docs/<module>.md` per public module (CLAUDE.md template), plus
    `app/character/README.md`.
  - The root `README.md`: link the deploy README, and add a "Character loop"
    section.
  - `docs/project_structure.md`: add `deploy/` and `app/character/`.
  - A `CHANGELOG.md` entry.
  - `.claude/prompts/INDEX.md`: replace the stale "run phase 2" next action.
  - Close the tracker.
- **Gate:** the user reviews.

## 5. The tracker

Keep `.claude/prompts/character_v4_build_status.md` current. Update it after
every WP, every blocked run, and every question. Its sections:

- Baseline (from WP-00).
- A WP table: status, spec file(s), attempts and model, gate result, and the
  host the gate ran on.
- Test corrections, each with the plan citation that justified it.
- Questions for the user.
- Small local choices, per §2.5.

## 6. Why it is shaped this way (for the reviewer)

- Test lists are fixed by the planner, so a 27B model never decides *what*
  to test. It only encodes it. This is the same discipline that made the
  campaign and 3-layer modules work (`docs/campaign_module_status.md:31-33`).
- There are at most 2 target files per spec, because the harness always
  emits whole files. Big specs fail in whole-file rewrites.
- Pure logic (clock, route, recall, sw_score, guard, windows) is split from
  I/O everywhere, so most tests are unit tests. Fragile integration tests are
  kept to the stores, the jobs and one e2e.
- The reference SQL and the clock and recall reference code are already
  validated (33/33 checks; the recall probe passes), so a WP failure is a
  code bug, not a design bug.
