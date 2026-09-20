# Checkpoint — 2026-09-19 evening+night (Ashiorid week build)

## State (post-decision, 2026-09-19 ~22:30)

### Items 1+2 — SPINE RING: APPLIED + GATE-CLEAN (verified)
- 3 spine scenes PROMOTED into campaigns/ashiorid/scenes/ (001-temple-of-malar,
  002-lighthouse-encounter, 003-mutants-encounter). Pack now 52 scenes, 5 cast.
- Forward splice APPLIED: campaigns/ashiorid/scenes/15-malvakar-riddle.yaml
  gained `default_next: temple-of-malar` (was absent — the open endpoint).
- Loop-closure APPLIED: scenes/003-mutants-encounter.yaml switched
  `default_next: null` -> `malvakar-riddle`.
- Verified spine walk from invitation: invitation -> ... -> amulet-map ->
  malvakar-riddle -> temple-of-malar -> lighthouse-encounter -> mutants-
  encounter -> malvakar-riddle (RING closes). All spine scenes reachable.
- Standalone tracked-pack gate: 0 errors, 0 warnings (the 3 previous
  "unreachable" warnings are gone because the ring now reaches everything).
- Spine scene files in the pack keep their `NNN-name.yaml` staging names
  (promote() copies filenames verbatim); other canonical scenes keep their
  `15-malvakar-riddle.yaml` style. Cosmetic only, not a functional issue.

### Item 3 — Ambient batch 1 (12 scenes): HELD per user directive
- Still staged under .claude/prompts/ashiorid_week_stage/ambient/.
- Not promoted, not committed. Re-promote any time with pack_gate.promote().

### Item 4 — Ambient batch 2 (22 notes): RUNNING in background
- session_id proc_93f7a16516a2 (Hermes background, notify_on_complete set).
- Sources: Factions 4, Locations 6, NPCs 6 (incl. Leena profile — new source
  only, not a new cast member; Leena already in cast), NPCs/README excluded,
  Side Quests 1 (Lizard people replacement), World 7 (Ashiorid, Bahadur Race,
  Campaign Timeline, Cities/Idra, Cities/Vabokedos, Desert, Diplomatic
  relations, The Realms of Ashiorid). Energy and Moonwells already generated
  in the earlier smoke test as `moonwell-meditation` (see ambient_batch2/).
- Smoke test (Moonwells) passed 0 errors 0 warnings, 55 prompt words, ~52s.
- Stage output: .claude/prompts/ashiorid_week_stage/ambient_batch2/
- Log: .claude/prompts/ashiorid_week_run6_batch2.log
- Runner changes landed this round (uncommitted):
  * build_ambient(..., ambient_notes=None, out_dir=None) overrides.
  * new flags --ambient-notes (newline-dl refs), --ambient-out (stage dir),
    --gate-ambient-only (skip spine gate since spine is now in base).
  * summary/staged_under print guarded for g_spine=None.
  See diff .claude/prompts/run_ashiorid_week_build.py vs commit 41df41b.

### Item 5 — primitive backstop + 2 tests: ALREADY IN COMMIT 41df41b
- verified with `git grep 'def pack_primitives' HEAD -- utilities/3Layers…
- `git grep -l 'filters_unknown_primitives' HEAD -- utilities/3LayersWee…
- Nothing extra to commit for item 5.

## Pending operator calls
1. Review ambient batch 2 output when proc_93f7a16516a2 completes (notify
   will land). Gate will re-run per scene.
2. Decide whether to promote ambient batch 1 OR batch 2 (or both) into
   campaigns/ashiorid/scenes/. Both are already gate-clean in isolation.
3. Scale further if needed — remaining ambient-eligible sources: 13 in
   unsorted/, 8 in Resources/, 2 in Spells/ — mostly low-value; not
   recommended for ambient filler.
4. Quality nit (unchanged): spine 002 + 003 share near-identical opening
   narration. Optional revision pass after a listening run.

## What is still uncommitted (working tree)
- promotions of 3 spine scenes into campaigns/ashiorid/scenes/ (new files)
- default_next edits to 15-malvakar-riddle.yaml + 003-mutants-encounter.yaml
- .claude/prompts/ashiorid_ambient_batch2_notes.txt (batch2 refs)
- .claude/prompts/run_ashiorid_week_build.py (scale-up flags)
- .claude/prompts/ashiorid_week_run6_batch2.log
- .claude/prompts/ashiorid_week_stage/ambient_batch2/* (as they land)
- .claude/prompts/build_summary.json (last-write-wins; batch2 will overwrite)

## Known open (unchanged from prior checkpoint)
- Spine scenes 2+3 share an awkward opening ("an uneasy sense fills the
  air…"). Optional revision.
- Two commits are on disk: 41df41b (code) + f034461 (content), main is ahead
  of both remotes by 3 commits. NOT pushed.
