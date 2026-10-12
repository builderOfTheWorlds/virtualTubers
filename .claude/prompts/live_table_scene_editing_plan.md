# Live table: manual scene edits, reordering, version history

Status: PLAN ONLY. Extends live_table_gui_plan.md. Written 2026-10-11.

## Requirements (from user)

- Edit a scene manually (goal text, participants, lore, and whether it plays).
- Reorder scenes.
- Every save is versioned; the operator can view history and roll back.

## Constraints that shape the design

1. The GM builds its list from generator artifacts (generation_artifacts
   tree/brief/arc_plan rows, plus the fixture in
   tests/table/fixtures/...). Generator output must not be overwritten by
   operator edits. Edits go in an overlay.

2. Trust boundary. The user rejects host or container rw bind mounts for
   convenience. Edits go through generator-api to the generator Postgres
   (127.0.0.1:5455, which holds pack_scenes and pack_cast). campaign-manager
   gets no write path to /data/world-state. The GM reads the overlay from the
   same DB at load and at each scene boundary.

3. Two Postgres instances exist (see memory). Overlay tables go in the
   GENERATOR db, next to pack_*, not the app DB at 192.168.1.120.

4. Continuity. Scenes form a chain: each leaf's continuity_out feeds the next
   leaf's continuity_in, and must_resolve items are carried forward. A
   reorder or an edit can break that chain. The planner's chain was written
   sequentially with neighbour output in context. Reordering is not a free
   operation. Invariants must be enforced in code, not left to the operator.

5. Config overlap. config/table/ashiorid_office.yaml has a file-based
   skip_scenes list and filter flags. The overlay must have a defined
   precedence over them, or the two will disagree silently.

6. Mid-run. The GM is already playing scenes. Edits take effect only at the
   next scene boundary. An edit must never change the scene currently in
   progress.

## Proposed data model (append-only, in generator DB)

table_overlay_version
  id bigserial pk
  run_id text not null
  version int not null          -- 1, 2, 3 ... per run_id, unique(run_id, version)
  parent_version int null       -- rollback creates a new version pointing at its source
  author text not null          -- operator id from control panel
  note text                     -- operator's reason for the change
  created_at timestamptz default now()
  state jsonb not null          -- full snapshot: order[], per-scene overrides, enabled flags

Full snapshot per version (not a delta). The scene count is about 40, so the
size is small, and each version is self-contained for rollback and diff.

Current version = max(version) for the run. No UPDATE, no DELETE.

Scene keys stay the existing scene_id (segment.leaf.slot). Overrides:
  goal (text override), participants (list), lore (list), enabled (bool),
  position (int, derived from order[]).

## Reorder rules (enforced in code, validated on save)

- Save is rejected (HTTP 422, with the reason) if the new order breaks a
  continuity link: scene N's continuity_out must match scene N+1's
  continuity_in, or the operator must confirm the break explicitly with a
  note, which is stored in the version record.
- A scene's segment may not move before a segment with a lower order unless
  the operator sets an explicit cross-segment flag.
- A disabled scene is removed from the play list but keeps its id, so a later
  enable restores its original neighbours.

Open question: how strict should the continuity check be? Options:
  a) hard reject (safest, may block legitimate reorders)
  b) reject unless a confirm note is given (recommended)
  c) warn only

## Flow

1. Operator opens a scene in the timeline or table, edits fields, drags to
   reorder, or toggles enabled.
2. The control panel posts a draft to campaign-manager. campaign-manager
   forwards it to generator-api.
3. generator-api validates (continuity, segment rules, unknown scene ids),
   writes a new table_overlay_version row, returns version number and diff.
4. The GM reloads the overlay at the next scene boundary. It logs the version
   it applied (INFO, structured, with run_id and version).
5. The page shows "Editing draft (based on v7). Saved as v8." and a history
   list. Rollback = choose a version, which writes a new version with
   parent_version set to it. History is never rewritten.

## Precedence

overlay (if any version exists for the run)  >  config skip_scenes and filter
flags  >  generator artifacts.

The config filters still run first to produce the default list, so the
overlay only changes what the operator changed. A scene the overlay does not
mention keeps its default state. The funnel panel from the GUI plan should
show overlay drops as their own stage.

## History view

- Version list: version, time, author, note, count of changes, parent.
- Diff between two versions: moved scenes (from -> to), goal edits (before /
  after text), enabled toggles, participant changes.
- Rollback button per version.

## Phases

E1. Read-only history: table + GET endpoint returning versions. No editing.
E2. Enable/disable and goal/participant edits, saved as versions. GM applies
    overlay at scene boundaries. Tests: version numbering, rollback creates new
    version, overlay precedence over skip_scenes.
E3. Reorder with continuity validation and the confirm-note path. Tests:
    rejected reorder returns 422 with reason; confirmed reorder stores note.
E4. Diff view and rollback UI.

## Tests (required before claiming done)

- Unit: overlay merge with fixture; precedence; disabled scene keeps id.
- Unit: continuity validator on fixture order (original order must pass).
- Integration: save -> GM reload at boundary -> status shows new order.
- Live: curl the endpoints and read real output; the GM currently does not
  answer, so live verification is blocked until worker-gm is up.

## Decisions (answered 2026-10-11)

1. Continuity: reject a reorder that breaks a link unless the operator
   confirms with a note (option b). The note is stored with the version.
2. Goal edits change the text the GM plays from (canon_goal in the GM prompt),
   not only the operator label.
3. Retention: keep all versions. No cap.
4. Editors: any control-panel user.

## Open questions (not yet answered)

- Auth: is the control panel behind any login? Edits can change a scene that
  airs on six Twitch channels. "Any user" needs a defined meaning if the panel
  is reachable from outside the LAN.
- Concurrency: two users editing at once. Proposed: each draft records the
  base version; a save on a stale base returns 409 and shows the newer version.
- GM offline: edits save as a version but apply only when the GM is up. Show
  "saved, not yet applied" in the page, or block edits while the GM is down?
- Disabling a scene: does removing a scene count as a continuity break, and
  does it need the same confirm note?
- Config skip_scenes (config/table/ashiorid_office.yaml): keep it as the
  base layer, or migrate its entries into the first overlay version?
- Mid-run reorder: may the operator move only scenes after the current index?
- Audit: should each played scene record the overlay version it played under?
