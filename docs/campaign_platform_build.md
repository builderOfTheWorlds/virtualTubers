# Campaign Platform — Build Plan

> **Status**: IMPLEMENTED. Landed 2026-07-26 on `feat/campaign-platform`
> (all eight steps below; test suite green, 780 passing).
> **Written**: 2026-07-26
> **Purpose**: hand-off document / design record. Kept intact below as the
> as-designed reference — see "As shipped" immediately below for where each
> step actually landed and the two places reality diverged from the design.
> For the authoritative frozen API signatures actually implemented, see
> `campaign_platform_contract.md` (same directory as this build's working notes) and the
> per-module docs it links to.

## As shipped (2026-07-26)

Every numbered step in "Suggested order" below landed:

1. **Primitive config** — `config/primitives.yaml` (shared) +
   `config/campaigns/coder/primitives.yaml` + `narration.yaml`, reproducing
   the pre-campaign-platform coder show exactly. `config/campaigns/dnd/`
   ships too, as the second working campaign. See `docs/primitives.md`.
2. **`app/primitives.py`** — behaviors, two-layer merge + `extends`
   resolution, screen-time estimation. See `docs/primitives.md`.
3. **`app/episode_schema.py` + `config/validation.yaml`** — load/normalize/
   validate. See `docs/episode_schema.md`.
4. **`plan_scenes` deleted** from `app/revoice.py`; `scene_visual_seconds`
   is now recipe-driven. See `docs/revoice.md`.
5. **`app/replay.py` reworked** to render via primitive recipes, honoring
   `mode`/`target`. See `docs/replay.md`.
6. **Campaign namespacing** — `replays/<campaign>/`,
   `app/narration_store.py` gained a `campaign` column. See
   `docs/replay_pane.md`, `docs/narration_store.md`.
7. **Generic workers** — `docker-compose.yml` now runs `worker-1`..
   `worker-8`, identical except container identity. See
   `docs/blank_workers.md`.
8. **Persona assignment** — `app/campaign_control.py` (Redis), the
   `/tmp/persona.json` relay file, and `POST /campaigns/{campaign}/start` /
   `POST /campaigns/stop` / `GET /campaigns/active` on `services/message-api`.
   See `docs/campaign_control.md`.

Episode generation moved OUT of the platform to `generators/coder/`
(`app/session_log_parser.py` and `scripts/build_replay_library.py` were
moved, not copied — see `generators/README.md` and
`generators/coder/README.md`). `docs/session_log_parser.md` now redirects
to the generator's own doc.

### Where reality diverged from this design

1. **The coder-campaign overlay is a second compose file, not a Compose
   `profiles:` entry**, contrary to this document's Codebase-changes table
   ("coder extras behind a profile") and its Blank-workers section. A
   Compose *profile* can only turn a whole service on/off in the file(s)
   where it's finally assembled — it cannot conditionally add volumes/env
   to a service (`worker-1`..`worker-8`) that another file already defines
   unconditionally and always-on. The actual mechanism is
   `docker-compose.coder.yml`, loaded via `-f` or `.env`'s `COMPOSE_FILE`,
   which Compose merges into the same service names by name. Full
   rationale: `docs/blank_workers.md`'s "Why an overlay file, not a
   `profiles:` entry" section.
2. **Stream key and layout preset do NOT hot-swap**, contrary to the
   "What hot-swaps, and what does not" table below (which marks stream
   key/RTMP target ✅ and layout preset ⚠️-but-described-as-buildable).
   `startup.sh` resolves both `STREAM_KEY`/`STREAM_RTMP_URL` and the layout
   preset (`build_layout.py`) exactly once, at container boot, before
   `app/agent.py`/`app/avatar.py`/`app/replay_pane.py` even start — there is
   no relay file or Redis key either one polls, unlike system prompt,
   avatar, and voice, which all genuinely hot-swap via `/tmp/persona.json`.
   Changing either still requires a container restart today. See
   `docs/campaign_control.md`'s "What genuinely hot-swaps, and what does
   not" section for the as-shipped table and root cause.

Everything else below is preserved as written — the original design record.

---

> **Original status note (superseded by "As shipped" above)**: design
> agreed, not yet implemented.
> **Written**: 2026-07-26
> **Purpose**: hand-off document. A fresh session should be able to read this
> and start building without re-deriving the design.

---

## Goal

Generalize the platform so it can perform **any** campaign, not just replays of
Claude Code coding sessions.

A "campaign" is a set of inputs — personas, scripts, and the primitives those
scripts use. Today's coder-worker content is one campaign. A D&D story show is
another. Whatever comes third is another.

## The architecture decision

**The platform accepts a finished script and performs it. Script generation
lives entirely outside the platform.**

This is not a new boundary — it formalizes one that already exists.
`scripts/build_replay_library.py` is already an offline tool; the platform
already consumes finished episode JSON from a read-only `replays/` mount and has
no idea how it was produced.

What changes is removing the coder-specific assumptions that leaked across that
boundary.

```
┌─ Generators (separate, one per campaign) ─┐     ┌─ Platform (this repo) ─┐
│  session-log-generator                    │     │  validate on ingest    │
│  dnd-generator (outline → two-pass)       │ ──▶ │  plan_scenes           │
│  …                                        │     │  revoice → TTS         │
│  emit: episode JSON                       │     │  replay.py performs    │
└───────────────────────────────────────────┘     └────────────────────────┘
              writes replays/<campaign>/*.json
```

Generators need not share a language, a machine, or a deploy cycle with the
platform. The episode schema is the only contract.

### Explicitly rejected alternatives

- **A `script_sources/` plugin registry inside the platform.** Makes the
  platform responsible for knowing about campaigns. Bigger, and unnecessary
  once generation is external.
- **A campaign-aware `plan_scenes`.** The opposite is better — see below.

---

## An episode is a performance score

Sheet music, not a recording. It says who speaks, what happens on screen, and in
what order. It deliberately does **not** say how long anything takes or what it
sounds like.

| Property | Why it matters |
|---|---|
| **Declarative** | Describes what appears, not how to draw it. Platform owns rendering. |
| **Display-only** | Recorded commands are rendered, never executed. Existing invariant — keep it. |
| **Speaker-attributed** | One field drives voice selection, avatar state, and duet casting. |
| **Timing-agnostic** | No durations in the file. Platform estimates from primitive config, then audio-anchors to actual TTS length. This is *why* the same episode re-voices differently each airing and still paces correctly. |
| **Self-contained** | All text is inline. Only binary assets are referenced, and only from the campaign's own asset directory. |

---

## Episode schema

```
Episode
├── meta      schema version, campaign, id, title, created
├── cast      speaker ids used (validation + duet casting)
└── scenes[]  the score — each scene is one spoken line plus its visuals
     ├── speaker   who
     ├── kind      what kind of moment this is → narration prompt + fallback template
     ├── text      narration source (what gets re-voiced)
     ├── fallback  what to say if the LLM re-voice fails
     ├── mode      sequence (default) | parallel — how render[] entries run
     └── render[]  zero or more visual primitives, freely composable
          ├── primitive  which recipe
          ├── payload    content for that recipe
          └── target     pane id (optional; primitive supplies the default)
```

**`kind` and `render[]` are orthogonal.** `kind` decides what the character
*says*; `render[]` decides what the audience *sees*. Fusing them is what forces
a combinatorial explosion of primitives — with them separate, `display_map` and
`type_text` and `open_inventory` compose freely without any pre-declared
combination.

### Example — D&D campaign

```json
{
  "meta": {
    "schema": 1,
    "campaign": "dnd",
    "id": "s03e02-idra-tavern",
    "title": "The Tavern at Idra",
    "created": "2026-07-26T21:14:00Z"
  },
  "cast": ["gm", "thorin", "sable"],
  "scenes": [
    {
      "speaker": "gm",
      "kind": "recap",
      "text": "Last time, our heroes crossed the Weeping Fen and limped into Idra at dusk.",
      "fallback": "The GM recaps last session.",
      "render": []
    },
    {
      "speaker": "gm",
      "kind": "scene_set",
      "text": "The tavern is low-ceilinged, smoke-dark, and louder than it looks from outside.",
      "fallback": "The scene is set at the tavern.",
      "mode": "parallel",
      "render": [
        {"primitive": "display_map", "payload": {"image": "idra_tavern.png"}},
        {"primitive": "type_text", "target": "notes",
         "payload": {"text": "The Rusted Tankard — Idra, dusk"}}
      ]
    },
    {
      "speaker": "thorin",
      "kind": "dialogue",
      "text": "Barkeep. Ale. And whatever you know about the woods road.",
      "fallback": "Thorin orders a drink and asks about the road.",
      "render": [
        {"primitive": "open_inventory", "payload": {"character": "thorin"}}
      ]
    }
  ]
}
```

### Example — coder campaign (same schema)

```json
{
  "speaker": "coder",
  "kind": "coder_work",
  "text": "Checking what's actually in the config directory, then fixing the typo.",
  "fallback": "Okay — running ls -la config/, then editing worker.yaml.",
  "mode": "sequence",
  "render": [
    {"primitive": "show_command",
     "payload": {"command": "ls -la config/", "output": "total 48\n..."}},
    {"primitive": "show_diff",
     "payload": {"file": "config/worker.yaml", "hunks": ["- fps: 30", "+ fps: 60"]}}
  ]
}
```

Same schema, different primitives — and note the coder scene now carries *two*
render entries under one spoken line, which is exactly the grouping
`plan_scenes` does today.

---

## Primitives — configurable, no code change

The key split is between two things that look like one:

### Behaviors — in code, deliberately few, rarely change

The atoms of "how pixels get drawn".

| Behavior | Does | Timing model |
|---|---|---|
| `type` | character-by-character typing | `chars / rate_cps` |
| `print` | dump or scroll a block | `lines / rate_lps` |
| `diff` | colorized +/- lines | `lines / rate_lps` |
| `image` | display an image in a pane | `hold_s` |
| `pause` | hold | fixed seconds |

Adding a behavior is the one thing that needs code — but the list is meant to
stay short. Reach for a new *primitive* first.

### Payload carries content; the primitive carries presentation

**This is the rule that keeps generators simple.** `payload` holds only what is
being shown — a command string, output text, diff hunks, a file path, an image
reference. Colors, fonts, rates, alignment, and pane targeting live entirely in
primitive config.

A generator that wants a scene heading emits `"kind": "scene_set"` with a
`type_text` render entry, not a styling hint. Styling in the episode would make
every generator responsible for presentation, break the declarative property, and
create a second config surface the validator cannot check.

### Text payload is always inline — no sidecar files

**Decided.** Today's episodes reference `detail_file` (`tool_001_Bash.md`) to
keep large command output out of the main JSON. The new schema drops that:
**every string that will be spoken or displayed lives in the episode itself.**

The reason is validation coverage. If payload can point at an external file, the
redaction audit has to resolve and scan it too — and the moment it misses one, a
password hides in a sidecar the audit never opened and goes out on stream. Inline
payload makes "the validator saw everything" true by construction rather than by
diligence.

Cost is negligible: current episodes average ~72 KB.

### Binary assets are referenced, and confined

Images cannot be inlined, so `display_map` takes `payload.image: "idra.png"`.
Two rules keep that safe:

- **Resolution is basename-only inside the campaign's asset directory**
  (`replays/<campaign>/assets/`) — the same containment property the episode
  library already has, so a payload string can never reach another path.
- **The validator checks existence at ingest**, so a missing or misspelled asset
  fails the episode rather than producing a blank pane mid-airing.

A regex audit cannot inspect a PNG. If an image could contain something
sensitive — a screenshot with credentials visible, say — that is a *generator*
responsibility, and an asset manifest listing approved files is the mechanism.

### The scene model

Every scene is exactly this, with no special cases:

> **one speaker + one narration line + zero or more visual primitives**

Pure dialogue is `render: []`. Pure visual business is an empty `text`. Anything
else composes. "Read a line while something happens on screen" is therefore the
*normal* case — narration and rendering always run together, audio-anchored to
each other.

Three layers, each owned by a different party:

| Layer | Lives in | Who adds to it |
|---|---|---|
| **Behaviors** (`type`, `print`, `diff`, `image`, `pause`) | code | rarely — platform devs |
| **Primitives** (`show_command`, `display_map`, `open_inventory`) | `config/primitives.yaml` | anyone, per campaign |
| **Scenes** (`kind` + `render[]`) | the episode file | generators, per episode |

### Primitives — in config, unlimited, added freely

A primitive is a **named recipe over behaviors**, plus styling and timing. It is
purely visual — it carries no narration, because narration belongs to the scene,
not to any one thing on screen.

Two fields make recipes composable:

- **`target`** — which pane the behavior draws into. Pane ids come from the
  layout system (`/tmp/panes/<id>.yaml`, see [build_layout.md](build_layout.md)).
  A scene's `render` entry can override it.
- **`mode`** — set per scene, not per primitive. `sequence` (default) runs
  render entries one after another; `parallel` starts them together.
  Screen-time estimation takes the sum under `sequence`, the max under
  `parallel`.

### Shared base + per-campaign additions

Primitives resolve in two layers, mirroring how `build_layout.py` resolves a
layout preset against reusable panel-type defaults:

```
config/primitives.yaml                    ← shared, presentation-neutral
config/campaigns/<name>/primitives.yaml   ← campaign additions and overrides
```

The campaign file **deep-merges over** the base, so it can add new primitives,
override a single field of a shared one (`rate_cps` for a faster-talking
campaign), or specialize via `extends`. A campaign redefining a shared primitive
outright is allowed but logged — silent overrides are confusing.

**Shared** primitives carry no domain meaning:

```yaml
# config/primitives.yaml
type_text:
  target: theater
  behaviors:
    - behavior: type
      field: payload.text
      rate_cps: 30
      style: {fg: white}

print_text:
  target: theater
  behaviors:
    - behavior: print
      field: payload.text
      rate_lps: 10

show_image:
  target: theater
  behaviors:
    - behavior: image
      field: payload.image
      hold_s: 10

beat:
  behaviors:
    - behavior: pause
      seconds: 1.5
```

**Campaign** primitives are where domain shows up:

```yaml
# config/campaigns/coder/primitives.yaml
show_command:
  target: theater
  behaviors:
    - behavior: type
      field: payload.command
      prefix: "$ "
      rate_cps: 45
      style: {fg: green, bold: true}
    - behavior: print
      field: payload.output
      max_lines: 15
      rate_lps: 8
      style: {fg: bright_black}

show_diff:
  target: theater
  behaviors:
    - behavior: diff
      field: payload.hunks
      rate_lps: 6
      style: {add: green, remove: red}
```

```yaml
# config/campaigns/dnd/primitives.yaml
display_map:
  extends: show_image           # same recipe, longer hold
  behaviors:
    - hold_s: 20

open_inventory:
  target: notes
  behaviors:
    - behavior: print
      field: payload.character
      rate_lps: 10
      style: {fg: cyan}

scene_heading:
  extends: type_text            # same recipe, different styling
  behaviors:
    - rate_cps: 18
      style: {fg: yellow, bold: true}
```

Note there is no `say` primitive and no `reveal_handout`. Speech is a scene
property, and "map plus caption" is just two render entries under
`mode: parallel` — no pre-declared combination needed.

### Campaign isolation falls out for free

The validator's `unknown_primitive: reject` checks against the **merged set for
the campaign being loaded**. A D&D episode referencing `open_inventory` fails
validation if someone tries to air it under the coder campaign, rather than
producing a blank pane mid-stream. Campaign isolation is enforced at ingest
without any extra mechanism.

### Narration prompts live with `kind`, not with primitives

```yaml
# config/campaigns/dnd/narration.yaml
dialogue:
  prompt: |
    You are {name}. Speak this line in character, roughly {words} words:

    {text}
  fallback_template: "{name} says something in character."

scene_set:
  prompt: |
    You are {name}, setting the scene. Roughly {words} words:

    {text}
  fallback_template: "The scene is set."
```

```yaml
# config/campaigns/coder/narration.yaml
coder_work:
  prompt: |
    The engineer is working. Say what they are doing, roughly {words} words:

    {material}
  fallback_template: "Okay — {render_summary}."
```

`{material}` is the platform's rendering of the scene's `render[]` entries into
prompt text, so a narration prompt can describe visuals it does not need to know
the details of.

**Adding a primitive is a YAML entry.** A campaign wanting `show_map` or
`roll_dice` composes it from existing behaviors with different styling and a new
prompt — no code, no rebuild.

This deliberately mirrors the existing pattern: `config/panels/*.yaml` defines
panel-type defaults and `config/layouts/*.yaml` composes them. Primitives are the
same idea one layer down. See [build_layout.md](build_layout.md).

### Honest limit

Composing existing behaviors is config-only. A genuinely *new* rendering
mechanism (an image pane, say) needs a new behavior in code. That is the same
escape hatch as the avatar provider registry — rare, and the boundary is clear.

### What this deletes

- **`plan_scenes()` disappears entirely.** It exists only because
  `session_log_parser` emits *raw* session events (one per tool call) that have
  to be grouped into scenes. When generators emit scene-sized units directly,
  the episode is already a list of scenes and there is nothing to group.
- `revoice.py`'s module-level `_PROMPTS` dict → `narration.yaml` lookup by `kind`
- `revoice.py`'s `fallback_narration()` → `scene.fallback`, or
  `fallback_template` from narration config
- `scene_visual_seconds()`'s per-kind branching → computed from the render
  recipes, summed under `sequence`, maxed under `parallel`

**Where `MAX_SCENE_EVENTS` goes.** Today it caps how much screen time one spoken
line has to cover. With generators owning scene boundaries, that becomes a
**validator rule** (`max_scene_seconds`) — reject or warn on a scene whose
estimated screen time exceeds the budget, rather than silently regrouping behind
the generator's back.

---

## Validator — configurable, runs in the pipeline

Rules in YAML, engine in code. Same config and same engine on both sides.

```yaml
# config/validation.yaml
structure:
  required_scene_fields: [speaker, kind]
  unknown_kind: reject             # reject | warn | skip_scene
  unknown_primitive: reject        # every render[].primitive must be defined
  unknown_speaker: reject          # must appear in meta.cast
  unknown_target: reject           # pane id must exist in the layout preset
  external_refs: reject            # no sidecar files — text payload must be inline
limits:
  max_scenes: 5000
  max_text_chars: 10000
  max_scene_seconds: 90            # replaces MAX_SCENE_EVENTS — one spoken line
                                   # should not have to cover more than this
assets:
  root: assets                     # relative to the campaign's library dir
  basename_only: true              # no path separators, no traversal
  must_exist: true                 # fail ingest, not the live airing
  manifest: assets/approved.txt    # optional allowlist of displayable files
redaction:
  on_match: reject                 # reject | redact | warn
  patterns:
    - name: password
      regex: '(?i)\b(password|passwd|pwd|secret)\s*[:=]\s*\S+'
    - name: api_key
      regex: '\b(sk-[A-Za-z0-9_-]{20,}|ghp_[A-Za-z0-9]{36})\b'
    - name: stream_key
      regex: '\blive_[A-Za-z0-9]{20,}\b'
    - name: public_ip
      regex: '\b(?:\d{1,3}\.){3}\d{1,3}\b'
      except: '^(10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.|127\.)'
```

### Run it in two places

1. **Generator pipeline** — fail the build, do not write the episode.
2. **Platform ingest** — refuse to load, log loudly, do not air.

The platform must not trust a script simply because it appeared in the
directory. These go out live on Twitch.

Config-driven patterns matter specifically because of the 2026-07-12 password
leak: **adding a pattern after an incident becomes a config edit, not a rebuild
and redeploy.** Private LAN IPs stay readable by design (`except` above) — that
was a deliberate earlier decision, preserve it.

---

## Codebase changes

| File | Change |
|---|---|
| `config/primitives.yaml` | **new** — shared, presentation-neutral primitives |
| `config/campaigns/<name>/primitives.yaml` | **new** — campaign additions, deep-merged over the base |
| `config/campaigns/<name>/narration.yaml` | **new** — prompts + fallback templates keyed by `kind` |
| `config/validation.yaml` | **new** — structure, limits, redaction patterns |
| `app/episode_schema.py` | **new** — load, parse, validate an episode |
| `app/primitives.py` | **new** — behavior implementations + recipe resolution + screen-time estimation |
| `app/revoice.py` | **`plan_scenes` deleted**; `_PROMPTS`/`fallback_narration` removed; `scene_visual_seconds` computed from recipes |
| `app/replay.py` | render via primitive recipes rather than per-type branches; honor `mode` and `target` |
| `app/replay_pane.py` | validate on load; resolve library path per campaign |
| `app/narration_store.py` | **cache key collision** — keys on `episode` alone today; add campaign to the key |
| `app/session_log_parser.py` | **moves out** to the generator side |
| `scripts/build_replay_library.py` | **moves out** to the generator side |
| `config/campaigns/<name>/personas.yaml` | **new** — speaker id → voice, avatar name, title, system prompt |
| `app/worker_control.py` | extend with persona assignment alongside the enabled flag |
| `services/message-api` | **new** — `POST /campaigns/{id}/start`, `/stop`, `GET /campaigns/active` |
| `docker-compose.yml` | generic `worker-1`…`worker-8`; coder extras behind a profile; library mount becomes `replays/<campaign>/` |

### Library namespacing

`replays/<campaign>/` — keep basename-only resolution **within** the campaign
directory, preserving the existing property that bus payloads cannot reach other
files.

---

## Out of scope

- **No migration script.** Existing episodes stay as they are; regenerate from
  the generator side if and when they are wanted in the new format.
- Runtime campaign switching via message-api. Later, if wanted — it would follow
  the `worker_control.py` pattern.
- Per-campaign layouts and panels. The existing `LAYOUT_PRESET` mechanism
  probably covers it.
- The coding pipeline handlers (`task_assignment` → `commit_notification` →
  `test_passed`/`bug_report` → `manager_report`). Leave working as-is; campaigns
  that do not use them simply never register them.

---

## Blank workers + runtime persona assignment

**Decided**: workers are interchangeable and get their identity at campaign
start, over the API — not from `docker-compose.yml`, and not from `.env`.

### Compose

`worker-1` … `worker-8`: identical services, no persona env, no per-worker
stream key. Coder-campaign extras (the tester's `repo:/data/repos/*:ro` mounts,
the A/B coder workspaces) move behind a compose **profile**, exactly as
`local-infra` already does.

### Boot state: blank

A worker with no persona assigned is **disabled** — reusing
[worker_control.py](../app/worker_control.py) exactly as it works today: the
agent tick loop pauses and `stream_supervisor` stops ffmpeg. No new "blank" mode
to build; blank *is* disabled.

### Assignment

```
POST /campaigns/{campaign}/start
  {"cast": {"gm": "worker-1", "thorin": "worker-2", "sable": "worker-3"}}

POST /campaigns/stop          → all workers back to blank
GET  /campaigns/active        → current campaign + cast
```

State lives in Redis alongside `worker:{id}:enabled` — `campaign:active` and
`worker:{id}:persona`. The agent picks the change up on its next tick and writes
a **local relay file** for the panes, following the same rule the duet protocol
established: *panes never read Kafka or Redis directly* (see
[duet_replay.md](duet_replay.md)).

### What hot-swaps, and what does not

| Attribute | Hot-swap? | Mechanism |
|---|---|---|
| Agent system prompt / role | ✅ | agent re-reads persona on tick |
| Avatar name, title, expressions | ✅ | avatar pane re-reads relay file |
| Piper voice model | ✅ | TTS client reloads the resident model |
| Stream key / RTMP target | ✅ | `stream_supervisor` already stops/starts ffmpeg as a child |
| **Layout preset** | ⚠️ | needs a **tmux session rebuild** — kill the session and re-run `build_layout.py`. In-container and fast; *not* a container restart |

The layout case is the one real limit. Campaigns that share a layout preset
swap instantly; campaigns that differ pay a session rebuild.

### Guard rail

Reassigning a worker mid-airing must **refuse** rather than reassign, in the same
spirit as duets refusing rather than degrading. Force flag available for the
operator who means it.

---

## Open questions

None currently blocking. Decisions to revisit once something is running:

- Whether `extends` deep-merges behaviors positionally (as written above) or
  needs named behavior slots. Positional is simpler and probably fine for
  single-behavior specializations; it gets ambiguous for multi-behavior recipes.
- Whether campaign primitive files should be allowed to override shared ones at
  all, or only add. Currently allowed-but-logged.

---

## Suggested order

1. Write the primitive config, split across both layers — shared
   `type_text`/`print_text`/`show_image`/`beat` in `config/primitives.yaml`, and
   `show_command`/`show_diff` in `config/campaigns/coder/primitives.yaml` —
   plus `config/campaigns/coder/narration.yaml` carrying the prompts lifted from
   `revoice.py`'s `_PROMPTS`. Reproduce today's coder visuals exactly. This is
   the piece everything else hangs off; get it precisely right first.
2. `app/primitives.py` — behaviors, two-layer merge + `extends` resolution,
   recipe resolution, screen-time estimation (sum under `sequence`, max under
   `parallel`).
3. `app/episode_schema.py` + `config/validation.yaml`.
4. Delete `plan_scenes`; make `scene_visual_seconds` recipe-driven.
5. Rework `replay.py` rendering to honor `mode` and `target`.
6. Campaign namespacing (library path, narration cache key).
7. Generic workers in compose + coder extras behind a profile.
8. Persona assignment: Redis keys, relay file, message-api endpoints, and the
   pane-side re-read for avatar and voice.

Steps 1–6 are the platform generalization and are enough to run a D&D campaign
with a manual config swap. Steps 7–8 are what make switching campaigns a
first-class operation.

The D&D generator is a separate project that can start in parallel as soon as
step 1 fixes the contract.

---

## Related

- [replay.md](replay.md) — the performance engine
- [revoice.md](revoice.md) — narration pass
- [replay_pane.md](replay_pane.md) — the theater pane
- [duet_replay.md](duet_replay.md) — multi-worker performance protocol
- [build_layout.md](build_layout.md) — the config-driven pattern this mirrors
- [environment/hardware_and_hosting.md](environment/hardware_and_hosting.md) — where this runs and what it costs
