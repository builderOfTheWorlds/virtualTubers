# Campaign: Ashiorid — The Ten Thousandth Anniversary

## Overview

The first campaign pack, and the reference implementation of the
[pack format](../campaign_pack_format.md). It is the opening arc of a fantasy
tabletop show: four strangers are invited to a party ten thousand years in the
making, the party is attacked, and the survivors learn they were manufactured.

Source material is a 137-note Obsidian vault at
`~/codeProjects/ashioridCampaign/DnD Campaign/`. This pack is a **curation**, not
an export — the vault is GM notes, this is a script written to be heard.

Pack lives at [campaigns/ashiorid/](../../campaigns/ashiorid/).

## Cast

| id | Who | Voice | The thing they don't know |
|---|---|---|---|
| `gm` | **Ashiorid**, the narrator | `narrator` | — |
| `buffalo` | Half-orc paladin, Oath of Vengeance | `gruff` | That the people irritating him are his siblings |
| `helen` | Human sorcerer, born **Alcinoe** | `bright` | That her village, her family and her name are all fabricated |
| `carl` | Human ranger, 32 | `dry` | That the wood elves who saved him were sent |
| `drokki` | Dwarf runeseeker of Vabokedos | `deep` | That his apprenticeship was the only placement that looked ordinary on purpose |

Enabled primitives: `roll_check`, `cast_spell`, `attack`, `move_to`, `search`,
`reveal_memory`. The cyberpunk verbs are deliberately absent — a second campaign
enables its own without touching any code.

## Scene graph

```
invitation
    │
the-age-of-war ──── success ──▶ magic-retained ─┐
    │  (default: success)                       │
    └───────────── failure ──▶ magic-lost ──────┤
                                                ▼
                                          letos-manor
                                                │
                                          party-attack ──── failure ──▶ burn-it-down
                                                │  (default: success)        │
                                                └──── success ───────────────┤
                                                                             ▼
                                                                   grovley-revelation
                                                                             │
                                                                        the-vault
                                                                             │
                                                                     portal-encounter  ■
```

`portal-encounter` has no `default_next` — the arc ends on the tear opening.
The weekly-loop reset hangs off `CampaignRuntime.reset()` and the `carry` dict,
not off a scene edge.

### The two forks

**`the-age-of-war`** is the real one. It replays the Event ten thousand years
before the rest of the arc and decides what kind of world the show is set in:

- `success` → **`magic-retained`**: the seal holds, magic survives thin and
  rationed. Helen is *rare*.
- `failure` → **`magic-lost`**: the well empties, magic dies out of the age.
  Helen is *impossible*, which is a much better question.

Both reconverge on `letos-manor`, so the fork changes the world's premise and
every later scene's subtext without duplicating a single downstream scene. The
`Age of War` vault note already had this as a written "Two Outcomes" section;
the pack just makes it executable.

**`party-attack`** is the pacing fork. `success` reaches Grovley directly;
`failure` routes through **`burn-it-down`**, where the holding spell lasts a beat
too long, most of the three hundred guests do not get out, and the party is
driven down the servants' stair instead of walking. Same destination, higher
cost, and the sound of it is meant to be carried into the rest of the arc.

Weights (`3`/`2` on `party-attack`, `1`/`1` on `the-age-of-war`) only apply under
`--seed`; scripted play always takes `default_next`.

## Beat plan

| Scene | Does |
|---|---|
| `invitation` | Cold open. The card, the impossible delivery, four strangers reacting in character. Ends by handing off to the flashback. |
| `the-age-of-war` | The Event, told straight. Helen's arcana check is the hinge. |
| `magic-retained` / `magic-lost` | Three-to-four beats each. Establish the premise, let Helen react to what she now is. |
| `letos-manor` | The ball. Drokki reads the joinery, Carl counts the exits, Buffalo says the quiet part. Grovley appears. |
| `party-attack` | Holding spell over three hundred people, Leto killed standing up, *"Burn it down, we will search through the ashes."* Doors already barred from outside. |
| `burn-it-down` | The cost of the failed fork. Drokki reads the building's bones; they go down, not out. |
| `grovley-revelation` | The Begene Sisters' program. Carl's memory of four cribs. *"Leto was father to all of them. To all of you."* |
| `the-vault` | The magic word, the thing holding back the 86th dimension, and the signet ring cut in half. |
| `portal-encounter` | The dying wizard lands his last syllable as Carl's arrow lands. The tear opens; fingers widen it from inside. |

## Lore

[campaigns/ashiorid/lore/](../../campaigns/ashiorid/lore/) holds three curated
notes the GM may cite: `the-event.md`, `moonwells.md`,
`the-begene-program.md`. `the-event.md` documents both branch outcomes as canon —
the GM cites whichever the current loop took, and **never both in one loop**.

## Verifying

```bash
PYTHONPATH=app .venv/bin/python app/campaign/cli.py --pack campaigns/ashiorid --validate
# ok

PYTHONPATH=app .venv/bin/python app/campaign/cli.py --pack campaigns/ashiorid \
    --dry-run --no-pace --no-color                      # canon path, 8 scenes

PYTHONPATH=app .venv/bin/python app/campaign/cli.py --pack campaigns/ashiorid \
    --scene party-attack --force-branch failure --dry-run   # via burn-it-down

PYTHONPATH=app .venv/bin/python app/campaign/cli.py --pack campaigns/ashiorid \
    --force-branch failure --dry-run --no-pace              # via magic-lost too
```

The pack validates with zero errors and zero warnings: every scene is reachable,
every scene has beats, every cast member speaks.

## Open threads (deliberately unresolved)

- Who ordered the manor burned, and who holds the other half of the ring.
- What the shape in the vault actually is, and why Helen has dreamed it.
- Whether Grovley knew the attack was coming.
- Whether the party were assembled *for* the vault, or *by* whoever wants it open.

## Changelog

- **v1.0.0** (2026-08-16) — opening arc: 10 scenes, 5 cast, 2 forks, 3 lore notes.
