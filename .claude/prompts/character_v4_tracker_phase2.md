# Character v4 tracker additions: Phase 2 (tests and specs, OB-41 session)

For the parent to merge into `.claude/prompts/character_v4_build_status.md`.
Session: Linux cloud container, 2026-09-28, user decision "tests and specs only".
Nothing here is implementation code; modules come from the qwen harness on argyre.

## WP table rows

| WP | Status | Spec(s) | Runs / model | Gate result | Gate host |
|---|---|---|---|---|---|
| WP-07 clean | not applicable (OB-41) | | | book source stage; office cast has no book | |
| WP-08 tag | not applicable (OB-41) | | | | |
| WP-09 cast | not applicable (OB-41) | | | | |
| WP-10 load source | replaced by WP-10o (OB-41) | | | | |
| WP-10o load office profiles (new, OB-41) | tests-written | `character_wp10o_office_profiles.yaml`, `character_wp10o_load_cli.yaml` | not run | strict: 3 collection errors, ModuleNotFoundError `character.generator` (x2), ImportError scripts/load_office_profiles.py not found; default: 3 files skip | Linux cloud container (tests only) |
| WP-11 llm / embeddings / node names / prompts | tests-written; prompts written by hand | `character_wp11_node_names.yaml`, `character_wp11_llm.yaml`, `character_wp11_embeddings.yaml` | not run | strict: 3 collection errors, ModuleNotFoundError (`character.node_names`, `.llm`, `.embeddings`); prompts file 3 pass + 2 fail with ImportError (cannot import node_names); default: 3 files skip, prompts 3 pass + 2 skip | Linux cloud container (tests only) |
| WP-12 timeline | not applicable (OB-41) | | | office backstories are authored | |
| WP-13 backstory | not applicable (OB-41) | | | office backstories are authored | |
| WP-14 baseline + export + initialize | tests-written | `character_wp14_export.yaml`, `character_wp14_initialize.yaml` | not run | strict: 2 collection errors, ModuleNotFoundError (`character.generator`, `character.jobs_initialize`); default: 2 files skip | Linux cloud container (tests only) |

**Spec run order (Phase 2):** wp11_node_names, wp11_llm, wp11_embeddings,
wp10o_office_profiles, wp10o_load_cli, wp14_export, wp14_initialize. WP-11
runs BEFORE WP-10o because the loader validates node names with
`node_names.check` (small local choice). One at a time, with
`CHARACTER_TEST_REQUIRE_DB=1` (on aarch64 also `CHARACTER_TEST_DSN`).

**Phase 2 gate, adapted (OB-41):** "book 1 cleaned" does not apply;
"initialize against pgserver with the real LLM produces trio baselines"
becomes "`initialize` against pgserver loads the 8 office baselines (no LLM
call); the user reads them"; "the hptest pack validates" becomes "the
ashiorid_office AND hptest packs still load after export" (T14.9). The WP-11
live smoke test (`CHARACTER_LIVE_LLM=1`) runs on gx10.

## OB-41 test list for WP-10o (written by the Phase 2 test author, not the planner)

Modelled on WP-10 items 1-3 and WP-14 items 6-8 and 13. Targets:
`app/character/generator/office_profiles.py` (+ `generator/__init__.py`) and
`scripts/load_office_profiles.py`.

1. Loading the 8 profiles gives row counts that match the files: 8
   characters, 8 baselines (v1, book/chapter 0/0 per Q8 (a)), 16 backstories
   (believed + truth), 16 agents.
2. A re-load with the same profile sha256s does nothing (no rows, no
   `updated_at` change; CLI exit 2).
3. A changed profile sha256 replaces only that character: version N+1 for it
   (older versions kept, plan §6 "versioned, never modified"), the others
   untouched.
4. `truth` never reaches `believed`: separate rows, and the truth text is in
   neither the believed row nor the baseline profile nor its nodes.
5. Every `backstory_nodes[].name` is validated by `node_names.check`; one
   bad file means nothing is written (CLI exit 1, before connecting), and
   every bad file is reported.
6. `retains_fragments` is true for all 8.
7. `character_agents` has `char:<slug>` AND the seat worker id `tuber_N`
   (app/office/roles.py SEAT; matches campaign.yaml `seats`).
8. `--dry-run` writes nothing (loader and CLI).
9. An avatar change in `cast/<slug>.yaml` updates `avatar_params` only, with
   no new baseline (so a later export never clobbers OB-20 params).
10. The CLI exits 3 on an unmigrated database.

## Test list -> test name mapping

### WP-10o (tests/character/)

- T10o.1 -> test_character_office_profiles_db.py::test_first_load_row_counts_match_the_eight_files (+ test_character_office_profiles.py::test_read_profiles_reads_the_eight_files_and_skips_underscore_files, ::test_read_profiles_of_an_empty_directory_raises, ::test_name_and_aliases_come_from_identity; CLI test_character_load_office_profiles.py::test_cli_loads_eight_then_second_run_is_nothing_to_do)
- T10o.2 -> test_character_office_profiles_db.py::test_reload_with_the_same_shas_does_nothing (+ CLI exit 2 in the same CLI test)
- T10o.3 -> test_character_office_profiles_db.py::test_changed_profile_sha_replaces_only_that_character (+ test_character_office_profiles.py::test_profile_sha256_follows_content_and_ignores_crlf; CLI ::test_cli_changed_profile_exits_0_and_adds_one_version)
- T10o.4 -> test_character_office_profiles_db.py::test_truth_never_reaches_believed (+ test_character_office_profiles.py::test_record_splits_believed_and_truth_and_profile_has_no_backstory)
- T10o.5 -> test_character_office_profiles.py::test_bad_node_name_is_rejected_with_its_path_and_reason, ::test_read_profiles_collects_errors_from_every_file; test_character_office_profiles_db.py::test_invalid_profile_writes_nothing; CLI ::test_cli_invalid_profile_exits_1_before_connecting
- T10o.6 -> test_character_office_profiles_db.py::test_retains_fragments_is_true_for_all_eight (+ test_character_office_profiles.py::test_all_eight_repo_profiles_validate_clean)
- T10o.7 -> test_character_office_profiles_db.py::test_character_agents_seeded_with_char_slug_and_seat_worker_id (+ test_character_office_profiles.py::test_agent_ids_are_char_slug_and_the_seat_worker_id)
- T10o.8 -> test_character_office_profiles_db.py::test_dry_run_writes_nothing; CLI ::test_cli_dry_run_writes_nothing
- T10o.9 -> test_character_office_profiles_db.py::test_cast_avatar_change_updates_avatar_params_without_a_new_baseline
- T10o.10 -> test_character_load_office_profiles.py::test_cli_unmigrated_database_exits_3
- (D-23 supporting) test_character_load_office_profiles.py::test_cli_help_works_from_any_cwd

### WP-11 (tests/character/)

- T11.1 -> test_character_llm.py::test_request_body_has_format_json_think_false_and_profile_model (+ ::test_json_llm_uses_the_config_profile_and_base_url)
- T11.2 -> test_character_llm.py::test_invalid_json_is_retried_and_second_attempt_succeeds (+ ::test_non_object_json_is_retried)
- T11.3 -> test_character_llm.py::test_shape_error_is_retried_with_the_error_in_the_user_message
- T11.4 -> test_character_llm.py::test_retries_run_out_and_raise_llm_error (+ ::test_http_errors_are_retried_then_raise_llm_error)
- T11.5 -> test_character_embeddings.py::test_embed_batches_70_texts_as_3_calls_keeping_order (+ ::test_embed_empty_list_makes_no_call, ::test_embedder_uses_recall_embeddings_config)
- T11.6 -> test_character_embeddings.py::test_embed_rejects_a_response_with_the_wrong_count (+ ::test_embed_rejects_wrong_dimension_and_http_errors)
- T11.7 -> test_character_node_names.py::test_good_examples_pass (5 plan + 7 office names)
- T11.8 -> test_character_node_names.py::test_bad_examples_fail_with_a_reason_each (3 plan + 7 office) (+ ::test_non_string_or_empty_name_fails)
- T11.9 -> test_character_node_names.py::test_regex_bounds_one_word_fails_eight_pass_nine_fail
- T11.10 -> test_character_prompts.py::test_template_says_first_person_and_has_3_good_and_3_bad_examples, ::test_template_examples_agree_with_node_names_check (+ ::test_required_templates_exist)
- gate smoke -> test_character_llm.py::test_live_smoke_returns_valid_json (skipped unless CHARACTER_LIVE_LLM=1)

### WP-14 (tests/character/)

- T14.1-T14.5 -> not applicable (WP-12, WP-13 not applicable, OB-41)
- T14.6 -> test_character_office_profiles.py::test_all_eight_repo_profiles_validate_clean, ::test_bad_node_name_is_rejected_with_its_path_and_reason, ::test_validate_profile_reports_shape_id_and_seat_errors (9 cases)
- T14.7 -> test_character_office_profiles_db.py::test_new_version_moves_the_pointer_only_when_activate
- T14.8 -> test_character_office_profiles.py::test_avatar_params_come_from_the_cast_character_params, ::test_avatar_params_default_to_resolve_params_none_plus_configured_accent
- T14.9 -> test_character_export.py::test_office_round_trip_export_writes_nothing, ::test_export_writes_character_params_inline_and_office_pack_loads, ::test_export_into_hptest_and_pack_still_loads (+ ::test_roster_snippet_lists_each_seat_with_inline_params)
- T14.10 -> test_character_export.py::test_export_keeps_every_other_key_and_line, ::test_write_character_params_inserts_after_avatar_or_appends (+ ::test_export_dry_run_reports_but_writes_nothing)
- T14.11 -> test_character_initialize.py::test_initialize_dry_run_writes_nothing
- T14.12 -> test_character_initialize.py::test_initialize_twice_second_run_is_a_no_op_exit_2 (+ ::test_initialize_after_a_profile_change_exits_0)
- T14.13 -> test_character_initialize.py::test_initialize_seeds_character_agents_char_slug_and_seat (+ WP-10o T10o.7)
- (plan §5 supporting) test_character_initialize.py::test_initialize_without_profiles_is_precondition_exit_3, ::test_initialize_with_an_invalid_profile_fails_exit_1, ::test_main_registers_initialize

## Test corrections (each with its citation)

Citation "OB-41" = `.claude/prompts/ashiorid_office_build_plan.md`, Wave 5,
OB-41: "The pilot cast is the 8 office characters, not Harry, Ron and
Hermione. The book source stages are replaced by `scripts/load_office_profiles.py`,
which loads `profiles/*.yaml` into `characters` / `character_backstories` /
`character_baselines`. `retains_fragments: true` for all 8."

1. **WP-07..WP-10 (T07.*, T08.*, T09.*, T10.*) not written.** OB-41 (book
   source stages replaced). Replaced by the OB-41 test list T10o.1-10 above.
2. **WP-12, WP-13 and T14.1-T14.5 not applicable.** OB-41 plus
   `campaigns/ashiorid_office/profiles/_SCHEMA.md` ("backstory.believed:
   FIRST PERSON ... truth: GM-only"): the office backstories are authored,
   there is no book text to build a timeline or evidence from.
3. **T14.6** "bad node names are retried and then dropped" -> rejected with
   the path and reason, not retried: an authored profile has no LLM to
   retry, and dropping would silently lose authored content. The whole load
   refuses (ProfileError; CLI exit 1; initialize exit 1). OB-41 + plan §10
   ("A bad name is rejected").
4. **T14.7** "moves the pointer only when --activate" -> the loader's
   `activate` (default True; CLI `--no-activate`), tested as "pointer moves
   only when activate". OB-41 (the loader is the baseline stage for the
   office cast). See question P2-Q6.
5. **T14.8** "map_appearance returns resolve_params(None) + accent_color" ->
   `office_profiles.avatar_params(cast_doc, accent_color)`: the OB-20
   `character_params` from the cast file win; `resolve_params(None)` + the
   configured accent is the fallback (plan §9 v1 behaviour kept). OB-41 +
   the OB-20 status line in the build plan §6 (`app/character/avatar.py`
   map_appearance done, params written to the cast YAMLs, bc04460). File
   location question P2-Q1.
6. **T14.9** extended from campaigns/hptest to BOTH packs, plus "a fresh load
   then export is a no-op round trip" for the office cast. OB-41 + plan §1
   stage 7 validation ("app/campaign/pack.py still loads the pack").
7. **T14.11/T14.12** `initialize` runs the office profile load instead of
   "load source, then stages 4-6" (plan §5 job table). OB-41.
8. **T14.13** extended: `char:<slug>` AND `tuber_N`. OB-41 + `app/office/roles.py:40-49`
   (SEAT: office workers publish as tuber_N) + D-04 (char:<slug> kept).
9. **T11.7/T11.8** examples are plan §10's plus office ones (OB-41: the cast is
   the office). `knows-qui-llusions` (plan §10 "garbled") is not asserted: it
   passes every rule §10 specifies (question P2-Q2). The other three plan bad
   examples fail on the verb rule and are kept.
10. **T11.10** the templates carry office examples (OB-41) in a fixed,
    checkable format (`## Good node names` / `## Bad node names` bullets), and
    their bad examples are the mechanically detectable ones (misspelling is
    prose guidance, plan §10 "It can't be fully mechanical"). Only
    `summary_day.md` and `fragment.md` are written: the book generator
    templates (timeline, backstory, baseline) don't apply (OB-41).

## Questions for the user

- **P2-Q1 v4 `generator/avatar.py` (file location).** Plan §9 puts the
  appearance -> sliders seam at `app/character/generator/avatar.py:
  map_appearance(profile) -> dict` (defaults in v1, TODO Phase 4). OB-20
  already built the Phase 4 version at `app/character/avatar.py`
  (`map_appearance(profile, llm_client)`) and wrote `character_params` into
  all 8 cast YAMLs. Options: (a) no `generator/avatar.py`; the loader reads
  the cast `character_params` and falls back to `resolve_params(None)` +
  config accent (`office_profiles.avatar_params`); (b) a thin
  `generator/avatar.py` that wraps `character.avatar.map_appearance` plus the
  default fallback, called by the loader (only T14.8's import changes);
  (c) move `app/character/avatar.py` to `generator/avatar.py` (breaks
  `scripts/generate_office_avatars.py`, `tests/test_character_avatar.py` and
  the `character/__init__.py` exports). Recommendation: (a). The tests are
  written for (a).
- **P2-Q2 node_names banned list and "garbled" names.** Plan §10 names "a
  banned list" but not its contents. Chosen (in the spec): the office meta
  words of `scripts/validate_office_profiles.py:84-85` split into words, plus
  plurals / -ing forms: loop(s), looping, simulation, simulated, ai, npc,
  stream(s), streaming, script, scripted. "week", "reset" and "time" are NOT
  banned: real office nodes use them (tester `wants-one-green-week`). Plan
  §10's garbled example `knows-qui-llusions` passes every specified rule.
  Options: (a) accept that, garbling stays a prompt rule (recommended);
  (b) a heuristic (e.g. reject a word that starts with a doubled letter);
  (c) a dictionary check (needs a word list, against D-26).
- **P2-Q3 no `generator/baseline.py` (file location).** For the office cast
  the baseline stage IS the loader (validate, versioned write, activate), so
  it lives in `office_profiles.py`. Options: (a) keep it there
  (recommended); (b) split a `generator/baseline.py` (validate + write
  version N+1 + activate) that the loader calls: one more module and spec.
- **P2-Q4 who runs `db.migrate()`?** WP-06 has no `migrate` job, and
  `jobs.run_job` writes its jobs row before `job.run()`, so `initialize`
  cannot migrate an empty database; `scripts/load_office_profiles.py` exits 3
  on an unmigrated database. Options: (a) a `migrate` subcommand in main.py
  that runs outside the jobs-row path (recommended); (b) main.py migrates
  before every job; (c) the loader CLI migrates (non-dry-run). Blocks the
  Phase 2 gate on a real DB (OP-2), not the tests (they migrate first).
- **P2-Q5 where the profile sha256 lives.** `character_baselines.source_id =
  "office_profile:<sha256>"` (a free-text provenance column with no FK), not
  a `source_works` row or a new column. No schema change. Confirm, or pick a
  `002_*.sql` column.
- **P2-Q6 loader activation flag.** The plan's baseline generator used an
  opt-in `--activate`; the office loader activates by default and has
  `--no-activate` (the first load has no pointer to protect; revert, WP-21,
  moves it back). Confirm.
- **P2-Q7 (Q4 follow-up) `source:` block.** The loader and `initialize` find
  the profiles as `<cfg.pack>/profiles` (initialize `--pack DIR` overrides),
  so no config key was added. Recommendation: keep Q4 (a).
- **P2-Q8 WP-05 store gap found.** The frozen WP-05 tests never check that
  `insert_baseline` stores `source_id` / `created_by` (the spec says "INSERT
  the row"); the Phase 1 scratch store dropped them. WP-10o's idempotency
  keys on `source_id`, so a promoted store that drops them fails WP-10o at
  the store, which is not a WP-10o target. Fix if it happens: a WP-05 spec
  note + re-run, optionally one assertion in
  test_character_store_characters.py (a test correction citing the WP-05
  spec interface).
- **P2-Q9 (for Phase 3) "week" in the office content.** WP-22 T22.2 says the
  brief never contains "week". The believed backstories of ceo, engineer,
  tech_lead and tester contain "week" (tester twice), and the tester node
  statement for `wants-one-green-week` too; the brief includes the believed
  layer (plan §8 section 2). WP-22 needs a decision (word only banned in
  generated sections? or reword the profiles?).
- **P2-Q10 prompt template format.** `## System` / `## User` sections,
  string.Template `${name}` placeholders, listed in each file's header
  comment (summary_day: character_name, character_title, day, events,
  max_nodes; fragment: character_name, character_title, summaries, moments).
  The Phase 3 tests already read them this way
  (test_character_summaries.py:103-114, test_character_fragments.py). A
  Phase 3 spec that needs another placeholder should amend the by-hand
  template.

## Small local choices

- **Module location (proposed):** `app/character/generator/office_profiles.py`
  (pure read/validate + the DB load, one module, one spec; split pure / DB
  if the harness fails, playbook §2.4) + the thin CLI
  `scripts/load_office_profiles.py`. `app/character/generator/__init__.py`
  is a docstring-only package marker.
- `load_profiles` never commits (caller owns the transaction); validation of
  ALL files happens before any SQL; the CLI validates before connecting.
- Actions: created / updated / activated / avatar / unchanged. The change key
  is the profile file's `db.file_sha256` (CRLF-normalised); avatar params are
  compared separately (action "avatar", no new baseline version).
- Baseline `profile` = the profile doc minus `backstory` and
  `backstory_nodes`; `backstory_nodes` column = the authored list; backstory
  rows `{"text": ...}` with `evidence: []`; `created_by =
  "load_office_profiles"`; characters inserted with `status = "active"`.
- `name` = identity.full_name; `aliases` = (title, then the name's words not
  ending in "."): ceo ("CEO", "Graham", "Ellery"), party_member ("Party
  Member", "Penhale").
- Flags `retains_fragments` / `is_main` come from the profile (config mirrors
  them, per the WP-03 note); a test checks the config accent colours equal
  the cast `character_params` accent.
- Loader CLI exit codes mirror plan §5 (0 / 1 / 2 / 3); `--dry-run` exits
  as the real run would.
- llm.py: `complete_json(profile, system, user, shape, *, base_url,
  max_retries=2, client=None)` plus `JsonLLM(cfg.llm)`; no config loading
  inside llm.py; HTTP failures count as failed attempts; retries resend
  system + one user message with the errors appended.
- embeddings.py: `embed(texts, *, base_url, model, client=None, batch_size=32,
  dim=None)` plus `Embedder(cfg.recall.embeddings)` (callable); sorts by
  `index`; checks count and dim.
- node_names: fixed reason texts (contain "lowercase" / "verb" / "banned");
  `age` accepted and unused in v1.
- export: writes only when the params differ, never creates a cast file,
  roster sorted by seat; comment `# v4 export: app/character/generator/export.py`.
- initialize: `--pack DIR`, `--no-export`; `--character` ignored; exit 3 when
  `<pack>/profiles` is missing; opens the current week with
  `weeks.ensure_week`.
- summary_day.md node `kind` enum: fact, relationship, plan, feeling (plan
  silent; `week_knowledge_nodes.kind` is NOT NULL). fragment.md has a `name`
  field (a node name for the feeling, for `grew_from_fragment_id` later).
- Test file named `test_character_initialize.py`, not `..._jobs_initialize.py`,
  to stay clear of the Phase 3 `test_character_jobs_*` files.
- WP-specific fakes in `tests/character/fakes_generator.py`
  (RecordingTransport for httpx.MockTransport, pack copies, db_env for CLI
  subprocesses). `tests/character/pending.py` TARGET_WP was NOT edited (the
  tests pass `wp=` explicitly), to avoid clashing with the parallel agents.
  The parent may add: node_names.py / llm.py / embeddings.py -> WP-11;
  generator/office_profiles.py, scripts/load_office_profiles.py -> WP-10o;
  generator/export.py, jobs_initialize.py -> WP-14.
- **Satisfiability check (scratchpad only, nothing in the repo):** throwaway
  implementations of every Phase 2 target (scratchpad `p2impl/`, overlaid on
  the Phase 1 scratch implementations in a scratch copy of the repo) pass
  every Phase 2 test with `CHARACTER_TEST_REQUIRE_DB=1` on pgserver
  (tests/character: 153 passed, 10 skipped = Phase 3 pending + the live
  smoke). Two Phase 1 scratch gaps were patched in the scratch only: the
  scratch config lacked `llm.base_url` / `llm.max_retries` /
  `recall.embeddings` as an object / `pack` (all in the WP-03 spec), and the
  scratch store dropped `source_id` / `created_by` (P2-Q8).
- **Full suite after this session (real repo):** 4447 passed, 0 failed, 100
  skipped, 1 xfailed, exit 0 (`.venv/bin/python -m pytest -q`). +3 passed =
  the prompt structure tests; the skips include the Phase 3 agents' pending
  files written in parallel.

## Per-WP file lists (for "commit per WP")

- WP-11: `app/character/prompts/summary_day.md`, `app/character/prompts/fragment.md`,
  `tests/character/test_character_node_names.py`, `tests/character/test_character_llm.py`,
  `tests/character/test_character_embeddings.py`, `tests/character/test_character_prompts.py`,
  `tests/character/fakes_generator.py` (shared by WP-10o/WP-14; commit with WP-11),
  `tools/qwen_worker/specs/character_wp11_{node_names,llm,embeddings}.yaml`
- WP-10o: `tests/character/test_character_office_profiles.py`,
  `tests/character/test_character_office_profiles_db.py`,
  `tests/character/test_character_load_office_profiles.py`,
  `tools/qwen_worker/specs/character_wp10o_{office_profiles,load_cli}.yaml`
- WP-14: `tests/character/test_character_export.py`, `tests/character/test_character_initialize.py`,
  `tools/qwen_worker/specs/character_wp14_{export,initialize}.yaml`
- This file: `.claude/prompts/character_v4_tracker_phase2.md` (merge, then delete or keep).
