# Scene Continuity IDs and an Enforced Chain

Status: PLAN ONLY. No code changed yet. Written 2026-10-11.
Diagram: .claude/prompts/scene_continuity_ids_diagram.svg (open in a browser)

## 1. Problem

Spoken text on the stream jumps between scenes. Cause: continuity_in and
continuity_out are written independently by the LLM for each node. Nothing
links a node's continuity_out to its neighbour's continuity_in, and nothing
in code checks that they agree.

Evidence, artifact 668 (run ashiorid_office_20260929_135927_9c1b,
job job_20260930T002503_dd6fd2), subtree build-arc:

    build-arc-n0-n0  out: "The Marketing team begins working on their pitch for the product."
    build-arc-n0-n1  in:  "The Engineer and Tester pass their latest code for review and feedback."

These are adjacent leaves. The first one ends with Marketing starting a pitch;
the next one opens with Engineer and Tester passing code. The GM opens the
second scene in a state the first scene never reached.

The boundary n0-n1 -> n1 happens to match ("takes a break for lunch"), so the
break is not uniform. That is why it looks random on stream.

Node IDs already exist (node_id, parent_id, order, children). The missing piece
is links from each boundary to the node that owns the matching text.

## 2. Decisions

- Child IDs: the `children` key already holds the ordered child IDs on every
  branch. No new key. Keep it. It is the default path for branching.
- New keys on every node: continuity_in_nodes and continuity_out_nodes.
  Lists, with one element today. See Open Question 1.
- Node text (continuity_in / continuity_out) stays. The GM still reads it.
  The IDs are the join key for the visual layer.
- Enforcement is in code. The prompt may still ask for continuity text, but
  code overwrites it after the LLM returns.

## 3. Semantics of the link keys

Each link points at the node that owns the matching boundary, i.e. the node
that produced the state this node starts from (in) or the node that consumes
the state this node leaves behind (out).

Leaf:
    continuity_in_nodes  = [previous leaf in DFS order]   or []  if first leaf of segment
    continuity_out_nodes = [next leaf in DFS order]       or []  if last leaf of segment

Branch (per the request: first/last leaf of its subtree):
    continuity_in_nodes  = continuity_in_nodes of its first leaf
    continuity_out_nodes = continuity_out_nodes of its last leaf

Join rule the visual layer can rely on:
    for every leaf A with out_nodes containing B,
        B.continuity_in_nodes contains A
        and A.continuity_out == B.continuity_in  (exact string, by construction)

Segment edges: the first leaf's in_nodes and the last leaf's out_nodes are
empty. Cross-segment linking is out of scope (Open Question 3).

## 4. Enforcement in code (not the prompt)

File: utilities/3LayersWeeklyGeneration/src/plan_segment.py, _handle_branch.

After parse_children() returns and BEFORE validate_children() and
distribute_words():

    children[0].continuity_in   = node.continuity_in
    children[-1].continuity_out = node.continuity_out
    for i in range(len(children) - 1):
        children[i + 1].continuity_in = children[i].continuity_out

Rules:
- Runs at every expansion depth. A child that is expanded later gets the same
  treatment for its own children, so the invariant holds at every level.
- Leaves never receive LLM continuity. Their values are set by the parent.
- Overwrites are logged at DEBUG with key-value pairs (node_id, field,
  llm_value, enforced_value) so the drift stays visible.
- Overwriting happens before validation, so validate_children does not reject
  a value that code is about to fix.

## 5. Link computation

New file: utilities/3LayersWeeklyGeneration/src/continuity_links.py
(one concern per file, per CLAUDE.md).

- ordered_leaves(tree) -> list[str]: DFS from the root, children sorted by
  `order`. This is the same traversal merge_brief uses. Move the traversal here
  and have merge_brief call it, so both always agree.
- compute_continuity_links(tree) -> None: sets continuity_in_nodes and
  continuity_out_nodes on every node per section 3. Idempotent.

Called in plan_segment.py after the tree is fully resolved and before
_write_checkpoint / merge_brief.

## 6. Carry-through

utilities/3LayersWeeklyGeneration/src/segment_schema.py, merge_brief:
- nodes_out entries gain continuity_in_nodes and continuity_out_nodes.
- Slots already carry node_id (the leaf). The join is slot -> leaf -> links.

app/table/contract.py:
- SceneContract gains continuity_in_nodes and continuity_out_nodes as tuples
  with default (). Adding a defaulted field keeps existing callers working.
- Populated from the leaf node in the tree walk (around lines 204-250).
- The text fallback (node, then segment) stays for old artifacts.
- Before editing: grep every SceneContract consumer and any JSON serialiser
  (app/table/protocol.py, story_log.py) so the new fields are not dropped or
  break a schema.

Not in this plan: app/campaign/pack.py Scene (a separate, hand-authored pack
model with only text continuity). Flag if the visual layer needs it.

## 7. Regeneration (no backfill)

- Regenerate the segment whose tree root is build-arc (artifact 668). The
  segment id is confirmed during implementation.
- Delete that segment's tree checkpoint, then re-run planning for that segment
  only. This is a new run; keep the old artifact for before/after comparison.
- Runs on the local Ollama generator (hermes3:70b), sequentially. Do not run
  alongside a vLLM job on the GB10 (memory note: OOM risk).

## 8. Tests

utilities/3LayersWeeklyGeneration/tests/test_plan_segment.py
- test_branch_overwrites_llm_boundary_text_with_previous_out (parametrized
  over mismatch / empty / identical LLM text)
- test_first_child_in_equals_parent_in
- test_last_child_out_equals_parent_out
- test_nested_expansion_keeps_chain_invariant (two levels deep)
- test_overwrite_is_logged_at_debug

utilities/3LayersWeeklyGeneration/tests/test_segment_schema.py
- test_leaf_links_follow_dfs_order (linear tree)
- test_branch_links_are_first_in_and_last_out_leaf (see section 3)
- test_segment_edges_are_empty_lists
- test_links_are_idempotent_on_rerun
- test_merge_brief_carries_node_links
- test_build_arc_668_boundary_is_consistent (fixture = snapshot of artifact
  668 tree, saved under tests/fixtures/; asserts the n0-n0 / n0-n1 boundary
  now matches)

tests/table/test_table_contract.py
- test_contract_carries_leaf_continuity_node_ids
- test_contract_falls_back_to_text_when_node_links_absent (old artifacts)

All LLM calls use the existing fakes (segment_helpers). No real services.

Pass criteria: pytest green in both suites; new modules >= 80% coverage.

Post-regeneration check (real artifact, not a fixture): for every adjacent
leaf pair in the regenerated segment, A.out == B.in and the pointers agree.
Report the count of pairs checked.

## 9. Open questions

1. Lists or scalars? The plan uses lists (continuity_in_nodes /
   continuity_out_nodes). Lists are needed once choice-branching exists, since
   one leaf may lead to several alternative successors. Say if you want
   scalars instead (continuity_in_node / continuity_out_node).
2. Choice branching: when a character choice splits the path, out_nodes will
   hold alternatives, not a sequence. That needs an edge label (for example a
   choice id). Not in this plan; the field shape reserves room for it.
3. Cross-segment boundary: segment N's last out must equal segment N+1's first
   in. The same bug can exist here and is not checked. Propose a follow-up.
4. Confirm the "first/last leaf" wording for branches means the boundary
   owners described in section 3, not the entry/exit leaves themselves.

## 10. Out of scope

- Campaign pack Scene model (app/campaign/pack.py).
- Data Viewer UI.
- Runtime choice-branching logic.
- Backfilling existing artifacts (regenerate instead).

## Appendix: current vs planned (artifact 668, build-arc)

Current (broken):

    build-arc                   [branch]  in: "team disperses..."     out: "team gathers for final test pass..."
      build-arc-n0              [branch]  in: "team disperses..."     out: "lunch break"
        build-arc-n0-n0         [leaf]    in: "team disperses..."     out: "Marketing begins pitch"
        build-arc-n0-n1         [leaf]    in: "Eng+Tester pass code"  out: "lunch break"   <- in != prev out
      build-arc-n1              [leaf]    in: "lunch break"           out: "final test pass"

Planned (same text, links added, mismatch fixed in code):

    build-arc-n0-n0  in_nodes=[]              out_nodes=[build-arc-n0-n1]
    build-arc-n0-n1  in_nodes=[build-arc-n0-n0]  out_nodes=[build-arc-n1]   in = n0-n0.out
    build-arc-n1     in_nodes=[build-arc-n0-n1]  out_nodes=[]              in = n0-n1.out
    build-arc-n0     in_nodes=[]  out_nodes=[build-arc-n1]
    build-arc        in_nodes=[]  out_nodes=[]
