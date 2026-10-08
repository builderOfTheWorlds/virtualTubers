# DRAFT (P2.5), awaiting user review

# ashiorid (D&D): character profile schema (P2.5)

These profiles follow the **same schema** as
`campaigns/ashiorid_office/profiles/_SCHEMA.md` (v4 baseline profile + the agent_dnd §6.2
extension fields that the office pack keeps in `cast/`). The D&D `cast/*.yaml` files are left
untouched (the pack loader reads only `name, archetype, voice, avatar, system_prompt`), so the
§6.2 extension fields live **in the profile file** here.

## `campaigns/ashiorid/profiles/<id>.yaml` (player)

```yaml
id: chadwick                      # == cast id (file stem in campaigns/ashiorid/cast/)
retains_fragments: true
is_main: true
table_role: player                # D&D replacement for office_role: player | gm
seat: tuber_1                     # §6.2 extension (office keeps these in cast/)
turn_order_pos: 1                 # == seat number
wants: ["..."]                    # 2–4
fears: ["..."]                    # 2–4
speech: "rhythm, tics, vocabulary, what they never say"
relationships:                    # keyed by the other cast ids (>= 3); start-of-campaign view
  Leena: "one line"
knowledge: [the-event]            # LORE STEMS (lore/*.md basenames) known at campaign start.
                                  # NOTE: differs from office, where knowledge = node names.
identity:
  full_name: "..."
  age: 32                         # int, or null = not in the sources (user to decide)
  pronouns: "he/him"
  title: "class / archetype"
  seat: tuber_1
  home: "..."                     # null if the sources do not say
  hobbies: ["..."]                # sourced from ambient scenes where possible
appearance: |                     # only what the pack states; gaps are marked UNFILLED
personality: {traits: [], speech_tics: [], work_style: "", stress_response: ""}
objectives: {wants: [], fears: [], secrets: []}   # secrets = believed-layer things they hide
backstory:
  believed: |                     # FIRST PERSON. Only what the character could know at the
                                  # moment the invitation card arrives (before scene `invitation`).
  truth: |                        # GM-ONLY. Never enters a player brief.
backstory_nodes:                  # 8–20, believed-layer only; name regex + verb allowlist as office
  - {name: is-a-paladin-of-vengeance, statement: "first person"}
behaviour_contract: ["..."]
```

Dropped vs office: `office_role` (→ `table_role`), `identity.tenure_months`,
`identity.commute` (no workplace). Node names refer to other characters by cast id with hyphens
(`sodacan-bob`, `leena`, `vigil`, `chadwick`).

## `gm.yaml` (U7)

Same player schema, plus optional **GM-only blocks**. The GM context builder (P2.7) renders
whatever blocks exist, in `config/table/ashiorid.yaml: gm_blocks_order`; adding a block is data:

| block | content |
|---|---|
| `truth` | `backstory.truth`: the whole world truth (all lore + spine), incl. per-player truths |
| `style` | narration rules (TTS-first, short declaratives, concrete over atmospheric) |
| `table_rules` | turn/adjudication rules (describe, never decide for players; branch canon) |
| `secrets` | per-player secrets + the scene at which each may surface |
| `unlocks` | scene id → lore stems that become player knowledge there (from scenes' `lore:`) |

## Seats / turn order

`gm=tuber_0`, then `campaign.yaml` `players:` order: `chadwick=tuber_1`, `Leena=tuber_2`,
`Vigil=tuber_3`, `sodacan_bob=tuber_4`. Chadwick opens (he does not tolerate his authority being
questioned in a fight and says the blunt thing first); Leena second (she reacts out loud to what
feels familiar); Vigil third (he speaks after counting the room); Bob last (he will talk anyway).

## The believed/truth invariant

For every player, nothing player-visible (`believed`, `knowledge` lore text, nodes, objectives,
speech, relationships, wants/fears) may contain a truth-only proper noun or key phrase. Checked by
`validate_profiles.py` in this folder:

    .venv/bin/python campaigns/ashiorid/profiles/validate_profiles.py
