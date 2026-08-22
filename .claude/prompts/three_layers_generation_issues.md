# 3LayersWeeklyGeneration — review backlog

Issues raised against [utilities/3LayersWeeklyGeneration/PLAN.md](../../utilities/3LayersWeeklyGeneration/PLAN.md)
from the 2026-08-17 plan review. One issue per finding; each is independently
actionable. Target tracker: Gitea (`gitea_admin/virtualTubers`).

Measured baseline referenced throughout, from `campaigns/ashiorid/generated/manifest.jsonl`
(24 takes): **105 words/take, 7.7 beats/take, 95.4 words/min**.

Issues **#2**, **#6** and **#7** were resolved in the 2026-08-18 planning
session (A) — the reasoning, the revised budget, and the sequenced build plan
live in [three_layers_design_decisions.md](three_layers_design_decisions.md).

Issues **#13-#17** were raised by the follow-up session (B) on efficiency,
random events and branching (D10-D18 in the same doc). They are obligations
the branching design creates, not defects in the original plan.

---

## 1. [blocking] [RESOLVED 2026-08-18] Plan has no budget arithmetic connecting segments to the 168-hour target

**Severity:** blocking — determines whether the whole shape is buildable.

**Resolution:** PLAN.md now has a single "Budget" section (previously
Hermes had produced this twice, verbatim-duplicated and with corrupted LaTeX
escaping in the first copy, and it didn't do the arithmetic anyway — that
attempt was reverted). It derives the full chain from the measured baseline:
262 GPU-hours (best case) / 786 GPU-hours (worst case, pending #7) to
generate 1.5M words; ~53,600 words / ~510 takes / ~170 slots per 6-hour
segment. `generation.yaml`'s spec gained a top-level `budget:` block (the
baseline + derivation inputs) and `segment.target_words` / `segment.target_slots`,
so Layer 2 has a concrete number to hit. Flags two explicit open
dependencies rather than silently resolving them: issue #7 (does
`takes_per_slot` mean airtime or alternates — changes the total by 3x) and
issue #2 (170 slots/segment needs Layer 2 batching to be requestable at all).

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

## 2. [blocking] [RESOLVED 2026-08-18] Layer 2 has no batching; one 6-hour segment cannot fit in a single 4096-token call

**Severity:** blocking.

**Resolution:** neither proposed fix taken as written. Blind batching gives the
model no structural anchor (it doesn't know what span a batch covers), and
shrinking `segment_hours` was rejected because the arc segment count and spine
pacing are written around 6 h. Instead Layer 2 **fans out into 2a -> 2b**:
2a emits ~9 chapter one-liners per segment (~40 min each) carrying their own
`continuity_in`/`continuity_out`; 2b emits ~19 slots per chapter. At ~60
tokens/slot that is ~1,140 tokens per call against ~10,200 for 170 slots in
one shot. Resumable per chapter.

The split has a second payoff: it is what makes Layer 2 **parallelisable**,
provided cross-segment continuity flows through `arc_plan.yaml` rather than
through a sibling segment's `brief.yaml` (see D6/D7 in the decisions doc).

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

## 5. [RESOLVED 2026-08-18] `target_minutes` is unreachable given max_beats x max_words

**Resolution:** the second option is taken — `target_minutes` is **dropped from
the brief schema**, and slot *count* carries duration (which is what #1's
arithmetic already assumes: ~170 slots for 6 hours). PLAN.md's Layer 2 slot
shape now reads `{slot_id, kind, prompt, lore, participants, sensitivity,
depends_on}`; the two new fields are session B's state-coverage labels (D14),
not a replacement for duration.

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

## 6. [RESOLVED 2026-08-18] `loop` and `carry` never reach Layer 3 — `_generate_take` overwrites them

**Resolution:** the proposed fix is adopted, and is now doubly required.
`LLMImproviser` holds mutable `scene`/`carry`/`loop`/`recent` state that
`update_context` mutates, so sharing one across the worker pool (D5/D8) would
cross-contaminate context between takes — Layer 3 needs one improviser per
worker regardless. That rules out reusing `_generate_take` verbatim anyway,
since it takes an improviser and mutates it. **One local ~10-line take
function resolves the loop/carry defect and thread-safety together**, keeping
`_write_take` / `_append_manifest` reuse intact.

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

## 7. [RESOLVED 2026-08-18] Unresolved: is `takes_per_slot` alternates or airtime? (3x cost swing)

**Resolution: neither — `takes_per_slot` is a *choice pool*.** A slot is a
recurring moment in a time-loop show; its 3 takes are three variants of that
moment, all of which are usable inventory. Which one airs — or whether the
story has branched far enough to need a fresh one — is decided at airtime by
the scheduler tier (D4), from current story state.

Consequences:
- Nothing is discarded as curation waste, so generated words == usable words
  and the 3x cost ambiguity disappears.
- Nothing chains, so `_generate_take`'s `improviser.recent = []` reset is
  **correct as written**.
- Every take stays an independent unit of work, which is what makes the Layer 3
  worker pool possible at all.

The larger finding from the same session: **request concurrency, not pipeline
structure, is the dominant cost lever.** Batching to one ollama instance at
parallel-8 (multiple instances are counterproductive — the GB10 is
memory-bandwidth-bound) is estimated to cut 262 GPU-hours to ~52. See D5 in the
decisions doc; the multiplier is not yet measured.

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

---

## 13. Nothing prevents a drafted event from contradicting spine canon

**Severity:** blocking for Layer 2 — a bad event table poisons every brief
generated against it.

`config/events.yaml` is **drafted by Layer 1** (hermes3:70b) and reviewed by
hand (D15). The spine is authored canon that this pipeline never regenerates —
[campaigns/ashiorid/scenes/](../../campaigns/ashiorid/scenes/) — so an event
that sets `moonwell-tainted: true` in a window where a scripted spine scene
narrates a clean moonwell produces content that contradicts what the show
already said out loud.

The model has no mechanism for noticing this, and the human gate is a person
reading ~40 entries against ten scene files.

**Fix:** `src/events.py` validates every drafted event before it is written —
`scope: ambient` on every entry (never `spine`), `requires:`/`sets:` naming
only keys and values from `generation.yaml`'s `state:` block, and no `sets:`
key asserting state that a spine scene in that event's `windows` contradicts.
Failures are surfaced in the file itself as comments so the human gate is
reading a pre-flagged table rather than proofreading from scratch.

**Done when:** an event asserting spine-contradicted state fails validation,
covered by a test against a fake pack.

---

## 14. Neutral-take coverage has no guard, and its absence is silent

**Severity:** blocking for the run — the failure only shows up on air.

D12 makes take 001 of every ambient slot **unconditioned**: the guarantee that
the picker always has something airable, whatever the live state and whatever
is down. D16's step 4 ("air the neutral take, never dead-air") depends on it
entirely.

Nothing enforces it. A slot whose take 001 failed generation, or whose
condition planning assigned a condition to all three takes, produces a library
that looks complete — right take count, right word count, manifest intact —
and dead-airs the first time that slot comes up in a state the pool does not
cover. Under D17's pre-generate-everything choice this is discovered days
after the run finished.

**Fix:** a hard post-Layer-3 guard: every ambient slot has exactly one take
with `conditions: {}`. **Fail, not warn** — this is the one outcome the whole
design exists to prevent. The same check belongs in the work-list pass so a
resumed run re-queues the missing neutral take rather than skipping the slot
as complete.

**Done when:** a fake output tree with a slot missing its neutral take fails
the guard, and the work-list re-queues that take on rescan.

---

## 15. Fork convergence is a four-rule contract with no validator

**Severity:** blocking for Layer 1 — a broken fork turns a shallow DAG into an
exponential tree, which is the exact cost the branching design exists to
avoid.

D11's contract: variants share one `merge_at`; variants write the same
`carry_out` key set from `state.carry_keys`; no post-merge segment conditions
on which variant ran; forks never nest.

Rule 3 is the one hermes3:70b will violate given the chance — writing "picking
up from Helen's decision at the ford" into a post-merge synopsis is exactly
what a coherence-seeking model does, and it silently re-couples the branches.

**Fix:** `src/forks.py` validates all four rules after Layer 1 writes
`arc_plan.yaml`, and rule 3 goes into Layer 1's prompt verbatim as well.
Unlike the other budget guards this one **fails the stage** rather than
warning, and it is never repaired silently.

**Done when:** each of the four rules has a failing fixture and a passing one,
and a violation aborts Layer 1 with a message naming the fork and the rule.

---

## 16. Patch-tier throughput is an unmeasured estimate that D17 leans on

**Severity:** should be measured before the full run, not after.

D16's picker patches a drifted slot with `llama3.1:8b` minutes before it airs.
Whether that is viable rests on a derived number, not a measurement:
~4.9 GB of weights against the GB10's ~273 GB/s, scaled by hermes3's measured
31% of theoretical, gives ~17 tok/s ~= **~780 words/min** — about 5x the
149 w/min consumption rate.

If the real figure is materially lower, the sustainable patch rate drops and
`segment.sensitivity_budget` (0.40) is set too high, which is only discoverable
on air. D17's pre-generate-everything choice maximises lead time and therefore
maximises drift, so this tier carries more load here than under the
lead-buffer alternative that was rejected.

**Fix:** benchmark `llama3.1:8b` single-stream against a *re-write* prompt
(existing take + drifted state -> revised take), in the same sitting as the
hermes3 concurrency benchmark. Write down the measured words/min and the
sustainable patched-slots-per-airtime-hour, and set `sensitivity_budget` from
it rather than from the 0.40 placeholder.

**Done when:** a measured patch-path words/min exists and
`segment.sensitivity_budget` is derived from it.

---

## 17. `carry` is structurally unreadable by the branch selector

**Severity:** blocking for the scheduler tier (not for Layers 1-3).

Verified in the code, not inferred:

- [app/campaign/runtime.py:111](../../app/campaign/runtime.py#L111) —
  `advance()` passes `self.state.context` to `graph.next_scene_id()`. `carry`
  is never passed.
- [app/campaign/scene_graph.py](../../app/campaign/scene_graph.py) —
  `_branch_matches` tests `branch.when` keys against that `context` dict only.
- [app/campaign/runtime.py:156](../../app/campaign/runtime.py#L156) —
  `reset()` sets `context = {}` while preserving `carry`.

So branch conditions can read only **loop-local** state. `carry` — the one
thing that survives a loop reset, and D11's entire fork-convergence channel —
is invisible to the selector. A fork that must be decided from `carry` cannot
be, as written.

This does **not** require editing anything under `app/` (this utility's
standing constraint): `BranchSelector` is already a pluggable seam, so the
scheduler supplies its own selector and merges `carry` into the context
mapping it passes. The point of filing it is that it must be designed in
deliberately rather than discovered at airtime.

**Fix:** the scheduler tier's `BranchSelector` merges `carry` into selector
context, with `context` winning on key collision (loop-local state is more
recent than carried state). Document the precedence rule where the selector
lives.

**Done when:** the scheduler's selector resolves a fork from a `carry`-only
condition, covered by a test.

---

## 18. [blocking-CI] `app/campaign/ambient.py` was never committed — the suite is red at HEAD

**Severity:** blocking for any "full regression green" gate; harmless at runtime
until the ambient scheduler is actually wired in.

**Found:** 2026-08-21, while establishing the verification baseline for the
PLAN_v3 build.

**Symptom:** `pytest` at repo root aborts during collection:

```
ERROR tests/test_campaign_ambient.py
E   ModuleNotFoundError: No module named 'campaign.ambient'
```

`tests/test_campaign_ambient.py` is tracked in git. `app/campaign/ambient.py`
is not, has never appeared in any commit (`git log --all -- app/campaign/ambient.py`
is empty), and has no staged copy under `.qwen_staging/`. A further **13
failures** in `tests/test_campaign_validator.py` come from the same gap — the
validator's ambient-related checks. Both were proven pre-existing by stashing
all PLAN_v3 changes and re-running.

This is the uncommitted Wave 4 that `PROJECT_CLAUDE.md` warns about
("Nothing in that module is committed yet").

**Why it matters:** every plan in this project uses "full suite green" as its
release gate. While this is broken that gate cannot be met, so a real
regression introduced later would be indistinguishable from the existing noise.

**Fix:** regenerate through the existing harness — the spec is already written:

```bash
.venv/bin/python tools/qwen_worker/runner.py --model qwen3.8:27b \
    run tools/qwen_worker/specs/campaign_ambient.yaml --attempts 3
# review .qwen_staging/campaign_ambient/, then:
.venv/bin/python tools/qwen_worker/runner.py --model qwen3.8:27b \
    promote tools/qwen_worker/specs/campaign_ambient.yaml
```

Then confirm the 13 `test_campaign_validator.py` failures clear with it.
Note `tools/qwen_worker/ollama_client.py` now sends `think: false`; that fix
postdates the original campaign-module build and makes this dispatch viable on
a qwen3-family model.

**Scoped out of the PLAN_v3 build** deliberately — it predates that work and
is unrelated to it.

---

## 19. [blocking-runtime] Ollama is bound to loopback, so the container cannot reach it

**Severity:** blocking for any real generation run through the service. The
unit suites and the API surface are unaffected.

**Found:** 2026-08-22, running the first real arc job through
`3layer-generator`.

**Symptom:** every LLM call from the container fails, and — because
`plan_arc`'s contract is to log and skip a batch it cannot plan — the job
still finishes `completed` with an empty `arc_plan.yaml`:

```
LLM call failed (attempt 2/2): Ollama request to
http://host.docker.internal:11434 failed: [Errno 111] Connection refused
Skipping batch for orders [24, 25, 26, 27] after 2 attempts
```

**Cause:** the host's systemd unit pins

```
OLLAMA_HOST=127.0.0.1:11434
```

so Ollama listens on loopback only. `extra_hosts: host.docker.internal:host-gateway`
(now in the compose entry) makes the NAME resolve, but nothing is listening on
that interface.

**Fix — operator decision, deliberately not made automatically.** Rebinding
Ollama exposes an unauthenticated GPU inference endpoint beyond loopback, so it
should be a conscious choice:

```bash
sudo systemctl edit ollama
# [Service]
# Environment="OLLAMA_HOST=0.0.0.0:11434"
sudo systemctl restart ollama
```

If the machine is not on a trusted network, prefer binding to the docker bridge
address only (`OLLAMA_HOST=172.17.0.1:11434`) rather than `0.0.0.0`.

Verify with:

```bash
docker compose exec 3layer-generator python -c \
  "import httpx; print(httpx.get('http://host.docker.internal:11434/api/tags').status_code)"
```

Related: issue #20 — this failure mode should not report `completed`.

---

## 20. [design] A run that plans nothing still reports `completed`

**Severity:** design gap, not a crash. It is what made issue #19 hard to see.

**Found:** 2026-08-22, same run.

**Symptom:** with Ollama unreachable, every batch was skipped, `arc_plan.yaml`
was written as `segments: []`, and the job row read
`status: completed, error: null`. The operator's dashboard shows a green run
that produced nothing.

**Why it happens, and why it is not simply a bug:** `plan_arc` is deliberately
tolerant — a single bad batch is logged and skipped so one stubborn batch
cannot cost the whole arc (the same "degrade, don't drop" principle as Layer
2's forced leaves). The runner faithfully reports that the layer function
returned without raising. Each piece is behaving as specified; the emergent
result is wrong.

**Suggested fix:** the runner should treat an empty or near-empty result as a
failure, not a success. Concretely, in `_run_arc`, when the mirrored arc plan
has zero segments, `finish(..., "failed", error="arc plan produced no
segments; check Ollama reachability")`. The same argument applies to a segment
job that plans zero slots and a dialogue job that writes zero takes — a job
whose whole output is empty is a failed job.

Worth a config knob (`min_result_fraction`) rather than a hard zero-check, so a
partially-successful run can still be flagged rather than silently accepted.

---

## 21. [hygiene] Generated artifacts on the bind mount are root-owned

**Severity:** minor, but it makes the operator's own files awkward to manage.

**Found:** 2026-08-22.

**Symptom:** the container runs as root by default, so everything it writes to
`./utilities/3LayersWeeklyGeneration/output/` is owned by `root:root` on the
host. Removing or editing a generated plan needs `sudo`, and a stale file
cannot be cleared by the user who owns the checkout.

**Fix:** add `user: "1000:1000"` to the `3layer-generator` compose entry (the
`secus` uid/gid on argyre), and make sure the output directory is writable by
that uid. Check whether anything in the image needs root first — nothing
obvious does; the service only reads its two ro mounts and writes the output
mount.
