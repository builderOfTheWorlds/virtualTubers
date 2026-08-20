"""Shared fixtures for the Layer 2 test modules.

Layer 2 is split the same way Layer 1 is — `segment_schema` (pure: parse,
validate, build prompts, merge the brief) and `plan_segment` (the 2a/2b
orchestrator) — so the config and the sample chapter/slot builders live here
rather than drifting apart across two test files.
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
            "chapters_per_segment": 9,
            "concurrency": 4,
            "max_attempts": 2,
            "sensitivity_budget": 0.40,
            "density_floor": 0.80,
        },
        "dialogue": {"takes_per_slot": 3, "neutral_takes": 1},
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


def chapter(order, **overrides):
    """A valid 2a chapter mapping."""
    ch = {
        "chapter_id": f"ch-{order + 1:02d}",
        "order": order,
        "title": f"Chapter {order + 1}",
        "summary": f"Forty minutes of the company's afternoon, part {order + 1}.",
        "continuity_in": "They are walking.",
        "continuity_out": "They are still walking, but wetter.",
    }
    ch.update(overrides)
    return ch


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


def chapters_reply(count):
    return yaml.safe_dump({"chapters": [chapter(i) for i in range(count)]},
                          sort_keys=False)


def slots_reply(count, start=1, **overrides):
    return yaml.safe_dump(
        {"slots": [ambient_slot(i, **overrides) for i in range(start, start + count)]},
        sort_keys=False)


class FakeSegmentLLM:
    """An LLM that answers 2a and 2b calls differently.

    It dispatches on the system prompt rather than on call order, because the
    2b calls run concurrently and their order is not knowable.
    """

    def __init__(self, chapters=9, slots=19, chapter_replies=None,
                 slot_replies=None):
        self.chapters = chapters
        self.slots = slots
        # Optional lists of canned replies, consumed in order, for testing
        # retries. When exhausted, the good reply is returned.
        self.chapter_replies = list(chapter_replies or [])
        self.slot_replies = list(slot_replies or [])
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
            if system_prompt == segment_schema.SYSTEM_PROMPT_CHAPTERS:
                self.chapter_prompts.append(prompt)
                if self.chapter_replies:
                    return self.chapter_replies.pop(0)
                return chapters_reply(self.chapters)

            assert system_prompt == segment_schema.SYSTEM_PROMPT_SLOTS, (
                "every call must use one of the two exported system prompts")
            self.slot_prompts.append(prompt)
            if self.slot_replies:
                return self.slot_replies.pop(0)
            return slots_reply(self.slots)
