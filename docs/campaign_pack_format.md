# Campaign Pack Format

## Overview

A **campaign pack** is a directory of YAML that describes a show: who is in it,
what they say, and where the story can fork. Packs are data, not code — a new
genre (cyberpunk, mining colony) is a new pack, not a new module.

The pack is read by `load_pack` ([app/campaign/pack.py](../app/campaign/pack.py)),
checked by `validate_pack` ([app/campaign/validator.py](../app/campaign/validator.py)),
and played by `CampaignRuntime` through `SceneRenderer`.

## Layout

```
campaigns/<name>/
  campaign.yaml     # metadata, cast roster, enabled primitives, theme
  cast/
    gm.yaml         # one file per cast member; filename = the id used in beats
    chadwick.yaml
    ...
  scenes/
    01-invitation.yaml   # one file per scene; filenames are cosmetic, `id:` is authoritative
    ...
  lore/             # optional; curated background the GM may cite
```

Scene files are loaded in sorted filename order, so numbering them keeps a
directory listing in story order. **The `id:` field inside the file is what the
graph uses** — renaming a file changes nothing.

## campaign.yaml

| Key | Required | Meaning |
|---|---|---|
| `name` | yes | Pack identifier. Must match the `campaign` field in any saved state file. |
| `start_scene` | yes | Scene id the show opens on. Must exist. |
| `gm` | yes | Cast id of the narrator. |
| `title` | no | Display title. |
| `genre` | no | Free text. |
| `players` | no | List of cast ids. Each needs a `cast/<id>.yaml`. |
| `primitives` | no | Whitelist of action verbs this campaign may use. An action beat naming a primitive outside this list is a validation **error**. |
| `theme` | no | Mapping passed through to the renderer's palette. |

The `primitives` whitelist is how one registry serves every genre: the fantasy,
cyberpunk, and office verbs are all registered in
[app/campaign/primitives.py](../app/campaign/primitives.py), and each pack
enables only its own.

Shipped primitives by genre (params: **bold** = required, others optional;
`a|b` = fixed choices). Every verb is pure narration — none decides an outcome
the script didn't supply.

| Genre | Primitive | Params |
|---|---|---|
| fantasy | `roll_check` | **skill**, dc, outcome `success\|failure` |
| fantasy | `cast_spell` | **spell**, target, level |
| fantasy | `attack` | **target**, weapon |
| fantasy | `move_to` | **destination**, manner |
| fantasy | `search` | **target**, detail |
| fantasy | `reveal_memory` | **subject**, detail |
| cyber | `execute_exploit` | **exploit**, target |
| cyber | `scan_target` | **target**, depth |
| office | `assign_task` | **to**, **task**, due |
| office | `write_spec` | **topic**, detail |
| office | `open_ticket` | **title**, priority `low\|medium\|high\|critical` |
| office | `commit` | **message**, branch |
| office | `run_tests` | **suite**, result `pass\|fail` |
| office | `file_bug` | **title**, component, severity `low\|medium\|high\|critical` |
| office | `open_pr` | **title**, branch, reviewer |
| office | `merge_pr` | **pr**, into |
| office | `deploy` | **environment**, version, outcome `success\|failure\|rolled back` |
| office | `pitch` | **idea**, audience |
| office | `brew_coffee` | recipient, strength |
| office | `take_out_trash` | detail |
| office | `hr_notice` | **subject**, audience |
| office | `observe` | target (the Party Member's silent verb) |

## cast/&lt;id&gt;.yaml

| Key | Required | Meaning |
|---|---|---|
| `name` | yes | Display name shown before dialogue. |
| `archetype` | no | Short descriptor. |
| `system_prompt` | no | Persona prompt used when a beat sets `improv: true`. |
| `voice` | no | Voice id handed to the TTS client. |
| `avatar` | no | Avatar id for the expression/bubble state file. |

The `role` (`gm` or `player`) is **not** set in the file — it is derived from
whether the id appears as `gm:` or in `players:` in `campaign.yaml`.

## scenes/&lt;file&gt;.yaml

```yaml
id: party-attack
title: The Party Attack
enter_narration: >-
  The doors open on people who were not invited.

beats:
  - {type: pane,      show: combat}
  - {type: narration, speaker: gm,      text: "..."}
  - {type: dialogue,  speaker: chadwick, text: "...", improv: true}
  - {type: action,    speaker: Leena,   primitive: cast_spell,
     params: {spell: shatter, target: the barred doors, level: 3}}

branches:
  - {id: success, when: {outcome: success, weight: 3}, next: grovley-revelation}
  - {id: failure, when: {outcome: failure, weight: 2}, next: burn-it-down}

default_next: grovley-revelation
```

### Beat kinds

| `type` | Requires | Notes |
|---|---|---|
| `narration` | `text` | `speaker` is conventionally the GM. |
| `dialogue` | `text`, `speaker` | Rendered as `Name: text`. |
| `action` | `primitive` | `params` are validated against the primitive's `ParamSpec`s. |
| `pane` | `show` | Switches the tmux pane (`map`, `party`, `combat`, `inventory`). Inert in `--dry-run`. |

`improv: true` hands the beat's text to the persona LLM as *intent* rather than
a script. Default is `false` — verbatim, deterministic, and free.

### Branches and `default_next`

`default_next` is the **canon path**: the route taken when no branch matches.
Every branchable scene should have one, or the show simply ends there.

A branch is chosen by a `BranchSelector`:

- **`ScriptedSelector`** (default) — takes the first branch whose every `when`
  key matches the runtime context. With an empty context nothing matches, so a
  plain `--dry-run` walks the `default_next` spine end to end.
- **`ForcedSelector`** (`--force-branch ID`) — takes branch `ID` wherever a
  scene has one, and falls back to scripted matching where it does not. This is
  how you walk an alternate route without inventing context.
- **`WeightedRandomSelector`** (`--seed N`) — ignores `when` entirely except for
  the reserved `weight` key, and rolls.

**`weight` is reserved.** It steers `WeightedRandomSelector`, is skipped by
scripted matching, and is validated as a non-negative number at load time so a
stringy YAML value fails on the ground rather than mid-show. Weight `0` makes a
branch unreachable by the random selector while leaving it scripted-reachable.

**Cycles are legal and intentional** — the show is a weekly time loop. Traversal
is bounded by `--max-scenes`, never by cycle detection.

## Validation

`validate_pack` collects **every** problem rather than raising on the first, so
one pass gives the whole list.

**Errors** (exit 1, nothing plays):
dangling branch target or `default_next` · duplicate branch id · unknown beat
kind · unknown speaker · action beat with no primitive, or one outside the
pack's whitelist · narration/dialogue with no text · pane beat with no `show` ·
non-numeric or negative `weight` · `start_scene` not among the scenes.

**Warnings** (exit 0, the show still plays):
unreachable scene · scene with no beats · cast member who never speaks.

## CLI

The campaign CLI is the authoring loop — no Kafka, no tmux, no docker, no TTS.
Modules under `app/` are imported by package name, so run it with `app` on the
path:

```bash
# Is the pack sound?
PYTHONPATH=app .venv/bin/python app/campaign/cli.py --pack campaigns/ashiorid --validate

# What scenes exist?
PYTHONPATH=app .venv/bin/python app/campaign/cli.py --pack campaigns/ashiorid --list-scenes

# Play the canon path in the terminal, instantly
PYTHONPATH=app .venv/bin/python app/campaign/cli.py --pack campaigns/ashiorid \
    --dry-run --no-pace --no-color

# Start mid-arc and take the losing fork
PYTHONPATH=app .venv/bin/python app/campaign/cli.py --pack campaigns/ashiorid \
    --scene party-attack --force-branch failure --dry-run

# Roll the forks, reproducibly
PYTHONPATH=app .venv/bin/python app/campaign/cli.py --pack campaigns/ashiorid \
    --seed 2 --dry-run --no-pace

# Stop after three scenes, then pick up where it stopped
PYTHONPATH=app .venv/bin/python app/campaign/cli.py --pack campaigns/ashiorid \
    --dry-run --max-scenes 3 --state-file /tmp/run.json
PYTHONPATH=app .venv/bin/python app/campaign/cli.py --pack campaigns/ashiorid \
    --dry-run --resume --state-file /tmp/run.json
```

A clean pack prints **nothing** on its way to playing — the first line of a dry
run is the cold open, not a status line. The `ok` verdict belongs to
`--validate` alone.

Exit `0` = played, validated, or listed. Exit `1` = an operator-caused failure
(pack will not load, fails validation, unknown `--scene`, `--resume` with no
`--state-file`, unreadable state file). A genuine bug propagates as a traceback
rather than being disguised as a bad pack.

## Writing for the ear

Every line of `text` is **read aloud by TTS on stream**. Write sentences that
survive one hearing: short, declarative, concrete. A sentence that needs
re-reading is a sentence that fails. Prefer what is in the room over what the
room evokes.

## Adding a campaign

1. `mkdir -p campaigns/<name>/{cast,scenes,lore}`
2. Write `campaign.yaml` with the cast roster and the primitive whitelist.
3. One `cast/<id>.yaml` per member — every id in `gm:`/`players:` needs a file.
4. Write scenes. Wire the spine with `default_next` first, then add branches.
5. `--validate` until clean, `--dry-run` until it reads well aloud.
6. `--force-branch` every alternate route at least once.

No Python is involved at any step.

## Content expansion

The keys that turn a finite pack into 24/7 content — `ambient:` and `prompt:` on
a scene, `lore:` selectors, and variant pools on a beat's `text` — are documented
separately in
[campaign_content_expansion.md](campaign_content_expansion.md), along with the
authoring recipes for each. All of them are optional; a pack using none of them
behaves exactly as this document describes.

## Seats

`seats:` is an optional `campaign.yaml` mapping that pins cast members to tuber
slots — the positional stream seats `tuber_0` … `tuber_7`:

```yaml
seats:
  ceo: tuber_0
  tech_lead: tuber_1
  engineer: tuber_2
```

- Loaded as `CampaignPack.seats` (`dict[str, str]`); `{}` when absent or empty.
- The loader (`load_pack`) raises `PackError` if `seats` is not a mapping or a
  value is not a `tuber_N` string.
- The validator (`validate_pack`, or `check_seats` on its own) reports as
  errors: N outside 0–7, two cast ids sharing a seat, and a seated id that is
  not a loaded cast member (`gm:`/`players:`). A cast member may go unseated.
- `.claude/prompts/build_campaign_episode.py`: when a pack has seats, dialogue
  speakers become their `tuber_N` slot and the episode's `show.slots` lists
  every seated slot, so the episode validator checks each slot-shaped speaker
  is cast. The builder refuses to build if the seat map is invalid. A pack
  with no seats keeps the legacy `SPEAKER_TO_WORKER` worker-id mapping, and its
  output is unchanged.

## Changelog

- **v1.3.0** (2026-09-27) — optional `seats:` cast-to-tuber-slot map (OB-03).
- **v1.2.0** (2026-09-27) — list shipped primitives by genre; add the `office`
  verb set (OB-02, `ashiorid_office`).
- **v1.1.0** (2026-08-17) — cross-reference the content-expansion additions
  (`ambient`, `prompt`, `lore`, variant pools).
- **v1.0.0** (2026-08-16) — initial format: pack layout, beat kinds, branch
  selection, `weight` reservation, validation rules, CLI authoring loop.
