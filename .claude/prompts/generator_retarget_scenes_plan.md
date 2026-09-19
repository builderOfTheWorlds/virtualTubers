# Retargeting the 3-Layer Generator: Scenes, Not Dialogue

**Status:** design plan, pending review. No code changed yet.
**Date:** 2026-09-19
**Supersedes the output contract in** `utilities/3LayersWeeklyGeneration/PLAN_v1.md` §Context (Layer 3).
**Companion specs:** `ring_composition_spec.md` (v3.3), `docs/campaign_content_expansion.md`.

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

Commit: `feat(generator): author new spine scenes with ring-shaped roles`

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

## 7. Risks and tradeoffs

| Risk | Mitigation |
|---|---|
| A model writing tracked source files | Staging dir + `load_pack()` gate + explicit `--promote`. Never auto-write to `scenes/`. |
| Generated spine scenes are lower quality than hand-authored | Spine is *plot* — it is the highest-stakes output. Start with ambient (Phase 1), prove spine on `test_pack`, review every spine scene by hand before promoting. |
| Story-graph corruption from bad chaining | `test_spine_chain.py` reachability/cycle assertions; chaining edits are single reviewable diffs. |
| Losing the 168h airtime target | Hours now come from ambient *scenes* multiplied by render-time variation, not frozen takes. Re-derive the budget — the arithmetic in `PLAN_v1.md` §Budget assumes takes and is now wrong. |
| Two `arc.batch_size` / `previous_continuity` bugs in `plan_arc.py` | Pre-existing, documented in `ring_composition_spec.md` §0.1, still gate ring work. Fix before Phase 2. |

**Open questions**
1. Do new spine scenes extend the existing Ashiorid story, or open a new arc
   after the current `default_next` chain terminates?
2. Should ring composition shape only new content, or be retrofitted as roles
   onto the 15 existing spine scenes so the whole loop is one ring?
3. Commit or gitignore `generated/`? (§6)
4. Is `ashiorid_v00` still needed, or can it be archived? It is a strict subset
   of `ashiorid_1`.

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
