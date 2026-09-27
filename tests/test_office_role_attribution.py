"""Tests for app/office/role_attribution.py (OB-12). Synthetic records only —
never real session contents."""
import json
from unittest.mock import MagicMock

import pytest

from episode_validator import validate_episode
from office import role_attribution as ra
from office.role_attribution import AttributionError, attribute, classify_command, classify_opening


def ev(kind, **kw):
    base = {"seq": 0, "ts": "2026-09-01T10:00:00Z", "type": kind, "text": None,
            "tool": None, "input": None, "output": None, "error": False}
    base.update(kw)
    return base


def tool(name, inp=None, output="", error=False):
    return ev("tool_call", tool=name, input=inp, output=output, error=error)


def make_record(events=None, **kw):
    rec = {
        "source_tool": "claude_code", "host": "devbox", "project": "demo-project",
        "session_id": "sess-001", "started_at": "2026-09-01T10:00:00Z",
        "ended_at": "2026-09-01T11:00:00Z", "model": "test-model",
        "events": events if events is not None else [
            ev("user_message", text="Add a greeting function to the app"),
            ev("assistant_text", text="Plan: first I'll read the module, then add the function and test it."),
            tool("Read", {"file_path": "src/app.py"}, "def main(): pass"),
            tool("Grep", {"pattern": "greet"}, ""),
            tool("Edit", {"file_path": "src/app.py", "old_string": "pass", "new_string": "return greet()"}),
            tool("Read", {"file_path": "src/util.py"}, ""),
            tool("Bash", {"command": "cd repo && python -m pytest -q"}, "3 passed in 0.1s"),
            tool("Bash", {"command": "rm -rf build/"}, ""),
            tool("Bash", {"command": "git commit -m 'feat: greet'"}, ""),
            tool("Bash", {"command": "pip install -e ."}, ""),
            ev("assistant_text", text="Done: greeting added and tests pass."),
        ],
    }
    rec.update(kw)
    return rec


def roles(ep):
    return [e["role"] for e in ep["events"]]


def test_attribute_default_record_maps_roles_per_rule_table():
    ep = attribute(make_record())
    assert roles(ep) == ["ceo", "tech_lead", "analyst", "analyst", "engineer", "engineer",
                         "tester", "office_manager", "tech_lead", "engineer", "engineer"]
    assert ep["events"][0]["speaker"] == "tuber_0"
    assert ep["show"]["slots"] == ["tuber_0", "tuber_1", "tuber_2", "tuber_3", "tuber_4", "tuber_6"]


def test_attribute_output_passes_episode_validator():
    ep = attribute(make_record())
    result = validate_episode(ep)
    assert result["event_count"] == len(ep["events"])
    assert result["name"] == "office-claude_code-sess-001"


def test_attribute_hermes_tools_mapped_and_rendered():
    rec = make_record(source_tool="hermes", events=[
        ev("user_message", text="Clean up the repo"),
        ev("assistant_text", text="You want the stale branches removed and the tests green."),
        tool("search_files", {"pattern": "*.py"}),
        tool("read_file", {"path": "a.py"}),
        tool("patch", {"path": "a.py", "old_string": "x", "new_string": "y"}),
        tool("write_file", {"path": "b.py", "content": "print(1)"}),
        tool("terminal", {"command": "pytest tests/"}, "1 failed, 2 passed"),
        tool("terminal", {"command": "git branch -d old"}),
        tool("execute_code", {"code": "print(2)"}),
        tool("delegate_task", {"goal": "review"}),
        tool("todo_list", {"todos": []}),
    ])
    ep = attribute(rec)
    assert roles(ep) == ["ceo", "analyst", "analyst", "analyst", "engineer", "engineer",
                         "tester", "office_manager", "engineer", "tech_lead", "tech_lead"]
    tools = [e.get("tool") for e in ep["events"]]
    assert tools[3:8] == ["Read", "Edit", "Write", "Bash", "Bash"]
    assert ep["events"][4]["detail"] == {"file": "a.py", "old": "x", "new": "y"}
    assert ep["events"][4]["source_tool_name"] == "patch"
    validate_episode(ep)


def test_attribute_discovery_resets_each_user_turn():
    rec = make_record(events=[
        ev("user_message", text="one"),
        tool("Edit", {"file_path": "a.py", "old_string": "a", "new_string": "b"}),
        tool("Read", {"file_path": "a.py"}),
        ev("user_message", text="two"),
        tool("Glob", {"pattern": "*.py"}),
    ])
    assert roles(attribute(rec)) == ["ceo", "engineer", "engineer", "ceo", "analyst"]


def test_attribute_only_first_assistant_text_in_turn_is_opening():
    rec = make_record(events=[
        ev("user_message", text="do it"),
        ev("assistant_text", text="So the requirement is a CSV export."),
        ev("assistant_text", text="Working on the plan now."),
    ])
    assert roles(attribute(rec)) == ["ceo", "analyst", "engineer"]


@pytest.mark.parametrize("command,role", [
    ("python -m pytest -q", "tester"),
    ("cd repo && npm test", "tester"),
    ("git push origin main", "tech_lead"),
    ("git merge feature", "tech_lead"),
    ("git gc --prune=now", "office_manager"),
    ("git branch -D stale", "office_manager"),
    ("docker system prune -f", "office_manager"),
    ("make build", "engineer"),
    ("ls -la", "engineer"),
])
def test_classify_command_maps_shell_to_role(command, role):
    assert classify_command(command).value == role


@pytest.mark.parametrize("text,role", [
    ("Here's my plan: three steps.", "tech_lead"),
    ("You want a login page that must support SSO.", "analyst"),
    ("Sure.", "tech_lead"),
])
def test_classify_opening_heuristic(text, role):
    assert classify_opening(text).value == role


def test_attribute_drops_harness_noise_and_empty_turns():
    rec = make_record(events=[
        ev("user_message", text="<system-reminder>ignore</system-reminder>"),
        ev("user_message", text="real ask"),
        ev("assistant_text", text="   "),
        ev("unknown_kind", text="x"),
        tool("Bash", {"command": "echo hi"}, "hi"),
    ])
    ep = attribute(rec)
    assert [e["type"] for e in ep["events"]] == ["user_message", "tool_call"]


def test_attribute_redacts_usernames_before_audit():
    rec = make_record()
    rec["events"][2]["input"] = {"file_path": "C:/Users/frogg/src/app.py"}
    ep = attribute(rec)
    assert "frogg" not in json.dumps(ep)


def test_attribute_audit_failure_raises_without_echoing_secret(monkeypatch):
    monkeypatch.setattr(ra, "redact", lambda text: text)   # disable redaction
    rec = make_record()
    rec["events"][0]["text"] = "use password=hunter2value please"
    with pytest.raises(AttributionError) as info:
        attribute(rec)
    assert "hunter2" not in str(info.value)


@pytest.mark.parametrize("bad", [None, [], {"events": "nope"}])
def test_attribute_rejects_malformed_record(bad):
    with pytest.raises(AttributionError):
        attribute(bad)


def test_attribute_embellish_requires_client():
    with pytest.raises(AttributionError):
        attribute(make_record(), embellish=True)


def test_attribute_embellish_inserts_handoffs_and_marketing_reactions():
    client = MagicMock()
    client.complete.return_value = "Sure! " + json.dumps({
        "handoffs": [
            {"before": 4, "role": "tech_lead", "text": "Engineer, make the change."},
            {"before": 2, "role": "tester", "text": "illegal: tester can't direct analyst"},
            {"before": 99, "role": "ceo", "text": "out of range"},
        ],
        "reactions": [{"after": 6, "text": "Tests green! Ship it to the fans!"}],
    })
    ep = attribute(make_record(), llm_client=client, embellish=True)
    client.complete.assert_called_once()
    handoffs = [e for e in ep["events"] if e.get("embellished") == "handoff"]
    reactions = [e for e in ep["events"] if e.get("embellished") == "reaction"]
    assert len(handoffs) == 1 and handoffs[0]["speaker"] == "tuber_1"
    assert len(reactions) == 1 and reactions[0]["speaker"] == "tuber_5"
    assert "tuber_5" in ep["show"]["slots"]
    idx = ep["events"].index(handoffs[0])
    assert ep["events"][idx + 1]["tool"] == "Edit"
    validate_episode(ep)


@pytest.mark.parametrize("reply", ["not json at all", "[1, 2]"])
def test_attribute_embellish_bad_reply_falls_back(reply):
    client = MagicMock()
    client.complete.return_value = reply
    assert attribute(make_record(), llm_client=client, embellish=True)["events"] == \
        attribute(make_record())["events"]


def test_attribute_embellish_client_error_falls_back():
    client = MagicMock()
    client.complete.side_effect = RuntimeError("down")
    assert len(attribute(make_record(), llm_client=client, embellish=True)["events"]) == 11


def test_main_cli_writes_episode(tmp_path):
    export = tmp_path / "export.jsonl"
    export.write_text("\n".join(json.dumps(r) for r in [
        make_record(session_id="other"), make_record()]) + "\n", encoding="utf-8")
    out = tmp_path / "ep.json"
    assert ra.main([str(export), "--session", "sess-001", "--out", str(out)]) == 0
    ep = json.loads(out.read_text(encoding="utf-8"))
    assert ep["session_id"] == "sess-001"
    assert ra.main([str(export), "--session", "missing", "--out", str(out)]) == 1
