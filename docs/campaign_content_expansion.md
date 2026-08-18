# Campaign Content Expansion

## Overview

The campaign module plays a finite, fully-scripted show. The Ashiorid canon path
is **1,486 spoken words** — roughly 10–15 minutes. The target is **168 hours of
continuous live content**, which at ~150 wpm is about **1.5 million spoken
words**. That is a factor of roughly **1,000×**, so hand-authoring is not a
strategy.

This document describes the four mechanisms built to close that gap, the pack
format additions that expose them to authors, and how to extend each one.

The governing idea: **the spine stays authored and deterministic; wording,
filler and memory become generated.** The show's premise is a weekly time loop,
which is what makes replaying the same ten spine scenes several hundred times
narratively legal — the plot is *supposed* to repeat. What must never repeat is
the wording.

## The four mechanisms

| Mechanism | What it multiplies | Where it lives |
|---|---|---|
| **Variant pools** | Wording. A beat holds a list of phrasings; the renderer cycles them deterministically, covering the pool fully before any repeat. | `Beat.texts`, `Beat.key` |
| **Improv** | Wording, without a ceiling. `improv: true` hands the beat's text to a persona LLM as *intent* rather than script. | `app/campaign/improviser.py` |
| **Ambient scenes** | Hours. Fully-generated filler scenes carrying a `prompt:` and no beats, injected between spine scenes. | `app/campaign/ambient.py` |
| **Lore selectors + carry** | Continuity. `lore:` chooses which background notes enter model context; `state.carry` accumulates across loops so loop 40 is narratable as different from loop 1. | `CampaignPack.lore`, `CampaignState.carry` |

Variant pools and improv compound: the variant is selected **first**, then improv
riffs on the selected variant, so the model gets a different starting point on
every pass.

### Why ambient scenes are the hours

The spine is plot. Every spine scene advances the story, so there is a hard limit
on how many can exist before the story is over. Ambient scenes decide nothing —
they are the party talking around a fire — so they can be injected anywhere, in
any order, any number of times. That is where the 168 hours actually come from.

**Ambient scenes are fully generated.** They carry no scripted beats. The
consequence is deliberate and handled: if generation fails, the runtime **skips**
the ambient scene and advances straight to the pending spine scene. An LLM outage
costs filler, never dead air.

## Pack format additions

These extend the format documented in
[campaign_pack_format.md](campaign_pack_format.md). Everything below is
optional — a pack using none of it behaves exactly as before.

### campaign.yaml

```yaml
ambient:
  every: 2                       # inject an ambient scene after every N spine
                                 # scenes. 0, negative, or absent disables it.
  pool: [camp-fire, road-talk]   # optional. Empty or absent means "any
                                 # ambient-flagged scene in the pack".
```

A YAML key written with no value parses as `None`, not as a missing key —
`every:` alone means disabled, not default.

### scenes/&lt;file&gt;.yaml

```yaml
id: camp-fire
ambient: true          # not part of the spine; injected, never linked to
prompt: |              # the generation brief, required when ambient and beatless
  The party waits out a rainstorm under a rock overhang. Nothing happens.
  They talk about home. Six to ten short lines. Everything here is read
  aloud, so write for the ear.
lore: [the-event, moonwells]   # which lore notes enter model context here
```

| Key | Applies to | Meaning |
|---|---|---|
| `ambient` | any scene | Marks it as filler. Exempt from the "unreachable" and "no beats" warnings. **Error** if it appears as a branch target or `default_next`. |
| `prompt` | ambient scenes | The generation brief. Required when the scene has no beats. Whitespace-only counts as absent. |
| `lore` | any scene | Lore note stems, matching `lore/<stem>.md`. Only the selected notes travel to the model — context is a budget, never a dump. An unknown stem is a validation **error**. |

### Variant pools on a beat

`text` accepts a string or a list. A list makes it a variant pool:

```yaml
- type: dialogue
  speaker: helen
  improv: true
  text:
    - "We should run."
    - "This is where we leave."
    - "I'm not staying to watch the rest of this."
```

The **first entry is the canon phrasing**. A single string is normalized to a
one-entry pool, so `Beat.texts` is always populated and `Beat.text` is always the
first variant. This is what keeps `--dry-run` output byte-identical after a pool
is added.

An empty or whitespace-only entry in a pool is a validation **error** — it would
render as silence.

## Module status

Tasks are in dependency order; `T1` had to land before anything else.

| # | Module | State |
|---|---|---|
| T1 | `app/campaign/pack.py` | **Promoted.** `Beat.texts`/`key`, `Scene.ambient`/`prompt`/`lore`, `CampaignPack.lore`/`ambient_every`/`ambient_pool`. 74 tests. |
| T2 | `app/campaign/validator.py` | Tests (65) and spec complete; awaiting dispatch. 13 failing by design until it regenerates. |
| T3 | `app/campaign/improviser.py` | Tests (90) and spec complete; in dispatch. Two contracts: `__call__` raises, `generate_scene` never does. |
| T4 | `app/campaign/ambient.py` | Tests (23) and spec complete; awaiting dispatch. |
| T5 | `app/campaign/renderer.py` | Pending. Variant cycling on `beat.key`, resolved before improv; beatless ambient scenes call `generate_scene`; rendered lines feed back via `observe()`. |
| T6 | `app/campaign/runtime.py` | Pending. Ambient injection via `state.pending_next`/`spine_played`; `carry` producers in `reset()`. |
| T7 | `app/campaign/cli.py` | Pending. `--improv`, `--improv-model`, `--ambient-every`, `--no-ambient`, `--loops N`. |

**`--dry-run` must remain exactly what it is today**: no improviser, no ambient,
no network. It is the reproducibility guarantee the canon regression rests on.

### Content shipped

- 10 spine scenes carrying **56 variant pools / 167 phrasings**
- **56 beats** marked `improv: true` (was 3)
- `lore:` selectors on 9 of 10 spine scenes, so the three lore notes finally
  reach a model
- **8 ambient scenes** — `a01-camp-fire` … `a08-buffalo-lost`
- `config/campaigns/ashiorid.yaml` — the `llama3.1:8b` block

Ambient scene files sit in `scenes/` alongside the spine with an `a` prefix, not
in a subdirectory: the loader globs `scenes/*.yaml` **non-recursively**. The
prefix sorts them after the numbered spine files.

## Expanding the content

None of this involves Python. Validate and dry-run after each change.

### Add phrasings to an existing beat

Turn `text` into a list, keeping the current wording as the **first** entry, then
add alternates. Every entry is read aloud by TTS, so each must survive one
hearing on its own — no entry may depend on having heard another.

Aim for genuinely different sentence shapes rather than synonym swaps. Three
variants that restructure the thought beat six that trade adjectives.

### Add an ambient scene

Create `campaigns/<pack>/scenes/a<NN>-<slug>.yaml`:

```yaml
id: night-watch
ambient: true
lore: [moonwells]
prompt: |
  Two of the party share a watch while the others sleep. Nothing happens.
  They talk about what they will do after. Six to ten short lines.
  Everything here is read aloud, so write for the ear.
```

Rules that keep it injectable:

- **Decide nothing.** It can be injected anywhere in the arc, including before
  the party has learned who they are. A scene that assumes a revelation will
  eventually play before it.
- **No beats needed.** The prompt is the content.
- **Never link to it.** No branch or `default_next` may target it; that is a
  validation error.
- **State the length and the medium in the prompt.** "Six to ten short lines" and
  "written for the ear" are doing real work on a small model.

### Add a lore note

Drop `lore/<stem>.md` into the pack, then add `<stem>` to the `lore:` list of
scenes that should see it. A note no scene selects is loaded but never sent — it
costs nothing and is not a warning.

### Widen improv

Add `improv: true` to any narration or dialogue beat. Safe to apply broadly: the
renderer catches every improviser failure and falls back to the scripted text, so
the worst case for a beat is that it plays as written.

Leave `improv` off where the exact words are load-bearing — a name, a number, a
line another scene quotes back.

### Add a whole scene or pack

Unchanged from [campaign_pack_format.md](campaign_pack_format.md): wire the spine
with `default_next` first, then branches, then `--validate` until clean and
`--dry-run` until it reads well aloud.

## Changing the modules

Code under `app/campaign/` is generated by a local model through the worker
harness in `tools/qwen_worker/`, not hand-written. Claude writes the spec and the
pytest file first, `qwen3-coder:30b` implements, a human reviews and promotes.

```bash
.venv/bin/python tools/qwen_worker/runner.py preflight
.venv/bin/python tools/qwen_worker/runner.py run tools/qwen_worker/specs/<task>.yaml --attempts 3
# review .qwen_staging/<task>/ BY HAND, then:
.venv/bin/python tools/qwen_worker/runner.py promote tools/qwen_worker/specs/<task>.yaml
```

The acceptance test file **must exist in the working tree before the task runs** —
`cmd_run` does not inject it. Regeneration is gated by each module's *full*
existing test file, so a regression cannot be promoted.

### The ratchet

How a review finding is handled depends on what kind of finding it is:

| Finding | Response |
|---|---|
| **Behavioural gap** | New test + spec note, then **regenerate**. Never hand-patch behaviour — the next regeneration would silently drop the patch. |
| **Hygiene** (unused imports, dead branches, pointless f-prefixes) | Hand-patch after promotion. |
| **Prose or show content** | Hand-write. The model does not write the show. |

### Two rules that were earned the hard way

**When a dispatch fails, suspect the spec before the model.** Most failures this
wave were spec defects. A worked example: a passing build was rejected on review
for four behavioural defects; the tightened spec fixed all four but regressed
three *new* things, and all three traced back to the spec — a code skeleton was
read as the whole function body, dropping guards that had been prose-only.

**Positive prescription beats prohibition.** A negative rule ("NEVER derive the
id from the filename") failed twice against a 30B model. A concrete code skeleton
showing the right thing succeeded on the next attempt. Where a rule matters,
write the three lines of code you want rather than the sentence forbidding what
you don't.

## Verification

```bash
# 1. Full suite
.venv/bin/python -m pytest -q

# 2. Pack validates
PYTHONPATH=app .venv/bin/python app/campaign/cli.py --pack campaigns/ashiorid --validate

# 3. Canon regression — the important one. With the new features off,
#    output must be byte-identical to before the wave.
PYTHONPATH=app .venv/bin/python app/campaign/cli.py --pack campaigns/ashiorid \
    --dry-run --no-pace --no-color | diff - /tmp/canon-baseline.txt

# 4. Ambient injection offline (scheduler on, improviser off — scenes skip,
#    spine stays intact)
PYTHONPATH=app .venv/bin/python app/campaign/cli.py --pack campaigns/ashiorid \
    --dry-run --no-pace --ambient-every 2

# 5. Live smoke against real Ollama
PYTHONPATH=app .venv/bin/python app/campaign/cli.py --pack campaigns/ashiorid \
    --improv --improv-model llama3.1:8b --ambient-every 2 --no-pace

# 6. Soak — 20 loops, measure generated words against the 1.5M target
PYTHONPATH=app .venv/bin/python app/campaign/cli.py --pack campaigns/ashiorid \
    --improv --loops 20 --ambient-every 2 --no-pace --no-color | wc -w
```

Steps 4–6 require T5–T7. Step 3 is the one that must never move: it is what
proves the expansion is additive.

## Changelog

- **v1.0.0** (2026-08-17) — initial: four expansion mechanisms, pack format
  additions (`ambient`, `prompt`, `lore`, variant pools), module status,
  authoring recipes, worker-harness workflow and the ratchet.
