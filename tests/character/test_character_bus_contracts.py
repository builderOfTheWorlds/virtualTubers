"""WP-15 tests for app/character/bus_contracts.py: the v4 bus message builders.

Frozen test list (playbook §4 WP-15, items 1-2; items 3-6 are about
app/bus_attribution.py and live in test_character_bus_attribution.py).
Plan §3.2: three new message types on the one topic `vtuber.messages`, built
with `build_message` (app/message_bus.py:33), so they carry `id`,
`timestamp`, `correlation_id` like every other message:

    character_say      from "char:<slug>"      {campaign, scene_id, character, addressees, present, text}
    scene_event        from "char-live:<camp>" {campaign, scene_id, kind, character, present, text}
    character_refresh  from "character-updater" {campaign, week, characters, reason}

OB-41 adaptation (test correction, cited: .claude/prompts/ashiorid_office_build_plan.md
OB-41 "The pilot cast is the 8 office characters"): the examples use campaign
`ashiorid_office` and office slugs (engineer, tester, ceo) instead of hp/harry.
"""
import uuid
from datetime import datetime

import pytest

from pending import require

bus_contracts = require("character.bus_contracts", "app/character/bus_contracts.py", wp="WP-15")

CAMPAIGN = "ashiorid_office"
PRESENT = ["engineer", "tester", "ceo", "office_clock"]


def _is_envelope(msg, type_, from_):
    assert msg["type"] == type_
    assert msg["from"] == from_
    assert msg["to"] == "broadcast"
    uuid.UUID(msg["id"])
    assert datetime.fromisoformat(msg["timestamp"]).utcoffset() is not None
    assert msg["correlation_id"] == msg["id"]  # a new chain (build_message default)
    assert "causation_id" in msg


# T15.1
def test_builders_use_build_message_and_the_plan_payload_shapes(monkeypatch):
    say = bus_contracts.character_say(CAMPAIGN, "day-2-standup", "engineer",
                                      "The build is green again.", present=PRESENT,
                                      addressees=["tester"])
    _is_envelope(say, "character_say", "char:engineer")
    assert say["payload"] == {"campaign": CAMPAIGN, "scene_id": "day-2-standup",
                              "character": "engineer", "addressees": ["tester"],
                              "present": PRESENT, "text": "The build is green again."}

    event = bus_contracts.scene_event(CAMPAIGN, "day-2-standup", "narration",
                                      "The Glass Box goes dark.", present=PRESENT)
    _is_envelope(event, "scene_event", "char-live:ashiorid_office")
    assert event["payload"] == {"campaign": CAMPAIGN, "scene_id": "day-2-standup",
                                "kind": "narration", "character": None,
                                "present": PRESENT, "text": "The Glass Box goes dark."}

    refresh = bus_contracts.character_refresh(CAMPAIGN, 2)
    _is_envelope(refresh, "character_refresh", "character-updater")
    assert refresh["payload"] == {"campaign": CAMPAIGN, "week": 2, "characters": ["*"],
                                  "reason": "weekly_reset"}
    targeted = bus_contracts.character_refresh(CAMPAIGN, 3, characters=["tester"], reason="revert")
    assert targeted["payload"]["characters"] == ["tester"]
    assert targeted["payload"]["reason"] == "revert"

    # the agent-id helpers (D-04, plan §3.2)
    assert bus_contracts.character_agent_id("engineer") == "char:engineer"
    assert bus_contracts.live_agent_id(CAMPAIGN) == "char-live:ashiorid_office"
    assert set(bus_contracts.SCENE_KINDS) == {"narration", "action", "scene_start",
                                              "scene_end", "story_end"}
    assert set(bus_contracts.REFRESH_REASONS) == {"weekly_reset", "revert", "testctl"}

    # every builder goes through message_bus.build_message (by its module-level name)
    calls = []
    real = bus_contracts.build_message

    def spy(*args, **kwargs):
        calls.append((args, kwargs))
        return real(*args, **kwargs)

    monkeypatch.setattr(bus_contracts, "build_message", spy)
    bus_contracts.character_say(CAMPAIGN, "s", "tester", "Suite passes.", present=["tester"])
    bus_contracts.scene_event(CAMPAIGN, "s", "scene_start", "Standup begins.", present=["tester"])
    bus_contracts.character_refresh(CAMPAIGN, 2, reason="testctl")
    assert [c[0][2] for c in calls] == ["character_say", "scene_event", "character_refresh"]
    assert [c[0][1] for c in calls] == ["broadcast"] * 3


# T15.1 (correlation passthrough, so a reply stays in its chain: docs/message_bus.md)
def test_builders_pass_correlation_ids_through():
    parent = str(uuid.uuid4())
    say = bus_contracts.character_say(CAMPAIGN, "s", "ceo", "Ship it.", present=["ceo"],
                                      correlation_id=parent, causation_id=parent)
    assert say["correlation_id"] == parent and say["causation_id"] == parent


# T15.2
@pytest.mark.parametrize("build", [
    lambda: bus_contracts.scene_event(CAMPAIGN, "s", "monologue", "Text.", present=PRESENT),
    lambda: bus_contracts.scene_event(CAMPAIGN, "s", "", "Text.", present=PRESENT),
    lambda: bus_contracts.character_say(CAMPAIGN, "s", "engineer", "", present=PRESENT),
    lambda: bus_contracts.character_say(CAMPAIGN, "s", "engineer", "   ", present=PRESENT),
    lambda: bus_contracts.scene_event(CAMPAIGN, "s", "narration", "", present=PRESENT),
    lambda: bus_contracts.character_refresh(CAMPAIGN, 2, reason="because"),
], ids=["unknown-kind", "empty-kind", "say-empty-text", "say-blank-text",
        "scene-empty-text", "refresh-unknown-reason"])
def test_builder_rejects_unknown_kind_or_empty_text(build):
    with pytest.raises(bus_contracts.ContractError):
        build()
    assert issubclass(bus_contracts.ContractError, ValueError)
