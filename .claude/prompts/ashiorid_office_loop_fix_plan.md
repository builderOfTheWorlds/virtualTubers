# Plan: stop the ashiorid_office table restaging the same morning

Source: .claude/prompts/ashiorid_office_loop_handover.md (status: open, run stopped).
Status (2026-10-10): P1-P5 IMPLEMENTED, uncommitted; 60-min live test run started. P6 open.
  New: app/table/story_log.py, app/table/contract_filter.py (written by the local vLLM
  harness, reviewed), tests/table/test_table_{story_log,contract_filter,story_runtime}.py.
  Changed: app/agent_handlers/table_gm.py, app/agent_handlers/table.py, app/turns.py,
  app/table/gm_context.py, scripts/build_table_configs.py, config/table/ashiorid_office.yaml.
  Result of the dry run: the word filters alone dropped only 5 scenes (true repeats overlap
  0.25-0.53, the same as distinct scenes), so config skip_scenes (11 ids, flagged by the local
  model, reviewed by hand) was added: 38 -> 21 scenes.
  Live: arrival restaged twice in off-hours (scenes 2-3); from the standup on the story
  advanced every scene; the story restored after a GM restart; notes now say what happened.
  Still open: the GM voices seats' lines inside its own narration (Nora's catchphrase,
  Owen's lines). Only the silent "you" is enforced (gm_silent_you).
Token policy: implementation phases are offloaded to the local vLLM/qwen harness
(skill: qwen-harness-local-codegen); Claude plans, reviews diffs, runs tests and verifies.

## 0. What I re-verified (2026-10-10) and where the handover is wrong or incomplete

Confirmed:
- table_gm.py:405-408 context_provider passes segment=None. The GM sees identity, gm_blocks,
  sheets and the current contract only. No earlier scene reaches it.
- Seats (app/agent_handlers/table.py) see their brief, the CURRENT scene's committed
  transcript and the GM instruction. They also have no memory of earlier scenes.
- 42 contracts in the fixture, 38 after only_with_active_players (drops 4 Party-Member-only
  slots), so index 0 = off-hours-arc-n0-n0-004. Matches the handover.

New findings that change the plan:
F1. The near-duplicate dedupe (handover 2c) is not active at all: the office config has no
    dedupe_canon_goal key, so contracts_provider skips the exact-match dedupe too.
F2. The repeated content is in the tree, not just in single slots. The planner wrote sibling
    leaves that each retell the whole segment synopsis:
      off-hours: n0-n0 (PM writes, OM arrives) -> n0-n1 (OM arrives) -> n1 (PM writes, OM arrives)
      build:     n0-n0 and n0-n1 are the same 4 slots reworded (collab, pitch, lunch, analyst)
      ship:      n0 and n1 both do final tests, release notes, wind-down
      standup:   "team gathers for the 06:00 standup" opens n0-001 and n1-n0-001
    The continuity chain also goes backwards: n0-n1 continuity_out "OM begins her day" ->
    n1 continuity_in "OM arrives before the others". A GM that faithfully plays this
    re-stages the arrival even with perfect memory. Runtime memory alone will not fix it.
F3. "Three things, then I'll let you go" is Nora's persona catchphrase
    (config/workers/office/office_manager.yaml system_prompt, cast bible). The repeat check
    (commit_check.repeats_prior, Jaccard 0.8) only compares against the CURRENT scene, so a
    catchphrase is never blocked across scenes.
F4. GM direction text (the scene-opening "The emergency strip hums...") is committed in
    turns.py:290 with no check at all. Seat lines are checked; GM narration is not.
F5. state_delta is free-form and unreliable as a summary (live examples:
    {"round": 2, "next_beat": "analyst_floor", "status": "completed"}). Adjudication notes
    are better ("Nora arrives early, cleans, prepares for standup...").
F6. Handover 5.1 says "make sure the summary is not cut to zero". In gm_context.py the
    segment and contract are never cut; only sheets shrink, and ValueError is raised if the
    fixed parts do not fit. An uncapped summary will not be cut, it will crash the context
    build. It needs its own hard cap.
F7. TableRuntime._context caches by scene_id (table_gm.py:533). Once the context includes the
    story so far, a replayed scene would reuse a stale summary. Cache key must include the
    history length.
F8. Handover 5.3 ("tell the GM an entrance already happened") is a prompt-wording rule. Per
    project experience, wording rules drift; the invariant should be enforced in code
    (F3/F4 guards) with wording only as a hint.

## 1. Flow after the fix

    generator run (fixture / gen DB)
          |
          v
    contracts_provider --[P2 collapse redundant leaves + near-dup dedupe, logged]--> contracts
          |
          v
    TableRuntime._start(i) ----> context_provider(contract, story_log, segment)   [P1]
          ^                            |  "What has already happened" (capped)
          |                            v
          |                      Arbiter: direct -> [P3 cross-scene repeat guard on GM text]
          |                               seats  -> [P3 repeat guard incl. recent scenes]
          |                               adjudicate -> scene_resolve
          |                            |
          +---- story_log.append(entry) <-- tick() at TERMINAL (skipped scenes marked, not told)
                     |
                     v
              position file (index, scene_id, story_log tail)   survives GM restart

## 2. Phases

P1. Story so far (GM + seats)                                   [offload: qwen harness]
  - New app/table/story_log.py: StoryEntry(scene_id, canon_goal, notes, state_delta,
    resolved, skipped) and render(entries, max_chars) -> "# What has already happened"
    block, newest last, oldest dropped first to fit the cap (default 2500 chars, last 6).
  - TableRuntime.tick(): before self.arbiter = None, append an entry built from the arbiter
    state (canon_goal + last adjudication notes + state_delta). Skipped scenes are recorded
    as skipped and rendered as one line, not as events.
  - Persist the last N entries in the existing position file (same atomic write). On boot
    load them. On table_run_start from=beginning clear them.
  - build_gm_context: new optional history= argument rendered after the players and
    before the segment; counted as fixed text; capped by story_log.render, so F6 cannot
    raise.
  - Wire segment=: pass the current segment's arc_plan synopsis + continuity_in/out and the
    leaf title (not the whole brief/tree; keeps the budget small).
  - Fix the cache key (F7): (scene_id, len(story_log)).
  - Seats: add the same rendered block (shorter cap, ~800 chars) to the turn_assignment
    payload by value, consistent with the "transcript passed by value" rule; table.py
    prepends it to the user prompt.
  - GM identity line: "Scenes continue from the state above. Do not restage arrivals or
    greetings that already happened." (hint only, see P3 for enforcement).

P2. Contract cleanup at load time                               [offload: qwen harness]
  - Enable dedupe in config/table/ashiorid_office.yaml (dedupe_canon_goal: true), regenerate
    with scripts/build_table_configs.py (never hand-edit the generated files).
  - Replace the exact-match key with token-set Jaccard (reuse commit_check._words; threshold
    configurable, default 0.6) compared against the last K kept goals (default 8), not the
    whole run (lunch can legitimately recur on a later day). Log every drop with both ids.
  - Optional, config-gated (collapse_redundant_leaves): drop a whole leaf when most of its
    slots are near-duplicates of the previous sibling leaf. Measure first on the fixture:
    print which contracts each setting removes and review the list before enabling.

P3. Code-enforced anti-repetition across scenes                [offload: qwen harness]
  - Arbiter gets recent_lines (committed texts of the last 2-3 scenes, from story_log or a
    small ring buffer in TableRuntime). Seat check uses prior = current transcript +
    recent_lines, so a cross-scene catchphrase repeat fails "repeats" and the seat retakes.
  - GM direction: run repeats_prior(direction, recent GM directions); on a hit, call direct
    once more with "Your opening repeats an earlier scene; continue from the current state."
    Second hit: commit anyway and log WARN (never block the table).

P4. Tests (pytest, tests/table/)                                [offload, Claude reviews]
  - test_story_log.py: render order, cap, skipped entries, empty log.
  - test_table_gm_context.py: history block present, never raises at max_chars, sheets
    still shrink first.
  - test_table_gm_handler.py: entry appended at TERMINAL, persisted, restored after a new
    TableRuntime on the same position file, cleared on from=beginning, cache key changes.
  - contracts_provider: near-dup drop on the office fixture with expected dropped ids.
  - Arbiter: a seat line repeating a previous scene's line is rejected; GM direction
    re-ask path.
  - Full suite: .venv/bin/python -m pytest -q tests/table (baseline 287 passed, 1 skipped).

P5. Live verification (Claude, real output only)
  - Rebuild/redeploy the GM image; grep the container for the new story_log module before
    testing (images lag HEAD).
  - table_run_start from=beginning, 6 scenes. From docker logs: no scene after the first
    opens with Nora arriving; scene_resolve notes advance (arrive -> set up -> standup);
    /table/status next_scene_id advances each time.
  - docker restart the GM mid-run; confirm the story block comes back (log line with
    entry count) and the next scene does not restage the morning.
  - Read the aired lines (strip ANSI from the roundtable ♪ lines) and count cross-scene
    repeats of "Three things".

P6. Root fix in the generator (separate issue, not in this pass)
  - Layer 2 writes sibling leaves independently from the segment synopsis, which produces F2.
    Sibling leaves must be generated sequentially with the previous sibling's committed
    continuity_out and slot goals in context, and reject a leaf whose continuity_in
    regresses. Then regenerate the office run. File as a tracked issue with F2's evidence.

## 3. Changes vs the handover's proposal
- Kept: running summary (5.1), segment wiring (5.2), near-dup dedupe (5.4), tests (5.5).
- Changed: summary source is adjudication notes + canon_goal, not state_delta alone (F5);
  summary has its own cap instead of riding the sheet truncation (F6); seats get it too.
- Changed: 5.3 becomes a hint; the real guard is the cross-scene repeat check (F8, P3).
- Added: enable dedupe in config (F1), cache key (F7), GM direction check (F4), generator
  issue (F2/P6).

## 4. Open questions (answers proposed)
- Persist summary in the position file or rebuild from logs? Proposed: position file. The
  scene_resolve history is not stored anywhere queryable (record_id is None), so rebuilding
  would mean parsing docker logs.
  do the position file
- Party Member silent? Yes by config: tuber_7 is in silent_seats and not in active_players,
  and commit_check rejects any line from it. If GM narration gives them speech, that is GM
  drift; P3's GM check can also reject GM text that quotes the Party Member. Needs a look at
  the actual log lines before adding a rule.
  that correct, the party member is silent, hes a mysterious government man who doesnt interact with teh rest of the characters yet. 
- 60-minute default or no duration? User decision.
test with 60m
- P2 leaf collapse: enable, or only near-dup slot dedupe? Decide after the dry-run list.
enable and allow to change as a config var
