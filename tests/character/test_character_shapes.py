"""WP-03 tests for app/character/shapes.py (D-26: stdlib validator, no jsonschema).

Frozen test list (playbook §4 WP-03, item 14). A shape is a small dict DSL:

    {"type": "object", "required": [...], "properties": {name: shape}}
    {"type": "str"} {"type": "int"} {"type": "float"} {"type": "bool"}
    {"type": "list", "items": shape}
    {"type": "enum", "values": [...]}

`validate(obj, shape)` returns a list of error strings "<path>: <message>",
where the path starts at "$" (e.g. "$.nodes[1].name"). Each violation gives
exactly one error.
"""
import pytest

from pending import require

shapes = require("character.shapes", "app/character/shapes.py")

NODE = {
    "type": "object",
    "required": ["name", "kind"],
    "properties": {
        "name": {"type": "str"},
        "kind": {"type": "enum", "values": ["fact", "feeling", "want"]},
        "weight": {"type": "float"},
    },
}
SUMMARY = {
    "type": "object",
    "required": ["summary", "event_count", "partial", "nodes"],
    "properties": {
        "summary": {"type": "str"},
        "event_count": {"type": "int"},
        "partial": {"type": "bool"},
        "mood": {"type": "enum", "values": ["calm", "tense"]},
        "nodes": {"type": "list", "items": NODE},
    },
}


def _valid():
    return {
        "summary": "I spent the day chasing a flaky test.",
        "event_count": 12,
        "partial": False,
        "mood": "tense",
        "nodes": [
            {"name": "knows-the-deploy-is-friday", "kind": "fact", "weight": 0.5},
            {"name": "fears-the-audit", "kind": "feeling", "weight": 1},
        ],
    }


def _missing_summary(doc):
    del doc["summary"]


def _event_count_is_str(doc):
    doc["event_count"] = "12"


def _event_count_is_bool(doc):
    doc["event_count"] = True


def _partial_is_int(doc):
    doc["partial"] = 0


def _bad_mood(doc):
    doc["mood"] = "furious"


def _nodes_not_a_list(doc):
    doc["nodes"] = {"name": "x"}


def _node_missing_name(doc):
    del doc["nodes"][1]["name"]


def _node_bad_kind(doc):
    doc["nodes"][0]["kind"] = "rumour"


def _node_weight_is_str(doc):
    doc["nodes"][1]["weight"] = "heavy"


def _node_not_an_object(doc):
    doc["nodes"][0] = "knows-things"


# T03.14
@pytest.mark.parametrize("mutate,path", [
    (_missing_summary, "$.summary"),
    (_event_count_is_str, "$.event_count"),
    (_event_count_is_bool, "$.event_count"),
    (_partial_is_int, "$.partial"),
    (_bad_mood, "$.mood"),
    (_nodes_not_a_list, "$.nodes"),
    (_node_missing_name, "$.nodes[1].name"),
    (_node_bad_kind, "$.nodes[0].kind"),
    (_node_weight_is_str, "$.nodes[1].weight"),
    (_node_not_an_object, "$.nodes[0]"),
], ids=lambda value: getattr(value, "__name__", value))
def test_each_violation_gives_one_error_with_its_path(mutate, path):
    assert shapes.validate(_valid(), SUMMARY) == []
    doc = _valid()
    mutate(doc)
    errors = shapes.validate(doc, SUMMARY)
    assert len(errors) == 1, errors
    assert errors[0].split(": ", 1)[0] == path, errors
    assert errors[0].split(": ", 1)[1].strip(), errors


def test_root_that_is_not_an_object_gives_one_root_error():
    errors = shapes.validate(["not", "an", "object"], SUMMARY)
    assert len(errors) == 1
    assert errors[0].startswith("$: ")
