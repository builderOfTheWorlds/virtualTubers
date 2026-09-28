# character.avatar — backstory → avatar (`map_appearance`)

## Overview

`app/character/avatar.py` turns a character profile's written `appearance` and
`personality` (the `campaigns/<pack>/profiles/<id>.yaml` format, see
`campaigns/ashiorid_office/profiles/_SCHEMA.md`) into the flat codec-head
parameter dict defined in `app/character_schema.py`: the 8 `SLIDER_DEFAULTS`
sliders plus an `accent_color` from `ACCENT_COLORS`.

An LLM reads the description and replies with strict JSON. The reply is
extracted, structurally checked, and clamped/validated through
`resolve_params`. A bad reply is retried once, with the error fed back to the
model; if the retry also fails the neutral `PARAM_DEFAULTS` are returned and
the failure is logged at ERROR. `map_appearance` never raises because of a bad
reply.

`scripts/generate_office_avatars.py` runs this over the ashiorid_office cast
(work package OB-20). It writes the params into each cast file, prints a
roundtable roster snippet, and renders preview PNGs.

## Signature

```python
def map_appearance(profile: dict, llm_client) -> dict
def map_appearance_result(profile: dict, llm_client, request_id: str | None = None) -> AvatarResult
def parse_response(text: str) -> dict
def build_user_prompt(profile: dict) -> str

@dataclass
class AvatarResult:
    params: dict        # 8 sliders + accent_color
    source: str         # "llm" | "default"
    attempts: int       # 1 or 2
    errors: list[str]   # one entry per failed attempt

class ReplayLLMClient:
    def __init__(self, responses: str | dict | list): ...
    def complete(self, system_prompt: str, messages: list[dict]) -> str: ...
```

## Parameters

- `profile` (dict, required): a parsed profile YAML. Uses `id`, `identity.full_name`,
  `identity.age`, `identity.pronouns`, `appearance`, and `personality.traits` /
  `work_style` / `stress_response`. The backstory is not sent.
- `llm_client` (required): any object with `complete(system_prompt, messages) -> str`.
  This is the `app/llm_client.py` interface (`OllamaClient`, `ClaudeClient`, from
  `build_llm_client(config)`). Exceptions raised by `complete` count as a failed attempt.
- `request_id` (str, optional): the correlation id in log lines. Defaults to a random 8-hex id.
- `ReplayLLMClient(responses)`: replays canned replies. It takes one reply or a list
  played in order, and the last one repeats. A dict is JSON-serialized first, so it goes
  through the same parse path as real model text.

## Return Value

`map_appearance` returns a complete params dict. Sliders are floats clamped to 0..1 and
rounded to 2 places. `accent_color` is an uppercase palette name. On a double failure it
returns a copy of `PARAM_DEFAULTS`. `map_appearance_result` returns the same dict wrapped
in an `AvatarResult`, which also records the source, the number of attempts and the errors.

### What counts as a bad reply

The reply is bad when any of these is true. Each one triggers the retry:

- it contains no JSON object, or the JSON is invalid, or it is not an object
- any of the 9 keys is missing
- a slider is not a number (a string that isn't numeric, a bool, or null)
- `accent_color` is not one of `BLACK RED GREEN YELLOW BLUE PURPLE CYAN WHITE`

The parser is tolerant of some things that are not failures:

- Markdown fences and surrounding chatter are stripped.
- Out-of-range sliders are clamped.
- Extra keys are dropped with a WARNING.
- Colour case is ignored.

## Dependencies

- `app/character_schema.py`: `SLIDER_DEFAULTS`, `ACCENT_COLORS`, `PARAM_DEFAULTS`, `resolve_params`
- stdlib: `json`, `re`, `logging`, `uuid`, `dataclasses`
- The script also uses `app/llm_client.py` (live path only, imported lazily),
  `app/character_preview.py` + `pixel_raster.py` (rendering), `numpy` and `PyYAML`.

## The generator script

```bash
# Offline: replay canned replies (no model reachable)
.venv/bin/python scripts/generate_office_avatars.py \
    --params-file campaigns/ashiorid_office/profiles/_avatar_params.json

# Live: the llm: block of a worker config, with overrides (e.g. on argyre)
.venv/bin/python scripts/generate_office_avatars.py \
    --config config/workers/roundtable.yaml \
    --base-url http://192.168.1.23:11434 --model qwen2.5:7b-instruct-q4_K_M
```

Flags:

| Flag | Effect |
|---|---|
| `--pack` | Pack directory (default `campaigns/ashiorid_office`). Cast order comes from `campaign.yaml` `seats:`. |
| `--params-file` | JSON `{cast_id: reply}`. Keys starting with `_` are notes. |
| `--config` | Worker config whose `llm:` block builds the live client. `LLM_PROVIDER` / `LLM_BASE_URL` env vars still override it. Temperature is capped at 0.3 and `max_tokens` at 400. |
| `--base-url`, `--model` | Override `llm.base_url` / `llm.model`. |
| `--only ID` | Process only this cast id. Repeatable. |
| `--no-write` | Leave the cast YAMLs untouched. |
| `--no-render` | Skip the PNGs. |
| `--cpu` | Force the numpy renderer. Without a display it falls back to it automatically. |
| `--out-dir` | PNG directory (default `preview_out/office/`, gitignored). |
| `-v` / `-vv` | DEBUG / TRACE logging to stderr. |

The script exits with 0 when every character got LLM (or params-file) params. It exits
with 1 when any character fell back to defaults; those characters are listed on stderr.

### What it writes

- `cast/<id>.yaml` gets a top-level `character_params:` block right after `avatar: null`.
  The block is a block mapping in the fixed key order, and a comment on its key line
  records the source. `avatar` stays `null`:
  - `CastMember.avatar` is typed `str | None`.
  - The pack loader ignores unknown keys.
  - `scripts/validate_office_profiles.py` accepts extra keys.

  The edit is textual, so comments and block scalars elsewhere are untouched. A re-run
  replaces the block. Before the file is written it is re-parsed, and the write is
  refused if any other key changed.
- stdout gets a `roster:` snippet in the mapping form used by
  `config/workers/roundtable.yaml`, with the params inlined:
  `tuber_N: {name: "...", character_params: {...}}  # cast_id`.
- `preview_out/office/<id>.png`: the front and three-quarter codec views side by side
  (1120x700). The render uses the green codec tint, so `accent_color` is not visible in it.

## Usage Examples

```python
from character import map_appearance
from llm_client import build_llm_client
import yaml

profile = yaml.safe_load(open("campaigns/ashiorid_office/profiles/engineer.yaml"))
client = build_llm_client({"llm": {"provider": "ollama",
                                   "base_url": "http://192.168.1.23:11434",
                                   "model": "qwen2.5:7b-instruct-q4_K_M",
                                   "temperature": 0.2}})
params = map_appearance(profile, client)   # {"head_width": 0.4, ..., "accent_color": "GREEN"}
```

```python
from character import ReplayLLMClient, map_appearance_result

result = map_appearance_result(profile, ReplayLLMClient(["not json", '{"head_width": 0.3, ...}']))
if result.source == "default":
    print("fell back:", result.errors)
```

## Error Handling

- Bad replies and client exceptions never propagate. They are logged (WARNING for a
  parse failure, ERROR for a client failure and for the final fallback) and collected
  in `AvatarResult.errors`.
- `parse_response` raises `AvatarResponseError`, a `ValueError`, for any bad reply.
  Call it directly when you want the exception.
- `ReplayLLMClient([])` raises `ValueError`.
- The script raises `SystemExit` with a one-line message in these cases:
  - the params file is unreadable
  - a reply is missing for a cast id
  - an `--only` id is unknown
  - `campaign.yaml` has no `seats:`
- The script raises `RuntimeError` if the cast-file edit would change any other key.

## Changelog

- v1.0.0 (2026-09-28): Initial version (OB-20). `map_appearance`, `parse_response`,
  `ReplayLLMClient`, and `scripts/generate_office_avatars.py` with `--params-file`.
