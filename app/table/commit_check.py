"""Arbiter commit validator for the live agent table (build plan P3.3;
agent_dnd_architecture §5.3).

Cheap, local and deterministic: no LLM. It runs on every character reply
BEFORE the reply is committed to the transcript; a failure becomes a `retake`
whose reason is CheckResult.reason. The invariants live in code, not in
prompt wording: a silent seat never speaks, no seat writes or narrates another
seat's lines, and forbidden lore never reaches the table verbatim.
"""
import logging
import re
from dataclasses import dataclass, field
from typing import Mapping, Optional

try:  # single source of truth for the silent-cast rule
    from campaign.improviser import _NEVER_SPEAKS_RE
except ImportError:  # pragma: no cover - identical fallback
    _NEVER_SPEAKS_RE = re.compile(r"you never speak", re.IGNORECASE)

log = logging.getLogger(__name__)

#: Rule codes, in check order. The first failure wins.
RULES = ("silent_seat", "empty", "meta", "name_label", "speaks_for_other",
         "stage_direction", "narration", "too_long", "repeats", "forbidden_leak")

# repeats (2026-10-08): the W0 MoE slice collapsed into verbatim repetition, the
# GM even copying a player's line. A reply fails when, against any prior committed
# line: same normalised words; or word-set Jaccard >= REPEAT_JACCARD (both >= 4
# words); or it contains a whole prior line of >= 6 words. A recurring tic plus
# new content passes.
REPEAT_JACCARD = 0.8
_WORD_RE = re.compile(r"[a-z0-9']+")

# narration: a double-quoted span that looks like SPEECH (>= 3 words, or ending
# in . , ! ?) plus >= 2 words of prose outside the quotes. A quoted single word
# inside speech ('the "moonwell"') is not speech. Purely lexical narration
# without quotes ("I step back from the latch") is left to the SPEAK prompt:
# no word-list rule tells it apart from speech reliably.
_QUOTED_RE = re.compile(r'["“”]([^"“”]+)["“”]')

_QUOTES = "\"'\u201c\u201d\u2018\u2019"

_META_RE = re.compile(
    r"as an ai|language model|</?think|\booc\b|^\s*\[gm",
    re.IGNORECASE | re.MULTILINE,
)

# *action* / **action**: opening asterisk followed by a non-space, closing
# asterisk preceded by a non-space, so "3 * 4" or "2 * 3 * 4" is not an action.
_ASTERISK_ACTION_RE = re.compile(r"\*(?=[^\s*])[^*]+?(?<=[^\s*])\*")
# (parenthetical) containing at least one letter; ":)" has no opening paren.
_PAREN_RE = re.compile(r"\([^()]*[^\W\d_][^()]*\)")

# Lowercase words ending in s/ed that are not third-person verbs; a name
# followed by one of these is an ordinary spoken sentence ("Leena is right").
_NOT_VERBS = frozenset({
    "is", "was", "has", "does", "as", "us", "his", "its", "this", "thus", "yes",
    "plus", "less", "unless", "always", "perhaps", "besides", "towards", "afterwards",
    "sometimes", "nevertheless", "indeed",
})


@dataclass(frozen=True)
class CommitContext:
    seat: str                                  # the replying seat id, e.g. "tuber_1"
    cast_names: Mapping[str, str]              # seat id -> display name (all seats)
    silent_seats: frozenset = field(default_factory=frozenset)
    forbidden_phrases: tuple = ()
    max_words: int = 60
    max_lines: int = 2
    prior_lines: tuple = ()                    # committed transcript texts (repeats rule)


@dataclass(frozen=True)
class CheckResult:
    ok: bool
    code: Optional[str]                        # one of RULES, None when ok
    reason: str                                # "" when ok, else f"{code}: {detail}"


_OK = CheckResult(True, None, "")


def _fail(code, detail, seat):
    log.debug("commit_check fail seat=%s code=%s", seat, code)
    return CheckResult(False, code, f"{code}: {detail}")


def _strip_quotes(text):
    prev = None
    while prev != text:
        prev = text
        text = text.strip().strip(_QUOTES)
    return text


def _label_re(name):
    """A line starting with optional **, NAME, optional **, then ':' or a spaced dash."""
    return re.compile(
        r"^[ \t]*(?:\*\*)?" + re.escape(name) + r"(?:\*\*)?(?:[ \t]*:|[ \t]+[-\u2013\u2014])",
        re.IGNORECASE | re.MULTILINE,
    )


def _narration_hits(name, text):
    """True when NAME is directly followed by whitespace and a third-person verb."""
    pattern = re.compile(r"(?<!\w)" + re.escape(name) + r"\s+([a-z]+)\b", re.IGNORECASE)
    for match in pattern.finditer(text):
        word = match.group(1)
        if not word.islower():          # the verb must be a lowercase word
            continue
        if word == "said":
            return True
        if word in _NOT_VERBS:
            continue
        if re.fullmatch(r"[a-z]+(?:s|ed)", word):
            return True
    return False


def _normalise(text):
    return " ".join(text.lower().split())


def _words(text):
    return _WORD_RE.findall(str(text or "").lower().replace("’", "'"))


def repeats_prior(text, prior_lines):
    """The prior line `text` repeats, or None (see REPEAT_JACCARD above)."""
    words = _words(text)
    joined = " ".join(words)
    for prior in prior_lines or ():
        pw = _words(prior)
        if not pw:
            continue
        if words == pw:
            return prior
        a, b = set(words), set(pw)
        if len(a) >= 4 and len(b) >= 4 and len(a & b) / len(a | b) >= REPEAT_JACCARD:
            return prior
        if len(pw) >= 6 and f" {' '.join(pw)} " in f" {joined} ":
            return prior
    return None


def check_reply(text, ctx):
    """Validate one character reply against `ctx`; first failing rule wins."""
    text = "" if text is None else str(text)
    seat = ctx.seat
    core = _strip_quotes(text)

    # silent_seat: the empty line is the correct output; nothing else runs.
    if seat in ctx.silent_seats:
        if core:
            return _fail("silent_seat", f"seat {seat} never speaks", seat)
        return _OK

    if not core:
        return _fail("empty", "nothing left after stripping whitespace and quotes", seat)

    if _META_RE.search(text):
        return _fail("meta", "out-of-character or model-meta text", seat)

    own = ctx.cast_names.get(seat)
    if own and _label_re(own).search(text):
        return _fail("name_label", f"line starts with own name label '{own}'", seat)

    for other_seat, name in ctx.cast_names.items():
        if other_seat == seat or not name:
            continue
        if _label_re(name).search(text):
            return _fail("speaks_for_other", f"writes a line for {name}", seat)
        if _narration_hits(name, text):
            return _fail("speaks_for_other", f"narrates {name}", seat)

    if _ASTERISK_ACTION_RE.search(text):
        return _fail("stage_direction", "*asterisk action*; spoken lines only", seat)
    if _PAREN_RE.search(text):
        return _fail("stage_direction", "(parenthetical aside); spoken lines only", seat)

    speech_spans = [m.group(1) for m in _QUOTED_RE.finditer(text)
                    if len(m.group(1).split()) >= 3 or m.group(1).rstrip()[-1:] in ".,!?"]
    if speech_spans:
        outside = _QUOTED_RE.sub(" ", text)
        prose_words = [w for w in outside.split() if any(ch.isalnum() for ch in w)]
        if len(prose_words) >= 2:
            return _fail("narration", "quoted speech wrapped in narration; speak the line only", seat)

    words = len(text.split())
    if words > ctx.max_words:
        return _fail("too_long", f"{words} words > {ctx.max_words}", seat)
    lines = sum(1 for line in text.splitlines() if line.strip())
    if lines > ctx.max_lines:
        return _fail("too_long", f"{lines} lines > {ctx.max_lines}", seat)

    prior = repeats_prior(text, ctx.prior_lines)
    if prior is not None:
        return _fail("repeats", f"repeats an earlier line ('{str(prior)[:60]}'); say something new", seat)

    norm = _normalise(text)
    for phrase in ctx.forbidden_phrases:
        needle = _normalise(phrase)
        if needle and needle in norm:
            return _fail("forbidden_leak", f"contains forbidden phrase '{phrase}'", seat)

    return _OK


def is_silent_prompt(system_prompt):
    """True when a seat's system prompt declares it silent (improviser rule)."""
    if not system_prompt:
        return False
    return bool(_NEVER_SPEAKS_RE.search(system_prompt))


def silent_seats(seat_prompts):
    """Seat ids whose system prompt declares them silent."""
    return frozenset(seat for seat, prompt in seat_prompts.items() if is_silent_prompt(prompt))
