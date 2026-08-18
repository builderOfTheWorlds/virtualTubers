# Hierarchical 3-layer offline content generator — utilities/3LayersWeeklyGeneration

## Context

The previous pass (complete, merged into this working tree) proved the base
primitive: `app/campaign/batch_generate.py` calls
`LLMImproviser.generate_scene()`
([app/campaign/improviser.py](../../../codeProjects/virtualTubers/app/campaign/improviser.py))
against `hermes3:70b` and produces real, in-voice dialogue for the pack's 8
static ambient scenes. A sanity run generated 24 takes / 2,524 words in ~26
minutes — the model, config, and write path all work, and the live
`--dry-run` canon path is untouched.

The gap that run exposed: hitting the ~1.5M-word / 168-hour target by
grinding independent takes of 8 static, hand-written prompts has no
throughline — every take is a standalone airing with no awareness of where
the 168-hour arc actually is. The user's fix is a 3-layer pipeline that
plans top-down before generating dialogue, and wants it built as its own
self-contained utility rather than folded into `app/campaign/`:

1. **Arc layer** — hermes3:70b maps the full 168 hours into ~6-hour
   segments: what's happening, which characters, where the spine sits, how
   each segment connects to the one before and after.
2. **Segment layer** — hermes3:70b expands one segment's brief into an
   ordered sequence of concrete scene-level beats: what happens, who's on
   screen, in what order, bridging in from the previous segment and out to
   the next.
3. **Dialogue layer** — already built and unchanged.
   `LLMImproviser.generate_scene()` turns a scene prompt into actual GM
   narration + player dialogue; this layer just gets fed generated,
   plot-aware prompts instead of only the 8 static ones.

Confirmed with the user:
- hermes3:70b plays all three roles (no larger local model available).
- A segment is a new layer *above* scenes — groups existing spine + ambient
  scenes, doesn't replace them.
- Lives in its own directory, **`utilities/3LayersWeeklyGeneration/`**
  (already created, empty), not under `app/campaign/`.
- **One config file** for the whole pipeline, with the model (and
  temperature/tokens) independently settable per layer.
- **Output directory also set in that config file**, defaulting to inside
  the utility's own tree — fully self-contained, not written back into
  `campaigns/ashiorid/`.
- **Every layer's model is a set of named, swappable profiles** (not one
  fixed model per layer) — e.g. a `light` and `heavy` entry per layer — plus
  a **`--test-mode`** that runs one segment/slot through every configured
  profile for a stage side by side, so lighter vs. heavier models can be
  compared cheaply before committing to a full run. This directly hedges
  the "will this work" risk above: instead of discovering after a long run
  that hermes3:70b was overkill (or too slow) for a given layer, a test-mode
  pass on one segment answers that in minutes.

**Honest read on success odds**, given to the user before this plan: the
layering buys real coherence that flat independent takes can't provide, but
(a) getting hermes3:70b to reliably emit valid structured YAML across ~28
arc segments and dozens of scene-slots per segment will need retry/tuning,
not a clean first run, and (b) this buys quality-per-word, not less total
compute — Layer 3 still has to eventually write ~1.5M words. The plan below
bakes in staged, human-reviewed verification for exactly that reason: read
the arc plan before spending GPU time on segments, read a couple of segment
briefs before spending GPU time on dialogue.

## Layout

```
utilities/3LayersWeeklyGeneration/
  main.py                        # entry point: --stage arc|segment|dialogue|all
  src/
    config.py                    # load_config() for generation.yaml
    plan_arc.py                  # Layer 1
    plan_segment.py              # Layer 2
    generate_segment_dialogue.py # Layer 3
  config/
    generation.yaml              # the one config file — see below
  output/                        # default output.dir target; <output.dir>/<pack-name>/...
  README.md
```

Per CLAUDE.md's file-organization convention: `main.py` is the thin
orchestrator (argparse, calls into `src/`, minimal logic of its own); each
`src/` module exposes a plain function main.py calls — no per-module
argparse of its own, one CLI surface. Each module reaches the existing
campaign code by inserting the repo's `app/` onto `sys.path` at import time,
the same pattern `tests/conftest.py` already uses, then imports
`campaign.pack`, `campaign.improviser`, `campaign.batch_generate`, and
`llm_client` unchanged — **no changes to any file under `app/`**.

`app/campaign/batch_generate.py` and its output under
`campaigns/ashiorid/generated/` are untouched and still valid on their own
for regenerating the 8 static ambient scenes. This utility is additive and
separate.

## Config — `utilities/3LayersWeeklyGeneration/config/generation.yaml`

```yaml
campaign:
  pack: campaigns/ashiorid        # default; --pack overrides, repo-root-relative

output:
  dir: utilities/3LayersWeeklyGeneration/output   # <dir>/<pack-name>/... underneath

arc:
  models:                          # named, swappable profiles — add as many as you like
    light:
      provider: ollama
      base_url: "http://localhost:11434"
      model: "qwen3-coder:30b"
      temperature: 0.7
      max_tokens: 4096
    heavy:
      provider: ollama
      base_url: "http://localhost:11434"
      model: "hermes3:70b"
      temperature: 0.7               # consistency over variety for planning
      max_tokens: 4096
  active_model: heavy              # profile used when --model-profile isn't passed
  hours_total: 168
  segment_hours: 6                 # ~28 segments at the defaults
  batch_size: 6                    # segments requested per LLM call
  max_attempts: 2

segment:
  models:
    light:
      provider: ollama
      base_url: "http://localhost:11434"
      model: "qwen3-coder:30b"
      temperature: 0.7
      max_tokens: 4096
    heavy:
      provider: ollama
      base_url: "http://localhost:11434"
      model: "hermes3:70b"
      temperature: 0.7
      max_tokens: 4096
  active_model: heavy
  max_attempts: 2

dialogue:
  models:
    light:
      provider: ollama
      base_url: "http://localhost:11434"
      model: "llama3.1:8b"          # the existing live-improv model — cheapest useful baseline
      temperature: 0.9
      max_tokens: 1024
    heavy:
      provider: ollama
      base_url: "http://localhost:11434"
      model: "hermes3:70b"
      temperature: 0.9                # same as ashiorid_batch.yaml — variety matters here
      max_tokens: 1024
  active_model: heavy
  max_recent: 8
  max_words: 45
  max_beats: 12
  takes_per_slot: 3
  max_attempts: 2
```

Each profile's `llm:` shape is handed straight to `app/llm_client.py`'s
existing `build_llm_client({"llm": ...})` — unchanged, no new client code.
`active_model` picks the default profile per layer; `--model-profile NAME`
on the CLI overrides it for whichever stage(s) that invocation runs. Adding
a new profile (a third model, a different temperature) is a config-only
change — no code touched.

### Test mode

`--test-mode` (combined with `--segments <one-id>`, required in test mode to
keep it cheap) runs **every** profile listed under the active layer's
`models:` map against that one segment/slot, instead of just
`active_model`. Output goes to a separate, non-resumable path —
`<output.dir>/<pack>/_test/<stage>/<profile>/...` — so a test run never
pollutes `arc_plan.yaml`, a real `brief.yaml`, or a real manifest. After
each profile finishes, it prints a one-line summary (model, wall-clock
seconds, word/segment count) so profiles are comparable at a glance without
opening every file.

This is the practical answer to "can a lighter model do this layer" —
point `--test-mode` at one segment, read the light vs. heavy output side by
side, then set that layer's `active_model` accordingly before a full run.

`src/config.py` resolves `(layer, profile_name)` -> the concrete `llm:` dict
(profile_name defaults to that layer's `active_model`, overridable via
`--model-profile`) and hands it to `build_llm_client({"llm": ...})` — every
layer function below takes an already-resolved LLM client, never the raw
config, so test-mode's "run every profile" loop is just calling the same
layer function once per profile with a different client.

## Layer 1 — `src/plan_arc.py`

```
load pack, resolve arc's LLM client for the selected profile(s), build client
context = campaign title/genre + ordered spine scene summaries (id, title,
          enter_narration) + lore notes + existing ambient scene ids/prompts
n_segments = ceil(hours_total / segment_hours)   # ~28 at the defaults

resume: load existing arc_plan.yaml if present, keep its segments, only plan
        what's missing

for each batch of batch_size unplanned segments:
    prompt = context + the last-planned segment's continuity_out (or "this
              is the start of the arc" for the first batch) + explicit
              instruction: reply with ONLY a YAML list under `segments:`,
              one entry per segment, each carrying exactly
              {id, order, loop, hours, spine_scenes, ambient_focus, synopsis,
              continuity_in, continuity_out, branch_note} — spine_scenes is
              usually empty (spine scenes are rare/high-value, ~15 min each;
              most segments are pure ambient/downtime); loop increments each
              time a segment plays portal-encounter (the spine's last scene)
    reply = llm.complete(...)
    parsed = parse + validate (every segment has the required keys, id is
              unique, order is monotonic) — on failure, retry up to
              max_attempts, then log a warning and SKIP this batch (never
              raise — matches generate_scene's own contract)
    append validated segments, write arc_plan.yaml after every batch (a
    crash mid-run loses at most one batch)

report: segments planned, segments skipped, hours covered
```

## Layer 2 — `src/plan_segment.py`

```
load pack, resolve segment's LLM client for the selected profile(s), load arc_plan.yaml
segment_ids = args.segments or every segment without a brief.yaml yet

for each segment_id:
    segment = arc_plan.segments[segment_id]
    previous_out = the immediately preceding segment's continuity_out, if any
    prompt = segment.synopsis + continuity_in/out + relevant lore (from any
              spine_scenes' pack.scene(id).lore, plus ambient_focus scenes'
              lore) + cast roster + for each id in segment.spine_scenes, that
              scene's ACTUAL enter_narration and beat text as ground truth
              (the model bridges around real scripted content, never
              invents spine plot) + instruction: reply with ONLY a YAML list
              under `scenes:`, each entry either
                {slot_id, kind: spine, scene_ref, participants, summary}
              or
                {slot_id, kind: ambient, prompt, lore, participants, target_minutes}
              — ambient prompts must state length and "written for the ear",
              matching the pack format doc's existing ambient-authoring rule
    reply = llm.complete(...)
    parsed = parse + validate (required keys per kind; scene_ref must exist
              in pack.scenes for kind: spine) — retry up to max_attempts,
              then log and SKIP this segment (never raise)
    write <output>/<pack>/segments/<segment_id>/brief.yaml

report: segments briefed, segments skipped
```

## Layer 3 — `src/generate_segment_dialogue.py`

```
load pack, resolve dialogue's LLM client for the selected profile(s),
     build LLMImproviser — same construction batch_generate.py already does

segment_ids = args.segments or every segment with a brief.yaml
for each segment_id:
    brief = load <output>/<pack>/segments/<segment_id>/brief.yaml
    for each slot in brief.scenes where slot.kind == "ambient":
        construct a throwaway campaign.pack.Scene(id=slot.slot_id,
            prompt=slot.prompt, lore=slot.lore) — reuses the existing
            dataclass, no pack.py changes
        resume at next unused take number under
            <output>/<pack>/segments/<segment_id>/scenes/<slot_id>/
        for takes_per_slot:
            beats = batch_generate._generate_take(improviser, scene, take,
                     max_attempts)   # imported directly, not reimplemented
            write take file + append to
                <output>/<pack>/segments/<segment_id>/manifest.jsonl
                (via batch_generate._write_take / _append_manifest)
    # kind: spine slots are left untouched — canon, already scripted,
    # never regenerated by this layer

report: takes written, words generated, per segment
```

`generate_segment_dialogue.py` imports `_generate_take`, `_write_take`,
`_append_manifest`, `_next_take_number` straight from
`campaign.batch_generate` — same primitive, different prompt source, zero
duplication.

## Tests

Per the project's "smart test coverage" convention — this is orchestration
around already-tested primitives (`generate_scene` has 90 tests), so new
tests target only the new parsing/validation/resume logic, no network:

- `tests/test_plan_arc.py` — batch-boundary resume (existing arc_plan.yaml
  with N segments only plans the remainder), validation of a well-formed
  LLM reply, retry-then-skip-batch on a malformed reply, round-trip through
  `yaml.safe_load`.
- `tests/test_plan_segment.py` — prompt context built from a segment +
  previous continuity_out, validation of spine vs ambient slot shapes,
  `scene_ref` existence check against a fake pack, retry-then-skip on a
  malformed reply.
- `tests/test_generate_segment_dialogue.py` — reads a fake `brief.yaml`,
  confirms spine slots are skipped and ambient slots aren't, confirms it
  calls the imported `batch_generate._generate_take` (patched) rather than
  a reimplementation.
- `tests/test_config.py` — profile resolution: `active_model` picked by
  default, `--model-profile` override picked when given, unknown profile
  name raises a clear config error rather than a KeyError traceback,
  test-mode's "every profile for this layer" enumeration returns all named
  entries in `models:`.

Each test file inserts both `app/` and
`utilities/3LayersWeeklyGeneration/src/` onto `sys.path` (mirroring
`tests/conftest.py`'s existing pattern) and imports the leaf module names
directly (`from plan_arc import ...`) — the directory name isn't a valid
Python identifier, so it's never imported as a dotted package, only path-
inserted.

## Verification

```bash
.venv/bin/python -m pytest tests/test_plan_arc.py tests/test_plan_segment.py \
    tests/test_generate_segment_dialogue.py -q

# Stage 0 (optional but recommended) — test-mode: run every configured
# profile for one layer against ONE segment, compare quality/speed before
# committing to any full-scale run.
PYTHONPATH=app .venv/bin/python utilities/3LayersWeeklyGeneration/main.py \
    --stage arc --test-mode --segments seg-001 -v
PYTHONPATH=app .venv/bin/python utilities/3LayersWeeklyGeneration/main.py \
    --stage dialogue --test-mode --segments seg-001 -v
# -> eyeball utilities/3LayersWeeklyGeneration/output/ashiorid/_test/<stage>/<profile>/
#    and the printed per-profile timing/word-count summary; set each layer's
#    active_model in generation.yaml based on what you see.

# Stage 1 — plan the arc for real, then READ arc_plan.yaml by hand before
# spending any more GPU time. This is the one artifact everything else
# depends on.
PYTHONPATH=app .venv/bin/python utilities/3LayersWeeklyGeneration/main.py \
    --stage arc -v

# Stage 2 — brief a couple of segments, read them
PYTHONPATH=app .venv/bin/python utilities/3LayersWeeklyGeneration/main.py \
    --stage segment --segments seg-001,seg-002 -v

# Stage 3 — generate dialogue for just those, read it
PYTHONPATH=app .venv/bin/python utilities/3LayersWeeklyGeneration/main.py \
    --stage dialogue --segments seg-001,seg-002 -v

# Override the profile for any single invocation without editing the config:
PYTHONPATH=app .venv/bin/python utilities/3LayersWeeklyGeneration/main.py \
    --stage dialogue --segments seg-001 --model-profile light -v

# Confirm the live path is still untouched
PYTHONPATH=app .venv/bin/python app/campaign/cli.py --pack campaigns/ashiorid \
    --dry-run --no-pace --no-color | diff - /tmp/.../scratchpad/canon-now.txt

# Once the format is trusted: --stage all runs arc -> segment -> dialogue in
# one pass, resumable at each layer.
```

`utilities/3LayersWeeklyGeneration/README.md` documents the config shape,
the three stages, and the staged-verification order above, per CLAUDE.md's
per-utility documentation convention.

## Out of scope (unchanged)

- No changes to `app/campaign/renderer.py`, `runtime.py`, or `cli.py`.
- No changes to the live `config/campaigns/ashiorid.yaml` / `llama3.1:8b`.
- No changes to `app/campaign/pack.py`, `improviser.py`, or
  `batch_generate.py` — all reused as-is, imported not duplicated.
- `app/campaign/ambient.py` (T4, live-runtime injection scheduling) is a
  separate, already-specced concern
  ([tools/qwen_worker/specs/campaign_ambient.yaml](../../../codeProjects/virtualTubers/tools/qwen_worker/specs/campaign_ambient.yaml))
  and isn't touched or duplicated here.
- Not wiring the generated cache into the live show — still T5/T6, tracked
  separately.
