"""WP-11 tests for the generator prompt templates, app/character/prompts/*.md.

Frozen test list (playbook §4 WP-11, item 10). Plan §10: every template writes
character-facing text in the first person, asks for strict JSON, and includes
good and bad node-name examples (the bad ones with their reason). The
templates are written by hand (playbook §1.4, WP-11 "by hand, following plan
§10"), so the structural check runs now; the check that the examples agree
with node_names.check() waits for app/character/node_names.py.

Template format (so the examples can be checked mechanically):
    ## Good node names
    - `trusts-tech-lead-with-the-how`
    ## Bad node names
    - `tech-lead-is-trusted`: why it is bad

OB-41 adaptation (.claude/prompts/ashiorid_office_build_plan.md OB-41): the
examples are office ones (the cast is the 8 office characters, not the trio).
"""
import re
from pathlib import Path

import pytest

from pending import skip_if_pending

PROMPTS_DIR = Path(__file__).resolve().parents[2] / "app" / "character" / "prompts"
BULLET = re.compile(r"^- `([^`]+)`(.*)$")

#: The templates the Phase 3 jobs load (plan §5 / playbook WP-19, WP-20).
REQUIRED = ("summary_day.md", "fragment.md")


def _templates():
    return sorted(PROMPTS_DIR.glob("*.md"))


def _examples(text, heading):
    """[(name, rest_of_line)] for the bullets under `## <heading>`."""
    found, inside = [], False
    for line in text.splitlines():
        if line.startswith("## "):
            inside = line[3:].strip().lower() == heading.lower()
            continue
        match = BULLET.match(line.strip()) if inside else None
        if match:
            found.append((match.group(1), match.group(2).strip(" :-—")))
    return found


def test_required_templates_exist():
    names = {p.name for p in _templates()}
    assert set(REQUIRED) <= names, f"missing: {set(REQUIRED) - names}"


# T11.10
@pytest.mark.parametrize("path", _templates(), ids=lambda p: p.name)
def test_template_says_first_person_and_has_3_good_and_3_bad_examples(path):
    text = path.read_text(encoding="utf-8")
    assert "first person" in text.lower()
    assert "json" in text.lower()
    good = _examples(text, "Good node names")
    bad = _examples(text, "Bad node names")
    assert len(good) >= 3, good
    assert len(bad) >= 3, bad
    assert all(reason for _, reason in bad), "every bad example needs its reason"
    # never teach a character about the loop (plan §2: they never know)
    for _, reason in good:
        assert "loop" not in reason.lower()


# T11.10 (the examples agree with the mechanical check)
@pytest.mark.parametrize("path", _templates(), ids=lambda p: p.name)
def test_template_examples_agree_with_node_names_check(path):
    skip_if_pending("app/character/node_names.py", wp="WP-11")
    from character import node_names

    text = path.read_text(encoding="utf-8")
    for name, _ in _examples(text, "Good node names"):
        assert node_names.check(name) == [], name
    for name, _ in _examples(text, "Bad node names"):
        assert node_names.check(name), f"bad example {name!r} passes node_names.check"
