"""
Schema, parser, validator and prompt builder for one arc segment.

This is the PURE half of Layer 2 of the 3-layer offline content generator.
Layer 2 turns one arc segment into a brief: a RECURSIVE TREE of nodes, whose
leaves hold the slots Layer 3 will voice. Everything here is a function of its
arguments — no LLM, no network, no disk.

v3 replaces the flat nine-chapters-per-segment split with a tree. The reason
is airtime: a segment is six hours, and how much happens in those six hours is
wildly uneven. A flat split gives a quiet stretch of road the same budget as
the arrival at the moonwell. A tree lets the model say "this slice carries
three times the weight of that one" and recurse only where the density
actually demands it. A uniform-weight run reproduces the old shape almost
exactly — root splits into ~9 leaves of ~19 slots — so the default behaviour
does not regress.

Two properties matter more than any individual function here:

  * WORDS RECONCILE EXACTLY. A parent's `target_words` is split among its
    children and the children's shares MUST sum back to it, to the word.
    Slots may drift by a rounding unit; words may not. Words are the budget
    that ties back to the 168-hour target, and a tree that leaks three percent
    per level has lost a fifth of the week by depth four.

  * THE TREE ALWAYS TERMINATES, AND NEVER BY DROPPING A NODE. Both `max_depth`
    and expand-exhaustion force a LEAF, flagged `forced: True`. A forced leaf
    is oversized and gets planned anyway; a dropped node is a silent hole in
    the airtime that nothing downstream can detect.
"""
import logging
import math
import re
import yaml

log = logging.getLogger(__name__)


class SegmentPlanError(ValueError):
    """Raised for a reply that cannot be parsed, and for an infeasible tree
    config. NOT raised for a batch that fails validation — that path returns
    problem strings and the caller retries."""
    pass


SYSTEM_PROMPT_EXPAND = (
    "You are dividing a stretch of airtime into child slices. Each child slice "
    "carries a `weight` reflecting how much happens in it: a quiet stretch of "
    "road weighs less than the arrival at the moonwell, and the weights are "
    "relative, not a fraction that must sum to one. Give each child a title, a "
    "summary, a continuity_in that follows from the parent, a continuity_out "
    "that hands off to the next slice, and a positive numeric weight. Reply "
    "with ONLY YAML under a `children:` key."
)

SYSTEM_PROMPT_LEAF = (
    "You are writing the slots for one leaf of a segment. Each slot is a unit "
    "of airtime that Layer 3 will voice. Give each slot a slot_id, a kind "
    "('ambient' or 'spine'), and the fields its kind requires. Ambient slots "
    "carry a prompt, lore stems, participants, a sensitivity, and depends_on; "
    "spine slots point at an authored scene_ref. Reply with ONLY YAML under a "
    "`slots:` key."
)


def derive_target_slots(target_words, config) -> int:
    """Convert a word budget into a slot count.

    `round(target_words / (words_per_take * takes_per_slot))`, reading the
    measured baseline from config so that re-measuring it re-derives every
    budget in the tree. At the shipped defaults that is 105 * 3 = 315 words
    per slot, so the root's 53600 words derive to 170.
    """
    log.debug("derive_target_slots called with target_words=%s", target_words)
    words_per_take = config["budget"]["measured_baseline"]["words_per_take"]
    takes_per_slot = config["dialogue"]["takes_per_slot"]
    words_per_slot = words_per_take * takes_per_slot
    if words_per_slot <= 0:
        raise SegmentPlanError(
            f"words_per_slot must be positive, got {words_per_slot}")
    slots = round(target_words / words_per_slot)
    log.debug("derive_target_slots returning %d", slots)
    return slots


def leaf_eligible(target_slots, config) -> bool:
    """The recursion trigger: a node is a leaf when its slot count is at or
    below `max_leaf_slots`. Returns a real bool."""
    log.debug("leaf_eligible called with target_slots=%s", target_slots)
    max_leaf_slots = config["segment"]["tree"]["max_leaf_slots"]
    result = target_slots <= max_leaf_slots
    log.debug("leaf_eligible returning %s", result)
    return result


def validate_tree_config(config) -> list:
    """The knob interlock. Returns problem strings; [] when the config is
    feasible. Reads `config["segment"]["tree"]`.

    The last check exists because these knobs can be set mutually infeasible,
    and the failure is silent rather than loud: if `min_node_words` exceeds
    half a leaf's worth of words, no split can ever produce two floor-legal
    children, so every expand call fails validation, retries, exhausts, and
    degrades to a forced leaf. Catching it once at startup costs nothing.
    """
    log.debug("validate_tree_config called")
    problems = []
    tree = config["segment"]["tree"]
    words_per_take = config["budget"]["measured_baseline"]["words_per_take"]
    takes_per_slot = config["dialogue"]["takes_per_slot"]

    max_children = tree.get("max_children")
    if not isinstance(max_children, int) or isinstance(max_children, bool) or max_children < 2:
        problems.append(f"max_children must be >= 2, got {max_children!r}")

    max_depth = tree.get("max_depth")
    if not isinstance(max_depth, int) or isinstance(max_depth, bool) or max_depth < 1:
        problems.append(f"max_depth must be >= 1, got {max_depth!r}")

    max_leaf_slots = tree.get("max_leaf_slots")
    if not isinstance(max_leaf_slots, int) or isinstance(max_leaf_slots, bool) or max_leaf_slots < 1:
        problems.append(f"max_leaf_slots must be >= 1, got {max_leaf_slots!r}")

    leaf_density_floor = tree.get("leaf_density_floor")
    if (not isinstance(leaf_density_floor, (int, float))
            or isinstance(leaf_density_floor, bool)
            or not (0 < leaf_density_floor <= 1)):
        problems.append(
            f"leaf_density_floor must satisfy 0 < leaf_density_floor <= 1, "
            f"got {leaf_density_floor!r}")

    min_node_words = tree.get("min_node_words")
    if (isinstance(max_leaf_slots, int) and not isinstance(max_leaf_slots, bool)
            and max_leaf_slots >= 1
            and isinstance(min_node_words, (int, float))
            and not isinstance(min_node_words, bool)):
        half_leaf_words = max_leaf_slots * words_per_take * takes_per_slot / 2
        if min_node_words * 2 > max_leaf_slots * words_per_take * takes_per_slot:
            problems.append(
                f"min_node_words ({min_node_words}) exceeds half a leaf's worth "
                f"of words ({half_leaf_words}); no split can produce two "
                f"floor-legal children")

    log.debug("validate_tree_config returning %s", problems)
    return problems


def distribute_words(children, node, config) -> list:
    """Turn the model's relative `weight` values into exact word budgets.

    Returns a NEW list of NEW dicts — the input is not mutated. Weights need
    not sum to 1; they are normalised locally. The reconciliation in step 3
    is the point of this function: independent rounding of each share does not
    sum to the parent, so the remainder is added to the child with the largest
    share and the sum then equals the parent exactly. Derived per-child SLOT
    counts are NOT reconciled — they may drift by one per child.
    """
    log.debug("distribute_words called with %d children", len(children))
    total = sum(float(c["weight"]) for c in children)
    if total <= 0:
        raise SegmentPlanError("total weight must be positive")

    shares = [round(node["target_words"] * float(c["weight"]) / total)
              for c in children]

    remainder = node["target_words"] - sum(shares)
    if remainder:
        largest = max(range(len(shares)), key=lambda i: shares[i])
        shares[largest] += remainder

    out = []
    for child, share in zip(children, shares):
        entry = dict(child)
        entry["target_words"] = share
        entry["target_slots"] = derive_target_slots(share, config)
        out.append(entry)

    log.debug("distribute_words returning %d entries summing to %d",
              len(out), sum(e["target_words"] for e in out))
    return out


def _parse_reply(reply, key):
    """Shared parser. Tolerates, in this order: a ```yaml or bare ``` fence;
    prose before the document; a mapping with the expected key OR a bare
    top-level list. Raises SegmentPlanError when nothing parses or the result
    is not a list of mappings."""
    log.debug("_parse_reply called with key=%s", key)
    text = reply.strip()

    m = re.search(r"```(?:yaml|yml)?\s*\n(.*?)```", text, re.DOTALL)
    if m:
        candidates = [m.group(1)]
    else:
        candidates = [text]
        lines = text.splitlines()
        for i, line in enumerate(lines):
            if line.startswith(key + ":") or line.startswith("- "):
                candidates.append("\n".join(lines[i:]))
                break

    for candidate in candidates:
        try:
            parsed = yaml.safe_load(candidate)
        except yaml.YAMLError:
            continue

        if isinstance(parsed, dict) and key in parsed:
            value = parsed[key]
            if not isinstance(value, list):
                raise SegmentPlanError(f"{key} must be a list")
            for item in value:
                if not isinstance(item, dict):
                    raise SegmentPlanError(f"each {key} entry must be a mapping")
            log.debug("_parse_reply returning %d items", len(value))
            return value

        if isinstance(parsed, list):
            for item in parsed:
                if not isinstance(item, dict):
                    raise SegmentPlanError(f"each {key} entry must be a mapping")
            log.debug("_parse_reply returning %d items (bare list)", len(parsed))
            return parsed

    raise SegmentPlanError(f"could not parse reply into a {key} list")


def parse_children(reply) -> list:
    """Extract the children list from a raw model reply."""
    return _parse_reply(reply, "children")


def parse_slots(reply) -> list:
    """Extract the slots list from a raw model reply."""
    return _parse_reply(reply, "slots")


def validate_children(children, node, config) -> list:
    """Validate a batch of children. Returns problem strings, [] when clean.
    Never raises.

    The required-key check runs first and returns early, exactly as the arc
    validator does: every later check indexes into the child, and running them
    on a child missing `weight` would die with a KeyError, turning a
    recoverable bad batch into a crashed run.
    """
    log.debug("validate_children called with %d children", len(children))
    problems = []
    tree = config["segment"]["tree"]
    max_children = tree["max_children"]
    min_node_words = tree["min_node_words"]

    REQUIRED_KEYS = ["order", "title", "summary", "continuity_in",
                     "continuity_out", "weight"]

    # Required keys first, early return.
    for index, child in enumerate(children):
        if not isinstance(child, dict):
            problems.append(f"child {index} is not a mapping")
            continue
        for key in REQUIRED_KEYS:
            if key not in child:
                problems.append(
                    f"child {child.get('order', index)} is missing required "
                    f"key '{key}'")
    if problems:
        log.debug("validate_children returning early: %s", problems)
        return problems

    # Non-empty list. Load-bearing: every later check divides by the summed
    # weight, which is 0 for an empty list.
    if not children:
        problems.append("children list is empty")
        log.debug("validate_children returning: %s", problems)
        return problems

    # Cap.
    if len(children) > max_children:
        problems.append(
            f"{len(children)} children exceeds max_children ({max_children})")

    # Orders: exactly the set 0..N-1. Out-of-sequence but complete is fine.
    orders = [c["order"] for c in children]
    if sorted(orders) != list(range(len(children))):
        problems.append(
            f"orders {orders} are not exactly 0..{len(children) - 1}")

    # Weight: positive real number. bool is not acceptable.
    for index, child in enumerate(children):
        weight = child["weight"]
        if (isinstance(weight, bool)
                or not isinstance(weight, (int, float))
                or weight <= 0):
            problems.append(
                f"child {child.get('order', index)} weight must be a positive "
                f"number, got {weight!r}")

    # Prose fields: non-empty after strip.
    for index, child in enumerate(children):
        for field in ("summary", "continuity_in", "continuity_out"):
            value = child[field]
            if not isinstance(value, str) or not value.strip():
                problems.append(
                    f"child {child.get('order', index)} {field} must be a "
                    f"non-empty string")

    # If any of the above failed, the weights are not all positive numbers,
    # so distribute_words would raise. Return before touching it.
    if problems:
        log.debug("validate_children returning: %s", problems)
        return problems

    # Floor check on the DISTRIBUTED share. Only reached once every check
    # above has passed, so the list is non-empty and weights are positive.
    distributed = distribute_words(children, node, config)
    for entry in distributed:
        if entry["target_words"] < min_node_words:
            problems.append(
                f"child {entry.get('order')} gets {entry['target_words']} "
                f"words, below min_node_words ({min_node_words})")

    log.debug("validate_children returning %s", problems)
    return problems


def validate_slots(slots, pack, vocab, config, node=None) -> list:
    """Validate a batch of slots. Returns problem strings, [] when clean.
    Never raises.

    The rule that makes "never raises" true: when a value fails its type
    check, append the problem and then SKIP every check that uses it.
    `prompt`, `lore`, `sensitivity` and `depends_on` exist ONLY on ambient
    slots; `participants` and `slot_id` are checked on both kinds.
    """
    log.debug("validate_slots called with %d slots", len(slots))
    problems = []

    AMBIENT_REQUIRED = ["slot_id", "kind", "prompt", "lore", "participants",
                        "sensitivity", "depends_on"]
    SPINE_REQUIRED = ["slot_id", "kind", "scene_ref", "participants"]

    # Required keys first, early return.
    for slot in slots:
        if not isinstance(slot, dict):
            problems.append("slot is not a mapping")
            continue
        kind = slot.get("kind")
        if kind == "ambient":
            required = AMBIENT_REQUIRED
        elif kind == "spine":
            required = SPINE_REQUIRED
        else:
            problems.append(
                f"slot '{slot.get('slot_id', '<no slot_id>')}' has unknown "
                f"kind '{kind}' (must be 'ambient' or 'spine')")
            continue
        for key in required:
            if key not in slot:
                problems.append(
                    f"slot '{slot.get('slot_id', '<no slot_id>')}' is missing "
                    f"required key '{key}'")
    if problems:
        log.debug("validate_slots returning early: %s", problems)
        return problems

    # Per-slot checks.
    for slot in slots:
        slot_id = slot.get("slot_id", "<no slot_id>")
        kind = slot["kind"]

        # Delegate to vocab.
        problems.extend(vocab.validate_slot(slot))

        # participants: non-empty list, every id a key of pack.cast.
        participants = slot.get("participants")
        if not isinstance(participants, list):
            problems.append(f"slot '{slot_id}' participants must be a list")
        else:
            if not participants:
                problems.append(f"slot '{slot_id}' participants must be non-empty")
            else:
                for pid in participants:
                    if pid not in pack.cast:
                        problems.append(
                            f"slot '{slot_id}' has unknown participant '{pid}'")

        if kind == "ambient":
            # An ambient slot must NOT have a scene_ref.
            if "scene_ref" in slot:
                problems.append(
                    f"slot '{slot_id}' is ambient and must not have a "
                    f"scene_ref")

            # prompt: non-empty string.
            prompt = slot.get("prompt")
            if not isinstance(prompt, str) or not prompt.strip():
                problems.append(
                    f"slot '{slot_id}' prompt must be a non-empty string")

            # depends_on: must be a list.
            depends_on = slot.get("depends_on")
            if not isinstance(depends_on, list):
                problems.append(
                    f"slot '{slot_id}' depends_on must be a list")
            else:
                # flags with empty depends_on is a problem.
                if slot.get("sensitivity") == "flags" and not depends_on:
                    problems.append(
                        f"slot '{slot_id}' has sensitivity 'flags' but an "
                        f"empty depends_on")
                # Budget check.
                takes = config["dialogue"]["takes_per_slot"]
                neutral = config["dialogue"]["neutral_takes"]
                conditioned = takes - neutral
                if len(depends_on) * 2 > conditioned:
                    problems.append(
                        f"slot '{slot_id}' declares {len(depends_on)} "
                        f"dependencies, needing {len(depends_on) * 2} takes, "
                        f"but only {conditioned} conditioned takes are "
                        f"available")

    # Duplicate slot_ids.
    seen = set()
    for slot in slots:
        slot_id = slot.get("slot_id")
        if slot_id in seen:
            problems.append(f"duplicate slot_id '{slot_id}'")
        else:
            seen.add(slot_id)

    # Optional node density check.
    if node is not None:
        leaf_density_floor = config["segment"]["tree"]["leaf_density_floor"]
        if len(slots) < node["target_slots"] * leaf_density_floor:
            problems.append(
                f"slot count {len(slots)} is below the density floor "
                f"({node['target_slots']} * {leaf_density_floor}) for this "
                f"leaf")

    log.debug("validate_slots returning %s", problems)
    return problems


def sensitivity_mix(slots) -> dict:
    """Count AMBIENT slots by sensitivity. A slot with no `sensitivity` key
    is not counted at all. Spine slots have no sensitivity — they are canon."""
    log.debug("sensitivity_mix called with %d slots", len(slots))
    mix = {"none": 0, "tone": 0, "flags": 0}
    for slot in slots:
        if not isinstance(slot, dict):
            continue
        if slot.get("kind") != "ambient":
            continue
        sensitivity = slot.get("sensitivity")
        if sensitivity in mix:
            mix[sensitivity] += 1
    log.debug("sensitivity_mix returning %s", mix)
    return mix


def check_sensitivity_budget(slots, config):
    """Return a message string when the fraction of `flags` slots exceeds
    `config["segment"]["sensitivity_budget"]`, otherwise None.

    This is a WARNING, not a rejection. The brief is still usable; it just
    needs a human to look at it.
    """
    log.debug("check_sensitivity_budget called with %d slots", len(slots))
    mix = sensitivity_mix(slots)
    total = sum(mix.values())
    if total == 0:
        log.debug("check_sensitivity_budget returning None (no ambient slots)")
        return None
    fraction = mix["flags"] / total
    budget = config["segment"]["sensitivity_budget"]
    if fraction <= budget:
        log.debug("check_sensitivity_budget returning None (within budget)")
        return None
    log.warning("sensitivity budget exceeded: %d of %d slots are flags (%.2f), "
                "over budget %s", mix["flags"], total, fraction, budget)
    return (f"{mix['flags']} of {total} slots are state-sensitive "
            f"({fraction:.2f}), over the budget of {budget}")


def build_expand_prompt(pack, arc_segment, ancestors, node, config, problems) -> str:
    """The user prompt for ONE expand attempt. `ancestors` is a list of node
    records, ROOT-EXCLUSIVE, ordered shallow -> deep; empty at depth 1."""
    log.debug("build_expand_prompt called with %d ancestors", len(ancestors))
    tree = config["segment"]["tree"]
    max_leaf_slots = tree["max_leaf_slots"]
    max_children = tree["max_children"]

    expected_children = min(
        math.ceil(node["target_slots"] / max_leaf_slots), max_children)

    lines = [
        f"Segment: {arc_segment['synopsis']}",
        f"Continuity in: {arc_segment['continuity_in']}",
        f"Continuity out: {arc_segment['continuity_out']}",
        "",
    ]

    for ancestor in ancestors:
        lines.append(f"Ancestor: {ancestor['title']}")
        lines.append(f"  {ancestor['summary']}")

    lines.append("")
    lines.append(f"Node: {node['title']}")
    lines.append(f"  Summary: {node['summary']}")
    lines.append(f"  Continuity in: {node['continuity_in']}")
    lines.append(f"  Continuity out: {node['continuity_out']}")
    lines.append(f"  Target words: {node['target_words']}")
    lines.append("")
    lines.append(f"Expected children: {expected_children}")
    lines.append("Give each child a `weight` reflecting how much happens in it.")
    lines.append("")
    lines.append("Required keys for each child:")
    for key in ("order", "title", "summary", "continuity_in",
                "continuity_out", "weight"):
        lines.append(f"- {key}")
    lines.append("")
    lines.append("Reply with ONLY YAML under a `children:` key.")

    if problems:
        lines.append("")
        lines.append("Previous attempt had these problems:")
        for problem in problems:
            lines.append(f"- {problem}")

    result = "\n".join(lines)
    log.debug("build_expand_prompt returning %d-character string", len(result))
    return result


def build_leaf_prompt(pack, arc_segment, ancestors, node, config, problems) -> str:
    """The user prompt for ONE leaf attempt. Same content as the current slot
    prompt, PLUS the ancestor chain, with the slot count taken from
    `node["target_slots"]`."""
    log.debug("build_leaf_prompt called with %d ancestors", len(ancestors))
    lines = [
        f"Segment: {arc_segment['synopsis']}",
        f"Continuity in: {arc_segment['continuity_in']}",
        f"Continuity out: {arc_segment['continuity_out']}",
        "",
    ]

    for ancestor in ancestors:
        lines.append(f"Ancestor: {ancestor['title']}")
        lines.append(f"  {ancestor['summary']}")

    lines.append("")
    lines.append(f"Node: {node['title']}")
    lines.append(f"  Summary: {node['summary']}")
    lines.append(f"  Continuity in: {node['continuity_in']}")
    lines.append(f"  Continuity out: {node['continuity_out']}")
    lines.append(f"  Target slots: {node['target_slots']}")
    lines.append("")

    # Legal state keys.
    lines.append("Legal state keys:")
    for key in sorted(config["state"]["flags"]):
        lines.append(f"- {key}")
    lines.append("")

    # Cast list.
    lines.append("Cast:")
    for name in sorted(pack.cast.keys()):
        lines.append(f"- {name}")
    lines.append("")

    # Spine ground truth.
    lines.append("Spine scenes:")
    for scene_id, scene in pack.scenes.items():
        if not scene.ambient:
            lines.append(f"- {scene_id}: {scene.title}")
    lines.append("")

    lines.append("Write for the ear.")
    lines.append("")
    lines.append("Required keys for each slot:")
    lines.append("ambient: slot_id, kind, prompt, lore, participants, "
                 "sensitivity, depends_on")
    lines.append("spine: slot_id, kind, scene_ref, participants")
    lines.append("")
    lines.append("Reply with ONLY YAML under a `slots:` key.")

    if problems:
        lines.append("")
        lines.append("Previous attempt had these problems:")
        for problem in problems:
            lines.append(f"- {problem}")

    result = "\n".join(lines)
    log.debug("build_leaf_prompt returning %d-character string", len(result))
    return result


def merge_brief(arc_segment, tree, config) -> dict:
    """Flatten the tree into the shape `worklist.py` already reads.

    DFS from the root, visiting each node's children IN `order`. At each LEAF,
    append its slots in their own order. Tag every emitted slot with
    `slot["node_id"] = <the leaf's id>`.
    """
    log.debug("merge_brief called with %d nodes", len(tree))

    # Find the root: the node whose parent_id is None.
    root_id = None
    for node_id, node in tree.items():
        if node.get("parent_id") is None:
            root_id = node_id
            break
    if root_id is None:
        root_id = arc_segment["id"]

    slots_out = []
    nodes_out = []

    def dfs(node_id):
        node = tree[node_id]
        if node.get("kind") == "leaf":
            for slot in node.get("slots", []):
                tagged = dict(slot)
                tagged["node_id"] = node_id
                slots_out.append(tagged)
            slot_count = len(node.get("slots", []))
        else:
            slot_count = 0

        nodes_out.append({
            "node_id": node_id,
            "parent_id": node.get("parent_id"),
            "depth": node.get("depth"),
            "kind": node.get("kind"),
            "forced": node.get("forced", False),
            "target_slots": node.get("target_slots"),
            "slots": slot_count,
        })

        # Visit children in order.
        children = node.get("children", [])
        if children:
            # Sort by the order field, not dict insertion order.
            def child_order(cid):
                child = tree.get(cid, {})
                return child.get("order", 0)
            for cid in sorted(children, key=child_order):
                dfs(cid)

    dfs(root_id)

    brief = {
        "segment_id": arc_segment["id"],
        "order": arc_segment.get("order"),
        "loop": arc_segment.get("loop"),
        "hours": arc_segment.get("hours"),
        "synopsis": arc_segment.get("synopsis"),
        "continuity_in": arc_segment.get("continuity_in"),
        "continuity_out": arc_segment.get("continuity_out"),
        "carry_in": arc_segment.get("carry_in", {}),
        "carry_out": arc_segment.get("carry_out", {}),
        "slots": slots_out,
        "nodes": nodes_out,
    }

    log.debug("merge_brief returning brief with %d slots, %d nodes",
              len(slots_out), len(nodes_out))
    return brief
