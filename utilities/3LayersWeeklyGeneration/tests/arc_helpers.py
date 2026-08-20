"""Shared fixtures and fakes for the Layer 1 test modules.

Layer 1 is split across two modules — `arc_schema` (pure: parse, validate,
build the prompt) and `plan_arc` (the orchestrator that drives them) — so the
campaign fixture and the fake LLM live here rather than being duplicated into
both test files and drifting apart.
"""
import pytest
import yaml




# --------------------------------------------------------------------------
# Fixtures — a small pack and config, and an LLM that never touches a network.
# --------------------------------------------------------------------------

CARRY_KEYS = ["helen-wounded", "moonwell-tainted"]


class FakeScene:
    def __init__(self, scene_id, title=None, enter_narration=None,
                 ambient=False, prompt=None):
        self.id = scene_id
        self.title = title
        self.enter_narration = enter_narration
        self.ambient = ambient
        self.prompt = prompt
        self.beats = []
        self.branches = []
        self.default_next = None
        self.lore = []


class FakeCastMember:
    def __init__(self, member_id, name, role, archetype=None):
        self.id = member_id
        self.name = name
        self.role = role
        self.archetype = archetype
        self.system_prompt = None


class FakePack:
    """Just the attributes plan_arc is allowed to read."""

    def __init__(self):
        self.name = "ashiorid"
        self.title = "The Ashiorid Loop"
        self.genre = "weird-west folk horror"
        self.start_scene = "arrival"
        self.scenes = {
            "arrival": FakeScene("arrival", "Arrival",
                                 "The wagon crests the ridge at dusk."),
            "moonwell": FakeScene("moonwell", "The Moonwell",
                                  "Water the colour of a bruise."),
            "portal-encounter": FakeScene("portal-encounter", "The Portal",
                                          "It opens the way it always does."),
            "camp-chatter": FakeScene("camp-chatter", ambient=True,
                                      prompt="idle talk around the fire"),
            "road-song": FakeScene("road-song", ambient=True,
                                   prompt="someone hums to pass the miles"),
        }
        self.lore = {
            "the-loop": "Time in Ashiorid folds back on itself every seventh day.",
            "helen": "Helen carries a wound that never quite closes.",
        }
        self.ambient_pool = ["camp-chatter", "road-song"]

        self.cast = {
            "gm": FakeCastMember("gm", "The Narrator", "gm"),
            "helen": FakeCastMember("helen", "Helen Ward", "player"),
            "buffalo": FakeCastMember("buffalo", "Buffalo Pike", "player"),
        }
        self.gm_id = "gm"
        self.player_ids = ["helen", "buffalo"]

    def ambient_scene_ids(self):
        return list(self.ambient_pool)

    def scene(self, scene_id):
        return self.scenes[scene_id]

    def member(self, member_id):
        return self.cast[member_id]


@pytest.fixture
def pack():
    return FakePack()


@pytest.fixture
def config():
    return {
        "arc": {
            "hours_total": 168,
            "segment_hours": 6,
            "batch_size": 6,
            "max_attempts": 2,
        },
        "state": {
            "flags": ["helen-wounded", "moonwell-tainted", "buffalo-lost-axe"],
            "moods": ["tense", "weary", "hopeful", "giddy"],
            "carry_keys": list(CARRY_KEYS),
        },
    }


@pytest.fixture
def vocab(config, pack):
    import vocabulary
    return vocabulary.Vocabulary.from_config_and_pack(config, pack)


def segment(order, **overrides):
    """A valid segment mapping for `order` (0-based)."""
    seg = {
        "id": f"seg-{order + 1:03d}",
        "order": order,
        "loop": 0,
        "hours": 6,
        "spine_scenes": [],
        "ambient_focus": ["camp-chatter"],
        "synopsis": f"The company travels on. Hour block {order}.",
        "continuity_in": "They are on the road.",
        "continuity_out": "They make camp by the creek.",
        "carry_in": {},
        "carry_out": {"helen-wounded": True},
        "event_windows": [],
        "fork": None,
    }
    seg.update(overrides)
    return seg


def reply_for(orders, **overrides):
    """A well-formed model reply planning exactly `orders`."""
    return yaml.safe_dump({"segments": [segment(o, **overrides) for o in orders]},
                          sort_keys=False)


class FakeLLM:
    """Records every call and replays scripted replies.

    `replies` may hold strings (returned) or exceptions (raised) — a real
    ollama that has fallen over raises, and Layer 1 must survive that exactly
    the way it survives an unparseable answer.
    """

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []          # (system_prompt, messages)
        self.on_call = None      # optional hook, for observing on-disk state

    def complete(self, system_prompt, messages):
        # Assert the call shape here rather than letting it surface later as
        # an obscure AttributeError from `prompts`. `OllamaClient.complete`
        # concatenates `messages` onto the system message and posts it to the
        # ollama chat API, which requires role/content mappings — a bare
        # string reaches the server as a list of single characters.
        assert isinstance(messages, list), (
            f"messages must be a list of role/content mappings, "
            f"got {type(messages).__name__}")
        for message in messages:
            assert isinstance(message, dict) and "content" in message, (
                f"each message must be a mapping with a 'content' key, "
                f"got {message!r}")
        self.calls.append((system_prompt, messages))
        if self.on_call is not None:
            self.on_call(len(self.calls))
        if not self.replies:
            raise AssertionError("FakeLLM ran out of scripted replies")
        item = self.replies.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    @property
    def prompts(self):
        """Every prompt sent, system + user text flattened, for substring checks."""
        out = []
        for system_prompt, messages in self.calls:
            text = system_prompt or ""
            for message in messages:
                text += "\n" + str(message.get("content", ""))
            out.append(text)
        return out


def perfect_llm(total, batch_size):
    """An LLM that plans every batch correctly on the first attempt."""
    replies = []
    for start in range(0, total, batch_size):
        replies.append(reply_for(list(range(start, min(start + batch_size, total)))))
    return FakeLLM(replies)
