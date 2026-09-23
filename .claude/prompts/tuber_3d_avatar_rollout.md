# Plan: roll the 3D codec avatar out to the remaining tubers, the GM, and the roundtable

Status: PLAN ONLY (no code changed yet). Written 2026-09-22 after
`fc438ac` / `ac40102` landed the "sized to the avatar" `tuber_base` layout
and the pinned window geometry on `config/workers/coder.yaml`.

Decisions taken (user, 2026-09-22) — settled, not open:

- **Keep the current setup and copy it.** The pinned `window_pos` / `width`
  / `height` / `background` block is duplicated verbatim into every
  remaining worker config. No new `window_preset` key, no shared code-side
  constant. A drift test (A4) is what keeps the copies honest.
- **`tuber_0` STAYS the GM and becomes a `tuber_base` channel.** The
  roundtable moves out into its own **separate container** with its own
  identity. (This replaces the earlier "new `worker-gm-solo` container, leave
  the roundtable on `tuber_0`" shape — same container count, but `tuber_0`
  keeps being the GM character rather than the show host.)
- **Use the GPU** for the roundtable tiles — hardware acceleration, not the
  CPU fallback.
- **The roundtable is pinned to 1920x1080 + `FONT_SIZE=7`**, matching the six
  tubers, and the stale `font 14 => 160x44` tile math is corrected to the
  real 320x90 grid.

Scope:

1. **Part A** — the five other `tuber_base` workers (coder-native,
   coder-opencode, coder-aider, tester, manager) get the same *working*
   avatar wiring coder has, plus distinct faces.
2. **Part B** — `tuber_0` converts to a `tuber_base` channel (GM as a
   character, with its own 3D avatar); a new `worker-roundtable` container
   takes over hosting the show.
3. **Part C** — the roundtable channel gains GPU-rendered 3D heads in its
   eight tiles, at a pinned 1080p / font-7 grid.

---

## Current state (verified in-repo, not assumed)

- All six character workers already run `provider: codec_avatar`
  (`config/workers/*.yaml`) and all six use `layout.preset: tuber_base`.
- **Only `coder.yaml` has the geometry pinned** — `window_pos: [821, 8]`,
  `width: 549`, `height: 470`, `background: "#2b2b2b"`. The other five
  deliberately omit `window_pos` and rely on
  `codec_avatar._resolve_geometry` -> `pane_geometry.detect_pane_rect()`
  (tmux cell rect x xterm pixel rect via `xdotool`), which `coder.yaml`'s
  own comment records as **not reliably landing on gx10**. They also omit
  `background`, so any un-drawn pixel is hard black instead of the console
  grey — a black slab over the layout.
- Faces are only weakly differentiated: `app/character_schema.py` `PRESETS`
  gives `chadwick` a real slider set and gives `nyx1/oko2/ada3/tess3/max1`
  `SLIDER_DEFAULTS` + a distinct `accent_color` only. Unique silhouettes are
  explicitly flagged as follow-up in that file.
- The GM (`config/workers/tuber_0.yaml`) is still `provider: builtin`, with
  no `codec_avatar` block and no preset in `PRESETS`.
- The roundtable is untouched by the 3D work: `roundtable.yaml` is an even
  4x2 grid of `tile` panes; `tile_pane.py`'s avatar is `TILE_FACES` — three
  rows of ASCII (`( ^_^ )`), `TILE_AVATAR_LINES = 3`, deliberately
  placeholder ("the seam the real avatar work lands in").
- **`worker-gm` has no `CAPTURE_RESOLUTION` line at all** in
  `docker-compose.yml` (the six tubers each pin
  `${CAPTURE_RESOLUTION:-1920x1080}`), and **no service sets `FONT_SIZE`** —
  both fall through to `startup.sh`'s defaults (1920x1080, `FONT_SIZE=7`).
  So the roundtable is *already* rendering at the target resolution and
  typeface; what is wrong is that it is implicit, and that three places
  still do their arithmetic at the old font-14 / 160x44 grid:
  `config/layouts/roundtable.yaml:61`, `app/tile_pane.py:216`,
  `.claude/prompts/roundtable_stream_design.md:343`.
- `worker-gm` has `cpus: "2.5"` and, unlike all six tubers, **no
  `reservations.devices` nvidia block** and no
  `NVIDIA_DRIVER_CAPABILITIES` — its comment claims "it doesn't render its
  own tile", which Part C makes false.
- Rendering plumbing Part C reuses as-is: `FrameSource` (pure, headless),
  `GPURenderWorker` (GPU in a separate process — **required** on gx10: one
  process cannot own both an EGL context and an SDL window; it either
  BadAccess-crashes or silently degrades to llvmpipe),
  `pixel_raster.composite_on_background`,
  `codec_avatar._detect_truecolor_visual_id` (the DirectColor/black-capture
  fix).
- Cost data (`docs/gl_raster_benchmark.md`): CPU `pixel_raster` ~12-14 fps
  at 560x700; GPU ~290 fps — measured on the dev RTX 3080, **not** on gx10.
- There is **no config inheritance**: `message_bus.load_worker_config` is a
  bare `yaml.safe_load`. Anything shared between worker YAMLs is physically
  duplicated today, consistent with the copy-it decision.

---

## Part A — the five remaining `tuber_base` workers

All five avatar panes resolve to the *same* pixel rect as coder's (same
preset, same capture resolution), so the change is mechanical.

### A1. Copy the pinned geometry into all five worker configs

In each of `coder-native.yaml`, `coder-opencode.yaml`, `coder-aider.yaml`,
`tester.yaml`, `manager.yaml`, replace the
`# window_pos intentionally omitted — auto-detected...` comment under
`avatar.codec_avatar:` with the same four keys coder uses:

```yaml
  codec_avatar:
    character_params: nyx1
    # Pinned, NOT auto-detected — a verbatim copy of coder.yaml's block.
    # Every tuber_base worker's avatar pane resolves to the SAME rect
    # (same preset, same CAPTURE_RESOLUTION=1920x1080), so these numbers
    # are identical by design, not by coincidence. See coder.yaml for the
    # derivation from tuber_base.yaml's split math. Keep all seven copies
    # (6 tubers + tuber_0) in sync — tests/test_tuber_base_layout.py fails
    # if they drift.
    window_pos: [821, 8]
    width: 549
    height: 470
    background: "#2b2b2b"
```

Identical values in every file; only `character_params` differs. A4's drift
test ships with this step, not as a follow-up.

### A2. Give the five characters real silhouettes

`character_schema.PRESETS` currently differentiates by tint only, which on
stream reads as "one face, five colours". Fill in actual slider sets for
`nyx1`, `oko2`, `ada3`, `tess3`, `max1` the way `chadwick` has them
(`head_width`, `head_taper`, `eye_size`, `eye_spacing`, `jaw_width`,
`nose_length`, `ear_size`, `build`), keeping each existing `accent_color`.

Iterate *without deploying*, per preset:

```bash
source .venv/Scripts/activate
python app/character_preview.py --preset nyx1
python app/character_preview.py --preset nyx1 --set jaw_width=0.9
```

Acceptance: the five preview PNGs under `preview_out/` are distinguishable
from each other and from `chadwick` **in silhouette alone** (greyscale, tint
ignored). `accent_color` stays the secondary cue, not the only one.

### A3. Verify auto-detection is genuinely dead, or fix it

The pinned block now exists in seven files and does **not** survive a
`FONT_SIZE` or `CAPTURE_RESOLUTION` change. That is an accepted trade, but
spend one pass on the root cause — it is cheap, and it would let the copies
be deleted later:

- On the deploy host, inside a worker container:
  `docker exec <c> xdotool search --class XTerm` and
  `docker exec <c> tmux display-message -p -t <pane> '#{pane_left} #{pane_top} #{pane_width} #{pane_height}'`.
- `pane_geometry._xterm_window_pixel_rect` matches `--class XTerm`, while
  `startup.sh` matches by **PID** and its own comment says a class match is
  what previously broke (WM_CLASS is `XTerm`, case-sensitive). Confirm which
  one actually returns a window on gx10.
- `ac40102` already moved the splits to after the xterm resize — the race
  `_resolve_geometry`'s retry loop was papering over — so detection may
  simply work now.

Record the outcome in `docs/gl_raster_benchmark.md`'s open-issues note
either way, so the next person knows whether the pins are load-bearing.

### A4. Tests

- Extend `tests/test_tuber_base_layout.py` (it already loads all six worker
  configs; add `tuber_0` from Part B): assert **every** `tuber_base` worker
  resolves the *same* avatar `window_pos`/`width`/`height`/`background`, and
  that each `character_params` is a key in `character_schema.PRESETS`. This
  is the drift guard that makes copying safe.
- Extend `tests/test_character_schema.py`: every preset in `PRESETS` has a
  distinct slider tuple, so A2 cannot silently revert to defaults-plus-tint.

### A5. Deploy + verify (per the host notes — do NOT skip the frame check)

```bash
ssh secus@192.168.1.23
cd ~/codeProjects/virtualTubers && git pull && ./redeploy.sh -y
```

Then pull a real frame per worker — arithmetic has hidden layout bugs in
this repo before:

```bash
docker exec <container> ffmpeg -f x11grab -video_size 1920x1080 -i :99.0 \
    -frames:v 1 /tmp/shot.png
docker cp <container>:/tmp/shot.png /tmp/ && scp ...
```

Acceptance per worker: a visible, non-black, correctly-tinted head inside
the avatar pane's border; the tmux border still drawn on all four sides; no
black rectangle; and `docker exec <c> tmux capture-pane -p -t worker:0.<N>`
showing `codec_avatar: ready` plus which backend (GPU/CPU) rendered.

---

## Part B — split `tuber_0` (GM character) from the roundtable (show host)

**Yes, this is straightforward** — and it is the better shape. The key
finding, verified in `app/`: **nothing in the code keys the director on the
literal string `tuber_0`.** The only slot-literal references are cosmetic or
validation:

- `build_layout.py:182` — `tuber_0` -> the "Game Master" default *pane
  title* (cosmetic fallback when the roster has no entry).
- `episode_validator.py` — `ROSTER_SIZE = 8` and `SLOT_RE = ^tuber_([0-9]+)$`
  (validates slot *names*, indifferent to which one directs).
- `tile_pane.py:7` / `replay_pane.py:708` — comments.

The director role is resolved from **config**, in
`replay_pane._resolve_local_tiles`. Two gates, both required:

1. `TILE_RELAY_DIR` is set in the environment, **and**
2. `_layout_preset(config) == "roundtable"` **or**
   `config.agent.role == "roundtable"`.

`tuber_0.yaml` currently satisfies both (`agent.role: roundtable`,
`layout.preset: roundtable`). Flip those and it stops being the director;
give them to a new container and it becomes one. No code change required for
the split itself.

### B1. `config/workers/tuber_0.yaml` -> a `tuber_base` GM channel

Edit in place (it stays the GM, per the decision):

- `layout.preset: roundtable` -> `tuber_base`.
- `agent.role: roundtable` -> `manager` (or `custom`) — **this is gate 2**.
  Leaving it as `roundtable` would make this container a director again the
  moment `TILE_RELAY_DIR` is present.
- `avatar.provider: builtin` -> `codec_avatar`, plus the **verbatim**
  `codec_avatar` block from A1 with `character_params: gm0` (B3). Keep
  `name: "GM"` / `title: "Game Master"`.
- **Keep** the `voice.speakers` map and `speaker_names` as-is. It is large and
  hard-won (several live audio-routing bugs are documented in its comments)
  and the new roundtable container needs it verbatim — copy, don't trim.
- **Move** the `roster:` block to the roundtable config (B2): it exists for
  `build_layout._resolve_tile_title`, which only runs for tile panes.
- `stream.key` stays `tuber0`, so the GM keeps the channel it has today.

### B2. New `config/workers/roundtable.yaml` + `worker-roundtable` service

The show host. Start from today's `tuber_0.yaml` (pre-B1) so nothing is lost:

- `agent.role: roundtable`, `layout.preset: roundtable` — gates 1+2.
- The `roster:` block moved from B1, unchanged.
- The whole `voice.speakers` / `speaker_names` / `boss_name` block, verbatim.
- `avatar:` can stay `builtin`: on the roundtable preset the GM's face comes
  from its **tile**, not from an avatar pane (design §4.1 — "the director owns
  no scenes and is silent").
- New identity: `message_bus.worker_id: roundtable`,
  `world_state.worker_id: roundtable`.

Compose service `worker-roundtable`, copied from today's `worker-gm` block
(which is already the roundtable-shaped service — it has `TILE_RELAY_DIR`,
the voices mounts, and `voices.yaml`):

- `WORKER_ID: roundtable`, `DISPLAY_NUM: 106`.
- `STREAM_KEY: ${ROUNDTABLE_STREAM_KEY:-roundtable}`; add it to
  `.env.example`.
- `LAYOUT_PRESET: ${ROUNDTABLE_LAYOUT_PRESET:-roundtable}`.
- `TILE_RELAY_DIR` stays here and is **removed from `worker-gm`** — that is
  gate 1, and it is the cheap belt-and-braces guarantee that the GM channel
  can never act as a director even if its `role` is ever mis-edited.
- `CAPTURE_RESOLUTION` + `FONT_SIZE` pinned (C1).
- Add the nvidia `reservations.devices` block + `NVIDIA_DRIVER_CAPABILITIES`
  (C3 needs the GPU here).

Then `worker-gm` becomes a plain tuber-shaped service: keep its
`voices`/`voices.yaml` mounts (the GM still speaks on its own channel), drop
`TILE_RELAY_DIR`, add the `FONT_SIZE` pin.

### B3. `gm0` preset

Add a `gm0` entry to `character_schema.PRESETS` with its own slider set and a
distinct `accent_color` (`BLUE` is unused by the existing six). Preview it
the same way as A2 before deploying.

### B4. What actually needs care: routing and the cast map

The split is easy; the *wiring that names the director* is what breaks. Three
places to update together:

- **`services/control-panel/panel.py`** — `ROUNDTABLE_WORKER_ID = "tuber_0"`
  (line 75) is who the Play button sends the cast-bearing `replay_request`
  to. This **must** become `roundtable`, or pressing Play sends the show to a
  container that is no longer a director and nothing airs.
- **`WORKER_TO_TUBER_SLOT`** in the same file maps `"manager":
  ROUNDTABLE_WORKER_ID` — i.e. today the manager worker's lines are cast to
  `tuber_0`'s tile. That aliasing is deliberate (see its comment: the
  `manager` speaker id carries GM/Ashiorid narration in the
  `ashiorid_generated_ce8d` episodes). The **cast values are slot ids**, and
  the slots do not change — only the *director's* worker id does. So keep the
  slot map as-is and change only `ROUNDTABLE_WORKER_ID`.
- **`voice.speakers.manager`** in the roundtable config: its long comment
  explains it is pinned to `tuber_0`'s own voice, not MAX-1's, precisely
  because of that aliasing. Copying the block verbatim (B2) preserves this;
  do not "tidy" it.
- `startup.sh` §7.5 launches `replay_pane.py` **unconditionally and
  headless** in every container. That is fine — it is
  `_resolve_local_tiles`' two gates that decide whether it directs, not
  whether it runs. The GM container will keep a harmless idle director
  process, exactly as the six tubers already do today.

### B5. Tests + verify

- Add `tuber_0` to `tests/test_tuber_base_layout.py`'s `WORKER_CONFIGS` — it
  is a `tuber_base` worker now, so it inherits the whole preset suite and
  A4's drift test for free.
- `tests/test_roundtable_layout.py` should load `roundtable.yaml` instead of
  `tuber_0.yaml`.
- A test asserting exactly **one** worker config has
  `agent.role == "roundtable"` — the guard against two directors.
- `services/control-panel/tests/test_replay_play.py` already asserts Play
  reaches "every character worker and the roundtable"; update its expected
  recipient and keep it as the regression test for B4.
- Deploy, then verify **both** channels: a frame grab from `:105.0` (GM, now
  `tuber_base` with a 3D head) and `:106.0` (roundtable, 8 tiles), plus an
  actual show aired end-to-end via the Play button — that is the only thing
  that proves B4's routing is right.

**Host budget check:** still 8 stream workers at `cpus: "2.5"` = 20 cores of
cap, on a box whose 7-worker cap was already sized to "a ~20-core host" with
headroom for sshd — the comment exists because gx10 hit load average 328 and
lost sshd. Run `nproc` and re-tune across all 8 (e.g. 2.0 each) rather than
stacking an 8th 2.5 on top.

### B6. Migration order (this sequence matters)

`tuber_0` cannot be both things during a redeploy. Land it as one commit and
one deploy:

1. Create `roundtable.yaml` + `worker-roundtable` service.
2. Convert `tuber_0.yaml` to `tuber_base`; drop `TILE_RELAY_DIR` from
   `worker-gm`.
3. Update `panel.py`'s `ROUNDTABLE_WORKER_ID` + its test.
4. Deploy both together; confirm the roundtable airs from the new container
   before considering it done.

A partial deploy (new preset without the new container) means **no
roundtable at all** — and per `roundtable.yaml`'s own header, that failure is
silent: tiles run, no director polls the request file, no error anywhere.

---

## Part C — GPU 3D avatars in the roundtable tiles

### C0. The constraint that shapes everything

`stream_supervisor` captures the **X display**, so a pixel avatar must be a
real window positioned over its tile — the same reason `codec_avatar.py`
exists. The roundtable therefore needs **one small window per tile**: a
single window big enough to cover several tiles would occlude their tmux
text.

### C1. Pin 1080p + font 7 on the roundtable, and fix the stale math

Config (no behaviour change — it makes today's implicit defaults explicit,
so a future `.env` edit cannot silently move the roundtable off the grid the
tile math assumes):

- Add to `worker-gm`'s environment:
  `CAPTURE_RESOLUTION: ${CAPTURE_RESOLUTION:-1920x1080}` and
  `FONT_SIZE: ${FONT_SIZE:-7}`.
- Add the same `FONT_SIZE` line to all six tubers + `tuber_0`, so the value is
  stated per service instead of hiding in `startup.sh`.

Then fix the arithmetic that is provably stale. At `FONT_SIZE=7` the cell is
6x12 px (`startup.sh`'s comment), so 1920x1080 is a **320x90** grid and a
4x2 tile is **80 cols x 45 rows** — not the "~38-41 columns, ~11 rows" the
code currently reasons about:

- `config/layouts/roundtable.yaml:61` — "1920x1080 / font 14 => 160x44 ...
  ~40-col, ~22-row tiles" -> 320x90 => 80x45 tiles.
- `app/tile_pane.py:216` — same correction in the geometry comment.
- `.claude/prompts/roundtable_stream_design.md:343` — "~60-70 rows at
  1080p/font 14" -> the real number.
- `FALLBACK_TILE_WIDTH = 36` / `FALLBACK_TILE_HEIGHT = 20`: only used when
  `os.get_terminal_size()` fails, but now wrong by ~2x. Raise to a
  conservative 78 / 44 — still under the true 80/45, per the existing
  "always a little under" rule (an overflowing pane corrupts its neighbour's
  border on air).

Do this **first** in Part C: it is a comments-and-constants change that
makes C2's pixel math reviewable.

### C2. Geometry: reserve real pixels inside each tile

Today's avatar subpanel is `TILE_AVATAR_LINES = 3` = **36 px** tall.
Unusable for a head. With a 480x540 px tile (80x45 cells):

- Avatar subpanel: **18 rows = 216 px** (40% of tile height).
- Head window **200x200**, centred horizontally:
  `x = col * 480 + 140`, `y = row * 540 + 8`. The `+8` inset keeps the tile's
  own tmux border drawn, exactly as coder's `y: 8` does.
- Dialogue rows left: `45 - 2 - 18 - 1 - 1 - 1 = 22`, versus 2-3 today — the
  TEXT subpanel gains room, it does not lose any.

Derive these from the tile's **detected** rect rather than hardcoding
480/540: `tile_pane.py` already has `resolve_tile_width()` /
`resolve_tile_height()` from the pty, and `pane_geometry.detect_pane_rect()`
returns the pixel rect directly. Pin the numbers above as the fallback for
when detection fails — same policy as Part A.

Pure function to add and unit-test:
`tile_avatar_rect(pane_px_rect, avatar_fraction=0.4, window_px=200)`
-> `(x, y, w, h)`.

### C3. GPU rendering, per the decision

Each tile renders through `GPURenderWorker` (`gpu_subprocess: true`) — which
is not optional on gx10 anyway: a tile process owns a pygame window, and
`codec_avatar` sets `AVATAR_HAS_PYGAME_WINDOW=1`, which makes
`gl_raster.is_available()` refuse GPU rendering **in that process**. The GPU
must live in the subprocess.

- `worker-gm` gains the `reservations.devices` nvidia block (copy from
  `worker-coder`) plus
  `NVIDIA_DRIVER_CAPABILITIES: graphics,compute,utility`, which it currently
  lacks. Update the "the GM has no GPU reservation (it doesn't render its own
  tile)" comment — it becomes false here.
- Add `avatar.codec_avatar.fps` (default 30; tiles set 12-15).
  `tick_interval_s` is a hard class attribute today and must become
  config-driven.
- Render at the window size (200x200), not `WIDTH/HEIGHT`'s 560x700. The
  square aspect probably needs a `view_dist` tweak — check with
  `character_preview.py` at 200x200 before wiring it up.
- **Cost to measure, not assume:** 8 tiles = 8 subprocesses, 8 EGL contexts,
  8 shm segments in one container (`shm_size: 256mb`; 200x200x3 ≈ 120 KB
  each, so shm is a non-issue — contexts and VRAM are the risk). gx10 has
  been observed falling back to `llvmpipe` software Mesa, so **verify a tile
  actually logs `rendering on GPU`** rather than trusting the reservation.
  A 200x200 head is 40k px, ~10x cheaper than the benchmarked 560x700, so
  the CPU path would also clear 12 fps if the GPU does not materialise —
  that is the safety net, not the plan.
- If 8 contexts prove too many, the documented fallback is **one** shared GPU
  render process serving 8 requests per tick (a small extension of
  `gpu_render_worker.py`, which already has the queue/shm protocol). Do not
  build it speculatively.

### C4. Who owns the window

`tile_pane.py` is already a long-lived per-slot process in its own tmux pane
with its own `<slot>.state.json`. Give it the avatar:

- New module `app/tile_avatar.py` — owns one `CodecAvatarProvider` for one
  slot: resolves the rect (C2), builds the provider, exposes
  `tick(expression)`.
- `tile_pane.py` starts it in a **daemon thread** (pygame pump + blit; the
  render happens in the GPU subprocess) and feeds it the expression it
  already computes for `TILE_FACES`. Reserve the avatar subpanel's rows as
  **blank** console so the window sits over empty space, not over text.
- **Degrade, never blank** (§5, and `tile.yaml`'s contract): if the provider
  fails to construct or render — no pygame, no display, window creation
  fails, GPU worker dies — log once to stderr and fall back to today's
  3-row `TILE_FACES` ASCII face. A roundtable tile must never go blank; the
  3D head is an upgrade, not a dependency.
- Uncast slots (`tuber_4`, the grey `colour240` override in
  `roundtable.yaml`) get **no** head — they stay ASCII/offline. Keep that
  decision adjacent to the existing grey override so the two cannot drift.

### C5. Per-slot character params on the GM

`tuber_0.yaml`'s `roster` maps slot -> display name, and
`build_layout._resolve_tile_title` reads it. Extend it to carry the avatar
while accepting **both** shapes, so nothing breaks:

```yaml
roster:
  tuber_1: "Chadwick"                                   # still valid
  tuber_2: { name: "Vigil", character_params: nyx1 }     # new
  tuber_7: { name: "Iris",  character_params: iris7 }
```

- Add an `iris7` preset (slot 7 is cast but has no preset today).
- `tuber_0`'s own tile uses the `gm0` preset from B3 — the GM is a character
  like any other on the roundtable (`roundtable.yaml`'s header is explicit),
  and it now has a preset anyway.
- Helper: `tile_pane.resolve_slot_character_params(config, slot)` -> preset
  name or `None` (`None` => ASCII fallback, C4).

### C6. Eight windows on one Xvfb — check these early

Cheap to check now, expensive to discover on air:

- **Visual/colormap:** reuse `_detect_truecolor_visual_id()`. The
  DirectColor pick is what made a window capture as solid black while
  pygame's own readback looked correct — 8 windows make it 8x as likely.
- **Stacking order:** the windows must stay above the xterm. Verify after a
  `tmux refresh-client`; add an explicit `xdotool windowraise` if not.
- **Startup race:** `startup.sh` launches pane processes before the xterm
  resize (`ac40102` split the build into phases for this). Make sure the
  tile path uses `_resolve_geometry`'s retry loop rather than one-shotting a
  1x0 rect.

### C7. Tests

- `tests/test_tile_avatar.py` (new): `tile_avatar_rect()` for the 4x2 grid
  at 1920x1080 **and** at a non-default capture resolution; per-slot params
  resolution for both `roster` shapes; the ASCII-fallback path when provider
  construction raises (assert a face is still drawn — the never-blank rule).
- Extend `tests/test_roundtable_layout.py`: every cast slot resolves a
  `character_params` present in `PRESETS`; uncast slots resolve `None`.
- Extend the tile-pane tests for the new row arithmetic:
  `TILE_FIXED_OVERHEAD` changes, so assert the dialogue-line count at 45
  rows and the floor (`MIN_DIALOGUE_LINES`) at a tiny pane. C1's fallback
  constants need their own assertions.
- Keep `tests/test_codec_avatar.py`'s realtime-floor style check but at tile
  size: assert the render clears the tile fps target at 200x200, so a
  regression fails the suite instead of only showing up as a choppy
  roundtable.

### C8. Deploy + verify

Same loop as A5 against `worker-gm` (`DISPLAY_NUM: 105`, so `-i :105.0`),
pulling a full-frame capture. Acceptance: eight tiles; heads visible and
visibly *different* from each other; dialogue text not shifted or wrapped;
`tuber_4` still grey/ASCII; every tile logging `rendering on GPU` (not a
silent llvmpipe fallback); and CPU under cap via `docker stats` **while a
show is actually airing**, not at idle.

---

## Docs to update (repo convention: docs/ per module)

- `docs/avatar_3d_design.md` — silhouettes done (A2), GM preset, tile avatars.
- `docs/gl_raster_benchmark.md` — A3's auto-detect finding; C3's measured
  tile-size GPU numbers on gx10 (the doc only has dev-box 560x700 today).
  The "Open question: worker container GPU" section is now closed for the GM
  too.
- `docs/composite_on_background.md` — the tile window's background.
- `docs/deployment.md` + `README.md` — the new `worker-roundtable` container,
  its stream key, `tuber_0`'s change of role, and
  the re-tuned CPU caps (B2).
- `docs/panels.md` + `config/panels/tile.yaml`'s header — the tile is no
  longer "placeholder-grade ASCII"; this plan closes the seam that comment
  points at.
- `.claude/prompts/roundtable_stream_design.md` §5.1 — supersede
  "deliberately small placeholder face"; fix the §343 grid math (C1).
- New `docs/tile_avatar.md` per the doc-per-module convention.

## Suggested order

1. **A1 + A4** (copy the geometry, add the drift test), deploy, A5 frame
   check. Fastest visible win: five workers stop rendering black slabs.
2. **A3** (root-cause the detection) while the deploy is warm.
3. **A2** silhouettes — pure local iteration, no deploy needed.
4. **C1** (pin 1080p/font 7, fix the stale 160x44 math). Small, and it makes
   C2's pixel math reviewable.
5. **B1-B6** — the `tuber_0` / roundtable split. `panel.py`'s
   `ROUNDTABLE_WORKER_ID` and the new container must land in the **same**
   deploy as the preset change (B6), or the roundtable silently stops airing.
6. **C2-C7**, with a GPU spike up front: one tile at 200x200 on gx10 logging
   `rendering on GPU` before building the other seven.
7. Docs.

## Risks worth restating

- **8 containers x 2.5 cores** on a host whose 7-worker cap was already
  sized with sshd headroom in mind (B2). Re-tune before deploying, not after
  load average 328.
- **Two directors, or zero.** `tuber_0` and the new roundtable container must
  not both satisfy `_resolve_local_tiles`' gates — and the cutover must be
  atomic, since a preset change without the new container leaves the show
  with no director and no error message (B4, B6).
- **gx10's GPU may not actually engage** (llvmpipe has been observed). The
  reservation is not proof; the log line is (C3).
