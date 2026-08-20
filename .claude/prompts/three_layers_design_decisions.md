# 3LayersWeeklyGeneration — design decisions & next steps

Record of the 2026-08-18 planning session against
[utilities/3LayersWeeklyGeneration/PLAN.md](../../utilities/3LayersWeeklyGeneration/PLAN.md)
and the review backlog in [three_layers_generation_issues.md](three_layers_generation_issues.md).

Two sessions, both on 2026-08-18.

**Session A (D1-D9)** opened to resolve issues **#7** (takes semantics, 3x cost
swing) and **#2** (Layer 2 batching). Both resolved. It also turned up a larger
finding — **request concurrency**, not pipeline structure, is the dominant cost
lever — which changes the budget by ~5x and reshapes what Layers 2 and 3 have
to look like.

**Session B (D10-D18)** closed the last open design area: **efficiency,
random events, and story branching**. Its through-line is that branching is
affordable only if it is paid for in *state* rather than in *script* — see
D10's micro-drift vs macro-fork split, and D12, which buys the entire
random-event story for zero additional GPU time.

Measured baseline throughout, from `campaigns/ashiorid/generated/manifest.jsonl`
(24 takes): **105 words/take, 7.7 beats/take, 95.4 words/min single-stream**.

---

## Decisions made

### D1. Generation is text-only and offline; TTS is a separate, real-time stage

**Decided by the user.** Layers 1-3 produce and validate *script* only. Voice
is applied downstream.

Critically, TTS should be **real-time at airtime**, not a batch pre-render:

- Piper medium voices run at a real-time factor of ~0.1-0.25 on CPU. A ~5 s
  beat synthesizes in ~0.6-1.4 s — 4-10x headroom.
- The campaign renderer is sequential (one beat at a time), so with one-beat
  lookahead there is at most **one synthesis in flight**. **One CPU core**
  carries a stream; two is comfortable. This machine has 20.
- RAM: ~60 MB per loaded voice model (`ryan-high` is 115 MB), cached by
  `_load_local_voice`. A five-member cast is ~300 MB resident.
- Bandwidth, only if synthesis is remote via
  [`_piper_remote`](../../app/tts_client.py) (local synthesis costs zero):

  | Voice tier | Sample rate | Sustained | Over 168 h |
  |---|---|---|---|
  | `-medium` | 22050 Hz | **44 KB/s ~= 353 kbit/s** | ~27 GB |
  | `-low` | 16000 Hz | **32 KB/s ~= 256 kbit/s** | ~19 GB |

  Request side is the beat's text, ~80 bytes. On a LAN this is ~0.035 % of a
  gigabit link.
- Real-time synthesis also means 168 h of audio is never stored.

**Consequence:** because the show ultimately airs *voiced*, the words-to-airtime
conversion is the **spoken** rate (~149 wpm), so PLAN.md's original
**1.5M words for 168 hours is correct** and stands.

> An earlier reading of this session measured the *unvoiced* path — the
> renderer types at `DIALOGUE_CPS = 45` (narration 90) plus `EVENT_PAUSE_S = 0.8`
> per beat ([app/replay.py](../../app/replay.py)), i.e. ~370 wpm effective, which
> would need ~3.7M words. That applies only if any of the 168 h airs without
> voice. It does not apply to the planned pipeline.

**Deferred defect (downstream, not this utility):**
[app/campaign/renderer.py](../../app/campaign/renderer.py) synthesizes and plays
sequentially per beat, and has no audio-sync `scale` factor (unlike
[app/replay.py](../../app/replay.py)'s `Pacer`). Two consequences for whoever
builds the TTS stage: a ~1 s dead hitch before every beat, and typed text
drifting out of sync with audio. Fix is one-beat lookahead in a worker thread.

### D2. hermes3:70b for all three layers — no model tiering

**Decided by the user.** Consistent voice across all 168 hours is worth more
than the ~4x speedup tiering would have bought. The `light` profiles stay in
the config for `--test-mode` comparison, but `active_model: heavy` everywhere.

This locks bulk generation at 95.4 words/min single-stream, which is what makes
D5 (concurrency) the only remaining cost lever.

### D3. Issue #7 RESOLVED — `takes_per_slot` is a **choice pool**

Neither "curation alternates" (discard 2 of 3, 3x waste) nor "chained airtime"
(which duplicates what slots already do, and would require breaking
`_generate_take`'s `recent = []` reset).

A slot is a *recurring moment* in a time-loop show. Its 3 takes are three
variants of that moment. **All three are usable inventory**; which one airs —
or whether a fresh one is needed — is decided at airtime by the scheduler
(D4), based on current story state.

- Nothing is discarded as curation waste.
- Nothing chains, so `_generate_take`'s `improviser.recent = []` reset is
  **correct as written**.
- Every take stays an independent unit of work, which is what makes D5's
  concurrency possible at all.
- The 3x cost ambiguity disappears: generated words == usable words.

### D4. A late-binding runtime tier, because scripts drift before airing

**Raised by the user:** scenes may shift shortly before airing due to random
factors, and the story may branch, so mapping the entire voiceline ahead of
time is not sensible.

This is correct, and it exposes an arithmetic wall: **hermes3:70b cannot
generate just-in-time.**

- Production: **95.4 words/min**
- Airtime consumption: **149 words/min**
- Ratio 0.64 -> a buffer drains at ~54 words/min, i.e. you fall **~21 minutes
  of airtime behind for every hour on air**.

Breaking even needs >=149 w/min; branch regeneration needs 2-3x that for
headroom. So late-binding cannot come from the bulk model. Three runtime tiers:

| Tier | Model | When | Job |
|---|---|---|---|
| Bulk library | hermes3:70b | offline, resumable | Layers 1-3 — the ~1.5M-word inventory |
| Late-binding patch | `llama3.1:8b` | minutes before a slot airs | regenerate only slots whose state has drifted |
| Voice | Piper | at the beat | real-time, one-beat lookahead |

Note this tiering is **not** D2's rejected tiering: the fast model never writes
bulk content, only patches. Bulk voice consistency is preserved.

Consequence: the scheduler is a **just-in-time picker**, not a precomputed
168-hour playlist. It decides at airtime whether to air a cached take from the
slot's choice pool or request a fresh one. Repetition therefore becomes a
runtime policy knob (`min_hours_before_repeat`) rather than a design commitment.

### D5. Concurrency is the dominant cost lever — batch requests, don't run multiple instances

**Hardware:** NVIDIA **GB10** (DGX Spark class), 121 GB unified memory, 20
cores, ~273 GB/s memory bandwidth.

**Multiple hermes3:70b instances: no.** Two instances means two 40 GB weight
copies (80 GB of 121 GB, leaving nothing for KV cache) *and* both contend for
the same ~273 GB/s bus. Decoding is memory-bandwidth-bound — every token
streams the full 40 GB of weights. Two instances read 80 GB per token-pair
instead of 40 GB per token: same aggregate throughput at best, each instance at
half speed, near-OOM.

The bandwidth ceiling also explains the baseline: 273 GB/s / 40 GB ~= **6.8
tok/s theoretical max**, against ~2.1 tok/s measured (95.4 w/min). The model is
slow because it is large on a narrow bus, not because it is under-parallelised.

**Concurrent requests to one instance: yes.** The same physics that kills
multi-instance is what makes batching win — read the 40 GB once, decode a token
for N sequences simultaneously. Aggregate throughput scales near-linearly until
compute-bound, and on a bandwidth-starved machine that is a long runway. The
workload is ideal: ~14,300 takes, every one independent.

Two things currently block it:

1. **`OLLAMA_NUM_PARALLEL` is unset** -> single-stream. This is the entire
   reason the baseline is 95.4 w/min.
2. **`OLLAMA_CONTEXT_LENGTH=64000`** — KV cache is allocated *per parallel
   slot*. For this architecture (80 layers, 8 GQA KV heads, 128 head-dim, fp16)
   that is ~320 KB/token:

   | Context | KV per slot | 8 slots + 40 GB weights |
   |---|---|---|
   | 64,000 (current) | ~20.5 GB | **~204 GB — does not fit** |
   | 8,192 | ~2.7 GB | ~61 GB — fits comfortably |

   Layer 3 prompts are ~500 tokens with `max_tokens: 1024`. 64k is ~30x the
   workload and is what caps the machine at ~3 slots instead of 8-12.

**Estimated gain: 4-6x aggregate** at parallel-8 (not the full 8x — KV traffic
and prefill eat into it). **Not yet measured** — see N1.

### D6. Issue #2 RESOLVED — chapter tier under Layer 2, not blind batching

Neither of issue #2's two proposed fixes is taken as written.

- *Blind batching* (`batch 1 = slots 1-20`) gives the model no structural
  anchor — it does not know what span it is covering.
- *Shrinking `segment_hours`* was rejected: the rest of the plan (arc segment
  count, spine pacing) is written around 6 h.

Instead, **fan Layer 2 into 2a -> 2b**:

- **2a (chapters):** one call per segment emits ~9 chapter one-liners (~40 min
  each) with their own `continuity_in`/`continuity_out`.
- **2b (slots):** one call per chapter emits ~19 slots. At ~60 tokens/slot that
  is ~1,140 tokens — comfortable inside 4096, versus ~10,200 for 170 slots.

Resumable per chapter. This also has a second, larger payoff — see D7.

### D7. Parallelism preconditions on Layers 1 and 2

| Layer | Calls | Parallel? |
|---|---|---|
| L1 arc | ~5 | **No** — batch N needs batch N-1's `continuity_out`. Serial by nature, ~20 min total. Leave it. |
| L2a/L2b | ~28 + ~250 | **Yes, conditionally** |
| L3 dialogue | ~14,300 | **Yes** — fully independent, ~99 % of GPU time |

**The L2 precondition, which must be locked in now:** cross-segment continuity
must flow through `arc_plan.yaml` (already written by L1), **never through a
sibling segment's `brief.yaml`**. If segment 7's brief depends on segment 6's
*brief*, L2 becomes a 28-deep serial chain. If it depends only on segment 6's
`continuity_out` *as recorded in the arc plan*, all 28 run concurrently. The
same applies within a segment: 2a's chapter list must carry enough continuity
that every 2b call is independent.

This makes the D6 split load-bearing for two reasons, not just token budget.

### D8. Concurrency implementation requirements

**Plan-then-drain, not lazy allocation.** `_next_take_number`
([app/campaign/batch_generate.py](../../app/campaign/batch_generate.py))
globs the scene directory to pick the next take number; under concurrency two
workers both see `002.yaml` and both claim take 3. Replace with a **work-list
pass before any GPU time**: scan the output tree, compute every
`(segment, slot, take)` not yet present, assign deterministic output paths up
front, emit an ordered list, then drain it with a fixed pool. Gains:
race-freedom, idempotent resume (re-running the scan *is* the resume), and an
exact work count and ETA before starting a multi-day run.

**One improviser per worker; one shared LLM client.** Verified in this session:

- `OllamaClient` **is** thread-safe — [app/llm_client.py](../../app/llm_client.py)
  uses module-level `httpx.post` with no shared session, and every instance
  attribute is read-only after `__init__`. Share one across all workers.
- `LLMImproviser` **is not** — [app/campaign/improviser.py](../../app/campaign/improviser.py)
  holds mutable `scene`, `carry`, `loop`, `recent`, and `update_context`
  mutates them. Construct one per worker thread, sharing the read-only `pack`
  and the single client.

This rules out verbatim reuse of `_generate_take` (it takes an improviser and
mutates it) — which is the *same* ~10 lines issue **#6** already requires
locally, to stop `loop=take` clobbering the arc's loop value. **One local take
function resolves #6 and thread-safety together.**

**Single writer.** Workers return results; one consumer thread performs every
file write and `_append_manifest`. No locks, no interleaved JSONL, clean crash
semantics.

**Timeout wrapper — will otherwise bite silently.**
`app/llm_client.py` hardcodes `timeout=120`. Batching raises throughput *by
raising per-request latency*; at parallel-8 a request may take 3-5x longer than
the measured 65 s and cross 120 s. The failure is silent —
`generate_scene` catches the exception and returns `[]`, indistinguishable from
"the model wrote nothing" — so a run would burn hours writing empty files.

Per CLAUDE.md's shared-utilities rule (wrap, don't patch), add
`src/concurrent_llm.py`: a ~15-line subclass of `OllamaClient` overriding
`complete()` with a shared `httpx.Client` (connection pooling across 14,300
calls instead of a fresh TCP handshake each time) and a configurable timeout
(600 s). `build_llm_client` still parses config; only the transport swaps.
**No file under `app/` is modified.**

**Circuit breaker — non-negotiable for an unattended run.** Because
`generate_scene` never raises, a dead ollama, an OOM, or a wrong model name
yields an infinite stream of empty takes. Add a rolling failure-rate check: if
the last N takes exceed a failure threshold, abort with `CRITICAL`. The
difference between losing ten minutes and losing two days.

**Drain in arc order, not shuffled.** Stopping the run at any point should
leave a *contiguous usable prefix* of airtime, not a scattered fraction that
cannot air. An ordered queue keeps workers near the frontier at no throughput
cost.

**Pool size == `OLLAMA_NUM_PARALLEL`.** Oversubscribing just queues inside
ollama, where visibility is lost and timeouts are hit.

### D9. Config additions

`num_ctx` and `timeout_s` belong in the per-layer model profiles (alongside
`temperature`/`max_tokens`); `concurrency` is per-layer. Context length and
concurrency trade directly against each other, and the layers want opposite
ends — which is fine, because they run as separate stage invocations:

| Layer | Prompt size | `num_ctx` | `concurrency` |
|---|---|---|---|
| L1 / L2 | large (lore, spine narration, chapter lists) | 16-32k | 1-4 |
| L3 | ~500 tokens | **8192** | **8-12** |

```yaml
dialogue:
  models:
    heavy:
      model: "hermes3:70b"
      temperature: 0.9
      max_tokens: 1024
      num_ctx: 8192          # drives per-slot KV cache -> how many slots fit
      timeout_s: 600         # must exceed batched per-request latency
  concurrency: 8             # must match the server's OLLAMA_NUM_PARALLEL
```

Server side, for the generation run:

```bash
OLLAMA_CONTEXT_LENGTH=8192
OLLAMA_NUM_PARALLEL=8
```

This also folds in issue **#12** (`base_url` repeated six times): a `defaults:`
block merged into each profile at load time keeps the new keys from being
restated six more times.

---

## Randomness & branching — the 2026-08-18b session

Four questions were put to the user; all four are decided. The through-line:
**branching is affordable only if it is paid for in state, not in script.**

### D10. Two kinds of divergence, and only one of them costs GPU time

The word "branching" was covering two things with wildly different costs.
They are separated here and named for the rest of the plan:

| | **Micro-drift** | **Macro-fork** |
|---|---|---|
| What changes | what is *said* in a slot | *which segment* comes next |
| Frequency | continuous, ~all 168 h | 4 declared points |
| Mechanism | conditioned takes + JIT patch | `arc_plan.yaml` DAG edge |
| Marginal GPU cost | **zero** (see D12) | ~1.86 GPU-h per variant |
| Reconverges | n/a (never diverged) | at the next loop reset |

Everything that felt like "the story branches" in the original framing is
micro-drift, and micro-drift is free. Macro-forks are the small, budgeted
exception.

### D11. Macro-forks sit on loop boundaries and re-converge through `carry`

**Decided by the user:** ~4 fork points, 2 variants each.

The time loop is the branch-collapse mechanism, and it is already in the
runtime — [app/campaign/runtime.py](../../app/campaign/runtime.py)'s
`reset(keep_carry=True)` clears `context` and `history` but preserves `carry`.
So a fork opened inside a loop is closed by the next `portal-encounter`, and
`carry` is the only channel through which it may leave a mark.

This is what stops branching being 2^n. `arc_plan.yaml` becomes a **shallow
DAG, never a tree**:

```
seg-06 ──┬── seg-07a ──┐
         └── seg-07b ──┴── seg-08   (merged carry)
```

**The convergence contract (hard, validated post-L1):**

1. Every variant of a fork names the same `merge_at` segment.
2. Every variant writes the **same set** of `carry_out` keys — differing in
   values only, and only values from the declared closed vocabulary (D13).
3. No segment downstream of `merge_at` may condition on *which* variant ran,
   only on the merged `carry`.
4. Forks never nest. A fork must merge before the next fork opens.

Break any of these and the segment count explodes; rule 3 is the one a model
will violate given the chance, so it goes in Layer 1's prompt verbatim and is
checked by a validator, not trusted.

**The +14% is not waste.** 4 extra segments = ~214,000 words = ~7.4 GPU-h at
parallel-8. Only one side of each fork airs per pass — but the 168 h contains
multiple loops, and a fork point recurring on a later loop airs the *other*
variant. Fork variants are inventory that amortises across loops, not
discarded alternates. (Same reasoning as D3 applied one tier up.)

### D12. Repurpose the choice pool as **state coverage** — free branching

The single best efficiency move available, and it costs nothing.

`takes_per_slot: 3` (D3) currently buys three interchangeable variants of a
moment — pure repetition-avoidance. Respend the same three takes across
*state space* instead of across style:

- **Take 001 is always neutral** — generated with no state dependency, airs
  under any state whatsoever.
- **Takes 002/003 are conditioned** — each generated under one declared
  precondition, drawn from that slot's `depends_on` (D14).

Same 3 takes, same ~1.5M words, same ~52 GPU-h. The pool now spans the states
the story can actually be in, so a drifted state usually finds a cached take
instead of falling through to the patch tier.

For slots Layer 2 marks `sensitivity: none`, all three takes stay plain
variants. Nothing is wasted either way.

**Take 001's neutrality is a load-bearing invariant, not a nicety.** It is the
guarantee that the show can never dead-air: whatever the live state, whatever
has drifted, whatever is down, there is always something airable. Enforced by a
post-L3 guard (issue #14), not by convention.

### D13. A closed, typed state vocabulary — the same discipline issue #3 wants for lore

Open-ended state cannot be pre-generated against, because coverage has no
denominator. So the vocabulary is closed and declared in `generation.yaml`:

```yaml
state:
  flags:      [helen-wounded, moonwell-tainted, buffalo-lost-axe, ...]  # booleans
  moods:      [tense, weary, hopeful, giddy]                            # coarse tone dial
  carry_keys: [helen-wounded, moonwell-tainted]                         # survive a loop reset
```

Every `depends_on`, every event's `sets:`/`requires:`, and every fork's
`carry_out` may name **only** these. Anything else fails validation rather
than silently evaporating — which is exactly the failure mode issue #3
documents for lore stems (`pack.lore.get(stem)` drops misses with no warning).
**Same validation pass covers both**; write it once.

`carry_keys` is a strict subset of `flags`: it is the answer to "what does the
next loop remember". Keeping it small is what keeps the arc's state space
enumerable.

### D14. Layer 2 labels each slot's state sensitivity

Layer 2 already decides what happens in a slot; it is the only layer that
knows whether that slot *cares* about state. So each slot in `brief.yaml`
gains:

```yaml
- slot_id: ch03-s07
  kind: ambient
  sensitivity: flags        # none | tone | flags
  depends_on: [helen-wounded]   # closed vocabulary (D13), validated
```

- `none` — campfire small talk, craft nerdery, road banter. Airs identically
  whatever has happened. Expected to be the **large majority**.
- `tone` — needs only the coarse `mood` dial, not specific facts.
- `flags` — references specific state; names which keys in `depends_on`.

This label is what tells Layer 3 how to spend takes 002/003 (D12), and it is
what makes airtime patch load *predictable at build time* rather than
discovered on air. Hence the guard: if more than
`segment.sensitivity_budget` (default 0.40) of a segment's slots are
`flags`-sensitive, the 3-take pool cannot cover them and the patch tier will
be over-subscribed — warn at build time (issue #13's sibling), because that is
hours before it becomes dead air.

### D15. Random events: a closed table, rolled at chapter boundaries, seeded and ledgered

**Decided by the user:** seeded weighted RNG from a closed table; the table is
**drafted by Layer 1 and reviewed by hand** before Layer 2 runs.

`config/events.yaml`, one entry per event:

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
segment. This falls out of D6's chapter tier at no extra cost: ~252 rolls
across 168 h, a natural dramatic cadence, and it means the JIT picker knows
the chapter's state before it needs to pick any of that chapter's ~19 slots.

**`scope: ambient` is absolute.** An event may never assert state that a
scripted spine scene contradicts — the spine is authored canon and this
pipeline's whole reuse story depends on not touching it. Because Layer 1
drafts the table, this needs a validator and a human gate, not trust (issue
#13).

**Determinism.** The seed is derived from `(run_id, chapter_id)`, so a replay
reproduces the same rolls exactly. Every roll appends to
`output/<pack>/ledger.jsonl` — event fired, state before/after, take chosen,
patched or not. This is both the audit trail for `carry` and what makes a show
reproducible, which [app/campaign/primitives.py](../../app/campaign/primitives.py)
already demands of the render tier ("rendering the same primitive with the
same arguments must produce byte-identical text forever").

**Stingers.** Each event with `stinger: true` gets a small pre-generated pool
of interrupt beats announcing it on screen, so an event is *seen*, not just
silently true in a dict. 40 events x 3 takes x 105 words = ~12,600 words =
**~0.4 GPU-h at parallel-8** — effectively free. One small extra Layer 3 pass.

### D16. Three clocks — and the renderer's is not one of them

Randomness enters at exactly one of them. Stating this explicitly because
conflating them is how the replay guarantee gets broken:

| Clock | When | What is decided |
|---|---|---|
| **Build time** | offline, this utility | L1 declares fork points + event windows; L2 labels slot sensitivity; L3 writes neutral + conditioned takes + stingers. **No rolls.** |
| **Airtime − minutes** | the JIT picker (D4) | Roll events at the chapter boundary; resolve forks at the loop boundary; pick a take; patch on a miss. **All rolls happen here.** |
| **At the beat** | the renderer | **Nothing random.** Byte-identical forever, per `primitives.py`. |

The picker's decision rule, in order:

1. Compute live state from `carry` + `context` + hot events.
2. Choose the most *specific* take whose `conditions` the live state satisfies.
3. If only the neutral take matches but the slot is `flags`-sensitive and hot
   — patch it with `llama3.1:8b` (D4's tier).
4. If the patch tier is unavailable or out of time — **air the neutral take**.
   Never dead-air.

Steps 3-4 are why D12's neutral invariant is load-bearing.

### D17. Go-live: pre-generate all 168 h first — and what that costs

**Decided by the user.** The alternative (air from a ~12 h lead buffer while
generating ahead of the playhead) was rejected in favour of a complete library
before airing.

Stated honestly, because it is the one decision here with a real downside:

- **What it buys.** Total safety. The library is complete and reviewable
  before a single frame airs, and the plan no longer depends on the
  concurrency multiplier clearing any threshold — N1 (below) drops from an
  *architecture gate* to a pure cost measurement.
- **What it costs.** Lead time is maximised, and **drift waste scales with
  lead time**. Every take is generated as far as possible in advance of the
  state it will air into, so the state it was conditioned on is as stale as it
  can be. This is the maximum-invalidation choice.

**Consequence, and it is not optional:** with lead time maxed, D12's state
coverage and D4's patch tier stop being refinements and become the things
carrying the design. The `sensitivity_budget` guard (D14) and the neutral-take
invariant (D12) are what keep the patch tier inside its headroom. Build them
in Layers 2 and 3; do not defer them to the scheduler.

### D18. `carry` is invisible to the branch selector — a real blocker for D11

Found while verifying D11 against the code, not previously filed.

- [runtime.py:111](../../app/campaign/runtime.py#L111) — `advance()` passes
  `self.state.context` to `graph.next_scene_id()`. `carry` is never passed.
- [scene_graph.py](../../app/campaign/scene_graph.py) — `_branch_matches`
  tests `branch.when` keys against that `context` dict only.
- [runtime.py:156](../../app/campaign/runtime.py#L156) — `reset()` sets
  `context = {}` while preserving `carry`.

So branch conditions can read **only loop-local state**, and the one thing
that survives a loop reset — `carry`, D11's entire convergence channel — is
structurally unreadable by the selector. A fork that must be decided from
`carry` cannot be, as written.

This does not change D11's shape, and it does **not** require editing
`app/` (this utility's standing constraint): the scheduler tier supplies its
own `BranchSelector` and can merge `carry` into the context mapping it passes.
But it must be designed in deliberately rather than discovered at airtime.
Filed as issue #17.

---

## Revised budget

Airtime target unchanged at 168 h / ~1.5M words of *aired* content (D1).
Generated inventory now exceeds aired words, because fork variants and
conditioned takes are coverage rather than airtime (D11, D12).

| Stage | Quantity |
|---|---|
| Aired words | 1,500,000 |
| Takes @ 105 words | ~14,300 |
| Trunk segments @ 6 h | 28 |
| Fork variant segments (4 forks x 1 extra) | 4 |
| Takes/segment | ~510 |
| Slots/segment @ `takes_per_slot: 3` | ~170 |
| Chapters/segment @ ~40 min | ~9 |
| Slots/chapter | ~19 |
| Event-table entries (drafted L1, hand-reviewed) | ~40 |
| Event roll points (chapter boundaries over 168 h) | ~252 |

### Generation cost

| Component | Words | GPU-h sequential | GPU-h @ parallel-8 |
|---|---|---|---|
| Trunk (28 segments) | 1,500,000 | 262 | 52.0 |
| Fork variants (4 segments) | 214,400 | 37.5 | 7.5 |
| Event stingers (40 x 3) | 12,600 | 2.2 | 0.4 |
| **Total** | **~1,727,000** | **~302 (~12.6 days)** | **~60 (~2.5 days)** |

Branching adds **+14%** over the D5 figure and buys 4 real narrative forks
plus full state coverage of every slot the story can drift through. The
parallel-8 column still rests on the unmeasured 5x multiplier (O2 / N1).

### Efficiency levers, ranked

What actually moves the number, largest first:

1. **Request concurrency (D5)** — ~5x. Dominant, and still unmeasured (N1).
2. **`OLLAMA_CONTEXT_LENGTH` 64000 -> 8192** — not a lever on its own; it is
   what *permits* lever 1. At 64k the machine fits ~3 slots, not 8-12.
3. **Choice pool respent as state coverage (D12)** — **zero GPU cost**. Buys
   the entire micro-drift story for free. The best value in the plan.
4. **Forks amortised across loops (D11)** — turns the +14% from waste into
   inventory later loops consume.
5. **Stingers (D15)** — ~0.4 GPU-h for events being visible on screen.
6. **Drain in airtime order (D8)** — free; leaves a contiguous airable prefix
   at any stopping point.

Explicitly **not** levers, and both already rejected: multiple model instances
(D5 — bandwidth-bound, strictly worse) and model tiering for bulk content
(D2 — voice consistency is worth more). The `llama3.1:8b` patch tier is not an
exception to D2: it never writes bulk, only patches (D4).

---

## Still open

- **O1 — reuse policy.** Whether a generated take may air more than once
  across the 168 h. Now partly answered by D12: takes are differentiated by
  *state condition*, so "repeat" means "same slot, same condition, twice".
  `min_hours_before_repeat` remains a runtime knob. Decide before the
  scheduler tier (N7), not before Layers 1-3.
- **O2 — the 4-6x concurrency multiplier is an estimate.** See N1. D17 demotes
  it from an architecture gate to a cost measurement, but it still sets the
  whole budget.
- **O3 — issues #3, #4, #5, #8, #9, #10, #11, #12 are untouched by the first
  session** and remain as filed. **#13-#17 are new**, from this session.
- **O4 — renderer TTS defects** (no one-beat lookahead, no audio-sync
  `scale`). Downstream; tracked so it is not lost.
- **O5 — patch-tier throughput is an estimate.** `llama3.1:8b` at ~4.9 GB
  against the GB10's ~273 GB/s, scaled by hermes3's measured 31% of
  theoretical, gives ~17 tok/s ~= **~780 words/min** — ~5x the 149 w/min
  consumption rate. That headroom is what makes D16's step-3 patching viable
  at all, and it has never been measured. Benchmark alongside N1 (issue #16).
- **O6 — how many loops fit in 168 h?** D11's "fork variants amortise across
  loops" holds only if fork points actually recur. Falls out of Layer 1's arc
  plan; check it when reading `arc_plan.yaml` at N4 rather than assuming it.

---

## Next steps

Ordered. Steps 1-2 are cheap and de-risk everything after them.

### N1. Benchmark concurrency (~20 min GPU) — do this first

Drive hermes3:70b at parallel 1 / 4 / 8 / 12 with the *real* Layer 3 prompt
shape (~500-token prompt, `num_predict: 1024`, `num_ctx: 8192`) and record
**aggregate** words/min, not per-request latency. Pick the knee of the curve.

**Done when:** a throughput-vs-parallelism table exists and
`dialogue.concurrency` / `num_ctx` are set from it.

### N1b. Benchmark the patch tier (~10 min GPU) — same sitting

`llama3.1:8b`, single-stream, against a *re-write* prompt (existing take +
drifted state -> revised take). Confirm it clears 149 words/min with margin
(O5 predicts ~780). This is what D16 step 3 depends on, and D17's
pre-generate-everything choice leans on it harder than the alternative would
have.

**Done when:** a measured words/min for the patch path exists, and the
sustainable patch rate (patched slots per airtime hour) is written down.

### N2. Resolve the cheap backlog issues in PLAN.md (no GPU)

Issues **#3**, **#4**, **#5**, **#8**, **#9**, **#10**, **#11**, **#12** as
filed. Note that **#3 and D13 are one piece of work**, not two — a single
closed-set validation pass covering lore stems, state keys, event ids and
fork carry keys.

### N2b. Author the state vocabulary and validators (no GPU)

- `state:` block in `generation.yaml` (D13) — flags, moods, carry_keys.
- The shared closed-set validator (#3 + D13).
- The `events.yaml` schema and its spine-canon validator (#13).
- The fork convergence validator (#15).

Cheap, no GPU, and every later stage validates against them — so they come
before Layer 1 runs, not after.

### N3. Scaffolding — config, concurrent client, work-list

As previously specced (`src/config.py`, `src/concurrent_llm.py`,
`src/worklist.py`, `src/pool.py`). The work-list pass now enumerates
`(segment, slot, take, condition)` rather than `(segment, slot, take)` — the
condition is what distinguishes a neutral take from a conditioned one (D12),
and it must be assigned deterministically up front like every other path.

### N4. Layer 1 (arc) — serial, plus forks and the event-table draft

Build as PLAN.md specs it, with three additions:

- Declare the ~4 fork points and their variants, subject to D11's convergence
  contract (validated, not trusted).
- Emit `event_windows` per segment.
- Draft `events.yaml` (D15).

Post-L1 guards: segment hours within +/-5% of `hours_total`; **fork
convergence check**; every drafted event's `sets:`/`requires:` inside the
declared vocabulary and `scope: ambient`.

**Then stop.** Read `arc_plan.yaml` *and* `events.yaml` by hand — the event
table is authored canon in everything but who typed it, and it is the one
artefact that can quietly contradict the spine. Also check O6 here: does any
fork point recur across loops?

### N5. Layer 2a/2b (chapters, then slots) — parallel

Per D6 and D7, with D14's additions: every slot carries `sensitivity` and
`depends_on`, validated against the closed vocabulary. Mark chapter boundaries
as event roll points (D15).

Post-L2 guards: segment slots sum to within ~80% of `target_words`; **the
`sensitivity_budget` check** (D14) — warn if >40% of a segment's slots are
`flags`-sensitive.

Brief two segments, read them by hand, then run the rest.

### N6. Layer 3 (dialogue) — parallel, the ~60-hour run

Local take function (resolves #6 + thread-safety, D8), drained through N3's
pool. Take 001 neutral, 002/003 conditioned per D12. Stinger pass for the
event table (D15).

Post-L3 guard, hard: **every slot has exactly one neutral take** (#14). A run
that ends without it has produced a library that can dead-air.

Verify on two segments, confirm the manifest and resume behave under
concurrency, *then* start the full run.

### N7. Scheduler / late-binding tier (D4, D15, D16) — separate work

The JIT picker, the seeded event engine and its ledger, the fork resolver, the
`llama3.1:8b` patch path, and `min_hours_before_repeat` (O1). Must supply its
own `BranchSelector` that merges `carry` into the selector context (D18 /
#17). Depends on a real take library, so it follows N6.

### N8. TTS real-time stage (D1, O4) — separate work

One-beat lookahead and an audio-sync `scale` in the campaign renderer. Not
part of this utility; filed so it is not lost.
