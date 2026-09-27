# ashiorid_office: character file schema (OB-10)

This is the one shared contract for OB-10a/b/c and OB-20. Do not change a key name without
updating this file.

## `campaigns/ashiorid_office/cast/<id>.yaml`

This is the pack cast file. The pack loader reads the first five keys and ignores the rest.

```yaml
name: "Full Name"                 # required
archetype: "short phrase"
voice: tenor_high                 # a key of config/voices.yaml `voices:` (see table below)
avatar: null                      # filled later by OB-20 (character_params)
system_prompt: |                  # 150–350 words, second person ("You are ...")
  ...
# ---- agent_dnd §6.2 extension ----
seat: tuber_3
office_role: engineer             # OfficeRole value (app/office/roles.py)
wants: ["...", "..."]             # 2–4 short strings
fears: ["...", "..."]             # 2–4
speech: "how they talk: rhythm, tics, vocabulary, what they never say"
relationships:                    # keyed by the other cast ids; only pairs that matter (>=3)
  tech_lead: "one line"
knowledge: ["kebab-node-name", ...]   # node names this character may draw on (subset of backstory_nodes)
turn_order_pos: 3                 # 0..7, the same as the seat number
```

Rules for `system_prompt`. All of these are mandatory:

- **Rank and chain.** State who they take orders from and who they may direct. Use the table in
  `.claude/prompts/office_campaign_plan.md` §2 and `app/office/roles.py`. Two special cases:
  - Engineer → Tester is for test requests only.
  - The Tester reports to the Tech Lead.
- **Lane.** State the part of the Fraud-Stop repo they may change. This is build plan E3:

  | Role | Lane |
  |---|---|
  | ceo | issues only |
  | analyst | `docs/requirements/` |
  | tech_lead | `docs/design/`, plus reviewing and merging PRs |
  | engineer | `src/` |
  | tester | `tests/` |
  | marketing | `marketing/` |
  | office_manager | `CHANGELOG.md`, dependency files, stale-branch cleanup |
  | party_member | none |

- **Loop.** Include the literal sentence: "Never state or imply that time repeats."
- **No meta.** Never mention simulation, AI, loops, a script or a stream.
- **Party Member only.** Include the literal sentence: "You never speak. You only observe."

## `campaigns/ashiorid_office/profiles/<id>.yaml`

This is the v4 baseline, loaded into the memory DB later (OB-41).

```yaml
id: engineer                      # same as the cast id / OfficeRole value
retains_fragments: true
is_main: true
identity:
  full_name: "..."
  age: 31
  pronouns: "she/her"
  title: "Engineer"
  seat: tuber_3
  tenure_months: 24               # MUST match the lore tenure table below
  home: "where and with whom they live"
  commute: "..."
  hobbies: ["..."]
appearance: |                     # 80–200 words. Concrete cues for avatar sliders: face shape,
  ...                             # build, apparent age, skin/hair/eye colouring, hairstyle,
                                  # glasses/accessory, signature clothing, one distinctive feature
personality:
  traits: ["...", "..."]          # 4–6
  speech_tics: ["..."]
  work_style: "..."
  stress_response: "..."
objectives:
  wants: ["..."]
  fears: ["..."]
  secrets: ["..."]                # things they hide from colleagues (believed-layer secrets)
backstory:
  believed: |                     # FIRST PERSON, 600–1200 words. The only layer the character sees.
    ...
  truth: |                        # GM-only, short, may be mostly open. No loop mechanics yet.
    ...
backstory_nodes:                  # 8–20 permanent nodes
  - name: trusts-tech-lead        # regex ^[a-z0-9]+(-[a-z0-9]+){1,7}$, first word in the verb allowlist
    statement: "I trust the Tech Lead to ..."   # first person
behaviour_contract:
  - "Never state or imply that time repeats."
  - "..."
```

Verb allowlist (v4 §10): `knows|lives|wants|fears|trusts|likes|dislikes|believes|remembers|is|has|can|cannot|owes|suspects|hopes`.

In node names, refer to other characters by role id with hyphens (`tech-lead`, `office-manager`,
`party-member`), never by personal name. Names can change; roles don't.

## Canon fixed by lore (OB-01, `campaigns/ashiorid_office/lore/`)

Tenure:

| Role | Tenure |
|---|---|
| Tech Lead | 8 years (96 months, hire #4) |
| Office Manager | 5 years (60 months) |
| Analyst | 3 years (36 months) |
| Engineer | 2 years (24 months) |
| Tester | 18 months |
| CEO | 14 months |
| Party Member | 13 months |
| Marketing | 10 months |

Other fixed facts:

- The company is 9 years old.
- Previous CEOs:
  - Oswin Harrowgate, the founder. Left after 5 years, "stepping back".
  - Delphine Maro-Kest. Left in her 3rd year after "the long call".
- The Office Manager is female. The Party Member is male ("this guy"). The CEO is male ("this
  guy runs the office").

## Voices (from `config/voices.yaml`)

| Role | Voice |
|---|---|
| ceo | baritone_mid |
| tech_lead | bass_low |
| analyst | alto_warm |
| engineer | tenor_high |
| tester | tenor_low |
| marketing | baritone_soft |
| office_manager | alto_bright |
| party_member | narrator_plain |

`narrator_warm` is reserved for stage narration.

Gender follows the Piper voice models:

| Voice | Speaker | Gender | Role |
|---|---|---|---|
| alto_bright | kathleen | female | office_manager |
| alto_warm | kristin | female | analyst |
| tenor_high | ryan | male | engineer |
| tenor_low | danny | male | tester |

For the other roles, the cast bible decides and the choice must suit the voice. The CEO and
the Party Member are male.
