# office.role_attribution (+ CorpusAdapter)

## Overview

OB-12, the corpus → content bridge (`.claude/prompts/office_campaign_plan.md` WS-D). Recorded
coding-agent sessions collected by the sibling **sessionCorpus** repo become:

1. **Source notes** for the 3-layer weekly generator —
   `utilities/3LayersWeeklyGeneration/src/source_adapter.py` `CorpusAdapter` turns each
   session in an export JSONL into one `SourceNote(kind="work_session")` holding a condensed
   digest: the user's asks, the files edited and read, the commands run, the test outcomes
   and the final assistant summary. The digest is capped at about 3000 characters. `load_source(path)`
   picks `CorpusAdapter` for any `.jsonl` file.
2. **Office replay episodes** — `app/office/role_attribution.py` `attribute()` maps every
   event in one session to an office role and seat (`app/office/roles.py` `SEAT`). The
   output is a replay episode that `episode_validator.validate_episode` accepts.

Input record (sessionCorpus `src/normaliser.py` `export_view`):
`{source_tool, host, project, session_id, started_at, ended_at, model, events:[{seq, ts,
type: user_message|assistant_text|tool_call, text, tool, input, output, error}]}`. Exports
written with a tag also carry `tags: [...]`.

### Attribution rules (deterministic pass)

Turn state resets at each `user_message`.

| Event | Role (seat) |
|---|---|
| `user_message` (harness noise stripped via `clean_user_text`) | ceo (tuber_0) |
| first `assistant_text` after an ask | tech_lead if it has plan words (plan/todo/steps/approach…); else analyst if it has requirement words (you want/requirement/must/should…); else tech_lead |
| later `assistant_text` in the turn | engineer |
| plan/todo tools (`TodoWrite`, `todo_list`, `ExitPlanMode`…), delegation (`delegate_task`, `Task`, `Agent`) | tech_lead |
| discovery tools (`Read`, `Grep`, `Glob`, `WebSearch`, `read_file`, `search_files`, `web_search`…) before the first edit in the turn | analyst |
| the same tools after an edit in the turn | engineer |
| edit tools (`Edit`, `MultiEdit`, `Write`, `NotebookEdit`, `patch`, `write_file`) | engineer |
| shell (`Bash`, `PowerShell`, `terminal`) whose primary command (first segment after `cd`/`export` prefixes) is `git commit/push/merge/tag` | tech_lead |
| … matches `pytest`, `test`, `unittest` or `tox` | tester |
| … matches `rm`, `prune`, `clean(up)`, `git gc` or `git branch -d/-D` | office_manager |
| any other shell command (builds, installs…), `execute_code`, unknown tools | engineer |

Hermes tool names are rendered as their Claude Code equivalents so `replay.Performer` can
act them out: `terminal→Bash`, `patch→Edit`, `write_file→Write`, `read_file→Read`. The
original name is kept in `source_tool_name`. Tool events get the `parse_session` fields
`input_summary`, `output_summary` and `detail` (`command`/`output`, `file`/`old`/`new`,
`file`/`content`). Payloads are capped at `MAX_OUTPUT_CHARS`.

### Episode shape

```json
{"source": "office-claude_code-<session_id>", "project": "...", "session_id": "...",
 "date": "<started_at>", "events": [{"type": "...", "speaker": "tuber_N", "role": "...", ...}],
 "show": {"slots": ["tuber_0", "..."]}}
```

`show.slots` lists exactly the seats that were used, so the validator's show-header stage
confirms every speaker is cast.

### Optional LLM pass (`embellish=True`)

The injected client's `complete(system_prompt, messages)` (the `app/llm_client.py`
interface) receives an outline of the events and must reply with JSON:
`{"handoffs":[{"before":i,"role":r,"text":...}], "reactions":[{"after":i,"text":...}]}`.

- **Hand-offs** become `assistant_text` lines spoken by `role` and placed before event `i`.
  A hand-off is only kept if `role` is the next speaker's own role, or
  `roles.can_direct(role, next_role)` allows it.
- **Reactions** become Marketing (tuber_5) lines, at most 5.
- Inserted lines carry `embellished: "handoff" | "reaction"`.
- Malformed items are skipped. If the client errors or returns an unparseable reply, the
  pass falls back to the unembellished events and logs a WARN.

### Gate

Every string is run through `session_log_parser.redact`, then `session_log_parser.audit` is
run over the serialized episode. If the audit finds anything, `AttributionError` is raised.
The matched text is never echoed.

## Signature

```python
# app/office/role_attribution.py
def attribute(record: dict, *, llm_client=None, embellish: bool = False) -> dict
def classify_command(command: str) -> OfficeRole
def classify_opening(text: str) -> OfficeRole
def embellish_events(events: list[dict], llm_client) -> list[dict]
def load_record(path, session_id) -> dict
def episode_name(record: dict) -> str
class AttributionError(ValueError)

# utilities/3LayersWeeklyGeneration/src/source_adapter.py
class CorpusAdapter:
    def __init__(self, path, *, min_events: int = 5, tag: str | None = None, max_chars: int = 3000)
    def notes(self) -> list[SourceNote]
    def digest(self, rec: dict) -> str
```

## Parameters

- `record` (dict, required): one sessionCorpus export record.
- `llm_client` (optional): an object with `.complete(system_prompt, messages) -> str`.
  Required when `embellish=True`.
- `embellish` (bool, default `False`): run the LLM hand-off/reaction pass.
- `CorpusAdapter.path`: an export `.jsonl` file (must exist).
- `min_events` (default 5, matching `episode_validator.MIN_EVENTS`): skip sessions with
  fewer events.
- `tag`: keep only records whose `tags` list contains it.
- `max_chars` (minimum 500): the digest cap. The body is trimmed first so the `Result:`
  line always survives.

## Return Value

`attribute` returns the episode dict above. `CorpusAdapter.notes()` returns a list of
`SourceNote` with:

- `id = slug("<source_tool>-<session_id>")`
- `title` = the first ask
- `rel_path = "<file>#<session_id>"`
- `hash` = sha256 of `source_tool:session_id:digest`

## Dependencies

- `office.roles` (`SEAT`, `OfficeRole`, `can_direct`)
- `session_log_parser` (`audit`, `redact`, `clean_user_text`, `MAX_OUTPUT_CHARS`)
- `llm_client.build_llm_client` (only for CLI `--embellish`)
- Standard library only for `CorpusAdapter`

## Usage Examples

```bash
# sessionCorpus export -> one office episode
python app/office/role_attribution.py data/corpus.jsonl --session <SESSION_ID> --out ep.json
python app/office/role_attribution.py data/corpus.jsonl --session <SESSION_ID> --out ep.json --embellish
```

```python
from office.role_attribution import attribute
from episode_validator import validate_episode
ep = attribute(record)            # deterministic
validate_episode(ep)              # passes: shape, audit, show slots, dry run
```

```python
import source_adapter as sa
notes = sa.load_source("data/corpus.jsonl")                       # all sessions >= 5 events
features = sa.CorpusAdapter("data/corpus.jsonl", tag="feature").notes()
```

## Error Handling

- `AttributionError` is raised when:
  - the record is not a dict with an `events` list
  - `embellish=True` is passed without a client
  - the finished episode fails the leak audit
  - (`load_record`) the session is not found

  The message never includes session content.
- The CLI logs the error and exits with status 1.
- `CorpusAdapter` raises `FileNotFoundError` for a missing file. Unparseable lines are
  skipped with a WARN that gives only the line number.

## Changelog

| Version | Date | Change |
|---|---|---|
| v1.0.0 | 2026-09-27 | Initial version (OB-12): CorpusAdapter, rule attribution, optional LLM pass, audit gate, CLI. |
