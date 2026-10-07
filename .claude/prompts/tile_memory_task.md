# Task: find why app/tile_pane.py processes grow to ~5 GB RSS each

Repo root is the cwd of your tools; code lives in `app/`.

## Observed (production, Python 3.10, glibc, aarch64)
- Container runs 8 `python3 /app/tile_pane.py --slot tuber_N` processes plus one `replay_pane.py`.
- After ~24 h each tile_pane has ~4-5 GB RSS, ALL private anonymous memory; one ~3.7 GB brk `[heap]` region.
  Not shared memory, not file-backed. /dev/shm is only 114 MB.
- tuber_0 grew ~0.8 MB in 40 s (~1.8 GB/day). VmHWM of tuber_6 was 4.37 GB vs 3.79 GB RSS, so some memory is released.
- Each tile has a 3D avatar head (`app/tile_avatar.py` -> `app/avatar_providers/codec_avatar.py`),
  rendered at ~12 fps in a separate GPU subprocess (`app/gpu_render_worker.py`) whose frames come back through
  shared memory, then `render_tick` converts to uint8, builds a pygame surface and blits it.
- Dialogue history in `tile_pane.py` is a bounded deque. The tile also loads narration airings (WAV bytes) from Postgres via `app/narration_store.py`.

## Already ruled out
Mesh size (614 triangles), unbounded dialogue deque, /dev/shm, /tmp.

## Your job
1. Read `tile_pane.py` (main loop around line 1288 onward, the Performer/ticker threads, `AvatarDriver` ~line 775-1000,
   `perform_*` airing code ~1100-1250), `tile_avatar.py`, `avatar_providers/codec_avatar.py`, `gpu_render_worker.py`,
   `replay.py`, `narration_store.py`, `audio_player.py`, `gaze.py`, `relay_io.py`, `agent_state.py`.
2. Look for anything that accumulates per frame / per line / per airing: lists/dicts/sets that only grow,
   caches without eviction, retained WAV bytes / numpy arrays / pygame surfaces, per-tick allocations of full frames
   (glibc fragmentation candidates), threads or subprocesses created and not joined, memoryview/bytes held from DB rows,
   state files re-read into growing structures.
3. Where possible, TEST a hypothesis with `run_python` (e.g. allocate/free frame-sized numpy arrays in a loop and watch
   RSS from /proc/self/status, with and without `MALLOC_ARENA_MAX`/`MALLOC_TRIM_THRESHOLD_`). The GPU/pygame stack is not
   available here, so only test pure-Python/numpy behaviour.
4. Final report: ranked list of candidate causes with `file:line` evidence, which are leaks (unbounded growth) vs.
   fragmentation, the minimal fix for each, and one concrete experiment to confirm in production. Do NOT edit files.
