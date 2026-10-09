"""yaml_repair: planner replies with ': ' inside unquoted prose still parse."""
import pathlib

import yaml

from arc_schema import parse_reply
from segment_schema import parse_slots
from yaml_repair import quote_plain_scalars

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "arc_reply_unquoted_colon.txt"


def test_real_vllm_reply_failed_before_and_parses_now():
    text = FIXTURE.read_text()
    try:
        yaml.safe_load(text)
        raise AssertionError("fixture should be invalid YAML as written")
    except yaml.YAMLError:
        pass
    segments = parse_reply(text)
    assert len(segments) >= 1 and all("id" in s for s in segments)
    assert any("warning: the tear" in s.get("synopsis", "") + s.get("continuity_out", "")
               for s in segments)


def test_quotes_only_prose_with_colon_and_leaves_the_rest():
    src = ("segments:\n"
           "  - id: seg-1\n"
           "    synopsis: They find a note: run.\n"
           "    carry_in: {}\n"
           "    spine_scenes: [a, b]\n"
           "    quoted: \"already: fine\"\n"
           "    plain: no colon here\n")
    out = quote_plain_scalars(src)
    doc = yaml.safe_load(out)["segments"][0]
    assert doc["synopsis"] == "They find a note: run."
    assert doc["carry_in"] == {} and doc["spine_scenes"] == ["a", "b"]
    assert doc["quoted"] == "already: fine" and doc["plain"] == "no colon here"
    assert "  - id: seg-1" in out


def test_escapes_embedded_quotes_and_hash():
    out = quote_plain_scalars('k: she said "go": now #1')
    assert yaml.safe_load(out)["k"] == 'she said "go": now #1'


def test_valid_reply_is_unchanged_by_parser_path():
    src = "segments:\n  - id: s\n    synopsis: plain text\n"
    assert parse_reply(src)[0]["synopsis"] == "plain text"


def test_segment_parser_also_repairs():
    src = "slots:\n  - slot_id: a\n    prompt: Vigil asks: who opened it?\n"
    assert parse_slots(src)[0]["prompt"] == "Vigil asks: who opened it?"
