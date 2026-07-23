# repos/ — Vendored third-party avatar repositories

This directory holds vendored snapshots of third-party avatar projects that
the worker's avatar pane can render via the provider layer in
`app/avatar_providers/` (selected per worker with `avatar.provider` in the
worker config — see `docs/avatar_providers.md`).

Snapshots are plain copies (no `.git`), committed to this repo so the Docker
image build needs no extra fetch step.

## Vendored repositories

| Directory | Upstream | Pinned commit | License |
|---|---|---|---|
| `ascii-avatar/` | https://github.com/Angelopvtac/ascii-avatar | `5a75e174756e024a6fc0dcaa30fae3eb2a4049ed` (2026-07-12) | MIT |

## Updating a snapshot

1. Clone the upstream repo at the desired commit.
2. Replace the directory contents (everything except `.git`).
3. Update the pinned commit hash in the table above.
4. Rebuild the worker image (`docker build -t vtube-worker:latest .`).

## Notes on ascii-avatar

Only the animation/rendering modules are used (`src/avatar/frames/`,
`renderer.py`, `animation.py`, `state_machine.py`). Its ZeroMQ event bus,
MCP server, Claude Code hooks, and TTS voice engines are **not** wired up —
the avatar is driven by this project's own `app/agent_state.py` state file,
and voice comes from this project's existing TTS pipeline. Consequently its
heavier dependencies (`pyzmq`, `sounddevice`, `mcp`, `anthropic`) are not
installed in the worker image; only `blessed` (and transitively what the
frame modules import) was added to `requirements.txt`.

`assets/` (110MB of PNGs — `gits_frames/` sequences, a banner image, a ghost
portrait) was deleted from this snapshot: it only backs the upstream
`musetalk`/`portrait`/`layered2d` frame sets, which pull in Pillow/numpy and
are deliberately never loaded here (`avatar_providers/ascii_avatar.py` forces
`frame_set="cyberpunk"`, pure ANSI text, regardless of persona). It was dead
weight in every Docker build context and worker image. If a future frame set
needs image assets, re-vendor just that subdirectory rather than restoring
the whole thing.
