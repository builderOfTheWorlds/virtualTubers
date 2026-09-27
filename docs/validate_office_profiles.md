# validate_office_profiles

## Overview

`scripts/validate_office_profiles.py` checks the eight ashiorid_office character files (OB-10c)
against the contract in `campaigns/ashiorid_office/profiles/_SCHEMA.md`. For every office role id
(`ceo`, `tech_lead`, `analyst`, `engineer`, `tester`, `marketing`, `office_manager`,
`party_member`), it validates `cast/<id>.yaml` and `profiles/<id>.yaml`.

**Errors** (these fail the run):

- Either file is missing, can't be parsed, or isn't a mapping.
- A required key is missing or has the wrong type (the key set comes from the schema).
- In the cast file:
  - `seat` must equal `office.roles.SEAT[role]`.
  - `turn_order_pos` must equal the seat number.
  - `office_role` must be a valid `OfficeRole` and must equal the file id.
- The cast `voice` must exist in `config/voices.yaml` `voices:` and must equal the schema's voice
  table.
- The profile `id` must equal the file id, and `identity.seat` must equal the role's seat.
- `identity.tenure_months` must equal the lore tenure table.
- Each `backstory_nodes[].name` must match `^[a-z0-9]+(-[a-z0-9]+){1,7}$`, and its first word must
  be in the verb allowlist. A character can't use the same node name twice.
- Every cast `knowledge` entry must be one of that character's `backstory_nodes` names.
- Every `relationships` key must be another office cast id, never the character's own id.
- `system_prompt` must contain `Never state or imply that time repeats.`. For `party_member` it
  must also contain `You never speak. You only observe.`.
- Banned words are matched as case-insensitive whole words in `system_prompt` and
  `backstory.believed`: simulation, simulated, loop, looping, time loop, AI, artificial
  intelligence, stream, script, NPC. Whole-word matching means `loophole`, `Aiden` and `scripted`
  don't count.
- Any `*.yaml` in `cast/` or `profiles/` that isn't an office id (and doesn't start with `_`) is
  a pack error.
- The voice registry can't be read (also a pack error).

**Warnings** (reported, but the run still passes):

- Word counts outside these bounds:

  | Field | Words |
  |---|---|
  | `backstory.believed` | 600–1200 |
  | `appearance` | 80–200 |
  | `system_prompt` | 150–350 |

- List sizes outside these bounds:

  | Field | Entries |
  |---|---|
  | cast `wants` | 2–4 |
  | cast `fears` | 2–4 |
  | `relationships` | at least 3 |
  | `personality.traits` | 4–6 |
  | `backstory_nodes` | 8–20 |

- `behaviour_contract` doesn't contain the "time repeats" sentence.

The schema tables are copied into the script as constants: `VOICE_FOR`, `TENURE_MONTHS`, `VERBS`
and `BANNED_WORDS`. If you change `_SCHEMA.md`, update them to match.

## Signature

```python
def validate_pack(pack_dir: str | pathlib.Path,
                  voices_path: str | pathlib.Path | None = None) -> dict
def validate_character(pack_dir: pathlib.Path, cid: str, voice_names: set[str] | None) -> dict
def banned_hits(text: str | None) -> list[str]
def load_voice_names(voices_path) -> set[str] | None
def format_report(report: dict) -> str
def main(argv: list[str] | None = None) -> int
```

CLI:

```
validate_office_profiles.py [--pack DIR] [--voices FILE] [--json]
```

## Parameters

| Name | Type | Req. | Default | Notes |
|---|---|---|---|---|
| `--pack` / `pack_dir` | path | no | `campaigns/ashiorid_office` | Pack directory that contains `cast/` and `profiles/`. |
| `--voices` / `voices_path` | path | no | `config/voices.yaml` | Voice registry. The script reads its `voices:` mapping keys. |
| `--json` | flag | no | off | Prints a JSON report instead of the text report. |

## Return Value

`validate_pack` returns:

```python
{"pack": str, "ok": bool, "errors": int, "warnings": int,
 "pack_errors": [str],
 "characters": {cid: {"errors": [str], "warnings": [str]}}}
```

`main` returns the process exit code: `0` when there are no errors (warnings are allowed), `1`
otherwise.

## Dependencies

- PyYAML
- `app/office/roles.py`: `OfficeRole`, `SEAT`. The script adds `app/` to `sys.path` itself.
- `config/voices.yaml`
- The standard library: `argparse`, `json`, `logging`, `re`, `uuid`.

## Usage Examples

```bash
.venv/Scripts/python.exe scripts/validate_office_profiles.py
# engineer        FAIL  (1 errors, 1 warnings)
#     ERROR identity.tenure_months 12 != lore 24
#     WARN  appearance is 42 words (expected 80-200)
# Result: FAIL — 1 errors, 1 warnings
```

```bash
.venv/Scripts/python.exe scripts/validate_office_profiles.py --pack campaigns/ashiorid_office --json | jq '.characters.tester'
```

```python
import importlib.util
spec = importlib.util.spec_from_file_location("vop", "scripts/validate_office_profiles.py")
vop = importlib.util.module_from_spec(spec); spec.loader.exec_module(vop)
report = vop.validate_pack("campaigns/ashiorid_office")
assert report["ok"], vop.format_report(report)
```

## Error Handling

- YAML parse errors and file-read errors are caught, logged at ERROR, and reported as errors for
  that character. They never raise.
- A missing or unreadable voice registry becomes a pack error. The registry-membership check is
  skipped, but the schema voice-table check still runs.
- Logging is structured (`run_id=… op=… key=value`): TRACE on entry and exit, DEBUG at decision
  points, INFO once with the summary.

## Changelog

- **v1.0.0** (2026-09-27): Initial version (OB-10c).
