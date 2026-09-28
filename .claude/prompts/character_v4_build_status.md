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
| Linux cloud container (x86_64) | 2026-09-28 | 4444 | 0 | 0 (61 skipped, 1 xfailed) | `.venv/bin/python -m pytest -q` |
| Linux cloud container, after the WP-02..06 tests-written session | 2026-09-28 | 4444 | 0 | 0 (74 skipped = 61 + 13 v4 pending skips, 1 xfailed), exit 0 | `.venv/bin/python -m pytest -q` |

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
- **Linux baseline (2026-09-28, OB-41 session):** 4444 passed / 0 failed / 61
  skipped / 1 xfailed in the Linux cloud container (pgserver 0.1.4 + pgvector
  work here). Linux "still green" checks compare against this row; the
  Windows row stays the dev-PC reference. The v4 tests written this session
  add skips (pending code), never failures.
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

**Session decision (user, 2026-09-28, OB-41 cloud session):** "tests and specs
only" in the cloud session; the code is generated via the qwen harness on
argyre (`tools/qwen_worker/runner.py run <spec>`). So WP-02..WP-06 stop at
`tests-written`: frozen tests + spec YAMLs + the by-hand items, no module
code. Next step per WP: run its spec(s) in order on argyre with
`CHARACTER_TEST_REQUIRE_DB=1` (see Small local choices), review, promote,
gate.

**OB-41 overrides applied (office build plan OB-41):** pilot cast = the 8
office characters (ceo, tech_lead, analyst, engineer, tester, marketing,
office_manager, party_member), `retains_fragments: true` for all 8, loop
epoch Sunday 2026-09-27 America/New_York (= app/office/weekly_reset.py
DEFAULT_EPOCH / OFFICE_EPOCH), campaign `ashiorid_office`, pack
`campaigns/ashiorid_office`. The book source stages (WP-07..WP-10) are
replaced later by `scripts/load_office_profiles.py` (not Phase 1).
`app/character/` already existed (OB-20: `__init__.py`, `avatar.py`); the v4
modules live beside it and the WP-03 clock spec keeps the avatar exports.

| WP | Status | Spec(s) | Runs / model | Gate result | Gate host |
|---|---|---|---|---|---|
| WP-00 harness on Windows | gated | (by hand) | n/a | preflight OK twice; baseline 2826/56/86 recorded | dev PC (Windows) |
| WP-01 reader role | gated | local vLLM subagent (qwen3.8-27b) + hand fix | n/a | run_tests.sh 58/0; pytest db_scripts 1/1; compose config OK; stub-psql dry run both branches exit 0. Subagent indented the heredoc `SQL` terminators (script would not parse) -- fixed by hand, added bash -n regression checks. Subagent wall time ~7.5 h. | dev PC (Windows) |
| WP-02 backup.sh | tests-written | `character_wp02_backup.yaml` | not run | strict: 40 of 54 backup checks FAIL (backup.sh missing, bash exit 127), both pytest wrapper tests fail; default: section 17 skipped, 58/58 old checks pass | Linux cloud container (tests only) |
| WP-03 config + clock + shapes | tests-written | `character_wp03_config_shapes.yaml`, `character_wp03_clock.yaml` | not run | strict: 3 collection errors, all ModuleNotFoundError (character.config/.clock/.shapes); default: 3 files skip | Linux cloud container (tests only) |
| WP-04 db + migrations | tests-written | `character_wp04_db.yaml` | not run | strict: ModuleNotFoundError character.db; default: skip. `pg` fixture verified on pgserver here | Linux cloud container (tests only) |
| WP-05 stores | tests-written | `character_wp05_stores_{characters,weeks,events,knowledge,fragments,jobs}.yaml` (run in that order) | not run | strict: 5x ModuleNotFoundError character.store, weeks file hits character.clock first; default: 6 files skip | Linux cloud container (tests only) |
| WP-06 jobs + CLI + container | tests-written | `character_wp06_jobs.yaml`, `character_wp06_main.yaml`, `character_wp06_compose.yaml` (blocked on Q7) | not run | strict: ModuleNotFoundError character.jobs; ImportError main.py not found; default: 2 files skip | Linux cloud container (tests only) |
| **Phase 1 gate** | | | | | |
| WP-07 clean | n/a (OB-41) | replaced by WP-10o | | | |
| WP-08 tag | n/a (OB-41) | replaced by WP-10o | | | |
| WP-09 cast | n/a (OB-41) | replaced by WP-10o | | | |
| WP-10 load source | n/a (OB-41) | replaced by WP-10o | | | |
| WP-10o load office profiles (OB-41) | tests-written | `character_wp10o_office_profiles.yaml`, `character_wp10o_load_cli.yaml` | not run | see tracker_phase2 | Linux cloud container (tests only) |
| WP-11 llm / embeddings / node names | tests-written | `character_wp11_{node_names,llm,embeddings}.yaml` (run first) | not run | see tracker_phase2 | Linux cloud container (tests only) |
| WP-12 timeline | n/a (OB-41) | office backstories are authored | | | |
| WP-13 backstory | n/a (OB-41) | office backstories are authored | | | |
| WP-14 baseline + export + initialize | tests-written | `character_wp14_{export,initialize}.yaml` | not run | see tracker_phase2 | Linux cloud container (tests only) |
| **Phase 2 gate** | | | | | |
| WP-15 contracts + attribution | tests-written | `character_wp15_bus_contracts.yaml` | not run | see tracker_phase3a | Linux cloud container (tests only) |
| WP-16 ingest | tests-written | `character_wp16_{ingest,jobs_ingest,compose}.yaml` | not run | see tracker_phase3a | Linux cloud container (tests only) |
| WP-17 messages M1/M2 + logger | tests-written | `character_wp17_{logger,schema_copies}.yaml` | not run | see tracker_phase3a | Linux cloud container (tests only) |
| WP-18 compaction | tests-written | `character_wp18_compaction.yaml` | not run | see tracker_phase3a | Linux cloud container (tests only) |
| WP-19 summaries + daily job | tests-written | `character_wp19_{summaries,jobs_daily}.yaml` | not run | see tracker_phase3a | Linux cloud container (tests only) |
| WP-20 fragments + weekly reset | tests-written | `character_wp20_{fragments,jobs_weekly}.yaml` | not run | see tracker_phase3a | Linux cloud container (tests only) |
| WP-21 testctl | tests-written | `character_wp21_testctl.yaml` | not run | see tracker_phase3b | Linux cloud container (tests only) |
| WP-22 brief | tests-written | `character_wp22_brief.yaml` | not run | see tracker_phase3b | Linux cloud container (tests only) |
| WP-23 recall + harness | tests-written | `character_wp23_{recall,recall_harness}.yaml` | not run | see tracker_phase3b | Linux cloud container (tests only) |
| WP-24 live driver + story jobs | tests-written | `character_wp24_{live,jobs_story,compose}.yaml` | not run | see tracker_phase3b | Linux cloud container (tests only) |
| WP-25 e2e two weeks | tests-written | `character_(test only).yaml` | not run | see tracker_phase3b | Linux cloud container (tests only) |
| **Phase 3 gate** | | | | | |
| WP-26 docs | todo | | | | |


**2026-09-28 (cloud session): tests and specs for every WP are written and committed.** Details
(item→test mappings, test corrections with OB-41 citations, local choices, questions, spec run
order) are in the three phase files, which the orchestrator reads alongside this tracker:
[character_v4_tracker_phase2.md](character_v4_tracker_phase2.md) (WP-10o, 11, 14),
[character_v4_tracker_phase3a.md](character_v4_tracker_phase3a.md) (WP-15..20),
[character_v4_tracker_phase3b.md](character_v4_tracker_phase3b.md) (WP-21..25).
Next step on argyre: run the specs in WP order through `tools/qwen_worker/runner.py` with
`CHARACTER_TEST_REQUIRE_DB=1` (and `CHARACTER_TEST_DSN` on aarch64). Specs that rewrite
`services/character-updater/main.py` (WP-06, 14, 16, 19, 20, 21, 24) must run strictly in order;
check every main.py diff for dropped job registrations.

## Test list → test name mapping

### WP-00

- T00.1 -> test_venv_python_finds_posix
- T00.2 -> test_venv_python_finds_windows
- T00.3 -> test_venv_python_missing_raises
- T00.4 -> test_build_sandbox_copies_deploy_and_skips_har
- T00.5 -> test_env_vars_override_ollama_defaults

### WP-02 (`deploy/character-profile-db/tests/run_tests.sh` section 17; pytest wrapper `tests/test_character_profile_db_scripts.py::test_backup_script_passes_mocked_checks`)

- T02.1 -> section "-- T02.1", checks B1 (dry-run prints pg_dump, zfs snapshot, rm and zfs destroy lines; files, snapshots, calls and backup_runs unchanged)
- T02.2 -> "-- T02.2", checks B2 (pg_dump via `pct exec 101 -- docker exec character-profile-db`, daily/character_profile_20261006_0030.dump, @auto- snapshot, ok row, no weekly copy on Tuesday)
- T02.3 -> "-- T02.3", checks B3 (Sunday 2026-10-04: identical copy in weekly/)
- T02.4 -> "-- T02.4", checks B4 (20+1 daily -> 14, 10 weekly -> 8, 20+1 snapshots -> 14, boundaries checked)
- T02.5 -> "-- T02.5", checks B5 (notes.txt, *_manual.dump, other_*.dump, *.dump.partial, @manual-before-upgrade, @auto-notadate kept)
- T02.6 -> "-- T02.6", checks B6 (non-zero exit, backup_runs row 'failed')
- T02.7 -> "-- T02.7", checks B7 (create/pg_restore newest/count both/drop drill DB; mismatch exits 1, names the table, still drops)
- T02.8 -> "-- T02.8", checks B8 (not root: exit 1, message says root, no pct/zfs call)

### WP-03

- T03.1 -> test_character_config.py::test_load_repo_config_succeeds
- T03.2 -> test_character_config.py::test_epoch_not_sunday_names_loop_epoch
- T03.3 -> test_character_config.py::test_weights_not_summing_to_one_raise (+ test_weights_within_tolerance_are_accepted for the "±1e-6" clause)
- T03.4 -> test_character_config.py::test_unease_not_below_surface_raises
- T03.5 -> test_character_config.py::test_env_overrides_db_host_and_password_never_in_repr
- T03.6 -> test_character_config.py::test_missing_password_loads_then_connect_fails (the db.connect half skips until WP-04 is promoted)
- T03.7 -> test_character_config.py::test_ingest_dsn_falls_back_to_main_values
- T03.8 -> test_character_clock.py::test_saturday_last_second_is_week_1_and_sunday_midnight_is_week_2
- T03.9 -> test_character_clock.py::test_0200z_sunday_is_still_saturday_in_new_york
- T03.10 -> test_character_clock.py::test_dst_end_week_is_169h_and_dst_start_week_is_167h (office epoch: week 6 = 169 h, week 25 = 167 h)
- T03.11 -> test_character_clock.py::test_week_bounds_and_position_round_trip_for_59_weeks
- T03.12 -> test_character_clock.py::test_naive_datetime_raises_value_error
- T03.13 -> test_character_clock.py::test_previous_day_just_after_sunday_midnight_is_saturday
- T03.14 -> test_character_shapes.py::test_each_violation_gives_one_error_with_its_path (10 parametrized cases: missing, wrong types incl. bool-as-int, bad enum, nested list items) (+ test_root_that_is_not_an_object_gives_one_root_error)

### WP-04 (`tests/character/test_character_db.py`, integration)

- T04.1 -> test_migrate_empty_db_applies_001_and_creates_all_tables
- T04.2 -> test_second_migrate_applies_nothing
- T04.3 -> test_changed_checksum_raises_migration_error
- T04.4 -> test_advisory_lock_first_session_wins_second_fails
- T04.5 -> test_transaction_rolls_back_when_block_raises
- T04.6 -> test_vector_extension_is_present
- T04.7 -> test_reader_role_can_select_but_not_insert

### WP-05 (integration, `pg_conn` fixture)

- T05.1 -> test_character_store_characters.py::test_upsert_character_is_idempotent
- T05.2 -> test_character_store_characters.py::test_set_active_baseline_fails_for_missing_version
- T05.3 -> test_character_store_characters.py::test_agents_map_returns_agent_to_character_id
- T05.4 -> test_character_store_weeks.py::test_ensure_week_is_idempotent
- T05.5 -> test_character_store_weeks.py::test_run_step_locks_row_skips_completed_and_records_completed_at
- T05.6 -> test_character_store_weeks.py::test_raising_step_fn_leaves_step_unrecorded
- T05.7 -> test_character_store_events.py::test_insert_many_ignores_duplicates_and_returns_inserted_count
- T05.8 -> test_character_store_events.py::test_events_for_day_is_ordered_by_ts
- T05.9 -> test_character_store_events.py::test_update_ingest_status_touches_only_its_own_columns
- T05.10 -> test_character_store_knowledge.py::test_archive_week_marks_unarchived_nodes_and_edges_up_to_week
- T05.11 -> test_character_store_knowledge.py::test_current_nodes_excludes_archived
- T05.12 -> test_character_store_knowledge.py::test_unarchive_week_reverses_archive_week
- T05.13 -> test_character_store_fragments.py::test_create_fragment_with_lead_up_and_links_is_one_transaction
- T05.14 -> test_character_store_fragments.py::test_update_or_delete_without_test_flag_raises
- T05.15 -> test_character_store_fragments.py::test_dormant_for_excludes_unlocked
- T05.16 -> test_character_store_fragments.py::test_unlock_is_idempotent
- T05.17 -> test_character_store_fragments.py::test_test_delete_works_inside_allow_test_mutation
- T05.18 -> test_character_store_jobs.py::test_start_finish_and_fail_job
- T05.19 -> test_character_store_jobs.py::test_list_recent_returns_newest_first

### WP-06

- T06.1 -> test_character_updater_main.py::test_unknown_job_exits_argparse_2_with_usage
- T06.2 -> test_character_jobs.py::test_at_sets_ctx_now_and_naive_at_is_rejected
- T06.3 -> test_character_jobs.py::test_dry_run_writes_no_jobs_row
- T06.4 -> test_character_jobs.py::test_lock_contention_exits_2
- T06.5 -> test_character_jobs.py::test_raising_job_exits_1_writes_failed_row_and_logs_error
- T06.6 -> test_character_jobs.py::test_status_on_migrated_empty_db_prints_no_weeks_and_exits_0
- T06.7 -> test_character_updater_main.py::test_main_works_from_any_cwd

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
   **Answered (user, 2026-09-28):** "commit per WP". The parent commits
   one conventional commit per WP, staging only that WP's files (the per-WP
   file lists are in the OB-41 session notes below).

3. **Week numbering: office clock is 0-based, v4 clock is 1-based.**
   `app/office/clock.py:106` does `loop_week, day_index = divmod(days, 7)`,
   so the office week at the epoch is 0; v4 (D-01, plan §2,
   `.claude/prompts/character_v4_plan_validation.py:80-83`) makes it week 1.
   Same epoch (2026-09-27), so office week N = v4 week N+1. The office
   `v4_weekly_reset` hook (`app/office/weekly_reset.py:268`, `:516-520`)
   passes office week numbers. Options: (a) keep both, convert at the hook
   (v4 = office + 1); (b) make v4 0-based (changes D-01 and every validated
   clock case); (c) make the office clock 1-based (touches OB-30/31 code and
   its ledger). Recommendation: (a). The WP-03 tests are written for the
   plan's 1-based clock.
4. **`source:` block for the office campaign (config key).** Plan §11 has a
   book `source:` block; the office cast has none. `config/character.yaml`
   omits it (commented out) and the WP-03 spec makes `source` optional
   (`CharacterConfig.source is None`). Options: (a) keep it absent; (b) add an
   office form (e.g. `source: {kind: office_profiles, profiles_dir:
   campaigns/ashiorid_office/profiles}`) when `scripts/load_office_profiles.py`
   lands. Recommendation: (a) now, decide (b) at the loader step.
5. **`is_main` for the office cast.** All 8 `profiles/*.yaml` say
   `is_main: true`; plan §4 meant `is_main` as "the main streamed character"
   (harry only). `config/character.yaml` mirrors the profiles (all 8 true).
   Confirm, or set it on the CEO only.
6. **Plan claim about install.sh is false (backups dataset).** Plan §5.1:
   "writes to /tank_0/utilities/character-profile-db-backups/daily/... That
   dataset is created by install.sh." `deploy/character-profile-db/scripts/install.sh:32`
   (`DATASET=tank_0/utilities/character-profile-db`) and its step 1 create
   only the database dataset; nothing creates a backups dataset. The WP-02
   spec has backup.sh `mkdir -p` the two directories (they then live on the
   parent dataset `tank_0/utilities`). Options: (a) keep that; (b) add
   `zfs create tank_0/utilities/character-profile-db-backups` to install.sh
   (+ uninstall.sh keeps it by default), a small follow-up WP. Recommendation:
   (b) before OP-4, so dumps don't sit inside a snapshot of the DB dataset's
   parent and have their own quota; (a) is enough to run the drill.
7. **WP-06 compose service: the harness can't do this one well.**
   `docker-compose.yml` is ~50 KB: as context plus a whole-file reply it is
   over the playbook §2.2 budget (the spec's prompt measures ~76k chars), and
   `tests/test_office_configs.py` also reads `docker-compose.office.yml`,
   which the sandbox doesn't copy (`tools/qwen_worker/sandbox.py`
   SANDBOX_FILES = pytest.ini only), so the spec can't pass in the sandbox.
   `character_wp06_compose.yaml` is written (with the exact block) but
   flagged. Options: (a) add the `character-jobs` block by hand (it is config,
   like config/character.yaml; playbook §1.4 would need that exception);
   (b) retarget the spec at a new small `docker-compose.character.yml`
   override (a file-location change); (c) extend the sandbox to copy root
   compose files (a harness change, WP-00 scope). Recommendation: (a).
8. **`character_baselines.baseline_book/baseline_chapter` are NOT NULL**, but
   the office cast has no book position. The WP-05 characters spec and tests
   pass 0/0. Options: (a) 0/0 by convention for office baselines; (b) a
   `002_*.sql` migration making them nullable. Recommendation: (a); decide
   when `scripts/load_office_profiles.py` is specced.
9. **Harness counts an all-skipped run as PASS.** `runner.py` treats pytest
   exit 0 as a pass, and the v4 integration tests skip when no Postgres is
   reachable (e.g. gx10/argyre on aarch64 without `CHARACTER_TEST_DSN`).
   Mitigation added: `CHARACTER_TEST_REQUIRE_DB=1` makes the `pg` fixture
   fail instead of skip; every v4 spec says to run with it. Optional
   hardening (WP-00 scope): have `sandbox.run_pytest` fail when the summary
   shows 0 passed. Want that?
10. **`*.sql` line endings.** `.gitattributes` pins only `*.sh` to LF, so a
    Windows autocrlf checkout of `app/character/sql/001_init.sql` differs
    byte-wise from a Linux one. The WP-04 spec hashes with CRLF normalised
    to LF so the checksum guard doesn't fire across hosts. Adding
    `*.sql text eol=lf` to `.gitattributes` would be cleaner but is not in
    the playbook's file list. Want it added?

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

### OB-41 session (2026-09-28, Linux cloud container): tests and specs for WP-02..WP-06

- **Pending guard** `tests/character/pending.py` (by hand): `require(module,
  target_path, wp=None)` imports normally with `CHARACTER_V4_STRICT=1`;
  otherwise skips the test module with "v4 <WP>: code pending (qwen harness)"
  while the repo-relative target file is missing; otherwise imports normally
  so real errors surface. `skip_if_pending(target)` is the same check for
  non-import targets. REPO_ROOT comes from the file's own location, so in
  the harness sandbox (which copies app/ and tests/ and overlays the staged
  targets, `tools/qwen_worker/sandbox.py:96-132`) the target exists and the
  tests run for real. WP-02 (bash): `run_tests.sh` section 17 and the pytest
  wrapper `test_backup_script_passes_mocked_checks` key on
  `scripts/backup.sh` existing, with the same STRICT override.
- **`CHARACTER_TEST_REQUIRE_DB=1`** (tests/character/conftest.py): the `pg`
  fixture fails instead of skipping when no database is reachable. Set it for
  every harness run (question 9).
- **Test file names** are `tests/character/test_character_<module>.py`, not
  `test_<module>.py`: tests/ has no `__init__.py`, so pytest imports test
  files by basename and `test_config.py` would collide with
  `utilities/3LayersWeeklyGeneration/tests/test_config.py`. tests/character/
  also has no `__init__.py`, because a package named `character` would
  shadow `app/character`.
- **WP-06 tests are two files** (framework: `test_character_jobs.py`; CLI:
  `test_character_updater_main.py`) so each spec's pending guard sees only its
  own target.
- **Fixtures:** `pg` = fresh empty DB per test (`CREATE DATABASE ... TEMPLATE
  <empty session template>`), CHARACTER_TEST_DSN else pgserver in a pytest
  tmp dir else skip; `pg_conn` = `pg` + every `app/character/sql/*.sql`
  applied raw, so the store tests don't depend on `db.migrate()`. Verified
  here: fresh DBs per test, 23 tables + `vector` after `pg_conn`.
- **Satisfiability check (scratchpad only, nothing committed):** minimal
  throwaway implementations of every Phase 1 target, written in a scratch
  copy of the repo, pass all 60 v4 pytest tests with
  `CHARACTER_TEST_REQUIRE_DB=1` and all 112 `run_tests.sh` checks (58 old +
  54 backup). So the frozen tests are consistent and satisfiable. The same
  scratch run confirmed the WP-06 gate path: `main.py status --at
  2026-10-05T12:00:00-04:00` exits 0 and prints "no weeks" against a migrated
  pgserver DB with CHARACTER_DB_HOST=<socket dir>, PORT=5432, USER=postgres
  and any non-empty CHARACTER_DB_PASSWORD (pgserver uses trust auth; db.connect
  requires a password to be set).
- **WP-02 mocks** (tests/mockbin): new `docker` (pg_dump / pg_restore / psql
  in the container, SQL log, per-DB row counts) and `date` (real GNU date
  pinned to `$MOCK_STATE/now`) mocks; `pct` forwards the `docker exec` calls
  it doesn't answer to the docker mock; `zfs` gained `snapshot`, snapshot
  listing (`-t snapshot`, unsorted on purpose) and snapshot destroy; `id`
  honours `$MOCK_STATE/not_root`. backup.sh's paths are env-overridable
  (`BACKUP_ROOT`, like install.sh's `DATASET`). The README "Backups" section
  (the WP-02 gate) is a by-hand docs step after promote.
- **WP-03 by hand:** `config/character.yaml` from plan §11 with these
  deviations (all marked "OB-41:" in the file): campaign `hp` ->
  `ashiorid_office`; `loop.epoch` 2026-10-04 -> 2026-09-27; `source:` block
  omitted (question 4); `pack` `campaigns/hptest` -> `campaigns/ashiorid_office`;
  characters harry/ron/hermione -> the 8 office slugs, each
  `retains_fragments: true`, `is_main: true` (question 5), `accent_color`
  from `cast/<slug>.yaml character_params` (ceo BLUE, tech_lead WHITE,
  analyst RED, engineer GREEN, tester CYAN, marketing PURPLE, office_manager
  YELLOW, party_member BLACK). Every other key and value is as in §11.
- **tzdata:** not added. `requirements.txt:3` already has `tzdata>=2025.2`
  (OB-06) and `.venv` has tzdata 2026.4; adding `tzdata==2026.4` would
  duplicate the package line. `requirements-dev.txt` created with
  `pgserver==0.1.4; platform_machine != "aarch64"`. `pytest.ini` unchanged
  (`testpaths = tests` already covers tests/character).
- **WP-04 by hand:** `app/character/sql/001_init.sql` is a byte-identical copy
  of `docs/charcterProfileGenerationNotes/v4_reference_character_profile.sql`.
- **Interface choices written into the specs** (small, per §2.5): ConfigError
  keys drop the `character.` prefix (`loop.epoch`); an empty env var counts
  as unset; `db.connect()` with no password raises
  `ConfigError("db.password", ...)` before any network call (T03.6);
  migration version = file stem (`001_init`), sha256 over CRLF-normalised
  bytes, all checksums verified before anything new is applied; clock weeks
  are 1-based and a pre-epoch timestamp gives week <= 0 (no raise); shapes
  errors are `"$.path: message"`, float accepts int, bool is never int;
  stores never commit except `weeks.run_step`, `fragments.create_fragment`
  and `fragments.allow_test_mutation`; `current_nodes(c, W)` =
  `loop_week <= W AND archived_at_week IS NULL`; `update_ingest_status`
  takes deltas; `set_active_baseline` raises LookupError; a naive `--at` is
  an argparse usage error (exit 2); a lost lock writes a `skipped` jobs row
  and exits 2; "nothing"/"precondition" results finish as `skipped`;
  `StatusJob` lives in `app/character/jobs.py` (the plan names no separate
  file); `run_job(job, argv, *, cfg=None, env=None, connect=None)` takes
  injectable config/connect; `main.py` finds `character/` next to itself
  (container) or in `../../app` (repo).
- **WP-03 clock spec / `__init__.py`:** keep the avatar exports exactly;
  `tests/test_character_avatar.py` is not a harness test file because it
  loads `scripts/generate_office_avatars.py` and the sandbox doesn't copy
  `scripts/`; run it in the real tree after promote.
- **Spec run order:** wp02_backup; wp03_config_shapes, wp03_clock;
  wp04_db; wp05_stores_characters, _weeks, _events, _knowledge, _fragments,
  _jobs; wp06_jobs, wp06_main, (wp06_compose after question 7). One at a
  time; with `CHARACTER_TEST_REQUIRE_DB=1`; on argyre/gx10 (aarch64) also set
  `CHARACTER_TEST_DSN` to the test instance (OP-5).
