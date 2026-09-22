# Character generator (parametric 3D avatar heads)

Generates a worker's 3D avatar head from a flat set of 0..1 sliders, so a
character's look can be produced and iterated on by an AI agent instead of
being modeled by hand. This is **Backend A** of
[docs/avatar_3d_design.md](avatar_3d_design.md) — pure numpy, no Blender,
no new runtime dependency.

Chadwick (the `coder` worker, KODI-7) is the flagship character and the
only worker currently using it; every other worker is untouched.

## Quick start

Preview a character without deploying anything:

```bash
python3 app/character_preview.py --preset chadwick
```

Iterate on a slider:

```bash
python3 app/character_preview.py --preset chadwick --set jaw_width=0.9 --set ear_size=0.2
```

Show the full slider schema:

```bash
python3 app/character_preview.py --list-params
```

Locally this runs under the repo's `.venv` (numpy only). Inside a worker
container use `/opt/render3d/bin/python3`.

## The slider schema

Every slider is a float in `0..1`; out-of-range values are **clamped**, not
rejected. `accent_color` is an enum from termgl's named palette (`RED`,
`GREEN`, `YELLOW`, `BLUE`, `PURPLE`, `CYAN`, `WHITE`, `BLACK`).

| Parameter | Effect at 0.0 | Effect at 1.0 |
|---|---|---|
| `head_width` | narrow skull | wide skull |
| `head_taper` | blocky cranium | egg-shaped cranium |
| `eye_size` | small eyes | large eyes |
| `eye_spacing` | close-set | wide-set |
| `jaw_width` | narrow chin | heavy, flared jaw |
| `nose_length` | flat nose | long protruding nose |
| `ear_size` | small ears | large flared ears |
| `build` | thin neck | thick neck |
| `accent_color` | — | termgl color name |

Missing sliders take their defaults, so a partial dict is a valid
character: `{"eye_size": 0.9}` is a complete request.

## Configuring a worker

In a worker config's `avatar:` block:

```yaml
avatar:
  provider: termgl_avatar
  termgl_avatar:
    character_params: chadwick     # a preset name...
```

or inline the sliders:

```yaml
    character_params:
      preset: chadwick             # optional base to start from
      jaw_width: 0.9
      accent_color: CYAN
```

Optional knobs alongside it: `view_dist` (camera distance; lower = larger
head), `angle_speed` (rotation rate), `radius` (placeholder icosahedron
only).

**Omitting `character_params` keeps the old placeholder icosahedron.** That
is what every other worker does, and why this change is inert for them.

### Failure behaviour

The avatar pane must never crash a worker, so failures degrade instead:

- Unknown key, non-numeric value, or out-of-range slider → that slider
  falls back to its default (or is clamped) and a warning is logged; the
  head still renders.
- Unresolvable `character_params` (unknown preset name, wrong type) → the
  provider falls back to the placeholder icosahedron and prints why.

## Why the face reads as a face (and how to keep it that way)

The first version rendered as "a yellow ball with some density blocks."
Three things fixed it, and all three are load-bearing:

**1. Lighting.** `LitPixelShader`'s stock light points nearly down the view
axis, so the entire visible hemisphere saturated at full brightness — 262
of 388 on-screen characters were `@`. `head_mesh.FACE_LIGHT_DIRECTION`
replaces it with an **overhead, symmetric** light (`x=0`). A side/raking
light seems more sculptural but washes one cheek bright and the other
dark, and that left-right gradient competes with the features for the nine
ramp levels available — it measured noticeably noisier. The pane and the
preview both use this light, so they agree.

**2. Carve features IN, don't stick them ON.** A sphere with bumps attached
reads as a sphere with bumps. `_sculpt_face()` displaces the skull's own
vertices: recessed eye sockets, an overhanging brow, a flattened facial
plane, a forward muzzle mass, a mouth shadow. Under the overhead light the
brow lights up and the sockets fall dark, and *that contrast* is what a
viewer decodes as a face.

**3. Features must be few, wide and deep.** Flat shading gives every
triangle its own tone, so any displacement varying faster than the triangle
spacing becomes speckle rather than shape. Separate cheekbone and chin
bumps interfered and measurably raised the neighbour-character change rate;
merging them into one broad muzzle mass fixed it. Socket depth also had to
go far deeper than looked sensible on paper (0.30–0.40) — a shallow socket
just lands on the same ramp character as the cheek beside it.

Useful metrics when tuning, both computable from a rendered frame:

- **contrast** — mean brightness of the cheek band minus the eye band.
  Higher is better; the shipped face scores ~0.88.
- **speckle** — fraction of horizontally-adjacent character pairs that
  differ. Lower is better; a plain skull is ~0.168 and the shipped face is
  ~0.122, i.e. *smoother* than an unsculpted head despite having features.

If you add a feature and the face gets mushier, check speckle before
trusting your eyes on a single still.

### Attachments are welded, not placed

`_surface_z()` queries the sculpted skull's actual front surface, and the
nose and eyeballs anchor to it. Hardcoded Z offsets broke as soon as the
sculpt moved the facial plane back — the nose floated in front of the face
as a detached cone, which reads as a rendering glitch. Any new attached
feature should anchor the same way.

## The agent iteration loop

`app/character_preview.py` is the tool an agent drives
(avatar_3d_design.md §5 step 3):

1. Call it with a params dict.
2. Read the ASCII frames from stdout — a four-view turntable (front,
   three-quarter, profile, rear-quarter) by default.
3. Adjust sliders, call again.

`--json` emits `{params, frames}` for programmatic consumption. Since the
target is ASCII, the rendered frames go straight into an agent's context as
text — no image files and no vision model are involved.

### Why the preview doesn't use termgl

termgl's `TGL()` constructor requires a real terminal and raises `OSError`
under a pipe, a non-tty, or a Windows console — so it cannot render a frame
into a string. `app/ascii_raster.py` is a small pure-numpy rasterizer that
mirrors `render3d_common.py`'s pipeline (same camera/view matrices, same
flat-shaded `max(0.15, dp*0.5+0.5)` intensity, same gradient ramp, same
backface culling and Z-buffer) to produce the same picture as text.

It is a faithful **preview of silhouette and shading**, not a pixel-exact
emulator. The real pane in the container is ground truth.

## Resolution

The avatar pane renders at 55x24 termgl pixels, doubled horizontally to
110x24 terminal cells by termgl's `DOUBLE_CHARS` (which makes pixels square,
since a character cell is about twice as tall as wide).

In the `tuber_base` layout the avatar pane is about **144x26 cells** at the
default 240x67 tmux grid, so the current render leaves headroom. Raising
`WIDTH`/`HEIGHT` in `avatar_providers/termgl_avatar.py` increases detail,
bounded by:

- the pane's cell size (~144x26 today) — `WIDTH*2` must fit the pane width;
- `FONT_SIZE` / `CAPTURE_RESOLUTION` in `startup.sh`, which set how many
  cells exist on the virtual display in the first place;
- legibility after ffmpeg scales the 4K capture down to the 1080p stream.

Per-pixel density is fixed by the font — the only way to get more detail in
the same screen area is more cells (smaller font / larger capture), not a
denser character.

## Morph targets (future work)

Expressions are currently rotation-speed cues only. The generator is
already built for morph targets (avatar_3d_design.md §4):

- `build_head_params_mesh(params)` returns indexed `(verts, faces)`.
- Topology is **fixed and parameter-independent** — vertex count and face
  table are identical at every slider setting, enforced by
  `tests/test_head_mesh.py` across the parameter space.
- `head_mesh.lerp_verts(a, b, t)` blends two poses of that shared topology.
- `mesh3d.trigs_from_indexed(verts, faces)` converts the blended result to
  the `Trig3D` array termgl draws.

Adding a feature to the generator means adding it **unconditionally** and
scaling it to zero when absent — never branching geometry on a slider
value, which would silently break morphing.

## Files

| File | Role |
|---|---|
| `app/character_schema.py` | slider schema, validation/clamping, presets |
| `app/head_mesh.py` | Backend A geometry (skull, eyes, nose, ears, neck) |
| `app/ascii_raster.py` | pure-numpy preview rasterizer |
| `app/character_preview.py` | agent-facing CLI |
| `app/mesh3d.py` | `trigs_from_indexed()` converter |
| `app/avatar_providers/termgl_avatar.py` | `character_params` wiring |
| `config/workers/coder.yaml` | Chadwick, live |

## Tests

```bash
.venv/Scripts/python.exe -m pytest tests/test_character_schema.py \
  tests/test_head_mesh.py tests/test_ascii_raster.py \
  tests/test_termgl_avatar_character.py -q
```
