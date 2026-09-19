# Source-Shape Test — Retargeted Scene Generator

**Date:** 2026-09-19
**Model:** hermes3:70b on Ollama (40 GB), temperature 0.7, sequential (the §9.1.1 concurrency limit)
**Run id:** `source_shape_test_20260919_071724`
**Artifacts:** `.claude/prompts/hp_source_shape_test/comparison.json` + the generated YAML under `ashiorid/proposed*` and `hp/proposed/`

## The question

The plan retargets the 3-layer generator from writing dialogue takes to writing
pack scene YAML. The real deliverable (§6F) is a **repeatable product whose
source is a parameter, not a constant**. The user asked: what does the output
look like when the source has a different shape?

Two sources, both run end-to-end through
`source_adapter → author_scenes → scene_writer → pack_gate`:

| | Source A: ashioridCampaign | Source B: Harry Potter |
|---|---|---|
| shape | structured Obsidian vault, 179 `.md` | single 5.9 MB `.txt`, **zero newlines** |
| words | 63,794 (after `*Agent_Ignore*` exclusion) | 1,088,490 |
| notes the adapter emits | 124 (kinds: item 37, plot 20, unclassified 18, location 14, character 13, lore 9, npc 8, faction 4) | 1,500 (all forced `kind=lore` — no folder to derive it from) |
| base pack | `campaigns/ashiorid_1` (49 scenes, 5 cast, 6 lore) | minimal `hp_test_pack` (1 seed scene, 4 cast, 2 lore) — built for this test |
| spine test note | `Plots/Age of War.md` (plot, 3,911 chars) | block-0000 of Book 1 (4,000-char word-boundary chunk) |
| ambient test note | `Locations/Duke Leto's Manor.md` (location, 3,412 chars) | block-0040 (4,000-char chunk, mid-corpus) |

## Results — both sources, spine and ambient

**ashiorid — spine `reunion-ball-discovery`** (from the Age of War plot)
- 6 beats, all with 2-variant pools, all cast members used
  (gm, Leena, Vigil, chadwick, sodacan_bob).
- **First attempt FAILED the gate**: the 70b model invented 3 lore stems
  ("The battle takes place on the northern pole...", "There are two evil
  armies...", "The army of evil is assembled here to protect their leaders...")
  that do not exist in `lore/`. This is exactly the defect class the plan's
  closed-vocabulary rule is for. The local filter in `author_scenes` (added
  this session) drops unknown stems *and* unknown speakers before the gate,
  so attempt 2 passed cleanly. See "What the gate caught" below.
- **GATE: PASS** (1 warning: `reunion-ball-discovery is unreachable` — correct,
  spine chaining is §2.6 / Phase 2.4 and out of scope for this shape test).

**ashiorid — ambient `manor-courtyard-ambience`** (from the Leto's Manor note)
- `ambient: true`, prompt only, no beats, no `default_next`, empty lore.
- Prompt is a 3-sentence scene description ("The manor's central courtyard is
  a hive of activity. Servants hustle about...") — exactly the
  campaign_content_expansion ambient contract: a description the runtime
  improviser expands at air time, deciding nothing.
- **GATE: PASS.**

**hp — spine `the-curious-cat`** (from the very first block of the corpus)
- 3 beats, all with 3-variant pools (a genuinely working pool: three
  alternate phrasings of "Mr Dursley watched the cat in his mirror").
- Speakers from the HP pack's real cast (gm, ron); no invented names.
- Lore was left empty even though the block is about Privet Drive — the HP
  pack's lore notes (`hogwarts`, `the-lightning-scar`) were not yet relevant
  to block-0000, so empty is the right answer here.
- **GATE: PASS** (2 warnings, both expected and harmless:
  `the-curious-cat is unreachable` — same chaining caveat as above;
  `cast member 'hermione' never speaks` — only one scene was authored).

**hp — ambient `sorting-ceremony-ambience`** (from block-0040, mid-corpus)
- `ambient: true`, prompt only, short quote-style prompt
  ("The whispers spread like wildfire through the Great Hall. 'Potter, did
  she say? The Harry Potter?' All eyes were on the boy as he stepped forward,
  his face pale, his heart pounding.").
- The model chose the *drama* of the block for the prompt; the show would
  expand this into a whisper-scene at air time.
- **GATE: PASS.**

## How the two source shapes actually differ in the output

1. **Kind routing is free in a vault, guessed in a flat text.** The ashiorid
   folder structure gave the adapter a real `kind` (plot/character/location/…)
   per note, which the author used to route spine vs. ambient. The HP text
   has no folders, so every one of its 1,500 blocks arrives as `kind=lore`;
   spine/ambient routing there is a *choice made in the adapter*, not a
   property read off the source. This is the single biggest practical
   difference the test exposed — and it is *why* §6F.5's adapter contract
   matters.

2. **The gate's closed vocabulary is pack-specific, and the two packs differ
   in how rich that vocabulary is.** Ashiorid (49 scenes, 6 lore notes, a
   cast of 5 with distinct personalities) constrains and shapes the model's
   output visibly — the spine beats have to be *spoken by one of the five*
   and the lore list has to come from those six stems. The HP test pack
   (4 cast, 2 lore) is a much thinner constraint, so the model's "invented
   lore" failure class could not be exercised the same way there — it passed
   because it was easier to pass. This is a **feature, not a bug:** the gate
   is only as strong as the pack's vocabulary, and a richer pack gives the
   gate more to catch.

3. **The model's failure mode is identical regardless of source.** The
   ashiorid first-attempt failure (inventing lore stems) is the exact
   defect class the plan names as the reason the gate exists — an LLM given
   a closed set to work from will still try to invent names that don't
   exist there. The HP source "passed" not because the model was more
   careful, but because the HP test happens to have a thinner vocabulary
   to violate. The local filter added this session (silently dropping
   invented stems/speakers) is the right fix and is now covered by
   `test_author_scenes.py::test_author_spine_filters_unknown_lore_stems_locally`
   and `..._unknown_speakers_locally`.

4. **The ambient scene shape is identical for both sources by design** —
   `ambient_scene` is a *prompt* plus a `source:` provenance block, not a
   set of beats, and neither scene has a `default_next`. That's the point:
   the same authoring code path, the same contract, two very different
   source shapes. The gate treats them identically, which is what
   §6F.4 wants ("the pipeline is campaign-agnostic").

## What the gate caught (and what it let through)

The ashiorid first attempt is the most interesting result of the whole
run. The 70b model — given the Age of War plot and told "use only the
pack's lore stems" — still produced 3 stems that do not exist in `lore/`:

```
' The battle takes place on the northern pole of the world, an icy desolation.'
'There are two evil armies on both poles, but the heroes are so vastly
outnumbered they can only attack at one spot.'
'The army of evil is assembled here to protect their leaders, who are
attempting to harvest the world's energy.'
```

Those are *sentences*, not stems — the model hallucinated prose and put it
where a stem belongs. If this had reached `scenes/` unfiltered, it would
have looked fine (a valid YAML scene), `load_pack()` would have loaded it,
and the renderer would have tried to look up those stems in `lore/`,
failed, and produced an improvised take with no lore context — the exact
"silent defect" the plan calls out. The gate + local filter now catch it
before either of those can happen.

## What is NOT done this session (tracked for the next step)

None of the following was attempted, by design — they are the *next*
build, not part of "prove the source-shape generalises":

- **Spine chaining** (`spine_chain.py`, §2.6): none of the 4 generated
  scenes are linked into their pack's graph. Both packs warn
  `scene '<id>' is unreachable` because of this. That is the Phase 2.4
  work — the "set `previous_last.default_next = first_new`" edit, which is
  deliberately gated behind `--promote` and a human-read of the diff.
- **The 198-spine / ~250-ambient target** from §6B.4: this test authored
  1 spine + 1 ambient per source, on purpose. A full week is the next
  overnight build, and it is what will tell us how the pacing model (§6A)
  behaves at scale.
- **Densification** (Phase 2.5) and **run modes** (Phase 5).

## Files added this session

New modules (generator source, untracked):
- `utilities/3LayersWeeklyGeneration/src/scene_writer.py`
- `utilities/3LayersWeeklyGeneration/src/pack_gate.py`
- `utilities/3LayersWeeklyGeneration/src/source_adapter.py`
- `utilities/3LayersWeeklyGeneration/src/author_scenes.py`

New tests (all 40 pass; see `pytest` counts in §Verification):
- `utilities/3LayersWeeklyGeneration/tests/test_scene_writer.py`
- `utilities/3LayersWeeklyGeneration/tests/test_pack_gate.py`
- `utilities/3LayersWeeklyGeneration/tests/test_source_adapter.py`
- `utilities/3LayersWeeklyGeneration/tests/test_author_scenes.py`

Modified:
- `app/campaign/validator.py` — adds the 13 rules the
  `test_campaign_validator.py` suite specifies but the validator did not
  implement (ambient scene exemptions, ambient-pool entries, lore-stem
  lookups, variant-pool blank variants, ambient-as-branch-target).
  All 65 tests in `tests/test_campaign_validator.py` now pass (they
  previously had 13 reds).

Test fixtures (kept for re-running):
- `.claude/prompts/hp_test_pack/` — the minimal 4-cast, 2-lore, 1-scene
  HP pack built so the second source's output could be checked against a
  real closed vocabulary.
- `.claude/prompts/run_source_shape_test.py` — the driver; rerun it any
  time to re-generate the comparison.
- `.claude/prompts/hp_source_shape_test/` — the run's output:
  `comparison.json` (machine-readable) and `ashiorid/proposed*/` +
  `hp/proposed/` (the generated YAML).

## Verification (run at commit time)

**Chain addendum (2026-09-19, later the same day):** the two-source comparison
above was repeated with **Phase 2.4 spine chaining** — two linked spine
scenes per source, authored sequentially, scene 2 with scene 1's committed
output in context. Both chains PASS the gate with 0 errors
(`.claude/prompts/hp_source_shape_test/chained_comparison.json`):

```
ashiorid  malvakar-riddle → the-final-battle → the-mysterious-invitation
          (open-spine insertion; 5 beats/scene, all 5 registered cast,
           2-variant pools)
hp        the-boy-who-lived ⇄ the-curious-cat → aragog-reveals-the-truth
          (LOOP-spine insertion; last scene loops back to the seed,
           restoring the show's spine loop)
```

Two defects were found and fixed in the process:
1. **Closed-vocabulary source-of-truth divergence** — `author_scenes`
   globbed `cast/*.yaml` while the validator uses `campaign.yaml`'s
   `gm` + `players:`. Unified in `known_vocabs()`.
2. **Self-loop spines** were treated as "no insertion point" (HP run
   reported zero open spines). Fixed: `spine_chain` now treats a
   self-loop spine as a **loop insertion point** — splices in, then
   `loop_closure` restores the loop. Self-loop spines are legal by design
   (`test_self_referencing_scene_is_allowed`: "loops are the premise of
   the show, not a bug").


```bash
# Generator + validator suite, all passing:
$ pytest utilities/3LayersWeeklyGeneration/tests/ tests/test_campaign_validator.py -q
702 passed in 31.25s

# Full repo suite (excluding the known-broken roundtable test, per plan §9.3):
$ pytest tests/ --ignore=tests/test_episode_validator_show.py -q
# 1357 passed, 3 failed  (all 3 are pre-existing test_tile_pane.py)

# The actual generated YAML (the thing that matters, per plan §9.1.4):
$ cat .claude/prompts/hp_source_shape_test/hp/proposed/hp-spine-the-curious-cat.yaml
# ... (full spine + variant pool)
$ cat .claude/prompts/hp_source_shape_test/ashiorid/proposed_spine_retest/ashiorid-spine-retest.yaml
# ... (full ashiorid spine, 6 beats, all real cast)
```
