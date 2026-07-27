# episode_schema.py — Campaign Platform Episode Validator

## Overview

`app/episode_schema.py` loads, normalizes, and validates a campaign-platform
episode — the declarative "performance score" described in
`docs/campaign_platform_build.md` and frozen in `campaign_platform_contract.md` §1/§4. It is
the second half of the pipeline that starts with `app/primitives.py`
(rendering + screen-time estimation): where `primitives.py` answers "how
does this look and how long does it take", `episode_schema.py` answers "is
this episode safe and well-formed enough to load at all".

Rules live in `config/validation.yaml`; the engine that checks them lives
here. The same config and the same engine run in two places, exactly as the
design doc specifies:

1. **Generator pipeline** (e.g. `generators/coder/build_library.py`, built
   separately — see `campaign_platform_contract.md` §9) — fail the build, do not write the
   episode.
2. **Platform ingest** (`app/replay_pane.py`, via `load_episode`) — refuse
   to load, log loudly, do not air.

The platform must never trust a script just because it appeared in the
`replays/` directory — these episodes go out live on Twitch.

### Campaign isolation falls out of `unknown_primitive` for free

`validate_episode`'s `unknown_primitive` check resolves every render
entry's `primitive` name against the **merged primitive table for the
campaign actually being loaded** (`primitives.load_primitives(campaign)`).
A D&D episode referencing `open_inventory` or `display_map` fails
validation the instant someone tries to air it under the coder campaign —
no separate isolation mechanism is needed. See
`tests/test_episode_schema.py::test_dnd_episode_rejects_under_the_coder_campaign`
for the regression test that guards exactly this property.

### Adding a redaction pattern after an incident is a config edit, not a rebuild

This is the whole point of `config/validation.yaml`'s `redaction.patterns`
list being data instead of code. The 2026-07-12 password leak is why this
module exists in this shape at all: if a new kind of secret shows up on
stream tomorrow, the fix is:

```yaml
# config/validation.yaml
redaction:
  patterns:
    - name: password
      regex: '(?i)\b(password|passwd|pwd|secret)\s*[:=]\s*\S+'
    - name: new_incident_pattern       # <-- add this block
      regex: 'whatever the new leak shape is'
```

No code change, no rebuild, no redeploy — the next episode validated
(generator-side or platform-side) picks up the new pattern immediately.
Compare this to `generators/coder/session_log_parser.py`'s
`REDACTION_RULES`, which *are* code (a Python list of compiled regexes) and
do require a rebuild to change — that module lives on the generator side of
the platform/generator boundary (`generators/README.md`) specifically
because its generator is allowed that cost, while the platform's own safety
net (this module) is not.

### Belt-and-braces redaction, mirroring `LEAK_AUDIT`

`generators/coder/build_library.py`'s `LEAK_AUDIT` re-scans the *whole
serialized* session JSON before writing, rather than trusting a fixed list
of field names — the idea being that a leak hiding in a field nobody
thought to check still gets caught. `_check_redaction` does the same
thing, one level more granular: it `json.dumps()`s each *scene* as a whole
(plus one extra pass over `{meta, cast}`) and regexes over that text,
rather than walking a hand-picked list of "fields that might contain user
text". This is deliberately redundant with the generator side's own
redaction — the platform must not assume a script was already scrubbed
correctly just because it claims to be.

## Signature

```python
class EpisodeError(Exception): ...

@dataclass
class Issue:
    level: str          # "reject" | "warn"
    rule: str            # e.g. "unknown_primitive"; skip_scene severities
                         # get ":skip_scene" appended (see Error Handling)
    message: str
    scene_index: int | None = None

def load_rules(path=None) -> dict: ...
def normalize_episode(raw: dict) -> dict: ...
def validate_episode(episode: dict, *, primitives: dict, rules: dict,
                     pane_ids=None, assets_dir=None, kinds=None) -> list[Issue]: ...
def load_episode(path, *, primitives=None, rules=None, pane_ids=None,
                 assets_dir=None, campaign=None, kinds=None) -> dict: ...
```

## Parameters

### `load_rules(path=None)`

| Name | Type | Required | Default | Notes |
|---|---|---|---|---|
| `path` | path-like | no | `/config/validation.yaml` → `config/validation.yaml` | In-container path preferred, repo-relative fallback (mirrors `primitives._default_base_path`). |

### `normalize_episode(raw)`

| Name | Type | Required | Notes |
|---|---|---|---|
| `raw` | dict | yes | A freshly-parsed episode JSON document. Deep-copied internally — the caller's dict is never mutated. |

### `validate_episode(episode, *, primitives, rules, pane_ids=None, assets_dir=None, kinds=None)`

| Name | Type | Required | Default | Notes |
|---|---|---|---|---|
| `episode` | dict | yes | — | A **normalized** episode (see `normalize_episode`). |
| `primitives` | dict | yes | — | A `primitives.load_primitives(campaign)` table — this is what makes `unknown_primitive` campaign-aware. |
| `rules` | dict | yes | — | A `load_rules()` table. |
| `pane_ids` | iterable of str | no | `None` | Valid layout pane ids. `None` skips `unknown_target` entirely (can't validate against a layout the caller didn't supply). |
| `assets_dir` | path-like | no | `None` | The campaign's library directory (asset `root`/`manifest` in `config/validation.yaml` are resolved relative to this). `None` skips `assets.must_exist`/`assets.manifest` (still runs `basename_only`, which needs no filesystem). |
| `kinds` | iterable of str | no | `None` | Valid scene `kind` values — typically a campaign's `narration.yaml` keys. `None` skips `unknown_kind` entirely. See Error Handling for why this parameter exists and its scope. |

### `load_episode(path, *, primitives=None, rules=None, pane_ids=None, assets_dir=None, campaign=None, kinds=None)`

| Name | Type | Required | Default | Notes |
|---|---|---|---|---|
| `path` | path-like | yes | — | A `.json` episode file. |
| `primitives` | dict | no | `primitives.load_primitives(campaign)` | Auto-resolved when omitted; `campaign` (arg or `episode.meta.campaign`) is then required. |
| `rules` | dict | no | `load_rules()` | Auto-resolved when omitted. |
| `pane_ids` | iterable of str | no | `None` | Passed through to `validate_episode`. |
| `assets_dir` | path-like | no | `None` | Passed through to `validate_episode`. |
| `campaign` | str | no | `episode.meta.campaign` | Used to auto-resolve `primitives` when it isn't given explicitly. |
| `kinds` | iterable of str | no | `None` | Threaded straight through to `validate_episode`'s own `kinds` param (see below) — `None` skips `unknown_kind` here too. Added 2026-07-26 alongside the campaign persona-assignment work; see Error Handling for the defect this fixed. |

## Return Value

- `load_rules` → `dict` with `structure`/`limits`/`assets`/`redaction` keys
  (the shape of `config/validation.yaml`).
- `normalize_episode` → a new `dict` (never the same object as `raw`) with
  `meta`, `cast`, and `scenes[]` always present, and every scene carrying
  `text`/`fallback`/`mode`/`render` even if the source omitted them.
- `validate_episode` → `list[Issue]`, possibly empty. Never raises for
  content problems — a completely malformed episode still returns Issues,
  it does not crash the caller.
- `load_episode` → the normalized (and, when `redaction.on_match ==
  "redact"`, scrubbed) episode `dict`. Raises `EpisodeError` instead of
  returning if any `Issue.level == "reject"`.

## Dependencies

- Standard library: `copy`, `json`, `logging`, `re`, `dataclasses`,
  `pathlib`.
- Third-party: `PyYAML` (already a hard dependency of the worker image).
- `app/primitives.py` — `PrimitiveError`, `resolve_recipe`,
  `estimate_scene_seconds` (frozen API, not modified by this module), and
  `load_primitives` (imported lazily inside `load_episode`, only when the
  caller doesn't supply a `primitives` table itself).
- Consumed by: `app/replay_pane.py` (ingest-time validation before airing —
  wiring not shown here, built by a parallel effort per `campaign_platform_contract.md` §7),
  and `generators/coder/build_library.py` (validates before writing an
  episode — a generator importing from `app/` across the platform/generator
  boundary, an explicitly documented exception; see `campaign_platform_contract.md` §9).

## Usage Examples

### Validate an in-memory episode before writing it (generator side)

```python
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from episode_schema import load_rules, normalize_episode, validate_episode
from primitives import load_primitives

episode = normalize_episode(raw_episode_dict)
primitives = load_primitives("coder")
rules = load_rules()

issues = validate_episode(episode, primitives=primitives, rules=rules,
                          kinds={"boss", "coder_talk", "coder_work"})
rejecting = [i for i in issues if i.level == "reject"]
if rejecting:
    for issue in rejecting:
        print(f"[{issue.rule}] scene {issue.scene_index}: {issue.message}")
    raise SystemExit(1)  # fail the build; do not write the episode
```

### Load an episode at ingest time (platform side)

```python
from episode_schema import EpisodeError, load_episode

try:
    episode = load_episode(
        "replays/coder/2026-07-26_fix-flaky-test.json",
        campaign="coder",
        assets_dir="replays/coder",
        pane_ids={"theater", "notes"},
    )
except EpisodeError as exc:
    print(f"[replay_pane] refusing to air: {exc}")
else:
    # episode is normalized and validated -- safe to hand to Performer.perform
    ...
```

## Error Handling

- `load_rules` raises `EpisodeError` when `config/validation.yaml` is
  missing or fails to parse — there is no safe default for validation
  rules, especially the redaction patterns (same posture
  `primitives.load_primitives` takes toward its own required base config).
- `validate_episode` **never raises** for content problems. A scene that
  isn't even a dict, a render entry with no `primitive` key, an unknown
  primitive that makes `estimate_scene_seconds` unable to run — all of
  these degrade to an `Issue` (or are silently skipped when they'd only
  duplicate an `Issue` already reported elsewhere), never an exception.
- `load_episode` raises `EpisodeError` — with **every** rejecting `Issue`
  concatenated into the message — when validation produces any
  `level == "reject"` Issue. It also raises `EpisodeError` for a missing
  episode file, malformed JSON, or (when `primitives` isn't supplied and
  neither `campaign` nor `episode.meta.campaign` is available) an
  unresolvable campaign.
- **`skip_scene` severity**: `config/validation.yaml`'s `structure.*`
  settings (`unknown_kind`, `unknown_primitive`, `unknown_speaker`,
  `unknown_target`, `external_refs`) accept `reject | warn | skip_scene`.
  Because `Issue.level` is frozen to exactly `"reject"`/`"warn"`
  (`campaign_platform_contract.md` §4), `skip_scene` is encoded as `level="warn"` with
  `":skip_scene"` appended to `rule` (e.g. `"unknown_kind:skip_scene"`) —
  this keeps `validate_episode` from inventing a third `Issue.level` value
  the contract doesn't define, while still being unambiguous and
  greppable. `load_episode` looks for that suffix after validation and
  drops the flagged scenes from the episode it returns; `validate_episode`
  itself never mutates its input.
- **`redaction.on_match: redact`**: for the same reason (`validate_episode`
  is documented "Pure... returns Issues", never mutates), a redact-mode
  match becomes a non-blocking `Issue` (`level="warn"`, `rule` suffixed
  `:redact`) from `validate_episode`, and the *actual* text substitution
  (`[redacted:<pattern-name>]`) is applied by `load_episode`'s
  `_apply_redaction` helper, right before it returns the normalized dict.
  Detection still scans the whole serialized scene (belt-and-braces); the
  redact-mode rewrite then applies per string field (`scene.text`,
  `scene.fallback`, every string value in a render entry's `payload`) since
  none of the patterns shipped in `config/validation.yaml` contain
  characters JSON string-escaping would mangle, so a field-level
  substitution is equivalent and far simpler to splice back into the
  structured episode than trying to map a match found in one giant
  serialized string back onto a specific nested field.
- **`kinds=None` on `validate_episode` and `load_episode`**: `campaign_platform_contract.md`
  §4 asks for this parameter specifically for the `unknown_kind` check.
  **Fixed 2026-07-26** (alongside the campaign persona-assignment work,
  campaign_platform_contract.md §8): `load_episode` previously had no `kinds` parameter at
  all, so `unknown_kind` could only ever fire via a direct `validate_episode`
  call — never through the normal ingest path. `load_episode` now accepts
  `kinds` and threads it straight through to `validate_episode`'s own
  param, defaulting to `None` (check skipped) exactly as before when a
  caller doesn't supply one. `app/replay_pane.py` is the ingest-path caller
  that now supplies it on every `load_episode` call: it loads the
  resolved campaign's narration config
  (`revoice.load_narration_config(campaign)`) and passes `set(...) or None`
  as `kinds` — the `or None` matters, since a campaign with no
  `narration.yaml` at all degrades to an empty dict, and an empty-but-not-
  `None` `kinds` set would reject every single scene's `kind` as unknown
  instead of skipping the check. `generators/coder/build_library.py`
  (built separately, campaign_platform_contract.md §9) should do the same on the generator
  side for build-time enforcement.
- **Asset containment is this module's job, not `primitives.py`'s**
  (`campaign_platform_contract.md` §9b) — `RenderContext` has no `assets_dir` and does no
  containment of its own. `assets.basename_only` is checked with an
  explicit, platform-independent string check (rejecting any path
  separator, `.`/`..`) rather than relying on `pathlib.Path(...).name`
  (which only treats backslash as a separator on Windows) — validation
  must behave identically regardless of which OS runs it. Verified against
  the same hostile inputs `app/replay_pane.py::resolve_episode` is tested
  against: `../../../etc/passwd`, `..\..\secrets.json`, `/etc/passwd`,
  `c:\Users\dev\.env`.

## Changelog

- **v1.1.0** (2026-07-26, campaign_platform_contract.md §8): `load_episode` gained a `kinds`
  parameter (default `None`), threaded straight through to
  `validate_episode`'s existing one — the only change made to this module
  as part of the campaign persona-assignment work. Fixes the defect noted
  in v1.0.0's Error Handling section: `unknown_kind` could never fire
  through the normal ingest path before this. `app/replay_pane.py` now
  supplies it on every `load_episode` call via
  `revoice.load_narration_config(campaign)`.
- **v1.0.0** (2026-07-26): Initial version. `load_rules`/
  `normalize_episode`/`validate_episode`/`load_episode` per `campaign_platform_contract.md`
  §4. Implements `structure.{required_scene_fields, unknown_kind,
  unknown_primitive, unknown_speaker, unknown_target, external_refs}`,
  `limits.{max_scenes, max_text_chars, max_scene_seconds}`,
  `assets.{root, basename_only, must_exist, manifest}`, and
  `redaction.patterns` with `on_match: reject|redact|warn` and per-pattern
  `except`. Ships `config/validation.yaml` verbatim from
  `docs/campaign_platform_build.md`'s Validator section, preserving the
  `public_ip` pattern's private-LAN `except` clause from the 2026-07-12
  incident review. Ships `tests/fixtures/{coder,dnd}_episode_valid.json`
  as small reusable valid episodes for other test modules.
