# Live table GUI: story progress and scene plan

Status: PLAN ONLY. Nothing implemented. Written 2026-10-11.

## Problem

The `/table` page (services/campaign-manager/templates/table.html) shows a
"Start from" dropdown of bare indexes and goal text. The operator cannot see:

- which scenes are queued, which have played, which were skipped
- where the run is in the story (segment / leaf position)
- why the list is 21 scenes when the generator produced 42 slots
- which scene is next and what its participants are

Today the page only works when worker-gm answers `table_status_request`. When
it does not, the page shows "The GM did not answer" and nothing else.

## What the code already gives us

- `table_gm.contracts_provider` (app/agent_handlers/table_gm.py:381) builds the
  ordered list and logs every drop (dedupe, collapse, skip_scenes, near-dup,
  active-player filter). The drops are logged, not returned.
- `status(scenes=True)` (table_gm.py:883) returns per scene only
  `index`, `scene_id`, `goal[:140]`. No segment, leaf, participants, or state.
- `status()` returns `index`, `next_scene_id`, `total`, `run` (elapsed,
  remaining, played), `scene_id`, `round`, `phase`. No per-scene played state.
- The position file (`/data/world-state/table_<pack>_position.json`) holds
  `index`, `scene_id`, `at`, and `story`. `story` has one entry per finished
  scene with a `skipped` flag, so played history already persists.
- `story_log.entry_from_state` records each finished scene.

## Proposed view (ideas, ranked by value to the operator)

1. Funnel panel: "42 slots -> 21 scenes", one row per filter with count and
   the scene ids it removed. This answers "why is my index 0 not 0?" directly.

2. Story timeline (lanes by segment): four horizontal lanes
   (off-hours-arc, first-standup-arc, build-arc, ship-arc). Each scene is a
   chip coloured by state: played, skipped, now, next, queued. Hovering shows
   the goal and participants.

3. Now / next card: current scene id, phase, round, elapsed vs duration bar,
   next scene goal. Refreshes every 2 s via htmx on a fragment.

4. Scene table (sortable, filterable): #, slot id, segment/leaf, participants
   (active seats vs silent seats marked), goal, state, dropped-by reason.

5. Arc tree view: segments -> branch nodes -> leaves, with kept and dropped
   leaves marked. Shows the planner structure behind the flat list.

6. Recap pane: last N finished scenes from the story log, with skipped marks.

7. Dropdown labels: "#3 · off-hours-arc · n0-n0-004 · With a sigh…" so the
   list matches what the operator sees in the timeline.

## Data changes (small, read-only)

- Extend the scene payload: `segment_id`, `leaf_id`, `slot_id`,
  `participants`, `full goal` (drop the 140-char cut for the table view).
- Return a structured funnel from contracts_provider: list of
  `{stage, removed: [scene_id...], kept_count}`. Keep the log lines.
- Add a `played` map to status: `{scene_id: played|skipped}` derived from
  the story log, plus `upcoming` = remaining ids in order.
- Add `table_scenes_request` as a new message type, or extend
  `table_status_request` with `{"scenes": true, "detail": true}`. Prefer
  extending, to keep the protocol in app/table/control.py unchanged.

## Offline fallback (important)

When the GM does not answer, the page should still render from the saved
position file and the fixture/config, marked "GM offline, showing saved
state". The campaign-manager has no write path to the GM; this stays
read-only.

Trust boundary: campaign-manager reads the position file through the GM
protocol only. Do not add a bind mount of /data/world-state into
campaign-manager (the user rejects rw/ro host mounts for convenience).

## Phases

P1. Funnel + scene table with detail fields. Backend: contracts_provider
    returns the funnel; status carries detail. Frontend: funnel panel and
    table. Tests: extend services/campaign-manager/tests/test_table_page.py
    and tests/table/test_table_gm_handler.py.

P2. Timeline lanes and now/next card, htmx polling. Tests: template render
    with fake status (no GM); state derivation unit tests.

P3. Offline fallback from the position file, plus recap pane.

P4. Arc tree view (optional, only if P1-P3 prove useful).

## Open decisions for the user

- Show dropped scenes in the timeline (greyed) or only in the funnel?
- Is the Start-from dropdown kept, or replaced by clicking a timeline chip?
- Should the offline view also show the planned order from the fixture when
  no position file exists yet?

## Verification plan

- pytest services/campaign-manager/tests and tests/table (both must pass).
- Render /table against a fake GM status (existing test_table_page pattern).
- Live check: curl /table and read the real output once worker-gm is up
  (it currently returns "GM did not answer"). Do not claim the GUI works
  from code alone.
