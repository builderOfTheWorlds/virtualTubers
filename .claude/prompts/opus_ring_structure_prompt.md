# Prompt for Opus — design a ring-composition story structure document

Paste everything below the `---` into Opus. It has everything it needs to
design the document without further repo access, but the two supporting
files (`ring_structure_diagram.md`, `ring_arc_findings.md`) are referenced
inline in case Opus (or you) want the fuller detail.

---

## Task

You are designing a **story-structure specification document** for a
24/7 livestreamed AI-generated fantasy campaign ("virtualTubers"). The show
runs on a **168-hour weekly loop**: it starts every Saturday at 00:00, runs
continuously through 28 six-hour segments, and then resets — the same show
starts again from the top next Saturday. Think of it like a TV series that
re-runs its own pilot premise every week, but isn't literally repeating
itself: it's a new telling of a structurally similar story, with room for
real variation, consequence, and callbacks.

We want the story shape to use **ring composition** (aka chiastic structure)
— the technique underlying Mike Klimo's "Star Wars Ring Theory" essay, and
originally found in Homer, the Torah, Beowulf, and other oral-epic
traditions. Ring composition builds a story as matched pairs of beats
(A, B, C, B', A' ...) that mirror each other on either side of a **central
keystone** — the single most important turning point, usually near the
narrative's midpoint. The mirrored pairs echo each other in theme, imagery,
or dramatic function, but the second half is TRANSFORMED by what happened at
the keystone, not a repeat of the first half.

Critically: **this structure is fractal/self-similar at any scale.** The
same A B C B' A' shape that organizes the full 168-hour week should also be
usable to organize a single 6-hour segment, and (in principle) a single
scene within a segment. Smaller rings nest inside larger ones.

## Existing system this has to fit into (don't redesign, extend)

This is a real, running content generator, not a green-field design. Ring
composition needs to be layered onto what already exists:

- **Arc layer** (top level): a 168h campaign week is planned as **28
  segments of 6 hours each**. Each segment already has these fields: `id`,
  `order` (0-27), `loop` (integer — already exists, marks which weekly
  repeat this is), `hours`, `spine_scenes` (authored scene ids), `synopsis`,
  `continuity_in`, `continuity_out`, `carry_in`/`carry_out` (state passed
  between segments, from a closed vocabulary of flags/moods).
- **Segment layer** (mid level): each 6h segment is recursively split into a
  **tree** of weighted child nodes (via an LLM "expand" step), down to leaves
  small enough to plan directly into dialogue slots. Every child already
  carries `order`, `title`, `summary`, `continuity_in`, `continuity_out`,
  and a relative `weight` (how much airtime it gets). This tree already
  reconciles word budgets exactly across levels.
- **Dialogue layer** (bottom level): each leaf's slots get 1-3 "takes"
  (alternate phrasings) generated per slot — this is where variant pools /
  callback phrasing execution would actually happen, not where structure is
  decided.
- The arc planner and segment "expand" planner are each driven by an LLM
  system prompt + user prompt built from Python (`arc_schema.py`,
  `segment_schema.py`). Those prompts currently do NOT mention ring
  composition, keystones, or mirroring at all — they just ask for "a
  coherent arc" / weighted children. That's the gap this document needs to
  close.

## How the underlying generator actually shapes its input/output

There's a separate GUI ("Campaign Manager") for submitting jobs and browsing
results, but it's just a thin form/viewer over a REST API — it has no
bearing on story design and you can ignore it entirely. What matters is the
**3-layer generator itself** (`utilities/3LayersWeeklyGeneration`, mirrored
into a `3layer-generator` service): it is what actually turns a config +
campaign pack into arc/segment/dialogue content, and its INPUT SHAPE is what
your design has to fit into. Three things define that input shape:

1. **The config file** (`generation*.yaml`) is what parameterizes a run —
   this is the knob-set your design must express itself through. Relevant
   sections: `arc.hours_total` / `arc.segment_hours` (together fix segment
   count — 168/6 = 28 for the real run), `arc.batch_size` (how many
   segments the arc planner requests from the LLM per call — batch
   boundaries are where continuity state can get lost, as seen below),
   `segment.tree.*` (`max_leaf_slots`, `max_children`, `max_depth`,
   `min_node_words`, `leaf_density_floor` — controls how each segment's
   internal tree can recurse), `state.flags` / `state.moods` /
   `state.carry_keys` (the CLOSED vocabulary that `carry_in`/`carry_out`
   values must draw from — any new "this callback references that earlier
   beat" mechanism your design proposes has to either reuse this vocabulary
   or extend it, not invent an untyped side channel).
2. **The campaign pack** (`campaigns/ashiorid_1/`) supplies the closed set
   of legal `spine_scenes` ids, cast ids, and lore stems that any generated
   segment/slot must reference — your worked 28-segment skeleton (item 7
   below) must use IDs from this real pack, not invented ones.
3. **The planner prompt builders** (`arc_schema.py build_prompt()`,
   `segment_schema.py build_expand_prompt()` / `build_leaf_prompt()`) are
   the actual mechanism that turns (config + pack + prior state) into the
   text an LLM sees. Your design's ring vocabulary/keystone/mirror-pairing
   rules ultimately have to be expressible as additions to what these
   functions put in front of the model — e.g. "tell the planner which
   ring_role this batch is filling and what its mirror pair's outcome was"
   is only useful if it can be phrased as new lines these functions emit
   and new required keys `validate_batch()`/`validate_children()` can
   check. Design in those terms (concrete fields, closed vocabularies,
   prompt content) rather than abstract narrative theory alone.

Critically: **job "completed" only means the LLM's reply parsed and
validated against the SCHEMA** (required keys, closed vocabularies,
word-budget reconciliation) — it says nothing about whether the content
satisfies any STORY-STRUCTURE property like ring composition, because no
such property is checked anywhere today. That gap between "schema-valid"
and "structurally sound" is exactly why the run below reported as clean,
successful jobs while being structurally broken. Your validation-checklist
requirement (item 6 below) needs to close that gap as new checks these same
`validate_*` functions could run, not as something only a human can catch by
eyeballing output.

## What we already tried and what broke (use this as real evidence)

We ran the arc planner (no ring guidance at all) against this system and
diagnosed the output. Full failure analysis is in `ring_arc_findings.md`;
summary:

1. **The keystone landed in a hole.** With no midpoint requirement, the
   planner's batching produced a gap in the middle of the arc (orders 12-17
   of 28 were never generated) — exactly where the ring's most important
   beat should sit. Nothing enforces that the midpoint gets planned at all.
2. **The back half replayed the front half instead of mirroring it.**
   Orders 19-23 reused the identical `spine_scenes` as orders 6-10, with
   `continuity_in` text that snapped back to an EARLIER story state (right
   after the manor) instead of continuing from where the arc actually was.
   This happened because a later batch lost the prior batch's continuity
   state across a gap — proving that "mirroring" cannot be left implicit;
   the second-half segment must be explicitly told what its FIRST-half
   pair established, not just handed "whatever the immediately-prior batch
   said."
3. **No loop-closure segment was generated at all** — the run stopped at
   order 23 of 28, well before any "return home" beat that would mirror
   the opening "leave home" beat and hand off cleanly to next week's loop 0.

Your document needs to make these three failure modes structurally
impossible, not just theoretically discouraged.

## Reference example (from the user, use as your worked example)

> Story start: the hero leaves home in search of adventure.
> First quarter: the hero joins a group of other adventurers and they set
> out for a quest together.
> Second: the party faces hardship.
> Third: the party overcomes evil.
> Final: the party disbands and our hero heads home.

Mapped to a 5-point ring: A (leave home) / B (party forms) / C (hardship,
keystone) / B' (overcome evil together — mirrors B, but the bond is now
tested & proven) / A' (disband, head home — mirrors A, but the hero is
changed). Use this as your worked example when illustrating the top-level
168h ring, then show how the SAME shape nests inside one 6h segment.

## What the document must define

1. **A ring-position vocabulary** usable in the arc/segment schemas without
   breaking existing required fields — e.g. a `ring_role` value from a
   closed set (`A`, `B`, `C`, `B'`, `A'`, or an extensible middle set for
   longer rings), and how it maps onto `order`/`loop`/`continuity_in`/
   `continuity_out`/`carry_in`/`carry_out`.
2. **Explicit keystone placement rules** — how the arc planner should be
   told, structurally (not just hoped for), to place exactly one keystone
   near the midpoint, and what a keystone segment's `synopsis` must
   accomplish dramatically (a true point of no return / thematic reveal).
3. **Explicit mirror-pairing rules** — how a second-half segment (B', A')
   should be given its paired first-half segment's outcome (not just
   "whatever the previous batch said") so the echo is provably a
   transformation, and concrete guidance for HOW it should differ (same
   location/cast dynamic/theme, changed stakes/knowledge/relationship).
4. **Fractal nesting rules** — how the same A/B/C/B'/A' shape applies inside
   a single segment's child tree, and whether/how a segment inside the
   top-level "C" block should carry its OWN internal keystone.
5. **Loop-closure rules** — precisely what must be true of the final segment
   (A') so it both closes THIS week's ring and sets up next week's `loop+1`
   opening (same spine scenes/ids, `loop` incremented, but not verbatim
   narration — variant pools handle wording, structure handles the shape).
6. **Validation checks** — a short checklist (mirroring `arc_schema.py`'s
   existing `validate_batch` pattern) that could catch, mechanically, each
   of the 3 failure modes above (missing keystone, un-mirrored/duplicate
   back half, missing loop-closure) before generation is considered done.
7. **A worked 28-segment skeleton** for the ashiorid campaign specifically —
   assign each of the 28 order slots a ring role and a one-line beat
   description, consistent with the existing campaign content (Leto's
   manor, the Bahadur, Malmont, the Amulet of Wonder, moonwells/magic-lost
   vs magic-retained) as seen in the real segment ids from the diagnosed
   run: invitation, letos-manor, party-attack, burn-it-down,
   grovley-revelation, the-vault, malmont-arrival, bahadur-revealed,
   amulet-map, malvakar-riddle, portal-encounter, portal-choice — reuse and
   extend these rather than inventing an unrelated plot.

## Output format

A single markdown document, structured as sections matching the 7 numbered
requirements above, written so it can be handed directly to whoever edits
`arc_schema.py` / `segment_schema.py` next as an implementation spec — prefer
concrete field names and closed vocabularies over prose description where
possible, since the existing schema code already validates against closed
vocabularies everywhere (`vocab.unknown_carry_keys`, `vocab.validate_slot`,
sensitivity enums, etc.) and this should follow that same convention.
