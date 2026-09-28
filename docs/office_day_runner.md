# office_day_runner (OB-30)

## Overview

`app/office/day_runner.py` is the CEO-as-GM **day runner** for the
ashiorid_office show. It runs the office day from the CEO worker's idle tick:
`agent_handlers.office.ceo_idle_tick` calls the runner that `set_day_runner()`
installed. Each tick, the runner places `now` on the office clock
(`office.clock.office_time`, docs/office_clock.md) and does whatever is due.
Each thing happens exactly once, and the state file makes it survive restarts.

| Local time (America/New_York) | What the runner does |
|---|---|
| 06:00 (s0 → s1) | `day_start`, `phase_change(morning)`, then today's directive (`issue_directive`) |
| s1 – s3 | one active directive at a time; at most 2 follow-ups, each only once the previous one is done; the stall detector |
| 12:00, 18:00 | `phase_change(build)`, `phase_change(ship)` |
| 23:45 | wrap-up: a `wrap_up` broadcast asking for status reports, a CEO narration line, and a "carried over" comment on each open directive issue. No new directives after this. |
| 00:00 (s3 → s0) | `day_end(<the day that ended>)`, `phase_change(off)`, then the s0 hand-off to the playlist |
| s0 | no live work. The playlist owns the stream until 06:00. |

The clock messages come from the protocol builders (`office.protocol`
`build_day_start` / `build_phase_change` / `build_day_end`) and are validated
there. The sender is the worker id when it is a clock sender (the CEO seat
`tuber_0`), otherwise `office_clock`. `phase_change`, `wrap_up` and `day_end`
join the `day_start` message's correlation chain. The s0 `phase_change` after
`day_end` starts a new chain.

### Directive sources

The runner tries the sources in order, and the first hit wins. The same order
is used for follow-ups (`context["kind"] == "follow_up"`).

1. **Arc plan spine** (OB-40): an injectable provider. There is none by
   default.
2. **Corpus feature session** (`CorpusFeatureSource`): records in a
   sessionCorpus JSONL export whose `tags` contain `feature` and that were
   not used on an earlier day, oldest `started_at` first. Each record goes
   through OB-12 `role_attribution.attribute` (redaction plus the leak audit
   as a hard gate). The runner sends a short outline of the attributed
   episode to the CEO's LLM (persona prompt + `REWRITE_INSTRUCTION`), which
   rewrites it as a Fraud-Stop directive (`{"title", "text"}` JSON; a plain
   text reply is taken as the text). The rewrite is leak-audited again, and a
   rewrite that fails the audit is refused. With no LLM client, the source
   is skipped.
3. **Gitea issues backlog** (`GiteaIssueBacklog`): the oldest open Fraud-Stop
   issue that has no `directive` label (those are the CEO's own directive
   issues) and was not used before. It can be narrowed to a `backlog_label`.

A source that returns `None` or raises (the error is logged) falls through to
the next one. Every picked `ref` (`arc:…`, `corpus:<session_id>`,
`issue:<n>`) is remembered in `used_refs` (the newest 500) so it is never
picked again.

### Guards

- **One active directive per day, plus at most 2 follow-ups.** A follow-up
  goes out only after the completion probe reports the previous directive
  done. `max_follow_ups` in config can lower the cap to 0 or 1 but never
  raise it above 2.
- **Retry cap.** Each failed attempt counts: every source empty,
  `issue_directive` returning `[]`, or `issue_directive` raising. The cap is
  `MAX_DIRECTIVE_ATTEMPTS = agent_handlers.manager.MAX_BUG_RETRIES` (3) per
  day, with `retry_backoff_s` between attempts. After the cap the runner logs
  `directive_retries_exhausted` (WARN) and stops trying for that day. The
  stall detector then fills the air.
- **Stall detector.** During work hours before 23:45: when nothing has
  happened for `stall_minutes` (default 45), the runner calls
  `playlist.stall(...)` and, if the playlist returns a replay request, sends
  it as `replay_request`. "Something happened" means a directive issued, a
  directive done, day_start, or a newer timestamp from the activity probe.
  The detector re-arms `stall_minutes` after each fallback.

### Restart safety

The state lives in one JSON file (`relay_io.atomic_write_json`, fsync). It
records the day, whether it has started, wrapped up or ended, the last
phase announced, the s0 hand-off date, today's directives, attempts, the stall
and poll timestamps, and `used_refs`. The file is saved after every emission.
A directive is recorded as `issuing` **before** `issue_directive` is called,
so a crash mid-issue can never lead to a second directive that day: a
leftover `issuing` record counts as issued, and it blocks follow-ups. A
restart after a missed night closes the old day late (wrap-up if it was
missed, then `day_end`) before the new `day_start`. A corrupt state file, or
one with a different `version`, starts fresh (WARN). An unwritable path logs
an ERROR, and the runner keeps its state in memory.

## Signature

```python
# app/office/day_runner.py
DirectiveSource = Callable[[str, dict], Optional[dict]]   # (day, context) -> {"text", "title"?, "ref"?} | None

class Playlist(Protocol):                                 # OB-33 implements it
    def off_hours(self, day: str, context: dict) -> Optional[dict]: ...
    def stall(self, day: str, context: dict) -> Optional[dict]: ...
    # optional: def resume_live(self, day: str, context: dict) -> None

class DayRunner:
    def __init__(self, *, clock=None, epoch=date(2026, 9, 27), tz="America/New_York",
                 state_path="/data/world-state/office_day_runner.json",
                 arc_provider=None, feature_source=None, backlog_source=None,
                 playlist=None, completion_probe=issue_closed_probe, activity_probe=None,
                 gitea_factory=None, issue_directive=None, stall_minutes=45.0,
                 retry_backoff_s=300.0, completion_poll_s=300.0, max_follow_ups=2,
                 replay_target=None, recipients=None)
    def __call__(self, worker_id, agent_config, llm_client, producer, state_path=None) -> list[dict]
    def tick(...)  -> list[dict]      # same, but raises (tests)

class CorpusFeatureSource:   # (export_path=None, tag="feature", loader=None, max_candidates=5)
class GiteaIssueBacklog:     # (label=None, skip_labels=("directive",))
def issue_closed_probe(directive: dict, context: dict) -> bool | None
def mtime_probe(paths: list[str]) -> Callable[[], float | None]
def load_factory(spec: str) -> object          # "module:attr"
def day_runner_settings(agent_config) -> dict
def day_runner_enabled(agent_config) -> bool
def build_day_runner(agent_config, *, worker_id="ceo", clock=None, **overrides) -> DayRunner
```

## Parameters

### Contracts for other work packages

**Playlist (OB-33, `app/office/playlist.py`).** Any object with
`off_hours(day, context)` and `stall(day, context)`. It is wired through the
`playlist: "office.playlist:<factory>"` config key, where `factory(agent_config)`
returns the object. `context` holds `worker_id`, `day`, `reason`
(`"off_hours"` | `"stall"`), `directives` (today's
`{title, issue, status, source, kind}` list), and `idle_minutes` (stall only).
Each call returns one of:

- `{"episode": <name>, "speed"?, "cast"?, "worker_name"?}`: the runner sends a
  `replay_request` bus message (payload plus `reason` and `day`) to
  `replay_target` (default: the CEO worker itself). The existing
  `agent_handlers.replay_relay.handle_replay_request` then writes the replay
  pane's request file.
- `None`: the playlist queued something itself, or has nothing to play.

The shipped implementation is `office.playlist:day_runner_playlist`
(`DayRunnerPlaylist`, docs/office_playlist.md "Wiring it into the day
runner"). It rotates ambient scenes and approved `office-*` replays, requests
ambient scenes by their approved library name only, and is tuned by
`playlist_options` (below).

`off_hours` is called once per s0 (at 00:00, or on the first tick in s0 after
a restart). The optional `resume_live(day, context)` is called right after
`day_start`. Exceptions are logged (`playlist_failed`) and do not stop the
runner. With no playlist configured, the runner logs `off_hours_no_playlist`
or `stall_no_playlist` (WARN).

**Arc provider (OB-40).** A callable `provider(day, context) -> dict | None`,
wired as `arc_provider: "module:factory"` (`factory(agent_config)` returns the
callable). It returns `{"text": <directive>, "title"?: str, "ref"?: str}` for
today's spine beat, or `None` when the arc has nothing for this day or kind.
`context` holds `kind` (`primary` | `follow_up`), `index`, `office_time`
(`OfficeTime`), `directives`, `used_refs`, `worker_id`, `agent_config`,
`llm_client`, `persona` (a zero-argument callable that returns the CEO persona
prompt) and `gitea` (`GiteaClient` or `None`).

### Config (`agent.office.day_runner` on the CEO worker)

The block's presence (or `day_runner: true`) turns the runner on.
`enabled: false` turns it off.

| Key | Default | Meaning |
|---|---|---|
| `enabled` | `true` when the block is present | master switch |
| `state_path` | `/data/world-state/office_day_runner.json` | state file (use a persistent volume); `null` = memory only |
| `epoch` | `agent.office.epoch`, else `2026-09-27` | loop epoch Sunday (only affects `loop_week` in logs) |
| `tz` | `agent.office.tz`, else `America/New_York` | office time zone |
| `stall_minutes` | `45` | idle minutes before the fallback replay |
| `retry_backoff_s` | `300` | wait between failed directive attempts |
| `completion_poll_s` | `300` | how often the completion probe polls Gitea |
| `max_follow_ups` | `2` | can be lowered, never raised above 2 |
| `replay_target` | the CEO worker id | recipient of `replay_request` |
| `playlist` | none | `"module:factory"` (OB-33: `office.playlist:day_runner_playlist`) |
| `playlist_options` | `{}` | read by `day_runner_playlist`: `message_api_url` (else env `MESSAGE_API_URL`), `scenes_dir`, `refresh_s`, `timeout_s`, `prefix`, `library`, `speed`, `cast`, `worker_name` |
| `arc_provider` | none | `"module:factory"` (OB-40) |
| `corpus_export` | none (source 2 off) | sessionCorpus JSONL export path |
| `corpus_tag` | `feature` | tag that marks feature sessions |
| `backlog` | on | `false` disables source 3 |
| `backlog_label` | none | only open issues with this label |
| `activity_paths` | none | files whose mtime counts as activity (e.g. the workers' avatar state files, the show log) |

Gitea access reuses `agent.office.gitea` through
`agent_handlers.office.build_gitea_client`, which reads the token by env-var
name only. Without Gitea, the backlog source is empty, no directive issue is
opened, and the completion probe returns "unknown". In that case the runner
sends no follow-ups.

## Return Value

`DayRunner(...)(...)` returns the list of messages sent on that tick (for
example `[day_start, phase_change, directive × 4]` at 06:00). It returns `[]`
when nothing was due or when the tick failed; a failed tick is logged as
`tick_failed`.

## Dependencies

- `office.clock` (`office_time`, `PHASES`, `DEFAULT_TZ`) and `office.protocol`
  (clock builders, `CLOCK_SENDER(S)`)
- `office.role_attribution.attribute` and `session_log_parser.audit`
  (source 2)
- `agent_handlers.office`, imported lazily: `issue_directive`,
  `build_gitea_client`, `persona_prompt`, `_speak`, `_gitea_call`
- `agent_handlers.manager.MAX_BUG_RETRIES`, `message_bus.build_message`,
  `agent_state.write_state`, `relay_io.atomic_write_json` / `read_json`

The hook in `agent_handlers/office.py` works like this: on the first CEO tick,
`ceo_idle_tick` checks for an `agent.office.day_runner` block. If there is
one, it builds the runner with `build_day_runner` and installs it with
`set_day_runner`. This happens once per process, and `_reset_office_state()`
re-arms it. A runner installed explicitly with `set_day_runner` always wins.
No new message handler is registered, because `day_start` / `day_end` are
emitted, not consumed, by the runner.

## Usage Examples

Config-driven (the normal path; this is the shape of
`config/workers/office/ceo.yaml`):

```yaml
agent:
  role: ceo
  office_role: ceo
  office:
    gitea: {token_env: GITEA_TOKEN_OFFICE}
    day_runner:
      enabled: true
      # docker-compose.office.yml mounts the office-day-runner volume here
      state_path: /data/office-state/day_runner.json
      playlist: office.playlist:day_runner_playlist
      playlist_options: {message_api_url: http://message-api:8000}
      corpus_export: /data/corpus/export.jsonl   # optional mount; missing file = source skipped
      backlog: true
      stall_minutes: 45
```

The directory of `state_path` must exist (the state file is written
atomically next to a temp file in the same directory); a volume mount point
does. A missing `corpus_export` file logs one WARN per pick and falls through
to the backlog.

Wired by hand (tests, or a custom playlist):

```python
from agent_handlers import office
from office.day_runner import DayRunner, GiteaIssueBacklog

runner = DayRunner(clock=fake_clock, state_path="/tmp/dr.json",
                   arc_provider=lambda day, ctx: {"text": "Ship the velocity rule."},
                   backlog_source=GiteaIssueBacklog(), playlist=my_playlist)
office.set_day_runner(runner)
office.ceo_idle_tick("tuber_0", ceo_config, llm, producer)   # -> [day_start, phase_change, directive...]
```

## Error Handling

- `__call__` never raises. Any failure is logged (`event=tick_failed`) and
  returns `[]`. `tick()` itself does raise, which tests use.
- Source, playlist, completion-probe and activity-probe exceptions are logged
  at ERROR, and the runner moves on.
- `issue_directive` failures count against the retry cap.
- An unknown `tz` raises `ValueError` from `DayRunner.__init__`, which
  `ceo_idle_tick` logs as `day_runner_install_failed`.
- A bad `playlist` / `arc_provider` spec logs `plugin_unavailable`, and that
  plugin is left out.
- Nothing logs a token, and nothing logs corpus session content (only refs
  and character counts).

## Changelog

- v1.0.0 (2026-09-28): First version (OB-30). Clock-driven day
  (day_start → directive → phase edges → 23:45 wrap-up → 00:00 day_end → s0
  playlist hand-off), 3 directive sources with fallback, follow-up and retry
  guards, stall fallback, restart-safe state file, auto-install from
  `ceo_idle_tick`.
- v1.1.0 (2026-09-28): The OB-33 playlist is wired: `office.playlist:day_runner_playlist`
  with `playlist_options`; the CEO config carries the `day_runner` block (state on the
  `office-day-runner` volume at `/data/office-state`). Config example corrected.
- v1.2.0 (2026-09-28): The 23:45 `wrap_up` is built and validated by
  `office.protocol.build_wrap_up` (a clock broadcast; sender `tuber_0` or `office_clock`), and
  every seat answers it (`agent_handlers.office.handle_wrap_up`). The CEO's wrap-up line is
  also published to the live roundtable transcript (`live_pane.publish_office_line`, gated by
  `agent.office.live_transcript`; not added to the tick's returned list). The office config
  sets `replay_target: roundtable` with a seat cast.
