# Hierarchical 3-layer offline content generator — utilities/3LayersWeeklyGeneration

> Design decisions from the two 2026-08-18 planning sessions — session A
> resolving issues #2, #6 and #7 plus the concurrency finding that reset the
> budget, session B settling efficiency, random events and story branching
> (D10-D18) — are recorded in
> [.claude/prompts/three_layers_design_decisions.md](../../.claude/prompts/three_layers_design_decisions.md),
> which also carries the sequenced build plan. The review backlog is
> [.claude/prompts/three_layers_generation_issues.md](../../.claude/prompts/three_layers_generation_issues.md).

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
    config.py                    # load_config() for generation.yaml, profile resolution
    concurrent_llm.py            # OllamaClient subclass: pooled httpx, configurable timeout
    worklist.py                  # scan-and-assign pass: deterministic paths, ordered queue
    pool.py                      # worker pool, per-worker improviser, single writer, breaker
    vocabulary.py                # the closed-set validator: lore stems, state keys,
                                 #   event ids, fork carry keys — one pass, four callers
    forks.py                     # fork declaration + convergence validator (Layer 1)
    events.py                    # events.yaml schema, spine-canon validator, stinger worklist
    plan_arc.py                  # Layer 1
    plan_segment.py              # Layer 2 (2a chapters -> 2b slots)
    generate_segment_dialogue.py # Layer 3
  config/
    generation.yaml              # the one config file — see below
    events.yaml                  # the random-event table: L1 drafts it, a human reviews it
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

budget:                             # derivation lives in the "Budget" section below
  measured_baseline:                 # campaigns/ashiorid/generated/manifest.jsonl, 24 takes
    words_per_take: 105
    beats_per_take: 7.7
    generation_words_per_min: 95.4   # GPU wall-clock generation throughput, NOT spoken pace
  target_total_words: 1500000
  target_total_hours: 168
  target_words_per_hour: 8929        # target_total_words / target_total_hours
  takes_per_slot_semantics: choice_pool  # resolved, issue #7 — see Budget below
  estimated_gpu_hours_sequential: 262    # single-stream hermes3:70b
  estimated_gpu_hours_parallel: 52       # at parallel-8, assuming ~5x (UNMEASURED)

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
  forks:                           # macro-branching — see "Randomness, events and branching"
    count: 4                       # fork points across the arc
    variants_per_fork: 2           # trunk + 1 alternative; +4 segments, +7.5 GPU-h
    at: loop_boundary              # only ever at a portal-encounter reset
    allow_nested: false            # a fork must merge before the next one opens

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
  target_words: 53600      # segment_hours (6, from arc:) * budget.target_words_per_hour
  target_slots: 170        # target_words / (budget.measured_baseline.words_per_take * dialogue.takes_per_slot)
  chapters_per_segment: 9  # ~40 min each; ~19 slots per 2b call (see Layer 2)
  concurrency: 4           # segments briefed in parallel; must be <= OLLAMA_NUM_PARALLEL
  max_attempts: 2
  sensitivity_budget: 0.40 # max fraction of a segment's slots that may be
                           #   `flags`-sensitive before the 3-take pool stops
                           #   covering them and the airtime patch tier is
                           #   over-subscribed. Exceeded -> WARNING, re-brief.

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
      num_ctx: 8192                   # drives per-slot KV cache -> how many slots fit
      timeout_s: 600                  # must exceed batched per-request latency
  active_model: heavy
  max_recent: 8
  max_words: 45
  max_beats: 12
  takes_per_slot: 3        # a CHOICE POOL, not curation alternates — see Budget
  neutral_takes: 1         # take 001 is ALWAYS unconditioned: the never-dead-air
                           #   guarantee. The remaining 2 are state-conditioned.
  concurrency: 8           # worker pool size; must equal OLLAMA_NUM_PARALLEL
  max_attempts: 2
  stingers:
    takes_per_event: 3     # short interrupt beats announcing an event on screen

# The closed state vocabulary. Every `depends_on`, every event `sets:`/
# `requires:`, and every fork's `carry_out` may name ONLY these. Anything else
# fails validation rather than silently evaporating — the same failure mode
# issue #3 documents for unknown lore stems, handled by the same validator.
state:
  flags:      [helen-wounded, moonwell-tainted, buffalo-lost-axe]   # booleans
  moods:      [tense, weary, hopeful, giddy]                        # coarse tone dial
  carry_keys: [helen-wounded, moonwell-tainted]  # subset of flags; survives a loop reset

events:
  table: config/events.yaml
  roll_at: chapter         # chapter | segment — chapter is ~252 rolls over 168 h
  max_hot: 2               # concurrent live events
  seed: 20260818           # rolls derive from (seed, run_id, chapter_id): reproducible
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
              continuity_in, continuity_out, carry_in, carry_out,
              event_windows, fork} — `carry_in`/`carry_out` are CLOSED-
              vocabulary state (config `state.carry_keys`), distinct from the
              prose `continuity_in`/`continuity_out`; `event_windows` lists
              which event ids may fire in this segment; `fork` is present only
              on a fork variant, as {id, variant, merge_at}. spine_scenes is
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

# --- fork pass (src/forks.py) ---
declare arc.forks.count fork points, each at a loop boundary (a segment whose
    spine_scenes include portal-encounter), each with variants_per_fork
    variants sharing one merge_at target
validate the convergence contract (see Randomness section) — every variant
    merges at the same segment, writes the SAME carry_out key set with values
    from state.carry_keys, and no post-merge segment references which variant
    ran. A violation FAILS the stage; it is never repaired silently.

# --- event-table draft (src/events.py) ---
prompt the arc model for ~40 events grounded in the pack's lore and cast,
    each {id, title, weight, windows, requires, sets, survives_loop_reset,
    decay_hours, tone, scope, stinger}
validate: scope == ambient for every entry; requires/sets name only
    config.state keys and values; no event asserts state a spine scene's
    enter_narration or beat text contradicts
write config/events.yaml — then STOP. This file is authored canon in
    everything but who typed it, and a human reads it before Layer 2 runs.

report: segments planned, segments skipped, hours covered, forks declared,
        events drafted
```

## Layer 2 — `src/plan_segment.py` (2a chapters -> 2b slots)

**Resolves issue #2.** A 6-hour segment needs ~170 slots (see Budget); at
~60 tokens/slot that is ~10,200 tokens, which does not fit a 4096-token call.
Rather than blind-batching (which gives the model no structural anchor — it
would not know what span a batch covers) or shrinking `segment_hours` (the arc
segment count and spine pacing are written around 6 h), Layer 2 fans out:

- **2a — chapters.** One call per segment emits `chapters_per_segment` (~9)
  chapter one-liners of ~40 min each, every one carrying its own
  `continuity_in` / `continuity_out`. Written to
  `<output>/<pack>/segments/<segment_id>/chapters.yaml`.
- **2b — slots.** One call per chapter emits ~19 slots, ~1,140 tokens —
  comfortable inside 4096. Written to
  `<output>/<pack>/segments/<segment_id>/chapters/<chapter_id>/slots.yaml`,
  then merged into `brief.yaml` once every chapter is present.

Resume granularity is the chapter, so a crash mid-segment re-plans at most one
chapter's slots.

**Parallelism precondition (do not violate):** cross-segment continuity flows
through `arc_plan.yaml` — which Layer 1 has already written — and *never*
through a sibling segment's `brief.yaml`. Likewise every 2b call derives its
continuity from `arc_plan.yaml` plus 2a's chapter list only. If segment 7's
brief depended on segment 6's brief, Layer 2 would collapse into a 28-deep
serial chain; with the dependency routed through the arc plan, all 28 segments
(and all chapters within them) run concurrently. See Concurrency below.

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
                {slot_id, kind: ambient, prompt, lore, participants,
                 sensitivity, depends_on}
              — `sensitivity` is one of none|tone|flags and `depends_on` names
              closed-vocabulary state keys (empty unless sensitivity: flags);
              together they are what tells Layer 3 how to spend takes 002/003
              and what makes airtime patch load predictable at build time;
              ambient prompts must state length and "written for the ear",
              matching the pack format doc's existing ambient-authoring rule;
              the prompt also states the segment's config.segment.target_words
              / target_slots (see Budget) as an explicit stop condition, so
              the model has a concrete count to fill instead of an
              open-ended "sequence of beats"
    reply = llm.complete(...)
    parsed = parse + validate (required keys per kind; scene_ref must exist
              in pack.scenes for kind: spine; every `lore` stem in pack.lore
              and every `depends_on` key in config.state — one closed-set
              pass, src/vocabulary.py) — retry up to max_attempts, then log
              and SKIP this segment (never raise)
    check the sensitivity budget: if the fraction of slots with
              sensitivity == flags exceeds segment.sensitivity_budget, log a
              WARNING and mark the segment for re-brief — the 3-take pool
              cannot cover it and the airtime patch tier would be
              over-subscribed
    mark each chapter boundary as an event roll point
    write <output>/<pack>/segments/<segment_id>/brief.yaml

report: segments briefed, segments skipped, sensitivity mix per segment
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
        plan this slot's take conditions (dialogue.neutral_takes):
            take 001 -> conditions {} — NEUTRAL, no state dependency, the
                        never-dead-air guarantee
            takes 002/003 -> one declared condition each, drawn from the
                        slot's depends_on; for sensitivity: none slots they
                        stay unconditioned plain variants
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

# --- stinger pass (src/events.py) ---
for each event in events.yaml with stinger: true:
    for stingers.takes_per_event:
        generate a short interrupt scene announcing the event on screen, so a
        random event is SEEN rather than silently true in a state dict
    write <output>/<pack>/stingers/<event_id>/NNN.yaml
# ~40 events x 3 takes x ~105 words = ~12,600 words: ~0.4 GPU-h at parallel-8

guard (hard): every ambient slot has exactly one take with conditions {}. A
    run that ends without it has produced a library that can dead-air.

report: takes written, words generated, per segment; neutral-take coverage;
        stingers written
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
- `tests/test_worklist.py` — the scan-and-assign pass: an existing partial
  output tree yields only the missing `(segment, slot, take)` units, take
  numbers are assigned deterministically (no two units claim the same path),
  and re-running the scan after a partial drain is idempotent.
- `tests/test_pool.py` — a fake slow/failing client confirms per-worker
  improvisers are not shared (no cross-take context bleed), the single writer
  serialises manifest appends, and the rolling failure-rate circuit breaker
  aborts rather than looping on empty takes.
- `tests/test_vocabulary.py` — the closed-set validator: an unknown lore
  stem, an unknown state flag, an unknown event id and an out-of-vocabulary
  fork carry key each fail with a message naming the offending key, and a
  fully valid set passes. This is issue #3's fix and the state-vocabulary
  check in one pass, so it is tested once.
- `tests/test_forks.py` — the convergence contract: variants that merge at
  different segments, that write differing `carry_out` key sets, or that nest
  are each rejected; a well-formed fork passes; a post-merge segment
  referencing a variant id is rejected.
- `tests/test_events.py` — events.yaml schema validation, `scope: spine`
  rejected, `sets:`/`requires:` outside the declared vocabulary rejected, and
  an event asserting state a fake pack's spine scene contradicts rejected.
- `tests/test_config.py` — profile resolution: `active_model` picked by
  default, `--model-profile` override picked when given, unknown profile
  name raises a clear config error rather than a KeyError traceback,
  test-mode's "every profile for this layer" enumeration returns all named
  entries in `models:`, and the `state:` / `events:` / `arc.forks` blocks
  parse with their documented defaults.
- `tests/test_take_conditions.py` — take-condition planning: take 001 is
  always emitted with `conditions: {}`, takes 002/003 draw one condition each
  from the slot's `depends_on`, a `sensitivity: none` slot yields three
  unconditioned variants, and the hard neutral-coverage guard fails a fake
  output tree in which some slot has no neutral take.

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

# Stage 3 — generate dialogue for just those, read it.
# Concurrency is server-side as well as config-side: the ollama instance must
# be started with matching settings, or the pool just queues inside it.
#   OLLAMA_CONTEXT_LENGTH=8192  OLLAMA_NUM_PARALLEL=8
# Benchmark parallel 1/4/8/12 against the real prompt shape first and set
# dialogue.concurrency / num_ctx from the knee of that curve.
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

## Concurrency

The single largest cost lever, and the reason the 262-hour sequential figure
above is not the number to plan against.

### Hardware and why multiple instances lose

Target machine is an NVIDIA **GB10** (DGX Spark class): 121 GB unified memory,
20 cores, ~273 GB/s memory bandwidth.

**Do not run multiple hermes3:70b instances.** Two instances means two 40 GB
weight copies (80 GB of 121 GB, leaving nothing for KV cache) *and* both
contend for the same ~273 GB/s bus. Decoding is memory-bandwidth-bound — every
token streams the full 40 GB of weights — so two instances read 80 GB per
token-pair instead of 40 GB per token: identical aggregate throughput at best,
each instance at half speed, near-OOM.

The same ceiling explains the measured baseline: 273 GB/s / 40 GB ~= 6.8 tok/s
theoretical max against ~2.1 tok/s measured (95.4 words/min). The model is slow
because it is large on a narrow bus, not because it is under-parallelised.

**Concurrent requests to one instance is the win.** Read the 40 GB once, decode
a token for N sequences simultaneously. The workload is ideal — ~14,300 takes,
every one independent (see issue #7's resolution above).

Two server-side settings currently block it:

1. `OLLAMA_NUM_PARALLEL` is unset, so generation runs single-stream. This is
   the entire reason the baseline is 95.4 words/min.
2. `OLLAMA_CONTEXT_LENGTH=64000` — KV cache is allocated *per parallel slot*.
   For this architecture (80 layers, 8 GQA KV heads, 128 head-dim, fp16) that
   is ~320 KB/token:

   | Context | KV per slot | 8 slots + 40 GB weights |
   |---|---|---|
   | 64,000 (current) | ~20.5 GB | **~204 GB — does not fit** |
   | 8,192 | ~2.7 GB | ~61 GB — fits comfortably |

   Layer 3 prompts are ~500 tokens with `max_tokens: 1024`; 64k is ~30x the
   workload and caps the machine at ~3 slots instead of 8-12.

For the generation run:

```bash
OLLAMA_CONTEXT_LENGTH=8192
OLLAMA_NUM_PARALLEL=8
```

**Estimated gain: 4-6x aggregate** at parallel-8 (not a full 8x — KV traffic
and prefill eat into it), taking 262 GPU-hours to **~52 (~2.2 days)**. This
multiplier is **not yet measured** — benchmark parallel 1/4/8/12 against the
real Layer 3 prompt shape and set `dialogue.concurrency` / `num_ctx` from the
knee of that curve before committing to a full run.

### Where concurrency applies

| Layer | Calls | Parallel? |
|---|---|---|
| L1 arc | ~5 | **No** — batch N needs batch N-1's `continuity_out`. Serial by nature, ~20 min total. |
| L2a / L2b | ~28 + ~250 | **Yes**, given the precondition in Layer 2 above |
| L3 dialogue | ~14,300 | **Yes** — fully independent, ~99 % of GPU time |

Plan serially, generate concurrently.

Context length and concurrency trade directly against each other, and the
layers want opposite ends — which is fine, since each stage is a separate
invocation:

| Layer | Prompt size | `num_ctx` | `concurrency` |
|---|---|---|---|
| L1 / L2 | large (lore, spine narration, chapter lists) | 16-32k | 1-4 |
| L3 | ~500 tokens | 8192 | 8-12 |

### Implementation requirements

- **Plan-then-drain, not lazy allocation.** `_next_take_number` globs the scene
  directory to pick the next take number; under concurrency two workers both
  see `002.yaml` and both claim take 3. Replace it with a **work-list pass
  before any GPU time** (`src/worklist.py`): scan the output tree, compute every
  `(segment, slot, take)` not yet present, assign deterministic output paths up
  front, emit an ordered list, then drain with a fixed pool. Gains race-freedom,
  idempotent resume (re-running the scan *is* the resume), and an exact work
  count and ETA before a multi-day run starts.
- **One improviser per worker; one shared LLM client.** `OllamaClient` is
  thread-safe — `app/llm_client.py` uses module-level `httpx.post` with no
  shared session and read-only instance attributes. `LLMImproviser` is **not**:
  it holds mutable `scene`/`carry`/`loop`/`recent` that `update_context`
  mutates. Construct one improviser per worker thread, sharing the read-only
  `pack` and the single client.
- **This is also issue #6's fix.** Per-worker improvisers rule out reusing
  `_generate_take` verbatim (it takes an improviser and mutates it) — which is
  the same ~10-line local take function issue #6 already requires, to stop
  `loop=take` clobbering the arc's loop value. One function resolves both;
  `_write_take` / `_append_manifest` reuse stays intact.
- **Single writer.** Workers return results; one consumer thread performs every
  file write and `_append_manifest`. No locks, no interleaved JSONL, clean
  crash semantics.
- **Timeout wrapper (`src/concurrent_llm.py`).** `app/llm_client.py` hardcodes
  `timeout=120`. Batching raises throughput *by raising per-request latency*;
  at parallel-8 a request may take 3-5x longer than the measured 65 s and cross
  120 s. The failure is silent — `generate_scene` catches it and returns `[]`,
  indistinguishable from "the model wrote nothing" — so a run would burn hours
  writing empty files. Per CLAUDE.md's shared-utilities rule (wrap, don't
  patch), subclass `OllamaClient` locally: shared `httpx.Client` for connection
  pooling across 14,300 calls, plus a configurable `timeout_s`. **No file under
  `app/` is modified.**
- **Circuit breaker.** Because `generate_scene` never raises, a dead ollama, an
  OOM, or a wrong model name yields an infinite stream of empty takes. A rolling
  failure-rate check that aborts with `CRITICAL` past a threshold is the
  difference between losing ten minutes and losing two days.
- **Drain in arc order, not shuffled**, so stopping at any point leaves a
  contiguous usable prefix of airtime rather than a scattered fraction that
  cannot air. Costs nothing in throughput.
- **Pool size == `OLLAMA_NUM_PARALLEL`.** Oversubscribing merely queues inside
  ollama, where visibility is lost and timeouts are hit.

## Runtime tiers (downstream of this utility)

Scripts drift before airing — random factors and story branches mean the whole
voiceline cannot be mapped ahead of time. That is workable, but it exposes an
arithmetic wall: **hermes3:70b cannot generate just-in-time.** It produces 95.4
words/min against 149 words/min of airtime consumption, so a buffer drains at
~54 words/min — roughly **21 minutes of airtime lost per hour on air**.

Late-binding therefore cannot come from the bulk model:

| Tier | Model | When | Job |
|---|---|---|---|
| Bulk library | hermes3:70b | offline, resumable | Layers 1-3 — this utility |
| Late-binding patch | `llama3.1:8b` | minutes before a slot airs | regenerate only slots whose state has drifted |
| Voice | Piper | at the beat | real-time, one-beat lookahead |

This is not model tiering for bulk content — the fast model never writes bulk,
only patches, so voice consistency across the 168 hours is preserved.

The scheduler is a **just-in-time picker**, not a precomputed playlist: at
airtime it either airs a cached take from the slot's choice pool or requests a
fresh one. Whether a take may air more than once therefore becomes a runtime
policy knob (`min_hours_before_repeat`) rather than a build-time commitment.

TTS is real-time and cheap: Piper medium voices run at RTF ~0.1-0.25 on CPU
(~0.6-1.4 s for a ~5 s beat), the renderer is sequential so at most one
synthesis is in flight, and one CPU core carries a stream with 4-10x headroom.
Remote synthesis via `_piper_remote` costs 353 kbit/s for `-medium` (22050 Hz)
or 256 kbit/s for `-low` (16000 Hz); local synthesis costs zero. Two renderer
defects are filed against this tier, not against this utility:
`app/campaign/renderer.py` synthesizes and plays sequentially per beat (a ~1 s
hitch before every beat — needs one-beat lookahead) and has no audio-sync
`scale` factor, unlike `app/replay.py`'s `Pacer`.

## Randomness, events and branching

Decided in the 2026-08-18 session recorded as D10-D18 in the decisions doc.
The through-line: **branching is affordable only if it is paid for in state,
not in script.** Layers 1-3 emit the metadata the runtime needs; no dice are
rolled here.

### Two kinds of divergence, and only one of them costs GPU time

"The story branches" was covering two things with wildly different costs:

| | **Micro-drift** | **Macro-fork** |
|---|---|---|
| What changes | what is *said* in a slot | *which segment* comes next |
| Frequency | continuous, ~all 168 h | 4 declared points |
| Mechanism | conditioned takes + JIT patch | `arc_plan.yaml` DAG edge |
| Marginal GPU cost | **zero** | ~1.86 GPU-h per variant |
| Reconverges | n/a — never diverged | at the next loop reset |

Everything that reads as "the story branched" on stream is micro-drift, and
micro-drift is free. Macro-forks are the small, budgeted exception.

### Micro-drift — the choice pool respent as state coverage

The best-value decision in the plan, because it costs nothing.

`takes_per_slot: 3` currently buys three interchangeable variants of a moment.
Respend the same three takes across *state space* instead of across style:

- **Take 001 is always neutral** — no state dependency, airs under any state.
- **Takes 002/003 are conditioned** — each generated under one declared
  precondition from that slot's `depends_on`.

Same 3 takes, same ~1.5M words, same ~52 GPU-h. The pool now spans the states
the story can actually be in, so a drifted state usually finds a cached take
instead of falling through to the patch tier.

Slots Layer 2 marks `sensitivity: none` keep all three as plain variants —
nothing is wasted either way.

**Take 001's neutrality is a load-bearing invariant.** It is the guarantee the
show can never dead-air: whatever has drifted, whatever is down, there is
always something airable. Enforced by the hard post-Layer-3 guard above.

### Macro-forks — a shallow DAG that re-converges on loop boundaries

The time loop is the branch-collapse mechanism, and it is already in the
runtime: [runtime.py](../../app/campaign/runtime.py)'s `reset(keep_carry=True)`
clears `context` and `history` but preserves `carry`. A fork opened inside a
loop is closed by the next `portal-encounter`, and `carry` is the only channel
through which it may leave a mark. That is what stops branching being 2^n.

```
seg-06 ──┬── seg-07a ──┐
         └── seg-07b ──┴── seg-08   (merged carry)
```

**The convergence contract — hard, validated post-Layer-1, never repaired
silently:**

1. Every variant of a fork names the same `merge_at` segment.
2. Every variant writes the **same set** of `carry_out` keys — differing in
   values only, and only values from `state.carry_keys`.
3. No segment downstream of `merge_at` may condition on *which* variant ran,
   only on the merged `carry`.
4. Forks never nest (`allow_nested: false`). A fork merges before the next
   opens.

Rule 3 is the one a model will violate given the chance, so it goes into Layer
1's prompt verbatim *and* is checked by `src/forks.py`.

**The +14% is not waste.** 4 extra segments = ~214,400 words = ~7.5 GPU-h at
parallel-8. Only one side of each fork airs per pass — but 168 h contains
multiple loops, and a fork point recurring on a later loop airs the *other*
variant. Fork variants are inventory that amortises across loops. (Whether
fork points do recur falls out of `arc_plan.yaml` — check it when reading the
plan by hand at Stage 1, rather than assuming it.)

### Random events — a closed table, rolled at chapter boundaries

Open-ended randomness cannot be pre-generated against, because coverage has no
denominator. So the events are a **closed table**, `config/events.yaml`,
drafted by Layer 1 and **reviewed by hand** before Layer 2 runs:

```yaml
events:
  - id: storm-rolls-in
    title: "A storm rolls in"
    weight: 3
    windows: [any]              # `any`, segment ids, or chapter tags
    requires: {weather: clear}  # preconditions, closed vocabulary
    sets: {weather: storm}      # state delta, closed vocabulary
    survives_loop_reset: false  # true -> writes carry; false -> writes context
    decay_hours: 4
    tone: tense
    scope: ambient              # MUST never be spine
    stinger: true
```

**Roll cadence is the chapter boundary** (~40 min), not the beat and not the
segment. This falls out of Layer 2's chapter tier at no extra cost: ~252 rolls
across 168 h, a natural dramatic cadence, and the picker knows a chapter's
state before it has to pick any of that chapter's ~19 slots.

**`scope: ambient` is absolute.** An event may never assert state a scripted
spine scene contradicts — the spine is authored canon and this pipeline's whole
reuse story depends on not touching it. Because Layer 1 *drafts* the table,
this needs a validator and a human gate, not trust.

**Determinism.** Seeds derive from `(events.seed, run_id, chapter_id)`, so a
replay reproduces the same rolls exactly. Every roll appends to
`<output>/<pack>/ledger.jsonl` — event fired, state before/after, take chosen,
patched or not. That is both the audit trail for `carry` and what preserves the
replay guarantee [primitives.py](../../app/campaign/primitives.py) already
demands of the render tier.

**Stingers** make an event visible: a short pre-generated interrupt announcing
it on screen, so a random event is *seen*, not silently true in a dict.
~40 events x 3 takes x ~105 words = ~0.4 GPU-h — effectively free.

### Three clocks — and the renderer's is not one of them

Randomness enters at exactly one of these. Stated explicitly because
conflating them is how the replay guarantee gets broken.

| Clock | When | What is decided |
|---|---|---|
| **Build time** | offline, this utility | L1 declares fork points + event windows and drafts the table; L2 labels slot sensitivity; L3 writes neutral + conditioned takes + stingers. **No rolls.** |
| **Airtime − minutes** | the JIT picker | Roll events at the chapter boundary; resolve forks at the loop boundary; pick a take; patch on a miss. **All rolls happen here.** |
| **At the beat** | the renderer | **Nothing random.** Byte-identical forever. |

The picker's decision rule, in order:

1. Compute live state from `carry` + `context` + hot events.
2. Choose the most *specific* take whose `conditions` the live state satisfies.
3. If only the neutral take matches but the slot is `flags`-sensitive and hot —
   patch it with `llama3.1:8b`.
4. If the patch tier is unavailable or out of time — **air the neutral take.**
   Never dead-air.

Steps 3-4 are why the neutral invariant is load-bearing.

### Known blocker for the runtime tier: `carry` is invisible to the selector

Found while verifying the fork design against the code:

- [runtime.py:111](../../app/campaign/runtime.py#L111) — `advance()` passes
  `self.state.context` to `graph.next_scene_id()`. `carry` is never passed.
- [scene_graph.py](../../app/campaign/scene_graph.py) — `_branch_matches`
  tests `branch.when` against that `context` dict only.
- [runtime.py:156](../../app/campaign/runtime.py#L156) — `reset()` sets
  `context = {}` while preserving `carry`.

So branch conditions read **only loop-local state**, and `carry` — the fork
design's entire convergence channel — is structurally unreadable by the
selector. This does not change the fork design and does **not** require editing
`app/`: the scheduler tier supplies its own `BranchSelector` and merges `carry`
into the context mapping it passes. But it is designed in deliberately here
rather than discovered at airtime. Filed as issue #17.

### Go-live: pre-generate all 168 h first — and what that costs

**Decided by the user**, over the alternative of airing from a ~12 h lead
buffer while generating ahead of the playhead.

- **What it buys.** Total safety: the library is complete and reviewable
  before a frame airs, and the plan no longer depends on the concurrency
  multiplier clearing any threshold.
- **What it costs.** Lead time is maximised, and **drift waste scales with
  lead time** — every take is generated as far as possible ahead of the state
  it will air into. This is the maximum-invalidation choice.

**Consequence, not optional:** with lead time maxed, the state coverage above
and the patch tier stop being refinements and become the things carrying the
design. The `sensitivity_budget` guard and the neutral-take invariant are what
keep the patch tier inside its headroom. They are built in Layers 2 and 3 —
not deferred to the scheduler.

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

## Budget

Resolves issue #1 (blocking): the plan targeted ~1.5M words / 168 hours
without deriving the numbers in between. Derived here from the measured
baseline in `campaigns/ashiorid/generated/manifest.jsonl` (24 takes,
confirmed on disk): **105 words/take, 7.7 beats/take, 95.4 words/min of
hermes3:70b wall-clock generation throughput** (24 takes / 2,524 words in
~26 minutes).

### Total word / GPU-hour budget

The 1.5M-word target comes from filling 168 hours of *airtime* at a spoken
pace; the 95.4 words/min figure is *generation throughput* (GPU wall-clock).
These are different rates and both matter:

- **Airtime pace**: 1,500,000 words / 168 hours = **8,929 words/hour**
  (~149 words/min spoken pace — a plausible narrated-dialogue rate).
- **Generation cost, best case**: 1,500,000 words / 95.4 words/min =
  15,723 minutes = **~262 GPU-hours (~11 days continuous)**. This assumes
  every generated word ends up as usable airtime — i.e. `takes_per_slot: 3`
  chains into more airtime per slot.
- **Generation cost, worst case**: if `takes_per_slot: 3` instead means
  curation alternates (only one of three takes is kept — which is what
  `_generate_take`'s `improviser.recent = []` reset between takes currently
  does), only ~1/3 of generated words count toward airtime, so the real cost
  is **~786 GPU-hours (~33 days continuous)**.
**Issue #7 is resolved: `takes_per_slot` is a *choice pool*.** Neither of the
two readings above is taken. A slot is a recurring moment in a time-loop show;
its 3 takes are three *variants* of that moment, all of which are usable
inventory. Which one airs — or whether the story has branched far enough that a
fresh one is needed — is decided at airtime by the scheduler tier (see Runtime
tiers below), from current story state. Therefore:

- Nothing is discarded as curation waste, so **generated words == usable
  words** and the 3x ambiguity disappears. The 262-hour figure stands.
- Nothing chains between takes, so `_generate_take`'s `improviser.recent = []`
  reset is **correct as written**.
- Every take remains an independent unit of work, which is what makes the
  Layer 3 worker pool possible at all.

**The 262-hour figure is a *sequential* number.** Request concurrency — not
pipeline structure — is the dominant cost lever, and cuts it to an estimated
~52 GPU-hours. See Concurrency below.

### Total cost including branching

Generated inventory exceeds *aired* words once forks exist: only one side of a
fork airs per pass (it amortises across later loops — see Randomness above).
Conditioned takes add nothing, because they respend the existing pool.

| Component | Words | GPU-h sequential | GPU-h @ parallel-8 |
|---|---|---|---|
| Trunk (28 segments) | 1,500,000 | 262 | 52.0 |
| Fork variants (4 segments) | 214,400 | 37.5 | 7.5 |
| Event stingers (~40 x 3) | 12,600 | 2.2 | 0.4 |
| State-conditioned takes | 0 | 0 | 0 |
| **Total** | **~1,727,000** | **~302 (~12.6 days)** | **~60 (~2.5 days)** |

Branching costs **+14%** over the flat figure and buys 4 real narrative forks
plus state coverage of every slot the story can drift through. The parallel-8
column still rests on the unmeasured ~5x multiplier — benchmark it first.

### Efficiency levers, ranked

What actually moves the number, largest first:

1. **Request concurrency** — ~5x. Dominant, still unmeasured.
2. **`OLLAMA_CONTEXT_LENGTH` 64000 -> 8192** — not a lever on its own; it is
   what *permits* lever 1. At 64k the machine fits ~3 slots, not 8-12.
3. **Choice pool respent as state coverage** — **zero GPU cost**. Buys the
   entire micro-drift story for free. The best value in the plan.
4. **Forks amortised across loops** — turns the +14% from waste into inventory
   later loops consume.
5. **Stingers** — ~0.4 GPU-h for events being visible on screen.
6. **Drain in airtime order** — free; leaves a contiguous airable prefix at any
   stopping point.

Explicitly **not** levers, both already rejected: multiple model instances
(bandwidth-bound, strictly worse) and model tiering for bulk content (voice
consistency is worth more). The `llama3.1:8b` patch tier is not an exception —
it never writes bulk, only patches.

### Per-segment target (drives Layer 2)

At the configured `segment_hours: 6`:

- 8,929 words/hour x 6 hours = **~53,600 words of airtime per segment**.
- 53,600 words / 105 words/take = **~510 takes per segment**.
- 510 takes / `takes_per_slot: 3` = **~170 ambient slots per segment**.

Cross-check against the whole arc: `hours_total / segment_hours` = 168 / 6 =
28 segments x 53,600 words/segment = ~1,500,800 words, matching the 1.5M
target — the chain is internally consistent at this segment size.

These numbers (`target_words: 53600`, `target_slots: 170`) are now carried
in `generation.yaml` under `segment:` (see Config above) alongside a new
top-level `budget:` block recording the baseline and the derivation inputs,
so Layer 2 has a concrete count to hit instead of an open-ended "sequence of
beats," and so changing `segment_hours` or re-measuring the baseline
recomputes them instead of leaving stale numbers in prose.

170 slots/segment cannot be requested in a single ~4096-token call (~25
tokens/slot at that count) — **this is what issue #2 was blocking on, now
resolved by the 2a/2b chapter fan-out** (see Layer 2). `segment_hours: 6` is
kept as-is: the rest of the plan (arc segment count, spine-scene pacing) was
written around it, and the fan-out solves the token budget without disturbing
any of that.

- 170 slots / `chapters_per_segment: 9` = **~19 slots per 2b call**
- ~19 slots x ~60 tokens = **~1,140 tokens** — comfortable inside 4096

### Runtime budget guards

The static targets above are checked at run time so drift is caught during
a run rather than after an 11-day one:

- **Post-Layer 1 (arc continuity)**: sum of planned `segment.hours` must
  land within +/-5% of `hours_total`; segment count must land within +/-5%
  of `hours_total / segment_hours` (~28). Materially short -> re-plan the
  missing duration or fail with a `CRITICAL` log rather than silently
  shipping a short arc.
- **Post-Layer 1 (fork convergence)**: every fork satisfies all four rules of
  the convergence contract. A violation **fails the stage** — this is the one
  guard that is not a warning, because a broken fork is what turns a shallow
  DAG into an exponential tree.
- **Post-Layer 1 (event table)**: every drafted event is `scope: ambient`,
  names only declared state keys/values, and contradicts no spine scene.
  Then the human review gate — Layer 2 does not run until `events.yaml` has
  been read.
- **Post-Layer 2 (segment density)**: each segment's slots must sum to
  within ~80% of `segment.target_words`; short segments are flagged
  "Expansion Required" and re-briefed rather than passed to Layer 3 as-is.
- **Post-Layer 2 (sensitivity budget)**: the fraction of a segment's slots
  marked `sensitivity: flags` must not exceed `segment.sensitivity_budget`
  (0.40). Over budget means the 3-take pool cannot cover the segment's state
  space and the airtime patch tier will be over-subscribed — `WARNING` and
  re-brief. This is the guard that makes patch load a build-time number
  rather than an airtime surprise.
- **Layer 3 (word budget guard)**: cumulative words generated is tracked
  against the ~1.73M-word ceiling (1.5M trunk + forks + stingers, see the
  cost table above); approaching it emits a `WARNING` so a run can be stopped
  before overshooting.
- **Post-Layer 3 (neutral coverage)**: every ambient slot has exactly one
  take with `conditions: {}`. **Hard failure**, not a warning — a library
  without it can dead-air, which is the one outcome the whole design exists
  to prevent.
