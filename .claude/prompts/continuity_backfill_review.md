# Continuity Backfill — Review Findings (49 scenes, hermes3:70b)

Run: 2026-09-19, ~7 min wall clock. Output:
`.claude/prompts/continuity_backfill.yaml`.
Plan reference: `generator_retarget_scenes_plan.md` §6C, open question 8.

**Verdict: NOT promotable as-is.** Structure is perfect; content has three
defect classes, two of which would corrupt the story graph if promoted.

---

## Structural checks — all pass

| Check | Result |
|---|---|
| Scenes covered | 49/49, none missing |
| Spine with both `continuity_in` + `continuity_out` | 15/15 |
| Ambient with `continuity_in` | 34/34 |
| **Ambient carrying `continuity_out`** (§6C.3 hard rule) | **0 — rule held** |

The §6C.3 ambient rule was enforced *structurally* (the script never asks the
model for an ambient outro and strips it if present) rather than requested in
the prompt. That is why it passed 34/34, and it is the pattern to keep: encode
invariants in code, not in prompt wording.

---

## Defect A — hallucinated identity (SERIOUS)

`the-vault.continuity_in` reads:

> "The party has learned they are **the Letos, an ancient family** charged with
> protecting reality."

The scene text says the opposite. `08-grovley-revelation` tells them they are
products of the **Begene Sisters breeding program**; `09-the-vault` says the
Letos are the family that *has been guarding* the vault and "that job has just
changed hands to the four of you." The party **inherits the Letos' duty** —
they are emphatically not Letos.

The immediately preceding `grovley-revelation.continuity_out` gets it right
("part of the Begene Sisters breeding program"), so the seam **contradicts
itself across one edge**. Promoting this would make every future insertion at
that seam validate against a false premise.

Root cause: the model was given one scene at a time plus the predecessor's
opening line. It had no access to what the predecessor actually *established*,
so it filled the gap by inference from the scene title and the word "Letos" in
the narration.

## Defect B — event ordering inverted (SERIOUS)

`amulet-map.continuity_out` claims:

> "They have **deciphered the first riddle** and know they are looking for
> something that happens exactly twice, forever."

But solving that riddle is what happens in `15-malvakar-riddle`, the *next*
scene (confirmed: `13-amulet-map` → `default_next: malvakar-riddle`). The outro
consumes its successor's content, so `malvakar-riddle` has nothing left to
establish and its own intro reads as a redundant restatement.

Same root cause as A: with only local context, the model summarizes toward a
satisfying stopping point rather than the scene's actual boundary.

## Defect C — over-specific ambient intros (MINOR, 5/34)

Ambient must be injectable anywhere (§6C.3). Five intros assume plot state:

| Scene | Assumes |
|---|---|
| `before-the-well` | a moonwell is nearby |
| `carrying-the-signet` | someone holds the half-signet ring |
| `the-ring-half` | same |
| `grovley-remembered` | Grovley and the manor are known |
| `malmont-market` | the party is in Malmont |

These are **defensible** — the scene *prompts themselves* reference those
things (`a18-carrying-the-signet` is literally about the ring). So the model
described them accurately; the real question is whether such scenes should be
ambient at all, or a third category: **contextual ambient**, eligible only
after a given spine scene has played.

That is a design gap in §6C, not a model error. Recommend adding
`continuity_in` as a *soft eligibility filter* for ambient: the ambient
selector skips scenes whose intro is not yet satisfied, rather than rejecting
them at validation.

---

## Why this is a good outcome, not a failed run

Both serious defects are the **same bug**: insufficient context, not model
incapacity. The 70B model produced clean, well-formed, in-voice summaries at
~5 s/scene for ambient and ~90 s for spine. Where it had the facts it was
accurate; where it had to infer, it invented plausibly.

That is exactly the failure mode the plan's gate architecture anticipates, and
it validates two decisions:

1. **Staged output + explicit promotion** (§1.4) caught this before anything
   touched `scenes/`. A pipeline that wrote directly to the pack would have
   silently poisoned two seams.
2. **`load_pack()` cannot catch this.** Both defects are *semantically* wrong
   but *structurally* valid YAML. Contract correctness needs its own check —
   a human read, or a cross-seam consistency pass.

---

## Fix: a second pass with chain context

The spine is a chain, so summarize it as one. Re-run spine scenes only (15 of
49 — ambient is fine) with:

1. **Sequential processing in `default_next` order**, feeding each scene the
   *actual accepted* `continuity_out` of its predecessor rather than the
   predecessor's opening narration.
2. **An explicit boundary instruction:** "`continuity_out` describes the state
   at the END of THIS scene only. Do not include anything that happens in the
   next scene, even if this scene sets it up."
3. **A cast/lore fact sheet** in the prompt — cast ids, lore stems, and the
   one-line "what the party is" fact — so identity cannot be inferred.

Cost: ~15 scenes x ~90 s ≈ 23 min. Ambient output (34 scenes) is accepted as-is
apart from the Defect C design question.

---

## Lesson for Phase 1-2 (generalizes beyond this task)

**Per-item LLM calls cannot produce a coherent chain.** Anything whose
correctness depends on neighbours — continuity contracts, `default_next`
wiring, mirror pairs — must be generated with the neighbour's *committed*
output in context, processed in dependency order.

This applies directly to `author_scenes.py`: generating spine scenes
independently and chaining them afterwards will reproduce exactly these
defects at scale. Scene authoring must be sequential along the spine, not
embarrassingly parallel. Ambient, which by definition has no neighbours, can
stay parallel — which is also where the throughput is (34 of 49 here, ~250 of
~450 at target).
