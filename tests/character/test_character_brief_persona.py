"""WP-22 (OB-41 addition) tests: character.brief as the drop-in for office.brief_stub.

OB-41 (.claude/prompts/ashiorid_office_build_plan.md): "When brief.py lands,
swap it in for app/office/brief_stub.py (E6)". docs/brief_stub.md: keep
`build_persona_prompt` stable "so that swap is a one-import change in
app/agent_handlers/office.py". So character.brief exposes the same entry
point with the same arguments and errors:

    build_persona_prompt(role, directive=None, pack_dir=None, max_backstory_chars=None) -> str
    BriefError, NO_DIRECTIVE_TEXT, resolve_pack_dir(pack_dir=None), PACK_DIR_ENV

Like the stub it has no length cap unless asked (keyword-only `max_chars=None`;
the office briefs run 5-8k characters, over brief.max_chars 6000, see the
tracker question). With no memory source it is the stub's content (cast system_prompt, believed
backstory, today's directive) in the v4 layout, cleaned of loop words. With a
source registered by `set_memory_source(fn)` (fn(slug) -> BriefParts | None,
e.g. a BriefCache over load_parts) it adds the week's knowledge and the
feelings. A failing source never breaks a live office agent: it falls back to
the pack-only brief. The swap itself (the import in app/agent_handlers/office.py)
is a later hand step, not part of WP-22.
"""
import logging
import re
import shutil

import pytest
import yaml

from fakes_e2e import FORBIDDEN_BRIEF_RE, OFFICE_PACK
from pending import require

brief = require("character.brief", "app/character/brief.py", wp="WP-22")
from office import brief_stub  # noqa: E402
from office.roles import OfficeRole  # noqa: E402


@pytest.fixture(autouse=True)
def no_memory_source():
    brief.set_memory_source(None)
    yield
    brief.set_memory_source(None)


def _profile(slug):
    with open(OFFICE_PACK / "profiles" / f"{slug}.yaml", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def _sentences(text):
    """Sentences of `text` (headings dropped, whitespace collapsed), split on
    brief.SENTENCE_SPLIT_RE's boundary: whitespace after . ! or ?"""
    flat = " ".join(" ".join(line for line in text.splitlines() if not line.startswith("## ")).split())
    return [s for s in re.split(r"(?<=[.!?])\s+", flat) if s]


def test_persona_prompt_carries_the_stub_content_without_loop_words():
    prompt = brief.build_persona_prompt("engineer", "Fix the empty decline reasons.",
                                        pack_dir=OFFICE_PACK)
    assert "You are Theo Palliser, the Engineer at Ashiorid" in prompt
    assert "Okay, so. Two years." in prompt
    assert "## Today's directive\nFix the empty decline reasons." in prompt
    assert prompt.index("## Today's directive") < prompt.index("## How you behave")
    assert FORBIDDEN_BRIEF_RE.search(prompt) is None
    truth = _profile("engineer")["backstory"]["truth"].strip()
    assert truth[:60] not in prompt
    stub = brief_stub.build_persona_prompt("engineer", "Fix the empty decline reasons.",
                                           pack_dir=OFFICE_PACK)
    flat_prompt = " ".join(prompt.split())
    for sentence in _sentences(stub):
        if not FORBIDDEN_BRIEF_RE.search(sentence):
            assert sentence in flat_prompt, sentence


@pytest.mark.parametrize("role", ["engineer", "tuber_3", OfficeRole.ENGINEER])
def test_role_may_be_a_role_a_value_or_a_seat(role):
    assert brief.build_persona_prompt(role, pack_dir=OFFICE_PACK) == \
        brief.build_persona_prompt("engineer", pack_dir=OFFICE_PACK)


def test_unknown_role_raises_value_error():
    with pytest.raises(ValueError):
        brief.build_persona_prompt("intern", pack_dir=OFFICE_PACK)


def test_party_member_brief_keeps_his_silence():
    prompt = brief.build_persona_prompt("party_member", pack_dir=OFFICE_PACK)
    assert "You never speak. You only observe." in prompt
    assert "Never confirm or deny who you work for." in prompt
    assert FORBIDDEN_BRIEF_RE.search(prompt) is None


def test_directive_rendering_matches_the_stub():
    assert brief.NO_DIRECTIVE_TEXT == brief_stub.NO_DIRECTIVE_TEXT
    assert f"## Today's directive\n{brief.NO_DIRECTIVE_TEXT}" in \
        brief.build_persona_prompt("tester", pack_dir=OFFICE_PACK)
    prompt = brief.build_persona_prompt(
        "analyst", {"text": "Ship the country-mismatch rule.", "title": "Country rule", "issue": 12},
        pack_dir=OFFICE_PACK)
    assert "## Today's directive\nCountry rule (issue #12)\nShip the country-mismatch rule." in prompt


def test_missing_cast_or_system_prompt_raises_brief_error_and_missing_profile_is_fine(tmp_path, monkeypatch):
    pack = tmp_path / "pack"
    shutil.copytree(OFFICE_PACK, pack)
    (pack / "profiles" / "tester.yaml").unlink()
    no_backstory = brief.build_persona_prompt("tester", pack_dir=pack)
    assert "You are Owen Hask" in no_backstory or "Owen Hask" in no_backstory
    assert "## What you remember of your life so far" not in no_backstory
    (pack / "cast" / "tester.yaml").unlink()
    with pytest.raises(brief.BriefError):
        brief.build_persona_prompt("tester", pack_dir=pack)
    cast = pack / "cast" / "analyst.yaml"
    doc = yaml.safe_load(cast.read_text(encoding="utf-8"))
    doc["system_prompt"] = "  "
    cast.write_text(yaml.safe_dump(doc), encoding="utf-8")
    with pytest.raises(brief.BriefError):
        brief.build_persona_prompt("analyst", pack_dir=pack)
    monkeypatch.setenv(brief.PACK_DIR_ENV, str(pack))
    assert brief.resolve_pack_dir() == pack
    with pytest.raises(brief.BriefError):
        brief.build_persona_prompt("tester")               # env pack has no tester cast


def test_max_backstory_chars_truncates_on_a_word_boundary():
    believed = _profile("engineer")["backstory"]["believed"]
    prompt = brief.build_persona_prompt("engineer", pack_dir=OFFICE_PACK, max_backstory_chars=200)
    body = prompt.split("## What you remember of your life so far\n", 1)[1].split("\n\n## ", 1)[0]
    assert body.endswith(" ...")
    assert len(body) <= 204
    assert body[:-4] == believed[:len(body) - 4] or believed.startswith(body[:-4].strip())


def test_memory_source_adds_knowledge_and_feelings_and_a_failing_source_falls_back(caplog):
    base = brief.parts_from_pack("engineer", OFFICE_PACK)
    memory = brief.BriefParts(**{**base.__dict__,
                                 "learned": ("I know the deploy bar froze at noon.",),
                                 "feelings": ("The Glass Box went dark and my hands shook.",)})
    calls = []

    def source(slug):
        calls.append(slug)
        return memory if slug == "engineer" else None

    brief.set_memory_source(source)
    prompt = brief.build_persona_prompt("tuber_3", "Fix it.", pack_dir=OFFICE_PACK)
    assert calls == ["engineer"]
    assert "I know the deploy bar froze at noon." in prompt
    assert "The Glass Box went dark and my hands shook." in prompt
    assert "## Today's directive\nFix it." in prompt
    plain_tester = brief.build_persona_prompt("tester", pack_dir=OFFICE_PACK)   # source says None
    brief.set_memory_source(None)
    assert plain_tester == brief.build_persona_prompt("tester", pack_dir=OFFICE_PACK)

    def broken(slug):
        raise RuntimeError("character_profile is down")

    brief.set_memory_source(broken)
    caplog.set_level(logging.WARNING)
    fallback = brief.build_persona_prompt("engineer", "Fix it.", pack_dir=OFFICE_PACK)
    brief.set_memory_source(None)
    assert fallback == brief.build_persona_prompt("engineer", "Fix it.", pack_dir=OFFICE_PACK)
    assert any(r.levelno == logging.WARNING and r.name.startswith("character") for r in caplog.records)
