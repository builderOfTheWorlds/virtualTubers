# Ring Composition — Implementation Spec for the 3-Layer Generator

Version 3.3. Supersedes v2 in full.

Status: structural design spec. Deliberately **campaign-agnostic** — it defines
shape only. Mapping source material onto the shape is a separate, later step;
nothing here should name a character, location, item, or plot beat.

Targets `utilities/3LayersWeeklyGeneration/src/arc_schema.py`,
`segment_schema.py`, `plan_arc.py`, and `config/generation.yaml`.

Companion: `ring_arc_findings.md` (failure evidence this closes).

---

## 0. What changed, and what it invalidated

v3 made two requested changes with larger blast radius than they appear:

1. **Descent and ascent may differ in length.** v2 required `d == a`.
2. **Plot layers are first-class and named** `plot_0 … plot_n`, replacing v2's
   split of "global ring role" vs "local segment role".

v3.1 adds four more:

3. **Partial layers** (§4.2.1) — a story need not cover the whole loop.
4. **Layer-level mirroring** (§4.5) — a whole story can mirror another story.
5. **Connective tethers** (§4.6) — small open/close links carried as an
   ordered, variable-length beat list in absolute `[loop, order]` time. Live
   only at their beats, exempt from the concurrency cap, and the one construct
   permitted to cross loop seams and persist for weeks.
6. **Per-story config blocks** (§4.3) — each story independently parameterized.

Change 1 invalidates three v2 constructs. Stated plainly so they are not
carried forward by accident:

| v2 construct | Status | Why |
|---|---|---|
| `ring_skeleton()`, `mirror(o) = (N-1) - o` | **Dead. Delete it.** | Assumes the keystone sits at the midpoint. With `d != a` it does not, so the formula pairs unrelated segments. |
| `ring_depth` (integer, counts inward) | **Dead.** | Only well-defined when halves are equal-length. |
| LAW 1 "spans are mirror-symmetric at every level" | **Dead.** | Verified false under asymmetric partitions. |

Changes 3-4 invalidate two v3 config rules:

| v3 rule | Status | Why |
|---|---|---|
| "every layer's rings tile the loop exactly" | **Dead.** | Partial layers are a feature; this rejects every structure in §9. |
| "keystone spans across layers must be distinct" | **Downgraded to a note.** | Shared keystones are legal and often intentional (§4.5). |

Everything else in v2 survives, including both `plan_arc.py` bugs, which still
gate the whole implementation.

### 0.1 The two bugs that must be fixed first

**Bug 1 — `arc.batch_size` is dead config.** `plan_arc.py:75` reads
`config["arc"]["segment_hours"]` where it means `config["arc"]["batch_size"]`.
Both are `6`, so it looks correct and is not; tuning `batch_size` is a no-op.
The phase batching in §8.3 cannot take effect until this is fixed.

**Bug 2 — `previous_continuity` is read positionally.** `plan_arc.py:84-87`
takes `plan_segments[-1]`, which is append-ordered with no placeholder for a
skipped batch. After orders 12-17 were skipped, the batch for 18-23 received
**order 11's** continuity as its immediate antecedent — the mechanical cause
of the diagnosed replay, not a prompt-quality problem.

```python
by_order = {s["order"]: s for s in plan_segments}
prior = by_order.get(batch_orders[0] - 1)
previous_continuity = prior["continuity_out"] if prior else ""
```

When `prior` is absent and the batch does not start at order 0, the prompt
must say so explicitly rather than silently substituting a distant state.

---

## 1. The ring

### 1.1 Definition

A **ring** covers a contiguous span of segments and partitions it into three
consecutive parts:

```
ring(span) -> [ descent | keystone | ascent ]
```

Part lengths are `[d, k, a]` with `d >= 1, k >= 1, a >= 1` and `d + k + a =
span`. **`d` and `a` are independent.** Asymmetry is now a first-class
authoring control:

| Shape | Effect |
|---|---|
| `d > a` | Long approach, fast fall. Dread; the turn arrives late and resolves hard. |
| `d < a` | Fast turn, long consequence. The act happens early and the ring is about living with it. |
| `d == a` | Classical balance. Degrades exactly to v2 behaviour (§2.2). |
| `k` large | The turn is an era, not a moment. |

### 1.2 Polarity — unchanged from v2

Each ring carries a direction. Children inherit multiplicatively:

```python
SIGN = {"descent": +1, "keystone": -1, "ascent": -1}

def child_polarity(parent_polarity: int, child_role: str) -> int:
    return parent_polarity * SIGN[child_role]

def traversal(polarity: int) -> tuple:
    return ("descent", "keystone", "ascent") if polarity == +1 \
      else ("ascent", "keystone", "descent")
```

A descent preserves direction; a keystone and an ascent both invert it. This
is the rule that makes a keystone-of-a-keystone run forwards again
(`-1 x -1 = +1`), and it is unaffected by variable lengths.

### 1.3 Termination

Recursion stops when a span has no partition defined for it — **not** when the
span reaches 1. A `1/2/1` partition produces a 2-segment child, and if no
partition is defined for span 2 that node is terminal with two segments in it.
The guard must be `if span not in partitions`, never `if span == 1`. This
crashed on the first attempt to build the tree, so treat it as certain rather
than cautionary.

### 1.4 Degenerate levels

A node whose span already equals the next level's unit skips that level, so
branches can have unequal depth. Under variable lengths this is now the
*normal* case rather than an edge case — a `16/4/8` week has a descent branch
that recurses three levels deep and an ascent branch that recurses two.
Implementations must never assume uniform tree depth or a fixed number of
plot layers per segment (§4.4).

---

## 2. Mirror correspondence under variable lengths

This section replaces v2's dead `mirror(o) = (N-1) - o`.

### 2.1 Keystone distance

Position within a ring is measured by **normalized distance from the
keystone**, `u ∈ [0, 1]`. `u = 0` is adjacent to the keystone; `u = 1` is the
ring's outer edge. Each segment occupies a half-open interval of `u`:

- descent segment `i` (time order, `0..d-1`) occupies `[(d-i-1)/d, (d-i)/d)`
- ascent segment `j` (time order, `0..a-1`) occupies `[j/a, (j+1)/a)`

Both halves are normalized to the same `[0, 1]` scale regardless of their
lengths. This is the whole trick: the descent is *stretched or compressed*
onto its partner rather than counted against it.

### 2.2 The pairing rule

> **Two segments in the same ring are mirror partners when their keystone-distance intervals overlap.**

```python
from fractions import Fraction

def mirror_partners(d: int, a: int) -> dict[int, list[int]]:
    """Map each descent index to the ascent indices it mirrors.
    Symmetric, total, and reduces to the classical rule when d == a."""
    out = {}
    for i in range(d):
        lo, hi = Fraction(d - i - 1, d), Fraction(d - i, d)
        out[i] = [j for j in range(a)
                  if min(hi, Fraction(j + 1, a)) > max(lo, Fraction(j, a))]
    return out
```

Use exact rationals. Floating point will produce boundary-touching
false positives at partition sizes that share factors, and the resulting
spurious pairs are extremely hard to diagnose downstream.

Three properties, verified exhaustively for all `d, a ∈ 1..12`:

1. **Symmetric** — `j ∈ M(i)` if and only if `i ∈ M(j)`. Pairing is a genuine
   relation and needs no bookkeeping to invert.
2. **Total** — every descent and every ascent segment has at least one
   partner. Nothing is orphaned, at any partition shape.
3. **Reduces correctly** — when `d == a`, it yields exactly `mirror(i) =
   d-1-i`, one-to-one. v2 is the special case, so existing reasoning about
   symmetric rings stays valid.

### 2.3 Fan-out and fan-in

When lengths differ, pairing is one-to-many. Worked examples from the
verification:

```
d=4, a=2  ->  {0:[1], 1:[1], 2:[0], 3:[0]}      two descents share one mirror
d=2, a=4  ->  {0:[2,3], 1:[0,1]}                one descent answered by two
d=3, a=5  ->  {0:[3,4], 1:[1,2,3], 2:[0,1]}     ragged, still total
```

This is dramatically meaningful, not a defect. `d > a` means several
setup beats are answered by a single hard payoff. `d < a` means one setup
beat gets an extended answer. Both are legitimate; the shape encodes intent.

**Validation consequence:** mirror checks must iterate over a *set* of
partners and pass when the requirement holds against **at least one**. A
validator written for one-to-one pairing will reject correct asymmetric plans.

### 2.4 Mirror-invariant quantity

`ring_depth` is dead. The mirror-invariant quantity is now the `u` interval:
partners overlap in keystone distance by construction. Where v2 compared
integer depths, v3 compares intervals. Store `u_lo`/`u_hi` per segment per
layer as exact fractions rendered to strings, or recompute from the partition
— never store a rounded float.

---

## 3. The three laws

Restated for v3 and verified across the full tree.

### 3.1 LAW A — mirror siblings have opposite polarity

Within any ring, the descent child and the ascent child always carry opposite
polarity. Verified true for all partitions.

This is the structural signature of the whole design: **a subplot and its
mirror subplot run in opposite directions.** Where one falls and recovers, its
partner recovers and falls. The echo is a genuine inversion rather than a
repeat, guaranteed by arithmetic rather than requested in a prompt.

This is the *survivor* of v2's LAW 2, rephrased to be about siblings within a
ring instead of about globally mirror-symmetric spans — which no longer exist.

### 3.2 LAW B — polarity is the product of the path

`polarity(node) = polarity(parent) x SIGN[role(node)]`, root forward.
Unaffected by variable lengths. This is the law that makes the whole structure
computable from a partition table with no LLM involvement.

### 3.3 LAW C — role labels are not mirror-covariant

Carried forward from v2 and now *more* important, because variable lengths
make label collisions more common.

A node's `role` is relative to its **parent's traversal direction**. A
reflected parent has already reversed its children. So whether a label flips
under mirroring depends on the parent's polarity, not on the node itself. Two
correct cases from the same tree can show a descent mirroring an ascent, and a
descent mirroring a descent.

> **Never validate a mirror pair by comparing role labels.**

A validator asserting "the mirror of a descent is an ascent" will reject
correct plans. Pairing is checked on keystone-distance overlap (§2.2) and
polarity inversion (LAW A) only. Role labels are for *generation* — telling a
planner what to write — never for *pairing*.

### 3.4 What LAW is gone

v2's "spans are mirror-symmetric at every level" is **false in v3** and was
verified false. Any code or check written against it must be removed, not
adapted.

---

## 4. Plot layers

This section replaces v2's `g-role` / `seg-role` / `ring_path` split.

### 4.1 The model

A **plot layer** is an independent tiling of the timeline by one or more
complete rings. Layers are numbered `plot_0, plot_1, … plot_n`, outermost
first.

- `plot_0` — one ring covering the whole loop. The main story.
- `plot_1` — one or more rings. Might be one ring across the week, or several.
- `plot_k` — progressively shorter rings, more instances.

Each ring in each layer is a **distinct story** with its own descent,
keystone, and ascent. A segment is simultaneously serving one ring from every
layer that covers it, which is what lets several stories run at once.

This subsumes v2 cleanly: v2's "global position" is just `plot_0`, and v2's
per-level roles are the deeper layers. One naming scheme, one mechanism, no
special cases.

### 4.2 Nested and braided layers

Two modes, both supported, chosen per layer:

**Nested (aligned).** Layer `k+1`'s rings are exactly the children of layer
`k`'s rings. Boundaries coincide; the structure is a clean tree. This is v2's
behaviour and remains the default — it guarantees subplots resolve inside
their parent's parts, keeping causality easy to reason about.

**Braided (staggered).** Layer `k+1` partitions the timeline on its *own*
boundaries, which need not align with layer `k`. Rings overlap across their
parent's part boundaries.

Braiding is dramatically valuable and mechanically sound. A staggered layer
can peak while `plot_0` is still falling, so a viewer is never between stories:
one thread turns while another is mid-descent. Verified with three layers
whose keystones land at three distinct, non-overlapping spans — all three
independently satisfy the §2.2 pairing properties.

**Cost of braiding:** the tree becomes a lattice. A braided ring can straddle
a parent boundary, so state scoping (§6.2) can no longer be inferred from tree
containment and must be declared per layer. Use nested by default; braid
deliberately, for a named effect.

### 4.2.1 Partial layers

**A layer need not cover the whole loop.** This corrects an over-strict rule
carried from earlier drafts ("every layer's rings tile the loop exactly"),
which is violated by every interesting structure in this section and must be
relaxed.

A layer is a set of non-overlapping rings placed anywhere inside the loop.
Segments a layer does not cover are simply segments where that thread is
**dormant** — the story is not running, and nothing about it needs to be
tracked, generated, or validated there.

The rules that survive, checked per layer at config load:

1. Rings within one layer must not overlap each other.
2. No ring may cross the loop boundary (§6.6).
3. Gaps between rings are legal and mean dormancy.

What is explicitly *not* required: covering the loop, aligning with any other
layer, or having more than one ring.

Partial layers are what make §4.5 and §4.6 possible. Without them the only
expressible structure is a full tree, which forces every thread to be running
at every moment — the opposite of how a serial actually feels.

### 4.2.2 Concurrency cap

**No more than 4 threads should run concurrently on any segment.** Beyond
four, no thread gets enough airtime per segment to stay legible, and a viewer
tracks none of them.

This is a load-time check over the union of all layers, not a per-layer one —
four layers each covering half the loop can still stack five deep somewhere in
the middle. Compute concurrency per segment across all layers and fail the
config if any segment exceeds the cap.

**Connective tethers (§4.6) do not count** toward this cap, at any point
including their endpoints. The cap bounds *stories a viewer must track*; a
tether is a note, not a story.

Threads may deliberately start together or end together. Simultaneous starts
give a strong "new chapter" beat; simultaneous ends give a convergence. Both
are good; they just cost concurrency budget at that moment.

### 4.3 Layer identity and per-story config

**Every story gets its own config block.** A layer is not a fixed slot in a
hierarchy — it is an independently parameterized story with its own span,
shape, mode, and role. Two layers sharing a number are unrelated except by
concurrency budget.

```yaml
plots:
  - id: plot_0
    role: main
    mode: nested
    span: [0, 28]              # half-open [lo, hi) in segment orders
    parts: [16, 4, 8]          # d, k, a — need not be symmetric
    polarity: forward
    children:                   # per-role partition; omit to stop recursing
      descent: [4, 4, 8]
      keystone: [1, 2, 1]
      ascent:   [2, 4, 2]

  - id: plot_1
    role: relationship
    mode: nested
    inherits: plot_0            # rings ARE plot_0's children

  - id: plot_2
    role: episodic
    mode: nested
    inherits: plot_1
    span: [0, 16]               # partial: dormant after segment 15

  - id: plot_3
    role: episodic
    mode: braided
    span: [8, 28]               # partial: dormant before segment 8
    parts: [8, 4, 8]
    polarity: forward
    mirror_of_plot: plot_2      # layer-level mirror; see §4.5
```

Per-story keys:

| Key | Meaning |
|---|---|
| `id` | `plot_0 … plot_n`, outermost first by convention only |
| `role` | closed vocabulary; keeps concurrent stories about different things |
| `mode` | `nested` (rings are a parent's children) or `braided` (own boundaries) |
| `span` | `[lo, hi)`; omit to mean the whole loop. Partial spans are legal (§4.2.1) |
| `parts` | `[d, k, a]` for this story's ring |
| `polarity` | `forward` or `reflected` |
| `children` | per-role partitions for recursion; omit to stop |
| `inherits` | take rings from another layer's children instead of declaring them |
| `mirror_of_plot` | this story is another story's layer-level mirror (§4.5) |
| `carry_scope` | state prefix; defaults to the layer id (§6.2) |

`role` is a closed vocabulary: `main`, `relationship`, `episodic`, `mystery`,
`thematic`. Three layers all running plot-mechanical adventure produce mush;
one main, one relational, one episodic produces texture.

**Recommendation: at most 4 concurrent threads (§4.2.2), and at most 4 `main`
stories per loop.** Other roles may exceed that count as long as concurrency
stays within the cap — several short `episodic` threads across a week is
cheap, because they are never all live at once.

Each ring instance also gets a stable positional id (`plot_1.0`, `plot_1.1`),
which is the unit of subplot continuity and the key for state scoping.

### 4.4 Ragged depth is normal

With variable lengths and partial layers, different segments sit under
different numbers of threads. In the §9 example segments 0-3 carry three
threads, 8-15 carry four, and 16-27 carry three again.

This is correct and should not be flattened. Thread count is a *density* dial:
where more threads overlap the hour is busier, and where a thread goes dormant
the remaining stories get more airtime.

**Implementation consequences:**

- `plot_path` is a variable-length list. Never index it positionally.
- "Which layer am I in" is answered by the entry's `id`, never by list index.
- Per-layer checks must skip segments that layer does not cover.

### 4.5 Layer-level mirroring

Distinct from §2's segment pairing *inside* a ring: an entire story can be the
mirror of another entire story.

Declared with `mirror_of_plot`. The mirrored layer is subject to one
structural rule and one authoring rule.

**Structural rule.** Where the two layers' spans overlap, their polarities
must be opposite. This is LAW A lifted from siblings to layers: the mirror
story runs *against* the direction of the story it answers. Where they do not
overlap there is no constraint — a mirror layer typically extends past its
partner, which is the point.

**Authoring rule.** The mirror layer inherits its partner's `role` and treats
its partner's outcomes as its mirror brief (§6.4), even for segments with no
overlap. Its whole existence is a response.

Worked from the §9 structure: `plot_2`'s final ring occupies segments 8-15
running **reflected**; `plot_3` opens on exactly that span running
**forward**. The two stories are simultaneously live on 8-15 pulling in
opposite directions, then `plot_2` goes dormant and `plot_3` carries its
inverted continuation through to the loop end. Verified: same span, opposite
polarity.

This is also where deliberate **keystone coincidence** shows up. In §9,
`plot_3`'s keystone lands on segments 16-19 — exactly `plot_0`'s keystone.
Two stories turning on the same hours is legal and often desirable: the loop's
central crisis and an episodic thread's crisis are the same event seen from
two altitudes. The only requirement is that the generated content make sense
as one event serving both. Distinct keystones remain the *default*, but
coincidence is an authoring choice, not an error (§7).

### 4.6 Connective tethers

A **tether** is any small link that opens and closes: a point of data that is
not part of the larger story arc but has a beginning and an end.

Examples of the class, deliberately varied:

- a character met once, met again later
- an item found, useful much later
- a locked door found, a key found, the door opened
- a rumour heard, later confirmed or disproved
- a debt incurred, later called in

What they have in common is *not* their content — it is their shape: two or
more beats, ordered, with nothing required between them. This is the
definition; the payload vocabulary is deliberately left open and will be
extended as real material is authored (§10.7).

Tethers are a distinct construct from plot layers. Conflating them is the
mistake to avoid: a plot layer is a story with a shape — descent, keystone,
ascent — while a tether has no shape at all, only beats.

#### Beats

A tether is an **ordered list of 2 or more beats**. There is no upper limit:
use as many as the link demands. Two is the common case (open, close); the
locked-door case needs three; a long-running acquaintance may need six.

Beats are written `[loop, order]` — an absolute position in campaign time,
not a position within one loop:

```yaml
tethers:
  - id: thread_0                 # 2 beats, inside loop 0 — met, met again
    beats: [[0, 2], [0, 13]]
    carry_scope: t0

  - id: thread_1                 # 3 beats — door found, key found, door opened
    beats: [[0, 5], [0, 17], [0, 22]]
    carry_scope: t1

  - id: thread_3                 # crosses the seam: opens loop 0, closes loop 1
    beats: [[0, 14], [1, 6]]
    carry_scope: t3

  - id: thread_5                 # 5 beats spanning four loops
    beats: [[1, 2], [1, 9], [1, 18], [2, 7], [3, 15]]
    carry_scope: t5
```

A bare integer is shorthand for `[0, n]`, so single-loop configs stay short.

Absolute time is `loop * loop_segments + order`. Ordering, spans, and
"is this tether open here" are all computed on that scalar.

Constraints, checked at config load:

- at least 2 beats, strictly increasing **in absolute time**
- every `order` in `[0, loop_segments)`; `loop >= 0`
- `carry_scope` unique per tether

The first beat opens, the last beat closes, and any beat between is a
**waypoint** — a step that advances the link without resolving it.

#### Crossing the seam

**Tethers may span any number of loops.** A tether can open on day 2 of week 1
and close in week 3; nothing about its shape changes, only the loop indices of
its beats. This is the mechanism for persistent multi-week campaign material
and the one construct in this spec that is *designed* to outlive a loop.

This is a deliberate exception to the rule that nothing crosses the seam.
Rings still may not (§6.6) — a ring that straddles the boundary has no way to
resolve, because its layer's state is cleared under it. A tether is different
precisely because it has no shape to resolve: it is state plus a list of
beats, and state is exactly what `loop_seam_carry` carries.

A tether that is open at a seam — first beat at or before the loop's last
segment, last beat after it — **must appear in that seam's carry** (§6.6).
That is what makes the next loop able to see it at all.

#### State

A two-beat tether sets one key at its open and clears it at its close.

A tether with waypoints needs state that **changes at each beat**, not a
single key held throughout. The locked door is the worked case: after beat 1
the door is known and shut; after beat 2 the key is held and the door is still
shut; only after beat 3 is anything resolved. One key cannot express that.

So the rule generalizes: each beat may set, replace, or clear keys in the
tether's own `carry_scope`, and every key it sets must be cleared by the final
beat. Nothing in a tether's scope may survive its close.

#### Cost

Two rules keep tethers honest:

1. **State persists between beats** (§6.2). That is the *entire* mechanical
   cost of a tether, and it is what makes a late payoff tracked state rather
   than a retcon at hour 150.
2. **A dormant tether must not consume a prompt slot.** It appears in the
   segment's state, but `build_prompt()` must not ask the planner to advance
   it between beats. A planner told to advance twenty segments of "the item is
   in the bag" will invent filler.

**Tethers do not count against the concurrency cap** — not while dormant, and
not at their beats. They are notes, not stories a viewer is asked to track,
and the cap exists to bound tracked stories.

**Advisory, not a failure (§8.2).** A tether beat is still a real beat, so
landing several on a segment already at the ring cap makes that segment carry
more than the cap nominally allows. Warn when a segment's ring concurrency is
at cap *and* it carries 2 or more tether beats; that combination is where an
hour genuinely overloads. A single beat on a segment at cap is fine — a tether
beat is usually a line, not a scene.

Placing a waypoint where the rings have headroom is the cheapest way to avoid
this. In §9, `thread_1`'s waypoint sits at segment 17 (ring concurrency 3)
rather than in the 8-15 stretch where four ring threads are already live.

A tether beat may deliberately land on a keystone. In §9, `thread_2` closes at
segment 19, inside `plot_0`'s keystone — the long-carried thing pays off
exactly at the loop's turn, which is usually the best available use of a
tether.

---

## 5. Schema fields

### 5.1 Arc segment fields

Added to `arc_schema.py` `REQUIRED_KEYS`:

| Field | Type | Meaning |
|---|---|---|
| `plot_path` | list | one entry per covering layer, outermost first (§5.2) |
| `mirror_of` | list of ids | mirror partners; may be empty for keystones, may hold several (§2.3) |
| `mirror_transform` | string | required when the segment is an ascent in `plot_0`; §6.3 |

`ring_role`, `ring_depth`, and the v2 `mirror_of` scalar are **removed**.
Global role now lives at `plot_path[0]`.

### 5.2 `plot_path`

```yaml
plot_path:
  - {plot: plot_0, ring: plot_0.0, role: descent,  polarity: forward,
     u_lo: "3/4", u_hi: "1"}
  - {plot: plot_1, ring: plot_1.0, role: keystone, polarity: reflected,
     u_lo: "0",   u_hi: "0"}
  - {plot: plot_2, ring: plot_2.1, role: ascent,   polarity: forward,
     u_lo: "1/3", u_hi: "2/3"}
```

`u_lo`/`u_hi` are exact fractions as strings. Keystone segments carry
`u_lo = u_hi = "0"`.

Like all derived values, `plot_path` is computable from `order` plus the
partition config. It is stored anyway because Layers 2 and 3 read the arc plan
without arc config access, and because a stored value can be diffed against
the derived one — turning silent drift into a caught error.

### 5.3 Unchanged meanings

- `order` — the source of truth; everything derives from it.
- `loop` — counts loops. `(loop, order)` is unique across all time.
- `continuity_in` / `continuity_out` — the strictly **linear** chain,
  `o-1 → o`. **Mirroring must never hijack these.** An ascent segment
  continues from its predecessor, never from its mirror. The diagnosed run
  broke by reaching backwards here; this rule must not be relaxed.
- `carry_in` / `carry_out` — same mechanism, now scoped per layer (§6.2).

---

## 6. Threads, state, and pairing

### 6.1 Thread identity

Every ring instance is a subplot thread with a stable positional id
(`plot_1.0`). Ids are positional, so the same slot always names the same
thread across regenerations.

### 6.2 Layer-scoped state

`state.carry_keys` is a closed vocabulary already enforced by
`vocab.unknown_carry_keys()`. Ring state rides those rails — anything the
structure wants to remember that is not a carry key is prose, and prose does
not survive a batch boundary.

Keys are prefixed by **layer**, not by tree level:

```yaml
state:
  carry_keys:
    - p0-<name>      # plot_0 scope: set at its keystone, held to loop end
    - p1-<name>      # plot_1 scope: cleared at its ring's end
    - p2-<name>      # plot_2 scope: cleared at its ring's end
    - t0-<name>      # tether scope: set at a beat, cleared by its last (§4.6)
```

Rules, mechanically checkable:

1. A keystone in layer `k` may set `pk-*` keys and no shallower scope.
2. A `pk-*` key must appear in `carry_in` for every segment after its keystone
   **within that ring's span**, and be absent after the span ends.
3. A `pk-*` key may not be cleared inside its own ring's span.
4. A tether beat may set, replace, or clear `tk-*` keys in its own scope. Any
   key it sets must appear in `carry_in` for every segment through to the next
   beat that changes it, and every key must be cleared by the tether's final
   beat. Nothing in a tether's scope survives its close. This is the entire
   mechanical cost of a tether — what makes a payoff tracked state, not a
   retcon.

Rule 2 makes a subplot's turn *observable*: the trace of an irreversible act
is state persisting to the end of the thread that committed it.

Layer prefixes rather than depth prefixes is what makes braiding work — a
braided ring's scope follows its own span, which tree containment cannot
express. Tether scopes work the same way, keyed to two points instead of a
ring.

### 6.3 Escalation

A deeper layer's keystone must not outrank a shallower one, or a day-scale
turn upstages the loop's centre.

Enforced by scope, not prose judgement: a `plot_2` keystone can only set
`p2-*` keys, cleared at its ring's end. It is structurally incapable of
changing the loop. Only `plot_0`'s keystone sets `p0-*`, and only `p0-*`
reaches the loop seam.

### 6.4 The mirror brief

An echo is a bug unless the second pass is *informed* by the first. So an
ascent segment is handed its mirror partners' outcomes as prompt content,
distinct from and additional to `previous_continuity`.

`build_mirror_brief(partners)` renders, per partner, exactly four things: `id`,
`synopsis`, `continuity_out`, `carry_out`. Nothing else — not full prose, not
neighbours. The planner needs to know what was *established*, not how it was
*narrated*; narration invites paraphrase, which is the duplication being
prevented.

With fan-in (§2.3) a segment may receive several briefs. State the
relationship explicitly, since it changes the writing task:

```
This segment answers 2 earlier beats at once. Their threads
converge here; do not address them separately.
```

And, load-bearing in every brief:

```
Your continuity_in must continue from the PRECEDING segment,
not from your mirror.
```

Without that line, handing the model an earlier segment's state is an
invitation to reproduce the exact diagnosed regression.

### 6.5 `mirror_transform`

Required on every `plot_0` ascent segment. Closed vocabulary, config-extensible:

| Value | Meaning |
|---|---|
| `knowledge_gained` | Same situation, now understood |
| `stakes_raised` | Same situation, higher cost of failure |
| `role_reversed` | The party now holds its opposition's former position |
| `bond_proven` | A tested relationship holds under worse pressure |
| `bond_broken` | That relationship fails, permanently |
| `debt_paid` | An obligation from the descent is discharged |
| `promise_betrayed` | An obligation from the descent is deliberately broken |
| `cost_revealed` | An apparent win is re-read as a loss |

**Variety requirement.** If more than half of `plot_0`'s ascent segments share
one value, the back half is tonally flat — most often an unbroken run of
`bond_proven`, which is a victory lap rather than a transformed echo. Warn,
do not error: a strong smell, but a legitimate authorial choice.

### 6.6 The loop seam

Requirements on the final segment of `plot_0`:

1. Shares spine material with order 0.
2. `carry_out` equals the **seam carry** — the union of `ring.loop_seam_carry`
   (static, default `{}`) and the live keys of every tether open across this
   seam (§4.6, computed). That union must equal next loop's order 0
   `carry_in`.
3. `mirror_transform` from the closing subset: `debt_paid`, `bond_proven`,
   `bond_broken`, `cost_revealed`. The others open questions; the seam closes
   one.
4. `continuity_out` must read as an opening as well as an ending — it becomes
   order 0's `continuity_in` next loop.
5. All `p1-*` and deeper keys cleared. Only `p0-*` and open `tk-*` keys may
   reach the seam.
6. Not verbatim: `synopsis` must differ from order 0's. Structural identity,
   textual difference.

Rule 2 is the change that makes multi-week tethers work. `loop_seam_carry` was
previously a static config value; it is now a **floor**, not the whole carry.
Computing the rest means a tether needs no per-loop bookkeeping — whether it
crosses is derived from its beat list.

**Rings must not straddle the seam; tethers may.** A ring that runs past the
loop boundary has no way to resolve, since its layer's state is cleared under
it. Every layer's final ring must end at or before the loop end — a layer that
goes dormant early (§4.2.1) is fine, one that runs off the end is not.
A tether is exempt because it has no shape to resolve: it is state plus beats,
and state is exactly what the seam carries.

**Cross-loop id collision.** `validate_batch()` rejects ids colliding with
`known_ids`, but next loop's order 0 deliberately reuses this loop's id with
`loop` incremented. Scope `known_ids` per loop — `(loop, id)` tuples, or
filter `existing_segments` by the loop being planned. A latent break that will
not surface until the second week runs.

### 6.7 Planning a loop that inherits open tethers

A loop is planned without knowledge of loops not yet generated. That is fine
for rings, which are loop-local, but a tether may open in loop 0 and close in
loop 3, so planning must handle three cases explicitly.

**Inbound.** At the start of a loop, the seam carry names every tether open
across the boundary. For each, `build_prompt()` supplies the tether id, its
live keys, and *which beat comes next* — nothing else. The planner needs to
know something is owed, not how it was originally narrated.

**Passing through.** A tether whose beats all fall outside this loop is
carried silently: its keys ride `carry_in`/`carry_out` untouched from order 0
to the final segment. It costs nothing and must not consume a prompt slot
(§4.6).

**Outbound.** A tether whose last beat lies in a future loop must have its keys
present in this loop's seam carry. A tether that closes in this loop must have
its keys absent from it.

Two validation rules follow, and they must be split, because one is checkable
now and one is not:

- **Per-loop, hard:** no beat may be an *orphan close* — a closing beat whose
  tether was never opened, in this loop or in the inbound seam carry. This is
  always checkable and must fail.
- **Global, advisory:** every tether eventually closes. This cannot be checked
  while later loops are unplanned, so it runs at the campaign horizon and warns
  rather than fails. A tether left permanently open is a dangling promise —
  usually a mistake, occasionally deliberate.

---

## 7. Config

The full worked config for the §9 structure. Every story is its own block
(§4.3); there is no implicit hierarchy beyond what `inherits` declares.

```yaml
ring:
  enabled: true
  loop_segments: 28
  loop_anchor: "sunday 00:00"    # see §10.1
  loop_seam_carry: {}
  max_concurrent_threads: 4      # §4.2.2
  max_main_stories: 4            # §4.3

  plots:
    - id: plot_0
      role: main
      mode: nested
      span: [0, 28]
      parts: [16, 4, 8]          # d, k, a — need NOT be symmetric
      polarity: forward
      children:
        descent: [4, 4, 8]
        keystone: [1, 2, 1]
        ascent:   [2, 4, 2]

    - id: plot_1
      role: relationship
      mode: nested
      inherits: plot_0

    - id: plot_2
      role: episodic
      mode: nested
      inherits: plot_1
      span: [0, 16]              # dormant from segment 16

    - id: plot_3
      role: episodic
      mode: braided
      span: [8, 28]              # dormant before segment 8
      parts: [8, 4, 8]
      polarity: forward
      mirror_of_plot: plot_2

  tethers:                        # §4.6 — notes, not stories; cap-exempt
    # beats are [loop, order]; a bare int is shorthand for [0, n]
    - id: thread_0                # 2 beats — met, met again
      beats: [[0, 2], [0, 13]]
      carry_scope: t0
    - id: thread_1                # 3 beats — door found, key found, door opened
      beats: [[0, 5], [0, 17], [0, 22]]
      carry_scope: t1
    - id: thread_2                # 2 beats — closes inside plot_0's keystone
      beats: [[0, 9], [0, 19]]
      carry_scope: t2
    - id: thread_3                # crosses the seam into loop 1
      beats: [[0, 14], [1, 6]]
      carry_scope: t3
    - id: thread_4                # 3 beats spanning three loops
      beats: [[0, 21], [1, 11], [2, 3]]
      carry_scope: t4
```

Validate at config load, not plan time:

- every `parts` sums to its span, all entries `>= 1`
- within each layer, rings do not overlap each other (§4.2.1)
- no ring crosses the loop boundary (§6.6); ending early is legal
- concurrency across the union of all layers never exceeds
  `max_concurrent_threads` on any segment (§4.2.2)
- `main`-role stories do not exceed `max_main_stories`
- every `mirror_of_plot` target exists, and the two layers have opposite
  polarity wherever their spans overlap (§4.5)
- every `inherits` target exists and is declared before its dependents
- every tether has 2+ beats, strictly increasing in **absolute time**
  (`loop * loop_segments + order`), every `order` in `[0, loop_segments)`,
  `loop >= 0`, and a unique `carry_scope` (§4.6). Tethers are **excluded**
  from the concurrency computation, and **may cross loop seams** (§6.6).
- if day alignment is wanted, `loop_segments` divides evenly by
  segments-per-day

Two rules from earlier drafts are **removed**:

- *"every layer's rings tile the loop exactly"* — wrong. Partial layers are a
  feature (§4.2.1), and this rule rejects every structure in §9.
- *"keystone spans across layers must be distinct"* — downgraded from a check
  to a note. Shared keystones are legal and often intentional (§4.5): two
  stories may turn on the same hours, viewed from different altitudes. The
  requirement is only that the generated content make sense as one event
  serving both. If a tool wants to surface it at all, make it an informational
  note, not a warning.

There is no `d == a` check. Its removal is the point of v3.

---

## 8. Validation

Pure functions in the house style: return problem strings, never raise,
append-then-skip on type failures.

### 8.1 Per-batch

`validate_plot_path(segments, config)` — each stored `plot_path` matches the
path derived from `order` and the partition config: correct layer sequence,
correct roles, `u` intervals matching the partition, and polarity satisfying
LAW B at every step. Tolerates ragged depth (§4.4).

`validate_mirror_pair(seg, partners)` — run when at least one partner is
planned. Passes when the requirement holds for **at least one** partner:

1. **Echo** — shares spine material with a partner. Shared material is
   *required*; reuse was never the problem, unmotivated reuse was.
2. **Transformation** — `carry_out` differs from every partner's. A mirror
   ending in an identical state transformed nothing.
3. **Irreversibility** — a `plot_0` ascent segment's `carry_in` contains the
   `p0` keystone flag. An ascent that has forgotten the keystone is a replay.
4. **Non-regression** — `continuity_in` present and not sourced across a gap.
5. **Anti-verbatim** — `synopsis` differs from every partner's, case- and
   whitespace-normalised. Crude, cheap, catches the laziest failure.

Absent by design: **no comparison of role labels** (LAW C).

### 8.2 Whole-plan — `validate_ring_arc(segments, config)`

Run after `plan_arc()` and **before Layer 2 starts**.

| Check | Catches |
|---|---|
| Every order `0..N-1` present exactly once | Gaps of any kind |
| Every layer's every ring has its keystone planned | A missing centre, at any scale |
| Each keystone's `carry_out` contains its layer's flag | Keystones that are not irreversible |
| Every `plot_0` ascent `carry_in` contains the `p0` flag | Ascent that forgot the keystone |
| Every mirror pair passes §8.1 | Un-transformed replay |
| Identical spine sets only within a thread or a mirror pair | Reuse outside the pairing |
| Pairing is symmetric and total per ring (§2.2) | Broken partition config |
| Mirror siblings have opposite polarity (LAW A) | Broken polarity inheritance |
| Layer-level mirrors have opposite polarity where spans overlap (§4.5) | Broken layer mirroring |
| `pk-*` keys held across their ring and cleared after (§6.2) | Subplot state leaking between threads |
| A tether's keys persist between its beats (§4.6) | A payoff with no tracked setup |
| Every tether key is cleared by its final beat | Tether state leaking past its close |
| Tether beats strictly increase in absolute time | Malformed tether |
| No orphan close: every closing beat has an open, local or inbound (§6.7) | A payoff for something never set up |
| Open tethers' keys appear in the seam carry (§6.6) | A cross-loop tether silently dropped at the seam |
| Concurrency never exceeds the cap on any segment (§4.2.2) | Unwatchable thread pile-up |
| No layer's ring crosses the loop boundary | Unresolvable braided thread |
| Final segment satisfies §6.6 | Missing loop closure |
| `continuity_in` non-empty for every order `1..N-1` | Broken linear chain |

**Failing this must abort before Layer 2.** Layers 2 and 3 are where the GPU
hours go; spending them on a spine already known broken is the most expensive
mistake this pipeline can make, and it is the mistake the diagnosed run made.

### 8.3 Generation order

Batching must let §8.1 evaluate — a mirror must exist when its partner is
planned. Required phases, no batch straddling them:

1. **All keystones first**, shallowest layer outward. A skipped `plot_0`
   keystone is **fatal**: raise rather than continue. Other batches may
   exhaust `max_attempts` and skip with a warning — tolerated degradation. An
   arc without its centre is not degraded, it is structureless. Generating it
   first also means it cannot be starved by upstream attrition, which is
   exactly how the diagnosed hole formed.
2. **Descent orders ascending**, so `previous_continuity` is always available.
3. **Ascent orders ascending**, starting immediately after the `plot_0`
   keystone, so the first ascent segment's predecessor was planned in phase 1
   and the linear chain is unbroken across the phase seam. That seam is
   precisely where the diagnosed run failed.

With asymmetric parts the phases are unequal in size — expected. Requires Bug
1 fixed, or `batch_size` cannot align to phases.

### 8.4 What is deliberately not validated

Whether a transformation is *dramatically satisfying*. No check here
distinguishes a genuine role reversal from a competent-sounding claim of one.
These checks make the structure **present and non-degenerate**; a human or a
critic model judges whether it is **good**.

Do not let a green tick read as a story-quality claim. That conflation is why
the diagnosed run reported clean while being broken. "Ring-valid" is strictly
stronger than "schema-valid" and strictly weaker than "well-told".

---

## 9. Worked abstract example

Structure only. `plot_0` asymmetric at `16/4/8` — long approach, hard turn,
brisk consequence. `plot_1`/`plot_2` nested; `plot_3` braided as `plot_2`'s
layer-level mirror. Verified against the recursion.

```
plot_0  [0..27]  forward         16 / 4 / 8
 |
 +-- descent [0..15]   fwd   ->  plot_1 partition 4/4/8
 |     +-- [0..3]    descent   fwd    -> plot_2  1/2/1
 |     +-- [4..7]    keystone  refl   -> plot_2  1/2/1
 |     +-- [8..15]   ascent    refl   -> plot_2  2/4/2
 |
 +-- keystone [16..19] refl    ->  plot_1 partition 1/2/1
 |
 +-- ascent  [20..27]  refl    ->  plot_1 partition 2/4/2

plot_3  [8..27]  forward  braided   8 / 4 / 8      mirror_of_plot: plot_2
        descent [8..15]  +   keystone [16..19]  −   ascent [20..27]  −

tethers (§4.6 — open ▶ / waypoint ◆ / close ◀; dormant between; cap-exempt)
  loop 0:   thread_0   2 ▶ ──────── 13 ◀        thread_2   9 ▶ ──── 19 ◀
            thread_1   5 ▶ ──── 17 ◆ ──── 22 ◀
  crossing: thread_3  0/14 ▶ ══seam══ 1/6 ◀
            thread_4  0/21 ▶ ══seam══ 1/11 ◆ ══seam══ 2/3 ◀
```

Resulting per-segment stack (`+` forward, `−` reflected, span in parens):

| seg | plot_0 | plot_1 | plot_2 | plot_3 | tethers |
|---|---|---|---|---|---|
| 0 | descent + (16) | descent + (4) | descent + (1) | — | · |
| 1-2 | descent + (16) | descent + (4) | keystone − (2) | — | t0▶ |
| 3 | descent + (16) | descent + (4) | ascent − (1) | — | · |
| 4 | descent + (16) | keystone − (4) | ascent + (1) | — | · |
| 5-6 | descent + (16) | keystone − (4) | keystone + (2) | — | t1▶ |
| 7 | descent + (16) | keystone − (4) | descent − (1) | — | · |
| 8-9 | descent + (16) | ascent − (8) | ascent + (2) | descent + (8) | t2▶ |
| 10-13 | descent + (16) | ascent − (8) | keystone + (4) | descent + (8) | t0◀ |
| 14-15 | descent + (16) | ascent − (8) | descent − (2) | descent + (8) | t3▶ |
| 16 | keystone − (4) | ascent + (1) | — | keystone − (4) | · |
| 17-18 | keystone − (4) | keystone + (2) | — | keystone − (4) | t1◆ |
| 19 | keystone − (4) | descent − (1) | — | keystone − (4) | t2◀ |
| 20-21 | ascent − (8) | ascent + (2) | — | ascent − (8) | t4▶ |
| 22-25 | ascent − (8) | keystone + (4) | — | ascent − (8) | t1◀ |
| 26-27 | ascent − (8) | descent − (2) | — | ascent − (8) | · |

Tether beats in wall-clock terms (day 1 = Sunday, 4 segments per day):

| tether | beats `[loop, order]` | wall clock | carried |
|---|---|---|---|
| `thread_0` | 0/2 → 0/13 | wk1 day 1 12:00 → wk1 day 4 06:00 | 66 h |
| `thread_1` | 0/5 → 0/17 → 0/22 | wk1 day 2 06:00 → day 5 06:00 → day 6 12:00 | 102 h |
| `thread_2` | 0/9 → 0/19 | wk1 day 3 06:00 → wk1 day 5 18:00 | 60 h |
| `thread_3` | 0/14 → 1/6 | wk1 day 4 12:00 → wk2 day 2 12:00 | 120 h |
| `thread_4` | 0/21 → 1/11 → 2/3 | wk1 day 6 06:00 → wk2 day 3 18:00 → wk3 day 1 18:00 | 228 h |

`thread_1` is the locked-door shape: door found at beat 1, key found at the
waypoint, door opened at the close. Its waypoint sits at segment 17, where
ring concurrency is 3 rather than the 4 running across 8-15.

`thread_3` and `thread_4` cross the seam, so at the end of loop 0 the seam
carry holds both — computed from their beat lists, not configured. At the end
of loop 1 only `thread_4` remains open. Loop 1's planner is told two things
about `thread_3`: it is open, and its next beat is at order 6.

Points worth reading off it:

- **Ragged depth.** Segments 0-3 carry three threads, 8-15 carry four (at the
  concurrency cap), 16-27 carry three. Density varies deliberately; the
  four-thread stretch is the busiest hour of the loop.
- **Adjacent same-role, opposite-direction.** `plot_1`'s keystone at 4-7 runs
  reflected; `plot_2`'s keystone at 5-6 runs forward, because it is a keystone
  inside an already-reflected parent.
- **Asymmetric pairing.** `plot_0` has `d=16, a=8`, so each ascent segment
  answers exactly two descent segments (§2.3) — the back half is doing double
  duty, which is what "fast fall" means structurally.
- **Layer mirroring.** On segments 8-15, `plot_2` runs reflected while
  `plot_3` runs forward over the identical span — two live stories pulling in
  opposite directions. `plot_2` then goes dormant and `plot_3` carries the
  inverted continuation to the loop end.
- **Keystone coincidence.** `plot_3`'s keystone (16-19) is exactly `plot_0`'s.
  Deliberate: the loop's central crisis and the episodic thread's crisis are
  one event seen from two altitudes.
- **The seam.** Order 27 is locally a `descent` in `plot_1` while `plot_0` is
  in `ascent`. Both true; the loop closes on falling momentum rather than a
  hard stop.

---

## 10. Resolved decisions

**10.1 Loop anchor — RESOLVED: Sunday 00:00.** The loop begins at midnight
between Saturday and Sunday, which is Sunday 00:00. The earlier "Saturday
00:00" phrasing referred to the same wall-clock moment described informally as
"Saturday night" and is **retired** — it should be corrected wherever it
appears in project materials to avoid a 24-hour offset. Config:
`ring.loop_anchor: "sunday 00:00"`.

**10.2 Keystone time-of-day — RESOLVED: unconstrained.** Keystones may fall at
any hour, and **two stories may share a keystone**. Coincident keystones are
legal and often desirable (§4.5) — the only requirement is that generated
content make sense as a single event serving both threads. The former
"distinct keystone spans" config check is removed (§7).

**10.3 Layer count and roles — RESOLVED with limits.** At most **4 concurrent
threads on any segment** (§4.2.2) and at most **4 `main` stories per loop**
(§4.3). Beyond that a viewer tracks nothing. Non-`main` roles may exceed four
*instances* provided concurrency stays within the cap, since short threads are
rarely all live at once. Threads may start together or end together
deliberately — simultaneous starts read as a new chapter, simultaneous ends as
a convergence.

Every story carries its own config block with its own span, parts, mode, and
role (§4.3), so the layer number is a label rather than a fixed tier.

**10.4 Nested vs braided — RESOLVED as recommendation.** Nested for `plot_0`
and `plot_1`; braided for shorter episodic layers, where staggering pays off
most and scope-leak risk is lowest. Both are supported per layer via `mode`.
Long-range connective material is a tether (§4.6), not a braided layer.

**10.5 Tether budget — RESOLVED: tethers are exempt from the cap.** A tether
(§4.6) is a note connecting two points, not a story a viewer tracks, so it
costs nothing against `max_concurrent_threads` — dormant or at its endpoints.
The only residual concern is *beat density*: a tether endpoint is still a real
beat, so §4.6 warns when a segment already at the ring cap also carries 2+
tether endpoints. Advisory only.

Two bugs in `plan_arc.py` documented in §0.1 have been reported separately and
are out of scope for this spec.

### Remaining open items

**10.6 Segment-count alignment.** 28 segments at 6 hours gives exactly 4
segments per day. Any change to `arc.segment_hours` or `arc.hours_total`
breaks day alignment and requires new partition tables. Validate divisibility
at config load and fail loudly rather than discovering a ragged final day
mid-run.

**10.7 Tether payload vocabulary — deliberately deferred.** §4.6 defines a
tether's *shape* (an ordered beat list) and leaves its content open by design:
a tether is "any point of data that is not part of the larger story arc but
has an open and a close". Observed cases so far — a character met and met
again, an item found and later used, a door found / key found / door opened,
a rumour raised and settled, a debt incurred and called in.

A closed `kind` vocabulary would let `build_prompt()` phrase a close correctly
(a `debt` close should discharge something; an `item` close need not) and let
validation check it. **Do not close this vocabulary yet** — it should be
derived from real authored material rather than guessed at, and extended as
the campaign is written. The shape is stable; the taxonomy is not.

**10.8 Tether density per loop.** No limit is currently defined on how many
tethers may be live at once. Since they are cap-exempt, nothing stops twenty
of them, at which point the planner's state block becomes unreadable even
though no concurrency rule is broken. Multi-week tethers make this sharper:
a loop can now inherit open tethers it did not open, so density is a property
of the *campaign*, not of one loop's config. A soft ceiling on
simultaneously-open tethers (roughly one per day, so ~7) is suggested; confirm
once real material exists.

**10.9 Campaign horizon.** Cross-loop tethers imply a planning horizon: the
"every tether eventually closes" check (§6.7) needs a point at which the
campaign is considered complete. Undefined for now — a rolling horizon (warn
about tethers open longer than N loops) is probably better than a fixed end
date for an ongoing series.

---

## 11. Implementation order

1. Fix the two `plan_arc.py` bugs (§0.1). Independently correct.
2. **Delete** `ring_skeleton()`, `ring_depth`, and any span-symmetry check.
   They are wrong under v3, not merely incomplete.
3. Implement the partition recursion, `SIGN`/polarity (LAW B), and
   `mirror_partners()` with exact `Fraction` arithmetic. Unit-test:
   symmetry and totality across `d,a ∈ 1..12`; reduction to `d-1-i` when
   `d == a`; terminal-span guard (§1.3); ragged depth.
4. Add the per-story `ring.plots` config (§7). Validate: parts sum, no
   intra-layer overlap, no loop-crossing, concurrency cap, `main` count,
   `mirror_of_plot` polarity opposition, `inherits` resolution. Do **not**
   validate loop tiling or distinct keystones — both were removed.
5. Extend and layer-scope `state.carry_keys`, including tether scopes.
6. Add `plot_path`, `mirror_of` (list), `mirror_transform`; implement
   `validate_plot_path` and `validate_mirror_pair`; wire into
   `validate_batch()`.
7. Extend `build_prompt()` with per-layer position and mirror briefs, handling
   multi-partner fan-in and dormant-tether suppression (§4.6).
8. Rewrite `plan_arc()` batching into the phases of §8.3, with the fatal
   keystone rule.
9. Add `validate_ring_arc()` and gate Layer 2 behind it.
10. Segment layer: ring-aware `build_expand_prompt()`, sibling mirror briefs,
    `validate_ring_children()`.
11. Fix per-loop `known_ids` scoping (§6.6) before any second-week run.
12. Confirm §10.5, then map material onto the structure.

Steps 1-3 are independently valuable and change no generated content. Do not
skip step 9 to reach a run faster — it is the only step that converts this
document from guidance into a guarantee.
