# office.brief_stub

## Overview

`app/office/brief_stub.py` builds the persona prompt that every
ashiorid_office handler sends to the LLM. This is the E6 stub brief from the
build plan (`.claude/prompts/ashiorid_office_build_plan.md`). It has three
parts and nothing else:

1. the cast `system_prompt` from `campaigns/ashiorid_office/cast/<id>.yaml`
2. the `backstory.believed` text from `campaigns/ashiorid_office/profiles/<id>.yaml`.
   `backstory.truth` is GM-only and is never included.
3. today's directive

The v4 `brief.py` will replace this module once the memory DB (WS-F) exists.
Keep `build_persona_prompt` stable, so that swap is a one-import change in
`app/agent_handlers/office.py` (`persona_prompt`).

The id is the OfficeRole value: `ceo`, `tech_lead`, `analyst`, `engineer`,
`tester`, `marketing`, `office_manager` or `party_member`. See
`campaigns/ashiorid_office/profiles/_SCHEMA.md` for the file shapes.

The pack directory is resolved in this order:

1. the `pack_dir` argument (the office handlers pass `agent.office.pack_dir`)
2. the env var `OFFICE_PACK_DIR`
3. `<repo>/campaigns/ashiorid_office`

The Docker image copies only `app/`, so in a container the pack has to be
mounted, and one of the first two has to point at it.

## Signature

```python
def build_persona_prompt(role, directive=None, pack_dir=None,
                         max_backstory_chars=None) -> str
def load_character(role, pack_dir=None) -> dict      # {"id", "cast", "profile"}
def character_id(role) -> str
def resolve_pack_dir(pack_dir=None) -> pathlib.Path
class BriefError(RuntimeError)
```

## Parameters

| Parameter | Type | Required | Default | Notes |
|---|---|---|---|---|
| `role` | `OfficeRole` \| str | yes | none | A role, a role value (`"tech_lead"`) or a seat (`"tuber_1"`). Anything else raises `ValueError`. |
| `directive` | str \| dict \| None | no | `None` | A dict uses `text`, plus optional `title` and `issue`, which render as `"<title> (issue #N)"` above the text. `None` or empty renders `NO_DIRECTIVE_TEXT`. |
| `pack_dir` | str \| Path \| None | no | see Overview | The pack root that holds `cast/` and `profiles/`. |
| `max_backstory_chars` | int \| None | no | `None` | Truncates the believed backstory at a word boundary and appends `" ..."`. |

## Return Value

The prompt is one string:

```
<cast system_prompt>

## Your life, as you remember it
<believed backstory>            (the whole section is left out when the profile or text is missing)

## Today's directive
<directive text>
```

## Dependencies

- `PyYAML` (`yaml.safe_load`)
- `office.roles.as_role`
- stdlib: `logging`, `os`, `pathlib`

## Usage Examples

```python
from office.brief_stub import build_persona_prompt

system = build_persona_prompt("analyst", "Add a velocity rule for bursts of transactions.")
reply = llm_client.complete(system, [{"role": "user", "content": "Write the functional plan."}])
```

```python
# Tests / tools: point at a scratch pack.
prompt = build_persona_prompt(
    "tuber_1",
    {"text": "Ship the country-mismatch rule.", "title": "Country rule", "issue": 12},
    pack_dir=tmp_path / "pack",
    max_backstory_chars=2000,
)
```

## Error Handling

- `BriefError` is raised when:
  - the cast file is missing or unreadable
  - the cast file is not a YAML mapping
  - the cast file has an empty or missing `system_prompt`

  Each case is logged at ERROR first. The office handlers catch it, log
  `event=persona_fallback`, and use `agent.system_prompt` instead.
- A missing or unparseable profile is not an error. It logs a WARNING and the
  brief has no backstory section.
- An unknown role raises `ValueError` (from `office.roles.as_role`).

## Changelog

- v1.0.0 (2026-09-27): Created for OB-21 (E6 stub brief).
