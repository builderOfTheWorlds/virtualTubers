"""Mechanical check for knowledge-node names (WP-11, plan §10).

Node names are the permanent keys of a character's knowledge. The office
profile loader (WP-10o) validates every authored `backstory_nodes[].name` with
`check()`. The nightly summaries (WP-19) will validate every LLM-proposed node
with it. A bad name is rejected; the caller decides whether to retry or drop it.

The check is strict because a malformed name, or one that teaches a character
about the loop (`remembers-the-last-loop`), would otherwise become permanent
knowledge that ends up in briefs and the knowledge graph.
"""
import logging
import re

log = logging.getLogger(__name__)

NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+){1,7}$")  # plan §10, exactly

VERBS = ("knows", "lives", "wants", "fears", "trusts", "likes", "dislikes",
         "believes", "remembers", "is", "has", "can", "cannot", "owes",
         "suspects", "hopes")  # plan §10, in this order

# Plan §10 names "a banned list" without its contents. These are the office meta
# words of scripts/validate_office_profiles.py split into single words, plus
# plurals and -ing forms. "week", "reset" and "time" are deliberately NOT banned:
# real office nodes use them (`wants-one-green-week`).
BANNED_WORDS = frozenset({"loop", "loops", "looping", "simulation", "simulated",
                          "ai", "npc", "stream", "streams", "streaming",
                          "script", "scripted"})


def check(name, age=None):
    """Return every reason `name` is not a valid node name ([] when valid).

    Never raises. `age` is accepted but unused in v1: age and period sense are
    a prompt rule, not a mechanical one (plan §10, last bullet).
    """
    if not isinstance(name, str) or not name:
        return ["node name must be a non-empty string"]
    if not NAME_RE.match(name):
        reasons = ["must be 2-8 lowercase words (a-z, 0-9) joined by single hyphens"]
        log.debug("node name %r rejected: %s", name, reasons)
        return reasons

    words = name.split("-")
    reasons = []
    if words[0] not in VERBS:
        reasons.append(f"must start with an allowed verb ({', '.join(VERBS)}), not {words[0]!r}")
    seen = set()
    for word in words:
        if word in BANNED_WORDS and word not in seen:
            seen.add(word)
            reasons.append(f"contains banned word {word!r}")
    if reasons:
        log.debug("node name %r rejected: %s", name, reasons)
    return reasons


def is_valid(name, age=None):
    """True when `check(name, age)` finds nothing wrong."""
    return check(name, age) == []
