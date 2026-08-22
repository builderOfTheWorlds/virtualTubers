"""
Layer 2 orchestrator for the 3-layer offline content generator.

This module owns the startup interlock, resume, the concurrent recursive
drain, retry/degrade, the checkpoints, and writing `brief.yaml`. Everything
pure — parsing, validation, prompt text, the brief merge — is imported from
`segment_schema` and NOTHING pure is reimplemented here.

The tree is as deep as the content needs and no deeper. A node is a leaf when
it is small enough to plan directly, and a branch when it is not. The drain is
continuous: a branch task discovers new children while other tasks are still
running, so the executor must accept new work after the first wave.
"""
import concurrent.futures
import logging
import threading
from pathlib import Path

import yaml

from segment_schema import (
    SYSTEM_PROMPT_EXPAND,
    SYSTEM_PROMPT_LEAF,
    SegmentPlanError,
    validate_tree_config,
    derive_target_slots,
    leaf_eligible,
    distribute_words,
    parse_children,
    parse_slots,
    validate_children,
    validate_slots,
    check_sensitivity_budget,
    build_expand_prompt,
    build_leaf_prompt,
    merge_brief,
)

log = logging.getLogger(__name__)


def _ancestors_of(node_id, tree):
    """Return the chain from the root to this node's parent, ROOT-EXCLUSIVE,
    ordered shallow -> deep. Empty for a node at depth 1."""
    chain = []
    current = tree[node_id]
    while current.get("parent_id") is not None:
        parent_id = current["parent_id"]
        chain.append(tree[parent_id])
        current = tree[parent_id]
    chain.reverse()
    return chain


def _handle_branch(node_id, tree, pack, arc_segment, config, llm, vocab, lock):
    """Attempt to expand a node into children. Returns (success, new_ids, node)."""
    node = tree[node_id]
    max_attempts = config["segment"]["max_attempts"]
    problems = []
    ancestors = _ancestors_of(node_id, tree)

    for attempt in range(1, max_attempts + 1):
        try:
            prompt = build_expand_prompt(pack, arc_segment, ancestors, node, config, problems)
            reply = llm.complete(SYSTEM_PROMPT_EXPAND, [{"role": "user", "content": prompt}])
            children = parse_children(reply)
            problems = validate_children(children, node, config)
            if not problems:
                children = distribute_words(children, node, config)
                break
        except Exception as exc:
            problems = [str(exc)]
            log.debug("expand attempt %d/%d failed for %s: %s", attempt, max_attempts, node_id, exc)

    if problems:
        # Exhausted: degrade to a forced leaf
        log.warning("expand exhausted for %s after %d attempts; degrading to forced leaf", node_id, max_attempts)
        return False, [], node

    # Success: create child nodes
    new_ids = []
    with lock:
        for order, child in enumerate(children):
            child_id = f"{node_id}-n{order}"
            child_node = {
                "node_id": child_id,
                "parent_id": node_id,
                "order": order,
                "depth": node["depth"] + 1,
                "title": child.get("title", ""),
                "summary": child.get("summary", ""),
                "continuity_in": child.get("continuity_in", ""),
                "continuity_out": child.get("continuity_out", ""),
                "target_words": child["target_words"],
                "target_slots": child["target_slots"],
                "kind": None,
                "forced": False,
                "children": [],
                "slots": None,
            }
            tree[child_id] = child_node
            new_ids.append(child_id)
        node["kind"] = "branch"
        node["children"] = new_ids

    return True, new_ids, node


def _handle_leaf(node_id, tree, pack, arc_segment, config, llm, vocab, lock):
    """Attempt to plan a leaf node into slots. Returns (success, node)."""
    node = tree[node_id]
    max_attempts = config["segment"]["max_attempts"]
    problems = []
    ancestors = _ancestors_of(node_id, tree)

    for attempt in range(1, max_attempts + 1):
        try:
            prompt = build_leaf_prompt(pack, arc_segment, ancestors, node, config, problems)
            reply = llm.complete(SYSTEM_PROMPT_LEAF, [{"role": "user", "content": prompt}])
            slots = parse_slots(reply)
            problems = validate_slots(slots, pack, vocab, config, node=node)
            if not problems:
                # Prefix slot ids with node id
                for slot in slots:
                    slot["slot_id"] = f"{node_id}-{slot['slot_id']}"
                break
        except Exception as exc:
            problems = [str(exc)]
            log.debug("leaf attempt %d/%d failed for %s: %s", attempt, max_attempts, node_id, exc)

    if problems:
        # Exhausted: skip this leaf
        log.error("leaf exhausted for %s after %d attempts; skipping", node_id, max_attempts)
        with lock:
            node["slots"] = []
            node["kind"] = "leaf"
        return False, node

    # Success
    with lock:
        node["slots"] = slots
        node["kind"] = "leaf"

    return True, node


def _write_checkpoint(tree, out_path, arc_segment, config, lock):
    """Write the tree checkpoint and the brief to disk."""
    checkpoint = out_path.with_name("tree.yaml")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with lock:
        with open(checkpoint, "w", encoding="utf-8") as f:
            yaml.safe_dump(tree, f, sort_keys=False, allow_unicode=True)

        brief = merge_brief(arc_segment, tree, config)
        with open(out_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(brief, f, sort_keys=False, allow_unicode=True)


def plan_segment(pack, arc_segment, config, llm, vocab, out_path,
                 *, progress=None, cancel_check=None) -> dict | None:
    """
    Plan one arc segment into a brief by driving a recursive tree of expand/leaf
    calls.

    Args:
        pack: The campaign pack
        arc_segment: The arc segment dict
        config: The configuration dictionary
        llm: Object with complete(system_prompt, messages) method
        vocab: Vocabulary object for validation
        out_path: pathlib.Path to write the brief YAML
        progress: Optional callback progress(done, total, node)
        cancel_check: Optional callback cancel_check() -> bool

    Returns:
        The brief dict, or None if no slots were produced.
    """
    # 1. STARTUP — the knob interlock
    problems = validate_tree_config(config)
    if problems:
        raise SegmentPlanError("; ".join(problems))

    # 2. RESUME — the tree checkpoint
    checkpoint = out_path.with_name("tree.yaml")
    tree = {}

    if checkpoint.exists():
        try:
            with open(checkpoint, "r", encoding="utf-8") as f:
                tree = yaml.safe_load(f)
            if not isinstance(tree, dict):
                raise SegmentPlanError("checkpoint is not a mapping")
        except yaml.YAMLError as exc:
            raise SegmentPlanError(f"failed to parse tree checkpoint: {exc}") from exc
    else:
        # Seed the tree with the ROOT node
        node_id = arc_segment["id"]
        target_words = config["segment"]["target_words"]
        target_slots = derive_target_slots(target_words, config)
        tree[node_id] = {
            "node_id": node_id,
            "parent_id": None,
            "order": 0,
            "depth": 0,
            "title": arc_segment.get("synopsis", "")[:80] or node_id,
            "summary": arc_segment["synopsis"],
            "continuity_in": arc_segment.get("continuity_in", ""),
            "continuity_out": arc_segment.get("continuity_out", ""),
            "target_words": target_words,
            "target_slots": target_slots,
            "kind": None,
            "forced": False,
            "children": [],
            "slots": None,
        }

    # Identify unresolved nodes
    unresolved = [nid for nid, node in tree.items() if node.get("kind") is None]

    if not unresolved:
        # All resolved: write final brief and return
        _write_checkpoint(tree, out_path, arc_segment, config, threading.Lock())
        brief = merge_brief(arc_segment, tree, config)
        if not brief["slots"]:
            return None
        return brief

    # 3. THE DRAIN — one executor, continuous submission
    lock = threading.Lock()
    max_depth = config["segment"]["tree"]["max_depth"]
    concurrency = config["segment"]["concurrency"]

    def handle_node(nid):
        node = tree[nid]
        # Classify
        if node["depth"] >= max_depth:
            log.warning("node %s at max_depth %d; forcing leaf", nid, max_depth)
            node["forced"] = True
            return _handle_leaf(nid, tree, pack, arc_segment, config, llm, vocab, lock)
        elif leaf_eligible(node["target_slots"], config):
            return _handle_leaf(nid, tree, pack, arc_segment, config, llm, vocab, lock)
        else:
            success, new_ids, node = _handle_branch(nid, tree, pack, arc_segment, config, llm, vocab, lock)
            if not success:
                # Degrade to forced leaf
                node["forced"] = True
                return _handle_leaf(nid, tree, pack, arc_segment, config, llm, vocab, lock)
            return new_ids

    with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as executor:
        pending = {executor.submit(handle_node, nid): nid for nid in unresolved}
        cancelled = False

        while pending:
            # Check cancellation between submissions
            if cancel_check is not None and cancel_check():
                log.info("cancellation requested; stopping drain")
                cancelled = True
                break

            done, _ = concurrent.futures.wait(pending, return_when=concurrent.futures.FIRST_COMPLETED)
            for future in done:
                nid = pending.pop(future)
                try:
                    result = future.result()
                except Exception as exc:
                    log.error("node %s raised: %s", nid, exc)
                    continue

                # Apply result
                if isinstance(result, list):
                    # Branch success: submit new children
                    for new_nid in result:
                        if cancel_check is not None and cancel_check():
                            cancelled = True
                            break
                        pending[executor.submit(handle_node, new_nid)] = new_nid
                else:
                    # Leaf result: (success, node)
                    pass

                # Checkpoint after every completed node
                _write_checkpoint(tree, out_path, arc_segment, config, lock)

                # Progress callback (outside lock)
                if progress is not None:
                    done_count = sum(1 for n in tree.values() if n.get("kind") is not None)
                    total_count = len(tree)
                    progress(done_count, total_count, tree[nid])

                if cancelled:
                    break

    # 8. FINAL CHECKS AND RETURN
    _write_checkpoint(tree, out_path, arc_segment, config, lock)
    brief = merge_brief(arc_segment, tree, config)

    # Sensitivity budget check on the flattened segment slot list
    sens_msg = check_sensitivity_budget(brief["slots"], config)
    if sens_msg:
        log.warning("sensitivity budget: %s", sens_msg)

    # Density check
    target_slots = config["segment"]["target_slots"]
    density_floor = config["segment"]["density_floor"]
    if len(brief["slots"]) < target_slots * density_floor:
        log.warning("density below floor: %d slots < %d * %.2f", len(brief["slots"]), target_slots, density_floor)
        brief["needs_rebrief"] = True

    # Tree-shape summary
    total_nodes = len(tree)
    leaves = sum(1 for n in tree.values() if n.get("kind") == "leaf")
    forced_leaves = sum(1 for n in tree.values() if n.get("forced"))
    max_depth_seen = max((n["depth"] for n in tree.values()), default=0)
    total_slots = len(brief["slots"])
    skipped = sum(1 for n in tree.values() if n.get("kind") == "leaf" and not n.get("slots"))

    log.info("tree shape: %d nodes, %d leaves (%d forced), max depth %d, %d slots, %d skipped",
             total_nodes, leaves, forced_leaves, max_depth_seen, total_slots, skipped)

    # Write final brief with needs_rebrief if set
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(brief, f, sort_keys=False, allow_unicode=True)

    if not brief["slots"]:
        return None

    return brief
