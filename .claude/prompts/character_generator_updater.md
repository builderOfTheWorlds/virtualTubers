# Character Generator + Updater — Design Plan

Status: design, not yet implemented. Written 2026-09-23.

Source concept: `docs/character_agent_generator.md` (user's own notes).

## Purpose

Two collaborating subsystems for VTuber character management:

1. **Character generator** — reads source material, interprets it, and
   produces a complete character: identity profile (personality, self-image,
   hopes/desires/objectives), a knowledge graph (backstory, world facts,
   self-knowledge), 3D avatar parameters, and an agent ingestion brief.
   Everything lands in Postgres.

2. **Character updater** — runs at each weekly timeline reset. Breaks the
   connections the character had between pieces of knowledge (the "forgotten
   links" model), keeps only the week's most significant additions active,
   folds the week's experiences into the profile (new relationships, events,
   discoveries), drifts personality/self-image toward significant experiences
   while protecting a locked core identity, and advances goal/milestone
   tracking.

Both are designed for extension: new generation features plug in as new
stages without touching existing ones.

## Existing assets to build on (do not reinvent)

| Asset | Location | What we take from it |
|---|---|---|
| Postgres conventions | `docs/database_schema.md` | No ORM, raw psycopg2, `CREATE_TABLE_SQL` constant per owning module, mirrored in `docs/sql/02_create_tables.sql`, no migration framework (`ADD COLUMN IF NOT EXISTS` for column additions) |
| 3layer-generator pipeline | `services/3layer-generator/` | The job/artifact pattern to copy: `generation_jobs` + `generation_artifacts` tables, `Context` dependency injection for testability, per-layer LLM profiles, `ON CONFLICT` upserts, staged execution (arc → segment → dialogue) with per-stage `kind` in artifacts |
| 3D character generator | `app/character_schema.py`, `app/character_preview.py` | The avatar stage outputs the *same* 0..1 slider dict the existing renderer consumes (`head_width`, `eye_size`, ..., `accent_color`). Presets exist (`chadwick`). `character_preview.py --json` gives the agent an ASCII iteration loop — the generator's avatar stage can validate/refine via exactly this loop |
| Cast format | `campaigns/*/cast/*.yaml` | Output shape compatibility: `name`, `archetype`, `voice`, `avatar`, `system_prompt`. Generated characters should be loadable by the campaign pack format with zero changes to `load_pack()` |
| Weekly reset seam | `app/campaign/runtime.py` `CampaignRuntime.reset(keep_carry=True)`, `CampaignState.carry` | The campaign already has the weekly loop concept and a carry mechanism. The character updater is the per-character analogue of `carry`, stored in Postgres rather than a dict |
| Knowledge pane | `app/knowledge_graph_pane.py` | Currently a static placeholder (Decision 3: "wiring to real data is a separate future task" — this is that task, eventually). Our graph schema is what that pane reads |
| LLM client | `app/llm_client.py` | Provider-switchable `complete(system_prompt, messages)` — the stage LLMs use it |
| Source material | `sourceworks/` (full Harry Potter text, movie script), `_quarantine_harry_potter/` (a prior hand analysis) | The generator's input format: plain-text source files. Store source text (or a stable reference to it) so regeneration is reproducible |
| Experience data | `messages` table (every bus message durably logged) | The updater's raw material for "what happened this week" — no new ingestion pipeline needed |

## Data model (Postgres)

Tables the module owns, created by `app/character/store.py` `CREATE_TABLE_SQL`
on first use, mirrored in `docs/sql/02_create_tables.sql` +
`docs/database_schema.md` per project convention.

### `characters`

| Column | Type | Notes |
|---|---|---|
| `id` | TEXT (UUID) PK | |
| `name` | TEXT NOT NULL UNIQUE | Canonical character name (e.g. `Harry`) |
| `slug` | TEXT NOT NULL UNIQUE | `harry-potter` — file/YAML names, cast filename |
| `origin_source` | TEXT nullable | Pointer to source: `file:sourceworks/harry.txt` or `pack:hptest` |
| `source_sha256` | TEXT nullable | Drift detection on re-generation |
| `avatar_params` | JSONB | Slider dict — consumed directly by `character_avatar` providers |
| `avatar_notes` | TEXT | Provenance for the look (why these sliders) |
| `status` | TEXT NOT NULL | `draft` | `active` | `retired` |
| `core` | JSONB NOT NULL DEFAULT '[]' | **Locked identity**: trait keys + short statements that the updater may never drift (the "consistent core identity" requirement) |
| `created_at` / `updated_at` | TIMESTAMPTZ | |

### `character_profiles` (versioned)

| Column | Type | Notes |
|---|---|---|
| `character_id` | TEXT FK | |
| `version` | INT | 1 = generated from source; increments per updater profile pass |
| `data` | JSONB NOT NULL | The profile document — see `Profile shape` below |
| `change_notes` | TEXT | Human/LLM summary of what this version changed vs the previous (empty for v1) |
| `created_by` | TEXT | `generator` | `updater:week-<N>` |
| `created_at` | TIMESTAMPTZ | |
| PK | (character_id, version) | |

**Never destroy a profile version** — the updater creates version N+1; the
agent reads the latest. This is what makes personality drift auditable and
reversible.

### `knowledge_nodes`

| Column | Type | Notes |
|---|---|---|
| `id` | TEXT (UUID) PK | |
| `character_id` | TEXT FK | |
| `name` | TEXT NOT NULL | **The wikilinkable name** — the Obsidian `[[name]]` token. Unique per character (`UNIQUE (character_id, name)`) |
| `kind` | TEXT NOT NULL | See `Node kinds` below |
| `content` | JSONB NOT NULL | Free-form detail: description, quotes, attributes |
| `core` | BOOLEAN NOT NULL DEFAULT FALSE | TRUE = survives every reset unconditionally (core identity, unshakable backstory facts). Seeded by the generator from `characters.core` + profile v1; the updater may only *add* to this set, never remove it without explicit operator action |
| `active` | BOOLEAN NOT NULL DEFAULT TRUE | The "forgotten links" state: TRUE = remembered, FALSE = dormant (knowledge preserved but not recalled) |
| `salience` | DOUBLE NOT NULL DEFAULT 0.5 | 0..1 significance score the updater re-ranks each reset week |
| `first_known_week` | INT | Week number the character first held this node |
| `last_reinforced_week` | INT nullable | Set when a dormant node/edge is re-learned |
| `origin` | TEXT NOT NULL | `generated` | `experience:week-<N>-<event>` |
| `created_at` / `updated_at` | TIMESTAMPTZ | |

### `knowledge_edges`

This is where the weekly reset happens.

| Column | Type | Notes |
|---|---|---|
| `id` | TEXT (UUID) PK | |
| `character_id` | TEXT FK | |
| `src_node` | TEXT FK → knowledge_nodes.id | |
| `dst_node` | TEXT FK → knowledge_nodes.id | |
| `relation` | TEXT NOT NULL | See `Edge relations` below |
| `strength` | DOUBLE NOT NULL DEFAULT 0.5 | 0..1 — how strongly the character holds this link |
| `active` | BOOLEAN NOT NULL DEFAULT TRUE | **FALSE = the edge is broken** (knowledge on both ends still exists, the connection is forgotten) |
| `established_week` | INT | |
| `broken_at_week` | INT nullable | Set by the updater when the reset breaks it |
| `relearned_at_week` | INT nullable | Set when a new experience re-establishes it |
| `origin` | TEXT NOT NULL | `generated` | `experience:week-<N>-<event>` |

**Reset semantics (the core of the updater):**

1. At reset week W→W+1, every edge with `active=TRUE` and
   `broken_at_week IS NULL` gets `active=FALSE`, `broken_at_week=W+1` —
   **except** edges where both endpoints are `core=TRUE` nodes, and except the
   week's top-salience selections (step 2).
2. The LLM ranker picks the top N salient nodes *and their incident edges*
   from the week's new/experience-derived knowledge (N from config, default
   e.g. 8) — those keep `active=TRUE`. These are "the most significant things
   they added to their knowledge that week."
3. Nodes: **all nodes are preserved** (the user's explicit model: "the
   knowledge is still there but forgotten"). Non-core nodes get
   `active=FALSE` after reset EXCEPT the top-salience set and `core=TRUE`
   nodes. `core=TRUE` nodes never deactivate.
4. Re-learning: during the next week, when the agent's experience stream
   touches a dormant node or a broken edge-endpoint pair (matched by name
   match or the LLM judge in the per-week `ingest` pass), the row flips back
   to `active=TRUE`, `last_reinforced_week`/`relearned_at_week`=W+1,
   `strength` refreshed. The agent is told (in its brief) only the *names*
   of dormant nodes relevant to the scene — a vague sense of familiar shapes,
   without the content — which produces the "faint memory that resolves"
   flavour the user described.

No rows are ever deleted by the reset. History lives in the rows.

### `character_weeks` (per-character reset audit + salience snapshot)

| Column | Type | Notes |
|---|---|---|
| `character_id` | TEXT FK | |
| `week` | INT | 1-based |
| `reset_at` | TIMESTAMPTZ | |
| `retained_node_ids` | TEXT[] | What the ranker kept active |
| `rank_summary` | JSONB | The LLM ranker's full scored list + reasoning (audit trail) |
| `profile_version` | INT | Which profile version this week ran on / produced |
| `edges_broken` | INT | Count |
| `edges_relearned` | INT | Count (re-established during the week, tallied at next reset) |
| `notes` | TEXT | One-paragraph human-readable summary of the character's state after reset |
| PK | (character_id, week) | |

### `generation_jobs` / `generation_artifacts` pattern

Reuse the *pattern*; create `character_jobs` + `character_artifacts` with the
same shape (status machine, queue, per-stage artifacts) but character-scoped:

- `stage`: `profile` | `knowledge` | `avatar` | `brief` | `all` | `reset` | `ingest`
- `character` name on the job row (maps to `characters.name`)
- artifacts keyed by `(character, stage, artifact_key)`

(Open decision D3 below: standalone tables vs. adding a `domain` column to
the existing 3layer tables. Leaning standalone — the 3layer store is
`services/3layer-generator`-owned and its natural keys are pack/segment.)

## Node kinds (taxonomy — enforce like llm-wiki's tag taxonomy)

| kind | Meaning | Example (Harry) |
|---|---|---|
| `self_concept` | Who they believe themselves to be | `orphan-born-philosopher` |
| `backstory` | Past events that shaped them | `godsley-shelter-abuse` |
| `belief` | A held conviction | `magic-should-not-be-misused` |
| `relationship` | Another person/NPC | `ron-weasley`, `hermione-granger` |
| `world_fact` | What they know about the world | `hogwarts-structure`, `philosopher-stone-legend` |
| `hope` | Forward-looking wish | `belonging-to-a-real-family` |
| `desire` | Want, stronger than hope | `defeat-voldemort` |
| `objective` | Tracked goal — **the only kind with progress** | `pass-third-year-exams` |
| `achievement` | Milestone reached (child of an objective) | `won-quidditch-championship` |
| `event` | Something that happened this week+ | `troll-encounter` |
| `discovery` | New information learned | `mirror-of-erised-shows-wanting` |
| `technique` | Skill/ability they can do | `wingardsium-levia` |
| `fear` | Thing that threatens their goals/self | `dementors` |

Every kind except `objective` is pure content. `objective` additionally
carries (in `content`) `progress: 0..1`, `target`, `status: open|achieved|abandoned` —
the updater's goal-tracking requirement.

## Edge relations

| relation | Meaning |
|---|---|
| `knows` | General familiarity (weak prior) |
| `caused` | One event shaped another |
| `met` | First-encounter link (person ↔ person) |
| `loves` / `fears` / `trusts` / `distrusts` | Relationship-valence edges (usually on `relationship` nodes) |
| `part_of` | Membership (room, house, organization) |
| `milestone_for` | `achievement` → `objective` |
| `supports` / `contradicts` | Belief ↔ belief / belief ↔ world_fact |
| `learned_from` | world_fact/technique → relationship |

Relation is an open vocabulary *constrained by this table*; the validator
rejects unknown values (the generator LLM is instructed to pick from it).

## Profile shape (the `data` JSONB in `character_profiles`)

```yaml
identity:
  name: Harry Potter
  age: 11
  archetype: orphan-born hero, half-blood
appearance:            # human-readable; avatar_params carries the machine form
  hair: black, untidy
  eyes: green
  distinguishing: lightning-bolt scar, round glasses
personality:
  traits:
    - {trait: bravery, strength: 0.9, evidence: ["faced Fluffy", "dove for the snitch"]}
    - {trait: loyalty, strength: 0.85, evidence: ["chess sacrifice"]}
    - {trait: stubbornness, strength: 0.7, evidence: []}
  speech_style: short, blunt, dry humour under pressure
  values: [fidelity, fairness, standing-up-for-the-powerless]
self_image:
  who_i_am: the boy who lived — a burden I didn't ask for
  pride: quidditch, my friends
  shame: being famous for a tragedy
  insecurities: belonging; being defined by the scar
hopes: [a real family, to understand the scar]
desires: [defeat Voldemort, keep my friends safe]
objectives:
  - {name: pass-third-year-exams, progress: 0.2, target: end-of-year}
  - {name: defeat-voldemort, progress: 0.05, target: unknown}
secrets:
  - {name: knows-qui-llusions, told_to: [ron, hermione]}
quirks: [hates being the centre of attention, collects odd balls]
backstory_summary: 3-5 sentences, written in third person
```

**Generator contract:** `evidence` arrays reference source passages; the
profile is an *interpretation* but every claim must be traceable to the source
(the LLM-wiki provenance discipline) — this is what keeps the generator from
inventing a person out of thin air.

`core` (in `characters`, the locked set) is generated as: every
`self_image.who_i_am` statement + the top-3 personality traits + the
first-person identity claim, if present. The user can edit `characters.core`
directly; the updater is contractually forbidden to drift it.

## Generator pipeline (extensible per requirement)

**Stage registry** — each stage is a module under
`app/character/stages/` implementing one interface:

```python
class Stage(Protocol):
    name: str                      # 'profile' | 'knowledge' | 'avatar' | 'brief' | ...
    depends_on: list[str]          # stage names whose artifacts I need
    def run(self, ctx: StageContext, character: CharacterRow,
            inputs: dict[str, object]) -> StageOutput: ...
    def validate(self, output: object) -> list[Problem]: ...  # collect, never raise
```

`StageContext` carries: LLM client (per-stage profile like 3layer's
`build_llm(profile, layer)`), `store`, config dict, progress + cancel
callbacks (same shape as `generation_store`'s progress/LLM-progress hooks).

A registry in `app/character/stages/__init__.py` maps name → class.
**Adding a generation feature = one new stage module + one registry row +
one optional config block. Existing stages untouched.** That is the
"design so we can easily add/modify more generation features" requirement.

Current stages:

| Stage | Input | Output (artifact) | LLM? |
|---|---|---|---|
| `profile` | source text (chunked; map-reduce over long sources) | profile document (JSON, validated against the profile shape) | yes — strongest profile |
| `knowledge` | profile + source text | node/edge set (validated against the taxonomy) | yes |
| `avatar` | profile's `appearance` + name | slider dict (0..1, in-character_schema range) + optional ASCII preview refinement loop | yes (params) + no-LLM preview validation |
| `brief` | profile + top-N active nodes + their edges | the agent ingestion brief (see below) — **deterministic assembly, no LLM** | no |
| `all` | — | runs the chain, materializes `characters` row + profile v1 + graph + avatar + brief | mixed |

The `avatar` stage reuses `app/character_schema.SLIDERS` directly for
validation (range-clamp, unknown-key warn — the existing failure contract
from `docs/character_generator.md`), and may call `character_preview.py`'s
render path to do a 2-3 iteration refinement against the profile's
`appearance` prose (same agent-loop discipline already used for Chadwick).

**Generation is idempotent per (name, source_sha256):** re-running with the
same source upgrades the profile in place (new version) rather than creating
a second character; a `--fresh` flag forces a new character row.

## Updater pipeline (weekly)

Runs per-character. Deterministic code owns structure; LLM owns *judgment*;
a validator enforces both. Two phases, one LLM call boundary between them:

**Phase 1 — collect (no LLM).** From the `messages` table + campaign
runtime history for the week, gather the experience stream: every
narration/dialogue beat the character participated in, every event/scene,
chat interactions. Raw, unfiltered. (Read-only; the updater never writes to
`messages`.)

**Phase 2 — judge (LLM, one call with a strict JSON contract):**

Given: the experience stream, the character's current profile vN,
active node names + edge list, dormant node names (names only).
Produce:
1. **retained_set** — the week's most significant additions: which
   experiences become new nodes (with kind + content + proposed edges),
   which are too minor to even become nodes, and **which existing
   edges/nodes get kept active over reset** (the top-N).
2. **profile_diff** — version N+1: which traits/strengths changed and by
   how much (bounded deltas, e.g. max ±0.15 per step per trait), which
   `self_image` lines changed, which `objectives` advanced
   (progress delta + rationale), new `secrets`/`quirks`/achievements.
   Every diff row carries an `evidence` pointer into the experience stream.
3. **relearning** — which dormant nodes/edges the week's experience
   re-activated (name + strength).
4. **new_objectives_or_milestones** — per the goal-tracking requirement.

**Phase 3 — apply (no LLM, transactional):**
- New nodes upserted with `origin: experience:week-W-...`, `first_known_week=W`.
- Retained set marked `active=TRUE` (already true) or flipped on relearn.
- Everything else: the reset sweep (deactivate non-core nodes + break
  non-protected edges) — one UPDATE statement batch, audited into
  `character_weeks`.
- Profile version N+1 written with the diff as `change_notes`; **core rows
  from `characters.core` are restored verbatim** (guard: assert no core
  trait strength changed by more than an epsilon; if the LLM touched one,
  revert that row and log a WARN — the lock is in code, not in the prompt).
- Objective progress clamped 0..1; ≥1.0 flips `status: achieved` + creates
  the `achievement` node + a `milestone_for` edge.

**The reset is a pure function of (state, week) — deterministic, replayable,
testable with a fake LLM** (the same discipline as `CampaignRuntime.reset()`).

## Agent ingestion (what a launched character actually reads)

`brief` stage + a runtime `load_character_brief(character_name)` in
`app/character/loader.py` assembles, at worker startup:

1. **Identity block** — profile vN (current): identity, personality,
   self_image, hopes/desires, speech_style.
2. **Known world** — the active subgraph: active nodes with content, active
   edges as `[[src]] --relation--> [[dst]]` lines (the Obsidian shape,
   directly legible to an LLM).
3. **Faint memories** — the names only of dormant nodes (grouped by kind),
   with the instruction: "these are familiar shapes you half-remember and
   cannot quite place — when a situation touches one, you may reach for it,
   but you have no details yet." (The re-learning affordance.)
4. **Objectives** — with current progress.
5. **Locked core** — stated as inviolable: "these are who you are; no
   experience changes them."
6. **Avatar** — `avatar_params` consumed by the worker's avatar provider
   (existing wiring: `config/workers/*.yaml` `avatar.termgl_avatar.character_params`
   accepts exactly this dict shape).
7. **Behaviour contract** — how the character should act as a virtual
   tuber: speak in 2nd person, keep to its speech_style, reference its
   knowledge graph naturally, never claim to remember a dormant node's
   content, let re-learned relationships surface as warm surprise.

The brief is regenerated from Postgres at worker boot — the worker never
ships a stale copy. (Open decision D2: brief at boot vs. refresh on
`character_reset` bus message.)

## Module layout

```
app/character/
  __init__.py              # public API: generate_character, run_weekly_reset, load_character_brief
  store.py                 # CREATE_TABLE_SQL + all SQL (psycopg2, per project convention)
  schema.py                # node/edge taxonomy validators + profile shape validator (collect-problems, never raise — campaign.validator style)
  pipeline.py              # StageContext, Stage protocol, StageRegistry, job dispatch (runner.py pattern)
  generator.py             # generate_character(name, source, stages=['all'])
  updater.py               # run_weekly_reset(character, week); the deterministic apply-phase
  loader.py                # load_character_brief() — worker-facing
  cli.py                   # operator surface (campaign/cli.py style)
  stages/
    __init__.py            # registry
    profile.py
    knowledge.py
    avatar.py
    brief.py
tests/
  test_character_store.py
  test_character_schema.py
  test_character_pipeline.py
  test_character_updater.py    # fake LLM — the reset semantics are all here
  test_character_loader.py
  test_character_avatar.py     # slider output validates against character_schema
  test_character_brief.py
```

Config: `config/character.yaml` (new top-level section in
`config/worker.yaml` is the template):

```yaml
character:
  stages:
    profile:    { profile: character_profile_strong }
    knowledge:  { profile: character_knowledge, max_nodes: 40 }
    avatar:     { profile: character_avatar, refine_iterations: 2 }
  reset:
    retained_top_n: 8
    max_trait_drift_per_week: 0.15
    core_lock: true
  experience:
    source: messages            # the bus log
    messages_types: [narration, dialogue, campaign_event, chat]
```

## Conventions to honour (from CLAUDE.md / database_schema.md)

- Python: project-local `.venv` only (the PreToolUse hook enforces it).
- Structured logging with correlation IDs (character + week) on every
  pipeline step; LLM calls logged with model + prompt-length, never content.
- Validators collect all problems into a report, never raise (campaign style).
- Tests: pytest, LLM mocked, `ON CONFLICT` upserts, no live DB in unit tests.
- Update `docs/database_schema.md`, `docs/sql/02_create_tables.sql`, and
  `CHANGELOG.md` the moment a table ships.
- One `docs/character_generator.md` update (the existing doc is the *avatar*
  generator only) or a new `docs/character_system.md` — the latter, to avoid
  two different things called "character generator" in two docs.

## Open decisions (need user input)

- **D1 — Reset trigger.** Manual (`cli.py reset --character harry --week 2`)
  vs. automatic on a `campaign_reset` bus message vs. a scheduled job.
  Recommend: manual first (the campaign runtime's `reset()` seam can call it
  later), with a `--dry-run` that prints what would break before committing.
- **D2 — Brief freshness.** At worker boot only (simplest, matches the
  existing cast-YAML flow) vs. push-refresh on reset. Recommend: boot only
  for v1; the bus-message refresh is a small seam left (like
  `CampaignRuntime`'s `carry`).
- **D3 — Jobs tables.** Standalone `character_jobs`/`character_artifacts`
  (my lean) vs. adding a `domain TEXT` column to the 3layer tables. Standalone
  keeps the 3layer service's store untouched — matches the "never modify a
  shared utility for one project's needs" rule.
- **D4 — First character to run end-to-end.** `harry` (source in
  `sourceworks/`, pack already exists in `campaigns/hptest`) is the obvious
  pilot — it also exercises the multi-character + relationship-edge paths
  that a single character wouldn't.

## Build order (suggested)

1. `store.py` + `schema.py` + tests (tables + taxonomy, no LLM)
2. `pipeline.py` + `stages/profile.py` + `stages/knowledge.py` with fake LLM
3. `updater.py` (deterministic apply + fake-LLM judge) + reset tests
4. `stages/avatar.py` (reuses character_schema; preview-loop optional)
5. `stages/brief.py` + `loader.py` + worker config wiring
6. `cli.py` + config/character.yaml + docs (database_schema, sql, CHANGELOG)
7. Pilot: generate `harry` from `sourceworks/`, run one weekly reset
   end-to-end with the `hptest` pack, inspect the knowledge pane data
