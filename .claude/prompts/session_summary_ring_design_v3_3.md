# Session Summary — Ring Composition Spec (v3.3)

**Date:** 2026-08-27
**Project:** virtualTubers — 3-Layer Weekly Generator
**Spec:** `.claude/prompts/ring_composition_spec.md`
**Prior input:** `.claude/prompts/opus_ring_structure_prompt.md`

Companion to the spec, not a replacement. The spec is authoritative; this
records *why* things are the way they are and what was rejected.

---

## What this design is

A **ring** partitions a span into `[descent | keystone | ascent]`. Rings nest
recursively, and each level inherits direction multiplicatively via
`SIGN = {descent:+1, keystone:-1, ascent:-1}`. That single rule makes the whole
structure computable from a partition table with no LLM involvement, and it is
what guarantees a subplot and its mirror run in *opposite* directions rather
than simply repeating.

---

## Decisions made this session

### 1. Descent and ascent may differ in length (`d != a`)

Asymmetry became a first-class authoring control: `d > a` is dread (long
approach, hard fall), `d < a` is consequence (fast turn, long aftermath).

**This invalidated three v2 constructs**, all marked *delete, not adapt*:

| Construct | Why it died |
|---|---|
| `mirror(o) = (N-1) - o` | Assumes the keystone is at the midpoint. It isn't when `d != a`, so it pairs unrelated segments. |
| `ring_depth` | Only definable when halves are equal length. |
| "spans are mirror-symmetric at every level" | Verified false under asymmetric partitions. |

**Replacement pairing rule:** position is normalized distance from the
keystone, `u ∈ [0,1]`, with each half scaled onto the same range. Two segments
mirror when their `u` intervals overlap — the descent is *stretched onto* the
ascent rather than counted against it.

Verified exhaustively for all `d,a ∈ 1..12`: symmetric, total (nothing
orphaned), and reduces to exactly `d-1-i` when `d == a`, so v2 is the special
case and prior symmetric reasoning stays valid.

**Consequences:** pairing is one-to-many, so validators must check a *set* of
partners and pass on at least one. Use `fractions.Fraction`, never floats —
boundary-touching false pairs are very hard to diagnose. With `16/4/8`, each
ascent segment answers exactly two descent segments (verified).

### 2. Plot layers `plot_0 … plot_n`

Replaced v2's global-role/local-role split. Each layer is an independent
tiling by complete rings; `plot_0` *is* what v2 called global position, so
this is one mechanism with no special cases.

- **nested** — rings are another layer's children (default; clean causality).
- **braided** — own boundaries, can straddle a parent's. Cost: the tree becomes
  a lattice, so state scoping must be declared per layer rather than inferred
  from containment.
- **partial layers** — a layer need not cover the loop. Gaps mean *dormant*.
- **per-story config** — every story has its own `span`, `parts`, `polarity`,
  `mode`, `role`, plus optional `inherits` / `mirror_of_plot`. The layer number
  is a label, not a tier.

### 3. Layer-level mirroring (new, arose from plot_3)

A whole story can mirror another whole story, declared via `mirror_of_plot`.
Rule: **opposite polarity wherever the two spans overlap** — LAW A lifted from
siblings to layers.

Emerged from adding `plot_3` as `[8,28)` with parts `8/4/8`. Two things fell
out unplanned:

- Its descent covers segments 8-15 running *forward* while `plot_2`'s final
  ring covers that identical span running *reflected*. Verified: same span,
  opposite polarity. Two live stories pulling opposite directions, then
  `plot_2` goes dormant and `plot_3` carries the inverted continuation to the
  loop end.
- Its keystone lands on 16-19 — exactly `plot_0`'s keystone.

### 4. Connective tethers (§4.6) — replaced the "long-range thread" idea

**Definition:** any point of data that is not part of the larger story arc but
has an open and a close. Observed cases: a character met and met again; an item
found and later useful; a locked door found, a key found, the door opened; a
rumour raised and settled; a debt incurred and called in.

**Rejected framing #1:** modelling this as a plot layer with an oversized
keystone (`parts: [2, 20, 2]`). It made a note masquerade as a story.

**Rejected framing #2:** `start` + `finish`. Two points cannot express the
locked-door case, which is three beats — find door, find key, *return*. This
was caught by testing the user's own example against the spec.

**Adopted framing:** a tether is an **ordered, variable-length list of 2+
beats** in absolute `[loop, order]` time. First beat opens, last closes, any
beat between is a **waypoint**. No descent, no keystone, no ascent — a tether
has no shape at all, only beats. No upper bound on beat count: add what the
story demands.

**Tethers may cross loop seams and persist for weeks.** This is the sole
exception to "nothing crosses the seam" — rings still may not, because a ring
that straddles the boundary has no way to resolve once its layer's state is
cleared. A tether has no shape to resolve: it is state plus beats, and state
is exactly what the seam carries. Two structural consequences:

- `loop_seam_carry` changed from a static config value to a **floor**. The
  actual seam carry is that union the live keys of every tether open across
  the seam — computed from beat lists, so a tether needs no per-loop
  bookkeeping.
- §6.6 rule 5 previously cleared every non-`p0` key at the seam, which would
  have silently destroyed any cross-loop tether. Now `p0-*` **and open
  `tk-*`** survive.

Rules:
1. Each beat may set, replace, or clear keys in the tether's own scope; every
   key must be cleared by the final beat. A single held key is insufficient for
   waypoints — after "key found" the state differs from "door found", and only
   the last beat resolves anything.
1b. Validation splits in two (§6.7), because a loop is planned without seeing
   later loops: **orphan close** (a close whose open never happened, locally or
   inbound) is checkable per loop and must fail; **every tether eventually
   closes** cannot be, so it runs at the campaign horizon and only warns.
2. A dormant tether must not consume a prompt slot. A planner told to advance
   twenty segments of "the item is in the bag" will invent filler.

The payload taxonomy is **deliberately left open** (§10.7) — to be derived from
real authored material rather than guessed at. The shape is stable; the
vocabulary is not.

### 5. Concurrency cap

**Max 4 concurrent threads per segment; max 4 `main` stories per loop.**

Checked at load time across the **union of all layers**, not per layer — four
half-loop layers can still stack five deep in the middle.

**Tethers are exempt**, dormant *and* at their endpoints. The cap bounds
stories a viewer must track; a tether is a note.

Residual concern kept as an advisory, not a failure: a tether endpoint is
still a real beat. In the worked example, segments 9, 13 and 14 sit at the
4-thread ring cap *and* carry a tether endpoint. Legal and usually fine — the
spec warns only when a segment at cap carries 2+ endpoints.

### 6. Config rules removed

| Rule | Fate |
|---|---|
| "every layer's rings tile the loop exactly" | **Dead.** Partial layers are a feature; this rejects every structure in §9. |
| "keystone spans across layers must be distinct" | **Downgraded to a note.** Shared keystones are legal and often intentional. |
| `d == a` check | **Dead.** Its removal is the point of v3. |

---

## Resolved configuration

| Item | Decision |
|---|---|
| Loop anchor | **Sunday 00:00** (midnight Sat→Sun). "Saturday 00:00" retired — a silent 24 h offset wherever it survives in project docs. |
| Keystone timing | Unconstrained. Any hour. |
| Keystone coincidence | Legal and often desirable — one event serving two threads at different altitudes. |
| Concurrency | 4 threads max; 4 `main` stories max; tethers exempt. |
| Nested vs braided | Nested for `plot_0`/`plot_1`; braided for short episodic layers. Long-range material is a tether, not a braided layer. |

---

## Worked example (spec §9, machine-verified)

`plot_0` at `16/4/8`. `plot_1`/`plot_2` nested, `plot_3` braided mirroring
`plot_2`, plus four tethers. All 28 segments verified against the recursion.

| tether | beats | wall clock | carried |
|---|---|---|---|
| `thread_0` | 0/2 → 0/13 | wk1 day 1 12:00 → wk1 day 4 06:00 | 66 h |
| `thread_1` | 0/5 → 0/17 → 0/22 | wk1 day 2 06:00 → day 5 06:00 → day 6 12:00 | 102 h |
| `thread_2` | 0/9 → 0/19 | wk1 day 3 06:00 → wk1 day 5 18:00 | 60 h |
| `thread_3` | 0/14 → 1/6 | wk1 day 4 12:00 → wk2 day 2 12:00 | 120 h |
| `thread_4` | 0/21 → 1/11 → 2/3 | wk1 day 6 06:00 → wk3 day 1 18:00 | 228 h |

`thread_1` is the locked-door shape, with its waypoint at segment 17 where
ring concurrency is 3 — placing waypoints in ring headroom is the cheapest way
to avoid beat pile-up. `thread_2` deliberately closes inside `plot_0`'s
keystone (16-19), paying off at the loop's turn. `thread_3` and `thread_4`
cross the seam: at the end of loop 0 the seam carry holds both, at the end of
loop 1 only `thread_4`.

---

## Still open

- **§10.6** Segment-count alignment — 28 segs × 6 h = 4/day. Changing
  `arc.segment_hours` or `arc.hours_total` breaks day alignment and needs new
  partition tables.
- **§10.7** Tether payload vocabulary — deferred by intent. A `kind` field
  would let prompts phrase a close correctly (a `debt` close discharges
  something; an `item` close need not), but the taxonomy should come from real
  authored material.
- **§10.8** Tether density — cap-exempt means nothing stops twenty of them,
  at which point the planner's state block becomes unreadable. Sharper now
  that a loop can inherit tethers it did not open: density is a campaign
  property, not a per-loop one. Soft ceiling ~7 simultaneously open.
- **§10.9** Campaign horizon — "every tether eventually closes" needs a point
  at which the campaign counts as complete. A rolling horizon (warn on tethers
  open longer than N loops) probably beats a fixed end date for an ongoing
  series.

The two `plan_arc.py` bugs (spec §0.1 — dead `batch_size` config, positional
`previous_continuity` lookup) were reported separately and are out of scope
for the spec.

---

## Load-bearing cautions

- **"Ring-valid" ≠ "well-told".** Validation proves the structure is present
  and non-degenerate. Nothing checks whether a transformation is dramatically
  satisfying. Conflating the two is why the diagnosed run reported clean while
  being broken.
- **Never validate a mirror pair by comparing role labels** (LAW C). A node's
  role is relative to its parent's traversal direction, so a descent can
  legitimately mirror a descent. Pair on `u`-overlap and polarity only.
- **Mirroring must never hijack `continuity_in`/`continuity_out`.** Those are
  the strictly linear `o-1 → o` chain. An ascent segment continues from its
  predecessor, never from its mirror — reaching backwards here is exactly how
  the diagnosed run broke.
- **Gate Layer 2 behind `validate_ring_arc()`.** Layers 2-3 are where the GPU
  hours go; spending them on a spine already known broken is the most
  expensive mistake available.
