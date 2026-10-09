# Live table: operator control

The table GM (worker `tuber_0`, role `table_gm`) starts scenes when an operator asks.
Messages go through message-api (the only external publisher; they arrive `from: operator`).

## Start mode (`table.start_mode` in `config/table/<pack>.yaml`)

| mode | after boot | after a scene |
|---|---|---|
| `manual` (default for both packs) | waits for a `scene_request` | goes idle |
| `auto` | starts by itself at the saved position (else `start_index`) | starts the next; plays at most `max_scenes` automatically |

The position (index of the last started scene) is saved in `table.position_file`
(default `/data/world-state/table_<pack>_position.json`, the shared world-state volume),
so "next" after a GM restart continues the arc. Delete the file to start the arc over.

### Long unattended auto runs (added 2026-10-09 for the 1-hour D&D run)

| key | effect |
|---|---|
| `auto_duration_s` | no new scene after this many seconds since the first auto scene; the running one finishes |
| `auto_pace: {words_per_s, per_line_s, lead_s}` | the GM models the roundtable voice queue (each committed line = `per_line_s + words/words_per_s`, back to back) and starts the next scene only `lead_s` before it drains. Without it the GM writes ~2x faster than the voices speak, the live spool backs up and lines are dropped past `live.max_age_s` |
| `auto_skip_stuck` (default true) | a scene the GM is stuck on (2 failed GM calls) is skipped instead of waiting for an operator |
| `auto_max_consecutive_stuck` (default 3) | after this many skips in a row the table halts and stays stuck (model probably down); any operator `scene_request` clears the halt |

A new story run should get its own `position_file` so an old index doesn't skip the opening.
Story fixtures: `scripts/export_table_fixture.py <run_id>` writes `tests/table/fixtures/<run_id>.json`
and prints the contract count the table will see.

## CLI

```bash
.venv/bin/python scripts/table_ctl.py status            # idle/running, scene, round, next scene
.venv/bin/python scripts/table_ctl.py next              # start the next scene in the arc
.venv/bin/python scripts/table_ctl.py next --force      # end the running scene first
.venv/bin/python scripts/table_ctl.py start 6           # by index
.venv/bin/python scripts/table_ctl.py start first-standup-arc.first-standup-arc-n0.first-standup-arc-n0-003
.venv/bin/python scripts/table_ctl.py stop              # skip the running scene
```

It prints the GM's `table_status` reply (read back from message-api `GET /logs/messages`).
Exit codes: 0 ok, 1 busy/error, 2 no reply.

## curl

```bash
curl -s -X POST localhost:8090/messages -H 'Content-Type: application/json' \
  -d '{"to":"tuber_0","type":"scene_request","payload":{"next":true}}'
# {"scene_id":"<id>"} | {"index":N}, optional "force":true ; "type":"scene_stop" ; "type":"table_status_request"
```

## Control panel (port 8091)

Section **Live table**: Next scene, Next scene (force), Stop scene, Status, and a
"start a scene" form (payload `{"index": N}` or `{"scene_id": "..."}`). The panel shows that the
message was sent; the GM's reply is in the message log (or `table_ctl.py status`).

## Replies

Every request gets `table_status` back to the sender:
`{state, scene_id, round, phase, index, next_scene_id, total, mode, result, error}` where
`result` is `started | busy | stopped | idle | status | error`. A running scene answers `busy`
unless `force` is set. No transcript, intent or reasoning is ever in a status.

Code: `app/table/control.py`, `app/agent_handlers/table_gm.py` (TableRuntime), tests
`tests/table/test_table_control.py`, `tests/test_table_ctl.py`.
