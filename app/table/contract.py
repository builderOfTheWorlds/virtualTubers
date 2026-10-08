"""Scene contracts for the live agent table (build plan P3.2, decision U8).

A generator run's tree leaf slot is one scene contract: the live table plays
the slot (instead of Layer 3 writing its dialogue) and the arbiter checks the
scene against the contract. `contracts_for_run` is pure (no DB): feed it a
`RunArtifacts` from `table.arc_source`.

Walk order (deterministic): arc_plan segments by `order` (tie: id) -> that
segment's tree depth-first, siblings by node `order` (tie: position in the
parent's `children`, then node id) -> leaf slots in list order. Branch-node
slots (none in current runs) are played before their children.

Field mapping:
  canon_goal      <- slot.prompt; a `spine` slot has no prompt, only a
                     `scene_ref` (a campaigns/<pack>/scenes stem): canon_goal then
                     falls back to the leaf summary, `scene_ref` is carried and a
                     warning is recorded. A slot with neither is warned too.
  scene_ref       <- slot.scene_ref ("" when absent)
  participants    <- slot.participants, normalised to pack cast ids
                     (case-insensitive; spaces/hyphens -> '_'), then to seats via
                     `seat_of` (looked up by cast id, then by the raw name).
                     Unknown / unmapped names are dropped AND recorded in
                     `ContractList.warnings`; duplicates collapse (first wins).
  lore            <- slot.lore
  continuity_in/out <- leaf.continuity_in/out, falling back to the segment's
  must_resolve    <- leaf continuity_out (fallback: segment continuity_out)
                     split into sentence items: split after '.', '!' or '?'
                     followed by whitespace and on ';' / newlines; items are
                     stripped, trailing '.' removed, empties dropped. Every slot
                     of a leaf carries the leaf's must_resolve (the leaf is the
                     smallest unit with a continuity contract).
  expected_beats  <- leaf.summary split into sentences by the same rule, then
                     "kind:<slot.kind>" when the slot has a kind.
  scene_id        <- f"{segment_id}.{leaf_id}.{slot_id}" with every char outside
                     [A-Za-z0-9_.-] replaced by '_' (collisions get '~N').
"""
import dataclasses
import re
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

CONTRACT_FIELDS = (
    "scene_id", "run_id", "segment_id", "leaf_id", "slot_id", "order",
    "canon_goal", "participants", "lore", "continuity_in", "continuity_out",
    "must_resolve", "expected_beats", "max_rounds", "max_retries_per_turn",
    "scene_ref",
)
_OPTIONAL = {"max_rounds": 4, "max_retries_per_turn": 2, "scene_ref": ""}
_TUPLE_FIELDS = ("participants", "lore", "must_resolve", "expected_beats")
_INT_FIELDS = ("order", "max_rounds", "max_retries_per_turn")


class ContractError(ValueError):
    """Bad contract payload."""


@dataclasses.dataclass(frozen=True)
class SceneContract:
    scene_id: str
    run_id: str
    segment_id: str
    leaf_id: str
    slot_id: str
    order: int
    canon_goal: str
    participants: Tuple[str, ...]
    lore: Tuple[str, ...]
    continuity_in: str
    continuity_out: str
    must_resolve: Tuple[str, ...]
    expected_beats: Tuple[str, ...]
    max_rounds: int = 4
    max_retries_per_turn: int = 2
    scene_ref: str = ""

    def to_payload(self) -> Dict[str, Any]:
        """JSON-safe dict (tuples -> lists); used as scene_start.contract."""
        out = {}
        for name in CONTRACT_FIELDS:
            value = getattr(self, name)
            out[name] = list(value) if name in _TUPLE_FIELDS else value
        return out

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "SceneContract":
        if not isinstance(payload, Mapping):
            raise ContractError("contract payload must be a mapping")
        kwargs = {}
        for name in CONTRACT_FIELDS:
            if name not in payload:
                if name in _OPTIONAL:
                    continue
                raise ContractError(f"contract payload missing field {name!r}")
            value = payload[name]
            if name in _TUPLE_FIELDS:
                if not isinstance(value, (list, tuple)) or not all(isinstance(v, str) for v in value):
                    raise ContractError(f"contract field {name!r} must be a list of str")
                value = tuple(value)
            elif name in _INT_FIELDS:
                if not isinstance(value, int) or isinstance(value, bool):
                    raise ContractError(f"contract field {name!r} must be an int")
            elif not isinstance(value, str):
                raise ContractError(f"contract field {name!r} must be a str")
            kwargs[name] = value
        return cls(**kwargs)


class ContractList(list):
    """list[SceneContract] plus `warnings` (dropped participants, missing trees...)."""

    def __init__(self, items=(), warnings=None):
        super().__init__(items)
        self.warnings: List[str] = list(warnings or [])


_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+|;|\n+")
_UNSAFE = re.compile(r"[^A-Za-z0-9_.\-]")


def split_items(text: Optional[str]) -> Tuple[str, ...]:
    """The must_resolve / expected_beats sentence-splitting rule (module doc)."""
    if not text:
        return ()
    items = []
    for part in _SENT_SPLIT.split(str(text)):
        part = part.strip().rstrip(".").strip()
        if part:
            items.append(part)
    return tuple(items)


def safe_id(text: str) -> str:
    return _UNSAFE.sub("_", str(text))


def _norm(name: str) -> str:
    return re.sub(r"[\s\-]+", "_", str(name).strip()).casefold()


def _walk_tree(tree: Mapping[str, Any], warnings: List[str], segment_id: str):
    """Yield nodes depth-first, siblings by node order."""
    nodes = {nid: n for nid, n in tree.items() if isinstance(n, Mapping)}

    def sort_key(nid, pos):
        n = nodes.get(nid) or {}
        return (n.get("order", 0), pos, str(nid))

    roots = [nid for nid, n in nodes.items() if not n.get("parent_id")]
    roots.sort(key=lambda nid: sort_key(nid, 0))
    seen = set()

    def visit(nid):
        if nid in seen:
            return
        if nid not in nodes:
            warnings.append(f"{segment_id}: tree references missing node {nid!r}")
            return
        seen.add(nid)
        node = nodes[nid]
        yield nid, node
        children = list(node.get("children") or [])
        for child in sorted(children, key=lambda c: sort_key(c, children.index(c))):
            yield from visit(child)

    for root in roots:
        yield from visit(root)
    unreached = sorted(set(nodes) - seen)
    if unreached:
        warnings.append(f"{segment_id}: {len(unreached)} tree node(s) unreachable from a root: {unreached}")


def contracts_for_run(artifacts, seat_of: Mapping[str, str], *,
                      pack_cast: Iterable[str],
                      max_rounds: int = 4,
                      max_retries_per_turn: int = 2) -> ContractList:
    """All scene contracts of a run, in play order. See the module doc for the
    mapping. Unmapped participants are recorded in the result's `warnings`."""
    cast_by_norm = {}
    for cid in pack_cast:
        cast_by_norm.setdefault(_norm(cid), cid)
    seat_lookup = {}
    for key, seat in seat_of.items():
        seat_lookup.setdefault(str(key), seat)
        seat_lookup.setdefault(_norm(key), seat)

    warnings: List[str] = []
    out: List[SceneContract] = []
    used_ids = set()
    run_id = artifacts.run_id

    for seg in artifacts.segments():
        seg_id = str(seg.get("id"))
        tree = artifacts.trees.get(seg_id)
        if not tree:
            warnings.append(f"{seg_id}: no tree artifact; segment skipped")
            continue
        seg_start = len(out)
        empty_leaves = []
        for node_id, node in _walk_tree(tree, warnings, seg_id):
            slots = node.get("slots") or []
            if not slots:
                if node.get("kind") == "leaf":
                    empty_leaves.append(str(node_id))
                continue
            c_in = node.get("continuity_in") or seg.get("continuity_in") or ""
            c_out = node.get("continuity_out") or seg.get("continuity_out") or ""
            must = split_items(c_out)
            summary_beats = split_items(node.get("summary"))
            for slot in slots:
                slot_id = str(slot.get("slot_id") or f"slot{len(out)}")
                where = f"{seg_id}/{node_id}/{slot_id}"
                seats: List[str] = []
                for raw in slot.get("participants") or []:
                    cast_id = cast_by_norm.get(_norm(raw))
                    if cast_id is None:
                        warnings.append(f"{where}: participant {raw!r} is not in the pack cast; dropped")
                        continue
                    seat = (seat_lookup.get(cast_id) or seat_lookup.get(str(raw))
                            or seat_lookup.get(_norm(cast_id)))
                    if seat is None:
                        warnings.append(f"{where}: participant {raw!r} (cast {cast_id!r}) has no seat; dropped")
                        continue
                    if seat not in seats:
                        seats.append(seat)
                if not seats:
                    warnings.append(f"{where}: no seated participants")
                beats = summary_beats
                if slot.get("kind"):
                    beats = beats + (f"kind:{slot['kind']}",)
                goal = str(slot.get("prompt") or "").strip()
                scene_ref = str(slot.get("scene_ref") or "")
                if not goal:
                    goal = str(node.get("summary") or "").strip()
                    warnings.append(
                        f"{where}: slot has no prompt (kind={slot.get('kind')!r}, "
                        f"scene_ref={scene_ref!r}); canon_goal <- leaf summary")
                scene_id = safe_id(f"{seg_id}.{node_id}.{slot_id}")
                if scene_id in used_ids:
                    n = 2
                    while f"{scene_id}~{n}" in used_ids:
                        n += 1
                    warnings.append(f"{where}: duplicate scene id {scene_id!r}; renamed")
                    scene_id = f"{scene_id}~{n}"
                used_ids.add(scene_id)
                out.append(SceneContract(
                    scene_id=scene_id, run_id=run_id, segment_id=seg_id,
                    leaf_id=str(node_id), slot_id=slot_id, order=len(out),
                    canon_goal=goal, scene_ref=scene_ref,
                    participants=tuple(seats),
                    lore=tuple(str(x) for x in (slot.get("lore") or [])),
                    continuity_in=str(c_in), continuity_out=str(c_out),
                    must_resolve=must, expected_beats=beats,
                    max_rounds=max_rounds, max_retries_per_turn=max_retries_per_turn,
                ))
        if empty_leaves:
            warnings.append(f"{seg_id}: {len(empty_leaves)} leaf node(s) without slots: {empty_leaves}")
        if len(out) == seg_start:
            warnings.append(f"{seg_id}: segment produced no contracts")
    return ContractList(out, warnings)


def next_contract(contracts: Sequence[SceneContract],
                  after_scene_id: Optional[str]) -> Optional[SceneContract]:
    """The contract after `after_scene_id` (None -> the first); None at the end.
    Raises KeyError for an unknown scene id."""
    if after_scene_id is None:
        return contracts[0] if contracts else None
    for i, c in enumerate(contracts):
        if c.scene_id == after_scene_id:
            return contracts[i + 1] if i + 1 < len(contracts) else None
    raise KeyError(after_scene_id)
