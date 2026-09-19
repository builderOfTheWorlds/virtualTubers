# Retargeting the 3-Layer Generator: Scenes, Not Dialogue

**Status:** design plan, pending review. No code changed yet.
**Date:** 2026-09-19
**Supersedes the output contract in** `utilities/3LayersWeeklyGeneration/PLAN_v1.md` §Context (Layer 3).
**Companion specs:** `ring_composition_spec.md` (v3.3), `docs/campaign_content_expansion.md`.

---

## 0. Build status (this build, 2026-09-19)

- **Phase 1.3 (pack gate) — done.** `app/campaign/validator.py` now enforces
  the 13 previously-red rules from `tests/test_campaign_validator.py`
  (ambient exemptions, ambient-pool entries, lore-stem lookups, empty
  variant-pool entries, ambient-as-branch-target); 65/65 pass.
  `utilities/3LayersWeeklyGeneration/src/pack_gate.py` is the thin
  staging-directory gate the plan names.
- **Scene pipeline — done.** `scene_writer.py`, `source_adapter.py`,
  `author_scenes.py`, `spine_chain.py` under the generator's `src/`;
  644 generator-tree tests pass (40+ from this build's four new test files,
  including 15 spine-chain tests that prove the sequential
  "previous scene in context" ordering with a fake LLM).
- **Two-source live run — done (stalled only, results in the tree).**
  ashiorid (structured vault) and Harry Potter (flat 1.09M-word text),
  both spine and ambient, both PASS the gate. Comparison and the gate
  failure that the local vocabulary filter fixed: `.claude/prompts/source_shape_test_report.md`
  and `.claude/prompts/hp_source_shape_test/comparison.json`.
- **Closed-vocabulary filter — done, with one real defect found and fixed
  in the process:** `author_scenes.cast_ids` used to glob `cast/*.yaml`,
  which can diverge from `campaign.yaml`'s registered `gm` + `players:`
  (the validator's actual source of truth, `app/campaign/pack.py:210-214`).
  Now shared through `known_vocabs()` / `lore_stems()`, so the filter and
  the gate cannot disagree.
- **Self-loop spines handled, not forbidden.** The HP test
  pack's seed is a self-loop spine (`default_next: its own id`), which is
  *legal and deliberate* per
  `test_self_referencing_scene_is_allowed` ("loops are the premise of the
  show, not a bug"). `spine_chain` now treats such spines as **loop
  insertion points**: a generated chain splices in (the seed points at the
  new first) and the loop is restored (the new last points back at the
  seed). This was found when the HP run reported zero open spines; the
  fix is `spine_chain._base_pack_insertion_points` + the `loop_closure`
  patch, tested by
  `test_promote_chained_into_a_loop_spine_restores_the_loop_and_validates`.
- **Phase 1.5 (continuity contract) — done, promoted.** `Scene` gained
  `continuity_in`/`continuity_out` (additive, absent = no constraint, wrong
  shape is a PackError). All 15/15 ashiorid spine scenes in
  `campaigns/ashiorid/` carry the contracts promoted from
  `.claude/prompts/continuity_backfill_v2.yaml`; ambient scenes correctly
  carry none. `pytest -q --ignore=tests/test_episode_validator_show.py`:
  2159 passed / 3 known tile_pane failures (unrelated, out of scope).
- **Pre-flight — done.** `campaigns/ashiorid/` seed pack (49 scenes, 5 cast,
  loads clean); `ashiorid_1` + `ashiorid_v00` moved to `campaigns/_archive/`.
- **§2.6 closed-vocabulary gap FIXED (latent defect found in live run 1):**
  `author_spines`/`author_scenes` had local backstops for speakers and lore
  stems but NOT for action-beat `primitive`s. The model's first Ashiorid
  spine invented `primitive: investigate` / `interact` (not in the pack's
  enabled set `roll_check, cast_spell, attack, move_to, search,
  reveal_memory`) — `validator.py:218-224` would have rejected it. Added
  `pack_primitives()` + `_beat_primitive_ok` filter in `author_scenes.py`
  (same architecture as the existing speaker/lore backstops), with 2
  regression tests (`test_author_scenes.py`). The gate still catches
  anything novel.
- **Ashiorid week spine build — in progress (run 4, proc_c5043aac161b).**
  12/12 ambient staged and gate-clean (run 1 artifacts, preserved under
  `.claude/prompts/ashiorid_week_stage/ambient/`); spine continuation
  (Shewolf of Idra → Lighthouse Campaign → Mutant History–Alien World)
  authored sequentially from the malvakar-riddle seed, gated with 0 errors
  (run 3 reached "0 errors, 2 warnings" = the expected un-chained
  overlay). Promotion of spine + splice (malvakar-riddle → first new
  scene) is still the explicit human step; loop-closure stays the
  operator's choice per the ring.

---

## 1. The correction

The 3-layer generator + ring composition was built to **write the show's
dialogue**. It was supposed to **write the show's source material** — new arcs
and new `scenes/*.yaml` — from new input material, leaving dialogue to the
already-working render-time path.

| | As built | As intended |
|---|---|---|
| Layer 1 (arc) | Plans 28x6h segments, **scheduling existing scenes** | Plans the arc **and what new scenes must exist to fill it** |
| Layer 2 (segment) | Expands a segment into a slot tree | Same — slots become scene specs |
| Layer 3 | Calls the improviser, writes **voiced takes** to `campaigns/<pack>/generated/` | Writes **new `campaigns/<pack>/scenes/*.yaml`** in authored pack format |
| Dialogue | Generated offline, frozen on disk | Generated **at air time** by `renderer.py` + `improviser.py` (variant pools + `improv: true`), as `docs/campaign_content_expansion.md` already specifies |

### 1.1 Why this is the right correction, not a matter of taste

`docs/campaign_content_expansion.md` already states the governing rule:

> **the spine stays authored and deterministic; wording, filler and memory
> become generated.**

The existing runtime already implements that rule — variant pools cycle
phrasings, `improv: true` hands a beat to a persona LLM at render time, and
ambient scenes are generated live from a `prompt:`. Layer 3 duplicated that
machinery *offline and frozen*, which produces strictly worse output:

- **It freezes what was designed to vary.** A take on disk plays the same
  words forever; the render-time path never repeats wording. The whole
  premise for replaying ten spine scenes hundreds of times is that the plot
  repeats and the wording does not.
- **It does not grow the pack.** 2,671 files in
  `campaigns/ashiorid_1/generated/` did not add a single scene. The scene
  library is still the 49 hand-authored files. We generated a lot of words
  and zero story.
- **It bypasses pack validation.** Takes never pass `load_pack()`, so nothing
  checks their lore stems, cast ids, or primitives. Hence the known defects
  already recorded in the ops skills: beats mislabelled `kind: narration`,
  invented characters absent from `pack.cast`.

### 1.2 The blocker that proves the intent was never implementable

`arc_schema.py:282-286`:

```python
# Check spine scenes
unknown = vocab.unknown_scene_refs(seg["spine_scenes"])
if unknown:
    problems.append(f"segment {seg['id']!r} spine_scenes references unknown scene {scene_id!r}")
```

The arc validator **rejects any spine scene id that is not already in the
pack**. Layer 1 can only ever re-schedule the scenes a human already wrote.
There is no code path by which the generator can propose a new spine scene.
This is the single most important thing to change, and everything downstream
is comparatively mechanical.

### 1.3 The good news: Layer 2 is already most of a scene author

`segment_schema.py:338-340`:

```python
AMBIENT_REQUIRED = ["slot_id", "kind", "prompt", "lore", "participants",
                    "sensitivity", "depends_on"]
SPINE_REQUIRED   = ["slot_id", "kind", "scene_ref", "participants"]
```

An ambient slot already carries `prompt`, `lore` and `participants` — which is
almost exactly the ambient-scene YAML contract in
`docs/campaign_content_expansion.md`. Ambient slots are *already scene
definitions*; they were simply consumed by the improviser instead of being
serialized to `scenes/`. Emitting them is a serializer, not a redesign.

The genuinely new work is the **spine** side: a spine slot today only holds
`scene_ref` (a pointer). It must be able to hold an authored scene — beats,
branches, `default_next` — which is a new Layer 2.5 concern.

### 1.4 What ring composition is actually for, under the correction

Ring composition does **not** change meaning; it changes *what it shapes*.
Today it shapes the order in which airtime is spent. Under the correction it
shapes **the story being written** — descent/keystone/ascent become the
dramatic role of each *new scene*, and `mirror_of` / `mirror_transform` become
authoring instructions ("this new scene mirrors that one, transformed by
`cost_revealed`"). This is a better fit than what it has now: the ring spec is
explicitly campaign-agnostic *shape*, and shape belongs to composition, not to
scheduling. `ring.py` is unchanged by this plan.

`pack.py:294-330` already added `ring_tone` and `mood` to `Scene` — the pack
format is pre-wired to receive ring-shaped scenes. That was built for this and
is currently unused in the authoring direction.

---

## 2. Target architecture

```
  NEW SOURCE MATERIAL  (Obsidian vault, lore notes, plot outline)
            |
            v
  [ Layer 0: ingest ]  -- NEW --------------- lore/*.md + a source digest
            |
            v
  [ Layer 1: arc ]  ring-shaped              arc_plan.yaml
   plans segments AND declares which are      segments may now declare
   filled by EXISTING vs NEW scenes           `new_spine: [...]` (proposals)
            |
            v
  [ Layer 2: segment ]                        brief.yaml + tree
   slot tree; slots are SCENE SPECS
            |
            v
  [ Layer 3': scene authoring ]  -- REPLACES dialogue layer --
   ambient slot -> scenes/a<NN>-<slug>.yaml
   spine slot   -> scenes/<NN>-<slug>.yaml  (beats, branches, default_next)
            |
            v
  [ Gate: load_pack() + validator ]  <-- new scenes must pass before landing
            |
            v
  campaigns/<pack>/scenes/*.yaml   <-- the pack GROWS. This is the deliverable.
            |
            v
  [ AIR TIME ]  renderer.py -> variant pools -> improviser.py -> TTS -> Twitch
   dialogue is generated HERE, fresh, every airing
```

The line that matters: **the generator's output is now an input to the pack,
not an input to playback.** Generator output stops bypassing `load_pack()` and
starts going through it.

---

## 3. Fate of each existing component

| Component | Fate |
|---|---|
| `ring.py` | **Unchanged.** Pure math, already campaign-agnostic. |
| `plan_arc.py` / `arc_schema.py` | **Extended.** Must allow proposing new spine scenes (§1.2 blocker). |
| `plan_segment.py` / `segment_schema.py` | **Mostly unchanged.** Slots gain the fields a scene needs. |
| `generate_segment_dialogue.py` | **Retired** from the pipeline. Becomes `author_scenes.py`. |
| `worklist.py`, `pool.py`, `concurrent_llm.py` | **Reused as-is.** Work-unit + worker-pool + breaker machinery is agnostic to what the unit produces. |
| `vocabulary.py` | **Extended.** Must distinguish "unknown scene" from "scene this run is proposing". |
| `app/campaign/renderer.py`, `improviser.py` | **Unchanged.** They were always right. |
| `campaigns/*/generated/` | **Frozen, not deleted.** See §6. |
| `.claude/prompts/build_generated_episode.py` | **Deprecated** once new scenes land; `build_campaign_episode.py` (authored path) becomes the only bridge. |

---

## 4. Implementation plan

Phases are ordered so each ends somewhere safe to stop. Phase 1 alone already
delivers value (ambient scene growth) without touching the arc validator.

### Phase 0 — Freeze and record (no behaviour change)

**0.1** Write `docs/generator_output_contract.md` stating the corrected
contract: the generator emits pack source, not takes. Link it from
`PLAN_v1.md` §Context and `docs/campaign_content_expansion.md`.

**0.2** Mark `campaigns/ashiorid_1/generated/` as legacy output in a
`README.md` inside that directory — what produced it, why it is not scene
source, and that it remains valid for already-built episodes only.

**0.3** No code. Commit: `docs: record corrected generator output contract`

---

### Phase 1 — Ambient scene emission (the cheap 80%)

Ambient slots already carry everything an ambient scene needs (§1.3). This
phase is a serializer plus a validation gate, and needs **no** arc changes.

**1.1** New module `utilities/3LayersWeeklyGeneration/src/scene_writer.py`.
Pure functions, no LLM, no network — testable without a model.

- `ambient_slot_to_scene(slot, *, scene_id) -> dict` — maps a Layer 2 ambient
  slot to ambient scene YAML: `id`, `ambient: true`, `prompt`, `lore`, plus
  `ring_tone` and `mood` carried from the slot's node.
- `slug_for(slot) -> str` and `next_scene_filename(scenes_dir, prefix) -> str`
  — filename allocation. Ambient files use the `a<NN>-` prefix; the loader
  globs `scenes/*.yaml` **non-recursively**, so they must sit flat alongside
  the spine (documented in `campaign_content_expansion.md`).
- `write_scene(scenes_dir, filename, scene_dict) -> pathlib.Path`.

Tests: `tests/test_scene_writer.py` — round-trip a slot to YAML, assert the
result parses and carries every required ambient key; assert filename
allocation skips existing numbers; assert a slot missing `prompt` raises.

**1.2** New module `src/author_scenes.py` — the Layer 3' entry point,
replacing `generate_segment_dialogue.py` in the pipeline.

Reuses `worklist` + `pool` unchanged. Per segment: load `brief.yaml`, take the
ambient slots, emit one scene per slot via `scene_writer`, into a **staging
directory** (`<out_root>/proposed_scenes/`), never straight into the pack.

**1.3** Validation gate — `src/pack_gate.py`.

- Copy the pack to a temp dir, overlay `proposed_scenes/`, run `load_pack()`.
- On `PackError`, reject with the offending scene id and the raw error.
- Only on a clean load are scenes eligible to be promoted into the real pack.

This is the structural fix for the "invented characters / bad lore stems"
class of defect: nothing reaches `scenes/` without passing the same loader the
hand-authored content passes.

**1.4** Promotion is an **explicit, separate step** — `--promote` — never
automatic. The generator proposes; a human (or an explicit flag) accepts.
Rationale: scene files are source material under git, and an unreviewed model
writing directly into tracked source is the thing we are correcting away from.

Tests: `tests/test_pack_gate.py` — a proposed scene with an unknown lore stem
is rejected; a clean one is accepted; promotion is a no-op without `--promote`.

**1.5** Verify end-to-end on `test_pack` first, then a scoped `ashiorid_1`
run. Read the emitted YAML by hand before believing any log line.

Commit: `feat(generator): emit ambient scenes instead of dialogue takes`

---

### Phase 2 — Spine scene authoring (the genuinely new capability)

**2.1** Unblock the arc validator (§1.2). In `arc_schema.py`, a segment gains
an optional `new_spine:` list of **proposed** scene ids. Validation becomes:
every id in `spine_scenes` must be either a known pack scene **or** declared
in this arc plan's `new_spine`. `vocabulary.py` gains
`known_or_proposed_scene_refs(ids, proposed)` so the closed-vocabulary
discipline survives — a proposed id is still a *declared* id, not a free
string. Unreferenced proposals are an error, not a warning.

**2.2** Extend the spine slot schema in `segment_schema.py`. `SPINE_REQUIRED`
becomes conditional:
- slot referencing an existing scene: `["slot_id", "kind", "scene_ref", "participants"]` (unchanged)
- slot authoring a new scene: `["slot_id", "kind", "new_scene_id", "participants", "summary", "beats_brief"]`

Exactly one of `scene_ref` / `new_scene_id` — both or neither is an error.

**2.3** Spine scene generation in `author_scenes.py`. A spine scene is
structurally richer than an ambient one and is the one place an LLM call is
still needed at this layer: it must produce `beats` (narration/dialogue/action
with `speaker` from `pack.cast`, `text` as a **variant pool of 2-3 phrasings**
per `campaign_content_expansion.md`), and `improv: true` on beats whose exact
wording is not load-bearing.

Hard constraints for the prompt, all enforced in validation, not just asked
for:
- every `speaker` must be a `pack.cast` key
- every `primitive` must be in `campaign.yaml`'s `primitives` list
- every `lore` stem must resolve to `lore/<stem>.md`
- narration must be written for the ear (short lines, read aloud by TTS)

**2.4** Spine chaining — `src/spine_chain.py`. New spine scenes must be linked
into the story graph: set the previous last scene's `default_next` to the
first new scene, chain the new ones, and leave the final one's `default_next`
open (or closing the loop, per the ring). Chaining **edits an existing tracked
scene file**, so it is gated behind `--promote` like everything else, and must
be a single well-formed diff a human can read.

Tests: `tests/test_spine_chain.py` — chain N new scenes onto a pack, assert
graph reachability from `start_scene`, assert no cycles except an intentional
loop closure, assert an ambient scene is never a `default_next` target (that
is already a pack validation error).

**2.5** Ring integration: `ring_tone` on each new scene comes from its
segment's ring role; `mirror_of` / `mirror_transform` become part of the
authoring prompt ("this scene mirrors <id>, transformed by <transform>").
`ring.py` unchanged.

**2.6** Continuity contract (§6C). Add `continuity_in` / `continuity_out` to
`Scene` in `app/campaign/pack.py` (optional, never spoken), emit them from
`author_scenes.py`, and enforce in `pack_gate.py` that an ambient scene never
carries a non-empty `continuity_out`. Backfill the 49 existing scenes (open
question 8) so they become valid bridge targets.

**2.7** Arc-level scene-count control (§6D). Add `spine_scene_count` and
`spine_density` to the arc segment schema; Layer 2 must produce exactly that
many spine slots or fail validation and retry. Add the reconciliation check
and the 20% ambient floor.

Commit: `feat(generator): author new spine scenes with ring-shaped roles`

---

### Phase 2.5 — Densification loop (the ongoing operating mode)

This is not a one-off phase; it is how the pack is grown from here on.

**2.5.1** Extend `spine_chain.py` with the insertion algorithm (§6C.4):
given `A -> B` and a new scene `N`, verify both contract directions, rewrite
`A.default_next` to `N`, set `N.default_next` to `B`, re-run `pack_gate.py`.

**2.5.2** `src/find_gaps.py` — report the longest ambient stretches in the
current pack, ranked by duration, as densification candidates. This is the
"where should the next bridge go" tool.

**2.5.3** Bridge authoring reuses `author_scenes.py` with a segment scoped to
one gap. Side quests from the vault (`Sarah's Simple Inn`, `The Mage Hole`,
`WerePomeranian`, `Dwarf Related Issues`) are natural first bridges — they are
self-contained and low-stakes (§6E.2).

Tests: `tests/test_insertion.py` — inserting `N` between `A` and `B` preserves
reachability; a contract violation in either direction is rejected; a rejected
insertion leaves the pack byte-identical.

Commit: `feat(generator): insert bridge scenes into ambient gaps`

---

### Phase 3 — Layer 0 ingest (new source material)

Currently source material reaches the pack only by a human reading an Obsidian
vault. Phase 3 makes "new source material" a real pipeline input.

**3.1** `src/ingest_source.py` — point at a directory of markdown, produce
(a) candidate `lore/<stem>.md` notes in pack lore style (2-4 short paragraphs,
no headers) and (b) a source digest fed to Layer 1 as arc context.

**3.2** Lore stems are closed vocabulary — new stems must be registered in
`vocabulary.py` as part of the same run, or scenes referencing them fail the
gate. Ingest and authoring must therefore share one vocabulary instance.

**3.3** Ingest output is proposed, gated, and promoted exactly like scenes. No
special case.

Commit: `feat(generator): ingest external source material into pack lore`

---

### Phase 4 — Retire the dialogue path

Only after Phases 1-2 have landed real scenes on stream.

**4.1** Delete `generate_segment_dialogue.py` and its `stage: dialogue` job
type, or keep it behind an explicit `--legacy-dialogue` flag if any built
episode still depends on regenerating takes.

**4.2** Deprecate `.claude/prompts/build_generated_episode.py`;
`build_campaign_episode.py` becomes the single bridge, because after this
change there is only one kind of content — authored pack scenes.

**4.3** Update `docs/campaign_content_expansion.md` module status, the
`3layer-generator-ops` skill (Rule 2's three-stage chain becomes
arc -> segment -> **author**), and `virtualtubers-campaign-content`.

Commit: `refactor(generator): retire offline dialogue generation`

---

### Phase 5 — Run modes: the pipeline as a repeatable product (§6F)

The phases above build a one-shot generator. This phase makes it a **process
you can re-run** as the source changes, and re-point at a different source.

**5.1** `src/source_adapter.py` — the adapter contract (§6F.5).
`load_source(path) -> list[SourceNote]` where `SourceNote` carries
`id, title, text, kind, rel_path, hash`. Ship `ObsidianAdapter` (honouring the
`*Agent_Ignore*` filename convention already used in the Ashiorid vault) and
`PlainMarkdownAdapter`.

Tests: `tests/test_source_adapter.py` — an `*Agent_Ignore*` file is excluded;
`kind` is derived from the folder; `hash` is stable across reads and changes
when the file changes.

**5.2** `src/provenance.py` — read/write the `source:` block (§6F.2) and
compute staleness. Pure functions over scene dicts and a note index; no LLM.

- `stale_scenes(pack, notes) -> list[(scene_id, reason)]`
- `is_protected(scene) -> bool` — True when `authored` is `human` or
  `human-edited`. **Protected scenes are never regenerated**, only reported.

Tests: `tests/test_provenance.py` — an unchanged note yields no stale scenes;
a changed note marks exactly its derived scenes; a protected scene is reported
but excluded from the regeneration set.

**5.3** Run modes in `main.py` (§6F.3): `--seed`, `--refresh`, `--densify`.
Each prints a plan and does nothing without `--promote`, matching Phase 1.4.

**5.4** The staleness report. `--refresh` without `--promote` must print what
would change *and what would be skipped and why* — a silent skip of a
protected scene is indistinguishable from a bug.

Commit: `feat(generator): source-parameterized run modes with provenance`

---

## 5. Files likely to change

**New**
- `utilities/3LayersWeeklyGeneration/src/scene_writer.py`
- `utilities/3LayersWeeklyGeneration/src/author_scenes.py`
- `utilities/3LayersWeeklyGeneration/src/pack_gate.py`
- `utilities/3LayersWeeklyGeneration/src/spine_chain.py`
- `utilities/3LayersWeeklyGeneration/src/ingest_source.py` (Phase 3)
- `docs/generator_output_contract.md`
- `tests/test_scene_writer.py`, `test_pack_gate.py`, `test_spine_chain.py`,
  `test_author_scenes.py`

**Modified**
- `utilities/3LayersWeeklyGeneration/src/arc_schema.py` (:282-286 blocker, `new_spine`)
- `utilities/3LayersWeeklyGeneration/src/segment_schema.py` (:338-340 slot schema)
- `utilities/3LayersWeeklyGeneration/src/vocabulary.py` (proposed-id vocabulary)
- `utilities/3LayersWeeklyGeneration/src/plan_arc.py` (pass proposals through)
- `utilities/3LayersWeeklyGeneration/main.py` (`--stage author`, `--promote`)
- `utilities/3LayersWeeklyGeneration/config/generation*.yaml` (`dialogue:` -> `author:`)
- `docs/campaign_content_expansion.md`, `PLAN_v1.md`

**Deleted (Phase 4)**
- `utilities/3LayersWeeklyGeneration/src/generate_segment_dialogue.py`

**Untouched (important)**
- `app/campaign/renderer.py`, `app/campaign/improviser.py`, `ring.py`

---

## 6. What happens to the 2,671 existing generated files

They are real generated words, but they are **not** scene source and never
will be — wrong shape, never passed `load_pack()`, known attribution defects.

Recommendation: **freeze, don't delete.** Keep `campaigns/ashiorid_1/generated/`
so any already-built episode stays reproducible, add the README from Phase 0.2,
and stop writing to it. Do not attempt to back-convert takes into scenes — the
takes are dialogue *instances*, and a scene is a dialogue *template*; going
backwards would bake one frozen phrasing in as canon, which is precisely the
mistake being corrected.

**Open question for you:** they are currently untracked (`??` in git status,
2,671 files). Commit them as a frozen artifact, or gitignore them?

---

## 6A. Pacing model — duration-driven, replacing the take-count budget

**Decided 2026-09-19.** This replaces `PLAN_v1.md` §Budget in full.

### 6A.1 The principle

Significant scenes run **as long as the story needs**. Ambient scenes are the
**elastic filler** that paces the gaps between them. Airtime is therefore a
*consequence* of the story, not an input constraint the story is squeezed into.

This inverts the old model, and inverts it correctly. `PLAN_v1.md` derived
everything from `words_per_take x takes_per_slot`, so the week's shape was
dictated by a measured LLM throughput number — a **generation-cost** metric
masquerading as an **editorial** one. Nothing in that chain asked how long a
scene should be; it asked how many words a model emits per call.

### 6A.2 The structural win: tempo becomes a dial, and the ring drives it

The real prize is that ambient elasticity makes **tempo controllable per ring
phase**. Spine density is no longer a global constant — it is a function of the
segment's ring role:

| Ring phase | Story function | Spine share | Feel |
|---|---|---|---|
| **descent** | world-building, slow burn, setup | **15%** | sparse, breathing, lots of campfire |
| **keystone** | the turn — maximum consequence | **45%** | dense, events stacked, little idle time |
| **ascent** | consequences, acceleration, release | **30%** | quickening, tightening |

This is what finally makes ring composition earn its place. Today the ring only
reorders *which existing scene airs when*. Under this model the ring sets the
**dramatic tempo of the week** — a viewer tuning in during descent gets a
different *rhythm* than one tuning in at the keystone, which is exactly what
ring composition is for. `ring.py` still needs no changes; the partition it
already computes becomes the input to the pacing table above.

### 6A.3 The arithmetic, on the spec's 16/4/8 week

Constants: 168 h = 10,080 min; spoken rate ~150 wpm; 28 segments x 6 h.
Partition per `ring_composition_spec.md` §4: descent 16, keystone 4, ascent 8.

| Phase | Segs | Hours | Spine % | Spine h | Ambient h |
|---|---|---|---|---|---|
| descent | 16 | 96 | 15% | 14.4 | 81.6 |
| keystone | 4 | 24 | 45% | 10.8 | 13.2 |
| ascent | 8 | 48 | 30% | 14.4 | 33.6 |
| **total** | **28** | **168** | **23.6%** | **39.6** | **128.4** |

Words: spine 39.6 h x 60 x 150 = **356,400**; ambient 128.4 h x 60 x 150 =
**1,155,600**; total **1,512,000** — which lands on the 1.5M target without
being reverse-engineered from it. The target is now an *output* of the pacing
model, and that is the point.

### 6A.4 Scene counts — the actual authoring deliverable

At a spine scene of ~12 min (1,800 spoken words) and an ambient scene of
~3 min (450 words):

| | Today | Target | Per segment |
|---|---|---|---|
| spine scenes | 15 | **~198** | ~7 |
| ambient scene **definitions** | 34 | **~250** | ~9 |
| **total scene files** | **49** | **~450** | ~16 |

**Ambient definitions are not airings.** 128.4 h of ambient at 3 min each is
~2,568 *airings*, drawn from ~250 *definitions* — roughly **10 airings per
definition per week**, each differently worded by variant pools + `improv:
true` at render time. That ratio is the health metric: 34 definitions would
mean 75 airings each (the same campfire premise 75 times, and no amount of
rephrasing hides that), while ~250 keeps any single premise to ~10 appearances.

This is the number that proves the correction was necessary. The old pipeline
produced 2,671 files and **0** new scenes. The target is ~450 scenes — 9x the
current pack — and only the retargeted generator can produce them.

### 6A.5 What this changes in code

1. **`segment_schema.py:66` `derive_target_slots()` is replaced.** It currently
   computes `round(target_words / (words_per_take * takes_per_slot))` — pure
   take-model arithmetic. It becomes duration-driven: a node's budget is
   **minutes of airtime**, split into spine scenes (sized by narrative need,
   within a floor/ceiling) and ambient scenes (sized to fill the remainder).
2. **`config/generation*.yaml` gains a `pacing:` block**, replacing
   `budget.measured_baseline`:

   ```yaml
   pacing:
     spoken_wpm: 150
     spine_scene_minutes:   {min: 6, target: 12, max: 25}
     ambient_scene_minutes: {min: 2, target: 3,  max: 6}
     spine_share_by_phase:  {descent: 0.15, keystone: 0.45, ascent: 0.30}
     ambient_airings_per_definition_target: 10   # health metric, §6A.4
   ```

3. **`budget.measured_baseline` is deleted, not repurposed.** `words_per_take`
   and `generation_words_per_min` are GPU throughput figures. They belong in
   capacity planning ("can we generate this in time"), never in editorial
   planning ("how long should this scene be"). Conflating the two is the
   original sin being corrected here; keeping the key around invites it back.
4. **Layer 1 gains a per-segment airtime budget** carrying `spine_minutes` and
   `ambient_minutes`, derived from its ring phase. Layer 2 spends that budget.
5. **A reconciliation invariant, mirroring the existing word-reconciliation
   rule** (`segment_schema.py` docstring, "WORDS RECONCILE EXACTLY"): a
   parent's **minutes** must equal the sum of its children's minutes. Same
   discipline, new unit. Drift of 3% per level loses a fifth of the week by
   depth four — the existing docstring already makes this argument for words,
   and it transfers unchanged.

### 6A.6 Pitfall this model introduces

Spine scenes are the tempo-critical element, and an LLM asked for "a scene"
reliably produces ~500 words regardless of instruction. A keystone segment
needing 45% spine density will silently degrade into descent-like pacing if
generated scenes all come out the same length.

**Mitigation:** `spine_scene_minutes` is validated per scene after generation
(word count / `spoken_wpm`), and a scene landing outside `{min, max}` is
rejected back to the planner rather than accepted and silently reconciled. Do
not "fix" an undersized scene by padding the ambient around it — that converts
a pacing failure into an invisible one, which is the exact failure class §3 of
the ops skill already warns about (`written: 0` reading identically to
"nothing needed generating").

---

## 6B. Campaign decisions (answered 2026-09-19)

| # | Question | Decision |
|---|---|---|
| 1 | Extend Ashiorid or new arc? | **Continue Ashiorid.** One arc spanning the full 168 h. |
| 2 | Retrofit ring roles onto existing spine? | **No — author new content.** Source: `/home/secus/codeProjects/ashioridCampaign`. |
| 3 | `generated/` — commit or ignore? | **Committed and pushed** as a frozen backup. Phase 0.2 README still applies. |
| 4 | Archive `ashiorid_v00`? | **Yes — archive both** existing packs, generate fresh. |

### 6B.1 Source material survey (verified, not assumed)

`/home/secus/codeProjects/ashioridCampaign/DnD Campaign/` — **~39,900 words**
across 180 markdown files:

| Dir | Files | Notable |
|---|---|---|
| `Plots/` | 12 | `Age of War`, `The Amulet of Wonder Quest`, `Campaign Opening - Letos Manor`, `The Party Attack`, `Portal Encounter`, `Shewolf of Idra` |
| `World/` | 7+ | `Campaign Timeline` (5 Ages, Year 310 present), `Energy and Moonwells`, `Bahadur Race`, `The Realms of Ashiorid` |
| `NPCs/` | 8 | Grovley, Azra, Ylva, Leena, Drokki RedGem Finnsson, The Rob Boss |
| `Characters/` | 5+ | Carl the Ranger, Buffalo, Helen (+ spell list), Chadwick |
| `Locations/` | 7 | Malmont, Losira, The Carnage Wall, Glaciers Rest, Henderson, Duke Leto's Manor |
| `Factions/` | 4 | Faction Statistics and Diplomacy, Bahadur Leadership |
| `Items/` | 4 | Blood-Iron Weapon (3,198 w), Cursed Ring of Cannibalism, Treant's Fall |

**~40k words of source must become ~810k words of scene content** (450 scenes
x ~1,800/450 avg). That is a **20x expansion**, which is precisely the job the
generator exists to do — and it is expansion from *real canon*, not invention.

### 6B.2 The structural gift in the source material

`The Amulet of Wonder Quest` is a **campaign-spanning quest with one dungeon
per region**, each gated behind a riddle in a different language (Abyssal,
Ignan, Terran, Aquan, Primordial, Draconic, Elvish, Giant, Sylvan, Celestial,
Gnoll, Common). Each dungeon yields a map fragment; the last reveals the
Amulet's burial site.

This maps onto the ring almost too neatly to ignore:
- **descent (16 segs)** — dungeons discovered one at a time, map filling in,
  riddles solved. Naturally episodic, naturally sparse, naturally 15% spine.
- **keystone (4 segs)** — the final fragment, the burial site, the Amulet
  claimed. Dense by construction.
- **ascent (8 segs)** — the Amulet's curse. Its six activation conditions
  (*take a life, fall to 0 HP, personal sacrifice, steal greatly, acquire
  great wealth, lose everything*) are **six ready-made ascent beats**, and its
  verse (*"I open but for tales complete / So make a worthy end"*) is a
  loop-closing line written before anyone thought about rings.

And the weekly time loop has canon justification already: `The Event` is an
unexplained catastrophe the entire calendar is measured from, and the Amulet
speaks of mists falling and wishes that "save the world." The reset is
narratable from existing lore rather than bolted on.

**Discrepancy to resolve before Phase 3:** the quest doc's prose says *nine*
dungeons but the body lists **twelve** regions. Pick a count during ingest —
12 aligns better with a 16-segment descent (12 dungeons + 4 setup segments).

### 6B.3 Archive and seed plan

```
campaigns/_archive/ashiorid_1/     <- moved, frozen, git-tracked backup exists
campaigns/_archive/ashiorid_v00/   <- moved, strict subset of the above
campaigns/ashiorid/                <- NEW pack, the 168h target
```

**Seed decision — carry the 49 authored scenes forward.** "Archive and
generate new" applies to the *pack*, not to hand-written prose. Those 49 scenes
are polished, TTS-shaped, human-authored canon, and re-deriving them from the
vault would be strictly worse. They become the **opening of the descent**; the
generator grows the pack from 49 to ~450 around them.

What is *not* carried forward: `generated/` takes (frozen in the archive) and
any assumption that the old arc plan is still valid.

**Open for your call:** carrying the 49 forward means the new pack is not a
clean-room regeneration. If you want the week to be fully ring-shaped from
segment 0 — including its opening — say so and the 15 existing spine scenes
get re-derived from the vault instead of seeded.

---

## 6C. Continuity contract — intro/outro summaries and progressive densification

**Decided 2026-09-19.** This is the mechanism that makes the whole plan
incremental rather than one enormous generation run.

### 6C.1 The idea

Every scene carries an **intro summary** (state the scene assumes on entry) and
an **outro summary** (state it guarantees on exit). Two scenes chain when the
first's outro satisfies the second's intro. Because chaining is a *contract*
between neighbours rather than a fixed edge, **new content can be inserted
between any two scenes later** — the inserted scene simply has to accept the
predecessor's outro and re-establish the successor's intro.

That turns the 168 h week into something we grow instead of something we must
finish:

- **Now:** generate ~40 h of spine + ~130 h of ambient. The week is complete
  and airable, just ambient-heavy.
- **Later:** repeatedly pick an ambient gap and bridge it with new spine
  content. Each bridge converts filler into plot **without regenerating
  anything around it**, because the neighbours' contracts are unchanged.

Density rises over time; the week never has to be torn down and rebuilt.

### 6C.2 The seam already exists — on one side only

The generator **already speaks this vocabulary**:

- `arc_schema.py:226` — every arc segment requires `continuity_in` and
  `continuity_out`.
- `segment_schema.py:253` — every brief tree node requires `summary`,
  `continuity_in`, `continuity_out`.

The **pack format does not**. `app/campaign/pack.py:317` gives `Scene` an
`enter_narration` (an intro, and a *spoken* one at that) and a `default_next`
edge — but **no outro, and no machine-readable intro state**. So the planner
reasons in continuity, then throws that reasoning away when it writes a scene.

That asymmetry is exactly why insertion is currently impossible: `default_next`
is a hard edge with no contract attached, so nothing can verify that an
inserted scene fits, and nothing records what the seam actually required.

**This is a small, high-leverage addition** — the concept is already designed
and validated upstream; it just needs to survive into the scene file.

### 6C.3 Schema addition

`Scene` gains two optional fields (additive — packs without them behave
exactly as today):

```yaml
id: grovley-revelation
title: Grovley
enter_narration: >-          # UNCHANGED — spoken aloud on entry
  In the subbasement, the butler tells the four of you what you are.

continuity_in: >-            # NEW — machine-readable, never spoken
  The party has found the Duke's body. They do not yet know what they are.
continuity_out: >-           # NEW — what this scene guarantees on exit
  The party knows they are products of the Begene program. Grovley is trusted.

default_next: the-vault
```

Rules:
- **Never spoken.** `continuity_*` are planning metadata, not TTS text. The
  renderer must ignore them; only `enter_narration` and beats reach the voice.
- **Optional.** Absent means "imposes no constraint" — today's behaviour.
- **Ambient scenes carry `continuity_in` only, and it must be satisfiable by
  any point in the arc.** Ambient decides nothing (per
  `docs/campaign_content_expansion.md`), so it must never emit a
  `continuity_out` that a later scene could depend on. An ambient scene with a
  non-empty `continuity_out` is a **validation error** — that is the rule that
  keeps ambient injectable anywhere, and it is the one most likely to be
  violated by a generated scene.

### 6C.4 Insertion algorithm (`spine_chain.py`, extended)

To insert new scene `N` between existing `A -> B`:

1. Check `A.continuity_out` satisfies `N.continuity_in`.
2. Check `N.continuity_out` satisfies `B.continuity_in`.
3. Rewrite `A.default_next = N.id`; set `N.default_next = B.id`.
4. Re-run `pack_gate.py`; reachability from `start_scene` must be unbroken.

Step 2 is the load-bearing one: **an inserted scene must not invalidate its
successor's assumptions.** A bridge that reveals a secret `B` assumes is still
hidden corrupts the story silently, and nothing downstream would catch it.

"Satisfies" is an LLM judgement, not string equality — but it is a *cheap,
bounded* judgement over two short summaries, and it is verifiable by a human
reading a one-line diff.

### 6C.5 Why this ordering is the right build order

Generating ambient first and densifying later is strictly safer than the
reverse:

- Ambient is **low-stakes** — it decides nothing, so a weak ambient scene
  costs flavour, never coherence.
- Spine is **high-stakes** — it is plot, and the thing most likely to need
  human review. Deferring it means the review burden is spread over weeks
  instead of landing all at once.
- A 130 h-ambient week is **airable immediately**. We get a running stream
  early and improve it continuously, rather than waiting for a complete week
  before anything can go live.

**Consequence worth stating plainly:** the first airable week will feel
*slow* — heavy on campfire talk, light on plot. That is expected and
correct, not a failure of the generator. Densification is the plan, not a
patch for a bad first result.

---

## 6D. Arc-level scene-count control (answers question 3)

**Yes — and it should be an explicit arc-plan field, not just prompt wording.**

### 6D.1 Why prompt-only instruction is not enough

Asking the planner in prose for "more scenes at the keystone" fails the same
way §6A.6 describes: LLMs regress to a uniform output size regardless of
instruction. Worse, a prompt-only knob is **unverifiable** — there is no field
to check afterwards, so a keystone that quietly generated 3 scenes instead of
12 looks identical to one that worked. That is precisely the silent-failure
class the ops skill already warns about (`written: 0` reading the same as
"nothing needed generating").

### 6D.2 The design: Layer 1 declares, Layer 2 must comply

Arc segments already carry `hours` (`arc_schema.py:226`). Add two siblings:

```yaml
- id: the-final-battle
  order: 18
  hours: 6
  spine_scene_count: 12      # NEW — how many spine scenes this segment gets
  spine_density: high        # NEW — sparse | normal | high | maximum
  plot_path: [{role: keystone}]
  continuity_in:  "..."
  continuity_out: "..."
```

- `spine_density` seeds a **default** from the §6A phase table
  (descent 15% / keystone 45% / ascent 30%).
- `spine_scene_count` is an **explicit override** the planner may set when a
  beat deserves more room than its phase default allows — the "adjust it from
  the arc" control you asked for.
- Layer 2 **must** produce that many spine slots. A mismatch is a validation
  failure that retries, not a warning that is logged and ignored.

### 6D.3 Two guards this needs

1. **Reconciliation.** `sum(spine_scene_count x avg_scene_minutes)` for a
   segment must fit inside its `hours` budget, with ambient absorbing the
   remainder. If a segment's spine overflows its hours, the arc plan is
   rejected — otherwise §6A's minute reconciliation silently breaks a level
   down, which is the failure mode the existing "WORDS RECONCILE EXACTLY"
   docstring was written to prevent.
2. **A floor on ambient.** A `maximum`-density segment must still leave enough
   ambient to absorb pacing drift. Suggest a hard floor of **20% ambient** per
   segment: with zero ambient there is no elasticity left, and any spine scene
   running short becomes dead air with nothing to fill it.

### 6D.4 What this unlocks

Combined with §6C, the arc becomes a **directable** instrument rather than a
uniform grid:

- Give the keystone 12 scenes and the quiet descent segments 3 each.
- Later, raise a descent segment's `spine_scene_count` from 3 to 6 and
  regenerate **only that segment** — §6C's contracts mean neighbours are
  untouched.
- Tempo is tunable per segment without rebuilding the week.

---

## 6E. Keystone material after the Amulet removal

`The Amulet of Wonder Quest*Agent_Ignore*.md` is excluded (confirmed on disk),
which removes the structure §6B.2 proposed. **`Plots/Age of War.md` replaces it
as the keystone, and is a better fit.**

### 6E.1 Why it is stronger than the Amulet was

`Age of War` is **already canonically cyclical**, which is the hardest thing to
retrofit and the one thing the weekly loop actually needs:

> **If the players succeed** — the world's energy is saved, magic is retained;
> *in the far future, new players will once again have to stop an invading army
> from harvesting the world's power.*
>
> **If the players fail** — the world is stripped of its energy, magic is lost;
> *in the far future, the new players will be the counter-invading army,
> attempting to steal back the world's energy.*

The loop is **in the source text**. Both outcomes regenerate the same conflict
in the far future, which means the weekly reset needs no invented justification
at all — and the two outcomes are a natural **mirror pair** in ring terms
(`role_reversed`: defenders become invaders).

It also supplies the ring's structural furniture directly:
- **keystone** — the final battle at the northern pole, two armies, the world's
  energy being harvested. A single, maximum-consequence event.
- **branch** — succeed/fail is a genuine fork with divergent downstream lore
  (`magic retained` vs `magic lost`), and the pack **already has both scenes**:
  `03-magic-retained.yaml` and `04-magic-lost.yaml`.
- **descent** — the Begene breeding program, players separated at birth, the
  grand-ball letter reuniting them. That is already scenes 01-08.
- **ascent** — the magic worm in the vault under the mansion, power awakening,
  leeching magic from defeated enemies. Already scenes 09-10.

### 6E.2 What this means for the build

The existing 49 scenes are **not a seed we tolerate — they are the spine of the
Age of War arc already written**. Carrying them forward (your decision on Q1)
is now clearly right: they cover descent and the fork, in polished prose. The
generator's job is to **expand around a keystone that already exists in canon**,
not to invent one.

Remaining source for expansion, Amulet excluded: **~6,400 words of plot notes**
(`Campaign Opening - Letos Manor` 937 w, `Age of War` 704 w, `The Party Attack -
Full Scene` 683 w, `Shewolf of Idra` 612 w, plus 8 side quests) over **~39,900
words** of total vault material. Side quests (`Sarah's Simple Inn`, `The Mage
Hole`, `WerePomeranian`, `Dwarf Related Issues`...) are ideal **descent bridge
material** for §6C densification — self-contained, low-stakes, insertable
between existing scenes without disturbing their contracts.

---

## 6F. The pipeline as a repeatable product (source -> week)

**This is the real deliverable.** Everything above describes retargeting the
generator; this section states the goal the retargeting serves: **a repeatable
process that takes a source corpus as input and produces a week of content as
output**, runnable again whenever the source changes or a different source is
swapped in.

Sections 1-6E describe *one run* of that process against the Ashiorid vault.
The requirement is broader: **the source must be a parameter, not a constant.**

### 6F.1 What "source as a parameter" demands

Three capabilities, none of which the current design delivers outright:

| # | Capability | Why | Status in plan §1-6E |
|---|---|---|---|
| A | **Point the pipeline at any source directory** | Swap vaults for a different week | Partially — Layer 0 (§Phase 3) reads a directory, but the pack identity is hardcoded |
| B | **Re-run against a CHANGED source and update, not duplicate** | The Ashiorid vault will keep being edited | **Missing.** Nothing tracks which scene came from which source note. |
| C | **Run multiple sources side by side** | Different weeks from different corpora | Partially — packs are already separate dirs, but nothing manages them as a set |

(B) is the gap that matters most and is the easiest to get wrong. Without
provenance, a re-run after editing the vault either duplicates everything or
silently overwrites human edits. Both are unacceptable.

### 6F.2 Provenance — the missing field

Every generated scene records where it came from:

```yaml
id: sarahs-inn
# ... scene content ...
source:
  pack_run: ashiorid_2026w38          # which generation run produced it
  notes:                              # which source files fed it
    - "Plots/Side Quests/Sarah's Simple Inn.md"
  source_hash: 8f2a1c...              # hash of those notes at generation time
  authored: generated                 # generated | human | human-edited
```

This single block unlocks all three capabilities:

- **Re-run diffing.** On a re-run, compare each note's current hash against
  `source_hash`. Unchanged note -> skip its scenes entirely. Changed note ->
  the scenes derived from it are *stale* and become regeneration candidates.
  Nothing else is touched.
- **Human edits are protected.** A scene whose `authored` is `human` or
  `human-edited` is **never** regenerated automatically, even if its source
  changed. It is reported as "source changed, review by hand" and left alone.
  This is what makes the 49 authored scenes safe to keep forever.
- **New notes are additive.** A source note with no matching `source_hash`
  anywhere is simply new material — it produces new scenes, inserted via the
  §6C contract mechanism, with nothing regenerated around it.

### 6F.3 The three run modes

One command, three behaviours, selected by what already exists:

```bash
# 1. SEED — new pack from a source corpus
python main.py --source ~/codeProjects/ashioridCampaign --pack campaigns/ashiorid --seed

# 2. REFRESH — source changed; regenerate only what went stale
python main.py --source ~/codeProjects/ashioridCampaign --pack campaigns/ashiorid --refresh

# 3. DENSIFY — add content without a source change (§6C bridges)
python main.py --pack campaigns/ashiorid --densify --gaps 5
```

`--refresh` is the mode that answers "I will keep modifying the source." It
reports before acting:

```
3 notes changed since last run:
  Plots/Age of War.md            -> 4 scenes stale
  NPCs/Grovley.md                -> 2 scenes stale (1 human-edited, SKIPPED)
  World/Campaign Timeline.md     -> lore note only
2 notes are new:
  Plots/Side Quests/The Mage Hole.md
Regenerate 5 scenes, add ~3, skip 1 human-edited? [--promote to apply]
```

### 6F.4 Swapping sources entirely

A different corpus is just a different `--source` plus a different `--pack`.
What must NOT be shared between them:

- **Cast.** A new source implies new characters; `cast/` is per-pack.
- **Lore vocabulary.** `vocabulary.py` builds its closed sets per-pack.
- **Primitives.** `campaign.yaml` already scopes these per-pack (the Ashiorid
  pack deliberately excludes the cyberpunk primitive set — that comment is
  already in its `campaign.yaml`).

What CAN be shared: the generator, the ring math, the pacing config, the
scene-writer, the gate. **That separation is the actual product** — the
pipeline is campaign-agnostic, exactly as `ring_composition_spec.md` is
deliberately campaign-agnostic about shape.

### 6F.5 Source adapters — not every corpus is an Obsidian vault

Layer 0 should not assume Obsidian. Define a minimal adapter contract so a new
source type is a small module, not a pipeline rewrite:

```python
def load_source(path) -> list[SourceNote]:
    """SourceNote: id, title, text, kind, rel_path, hash"""
```

- `ObsidianAdapter` — markdown tree, `[[wikilinks]]`, folder-as-category
  (the Ashiorid case; note it must honour the `*Agent_Ignore*` filename
  convention already in use).
- `PlainMarkdownAdapter` — a flat directory of `.md`.
- Future: a single long document, a wiki export, a transcript set.

`kind` (plot / npc / location / lore / item) drives how a note is used: plot
notes seed spine scenes, npc/location notes seed ambient and lore, and lore
notes become `lore/*.md` directly.

### 6F.6 Where this lands in the phase plan

This does not add a new phase so much as constrain the existing ones:

- **Phase 1** — `author_scenes.py` must write the `source:` block from the
  outset. Retrofitting provenance later means re-deriving it for every scene
  already generated, which is strictly worse.
- **Phase 3** (Layer 0 ingest) — becomes the adapter layer (§6F.5) plus the
  hash index, not just a one-shot digest.
- **New Phase 5** — run modes (§6F.3): `--seed` / `--refresh` / `--densify`,
  the staleness report, and the human-edit protection rule.

### 6F.7 The honest limitation

Re-running a changed source produces **different prose**, not a diff of the old
prose. An LLM asked to regenerate a scene from an edited note writes a new
scene; it does not surgically edit the old one. So `--refresh` replaces whole
scenes, and any hand-polish applied to a regenerated scene is lost — which is
exactly why `authored: human-edited` exists and is never auto-regenerated.

Stated plainly so it is not discovered later: **the pipeline is reproducible in
structure, not in wording.** The same source produces the same *shape* of week
every time, but not the same sentences. For a show whose premise is that
wording varies on every airing, that is acceptable — but it means the source
corpus, not the generated pack, is the thing of record.

---

## 7. Risks and tradeoffs

| Risk | Mitigation |
|---|---|
| A model writing tracked source files | Staging dir + `load_pack()` gate + explicit `--promote`. Never auto-write to `scenes/`. |
| Generated spine scenes are lower quality than hand-authored | Spine is *plot* — it is the highest-stakes output. Start with ambient (Phase 1), prove spine on `test_pack`, review every spine scene by hand before promoting. |
| Story-graph corruption from bad chaining | `test_spine_chain.py` reachability/cycle assertions; chaining edits are single reviewable diffs. |
| Losing the 168h airtime target | **Resolved — see §6A.** Hours are now duration-driven: significant scenes run as long as needed, ambient fills the gaps. The 1.5M-word figure is an *output* of the pacing model, not an input. `PLAN_v1.md` §Budget is superseded. |
| Uniform scene length flattens ring tempo | Per-scene duration validated against `spine_scene_minutes {min,max}`; out-of-range scenes rejected to the planner, never silently absorbed by ambient padding (§6A.6). |
| 20x expansion from 40k words of source invents non-canon | Every scene cites its source note; `pack_gate.py` enforces closed lore/cast/primitive vocabulary. Ambient scenes decide nothing, so invention there is bounded by design. |
| Two `arc.batch_size` / `previous_continuity` bugs in `plan_arc.py` | Pre-existing, documented in `ring_composition_spec.md` §0.1, still gate ring work. Fix before Phase 2. |
| Inserted bridge scene invalidates its successor's assumptions | §6C.4 step 2 checks `N.continuity_out` against `B.continuity_in` before rewriting any edge. This is the highest-risk operation in the whole densification loop — a bridge that reveals a secret `B` still assumes hidden corrupts the story silently. |
| Ambient scene emits a `continuity_out` and becomes non-injectable | Hard validation error in `pack_gate.py` (§6C.3). Most likely defect in generated ambient content. |
| First airable week feels slow (130h ambient / 40h spine) | Expected and correct (§6C.5), not a generator failure. Densification is the plan. Set expectations before the first airing. |

**Open questions**
1. ~~Extend the story or open a new arc?~~ **Answered — continue Ashiorid, one 168h arc (§6B).**
2. ~~Retrofit ring roles, or new content only?~~ **Answered — new content from the vault (§6B).**
3. ~~Commit or gitignore `generated/`?~~ **Answered — committed and pushed as a frozen backup.**
4. ~~Archive `ashiorid_v00`?~~ **Answered — archive both packs, seed a fresh one (§6B.3).**
5. ~~Carry the 49 authored scenes forward?~~ **Answered — yes, hold on to them (§6E.2). They are the Age of War spine already written.**
6. ~~Amulet dungeon count?~~ **Moot — `The Amulet of Wonder Quest*Agent_Ignore*.md` excluded. `Age of War` is the keystone (§6E).**
7. ~~Can scene counts be directed from the arc?~~ **Answered — yes, via `spine_scene_count` + `spine_density` arc fields, not prompt wording alone (§6D).**
8. **Resolved — backfill DONE.** `continuity_in` / `continuity_out` for all
   15 spine scenes are staged in `.claude/prompts/continuity_backfill_v2.yaml`
   (model `hermes3:70b`, pass 2, DAG-ordered with convergence handling). Not
   yet promoted into `campaigns/ashiorid_1/scenes/` — that is an explicit
   `--promote` (§1.4), owned by the human / the run. grovley-revelation (the
   convergence point that initially failed to parse) is now included: the
   parser in `backfill_continuity_chain.py` was fixed to tolerate the quoted
   YAML scalar the model emits for it. Promote only after the Phase 1-2 gate
   is green, so the new field lands alongside the `load_pack` extension that
   reads it, not ahead of it.

---

## 9. Execution run sheet — for the overnight local-model build

**This section IS the build order.** The design in §1-8 is what we are
building; this is the order to build it in, the hard limits, and the gates
that decide when each step is *done* rather than *started*. A local model
running unattended must be able to act on this section alone: it states the
constraints, the acceptance bar, and where to stop if something fails.

### 9.1 Hard constraints (non-negotiable)

1. **Concurrency — the host crashed on 2026-09-19 for exceeding this.**
   At most **ONE 70b-class local model in flight, or at most TWO small
   models concurrently.** Never fan out parallel Ollama calls, and never run
   more than this many model-backed subagents at once. Every LLM phase below
   runs **sequentially, then the next.** If a step is slow, wait; do not add
   parallelism.
2. **Version every generated artifact.** Every file this build writes (a
   scene YAML, a brief, a tree, a promotion diff) carries a provenance
   block identifying *which run produced it* so we can trace, diff, and
   roll back by version:
   ```yaml
   source:
     run_id:            ashiorid_build_YYYYMMDD_HHMMSS
     batch:             <phase>.<step>       # e.g. 1.3, 2.3
     model:             <model + size>
     base_hash:         <sha256 of the input(s) this scene was derived from>
     version:           <scene id>@<N>       # N bumps on every regenerate
     authored:          generated          # generated | human | human-edited
   ```
   `run_id` is generated once per build and shared by every artifact in the
   build; `base_hash` is the content hash of the source note(s) fed in, so a
   later `--refresh` can detect drift (§6F.2). `version` makes `A@3 -> A@4`
   a legible diff instead of "the file changed."
3. **Never auto-write into tracked source.** Propose to
   `proposed_scenes/`, gate through `load_pack()` / the validator, then
   `--promote` explicitly (§1.4). This is the load-bearing correction of the
   whole plan — do not take a shortcut that skips the gate to save time.
4. **Read the output before believing a log line.** After each generation
   step, open the actual YAML and confirm the content is sensible and the
   provenance block is present. A `written: N` with N>0 and empty scenes is
   the failure class the ops skill already warns about.

### 9.2 Acceptance bar (per phase — this is "done", not "started")

- **Baseline the agent must not regress.** The suite is currently
  **1344 passed, 16 failed** (run with `--ignore=tests/test_episode_validator_show.py`,
  which crashes pytest's own reporter — see 9.3). The 16 = **13 in
  `test_campaign_validator.py`** (pack-validation rules the pack format does
  not implement yet — *these are exactly the `pack_gate` rules in Phase 1.3;
  turning them green is the acceptance for that phase*) **and 3 in
  `test_tile_pane.py`** (fade-timing, unrelated — do not touch, must not
  regress). Green goal: **1357/0 with the 13 validator tests passing** and
  the 3 tile_pane unchanged.
- **Unit tests exist for every new pure module** (`scene_writer`,
  `pack_gate`, `spine_chain`) and pass before any LLM-dependent step is run.
- **The pack loads.** `load_pack()` on the target pack after each promotion
  with zero errors.
- **Provenance present.** Spot-check 3+ generated scenes for the `source:`
  block above.

### 9.3 Known-broken test to avoid (already handled — do not re-diagnose)

`app/campaign/ambient.py` **was missing from the repo**; `tests/test_campaign_ambient.py`
imports it, so `pytest tests/` used to **die at collection** (0 tests ran).
It is now created at **`app/campaign/ambient.py`** to its test spec (33/33 pass). `test_episode_validator_show.py::test_show_check_never_touches_the_filesystem`
patches `Path.exists` globally and trips over `voice_registry.default_path()`
inside pytest's own failure-reporter — it produces an `INTERNALERROR`, not a
clean `FAILED`. **Always run the suite with
`--ignore=tests/test_episode_validator_show.py`** for these reasons. Do not
attempt to "fix" that test as part of this build; it is a roundtable code
issue orthogonal to the generator retargeting, and fixing it is out of scope
here. (See open-items list below if you want to track it.)

### 9.4 Build order (stop where a gate fails)

Run in this exact order. Each block ends with a **gate** — if the gate does
not pass, stop, leave a note in the build log naming the gate and the
failing evidence, and do not proceed.

**Pre-flight (no LLM).**
- Confirm the baseline in 9.2 by running the suite as specified. Record the
  exact pass/fail counts in the build log. This is the comparison baseline.
- Confirm `continuity_backfill_v2.yaml` has all 15 spine scenes (it does —
  do not re-run the backfill; it is already complete and staged).
- Decide target pack. §6B.3 carries the 49 authored scenes into a new
  `campaigns/ashiorid/` pack; archive `ashiorid_1` and `ashiorid_v00` into
  `campaigns/_archive/`. Create the new pack dir by copying
  `campaigns/ashiorid_1/{campaign.yaml,cast,scenes,lore}` (the 49 + 35
  scenes, cast, lore) and dropping `generated/` (it stays frozen in the
  archive). Gate: `load_pack("campaigns/ashiorid")` succeeds and reports the
  expected scene count.

**Phase 0 — Freeze and record (no LLM, no code).** Docs only. Gate: the
`generated/` README and the corrected output-contract doc exist; no test
count changes.

**Phase 1 — Ambient scene emission (the cheap 80%, mostly no LLM).**
- `scene_writer.py` (pure) + its tests. Gate: tests green, no LLM calls.
- `pack_gate.py` (pure) + its tests, **implementing the 13 validator rules
  that are currently red.** Gate: those 13 tests pass; suite at baseline or
  above.
- `author_scenes.py` ambient path (LLM for briefs only, sequential, within
  9.1). Emit to `proposed_scenes/` on `test_pack` first. Gate: emit at least
  one ambient scene that `pack_gate` accepts AND that carries the `source:`
  block; hand-read it.
- On `test_pack`, `--promote` one scene end-to-end. Gate: the promoted
  scene is present in `scenes/`, `load_pack` clean, and the file carries the
  provenance block.

**Phase 1.5 — Promote the continuity contract (LLM: 15 already-done scenes).**
- Extend `pack.py` `Scene` with optional `continuity_in` / `continuity_out`
  (additive; absent = no constraint).
- Promote the 15 staged scenes from `continuity_backfill_v2.yaml` into the
  target pack. Gate: the 15 spine scenes carry the fields; `load_pack` clean;
  an ambient scene is *not* forced to carry them.

**Phase 2 — Spine scene authoring (the new capability).**
- `arc_schema.py` `new_spine` proposal + `vocabulary.py` `known_or_proposed`.
  Gate: a segment may declare a not-yet-authored spine id and validation
  accepts it once it is in the arc's own `new_spine`.
- `segment_schema.py` conditional spine slot. Gate: slot-with-new-scene and
  slot-with-existing-scene both validate; both-or-neither rejected.
- `spine_chain.py` (pure) + tests. Gate: chain N new scenes onto a pack,
  reachability-from-start holds, no accidental cycle, an ambient scene is
  never a `default_next` target.
- `author_scenes.py` spine path (LLM, sequential, within 9.1). Author **2-3
  new spine scenes** on `test_pack`. Gate: each is 12±minutes at 150 wpm
  (§6A.6), has 2-3 variant phrasings per narration beat, every speaker is in
  `pack.cast`, every primitive is in `campaign.yaml`, and `pack_gate`
  accepts it. Hand-read before believing.
- Ring integration: set `ring_tone` from the segment's ring role; include
  `mirror_of` / `mirror_transform` in the authoring prompt. Gate: one
  keystone scene and one mirror pair exist with the expected `ring_tone`.

**Stop condition for the overnight build.** A clean, airable result for the
first week is achieved once Phase 2 has at least **198 spine scene files and
~250 ambient scene files** in the target pack, all passing `load_pack()` and
carrying the provenance block, with the test suite at or above the 9.2
baseline. Phases 2.5 (densification), 3 (ingest), 4 (retire dialogue), 5
(run modes) follow in a later build — they are the *operating mode*, not
part of the first airable week.

### 9.5 What NOT to do on this run

- Do not write directly into `campaigns/<pack>/scenes/` without the gate.
- Do not run more than the 9.1 concurrency limit, even to save time.
- Do not attempt to back-convert the 2,671 existing `generated/` takes into
  scenes (§6 — they are dialogue instances, not templates).
- Do not delete `generate_segment_dialogue.py` or its `stage: dialogue` job
  type yet (Phase 4 only, after landings are on stream).
- Do not "fix" an undersized scene by padding the ambient around it (§6A.6 —
  that converts a visible pacing failure into an invisible one).
- Do not re-run the continuity backfill (done, 15/15, staged).
- Do not modify `app/campaign/renderer.py`, `improviser.py`, `ring.py` —
  they were always right, and touching them is how a clean retarget turns
  into a broken stream.

### 9.6 Build log contract

After every step (pass or fail) the agent appends one line to
`.claude/prompts/overnight_build_log.md` (create it):

```
HH:MMSS | <phase.step> | PASS|FAIL | <gate> | <evidence: file/scene/test count>
```

At the end of the build, the last line should be readable as
"the first airable week is built" or "stopped at <gate> for
<reason>." This is the artifact the human reads in the morning.

---

## 8. Validation

```bash
source .venv/bin/activate

# Unit tests for the new pure modules
pytest tests/test_scene_writer.py tests/test_pack_gate.py tests/test_spine_chain.py -v

# Full suite must stay green
pytest -q

# The pack must still load after every promotion
PYTHONPATH=app python app/campaign/cli.py --pack campaigns/ashiorid_1 --validate

# Canon regression — authored output must be byte-identical with features off
PYTHONPATH=app python app/campaign/cli.py --pack campaigns/ashiorid_1 \
    --dry-run --no-pace --no-color | diff - /tmp/canon-baseline.txt

# Read the actual generated YAML. Never report success from logs alone.
```
