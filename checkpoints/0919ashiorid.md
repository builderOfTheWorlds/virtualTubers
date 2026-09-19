# Checkpoint — 2026-09-19 evening (Ashiorid week build)

## State
- Spine (DONE, staged, GATE PASS 0 errors, 3 expected "unreachable" warnings):
  .claude/prompts/ashiorid_week_stage/spine/
  - 001-temple-of-malar      (91w, 5 beats, reveal_memory)
  - 002-lighthouse-encounter (81w, 5 beats, search)
  - 003-mutants-encounter    (65w, 4 beats, move_to) — ends OPEN
  - splice.proposal.yaml at stage ROOT (NOT in scenes dir): forward splice
    malvakar-riddle -> temple-of-malar; loop-closure left to operator at promote
  - run_id ashiorid_week_20260919_181434 (run 4; runs 1-3 killed after defects)
- Ambient (run 5 IN PROGRESS, proc_00979ea2a37f --ambient-only, 8/12 at this
  checkpoint): sarahs-inn-ambience, city-streets-ambient (1 retry),
  mysterious-child-visits-camp, dwarf-gnome-chase, village-ambience,
  villagers-discuss-unclaimed-treasure, falling-man-impact, henderson-ambience
  -> remaining: Losira, Malmont market, Azra scouting, Grovley watches
  -> then gate, then DONE (exit 0)

## Fixes landed this session (all verified with tests/compiles)
1. author_scenes.py: added pack_primitives() + beat-filter for unknown
   primitives (shared-module bug: prompt never told the model the pack's
   enabled primitives; now backstopped same as speakers/lore, with WARN log
   on drop, and a retry hint added). No behavior change when primitives
   field absent.
2. test_author_scenes.py: +2 tests (drop path + enabled-keeps) — 9 pass.
   test_spine_chain.py 14 pass. test_author_scenes.py 9 pass.
3. run_ashiorid_week_build.py: splice.proposal moved to stage ROOT (was
   being staged as a scene -> "missing id" gate failure); ambient dir
   created in --spine-only mode; --ambient-only mode added; exact note-stem
   matching for CONTINUATION notes (Mutant History - Alien World found).
4. Gate both check now uses STAGE as root, with separate spine/ambient
   subdirs.

## Decisions this session
- Spine continuation seeded FROM malvakar-riddle (the pack's only open
  endpoint), NOT from the Amulet-of-Wonder Quest which is *Agent_Ignore*.
- Closed-vocab: cast=['Leena','Vigil','chadwick','gm','sodacan_bob'];
  primitives=['attack','cast_spell','move_to','reveal_memory','roll_check',
  'search']; lore stems=['amulet-of-wonder','malmont','moonwells',
  'the-bahadur','the-begene-program','the-event'].
- Loop-closure (mutants-encounter -> malvakar-riddle) deliberately left as
  an OPERATOR CHOICE at promote time (proposal.yaml documents this).

## What I'd do next (operator call needed)
1. Let run 5 ambient batch complete + gate green (in progress at this point).
2. Review the 5 spine + 12 ambient staged scenes.
3. Apply the forward splice (malvakar-riddle default_next -> temple-of-malar)
   if we want it, and DECIDE on loop-closure at promote.
4. Promote staged scenes into campaigns/ashiorid/scenes/ (the pack).
   Nothing in this session has touched the tracked pack.
5. (Optional) commit the shared-module fix (author_scenes.py) + 2 new
   tests to git — I have NOT committed anything yet this session.

## Known open quality nits (not blockers)
- Spine scenes 2 and 3 share a near-identical narration line
  ("an uneasy sense fills the air, as if ~ holds secrets best left
  undisturbed"). Consider revising one of 002/003 after listening pass.
- Run 1/2/3 logs are in .claude/prompts/ashiorid_week_run*.log; only run 4
  (spine) and run 5 (ambient) are the final state.
- The 2 spine scenes that DID land (001-002) use a different "type: action"
  style than the canonical scenes, but that's within the validator's accepted
  shape (pack.py Beat dataclass accepts type: action with primitive + params).
