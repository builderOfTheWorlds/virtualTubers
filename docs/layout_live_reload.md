# Live-Reloadable Layout Config — Parked Proposal

## Status

**Parked, not implemented** (2026-07-19). Layout changes (`config/layouts/*.yaml`,
`config/panels/*.yaml`) currently require an image rebuild + container recreate.
This doc captures what was learned scoping a live-reload path and the approach
we'd take if/when this gets picked back up, so the investigation isn't repeated
from scratch.

## Why it's not live today

Two independent reasons stack up, both found while investigating a routine
avatar-pane resize (`config/layouts/coder.yaml`, `avatar`/`filetree` split):

1. **Layout config is baked into the image, not bind-mounted.** `Dockerfile`
   does `COPY config/panels/ /config/panels/` and `COPY config/layouts/
   /config/layouts/` at build time. Only the per-role `config/worker.yaml` gets
   a runtime bind mount (`docker-compose.yml`, relative to the repo root on the
   deploy host: `./config/workers/coder.yaml:/config/worker.yaml:ro`).
   Editing a layout preset in the repo has zero effect on a running container
   until a new image is built.
2. **`build_layout.py` only ever runs once**, in `startup.sh` before the tmux
   session and xterm are created. There's no watcher, no `SIGHUP` handler, no
   poll loop — nothing re-invokes the layout engine after startup. Even if
   `config/layouts/` *were* bind-mounted, a live file edit still wouldn't apply
   without a container restart.

So "live update" needs both: a config source reachable without a rebuild, and
a running process that notices changes and re-applies them.

## The pattern already in this codebase

This project already solves the identical problem — "change behavior without
redeploying the stack" — for the worker on/off switch:

- [app/worker_control.py](../app/worker_control.py) — one Redis key per worker
  (`worker:{id}:enabled`).
- [app/stream_supervisor.py:140-156](../app/stream_supervisor.py#L140-L156) —
  a poll loop (`POLL_INTERVAL_S`) inside the container that reads the flag and
  starts/stops the ffmpeg subprocess accordingly. Reads fail open (Redis down
  ⇒ enabled) so a control-plane outage never silently kills a live stream.
- [services/message-api/api.py:86-101](../services/message-api/api.py#L86-L101)
  — `POST /workers/{id}/enable|disable` writes the Redis key.

See [docs/worker_control.md](worker_control.md) for the full write-up. Any
live-reload design for layout should mirror this shape rather than invent a
new one — it's the established idiom in this repo.

## Proposed approach (mirrors worker_control.py)

1. **`layout_control.py`** (new) — same shape as `worker_control.py`:
   `get_layout(worker_id)` / `set_layout(worker_id, resolved_config)` against a
   Redis key (e.g. `layout:{worker_id}:config`) plus a version/hash so a
   poller can cheaply detect "did this change."
2. **Config source** — either bind-mount `config/layouts/` and `config/panels/`
   like `config/worker.yaml` already is (edit-a-file-on-the-host workflow), or
   skip the filesystem for the *live* path entirely and make Redis the only
   source of truth once a layout has been pushed (cleaner parity with
   enable/disable; a `scripts/push_layout.py` would read the YAML and write it
   to Redis on demand).
3. **A reconciler loop** — new poll loop, or folded into `app/agent.py`'s
   existing tick loop, that: reads live pane geometry via `tmux list-panes -F
   ...`, recomputes desired geometry from the current config, diffs the two,
   and applies only what changed.
4. **`message-api` endpoint** — `POST /workers/{id}/layout`, same shape as
   `enable`/`disable`.

## The wrinkle: tmux panes aren't a single subprocess

Enable/disable only ever starts or stops one ffmpeg process — small, uniform
blast radius. Tmux panes don't have that property, and `build_layout.py` today
only knows how to emit a **from-scratch** `new-session` script; there's no
"reconcile an existing session" mode. Two tiers of change fall out of that:

- **Resize-only** (same panes, same split tree, just a `size` change — e.g.
  the avatar/filetree tweak that prompted this investigation): safe to
  hot-apply as a handful of `tmux resize-pane` calls. Nothing running inside
  the panes is disturbed. **This is the realistic v1 scope.**
- **Structural changes** (add/remove/reorder/retarget a pane): requires
  killing and re-splitting, which kills whatever's running in the affected
  panes — nvim's buffer, htop, the kafka feed's live connection, avatar state.
  Not truly zero-downtime the way enable/disable is. A reconciler should
  detect this case and refuse / log "restart required" rather than silently
  degrade something running on stream.

## Other gotchas surfaced along the way

- **`window-size latest`** (`startup.sh`): the tmux window tracks whichever
  client was most recently active. A second attached client (an operator doing
  a manual `docker exec ... tmux attach`, or a future reconciler process
  itself attaching) with a different terminal size can visibly resize the
  on-stream capture. Any reconciler should act via `tmux` commands against the
  session without attaching a sized client (e.g. `resize-pane` targeting a
  pane, not `attach`), to avoid this.
- Manual live tmux edits (`resize-pane` by hand) are currently the fastest
  iteration loop for tuning geometry, but are ephemeral — lost on the next
  restart/redeploy since `build_layout.py` re-emits the original config every
  time. Final numbers still need to land in `config/layouts/*.yaml`.

## If this gets picked back up

Open questions to settle before implementing:
- Redis-as-source-of-truth vs. bind-mounted-file-plus-watcher — which workflow
  do operators actually want (`curl` a new layout vs. edit-and-push a YAML)?
- Poll interval trade-off, matching `POLL_INTERVAL_S`'s existing pattern.
- Whether the reconciler lives in its own process (like `stream_supervisor.py`)
  or folds into `agent.py`'s tick loop.

## Related docs

- [docs/layout_system.md](layout_system.md) — the config model this would make
  live.
- [docs/worker_control.md](worker_control.md) — the pattern being mirrored.
- [docs/tmux_control.md](tmux_control.md) — existing tmux primitives a
  reconciler would build on.
- [docs/message_api.md](message_api.md) — where the new endpoint would live.
