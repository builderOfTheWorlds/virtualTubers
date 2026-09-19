# 3-Layer Generator: As-Built vs. Corrected

Companion visual for `.claude/prompts/generator_retarget_scenes_plan.md`.

---

## Diagram 1 — What it does today (the mistake)

```mermaid
flowchart TB
    subgraph SRC["SOURCE MATERIAL (human only)"]
        VAULT["Obsidian vault<br/>~/codeProjects/ashioridCampaign"]
        HUMAN["HUMAN authors by hand"]
        VAULT -->|"read manually"| HUMAN
    end

    subgraph PACK["CAMPAIGN PACK — campaigns/ashiorid_1/"]
        SCENES["scenes/*.yaml<br/>49 files: 15 spine + 34 ambient<br/>NEVER GROWS"]
        CAST["cast/*.yaml"]
        LORE["lore/*.md"]
    end

    HUMAN -->|"hand-written YAML"| SCENES

    subgraph GEN["3-LAYER GENERATOR — utilities/3LayersWeeklyGeneration/"]
        L1["Layer 1: plan_arc.py<br/>28 segments x 6h<br/>+ ring.py composition"]
        L2["Layer 2: plan_segment.py<br/>recursive slot tree"]
        L3["Layer 3: generate_segment_dialogue.py<br/>calls improviser OFFLINE"]
        L1 -->|arc_plan.yaml| L2
        L2 -->|"brief.yaml + tree"| L3
    end

    SCENES -.->|"READ ONLY<br/>arc can only SCHEDULE<br/>existing scenes"| L1
    CAST -.-> L2
    LORE -.-> L2

    GENOUT["campaigns/ashiorid_1/generated/<br/>2,671 frozen dialogue takes<br/>never passes load_pack()"]
    L3 -->|"frozen words"| GENOUT

    subgraph AIR["AIR TIME"]
        REND["renderer.py<br/>variant pools + improv"]
        IMPROV["improviser.py<br/>live LLM"]
        BRIDGE2["build_generated_episode.py"]
        EP["Episode store (Postgres)"]
        TTS["TTS -> ffmpeg -> Twitch"]
        REND --> IMPROV
    end

    SCENES -->|"build_campaign_episode.py"| EP
    GENOUT --> BRIDGE2 --> EP
    EP --> TTS
    SCENES --> REND
    IMPROV --> EP

    style SRC fill:#3a2a1a,stroke:#c08040,color:#fff
    style PACK fill:#1a2a3a,stroke:#4080c0,color:#fff
    style GEN fill:#3a1a1a,stroke:#c04040,color:#fff
    style AIR fill:#1a3a2a,stroke:#40c080,color:#fff
    style GENOUT fill:#5a1a1a,stroke:#ff4040,color:#fff
    style L3 fill:#5a1a1a,stroke:#ff4040,color:#fff
```

**The three problems this picture shows:**

1. **`scenes/` has no inbound arrow from the generator.** The only way the
   pack grows is the dotted human path on the left. The generator reads
   scenes and never writes them — `arc_schema.py:284` rejects any scene id
   not already in the pack.
2. **Layer 3 (red) duplicates the air-time path.** `generate_segment_dialogue.py`
   calls the same improviser that `renderer.py` calls, but offline, freezing
   words that were designed to vary on every airing.
3. **`generated/` bypasses `load_pack()`.** Two separate bridges into the
   episode store, and the generated one carries content no validator ever saw.

---

## Diagram 2 — The corrected pipeline

```mermaid
flowchart TB
    subgraph SRC["NEW SOURCE MATERIAL"]
        VAULT["Obsidian vault / plot notes / lore"]
    end

    subgraph GEN["3-LAYER GENERATOR — retargeted"]
        L0["Layer 0: ingest_source.py<br/>NEW: digest + candidate lore"]
        L1["Layer 1: plan_arc.py + ring.py<br/>plans arc AND declares<br/>new_spine proposals"]
        L2["Layer 2: plan_segment.py<br/>slots = SCENE SPECS"]
        L3["Layer 3': author_scenes.py<br/>REPLACES dialogue layer<br/>writes scene YAML"]
        L0 --> L1
        L1 -->|"arc_plan.yaml<br/>+ new_spine[]"| L2
        L2 -->|"brief.yaml + tree"| L3
    end

    VAULT --> L0

    STAGE["output/proposed_scenes/<br/>STAGING - not the pack yet"]
    L3 --> STAGE

    GATE{"pack_gate.py<br/>load_pack() on overlay<br/>lore stems? cast ids?<br/>primitives? reachability?"}
    STAGE --> GATE
    GATE -->|"PackError - rejected<br/>with offending scene id"| REJECT["rejected, not written"]

    PROMOTE{"--promote<br/>explicit human step"}
    GATE -->|clean load| PROMOTE

    subgraph PACK["CAMPAIGN PACK — campaigns/ashiorid_1/"]
        SCENES["scenes/*.yaml<br/>spine + ambient<br/>THIS NOW GROWS"]
        CAST["cast/*.yaml"]
        LORE["lore/*.md"]
        CHAIN["spine_chain.py<br/>wires default_next"]
    end

    PROMOTE -->|"new scene files"| SCENES
    PROMOTE -->|"new lore notes"| LORE
    PROMOTE --> CHAIN
    CHAIN -->|"edits prev scene's<br/>default_next"| SCENES

    SCENES -.->|"context: existing<br/>scenes, ring roles"| L1
    CAST -.-> L3
    LORE -.-> L3

    subgraph AIR["AIR TIME — unchanged, always was right"]
        REND["renderer.py<br/>variant pools cycle"]
        IMPROV["improviser.py<br/>live LLM per airing"]
        EP["Episode store"]
        TTS["TTS -> ffmpeg -> Twitch"]
        REND --> IMPROV --> EP --> TTS
    end

    SCENES -->|"build_campaign_episode.py<br/>THE single bridge"| REND

    LEGACY["generated/ 2,671 takes<br/>FROZEN - legacy only"]

    style SRC fill:#3a2a1a,stroke:#c08040,color:#fff
    style GEN fill:#2a1a3a,stroke:#8040c0,color:#fff
    style PACK fill:#1a2a3a,stroke:#4080c0,color:#fff
    style AIR fill:#1a3a2a,stroke:#40c080,color:#fff
    style GATE fill:#3a3a1a,stroke:#c0c040,color:#fff
    style PROMOTE fill:#3a3a1a,stroke:#c0c040,color:#fff
    style L3 fill:#2a4a2a,stroke:#40c040,color:#fff
    style LEGACY fill:#2a2a2a,stroke:#666,color:#888
```

**What changed, in one line each:**

- **Layer 3 is now green, not red** — it writes scene source, not frozen words.
- **`scenes/` has an inbound arrow from the generator.** The pack grows. That
  was the entire point.
- **A gate sits between the model and tracked source.** Nothing reaches
  `scenes/` without passing the same `load_pack()` the hand-authored content
  passes, and nothing lands without an explicit `--promote`.
- **One bridge to air, not two.** After the change there is only one kind of
  content: authored pack scenes.
- **Dialogue moved back to air time**, where variant pools + `improv: true`
  make wording different on every airing — which is what
  `docs/campaign_content_expansion.md` specified all along.

---

## Diagram 3 — Ring composition, before and after

```mermaid
flowchart LR
    subgraph BEFORE["AS BUILT: ring shapes the SCHEDULE"]
        B1["ring.py<br/>descent / keystone / ascent"]
        B2["arc segment order"]
        B3["which EXISTING scene<br/>airs in which 6h slot"]
        B1 --> B2 --> B3
    end

    subgraph AFTER["CORRECTED: ring shapes the STORY"]
        A1["ring.py<br/>descent / keystone / ascent<br/>UNCHANGED CODE"]
        A2["dramatic role of each<br/>NEW scene to be written"]
        A3["mirror_of + mirror_transform<br/>become AUTHORING instructions:<br/>'mirror scene X, transformed<br/>by cost_revealed'"]
        A4["scene.ring_tone + scene.mood<br/>pack.py:294-330<br/>already wired, unused"]
        A1 --> A2 --> A3 --> A4
    end

    style BEFORE fill:#3a1a1a,stroke:#c04040,color:#fff
    style AFTER fill:#1a3a2a,stroke:#40c080,color:#fff
```

`ring.py` needs **no changes**. The ring spec is deliberately campaign-agnostic
*shape*, and shape belongs to composition rather than scheduling. `Scene` in
`app/campaign/pack.py:294-330` already carries `ring_tone` and `mood` fields
that nothing currently populates — they were built for exactly this direction
and are waiting.

---

## Diagram 4 — Pacing: tempo as a dial (plan §6A)

![Pacing model](generator_pacing_model.png)

Regenerate with
`.venv/bin/python .claude/prompts/render_pacing_model_graph.py`.

```mermaid
flowchart LR
    subgraph OLD["AS BUILT — budget from LLM throughput"]
        O1["measured_baseline<br/>words_per_take = 105<br/>a GPU throughput number"]
        O2["derive_target_slots()<br/>segment_schema.py:66"]
        O3["slot count per node"]
        O4["takes generated, FROZEN"]
        O5["168h assumed,<br/>never verified"]
        O1 --> O2 --> O3 --> O4 --> O5
    end

    subgraph NEW["CORRECTED — budget from story duration"]
        N1["ring phase<br/>descent / keystone / ascent"]
        N2["spine_share_by_phase<br/>0.15 / 0.45 / 0.30"]
        N3["minutes budget per segment<br/>spine_minutes + ambient_minutes"]
        N4["spine scenes sized by NEED<br/>validated vs min/max"]
        N5["ambient STRETCHES<br/>to fill the remainder"]
        N6["168h is an OUTPUT<br/>~450 scene files"]
        N1 --> N2 --> N3 --> N4 --> N5 --> N6
    end

    style OLD fill:#3a1a1a,stroke:#c04040,color:#fff
    style NEW fill:#1a3a2a,stroke:#40c080,color:#fff
```

**The inversion in one line:** the old chain asked *how many words does a model
emit per call*; the new chain asks *how long should this scene be*. Only the
second is an editorial question, and only the second can be paced.

**Why ambient elasticity matters more than it sounds.** Because ambient
stretches, spine density becomes a free variable — and tying it to ring phase
turns tempo into a dial. Descent breathes at 15% spine; the keystone stacks
events at 45%; ascent quickens at 30%. That is ring composition doing real
work on the *feel* of the week, not just reordering a playlist.

| Phase | Segs | Hours | Spine | Ambient |
|---|---|---|---|---|
| descent | 16 | 96 | 15% — 14.4 h | 81.6 h |
| keystone | 4 | 24 | 45% — 10.8 h | 13.2 h |
| ascent | 8 | 48 | 30% — 14.4 h | 33.6 h |
| **total** | **28** | **168** | **39.6 h** | **128.4 h** |

At 150 wpm that is 1,512,000 words — the 1.5M target arrived at from the story
side, not reverse-engineered from throughput.

**Ambient definitions are not airings.** ~250 definitions x ~10 airings each
covers 128 h, each airing differently worded by variant pools + `improv: true`.
Today's 34 definitions would mean 75 airings each — the same campfire premise
75 times, which no amount of rephrasing disguises.

---

## Diagram 5 — Progressive densification via continuity contracts (plan §6C)

![Densification model](generator_densification_model.png)

Regenerate with
`.venv/bin/python .claude/prompts/render_densification_graph.py`.

Every scene carries `continuity_in` (state it assumes) and `continuity_out`
(state it guarantees). Two scenes chain when the first's outro satisfies the
second's intro — so chaining is a **contract**, not a hard edge, and new
content can be slotted between any two scenes later without regenerating
either neighbour.

**The build order this enables:**

| Wave | Spine | Ambient | Status |
|---|---|---|---|
| 1 — generate now | ~40 h | ~130 h | complete, airable, deliberately slow |
| 2 — bridge a gap | +1 scene | shrinks | one ambient stretch becomes plot |
| 3 — keep going | rising | shrinking | density grows, week never rebuilt |

**The seam already exists on one side only.** `arc_schema.py:226` and
`segment_schema.py:253` both already require `continuity_in`/`continuity_out` —
the *planner* reasons in continuity and then discards it, because
`app/campaign/pack.py:317` gives `Scene` only a spoken `enter_narration` and a
bare `default_next` edge. Adding the two fields to the pack format is what lets
that reasoning survive into the scene file, and it is what makes insertion
verifiable.

**Ambient scenes carry `continuity_in` only.** An ambient scene that emits a
`continuity_out` is a validation error — ambient decides nothing, so nothing may
ever depend on it having played. That rule is what keeps ambient injectable
anywhere, and it is the defect most likely to appear in generated ambient.

---

## Diagram 6 — Arc-level tempo control (plan §6D)

```mermaid
flowchart TB
    ARC["ARC PLAN — Layer 1<br/>per segment"]

    subgraph FIELDS["segment fields"]
        H["hours: 6<br/>(existing)"]
        D["spine_density: high<br/>NEW — sparse/normal/high/maximum"]
        SC["spine_scene_count: 12<br/>NEW — explicit override"]
    end

    ARC --> FIELDS

    PHASE["ring phase default<br/>descent 15% / keystone 45% / ascent 30%"]
    PHASE -->|"seeds"| D
    D -->|"default"| SC

    L2["Layer 2 MUST produce<br/>exactly spine_scene_count slots"]
    SC --> L2

    CHECK{"validation"}
    L2 --> CHECK
    CHECK -->|"count mismatch"| RETRY["FAIL + retry<br/>never a logged warning"]
    CHECK -->|"spine minutes > hours"| REJECT["arc plan rejected"]
    CHECK -->|"ambient < 20% floor"| REJECT
    CHECK -->|ok| OK["segment authored"]

    style FIELDS fill:#1a2a3a,stroke:#4080c0,color:#fff
    style CHECK fill:#3a3a1a,stroke:#c0c040,color:#fff
    style RETRY fill:#3a1a1a,stroke:#c04040,color:#fff
    style REJECT fill:#3a1a1a,stroke:#c04040,color:#fff
    style OK fill:#1a3a2a,stroke:#40c080,color:#fff
```

Scene count is directable **from the arc** — give the keystone 12 scenes and a
quiet descent segment 3. Crucially this is a *field*, not prompt wording: a
prompt-only instruction is unverifiable, so a keystone that quietly produced 3
scenes instead of 12 would look identical to one that worked. The count is
checked, and a mismatch retries rather than logging a warning nobody reads.

Two guards keep it honest: spine minutes must fit inside the segment's `hours`,
and every segment keeps a **20% ambient floor** so there is always elasticity
left to absorb a short scene. Zero ambient means any pacing drift becomes dead
air.

---

## Reading it without a Mermaid renderer

**Today:** a human reads the Obsidian vault and hand-writes `scenes/*.yaml`.
The generator reads those scenes, plans a 168-hour arc over them, and then
Layer 3 calls an LLM to write frozen dialogue takes into a separate
`generated/` directory. Those takes bypass pack validation and reach the
stream through their own bridge. The scene library never grows.

**Corrected:** new source material enters at Layer 0. Layer 1 plans the arc
*and* declares which new scenes must exist to fill it. Layer 2 turns each
segment into slot-shaped scene specs. Layer 3' serializes those into real
scene YAML in a staging directory. A gate runs `load_pack()` over the pack
with the proposals overlaid, rejecting anything with a bad lore stem, unknown
cast id, or illegal primitive. Surviving scenes land in `scenes/` only on an
explicit `--promote`, with `spine_chain.py` wiring `default_next` so the new
scenes join the story graph. From then on they are ordinary pack content, and
dialogue is generated fresh at air time by the renderer and improviser — which
is where it belonged from the start.
