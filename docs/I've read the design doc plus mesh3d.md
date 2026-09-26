I've read the design doc plus mesh3d.py, termgl_avatar.py, render3d_common.py, and tuber_0.yaml. The plan is coherent and Backend A is genuinely small — the real unknowns are about the iteration loop and where it runs, not the geometry.

What I found that shapes the questions:
- No .venv at the repo root; system Python is 3.11.16 with no numpy and no termgl. termgl compiles C (the Dockerfile installs build-essential for it), so plan step 3's "render a frame and look at it" may not work on this Windows box at all — only inside vtube-worker:latest.
Setup a .venv, we shgould have one

- mesh3d.py's house rule is "no termgl import", so build_head_mesh itself is trivially testable locally with just numpy. It's only the render half of the loop that needs termgl.
ok lets test locally

- termgl_avatar.py reads avatar.termgl_avatar.{angle_speed,radius}, so character_params slots in cleanly. But every current worker config (including tuber_0.yaml) sets provider: builtin — no worker is actually using the termgl provider today.
We will update theis 

Questions:

1. Where does the agent's render loop run? Options as I see them: (a) build/run inside the vtube-worker image via docker run, which is real but slow per iteration; (b) try to get termgl installed on this Windows host in a .venv (may not compile); (c) write a pure-numpy software rasterizer in the harness that reproduces termgl's flat-shade + gradient-ramp output at 55x24 — no termgl dependency, instant, and honestly close enough for silhouette judgment. I'd lean (c) for the iteration tool with (a) as the ground-truth check. Your call?
It needs to run on the same portiner stack as the rest of the tuber infrastructure



2. How does the agent "evaluate" a rendered frame? The panes are ASCII, so the harness can dump the 55x24 char grid as plain text straight into an agent's context — no image, no vision model. Is text-grid feedback what you want, or do you want PNG renders too?
Lets do it in ascii, can we scale up that 55x24 grid? Can we increase the resolution or pixel / character density inside of a single tmux pane?



3. Which character goes first? Plan says "flagship, OPEN-4/roster names are candidates" — roster is Chadwick, Vigil, Sodacan Bob, Leena, MAX-1, Iris. Pick one, or should I just build it against a generic test params set and not wire a real worker yet?
Start with Chadwick


4. Scope for this pass — all four of steps 1–3 (mesh builder + config wiring + agent iteration tool), or just step 1 (build_head_mesh + tests) so you can eyeball the geometry before I touch any worker config?
build it all


5. Topology stability (doc §4): should I enforce it now — fixed vertex count across all slider values, asserted in tests — even though morph targets aren't built until later? It's cheap now and expensive to retrofit, so I'd say yes unless you'd rather keep v1 minimal.
Yeah build it


6. One thing the doc doesn't settle: build_head_mesh returns a bare TRIG3D_DTYPE array, but morph targets need a shared vertex array with a face-index table, not independent triangles. Want me to add a (verts, faces) -> trigs split inside mesh3d.py now so lerping is possible later, or keep the flat-triangle return shape and deal with it in phase 2?
yeah add that converter
