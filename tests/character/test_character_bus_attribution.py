"""WP-15 tests for app/bus_attribution.py: which character a bus message is about.

Frozen test list (playbook §4 WP-15, items 3-6). D-04 / plan §3.4 M1:
`character_for_message(msg)` returns `payload.character` if it is a string,
else the slug of a `char:<slug>` sender, else None. It is pure (no DB lookup)
and stdlib only, because services/message-logger/Dockerfile copies this ONE
file into the logger image, and the logger must not depend on
character_profile.

OB-41 adaptations (test corrections, cited: .claude/prompts/ashiorid_office_build_plan.md
OB-41 "The pilot cast is the 8 office characters"):
- office slugs (engineer, tester, ceo) instead of harry; campaign `ashiorid_office`.
- office seats publish from worker ids `tuber_N` (app/office/roles.py:40-49).
  D-04 and item 6 ("Other senders give None") keep a bare `tuber_3` sender at
  None; resolving seats is the ingest router's job via character_agents
  (plan §3.3). Tracker question P3a-1 records this and the alternative.
"""
import ast
import sys
from pathlib import Path

import pytest

from pending import require

bus_attribution = require("bus_attribution", "app/bus_attribution.py", wp="WP-15")
character_for_message = bus_attribution.character_for_message

SOURCE = Path(__file__).resolve().parents[2] / "app" / "bus_attribution.py"


def _msg(from_, payload):
    return {"id": "m1", "from": from_, "to": "broadcast", "type": "character_say",
            "payload": payload, "timestamp": "2026-09-29T14:00:00+00:00"}


# T15.3
def test_payload_character_comes_first():
    assert character_for_message(_msg("char:tester", {"character": "engineer"})) == "engineer"
    assert character_for_message(_msg("char-live:ashiorid_office", {"character": "ceo"})) == "ceo"
    assert character_for_message(_msg("tuber_3", {"character": "engineer", "text": "hi"})) == "engineer"


# T15.4
def test_char_sender_gives_its_slug():
    assert character_for_message(_msg("char:engineer", {"text": "a thought"})) == "engineer"
    assert character_for_message(_msg("char:office_manager", {})) == "office_manager"
    # payload.character that is not a usable string falls through to the sender
    assert character_for_message(_msg("char:tester", {"character": None})) == "tester"
    assert character_for_message(_msg("char:tester", {"character": 7})) == "tester"
    assert character_for_message(_msg("char:tester", {"character": ""})) == "tester"


# T15.5
def test_char_live_sender_gives_none():
    assert character_for_message(_msg("char-live:ashiorid_office", {"text": "narration"})) is None
    assert character_for_message(_msg("char-live:ashiorid_office", {"character": None})) is None


# T15.6
@pytest.mark.parametrize("from_", [
    "tuber_3",            # an office seat (OB-41; resolved by ingest, not here)
    "office_clock",       # the office clock (app/office/protocol.py CLOCK_SENDER), not a character
    "coder", "character-updater", "char:", "", None, 42,
])
def test_other_senders_give_none(from_):
    assert character_for_message(_msg(from_, {"text": "x"})) is None


# T15.6
@pytest.mark.parametrize("msg", [
    None, "not a dict", 17, [], {},
    {"from": "char:engineer", "payload": "not a dict"},
    {"from": "tuber_3", "payload": ["character", "engineer"]},
    {"from": "tuber_3"},
])
def test_non_dict_or_payloadless_messages_are_handled(msg):
    result = character_for_message(msg)
    if isinstance(msg, dict) and msg.get("from") == "char:engineer":
        assert result == "engineer"  # a bad payload still falls back to the sender
    else:
        assert result is None


# T15.6 (D-04: pure and stdlib-only; the logger image copies this one file)
def test_bus_attribution_imports_only_the_stdlib():
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add((node.module or "").split(".")[0])
    stdlib = set(getattr(sys, "stdlib_module_names", ())) or {"logging", "typing", "re"}
    assert imported <= stdlib, f"non-stdlib imports: {sorted(imported - stdlib)}"
