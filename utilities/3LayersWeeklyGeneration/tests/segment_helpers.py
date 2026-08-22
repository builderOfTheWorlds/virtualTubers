"""Shared fixtures for the Layer 2 test modules.

Layer 2 is split the same way Layer 1 is — `segment_schema` (pure: parse,
validate, build prompts, merge the brief) and `plan_segment` (the recursive
tree orchestrator) — so the config and the sample node/child/slot builders
live here rather than drifting apart across two test files.
"""
import pytest
import yaml

from arc_helpers import FakePack


@pytest.fixture
def segment_config():
    return {
        "arc": {"hours_total": 168, "segment_hours": 6, "batch_size": 6,
                "max_attempts": 2},
        "segment": {
            "target_words": 53600,
            "target_slots": 170,
            "tree": {
                "max_leaf_slots": 19,
                "max_children": 12,
                "max_depth": 4,
                "min_node_words": 2000,
                "leaf_density_floor": 0.80,
            },
            "concurrency": 4,
            "max_attempts": 2,
            "sensitivity_budget": 0.40,
            "density_floor": 0.80,
        },
        "dialogue": {"takes_per_slot": 3, "neutral_takes": 1},
        "budget": {"measured_baseline": {"words_per_take": 105,
                                         "beats_per_take": 7.7,
                                         "generation_words_per_min": 95.4}},
        "state": {
            "flags": ["helen-wounded", "moonwell-tainted", "buffalo-lost-axe"],
            "moods": ["tense", "weary", "hopeful", "giddy"],
            "carry_keys": ["helen-wounded", "moonwell-tainted"],
        },
    }


@pytest.fixture
def arc_segment():
    """One entry out of arc_plan.yaml — Layer 2's whole input for a segment."""
    return {
        "id": "seg-001",
        "order": 0,
        "loop": 0,
        "hours": 6,
        "spine_scenes": ["moonwell"],
        "ambient_focus": ["camp-chatter", "road-song"],
        "synopsis": "The company reaches the moonwell and finds it fouled.",
        "continuity_in": "They are two days out from the ridge.",
        "continuity_out": "Helen is wounded and the well is tainted.",
        "carry_in": {},
        "carry_out": {"helen-wounded": True, "moonwell-tainted": True},
        "event_windows": [],
        "fork": None,
    }


def node(node_id="seg-001", parent_id=None, order=0, depth=0,
         target_words=53600, target_slots=170, **overrides):
    """One node of the segment tree. Depth 0 is the segment root."""
    record = {
        "node_id": node_id,
        "parent_id": parent_id,
        "order": order,
        "depth": depth,
        "title": f"Node {node_id}",
        "summary": "Part of the company's long afternoon.",
        "continuity_in": "They are walking.",
        "continuity_out": "They are still walking, but wetter.",
        "target_words": target_words,
        "target_slots": target_slots,
        "kind": None,
        "forced": False,
        "children": [],
        "slots": None,
    }
    record.update(overrides)
    return record


def child(order, weight=1.0, **overrides):
    """One entry as the model returns it from an expand call — no ids, no
    word budgets. Those are derived locally by distribute_words()."""
    record = {
        "order": order,
        "title": f"Child {order}",
        "summary": f"A slice of the afternoon, part {order + 1}.",
        "continuity_in": "They are walking.",
        "continuity_out": "They are still walking.",
        "weight": weight,
    }
    record.update(overrides)
    return record


def children_reply(count, weights=None):
    weights = weights or [1.0] * count
    return yaml.safe_dump(
        {"children": [child(i, weight=weights[i]) for i in range(count)]},
        sort_keys=False)


def ambient_slot(index, **overrides):
    """A valid 2b ambient slot."""
    slot = {
        "slot_id": f"s-{index:03d}",
        "kind": "ambient",
        "prompt": ("Around ninety seconds, written for the ear: Buffalo needles "
                   "Helen about the map while the fire pops."),
        "lore": ["the-loop"],
        "participants": ["helen", "buffalo"],
        "sensitivity": "none",
        "depends_on": [],
    }
    slot.update(overrides)
    return slot


def spine_slot(index, **overrides):
    """A valid 2b spine slot — a pointer to authored canon, not new dialogue."""
    slot = {
        "slot_id": f"s-{index:03d}",
        "kind": "spine",
        "scene_ref": "moonwell",
        "participants": ["helen", "buffalo"],
        "summary": "They reach the moonwell.",
    }
    slot.update(overrides)
    return slot


def slots_reply(count, start=1, **overrides):
    return yaml.safe_dump(
        {"slots": [ambient_slot(i, **overrides) for i in range(start, start + count)]},
        sort_keys=False)


class FakeSegmentLLM:
    """An LLM that answers expand and leaf calls differently.

    It dispatches on the system prompt rather than on call order, because the
    2b calls run concurrently and their order is not knowable.
    """

    def __init__(self, children=9, slots=19, chapter_replies=None,
                 slot_replies=None, weights=None):
        self.children = children
        self.slots = slots
        self.weights = weights
        # Optional lists of canned replies, consumed in order, for testing
        # retries. When exhausted, the good reply is returned.
        self.chapter_replies = list(chapter_replies or [])
        self.slot_replies = list(slot_replies or [])
        self.expand_prompts = []
        self.chapter_prompts = []
        self.slot_prompts = []
        self._lock = __import__("threading").Lock()

    def complete(self, system_prompt, messages):
        assert isinstance(messages, list), (
            f"messages must be a list of role/content mappings, "
            f"got {type(messages).__name__}")
        for message in messages:
            assert isinstance(message, dict) and "content" in message, (
                f"each message must be a mapping with a 'content' key, "
                f"got {message!r}")
        prompt = messages[-1]["content"]

        import segment_schema
        with self._lock:
            if system_prompt == segment_schema.SYSTEM_PROMPT_EXPAND:
                self.expand_prompts.append(prompt)
                self.chapter_prompts.append(prompt)
                if self.chapter_replies:
                    return self.chapter_replies.pop(0)
                return children_reply(self.children, self.weights)

            assert system_prompt == segment_schema.SYSTEM_PROMPT_LEAF, (
                "every call must use one of the two exported system prompts")
            self.slot_prompts.append(prompt)
            if self.slot_replies:
                return self.slot_replies.pop(0)
            return slots_reply(self.slots)
