# 3LayersWeeklyGeneration — review backlog

Issues raised against [utilities/3LayersWeeklyGeneration/PLAN.md](../../utilities/3LayersWeeklyGeneration/PLAN.md)
from the 2026-08-17 plan review. One issue per finding; each is independently
actionable. Target tracker: Gitea (`gitea_admin/virtualTubers`).

Measured baseline referenced throughout, from `campaigns/ashiorid/generated/manifest.jsonl`
(24 takes): **105 words/take, 7.7 beats/take, 95.4 words/min**.

---

## 1. [blocking] Plan has no budget arithmetic connecting segments to the 168-hour target

**Severity:** blocking — determines whether the whole shape is buildable.

The plan targets ~1.5M words / 168 hours but never derives the intermediate
quantities. Working it from the measured baseline:

- 95.4 words/min sustained -> 1.5M words = **262 GPU-hours (~11 days continuous)**.
- A 6-hour segment is ~50,000 words of speech -> **~480 takes** -> **~160 slots**
  at `takes_per_slot: 3`.

Neither number appears in the plan. 11 days of continuous hermes3:70b is a
materially different commitment from "a long run", and 160 slots/segment is what
breaks issue #2.

**Fix:** add a "Budget" section to PLAN.md fixing words-per-take, takes-per-slot,
slots-per-segment, segment length, and total GPU-hours. Re-derive `segment_hours`
and Layer 2's design from that number rather than from "~6 hours feels right".
Put the resulting per-segment slot/word target in `generation.yaml` so Layer 2 has
a concrete number to hit instead of an open-ended "sequence of beats".

**Done when:** PLAN.md states the full chain of quantities and the total GPU-hour
estimate, and `generation.yaml` carries the per-segment target.

---

## 2. [blocking] Layer 2 has no batching; one 6-hour segment cannot fit in a single 4096-token call

**Severity:** blocking.

`plan_segment.py` is specced to emit a segment's entire slot list in **one** LLM
call at `max_tokens: 4096`. Per issue #1 a 6-hour segment needs ~160 slot entries,
each carrying `slot_id`, `kind`, `prompt`, `lore`, `participants`, `target_minutes`.
That is a budget of ~25 tokens per entry. Not feasible.

Layer 1 already has `batch_size: 6` for exactly this reason; Layer 2 has no
equivalent.

**Fix (pick one):**
- Give Layer 2 the same batched + resumable treatment as Layer 1 (`batch_size`,
  write brief.yaml after every batch, resume from partial briefs), or
- Drop `segment_hours` to 30-60 min, which makes ~28 segments become ~200-340 and
  changes Layer 1's own cost accordingly.

Depends on the outcome of #1.

**Done when:** a single segment's brief can be generated to completion without
exceeding the configured `max_tokens`, and a crash mid-segment resumes without
re-planning the slots already written.

---

## 3. Slot-level `lore` is silently discarded for any invented stem

`LLMImproviser.generate_scene` resolves lore via `self.pack.lore.get(stem)` and
drops misses with **no warning** (app/campaign/improviser.py, ~L199-203).

`campaigns/ashiorid/lore/` contains exactly three stems:
`moonwells`, `the-begene-program`, `the-event`.

Layer 2 is asked to output a `lore` field per ambient slot. The model will invent
plausible stem names; every one of them vanishes silently. The plan validates
`scene_ref` against `pack.scenes` but applies no equivalent check to `lore`.

**Fix:** enumerate the valid stems in the Layer 2 prompt as a closed set, and
validate `lore` entries against `pack.lore` in the same validation pass that
checks `scene_ref` — rejecting/retrying the slot on a miss rather than writing a
brief whose lore is inert.

**Done when:** a brief containing an unknown lore stem fails validation, and a test
covers it.

---

## 4. `participants` is decorative — `generate_scene` always injects the full cast

`generate_scene` builds its cast roster by iterating **the entire pack cast**
(app/campaign/improviser.py, ~L181-186) and never reads a participants list.

So a Layer 2 slot scoped to "Drokki and Carl alone" will still be generated with
all five cast members offered to the model. The `participants` field in the brief
creates a false sense of control.

**Fix:** require the Layer 2 prompt instruction to bake participants into the
`prompt` string itself, and document `participants` in the brief as metadata for
downstream curation only — not something that constrains generation. (Alternative,
larger: a local `generate_scene` variant that filters the roster, which would mean
not reusing the primitive verbatim.)

**Done when:** PLAN.md states which fields actually reach the model, and the Layer 2
prompt template names participants inline.

---

## 5. `target_minutes` is unreachable given max_beats x max_words

The dialogue layer's caps (`max_beats: 12`, `max_words: 45`) bound a single take at
**540 words ~= 4 minutes of audio**. Observed output averages 105 words ~= 45
seconds.

A slot requesting `target_minutes: 15` therefore gets ~45 seconds. The field cannot
be honoured as specced.

**Fix (pick one):**
- Raise `max_beats` substantially for offline generation (it is already
  per-layer configurable, which is a good reason to), and re-measure; or
- Drop `target_minutes` from the brief schema and let slot *count* carry duration,
  which folds into #1's arithmetic.

**Done when:** either a take can plausibly reach its `target_minutes`, or the field
is gone from the schema.

---

## 6. `loop` and `carry` never reach Layer 3 — `_generate_take` overwrites them

Layer 1 is specced to track `loop`, incrementing each time a segment plays
`portal-encounter` (the spine's terminal scene, confirmed present at
`campaigns/ashiorid/scenes/10-portal-encounter.yaml`).

But `batch_generate._generate_take` calls:

    improviser.update_context(scene=scene, loop=take, carry={})

It overwrites `loop` with the **take number** and blanks `carry` unconditionally.
Importing `_generate_take` verbatim — which PLAN.md advertises as "zero
duplication" — is precisely what makes the arc's loop state unreachable by the
dialogue layer.

This is a direct conflict between the reuse goal and the plot-awareness goal, and
it matters because `loop`/`carry` are the only channel `generate_scene` has for
time-loop continuity.

**Fix:** add a thin local take-generation wrapper in
`src/generate_segment_dialogue.py` that sets context from the brief
(`loop=slot.loop`, `carry=...`) and calls `improviser.generate_scene` directly.
Costs ~8 duplicated lines; keeps `_write_take` / `_append_manifest` /
`_next_take_number` reuse intact.

**Done when:** a segment's `loop` value from `arc_plan.yaml` demonstrably reaches
the improviser context, covered by a test.

---

## 7. Unresolved: is `takes_per_slot` alternates or airtime? (3x cost swing)

`_generate_take` resets `improviser.recent = []` between takes, so takes 1/2/3 of a
slot are **three alternate versions of the same moment**, not three consecutive
chunks of content.

- If alternates (for curation): only ~1/3 counts toward 168 hours -> real cost
  ~**786 GPU-hours**.
- If airtime: the transcript reset is wrong and takes need to chain.

PLAN.md sets `takes_per_slot: 3` without saying which. This changes the total
project cost by 3x and should be decided before code is written.

**Fix:** decide, then either document takes as curation alternates (and multiply
#1's budget by `takes_per_slot`), or change the dialogue layer to carry transcript
between sequential takes.

**Done when:** PLAN.md states the semantics and #1's arithmetic reflects it.

---

## 8. Verification section's arc test-mode invocation is incoherent

PLAN.md's verification block contains:

    main.py --stage arc --test-mode --segments seg-001 -v

`--segments` names a segment id, but Layer 1 is the stage that **creates** segment
ids. There is nothing to pass at that point. Meanwhile `--test-mode` is specced to
*require* `--segments` to stay cheap.

**Fix:** define arc test-mode as "plan the first `batch_size` segments with each
configured profile" — cheap and comparable without needing an id — and correct the
verification block.

**Done when:** every command in PLAN.md's Verification section is runnable as
written.

---

## 9. Test location contradicts the plan's own self-contained goal

New tests are specced into the repo-root `tests/` (which `pytest.ini` scopes via
`testpaths = tests`), while everything else about the utility is deliberately
self-contained under `utilities/3LayersWeeklyGeneration/`.

That makes the root suite depend on the utility's `sys.path` insertion hack, and
splits the utility across two trees.

**Fix:** put tests in `utilities/3LayersWeeklyGeneration/tests/` with their own
`conftest.py` (mirroring `tests/conftest.py`'s pattern), and add that path to
`testpaths` in `pytest.ini`.

**Done when:** a bare `pytest` at repo root still collects and passes the new
tests, and the utility directory is self-contained.

---

## 10. Document that `--model-profile` with `--stage all` requires the name in every layer

`--model-profile NAME` applies to whichever stages the invocation runs. With
`--stage all`, a single `--model-profile light` is applied to all three layers, so
a profile called `light` must exist under `arc.models`, `segment.models`, **and**
`dialogue.models` or the run fails partway.

Behaviour is defensible; it is just undocumented and will surprise.

**Fix:** document it in the README, and have `src/config.py` validate the profile
name against every stage the invocation will run **up front**, failing with a clear
message before any GPU time is spent rather than mid-run.

**Done when:** an invalid profile for `--stage all` fails immediately with a
message naming the missing layer.

---

## 11. Plan omits CLAUDE.md-required logging, docs, and Loki conventions

PLAN.md covers a README but not three standing project conventions:

- **Per-function docs**: CLAUDE.md requires a markdown doc file in `docs/` per new
  function/class (Overview, Signature, Parameters, Return Value, Dependencies,
  Usage Examples, Error Handling, Changelog).
- **Log levels**: TRACE on function entry/exit, DEBUG at branches and around
  external calls, INFO on major completions, ERROR inside every handler. Relevant
  here because each layer is a long unattended run where log quality *is* the
  observability story.
- **Loki push**: structured push after major operations via
  `projectManager/src/loki_push.py`, labels `app` / `operation`.

**Fix:** add these to PLAN.md's deliverables so they are not retrofitted later.

**Done when:** PLAN.md lists the doc files to be produced and the logging/Loki
obligations per layer.

---

## 12. `generation.yaml` repeats `base_url` six times

Every one of the six model profiles restates `provider`, `base_url`, and often
identical `temperature` / `max_tokens`. Changing the Ollama host means six edits
and any missed one fails at runtime, not at load.

**Fix:** add a `defaults:` block merged into each profile at load time in
`src/config.py`, so a profile only states what it overrides (typically just
`model`).

**Done when:** a profile can be a single `model:` line and `src/config.py` fills
the rest, with a test covering the merge.
