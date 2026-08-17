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
    buffalo.yaml
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

The `primitives` whitelist is how one registry serves every genre: the fantasy
and cyberpunk verbs are all registered in
[app/campaign/primitives.py](../app/campaign/primitives.py), and each pack
enables only its own.

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
  - {type: dialogue,  speaker: buffalo, text: "...", improv: true}
  - {type: action,    speaker: helen,   primitive: cast_spell,
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

## Changelog

- **v1.0.0** (2026-08-16) — initial format: pack layout, beat kinds, branch
  selection, `weight` reservation, validation rules, CLI authoring loop.
