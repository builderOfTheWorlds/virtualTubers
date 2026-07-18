# Operator command reference

## Overview

Everything the operator (the human "boss") can send to the worker team goes
through `message-api`'s `POST /messages` endpoint (`docs/message_api.md`),
which publishes onto the shared `vtuber.messages` Kafka topic. This doc is a
practical reference for what to actually send — as opposed to `message_api.md`,
which documents the endpoint itself, and `agent.py` (`docs/agent.md`), which
documents how each message type is handled once it arrives.

Message shape is always the same three fields:

```json
{"to": "<worker or broadcast>", "type": "<message type>", "payload": {...}}
```

`to` is one of `coder`, `coder-native`, `coder-opencode`, `coder-aider`,
`manager`, `tester`, or `broadcast` (fans out to all workers). `type`
defaults to `operator_message` if omitted.

The three `coder-*` workers carry a real coding backend
(`docs/coding_backend.md`): a `task_assignment` sent to them edits actual
files in their workspace volume, commits, and gets REALLY tested by the
tester (`docs/test_runner.md`). The legacy `coder` remains narration-only.
To A/B test the backends, send the same task to each — see
`sandbox/README.md` for canonical tasks, and query `coding_backend_runs`
in Postgres for the results.

## Primary operator commands

These two are the ones an operator uses day to day — everything else in the
message-type table (see below) is normally worker-to-worker traffic that
flows automatically once one of these kicks off a ticket.

### `task_assignment` — give a worker something to do

```json
{"to": "coder", "type": "task_assignment", "payload": {"task": "add a healthcheck endpoint"}}
```

- `to`: `coder`, `manager`, or `tester`.
- `payload.task` (str) — the task description.
- `payload.retry_count` (int, optional) — normally omitted; only meaningful
  when the manager re-delegates a bug fix (see below).

The worker narrates the task via its LLM and replies `task_complete` to
`operator`. If `to` is `coder`, this also kicks off the full pipeline: the
coder additionally sends `commit_notification` to the tester, which reports
`test_passed`/`bug_report` to the manager, which reports back to the
operator as a `manager_report`.

### `operator_message` — talk directly to a worker

```json
{"to": "tester", "payload": {"message": "status?"}}
```
(`type` can be omitted — `operator_message` is the default.)

- `to`: any worker, or `broadcast` (all three reply independently — no
  de-duplication, so a broadcast produces three replies on the feed).
- `payload.message` (str) — free-form text.

Any worker (no role gate) answers in-character with an `operator_reply`
addressed back to `operator`. This is a lightweight chat channel — no tmux
demo side effects, no pipeline handoff.

### `replay_request` — perform a Rerun Theater episode

```json
{"to": "coder", "type": "replay_request",
 "payload": {"episode": "2026-07-02_04-27-00_6ecdde82", "speed": 1.5}}
```

- `payload.episode` (str, required) — episode script name in the worker's
  library (`/data/replays`, mounted from `/opt/virtualTubers/replays`;
  `.json` suffix optional). Resolved basename-only — path components are
  stripped, so only library episodes are reachable.
- `payload.speed` (number, optional) — playback speed multiplier.
- `payload.worker_name` (str, optional) — persona name override for the
  dialogue lines.
- `payload.voice` (bool, optional) — set `false` to force a silent airing
  even when the worker's `voice.provider` enables spoken narration
  (docs/revoice.md). Also skips narration reuse below.
- `payload.narration` (str, optional) — set to `"reuse"` to replay the
  most recent cached narration + audio for the episode (scenes rebuilt
  deterministically, cached text and WAVs reattached — no LLM, no TTS)
  instead of generating a fresh airing. Falls back silently to a fresh
  airing when there's nothing usable to reuse: the narration store is
  unavailable (no `POSTGRES_*` env / `psycopg2` / DB down), the episode
  has never been cached, or the cached scene structure no longer matches
  the script (docs/narration_store.md, docs/replay_pane.md).
- `payload.cast` (object, optional) — turns a solo airing into a **duet**:
  a `{speaker: worker_id}` map assigning who voices which speaker
  (v1 scripts only ever produce `"boss"` and `"coder"`). The worker `to`
  is addressed becomes the **director** — it performs on every stream
  involved, but only plays audio for the speaker(s) cast to itself; every
  other cast worker (a **follower**) is invited over the bus and performs
  the same episode on its own stream, voicing only its own speaker(s). A
  speaker not present in `cast` defaults to the director. Full protocol,
  timeouts, and deployment requirements: docs/duet_replay.md.

Any worker (no role gate) queues the episode for its "Rerun Theater" pane
and confirms with an `operator_reply`. The show only actually appears if
the worker's layout includes the replay pane (`layout.preset: replay` or
`LAYOUT_PRESET=replay` — see `docs/replay_pane.md`); an unknown episode is
reported in the worker's container logs. Episodes are pre-parsed, redacted
scripts of past dev sessions built by `scripts/build_replay_library.py` —
display-only, nothing is re-executed.

**Duet example** — the coder directs and voices the `coder` speaker; the
manager follows and voices the `boss` speaker; both streams show the
whole episode:

```json
{"to": "coder", "type": "replay_request",
 "payload": {"episode": "2026-07-02_04-27-00_6ecdde82",
             "cast": {"boss": "manager", "coder": "coder"}}}
```

**Duets never degrade to solo.** An invalid `cast` (not a dict, empty, or
non-string keys/values) is rejected immediately with an `operator_reply`
error and nothing is queued. A valid cast that later fails to come
together — the director can't reach Kafka or the narration store, voice
prep fails, or a follower never publishes ready within
`REPLAY_READY_TIMEOUT_S` (default 60s) — refuses the whole airing outright
instead of falling back to a solo show. Most refusals send a **second**
`operator_reply` with `{"error": "..."}` once the director gives up
(distinct from the initial "queued" reply); the one exception is a
director with no Kafka producer at all, which can't send anything further
back — check that worker's container logs (`duet refused: ...`) if a
duet airing seems to have silently gone nowhere. See docs/duet_replay.md
for the full refusal rule and timeout table.

## Manual/debug commands

`message-api`'s `type` field can be overridden to inject any of the other
message types directly — useful for testing a stage of the pipeline without
running the whole thing end to end. These are normally sent automatically by
one worker to the next; the operator sending them manually skips ahead.

| Type | `to` | Payload fields | Effect |
|---|---|---|---|
| `commit_notification` | `tester` | `task`, `commit_message`, `retry_count` | Triggers a test run without going through the coder first. |
| `retest_request` | `tester` | `task`, `retry_count` | Same handler as `commit_notification` — there is no automatic sender for this type yet, so it's an operator-only lever. |
| `bug_report` | `manager` | `task`, `severity`, `repro`, `retry_count` | Triggers manager triage: re-delegates to the coder (`retry_count + 1`), or escalates to the operator once `retry_count >= 3`. |
| `test_passed` | `manager` | `task` | Triggers the manager's celebration narration + a `manager_report` (`report_type: "milestone"`) to the operator. |
| `task_complete` | `manager` | `task` | Manager acknowledges only — sends nothing back onto the bus by design (avoids duplicating the tester run the coder's own `commit_notification` already triggered). |
| `clarification_request` | `manager` | `task`, `error` | Forces an immediate `manager_report` (`report_type: "blocker"`) escalation to the operator. |
| `viewer_joined` | any worker | `username`, `channel`; optional `episode`, `voice`, `narration` | The worker queues a Rerun Theater episode for its replay pane (random library pick unless `episode` is given; `voice`/`narration` forwarded as in `replay_request`) and greets the viewer in character, introducing the show (console + avatar bubble; no bus reply). **`cast` is NOT forwarded** — a `viewer_joined` payload can never start a duet, even if `cast` is included; it always queues solo. Use `replay_request` directly for duets. Normally sent automatically by the `twitch-presence` service when a viewer enters that worker's Twitch chat (docs/twitch_presence.md) — inject it manually to test without Twitch. |

All of these except `viewer_joined` are role-gated: sending one to a worker
whose configured `agent.role` doesn't match (e.g. `bug_report` to the
tester) is a no-op — the worker logs `ignoring <type> (role=..., expected
...)` and does nothing.

## Feed-only types (not operator-sendable)

Two types only ever appear as *output* — sending them yourself doesn't do
anything useful, but knowing what they mean helps reading the Kafka feed
pane:

- **`manager_report`** — manager → operator. Payload `report_type`:
  `"milestone"` (celebrating a passing suite), `"blocker"` (a worker's
  `clarification_request` escalated), or `"escalation"` (a bug hit
  `MAX_BUG_RETRIES = 3`). Highlighted cyan on the feed.
- **`operator_reply`** — any worker → operator, in response to an
  `operator_message`. Highlighted blue on the feed.
- **`status_update`** — heartbeat traffic, sent by every worker every tick.
  Hidden from the feed by default (heartbeat flood filter).

## Full pipeline example

```bash
curl -X POST http://localhost:8090/messages \
  -H "Content-Type: application/json" \
  -d '{"to": "coder", "type": "task_assignment", "payload": {"task": "add a healthcheck endpoint"}}'
```

Watch the Kafka feed pane: coder → `task_complete` + `commit_notification` →
tester → `test_passed` **or** `bug_report` → manager → `manager_report`
(milestone) or a re-delegated `task_assignment` back to the coder. A bug path
terminates within 3 retries with an `escalation` report to the operator.

## Using the PowerShell helper

`scripts/send_test_message.ps1` wraps the same endpoint, but does **not**
take `-To`/`-Type`/`-Payload` parameters — it only takes an optional
`-Url`. Instead it's a library of preset `$To`/`$Type`/`$Payload` blocks in
the script body; uncomment exactly one block (including its `$To`/`$Type`
lines, not just `$Payload`) and run it:

```powershell
.\scripts\send_test_message.ps1
.\scripts\send_test_message.ps1 -Url http://localhost:8090/messages
```

The script resets `$To`/`$Type`/`$Payload` to `$null` at the top and
errors out if any of the three is still unset after the preset section —
without that guard, a stale value from an earlier run **carries over**
when the script is dot-sourced (VSCode's F5 does this), so a previous
run's `$Type` can silently apply to this run's `$Payload`. Confirmed
2026-07-18: a `replay_request` duet payload was sent as `viewer_joined`
this way, which drops `cast` (see the table above) and aired solo with no
error.

The file is UTF-8 **without a BOM**, which PowerShell 5.1 reads as the
system ANSI codepage — a non-ASCII character (e.g. an em dash `—`) inside
a **double-quoted string** can decode to a smart quote and prematurely
terminate the string, breaking the parser several lines later with a
confusing error. Keep string literals in this file ASCII-only, or use `-`
instead of `—`; non-ASCII text is fine in `#` comments and `<# #>` blocks,
which aren't string-parsed the same way.

## See also

- `docs/message_api.md` — the HTTP endpoint itself.
- `docs/agent.md` — how each message type is handled worker-side.
- `docs/message_bus_feed.md` — reading the Kafka feed pane (highlight colors, filters).
- `docs/duet_replay.md` — full multi-worker duet replay protocol reference
  (`cast`, `replay_invite`/`replay_ready`/`replay_cue`/`replay_end`,
  timeouts, deployment requirements).
- `.claude/prompts/worker_interaction_kafka.md` — the implementation plan that landed this collaboration flow.
