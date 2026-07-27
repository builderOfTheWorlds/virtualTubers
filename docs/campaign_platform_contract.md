# Campaign Platform — Interface Contract

> **Status**: reference document. This was the frozen interface contract that every
> implementation agent built against during the 2026-07-26 campaign-platform build
> (see [campaign_platform_build.md](campaign_platform_build.md) for the design and
> "As shipped"/"Where reality diverged" notes). It is kept here because the rest of
> `docs/` cites it by section number (`CONTRACT.md §4`, `§8`, `§9b`, etc.) throughout —
> those citations point at the section headers below. Section numbering is therefore
> frozen too; do not renumber without checking those citations.

---

## Global rules

- Python 3.12, stdlib + existing deps. `.venv` exists at repo root — use `.venv/Scripts/python.exe`.
- `app/` is a flat directory of top-level modules on `sys.path` (NOT a package). Import siblings
  by bare name: `import primitives`, `from episode_schema import load_episode`.
- Tests: `tests/test_<module>.py`, pytest, `sys.path.insert(0, .../"app")` at the top with
  `# noqa: E402`, `monkeypatch.setattr` + hand-written duck-typed fakes (NOT `unittest.mock`)
  for `app/` modules; `unittest.mock.patch` is the norm only in `tests/test_message_api.py`.
  Long descriptive test names. Test docstrings should encode WHY (the regression story).
- Docs: every new module gets `docs/<module>.md` using CLAUDE.md's template (Overview,
  Signature, Parameters, Return Value, Dependencies, Usage Examples ×2, Error Handling,
  Changelog v1.0.0 dated 2026-07-26).
- Logging: `logging` module, TRACE-ish detail at DEBUG, errors inside every except block.
  **`build_layout.py`-style stdout discipline**: `replay.py`/`replay_pane.py` own stdout for
  the show — log to stderr only.
- NEVER commit, NEVER push, NEVER create a branch. The orchestrator handles git.
- Preserve every existing behavior not explicitly changed here — especially duet support,
  audio-anchored pacing, redaction, and the "the show must always air" soft-degradation rule.

---

## 1. Episode schema (the contract everything hangs off)

```jsonc
{
  "meta":  {"schema": 1, "campaign": "coder", "id": "<stem>", "title": "...", "created": "<ISO8601>"},
  "cast":  ["boss", "coder"],
  "scenes": [
    {
      "speaker":  "coder",              // required, must be in meta.cast
      "kind":     "coder_work",         // required, must key into narration.yaml
      "text":     "narration source",   // may be "" (pure visual business)
      "fallback": "spoken if LLM dies", // optional; narration.yaml fallback_template used if absent
      "mode":     "sequence",           // optional, default "sequence"; "sequence"|"parallel"
      "render":   [                     // optional, default []
        {"primitive": "show_command",
         "target": "theater",           // optional; recipe's own target is the default
         "payload": {"command": "...", "output": "..."}}
      ]
    }
  ]
}
```

Normalized in-memory scene ALWAYS has all of: `speaker, kind, text, fallback, mode, render`.
Loaders fill defaults. No `events` key anywhere. No `detail_file`, no external text refs.

**Scene keys added at runtime by other layers — do not repurpose these names:**
`narration` (spoken line), `audio` (`tts_client.Narration|None`), `owned` (bool, duet),
`target_duration` (float, duet).

---

## 2. `app/primitives.py` — FROZEN API

```python
BEHAVIORS: set[str] = {"type", "print", "diff", "image", "pause"}

class PrimitiveError(Exception): ...

def load_primitives(campaign, *, base_path=None, campaigns_dir=None) -> dict[str, dict]:
    """Merged, fully-resolved recipe table for one campaign.

    Loads config/primitives.yaml (shared), then deep-merges
    config/campaigns/<campaign>/primitives.yaml over it, then resolves `extends`.
    Logs a WARNING when a campaign redefines a shared primitive without `extends`.
    Missing campaign file is fine (shared-only). Missing base file raises PrimitiveError.
    """

def resolve_recipe(primitives, name) -> dict:
    """Fully-resolved recipe dict. Raises PrimitiveError on unknown name."""

def estimate_entry_seconds(entry, primitives, *, max_output_lines=24) -> float:
    """Screen-time for ONE render[] entry (a {primitive,payload,target} dict)."""

def estimate_scene_seconds(scene, primitives, *, max_output_lines=24, speed=1.0) -> float:
    """Screen-time for a whole scene. SUM under mode 'sequence', MAX under 'parallel'.
    Divided by max(speed, 0.01). A scene with render:[] is 0.0."""

def render_summary(scene, primitives) -> str:
    """Short human/LLM-facing description of what a scene shows on screen.
    Feeds narration.yaml's {material} and {render_summary} tokens. Never raises."""

def perform_entry(entry, primitives, ctx) -> None:
    """Execute one render[] entry's behaviors against a RenderContext."""
```

`RenderContext` is a small object supplied by `replay.py` (defined in `primitives.py`,
constructed by `replay.py`) exposing exactly:

```python
class RenderContext:
    def __init__(self, write, pacer, palette, avatar, max_output_lines=24): ...
    write(text: str, *, target: str | None = None) -> None   # routes to a sink
    pacer                    # replay.Pacer — sleep(), type_out(), check_stop()
    palette                  # replay.Palette — .dim/.bold/.cyan/.green/.yellow/.red/.magenta/.reset
    avatar(expression, action="", bubble=None) -> None       # never raises
    max_output_lines: int
```

### Merge semantics — mirror `build_layout.deep_merge` EXACTLY
Copy its semantics verbatim (dicts merge recursively; scalars AND lists are full replaces;
neither input mutated). Import it (`from build_layout import deep_merge`) rather than
re-implementing — it is already tested.

### `extends` resolution
`extends: <other primitive name>` — resolve the parent first (recursively; detect and raise
`PrimitiveError` on cycles), then apply the child. `behaviors` merges **positionally**:
child `behaviors[i]` deep-merges onto parent `behaviors[i]`; extra child entries append;
a child with fewer entries leaves the parent's tail intact. Everything else (`target`, etc.)
uses `deep_merge`. This is the design doc's stated choice — implement it, note the ambiguity
for multi-behavior recipes in the docstring.

### Behavior semantics + timing (constants MUST come out of `replay.py`)

| behavior | recipe fields | renders | seconds |
|---|---|---|---|
| `type` | `field`, `rate_cps`, `prefix`, `style`, `max_lines` | char-by-char via `pacer.type_out` | `chars_displayed / rate_cps` |
| `print` | `field`, `rate_lps`, `prefix`, `style`, `max_lines` | line-by-line, `pacer.sleep(1/rate_lps)` each | `lines_displayed / rate_lps` |
| `diff` | `field`, `rate_lps`, `rate_cps`, `style:{add,remove}`, `max_lines` | `-` lines scrolled, `+` lines typed | removed_lines/rate_lps + added_chars/rate_cps |
| `image` | `field`, `hold_s`, `style` | see below | `hold_s` |
| `pause` | `seconds` | nothing | `seconds` |

- `field` is a dotted path into the entry, e.g. `payload.text`, `payload.hunks`. Resolve
  safely; a missing field renders nothing and costs 0 seconds.
- `max_lines` defaults to `ctx.max_output_lines`; truncation prints a dim
  `… (N more lines)` footer exactly as `replay._truncate_lines`/`_paced_output` do today.
- `style` maps to `Palette` attribute names (`{fg: green, bold: true}`).
- `image`: soft-import Pillow; if available render 24-bit ANSI half-blocks scaled to the
  pane, otherwise a framed placeholder box naming the asset. Pillow must stay OPTIONAL —
  do not add a hard requirement. Resolution is basename-only inside the campaign asset dir.

### **Fidelity requirement — non-negotiable**
`config/campaigns/coder/primitives.yaml` + this engine must reproduce today's coder show
**exactly**: same glyphs, colors, rates, avatar expressions, truncation footers, and total
estimated seconds. Today's numbers (from `app/replay.py`):
`DIALOGUE_CPS=45, CODE_CPS=130, OUTPUT_LINES_PER_S=18, EVENT_PAUSE_S=0.8, MAX_OUTPUT_LINES=24, TOOL_BEAT_S=0.5`

Per-event totals to reproduce (`estimate_event_seconds`, see recon §2):
- boss/user_message: `len(text)/90 + 0.8`   (typed at DIALOGUE_CPS*2, cyan `┌─ BOSS ─` box, `│ ` prefix, avatar `thinking`)
- coder_talk/assistant_text: `len(text)/45 + 0.8`  (green/bold `{name} ▸` header, avatar `speaking` + bubble)
- Bash/PowerShell: `0.8 + len(command)/130 + displayed_output_lines/18`  (yellow `$ ` prefix, dim output, avatar `focused`)
- Edit: `0.8 + displayed_old_lines/18 + displayed_new_chars/130`  (magenta `✎ editing {file}`, red `- ` scrolled, green `+ ` typed)
- Write: `0.8 + displayed_content_chars/260`  (magenta `✎ new file {file}`, green `+ ` typed)
- Read: `0.8 + 0.5`  (dim `⋯ reading {file}`)
- generic tool: `0.8 + 0.5`  (dim `⋯ {tool}: {summary[:100]}`)

Model the trailing `EVENT_PAUSE_S` as a final `{behavior: pause, seconds: 0.8}` entry on each
coder primitive. Ship `tests/test_primitives.py` with an explicit table test asserting these
seven totals match to within 1e-6 against representative payloads.

Error handling: a failed error-marked action still renders the red `✗ that didn't work` line
and avatar `frustrated` — model it as `payload.error: true` handled by the coder recipes.

---

## 3. Config files

### `config/primitives.yaml` — shared, presentation-neutral
Exactly `type_text`, `print_text`, `show_image`, `beat` as written in the design doc
(§"Shared primitives"). Do not add domain primitives here.

### `config/campaigns/coder/primitives.yaml`
`show_boss_message`, `show_coder_line`, `show_command`, `show_diff`, `show_write`,
`show_read`, `show_tool` — reproducing the table above exactly.

### `config/campaigns/coder/narration.yaml`
Keyed by `kind`. Lift the three prompts verbatim from `revoice._PROMPTS`
(`boss`, `coder_talk`, `coder_work`) plus `fallback_template` per kind. Token set available
to prompts: `{name}`, `{words}`, `{text}`, `{material}`, `{render_summary}`.

### `config/campaigns/dnd/primitives.yaml` + `narration.yaml`
Ship the design doc's D&D examples as a working second campaign (proves generalization).

### `config/validation.yaml`
Exactly the design doc's block (§Validator). Do not change the regexes.

---

## 4. `app/episode_schema.py` — FROZEN API

```python
class EpisodeError(Exception): ...

@dataclass
class Issue:
    level: str          # "reject" | "warn"
    rule: str           # e.g. "unknown_primitive"
    message: str
    scene_index: int | None = None

def load_rules(path=None) -> dict:                     # config/validation.yaml
def normalize_episode(raw) -> dict:                    # fills scene defaults; never validates
def validate_episode(episode, *, primitives, rules,
                     pane_ids=None, assets_dir=None) -> list[Issue]:
    """Pure. Never raises for content problems — returns Issues."""
def load_episode(path, *, primitives=None, rules=None, pane_ids=None,
                 assets_dir=None, campaign=None) -> dict:
    """Read, normalize, validate. Raises EpisodeError if ANY Issue has level 'reject',
    with every rejecting issue in the message. Warns are logged. Returns normalized dict."""
```

Rules to implement (all from `config/validation.yaml`):
`structure.required_scene_fields`, `unknown_kind`, `unknown_primitive`, `unknown_speaker`,
`unknown_target`, `external_refs`; `limits.max_scenes`, `max_text_chars`, `max_scene_seconds`
(uses `primitives.estimate_scene_seconds`); `assets.*`; `redaction.patterns` with
`on_match` and per-pattern `except`.

- `unknown_kind` needs the narration config's kinds — accept an optional `kinds=None` arg
  (add it to the signature; when `None`, skip that check).
- `external_refs`: reject any scene/payload key named `detail_file`, or any payload string
  value that looks like a bare relative path to a `.md`/`.txt`/`.json` sidecar.
- `redaction` scans the serialized episode JSON, mirroring
  `scripts/build_replay_library.py`'s `LEAK_AUDIT` belt-and-braces approach. Private LAN IPs
  MUST stay readable (the `except` field) — this was a deliberate earlier decision.

---

## 5. `app/revoice.py` — rewritten

DELETE: `plan_scenes`, `_PROMPTS`, `fallback_narration`, `_scene_material`, `MAX_SCENE_EVENTS`.
KEEP: `WORDS_PER_SECOND=2.5`, `MIN_WORDS=8`, `MAX_WORDS=130`, `target_words`, `_trim_words`,
`_display_name`, `SYSTEM_PROMPT`, and `prepare_show`'s soft-degradation contract.

```python
def load_narration_config(campaign, *, campaigns_dir=None) -> dict:
    """config/campaigns/<campaign>/narration.yaml -> {kind: {prompt, fallback_template}}"""

def scene_visual_seconds(scene, primitives, *, max_output_lines=24, speed=1.0) -> float:
    """Thin delegate to primitives.estimate_scene_seconds — kept as the public name
    because replay_pane and tests use it."""

def fallback_line(scene, narration_config, primitives, max_words, names) -> str:
    """scene['fallback'] if non-empty, else narration_config[kind]['fallback_template']
    formatted with {name}/{render_summary}/{text}, else a generic last resort."""

def narrate_scene(scene, llm, words, names, narration_config, primitives,
                  verbatim=False) -> str:
def prepare_show(episode, llm, tts, workdir, *, primitives, narration_config,
                 worker_name="KODI-7", boss_name="the boss", speed=1.0,
                 max_output_lines=24, progress=None, speaker_names=None,
                 verbatim=False) -> list[dict]:
    """Iterates episode['scenes'] DIRECTLY — no grouping step. Returns the same scene
    dicts annotated with 'narration' and 'audio', same as today."""
```

`verbatim=True` speaks `scene['text']` in full with no LLM call whenever `text` is non-empty
(replaces today's `kind in ("boss","coder_talk")` check).
`names` bundles `worker_name`/`boss_name`/`speaker_names` — keep `_display_name`'s
resolution order (explicit map > `boss`/`coder` legacy defaults > raw id).

---

## 6. `app/replay.py` — rendering via recipes

KEEP UNCHANGED: `Pacer`, `Palette`, `ReplayStopped`, `_truncate_lines`, `BUBBLE_CHARS`,
`MIN_SCENE_SCALE=0.4`, `MAX_SCENE_SCALE=3.0`, the whole audio-anchoring block in
`_perform_scene` (Case A audio / Case B target_duration), the duet hooks
(`on_scene_start`, `wait_for_scene`, `owned`, `catch_up_to`), avatar driving via
`agent_state`, and `perform()`'s index-based loop + return-value semantics.

DELETE: `estimate_event_seconds`, `_on_user_message`, `_on_assistant_text`, `_on_tool_call`,
`_perform_shell/_perform_edit/_perform_write/_perform_read/_perform_generic`,
`_perform_events`, and `load_script`'s directory branch (the `session_log_parser` import).

REPLACE with:
```python
class Performer:
    def __init__(self, out=None, pacer=None, palette=None, worker_name="KODI-7",
                 state_path=None, max_output_lines=MAX_OUTPUT_LINES, *,
                 on_scene_start=None, wait_for_scene=None, speaker_names=None,
                 primitives=None, sinks=None): ...
    def _perform_render(self, scene) -> None:
        """Build a RenderContext and run scene['render'] entries.
        mode 'sequence': one after another. mode 'parallel': interleave via threads,
        joined before returning; a failure in one entry must not abort the scene."""

def load_script(source) -> dict:     # .json only now; delegates to episode_schema.load_episode
def perform(self, episode, show=None, start=0, limit=None) -> bool:
    """show is None -> perform episode['scenes'] silently (narration ignored).
    show given -> it IS the scene list (from prepare_show). Same as today."""
```

**`target` / sinks**: `sinks` is `dict[str, TextIO]`; `_perform_render` routes each entry's
`target` (entry override > recipe default > `"theater"`) through it. Default
`sinks = {"theater": out}`. An unresolvable target falls back to the theater sink and logs a
WARNING — do NOT invent cross-pane IPC. Document this honestly as the current limit in
`docs/replay.md` (the validator's `unknown_target` catches bad targets at ingest; a *valid*
target with no wired sink is the degraded case).

`_perform_scene` computes `natural` via `primitives.estimate_scene_seconds(scene, ...)`
instead of summing `estimate_event_seconds` over `scene["events"]`. Everything else in that
method stays byte-for-byte equivalent in behavior.

CLI: keep every existing flag; add `--campaign` (default from `meta.campaign`).

---

## 7. Campaign namespacing

- Library layout: `replays/<campaign>/<episode>.json`, assets at `replays/<campaign>/assets/`.
- `replay_pane.resolve_episode(library, episode, campaign)` — basename-only containment
  **within `library/campaign/`**. Keep every existing traversal test passing.
- Campaign resolution order: `request["campaign"]` > worker config `campaign:` >
  env `REPLAY_CAMPAIGN` > `"coder"`. Route it through `message_bus.resolve()`.
- `narration_store`: add a `campaign` column. `save_airing(..., campaign)` and
  `load_latest_airing(episode, campaign)` gain the parameter; `load_airing(message_id)`
  is unchanged. Add the `AND campaign = %(campaign)s` clause to `LOAD_SQL`'s subquery.
  Ship the migration in `docs/sql/` following the existing file conventions there.
- `_rebuild_scenes_from_rows` no longer calls `plan_scenes` — it zips cached rows against
  `episode["scenes"]` by index, keeping the existing length + `kind` pairwise guard
  (stale-cache refusal) and the `owns=` audio-stripping behavior.
- **Preserve the in-memory `narration` ↔ DB/bus `text` field-name translation.** Do not unify.

---

## 8. Blank workers + persona assignment

### `app/campaign_control.py` (new — mirror `worker_control.py`'s shape exactly)
```python
CAMPAIGN_KEY = "campaign:active"                 # JSON {"campaign":..., "cast": {...}, "started_at":...}
def persona_key(worker_id) -> str:               # f"worker:{worker_id}:persona"
class CampaignControl:
    @classmethod
    def from_config(cls, config=None) -> "CampaignControl": ...
    def get_active(self) -> dict | None          # fails OPEN -> None (no campaign)
    def get_persona(self, worker_id) -> dict|None  # {"campaign":..., "speaker":...}; fails OPEN -> None
    def start(self, campaign, cast, *, force=False) -> dict   # writes both key kinds
    def stop(self) -> None                        # clears campaign:active + every assigned persona
```
Reads fail open (None = blank = disabled). Writes propagate `redis.RedisError` so the API 503s.

### Persona resolution (worker side)
`config/campaigns/<campaign>/personas.yaml`: `{speaker_id: {name, title, role, system_prompt,
voice: {model_path, ...}, avatar: {name, title, expressions, provider}}}`.
The worker resolves `speaker -> persona doc` from its OWN mounted config — message-api never
reads persona files, it only stores `{campaign, speaker}` in Redis. This keeps message-api dumb.

### Relay file (the pane-facing half)
`/tmp/persona.json`, env `PERSONA_FILE`. Written by `agent.py` with the existing
`_atomic_write_json` idiom whenever the resolved persona changes. Contents: the fully
resolved persona doc plus `{"campaign":..., "speaker":..., "updated_at":...}`.
Panes POLL it — panes never touch Redis or Kafka. This is the duet_replay.md rule.

### Consumers
- `agent.py` tick loop: re-read persona each tick right after the existing
  `control.is_enabled()` check (same "cheap re-check, no caching" shape). No persona
  assigned -> treat exactly as disabled (blank == disabled; do NOT build a new mode).
  On change: overlay `agent.name`/`role`/`system_prompt` and write the relay file.
- `avatar.py`: poll `/tmp/persona.json` in its existing tick loop; on change rebuild the
  provider via `load_provider()`. Any failure keeps the current face — the avatar pane's
  only job is to stay up.
- `replay_pane.py`: poll the relay file in its idle loop; overlay `voice` onto its config
  dict so the next airing builds its `TTSClient` from the new persona.
  (`tts_client._LOCAL_VOICES` is already keyed by resolved path — no unload needed.)

### message-api routes (add to `services/message-api/api.py`)
```
POST /campaigns/{campaign}/start   body {"cast": {"<speaker>": "<worker_id>"}, "force": false}
                                   -> {"campaign", "cast"}   409 if any worker is mid-airing
                                      and force is false; 503 on RedisError
POST /campaigns/stop               -> {"stopped": true, "campaign": <previous|null>}
GET  /campaigns/active             -> {"campaign":..., "cast": {...}} or {"campaign": null}
```
"Mid-airing" = that worker's `replay_request`/cue relay state says a show is running. There is
no existing Redis flag for this — add `worker:{id}:airing` written by `replay_pane.py`
(set at airing start, cleared in a `finally`) and read here. Fail open (absent = not airing).
Follow the existing 503-on-RedisError convention and the `openapi_examples` style.

---

## 9. `generators/coder/` (moved out of the platform)

- `app/session_log_parser.py` -> `generators/coder/session_log_parser.py` (MOVE, delete original)
- `scripts/build_replay_library.py` -> `generators/coder/build_library.py` (MOVE, delete original)
- `tests/test_session_log_parser.py` -> `tests/test_coder_generator.py`, imports updated
- **Preserve all 17 `REDACTION_RULES` byte-for-byte and the separate `LEAK_AUDIT` re-scan.**
  Their exact patterns and ORDER are load-bearing (see recon_performance.md §5).
- The generator now emits the NEW schema: groups raw events into scenes using the old
  `plan_scenes` logic (moved here — it belongs on the generator side now), inlines every
  string (no `detail_file` sidecars — read the sidecar at build time and inline its content),
  and emits `meta`/`cast`/`scenes[]` with coder primitives.
- It validates before writing, by importing `app/episode_schema.py` (sys.path insert). This
  is the design doc's "same config and same engine on both sides" — document the exception.
- Writes to `replays/coder/`. `generators/README.md` explains the boundary.
- Add `generators/` to `.dockerignore` — it must never enter the worker image.

---

## 9b. ADDENDUM — recipe schema as actually shipped (2026-07-26, post-step-2)

`app/primitives.py` and the coder recipes are DONE and MERGED. The recipe schema grew three
things beyond what §2 described. These are now authoritative — read
`config/campaigns/coder/primitives.yaml` and `docs/primitives.md` for the full picture.

1. **Recipe-level `avatar:`** — `{expression, action, bubble}`, fires once when the entry
   starts, costs 0 seconds. Values support `{payload.x}` token substitution. This is where
   the avatar-driving that used to live in `replay.py`'s per-event handlers now lives.
2. **Recipe-level `on_error:`** — `{avatar: {...}, line: {text, style}}`, fires after the
   main behaviors when `payload.error` is truthy, costs 0 seconds. This is today's trailing
   red `✗ that didn't work` + `frustrated` avatar.
3. **Literal `text:` on `type`/`print` behaviors** — an alternative to `field:`, for static
   framing (the `┌─ BOSS ─` box, `✎ editing {payload.file}` headers). Always costs 0
   seconds. Supports `{payload.x}` tokens. Also shipped: `prefix_style`,
   `continuation_prefix`, and `style: {dim: true}`.

**Payload contract per coder primitive — the generator MUST emit exactly this:**
```
show_boss_message  {text}
show_coder_line    {text, name}          # name = on-screen speaker label
show_command       {command, output?, error?}
show_diff          {file, hunks, error?} # hunks = every pre-edit line prefixed "- ",
                                         # THEN every post-edit line prefixed "+ ",
                                         # concatenated in that order. NOT a unified diff.
show_write         {file, content, error?}
show_read          {file, error?}
show_tool          {tool, summary, error?}  # summary pre-truncated to <=100 chars
```

**Asset containment is `episode_schema.py`'s job, not `primitives.py`'s.** `RenderContext`
has no `assets_dir`, so the `image` behavior treats `payload.image` as already-validated.
The validator MUST enforce basename-only + must_exist at ingest.

---

## 10. Out of scope — do NOT do these

- No migration of the ~50 existing `replays/*.json` (hard cut, user's explicit decision).
- No deploying to d2000, no `docker compose up`, no image builds.
- No changes to the coding pipeline handlers (`task_assignment` -> `commit_notification` ->
  `test_passed`/`bug_report` -> `manager_report`).
- No per-campaign layouts/panels — `LAYOUT_PRESET` already covers it.
- No runtime campaign switching beyond the three endpoints above.
