# 3D avatar design: parametric character generator

Status: Backend A IMPLEMENTED (2026-09-22) — see
**[character_generator.md](character_generator.md)** for usage. Steps 1-4
of §5 below are done: `app/head_mesh.py` builds the mesh,
`app/character_preview.py` is the agent iteration loop, and Chadwick is
live on the `coder` worker. Backend B (Blender) remains a documented
future option, unbuilt. This document is kept as the DESIGN RATIONALE;
the implementation doc is the reference for how to use what was built.

Originally written 2026-09-21 alongside the termgl 3D
rendering work (`8edddda`, see `docs/panels.md`'s "3D rendering (termgl)"
section and `app/avatar_providers/termgl_avatar.py`).

## 1. Goal

Replace `termgl_avatar.py`'s placeholder icosahedron with real per-character
faces/heads — but not hand-authored ones. The ask: a **characterGenerator**
utility that an AI agent can drive itself, adjusting a character's
appearance the way a game character-creator screen works (sliders: eye
size, jaw width, etc.), so a persona's look can be generated/iterated on
by the agent at creation time rather than modeled by a person up front.

## 2. Hard constraint: what termgl can actually display

Before designing sliders, the render target matters — it caps how much
detail is worth generating:

- Panes are **55x24 characters** (`WIDTH`/`HEIGHT` in `termgl_avatar.py`).
- Shading is **flat, single accent color per mesh** + a brightness->char
  gradient ramp (`LitPixelShader` in `app/render3d_common.py`) — no
  textures, no per-vertex color, no RGB.
- No skeleton/skinning. Every frame rebuilds a plain `Trig3D` vertex array
  from scratch (see `mesh3d.py`) — "animation" today is rotation speed +
  color swap only (`EXPRESSION_STYLE` in `termgl_avatar.py`).

Conclusion: design for **silhouette + proportion + one accent color**, not
surface detail. A generator producing photoreal geometry is solving a
problem this renderer can't show. "Expressions" should be implemented as
a handful of named **vertex-position variants of the same topology**
(morph targets), numpy-lerped per frame in `render_tick()` — this fits the
existing per-tick rebuild loop with no skeleton system needed.

## 3. Architecture: decouple the slider schema from the mesh backend

The interface an agent edits is a **flat parameter schema** (JSON), not
raw geometry:

```json
{
  "head_width": 0.5,
  "head_taper": 0.3,
  "eye_size": 0.6,
  "eye_spacing": 0.5,
  "jaw_width": 0.4,
  "nose_length": 0.4,
  "ear_size": 0.5,
  "build": 0.5,
  "accent_color": "CYAN"
}
```

Every value 0..1 (or an enum for things like `accent_color`, matching
termgl's named-color palette — see `_ANSI_COLOR` in
`render3d_common.py`). This schema is the ONLY thing an agent touches.
Two backends can turn it into a mesh; the schema should stay generic
enough that either works (avoid parameters that only make sense as one
backend's internal transform).

### Backend A — procedural (numpy), build this first

A pure function, alongside the existing primitive-composition builders in
`mesh3d.py` (`build_radar_mesh`, `build_octahedron`, etc.):

```python
def build_head_mesh(params: dict) -> np.ndarray:  # TRIG3D_DTYPE array
    ...
```

Skull = scaled/tapered sphere, ears = small cones, jaw = a lofted ring —
each slider is a scale/offset/lerp on a base primitive skeleton, same
technique the radar spike mesh already uses. No external process, no new
dependency, sub-millisecond, unit-testable without termgl (matches
`mesh3d.py`'s existing "no termgl import" design rule). An agent "plays
with sliders" by calling this repeatedly and comparing rendered frames —
tight, fast, fully local loop.

This is the right fidelity for what termgl displays today and should be
the v1 backend for the flagship character.

### Backend B — Blender via blender-mcp, later / optional

[`ahujasid/blender-mcp`](https://github.com/ahujasid/blender-mcp) (also
blendermcp.org) is a real, actively-used community MCP server: a Blender
addon starts a local socket server, an MCP client executes `bpy` scripts
against the running Blender scene, inspects it, and pulls viewport
screenshots for a vision feedback loop. Not installed in this project —
would need Blender + the addon running, and registering the server in
Hermes's own MCP config (separate from this repo).

Mapping: the same param schema drives Blender **shape keys** on a base
head mesh — shape keys are literally the slider/morph-target primitive
Blender was built for, so this is a natural fit conceptually. Export to
OBJ/glTF, load into termgl via a new `mesh3d.load_obj()` (doesn't exist
yet).

Tradeoffs vs. Backend A:
- Heavier: a running Blender process, bpy scripting round-trips, a new
  OBJ/glTF loader and import step.
- Its extra geometric fidelity is currently wasted on termgl's 55x24
  flat-shaded output.
- Payoff: higher fidelity for future consumers beyond the terminal pane
  (stream overlay, VRM/Live2D-style export), and a human can sanity-check
  or hand-tweak a generated face in the Blender GUI.

**Decision:** treat Backend B as a documented future option behind the
same schema, not a v1 dependency. Build Backend A first to prove the
schema and the agent-driven iteration loop end-to-end cheaply.

## 4. Expression handling (once a base mesh exists)

Define 2-3 named vertex-position variants of the generated topology
(e.g. `idle`, `speaking`, `thinking`) as morph targets, and lerp between
them by expression in `termgl_avatar.py`'s `render_tick()` — replacing
today's rotation-speed/color-only `EXPRESSION_STYLE` cue. Requires the
generator to guarantee stable vertex ordering/topology across all slider
values (so morph targets stay meaningful) — worth stress-testing early
since procedural builders that branch geometry by parameter value
(e.g. adding verts only above some threshold) would break this.

## 5. Plan (flagship character first — OPEN-4/roster names are candidates)

1. Build `build_head_mesh(params)` in `mesh3d.py` (Backend A), covering a
   first slider set (head_width, head_taper, eye_size, eye_spacing,
   jaw_width, nose_length, ear_size, build, accent_color).
2. Wire it into `termgl_avatar.py` behind a per-worker config block
   (`avatar.termgl_avatar.character_params`, alongside the existing
   `angle_speed`/`radius` knobs) so one character can go live without
   touching the other 7 (still placeholder icosahedron) or the
   `builtin`/`ascii_avatar` fallback path.
3. Prove the "agent adjusts sliders" loop: a small script/tool an agent
   calls with a params dict, that renders and saves a frame (or a short
   sequence) for the agent to evaluate and iterate on.
4. Once the loop works for one character, scale the schema out to the
   rest of the roster (`config/workers/tuber_0.yaml`'s `roster:` names).
5. Revisit Backend B (Blender) only if Backend A's silhouette-level
   fidelity proves insufficient, or a non-terminal render target is
   added later.
