# Character v4: Phase 3a tracker additions (WP-15..WP-20)

Additions for `.claude/prompts/character_v4_build_status.md`, written by the
Phase 3a tests-and-specs session (OB-41 cloud session, 2026-09-28). Merge the
sections below into the tracker; this file does not edit it.

Session decision (user): **tests and specs only**. Frozen test files, harness
spec YAMLs and fakes were written; no implementation module was. The modules
are generated later on argyre with `tools/qwen_worker/runner.py run <spec>`.

## WP rows

| WP | Status | Spec(s), in run order | Runs / model | Gate result | Gate host |
|---|---|---|---|---|---|
| WP-15 contracts + attribution | tests-written | `character_wp15_bus_contracts.yaml` | not run | strict: ModuleNotFoundError character.bus_contracts / bus_attribution; default: 2 files skip | Linux cloud container (tests only) |
| WP-16 ingest | tests-written | `character_wp16_ingest.yaml`, `character_wp16_jobs_ingest.yaml`, `character_wp16_compose.yaml` (by hand per question 7 (a)) | not run | strict: ModuleNotFoundError character.ingest / character.jobs_ingest; default: 2 files skip | Linux cloud container (tests only) |
| WP-17 messages M1/M2 + logger | tests-written | `character_wp17_logger.yaml`, then `character_wp17_schema_copies.yaml` | not run | strict: ModuleNotFoundError migrate_partitioned; default: file skips; existing tests/test_message_logger.py unchanged and green | Linux cloud container (tests only) |
| WP-18 compaction | tests-written | `character_wp18_compaction.yaml` | not run | strict: ModuleNotFoundError character.compaction; default: skip | Linux cloud container (tests only) |
| WP-19 summaries + daily job | tests-written | `character_wp19_summaries.yaml`, `character_wp19_jobs_daily.yaml` | not run | strict: ModuleNotFoundError character.summaries / character.jobs_daily; default: 2 files skip | Linux cloud container (tests only) |
| WP-20 fragments + weekly reset | tests-written | `character_wp20_fragments.yaml`, `character_wp20_jobs_weekly.yaml` | not run | strict: ModuleNotFoundError character.fragments / character.jobs_weekly; default: 2 files skip | Linux cloud container (tests only) |

Suite after this session (Linux cloud container, `.venv/bin/python -m pytest -q`):
**4447 passed, 0 failed, 103 skipped, 1 xfailed, exit 0**. The 103 skips are
the 74 of the baseline, 10 Phase 3a pending files, and the other sessions'
new pending files. No failures.

**Satisfiability check (scratchpad only, nothing in the repo):** throwaway
implementations of every Phase 3a target (plus fuller throwaway Phase 1
stores/config and a jobs.py that calls `add_arguments`), in a scratch copy of
the tree, pass all 139 Phase 3a tests plus the 30 existing message-logger
tests with `CHARACTER_TEST_REQUIRE_DB=1` on pgserver (1 skip: the reference-SQL
check, whose docs/ file isn't in the scratch copy). A second scratch run
without docs/ and with the old Dockerfile (the `character_wp17_logger` sandbox)
passes with the 3 schema-copy checks skipped, which confirms the WP-17 spec split.

### Files (all new unless marked)

- WP-15: `tests/character/test_character_bus_contracts.py`,
  `tests/character/test_character_bus_attribution.py`,
  `tools/qwen_worker/specs/character_wp15_bus_contracts.yaml`
- WP-16: `tests/character/test_character_ingest.py`,
  `tests/character/test_character_jobs_ingest.py`,
  `tools/qwen_worker/specs/character_wp16_ingest.yaml`,
  `tools/qwen_worker/specs/character_wp16_jobs_ingest.yaml`,
  `tools/qwen_worker/specs/character_wp16_compose.yaml`
- WP-17: `tests/test_message_logger_v4.py`,
  `tools/qwen_worker/specs/character_wp17_logger.yaml`,
  `tools/qwen_worker/specs/character_wp17_schema_copies.yaml`
- WP-18: `tests/character/test_character_compaction.py`,
  `tools/qwen_worker/specs/character_wp18_compaction.yaml`
- WP-19: `tests/character/test_character_summaries.py`,
  `tests/character/test_character_jobs_daily.py`,
  `tools/qwen_worker/specs/character_wp19_summaries.yaml`,
  `tools/qwen_worker/specs/character_wp19_jobs_daily.yaml`
- WP-20: `tests/character/test_character_fragments.py`,
  `tests/character/test_character_jobs_weekly.py`,
  `tools/qwen_worker/specs/character_wp20_fragments.yaml`,
  `tools/qwen_worker/specs/character_wp20_jobs_weekly.yaml`
- Shared by WP-16..WP-20: `tests/character/fakes_runtime.py` (FakeLLM,
  FakeEmbed, FakeProducer, FakeConsumer, FakeConn, FakeSourceConn, bus envelope
  builders, raw-SQL seeders, the frozen legacy `messages` DDL and the M2 shape,
  the `migrated_dsn` fixture). Commit it with WP-16 (its first user).
- Not touched: `tests/character/pending.py`, `conftest.py`, `fakes.py`, the
  tracker, `services/message-logger/logger.py`, `docs/sql/02_create_tables.sql`,
  `docker-compose.yml`.

### Spec run order and gates

`wp15_bus_contracts`; `wp16_ingest`, `wp16_jobs_ingest` (+ the compose block by
hand); `wp17_logger`, `wp17_schema_copies` (+ `docs/message_logger.md` by hand:
M1, the M2 operator steps, "never run on production from a test");
`wp18_compaction`; `wp19_summaries`, `wp19_jobs_daily`; `wp20_fragments`,
`wp20_jobs_weekly`. One harness job at a time, with `CHARACTER_TEST_REQUIRE_DB=1`
(and `CHARACTER_TEST_DSN` on aarch64). WP-19/20 need WP-11 promoted
(`llm.complete_json`, `embeddings.Embedder`, `node_names.check`, the two
templates). The job specs each re-emit `services/character-updater/main.py`
with one more JOBS entry (the WP-06 registry), so they must run in order.

WP-16 manual gate (after promote, gx10/argyre, test DB only): publish 3 fake
`character_say` messages and see >= 3 rows.

## Test list -> test name mapping

### WP-15

- T15.1 -> test_character_bus_contracts.py::test_builders_use_build_message_and_the_plan_payload_shapes (+ test_builders_pass_correlation_ids_through)
- T15.2 -> test_character_bus_contracts.py::test_builder_rejects_unknown_kind_or_empty_text (6 cases)
- T15.3 -> test_character_bus_attribution.py::test_payload_character_comes_first
- T15.4 -> test_character_bus_attribution.py::test_char_sender_gives_its_slug
- T15.5 -> test_character_bus_attribution.py::test_char_live_sender_gives_none
- T15.6 -> test_character_bus_attribution.py::test_other_senders_give_none, ::test_non_dict_or_payloadless_messages_are_handled (+ test_bus_attribution_imports_only_the_stdlib, D-04 "pure", the logger image copies one file)

### WP-16

- T16.1 -> test_character_ingest.py::test_agent_thinking_from_mapped_sender_gives_one_self_row (char:engineer and tuber_3)
- T16.2 -> ::test_unmapped_agent_thinking_gives_no_rows_and_bumps_skip_count
- T16.3 -> ::test_character_say_rows_for_present_characters_only
- T16.4 -> ::test_present_character_and_addressees_union_is_deduplicated
- T16.5 -> ::test_type_outside_allowlist_is_dropped_before_payload_is_parsed
- T16.6 -> ::test_week_and_day_come_from_body_timestamp_and_pre_epoch_is_skipped
- T16.7 -> ::test_malformed_message_raises_malformed_message (route level, 9 cases) + ::test_malformed_message_is_logged_at_error_and_skipped_loop_continues
- T16.8 -> ::test_offsets_are_committed_only_after_the_db_commit
- T16.9 -> ::test_redelivery_of_the_same_batch_inserts_zero_rows (integration)
- T16.10 -> ::test_batching_flushes_at_200_messages_or_2_seconds
- T16.11 -> ::test_consumer_is_built_earliest_no_autocommit_group_character_ingest (+ the never-raising deserializer)
- T16.12 -> test_character_jobs_ingest.py::test_backfill_reads_a_cursor_and_routes_the_same_way (+ ::test_backfill_dry_run_writes_nothing, ::test_backfill_without_since_exits_3)
- OB-41 + user decision 2026-09-28 (item 8) -> test_character_ingest.py::test_party_member_is_a_full_character_his_own_thoughts_are_self_rows, ::test_a_line_spoken_by_the_party_member_routes_like_anyone_elses
- registration -> test_character_jobs_ingest.py::test_main_registers_the_ingest_job

### WP-17 (`tests/test_message_logger_v4.py`, integration)

- T17.1 -> test_startup_ddl_on_legacy_table_adds_character_and_indexes_idempotently
- T17.2 -> test_insert_fills_character_with_character_for_message
- T17.3 -> test_shape_is_plain_before_m2_and_partitioned_after
- T17.4 -> test_partitioned_insert_dedupes_on_id_and_timestamp
- T17.5 -> test_ensure_partitions_creates_today_through_today_plus_14_idempotently
- T17.6 -> test_migrate_dry_run_changes_nothing
- T17.7 -> test_migration_copies_every_row_keeps_legacy_and_refuses_a_second_run
- T17.8 -> test_m1_statements_appear_verbatim_in_logger_py, test_m1_statements_appear_verbatim_in_docs_sql (skips where docs/ is absent: the harness sandbox)
- plan §3.4 Dockerfile line -> test_logger_dockerfile_copies_bus_attribution (same skip rule)
- frozen-copy check -> test_reference_m1_matches_the_frozen_statements

### WP-18

- T18.1 -> test_character_compaction.py::test_noisy_types_older_than_n_hours_are_deleted
- T18.2 -> ::test_plain_old_rows_move_in_batches_into_created_archive
- T18.3 -> ::test_partitioned_old_partitions_are_detached_and_attached_to_archive
- T18.4 -> ::test_partitioned_old_default_rows_move_to_archive_default
- T18.5 -> ::test_partition_age_comes_from_the_name (11 cases), ::test_partition_not_matching_the_pattern_is_ignored
- T18.6 -> ::test_a_second_run_changes_nothing (plain, partitioned)
- T18.7 -> ::test_dry_run_reports_counts_and_changes_nothing (plain, partitioned)
- T18.8 -> ::test_skipped_with_precondition_when_ingest_lag_exceeds_max (+ ::test_ingest_lag_counts_ingest_type_messages_after_the_last_ingested)

### WP-19

- T19.1 -> test_character_summaries.py::test_day_with_events_gives_one_summary_and_up_to_10_valid_nodes (+ ::test_prompt_is_rendered_from_the_summary_day_template, ::test_day_label_and_event_rendering)
- T19.2 -> ::test_day_with_no_events_says_quiet_day_without_an_llm_call
- T19.3 -> ::test_rerun_for_the_same_character_and_day_is_a_no_op
- T19.4 -> test_character_jobs_daily.py::test_waits_up_to_catchup_wait_then_marks_summaries_partial (+ ::test_no_wait_when_ingest_has_passed_midnight)
- T19.5 -> ::test_summaries_then_compaction_and_precondition_keeps_summaries_exit_3
- T19.6 -> test_character_summaries.py::test_only_own_self_events_plus_present_events_are_summarised
- T19.7 -> test_character_jobs_daily.py::test_character_flag_limits_the_run_to_that_character
- OB-41 + user decision 2026-09-28 (item 8) -> test_character_summaries.py::test_party_member_summary_includes_his_own_thoughts_and_what_he_saw
- playbook §2.3 dry-run -> test_character_jobs_daily.py::test_dry_run_writes_nothing_and_calls_no_llm
- registration -> ::test_main_registers_daily_maintenance

### WP-20

- T20.1 -> test_character_jobs_weekly.py::test_steps_run_in_order
- T20.2 -> ::test_crash_in_archive_then_rerun_skips_steps_1_2_and_finishes (+ test_character_fragments.py::test_select_skips_a_character_that_already_has_its_fragments_for_the_week)
- T20.3 -> test_character_fragments.py::test_character_without_retains_fragments_gets_no_fragments
- T20.4 -> ::test_fragment_has_first_person_gist_embedded_lead_up_and_hooks (+ ::test_unknown_anchor_is_retried_once_then_skipped, ::test_prompt_lists_moments_as_id_colon_text)
- T20.5 -> ::test_lead_up_comes_from_events_before_the_moment_in_order
- T20.6 -> test_character_jobs_weekly.py::test_archive_hides_week_w_nodes_from_the_next_week
- T20.7 -> ::test_week_w_plus_1_is_open_with_bounds_from_the_clock
- T20.8 -> ::test_refresh_publishes_one_character_refresh, ::test_refresh_mode_none_publishes_nothing
- T20.9 -> ::test_before_sunday_midnight_is_not_due_unless_force
- T20.10 -> ::test_dry_run_writes_nothing_and_publishes_nothing
- registration -> ::test_main_registers_weekly_reset

## Test corrections (each needs a plan citation)

All cite the OB-41 override (`.claude/prompts/ashiorid_office_build_plan.md`
OB-41: "The pilot cast is the 8 office characters, not Harry, Ron and
Hermione"; "retains_fragments: true for all 8"; "The Party Member's experience
comes only from visibility: present events").

- WP-15 T15.1-T15.6: office slugs (engineer, tester, ceo, office_manager) and
  campaign `ashiorid_office` instead of hp / harry / ron.
- WP-15 T15.6: `tuber_3` and `office_clock` added to "other senders give None"
  (office seats publish from `tuber_N`, app/office/roles.py:40-49; the clock is
  not a character, app/office/protocol.py:55). D-04 is unchanged; see question P3a-1.
- WP-16 T16.1: run for both `char:engineer` and `tuber_3` (the agents map holds
  both, per the Phase 2 seeding), still "one row, visibility self".
- WP-16 T16.3: present [engineer, tester, office_clock] instead of [harry, ron,
  gm]; the office clock is the GM-equivalent non-character.
- WP-16 T16.12 / WP-19 T19.7: office slugs; `--character harry` -> `--character engineer`.
- WP-16 + WP-19 (new, OB-41): the Party Member tests
  (test_party_member_experience_is_only_present_events,
  test_party_member_summary_is_built_from_present_events_only). The fixtures
  map no agent id to him (question P3a-3, recommendation (a)).
  **Superseded 2026-09-28 (user decision item 8):** the Party Member is a full
  character; the fixtures map `char:party_member` and `tuber_7`, and the two
  tests are now test_party_member_is_a_full_character_his_own_thoughts_are_self_rows
  (+ test_a_line_spoken_by_the_party_member_routes_like_anyone_elses) and
  test_party_member_summary_includes_his_own_thoughts_and_what_he_saw. Logged
  in the main tracker's test corrections.
- WP-20 T20.3: every office character retains fragments, so the test uses a
  synthetic `visitor` fixture row with retains_fragments false.
- WP-20 T20.4: "a harry fragment" -> an engineer fragment.
- WP-20 T20.9 / T20.1: the office epoch 2026-09-27 (week 1 = Sep 27..Oct 3),
  so the week-1 reset is `--at 2026-10-04T00:02-04:00` (plan §13 crontab: 00:02).

## Questions for the user (Phase 3a)

P3a-1. **`character_for_message` and office seat senders (bus contract, D-04).**
   Office workers publish `agent_thinking` from `tuber_N`
   (app/office/roles.py:40-49), so under D-04 the logger's `messages.character`
   stays NULL for every office thought. Options: (a) keep D-04 as written
   (payload.character, else `char:<slug>`, else None); ingest still resolves
   `tuber_N` through `character_agents` (plan §3.3 already routes
   agent_thinking by the agents map, so WP-16 needs no contract change);
   (b) add an optional static seat map to `character_for_message(msg,
   seats=None)` that the logger loads from env, which changes the contract and
   the logger's inputs; (c) have the office publish its thoughts as
   `char:<slug>`, which changes the office worker id convention.
   **Recommendation: (a).** `messages.character` only feeds screens and
   compaction; the memory path doesn't read it. The WP-15 tests encode (a).

P3a-2. **The office's spoken lines never reach ingest.** The allowlist is
   agent_thinking / character_say / scene_event (plan §3.3), but the office
   speaks through its own protocol types (directive, functional_plan,
   technical_plan, test_request, status_report, wrap_up...,
   app/office/protocol.py:40-50), and nothing in app/ or services/ publishes
   `character_say` or `scene_event` (grep). So office characters would remember
   only their own thoughts. The Party Member, who never thinks aloud or speaks,
   would remember nothing: OB-41 needs him to have `present` events. Options:
   (a) the office publishes a `character_say` mirror (built with
   `bus_contracts.character_say`, `present` = the seats on shift) for every
   spoken office line, plus `scene_event` for clock beats. That is office-side
   work (a small OB step after WP-15) and leaves the v4 contracts unchanged;
   (b) add the office protocol types to `ingest.types` with an office routing
   rule, which changes a config value and the route() contract;
   (c) leave it until WP-24's live driver, which doesn't drive the office.
   **Recommendation: (a).** Tests and specs are written for the plan's
   contract only.

P3a-3. **How "the Party Member's experience comes only from present events" is
   enforced.** His worker still calls the LLM for silent stage actions
   (config/workers/office/party_member.yaml:46), and every LLM call publishes
   `agent_thinking` from `tuber_7` (app/agent_metrics.py:240-261). If the
   Phase 2 seeding maps `tuber_7` / `char:party_member` in `character_agents`,
   route() gives him `self` rows. Options: (a) the seeding maps NO agent id to
   party_member, so his thoughts count as `unmapped`; no code or contract
   change; (b) a config flag `characters.<slug>.self_events: false` read by the
   ingest job (a config key change); (c) filter by slug in route() (a hardcoded
   exception, which we don't want). **Recommendation: (a).** Tell the Phase 2
   seeding (WP-14 / load_office_profiles) to skip party_member's agent ids. The
   WP-16 fixtures encode (a).
   **Resolved (user, 2026-09-28, item 8), in favour of Phase 2:** "The Party
   Member is a full character who just has no lines at the moment."
   character_agents seeds both `char:party_member` and `tuber_7`; a `tuber_7`
   sender maps to party_member and his agent_thinking routes as `self`. No
   "never self" rule, no config flag, no slug filter. The WP-16 / WP-19 / WP-25
   fixtures and tests were updated (main tracker, test corrections).

P3a-4. **Two `character_refresh` messages per office reset, and the office
   drops the v4 one.** app/office/weekly_reset.py runs its own
   `character_refresh` step (office week numbering, 0-based, with
   `closing_week` and `branch`) BEFORE it calls the v4 hook. The v4
   `weekly-reset` refresh step then publishes a second one (from
   `character-updater`, v4 week, §3.2 shape). Office workers handle
   `character_refresh` and log `rank_violation` at ERROR for any sender that
   isn't the clock (app/agent_handlers/office.py:855-857, CLOCK_SENDERS in
   app/office/protocol.py:58), so every seat would log an ERROR each Sunday.
   The office refresh also arrives before the v4 archive, so a brief cache
   rebuilt on it would be stale until its TTL. Options:
   (a) set `reset.refresh_mode: none` in config/character.yaml for the office
   campaign (a config value change), and move the office's STEP_V4 before
   STEP_REFRESH (a change to OB-31 code), so the one office refresh comes after
   the archive; (b) keep both, and add `character-updater` to the office's
   accepted refresh senders (office code); (c) keep both and accept the ERROR
   lines. **Recommendation: (a).** The WP-20 tests cover both modes (push
   publishes one; none publishes nothing), so either answer needs no test change.
   **Resolved (user, 2026-09-28, item 5):** "The v4 weekly reset is the
   authoritative one; the office campaign aligns with it." NOT (a): for the
   office campaign `reset.refresh_mode` stays push. The v4 WP-20 weekly-reset
   publishes the only `character_refresh` (from "character-updater", payload
   {campaign, week, characters, reason}); the office weekly reset
   (app/office/weekly_reset.py) runs its repo steps first and then invokes the
   v4 `weekly-reset` job, and drops its own refresh (office-side change, the
   office agent's). No WP-20 test change; the WP-20 spec notes say so.

P3a-5. **Where the office -> v4 `weekly-reset` hook adapter lives.**
   `WeeklyReset(v4_hook=...)` calls `v4_hook(week, closing_week, campaign)`
   with office week numbers (app/office/weekly_reset.py:268, :516-520). With
   question 3 answer (a) (convert at the hook, v4 = office + 1), the adapter is
   `run_job(WeeklyResetJob(), ["--campaign", campaign, "--at",
   clock.week_bounds(week + 1)[0].isoformat()])`. That runs v4 week W =
   closing_week + 1 and returns the job's detail. The plan names no file for it.
   Options: (a) a follow-up spec after WP-20 adds
   `jobs_weekly.office_v4_hook(week, closing_week, campaign) -> dict`, with its
   own test; (b) put it on the office side (app/office/, OB code).
   **Recommendation: (a).** It sits next to the job whose CLI it wraps. It is
   not part of the frozen WP-20 tests.
   **Resolved (user, 2026-09-28, item 5):** Q3 has no +1 conversion: the office
   adopts the v4 LoopClock numbering (week 1 starts at the epoch Sunday
   2026-09-27). The office side invokes the v4 job itself
   (`run_job(WeeklyResetJob(), ["--campaign", campaign, "--at", <Sunday 00:00
   NY of the week that opens>])`), so no `jobs_weekly.office_v4_hook` and no
   follow-up v4 spec are needed.

P3a-6. **The reference M2 DDL drops two logger columns (a plan claim about
   existing code is false).** v4_reference_messages.sql M2 builds
   `messages_new` without `correlation_id` / `causation_id`, but the logger
   creates and fills both (services/message-logger/logger.py:29-30, 36-38,
   INSERT_SQL :91-93). Following the reference would drop them for every row.
   Options: (a) the migration keeps them (they are added to messages_new);
   (b) follow the reference and lose the columns. **Recommendation: (a).** The
   WP-17 tests and spec encode (a). The M2 partitioned indexes also get new
   names (`idx_messages_p_*`), because the legacy table keeps the old
   `idx_messages_*` names and `CREATE INDEX IF NOT EXISTS` would silently skip
   them.

P3a-7. **A plain `messages_archive` created before M2 blocks partitioned
   compaction.** If compaction B-plain runs before M2, `messages_archive` is a
   plain table, and after M2 whole partitions can't be ATTACHed to it. Options:
   (a) compaction raises `CompactionError` with the fix, and the M2 operator
   steps in docs/message_logger.md say to rename the plain archive to
   `messages_archive_legacy` right after M2 (compaction then creates the
   partitioned archive); (b) migrate_partitioned also converts the archive
   (bigger M2, more rows copied during downtime). **Recommendation: (a).** The
   spec encodes (a); no test covers it.

Also: the WP-16 `character-ingest` compose block has the same problem as
question 7 (the compose file is too big for the harness). Its spec is written
and flagged, and following question 7's recommendation (a), the block is added
by hand. **Confirmed (user, 2026-09-28, item 6):** by hand.

## Small local choices (Phase 3a, per playbook §2.5)

- P3a-L1 `bus_attribution`: an empty-string `payload.character` counts as absent
  and falls through to the sender (D-04 says "a string"; "" is useless in the
  column). bus_contracts builders reject blank text too, and `ContractError`
  subclasses ValueError. The builders take `correlation_id`/`causation_id` so a
  reply stays in its chain.
- P3a-L2 ingest: route() raises `MalformedMessage` and the loop catches any
  route error per message. The Kafka value deserializer returns None instead of
  raising (a raising deserializer stalls the partition). `ingest_status` gets
  per-batch deltas: messages_seen = batch size, rows_written = inserted,
  unmapped = the batch's unmapped count, last_message_ts = newest routed ts. A
  failed flush rolls back, keeps the batch and re-raises (compose restarts the
  process; Kafka redelivers). The agents map is loaded at job start, so a new
  character needs an ingest restart. The ingest job lives in
  `app/character/jobs_ingest.py` (the plan names no file). `--dry-run`: the
  backfill routes and counts only; the live consumer under --dry-run exits 2.
  Missing `--since` exits 3; missing KAFKA_BOOTSTRAP_SERVERS exits 3. The
  virtualtubers DB connection (`connect_messages`, POSTGRES_* env via
  `messages_db_env_prefix`) lives in ingest.py, the first module that needs it;
  compaction takes a connection and never connects.
- P3a-L3 "every active character" (plan §5) = the campaign's characters with
  status <> 'retired'. Nothing in the plan sets 'active' (WP-05's upsert defaults
  to 'draft').
- P3a-L4 weekly-reset target week: W = the clock week of `--at` minus 1 (the
  week that ended); with `--force`, the current week. W < 1 is "not due" and
  exits 2 without writing. "All steps done" exits 2.
- P3a-L5 the v4 `character_refresh` carries week W+1 (the week that opened),
  the same convention as the office's refresh. Step open_next also sets week W
  to `closed` (testctl reset-undo sets it back to open, plan §5.2).
- P3a-L6 `daily_summaries` has no partial column, so the summary JSON is
  `{"summary", "partial", "quiet", "nodes"}`. The quiet-day text is "A quiet
  day: nothing happened that I took part in or saw." Node names: one retry
  listing the rejected names, then drop (plan §10). No edges are written in v1.
  Prompt lines: `HH:MM [self] (my own thought) text` / `HH:MM [present]
  speaker: text`. character_title = the active baseline's
  `profile.identity.title`, else the title-cased slug.
- P3a-L7 fragments: the anchor must be one of the character's week-W event ids,
  and the gist must be first person (`I` / me / my / mine / myself). One retry
  with the errors appended, then skip. The lead-up is the <= lead_up_beats
  events before the anchor. hooks = the reply's entities/places/objects/tone
  plus weekday (lowercase) and hour of the anchor in NY. fragment.md's `name`
  field has no column, so it stays in raw_response only. Moment texts are
  truncated to 300 chars in the prompt.
- P3a-L8 idempotency of select_fragments: `store.fragments.create_fragment`
  commits on its own (WP-05 interface), which releases run_step's FOR UPDATE
  mid-step. So select_fragments skips a character that already has its
  `fragments_per_week` fragments for W, and a crash-and-rerun can't double
  them. The advisory lock still serialises the job.
- P3a-L9 WP-17: tests/test_message_logger_v4.py loads the `pg` fixtures from
  tests/character/conftest.py by file path (it lives in tests/, per the
  playbook). The docs/ and Dockerfile checks skip only where docs/ is absent
  (the harness sandbox). The legacy `messages` DDL is frozen in fakes_runtime.py
  (logger.py:21-38 at 324be04), so tests don't depend on the file WP-17 rewrites.
  The logger's hourly partition upkeep runs on message arrival (the consumer
  iterator blocks when idle; 14 days ahead covers that).
- P3a-L10 each job spec (WP-16/19/20) re-emits services/character-updater/main.py
  with one more JOBS entry (the WP-06 registry), and each job test file checks
  `main.py <job> --help`.

## Public interfaces other WPs depend on (WP-21..WP-25)

- `character.ingest.route(msg, agents, slugs, clock, *, types=ALLOWED_TYPES, counts=None) -> list[dict]`
  (rows keyed by store.events.EVENT_COLUMNS, ready for `store.events.insert_many`);
  `ingest.load_maps(conn, campaign=None) -> (agents, slugs)`;
  `ingest.IngestLoop(consumer, conn, clock, *, agents, slugs, ...)` with `.add()`, `.flush()`.
- `character.jobs_daily.DailyMaintenanceJob(complete=None, messages_connect=None, check_name=None, monotonic=None, sleep=None, poll_s=15.0)`,
  name "daily-maintenance", run via `character.jobs.run_job(job, argv, cfg=, connect=)`.
  In the e2e: pass fakes for `complete` and `messages_connect` (compaction runs on
  it; a pgserver DB with the legacy messages table works), and either a FakeClock
  for monotonic/sleep or `update_ingest_status(last_message_ts >= midnight)`
  first, or it waits up to 600 s.
- `character.jobs_weekly.WeeklyResetJob(complete_summary=None, complete_fragment=None, embed=None, producer=None, check_name=None)`,
  name "weekly-reset", flag `--force`, `STEPS = ("close_saturday",
  "select_fragments", "archive", "open_next", "refresh")`. The fragment FakeLLM
  must return an `anchor_event_id` from the "<event_id>: <text>" moment lines,
  with a first-person gist.
- `character.summaries.summarise_day(conn, clock, character, day, *, complete, check_name=None, partial=False, prompts_dir=None, llm_model=None) -> dict`,
  `summaries.campaign_characters(conn, campaign, slug=None)`, `summaries.make_complete(cfg, profile)`, `summaries.render_prompt(name, values)`.
- `character.fragments.select_fragments(conn, clock, character, week, *, complete, embed, n=1, lead_up_beats=8, embed_model=None, prompts_dir=None, llm_model=None) -> list[str]`.
- `character.compaction.run(conn, ccfg, *, now, ingest_lag=0, max_lag=1000, dry_run=False) -> dict`, `compaction.ingest_lag(conn, last_message_ts, types) -> int`, `partition_name(day)`, `partition_day(name)`, `detect_shape(conn)`.
- `character.bus_contracts.character_say / scene_event / character_refresh`,
  `character_agent_id(slug)`, `live_agent_id(campaign)`, `SCENE_KINDS`,
  `REFRESH_REASONS`, `ContractError`. `bus_attribution.character_for_message(msg)`.
