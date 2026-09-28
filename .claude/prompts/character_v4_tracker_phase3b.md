# Character v4: Phase 3b tracker additions (WP-21..WP-25)

Written by the OB-41 Phase 3b tests-and-specs session (2026-09-28, Linux cloud
container). The parent merges these into `.claude/prompts/character_v4_build_status.md`.
Session decision (user): "tests and specs only". No implementation module was
written in the repo; qwen3.8:27b generates them later on argyre via
`tools/qwen_worker/runner.py run <spec>`.

## WP rows

| WP | Status | Spec(s) | Runs / model | Gate result | Gate host |
|---|---|---|---|---|---|
| WP-21 testctl | tests-written | `character_wp21_testctl.yaml` (testctl.py + main.py) | not run | strict: ModuleNotFoundError character.testctl (2 files); default: 2 files skip. Scratch throwaways: 29/29 pass | Linux cloud container (tests only) |
| WP-22 brief | tests-written | `character_wp22_brief.yaml` | not run | strict: ModuleNotFoundError character.brief (2 files); default: skip. Scratch: 16/16 | Linux cloud container (tests only) |
| WP-23 recall + harness | tests-written | `character_wp23_recall.yaml`, `character_wp23_recall_harness.yaml` (in that order) | not run | strict: ModuleNotFoundError character.recall / .recall_harness; default: skip. Scratch: 15/15. gx10 real-embedding calibration still to do (gate) | Linux cloud container (tests only) |
| WP-24 live + story jobs | tests-written | `character_wp24_live.yaml`, `character_wp24_jobs_story.yaml` (jobs_story.py + main.py), `character_wp24_compose.yaml` (FLAGGED: hand step, Q7 + P3b-Q4) | not run | strict: ModuleNotFoundError character.live / .jobs_story; default: skip. Scratch: 11/11 | Linux cloud container (tests only) |
| WP-25 e2e two weeks | tests-written | none (test only) | n/a | strict: ModuleNotFoundError character.jobs; default: skip (guarded on 15 targets). NOT provable here: needs Phase 2 (initialize, office_profiles, node_names, embeddings) and Phase 3a (ingest, summaries, jobs_daily, fragments, jobs_weekly, bus_contracts) | not run |

Full suite after this session (real repo, other agents' Phase 2/3a files present):
`.venv/bin/python -m pytest -q` -> 4447 passed, 0 failed, 103 skipped, 1 xfailed, exit 0.
The 9 Phase 3b files add 9 module-level skips, no failures.

**Passability proof (scratch only):** throwaway implementations of testctl,
brief, recall, recall_harness, live and jobs_story, on top of the Phase 1
throwaways and a WP-15 bus_contracts stub written to
`character_wp15_bus_contracts.yaml`'s signatures, all under
`/tmp/claude-0/.../scratchpad/p3b/repo` (never in the repo): 71/71 Phase 3b
tests pass with `CHARACTER_TEST_REQUIRE_DB=1` (plus the 7 WP-06 tests). The
e2e file stays skipped there (Phase 2/3a code absent). Its recall numbers were
checked separately: with the real config/character.yaml recall parameters and
the fake embedder, the week-2 replay goes unease at beat 6 and surfaces at beat
8 of the replay day.

## Files (per WP)

- WP-21: `tests/character/test_character_testctl.py`, `tests/character/test_character_testctl_cli.py`,
  `tools/qwen_worker/specs/character_wp21_testctl.yaml`
- WP-22: `tests/character/test_character_brief.py`, `tests/character/test_character_brief_persona.py`,
  `tools/qwen_worker/specs/character_wp22_brief.yaml`
- WP-23: `tests/character/test_character_recall.py`, `tests/character/test_character_recall_harness.py`,
  `tests/character/fixtures/recall_office_engineer.yaml`,
  `tools/qwen_worker/specs/character_wp23_recall.yaml`, `character_wp23_recall_harness.yaml`
- WP-24: `tests/character/test_character_live.py`, `tests/character/test_character_jobs_story.py`,
  `tests/character/fixtures/app_campaign_sha256.json`,
  `tools/qwen_worker/specs/character_wp24_live.yaml`, `character_wp24_jobs_story.yaml`,
  `character_wp24_compose.yaml`
- WP-25: `tests/character/test_e2e_two_weeks.py`, `tests/character/fixtures/e2e_office_fortnight.yaml`
- Shared (all of the above): `tests/character/fakes_e2e.py`, this file.

## Spec run order

After Phase 3a's WP-15..WP-20 are promoted (WP-21 uses bus_contracts;
WP-24's specs use brief + recall):
`character_wp21_testctl` -> `character_wp22_brief` -> `character_wp23_recall` ->
`character_wp23_recall_harness` -> `character_wp24_live` -> `character_wp24_jobs_story`
-> (`character_wp24_compose` only after Q7 / P3b-Q4). One at a time, with
`CHARACTER_TEST_REQUIRE_DB=1` (+ `CHARACTER_TEST_DSN` on aarch64).
**main.py is re-emitted whole by several specs** (Phase 2 initialize, Phase 3a
ingest/daily/weekly, WP-21, WP-24): each spec lists the promoted main.py as
context and says "reproduce exactly, add only ...". Run them strictly in
sequence and review each main.py diff for dropped registrations.

## Test list -> test name mapping

### WP-21
- T21.1 -> test_character_testctl.py::test_reset_undo_restores_the_state_before_the_reset
- T21.2 -> test_character_testctl.py::test_delete_without_confirm_dbname_exits_1 (4 parametrized deleting commands)
- T21.3 -> test_character_testctl.py::test_fragment_delete_works_only_through_the_bypass_and_trigger_still_blocks
- T21.4 -> test_character_testctl.py::test_unlock_and_lock_toggle_the_unlock_row
- T21.5 -> test_character_testctl.py::test_seed_inserts_synthetic_events_with_week_and_day_from_the_clock
- T21.6 -> test_character_testctl.py::test_week_wipe_removes_that_weeks_rows_only
- T21.7 -> test_character_testctl.py::test_testctl_disabled_makes_every_subcommand_exit_1 (9 parametrized)
- T21.8 -> test_character_testctl.py::test_every_subcommand_writes_a_testctl_jobs_row_except_dry_run (9 parametrized)
- T21.9 -> test_character_testctl_cli.py::test_revert_moves_the_pointer_and_publishes_a_refresh
- (WP target "registration in main.py") -> test_character_testctl_cli.py::test_main_registers_testctl_and_revert

### WP-22
- T22.1 -> test_character_brief.py::test_sections_come_out_in_plan_order_with_their_headings
- T22.2 -> test_character_brief.py::test_never_contains_truth_dormant_gists_week_numbers_or_loop_words (integration, pg_conn)
- T22.3 -> test_character_brief.py::test_unlocked_fragments_appear_as_feelings
- T22.4 -> test_character_brief.py::test_stays_under_max_chars_by_dropping_oldest_knowledge_first
- T22.5 -> test_character_brief.py::test_brief_cache_ttl_hit_miss_and_invalidate
- T22.6 -> test_character_brief.py::test_includes_extra_feeling_surfaces_lines_from_the_caller
- OB-41 drop-in for app/office/brief_stub.py -> test_character_brief_persona.py (9 tests: stub content without
  loop words, role/value/seat, unknown role, Party Member silence, directive rendering, BriefError cases +
  OFFICE_PACK_DIR, max_backstory_chars, memory source + failing-source fallback)

### WP-23
- T23.1 -> test_character_recall.py::test_sw_score_of_identical_sequence_is_one_clipped
- T23.2 -> ::test_shuffled_sequence_scores_lower_than_ordered
- T23.3 -> ::test_unrelated_beats_under_the_bias_score_zero
- T23.4 -> ::test_hooks_are_case_insensitive_whole_words_and_alias_aware
- T23.5 -> ::test_activation_decays_by_decay_per_beat
- T23.6 -> ::test_spread_reaches_linked_fragments
- T23.7 -> ::test_crossing_unease_gives_one_event_per_cooldown
- T23.8 -> ::test_crossing_surface_calls_judge_yes_surfaces_and_unlocks_no_gives_nothing
- T23.9 -> ::test_unlocked_fragments_are_never_candidates (+ test_character_recall_harness.py::test_load_candidates_returns_dormant_fragments_with_ordered_lead_up, the DB side)
- T23.10 -> ::test_reset_clears_activation_window_and_cooldowns
- T23.11 -> test_character_recall_harness.py::test_harness_ranks_replay_above_shuffled_above_unrelated
  (+ test_harness_fails_when_replay_does_not_separate, test_replay_events_from_the_db_scores_the_matching_fragment_highest)
- extra: test_character_recall.py::test_params_from_config_match_config_recall

### WP-24
- T24.1 -> test_character_live.py::test_observing_renderer_calls_parent_then_publishes_say_and_scene_events
- T24.2 -> ::test_every_improviser_observes_every_beat
- T24.3 -> ::test_each_cast_members_system_prompt_is_the_brief_and_never_says_loop
- T24.4 -> ::test_character_refresh_invalidates_cache_clears_transcripts_and_resets_recall
- T24.5 -> ::test_disable_finishes_the_scene_then_publishes_story_end_and_exits_0
- T24.6 -> ::test_stops_the_same_way_at_the_weeks_ends_at
- T24.7 -> ::test_recall_surface_injects_the_feeling_into_the_next_brief_for_that_character_only
- T24.8 -> test_character_jobs_story.py::test_story_start_enables_and_story_stop_disables_both_idempotent
  (+ test_a_failing_compose_call_exits_1)
- T24.9 -> test_character_live.py::test_no_file_under_app_campaign_is_modified (sha256 manifest
  tests/character/fixtures/app_campaign_sha256.json, CRLF-normalised, taken at commit 324be04 state)
- plan §7 recall rows -> test_character_live.py::test_record_recall_event_writes_the_recall_and_unlocks_on_surface

### WP-25 (one test, `test_e2e_two_weeks.py::test_simulated_fortnight`, numbered comments 1-9)
- T25.1 initialize the office cast -> step "# 1."
- T25.2 week 1 via route() + daily-maintenance at each midnight -> "# 2." (plus Party Member present-only check)
- T25.3 weekly-reset -> dormant fragments, week 1 archived -> "# 3."
- T25.4 week-2 brief has no week-1 knowledge and no fragment -> "# 4."
- T25.5 week-2 replay surfaces the engineer's fragment, judge yes, unlock -> "# 5."
- T25.6 the brief carries it as a feeling -> "# 6."
- T25.7 weekly-reset week 2; week-3 brief still carries it -> "# 7."
- T25.8 testctl reset-undo --week 2 restores the state after week 2 exactly -> "# 8."
- T25.9 re-runs change nothing -> "# 9."

## Test corrections (each with its citation)

- WP-22 T22.1 (2026-09-28): plan §8 heading 4 "What you've learned this week"
  contradicts T22.2 (never the word "week"). The heading is "What you've
  learned recently". Citation: playbook T22.2; D-08 ("no dates and no mention
  of weeks"); OB-41 session instruction (brief never contains "week").
- WP-22 T22.1: an extra "Today's directive" section between the feelings and
  the behaviour contract. Citation: office build plan E6 and OB-41 ("When
  brief.py lands, swap it in for app/office/brief_stub.py (E6)"); docs/brief_stub.md.
- WP-22 T22.2: "never 'time repeats'" added to the forbidden list, and the
  office cast/profile sentence "Never state or imply that time repeats." is
  dropped from the brief. Citation: OB-41 session instruction; plan §8 item 6
  ("Never state that time repeats" is the *behaviour*; the brief must not
  carry the phrase). "truth" is tested as the truth-layer content (a marker),
  not as the English word.
- WP-23 T23.11: "replays the probe's fixture" -> the office fixture
  `tests/character/fixtures/recall_office_engineer.yaml`, same structure as the
  probe (8 lead-up; replay with 2 noise beats; shuffled with the probe's
  permutation; unrelated). Citation: OB-41 "The recall harness (WP-23) is
  calibrated on office beats."
- WP-24 (all items): "the real campaigns/hptest pack" -> the real
  campaigns/ashiorid_office pack. Citation: OB-41 "The pilot cast is the 8
  office characters". Its only spine scene is scripted, so T24.3 / T24.7 flip
  its dialogue beats to improv in memory (pack files untouched).
- WP-24 T24.3: the Party Member never speaks: his LLM is never called, nothing
  is published as him, his brief still contains "You never speak. You only
  observe." Citation: OB-41 session override; profiles/_SCHEMA.md ("Party
  Member only ... 'You never speak. You only observe.'"); cast/party_member.yaml.
- WP-25 T25.1: "initialize for the trio, with canned baselines" -> initialize
  for the office cast; the canned baselines are campaigns/ashiorid_office/profiles.
  Citation: OB-41 "The book source stages are replaced by
  scripts/load_office_profiles.py"; Phase 2 test_character_initialize.py.
- WP-25 T25.3: "one harry fragment" -> one dormant fragment per retaining
  character (all 8 retain); the worked example is the engineer's. Citation:
  OB-41 "retains_fragments: true for all 8"; D-09 seam.
- WP-25 T25.9: "a re-run of any completed job exits 2": held for initialize and
  weekly-reset. daily-maintenance's compaction always runs (plan §5 table:
  "compaction is naturally idempotent") and Phase 3a's frozen T19.5 re-run
  expects exit 0, so its re-run is checked as "no LLM call and no change",
  exit 0 or 2. Citation: plan §5; tests/character/test_character_jobs_daily.py
  (T19.5 re-run). See P3b-Q5.

## Questions for the user

- **P3b-Q1 brief.max_chars vs the office briefs.** With NO memory at all the
  office briefs are 7.3k (engineer, party_member) to 8.6k (ceo) characters,
  over `brief.max_chars: 6000`. Under the plan's rule (drop the oldest
  week-knowledge lines first, then trim) the live driver's office briefs would
  never carry any week knowledge, and the believed backstory gets cut.
  Options: (a) raise `brief.max_chars` to 12000 for OB-41 (a config value);
  (b) truncate the believed layer before dropping knowledge (changes the plan
  §8 drop order); (c) shorten the profiles. Recommendation: (a). The WP-22
  tests do not depend on the value; the e2e renders with a large cap.
- **P3b-Q2 the word "week" in office content.** The brief drops every sentence
  with loop / week / reset, as instructed. The office profiles use "week"
  naturally: the Tester's central want ("one week in which the full suite
  stays green", node `wants-one-green-week`), "three weeks" in several
  backstories, the Tech Lead's soup that lasts "a week". Those sentences vanish
  from the briefs. Options: (a) accept the loss; (b) re-word the office
  profile/cast text without the word (e.g. "seven straight days green"), an
  OB-10 content edit; (c) narrow the rule to loop mechanics (week numbers,
  "loop", "reset") and allow the plain word. Recommendation: (b).
- **P3b-Q3 who is silent.** The live driver finds the Party Member through
  `office.roles.HANDLER_ROLE == "observer"` (`live.default_silent`), with an
  explicit `silent=` override. The alternative is a config key
  `character.characters.<slug>.silent: true` (a config-key change).
  Recommendation: keep the derived rule for OB-41; add the key only if another
  campaign needs a silent member.
- **P3b-Q4 the character-live image.** The WP-06 image copies only
  app/character, but the driver imports app/campaign, app/office, replay,
  emotion, agent_state, audio_envelope, agent_metrics, message_bus,
  worker_control, llm_client and reads the pack under campaigns/. Options:
  (a) a second Dockerfile `services/character-updater/Dockerfile.live` copying
  all of app/ plus campaigns (a new file location); (b) run the driver in the
  existing worker image; (c) widen the WP-06 Dockerfile. Also: config.pack is
  repo-relative (`campaigns/ashiorid_office`) and must resolve in the container.
  Recommendation: (a), with campaigns mounted read-only; written into
  `character_wp24_compose.yaml`, which is a hand step like WP-06's (Q7).
- **P3b-Q5 daily-maintenance re-run exit code.** Playbook T25.9 says a re-run
  of any completed job exits 2; Phase 3a's frozen T19.5 re-run expects 0
  (compaction ran). Which wins? Recommendation: 0 is right for
  daily-maintenance (it did compaction work); the e2e accepts 0 or 2 there.
- **Observed Phase 2 / 3a mismatch (not mine to settle):** Phase 2 T14.13
  seeds `char:party_member` and `tuber_7` for all 8 characters; Phase 3a
  (test_character_ingest.py, question P3a-3) builds its agents map with no id
  for the Party Member. The WP-25 e2e does not depend on either.

## Cross-phase assumptions (in the header of test_e2e_two_weeks.py)

- A1 jobs run as `jobs.run_job(job, argv, cfg=, connect=)` (WP-06).
- A2 `character.jobs_initialize.InitializeJob()` takes `--pack DIR`, loads all 8
  profiles, seeds `char:<slug>` agents, opens week 1, exits 2 on a second run,
  and makes no LLM call (as in Phase 2's test_character_initialize.py).
- A3 `character.jobs_daily.DailyMaintenanceJob(complete=, messages_connect=,
  monotonic=, sleep=, poll_s=)`, `complete(system, user, shape)`; compaction is
  replaced via `jobs_daily.compaction.run` / `.ingest_lag`; ingest counts as
  caught up when `ingest_status.last_message_ts` is past midnight (Phase 3a).
- A4 `character.jobs_weekly.WeeklyResetJob(complete_summary=, complete_fragment=,
  embed=, producer=)`; run at Sunday 00:02 of week N+1 it resets week N; the
  fragment prompt lists moments as `<event_id>: <text>` (prompts/fragment.md) and
  the reply's `anchor_event_id` picks the moment; the lead-up is the
  `lead_up_beats` events before it; every retaining character with events gets
  one fragment (`fragments_per_week: 1`).
- A5 `character.ingest.route(msg, agents, slugs, clock)` with `slugs = {slug:
  character_id}` returns rows for `store.events.insert_many` (Phase 3a).
- A6 node names `remembers-<token>-note` pass the default `node_names.check`.
- A7 (WP-21 / WP-24 specs) `bus_contracts.character_refresh(campaign, week, *,
  characters, reason)`, `character_say(..., *, present, addressees=())`,
  `scene_event(..., *, present, character=None)` exactly as
  `character_wp15_bus_contracts.yaml`.
- A8 the Phase 2 loader stores the profile YAML minus backstory /
  backstory_nodes as `character_baselines.profile`, and the layers as
  `character_backstories` rows with content `{"text": ...}` (matches Phase 2's
  test_character_office_profiles_db.py); `characters.aliases` = title words + name
  words (used as recall hook aliases).
- A9 WP-06's `jobs.build_parser` calls `job.add_arguments(parser)` (the WP-06
  spec says so; WP-21 and WP-24 rely on it).

## Small local choices (§2.5)

- testctl: two-word commands are `fragment add|delete|unlock|lock` and
  `week wipe` (D-10's grouping), job names `testctl:fragment-add`,
  `testctl:week-wipe`, ...; common flags come after the command words.
  `testctl` is dispatched from main.py through a small `COMMANDS = {"testctl":
  testctl.main}` registry (its job name depends on the sub-command).
- testctl `--confirm` is compared with `SELECT current_database()` of the
  connection being changed (not cfg.db.dbname), so a DSN override can't
  redirect a delete. Deleting commands = reset-undo, fragment delete, fragment
  lock (it deletes the unlock row), week wipe. A `--dry-run` of a deleting
  command needs no --confirm (nothing is deleted).
- testctl sets the bypass with `SET LOCAL` itself instead of
  `store.fragments.allow_test_mutation()` (which commits), so the framework can
  roll a dry run back. `fragment add` calls `create_fragment` (which commits),
  so under --dry-run it only prints.
- testctl: unknown fragment id -> exit 1; unlock of an unlocked / lock of a
  dormant fragment -> exit 2 (nothing to do). `seed` default message ids are a
  sha256 of (character, ts, text), so a re-seed inserts nothing.
- revert: the pointer is committed before the refresh is published.
- T21.1 "run the reset" is simulated by the test's `_simulate_reset` (the plan §5
  writes), so WP-21 does not depend on WP-20; WP-25 undoes a real reset.
- brief: scrub works per sentence (split after . ! ?), list items one by one;
  empty sections (and their headings) are left out; feelings get a fixed intro
  line; `build_persona_prompt` has no length cap unless `max_chars=` is given
  (stub parity) and always renders the directive section; a failing memory
  source falls back to the pack-only brief with a WARNING.
- recall: unease and judging have separate per-fragment cooldowns; a judge
  exception counts as "no"; activation = min(1, max(raw + spread x max(linked raw),
  decay x previous)); `beat_index` starts at 1; the judge gets the window's texts.
- recall harness: `--file` mode uses `RecallParams()` defaults when no config is
  passed (no DB, no config file needed); `load_aliases` keys both `tech_lead` and
  `tech-lead`.
- live: narration and action beats are observed under the label "Narration";
  an action's scene_event carries `character = speaker`; `render_scene`
  publishes scene_start / scene_end; stop checks are between scenes only; the
  runtime is reset only right before replaying the show; an unease line goes
  into ONE next brief; a refresh re-creates the recall engines (fresh
  activation, the new week's dormant fragments) and ignores other campaigns.
  `record_recall_event` lives in live.py; production wiring (`build_driver`) in
  jobs_story.py.
- e2e: compaction is replaced (as Phase 3a's own daily tests do);
  `reset.refresh_mode` stays push (the fake producer sees one refresh per
  reset); briefs are rendered with a large cap for the knowledge checks
  (P3b-Q1); snapshots leave out character_jobs and character_artifacts (audit
  logs that reset-undo does not touch, plan §5.2).
