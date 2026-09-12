# Ring-Composition Findings — ashiorid_1 run (2026-08-26)

Diagnosis of `job_20260826T223127_c5e05b` (arc stage) against the ring
structure discussed in `ring_structure_diagram.md`. Written up so Opus has
real failure evidence to design against, not just a theoretical spec.

## What the run produced

18 of 28 requested segments, `order` values: 0,1,2,3,4,5,6,7,8,9,10,11,
**[gap]**, 18,19,20,21,22,23. Full segment JSON is in this session's history;
key fields (`id`, `order`, `spine_scenes`, `continuity_in/out`) were reviewed
directly, not inferred from logs.

## Problem 1 — the keystone landed in a hole that was never planned

For a 28-segment/168h arc, ring theory puts the "C" keystone (the single
biggest mid-arc crisis everything else mirrors around) near the midpoint —
roughly order 13–14. Orders 12–17 are entirely missing from this run. The
most structurally important beat of the whole arc doesn't exist, not because
it was misplaced, but because nothing was ever generated for that slice.

This is NOT a ring-design problem — `arc_schema.py`'s `SYSTEM_PROMPT` never
asked the planner for a keystone, a midpoint, or mirrored beats at all. It
currently just asks for "a coherent arc." The gap is a batching/continuity
bug (see Problem 2), and the missing-keystone framing is a design gap this
new document needs to close.

## Problem 2 — the back half re-ran the front half instead of mirroring it

Continuity chain across the batch boundary:
- order 11 (`portal-decision`) ends: "The group makes a crucial decision
  regarding the portal, shaping the future of the world."
- order 18 (`portal-consequences-arc`) begins: "The group has decided the
  portal's fate." — chains fine from 11, so THIS edge is fine.
- order 18 ends: "must deal with the aftermath and implications of their
  choice."
- order 19 (`malmont-arrival-arc`) begins: "The group has left the manor
  ruins." — that is the continuity state from **order 1**, not order 18.
  The planner silently reset story-state back to right after the manor.

Consequence: orders 19–23 reuse the IDENTICAL `spine_scenes` already used in
orders 6–10 (`malmont-arrival`, `amulet-map`, `malvakar-riddle`,
`bahadur-revealed`) with fresh, generic synopses that don't acknowledge the
party already lived through those beats. This reads as an accidental replay,
not a deliberate transformed echo — the defining difference between a bug and
a ring mirror is whether the second pass is INFORMED by the first pass's
outcome, and here it demonstrably is not (continuity_in proves it).

Root cause hypothesis: segments were generated in separate batches
(`arc.batch_size`) and a later batch's prompt lost the prior batch's
continuity_out / carry_out state, likely because the gap (12–17) meant the
"previous_continuity" handed to `build_prompt()` for the 18-23 batch was
stale — it referenced order 11's continuity_out but by the time this batch
ran, the model had no memory of what (if anything) had been planned to fill
12–17, and re-anchored to older, safer material it did remember (the manor's
aftermath) instead.

## Problem 3 — no loop-closure (A') segment at all

The run stops at order 23. A 28-segment arc needs a closing block (roughly
orders 24-27) that mirrors order 0 ("hero leaves home") as "hero returns /
party disbands" — that segment doesn't exist in this run, so there's no
observable loop-closure to evaluate.

## What DOES already work structurally (keep these, don't relitigate)

- `arc.loop` field already exists in the schema (`arc_schema.py`
  `REQUIRED_KEYS`) — the mechanism for marking "this is loop N vs loop N+1"
  is already there and doesn't need to be invented.
- The segment-level recursive tree (`segment_schema.py`, `segment.tree`
  config) already supports arbitrary internal structure per segment via
  weighted children — a ring role per child can be layered onto this without
  a tree-schema rewrite.
- `carry_in`/`carry_out` state-passing between segments already exists and is
  validated against a closed vocabulary (`vocab.unknown_carry_keys`) — ring
  mirroring can piggyback on this same mechanism to say "carry forward the
  fact that beat X happened so beat X' can reference it."

## What's missing and needs the new document to define

1. A vocabulary for ring position — some way to say "this segment/node is A,
   B, C (keystone), B', or A'" so the arc/segment planners can be told
   explicitly what role they're filling, instead of inferring structure from
   nothing.
2. An explicit mid-arc keystone requirement in the arc planner's system
   prompt/prompt-builder (`arc_schema.py` `SYSTEM_PROMPT` / `build_prompt`),
   so batch boundaries can't silently swallow it the way orders 12-17 were
   swallowed here.
3. A rule that mirrored segments (B/B', A/A') must be told what their pair
   segment established, so the "echo" is a deliberate transformation, not a
   duplicate — likely via referencing the paired segment's
   `continuity_out`/`carry_out` explicitly in the prompt for the second-half
   segment, not just "previous_continuity" from the immediately prior batch.
4. A rule for how loop-closure (A' -> next loop's A) should read: same spine
   scene ids, `loop` incremented, but NOT verbatim narration — variant pools
   already exist for this at the dialogue layer, so the arc/segment layers
   just need to flag "this is a loop-closing segment" so the right narration
   variants get chosen.
