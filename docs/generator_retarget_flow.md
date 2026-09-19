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
