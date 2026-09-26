"""Chapter splitting for the source pipeline (stage 0).

Splits one flat source text into chapters using a known table of contents.
Headings are located in reading order: chapter N is searched for only after
chapter N-1's heading, which is what stops in-text mentions of a later
chapter's title (a newspaper headline, a shop sign, shouted dialogue) from
being taken as that chapter's start. Every chapter ends exactly where the
next one begins; nothing ever runs to end-of-file except the final chapter.

A heading match must look like a heading:
  * followed by body text, i.e. the next token starts ``Xy`` (capital then
    lowercase) or is ``I``/``A`` — so ``THE CHAMBER OF SECRETS HAS BEEN
    OPENED`` (all-caps continues) is rejected;
  * preferably preceded by a sentence end, a bare chapter number, or the
    start of the text. A match that fails only this check is accepted with
    a warning (source noise such as ``lifetime .ayue NINETEEN YEARS LATER``).

Titles that the source spells differently from the table of contents are
supplied per source as ``heading_aliases`` — the splitter never guesses.
"""
import hashlib
import logging
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from toc import TocEntry

log = logging.getLogger(__name__)

DEFAULT_MAX_WORDS = 25000
DEFAULT_MIN_WORDS = 500

_STRIP_CHARS = re.compile(r"['\u2018\u2019`\".,:;!?\-\u2013\u2014]")
_SPACES = re.compile(r"\s+")
_PRECEDED_OK = re.compile(r"(?:^|[.!?]|\b\d{1,3})\s*$")
_FOLLOWED_OK = re.compile(r"\s+(?:[A-Z][a-z]|[IA]\s+[a-z]|[IA]\b)")
_TRAILING_CHAPTER_NUMBER = re.compile(r"\s+\d{1,3}\s*$")
# Heading-adjacent OCR debris after the final sentence end: a stray chapter
# number ("gang .3"), a dropped initial ("?d", ".J"), or a roman numeral ("XX").
_TRAILING_DEBRIS = re.compile(r"(?<=[.!?])\s*(\d{1,3}|[A-Za-z]\d{0,2}|[IVXL]{1,4})\s*$")


class SplitError(Exception):
    """Raised when the source cannot be split into sane chapters."""


@dataclass
class Chapter:
    entry: TocEntry
    heading: str          # the heading text as it appears in the source
    heading_start: int    # offset of the heading in the source text
    body_start: int       # offset of the first body character
    body_end: int         # exclusive
    text: str
    warnings: List[str] = field(default_factory=list)

    @property
    def words(self) -> int:
        return len(self.text.split())

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()


def normalize_heading(title: str) -> str:
    """Upper-case a title and drop punctuation the source text loses."""
    return _SPACES.sub(" ", _STRIP_CHARS.sub("", title)).strip().upper()


def heading_candidates(entry: TocEntry, aliases: Dict[int, Sequence[str]]) -> List[str]:
    """Heading strings to try for one chapter, most specific first.

    The title is normalised; aliases are LITERAL source text (only whitespace
    is collapsed) because they exist precisely for headings the source
    mangled in ways normalisation can't reproduce, e.g. ``O .W .L .S``.
    """
    keys = [normalize_heading(entry.title)]
    for alias in aliases.get(entry.number, []):
        key = _SPACES.sub(" ", alias).strip()
        if key and key not in keys:
            keys.append(key)
    # longest first, so "A PECK OF OWLS" can never be shadowed by "OWLS"
    return sorted(keys, key=len, reverse=True)


def _find_heading(text: str, keys: Sequence[str], cursor: int, limit: int):
    """Return (start, key, tier) of the best heading match in [cursor, limit).

    Tiers, tried in order — the earliest match of the best available tier wins:
      1  preceded by a sentence end/number/start AND followed by body text
      2  preceded OK, but followed by more capitals (body opens with a
         shout, sign, or headline: "THE KEEPER OF THE KEYS BOOM .")
      3  followed OK, but not preceded by a sentence end (source noise)
    A lower tier is only used when no higher-tier match exists anywhere in
    the window; the word-count bounds in split_text catch a wrong fallback.
    """
    best: Dict[int, Optional[Tuple[int, str, int]]] = {1: None, 2: None, 3: None}
    for key in keys:
        pattern = re.compile(r"(?<![A-Za-z])" + re.escape(key) + r"(?![A-Za-z])")
        for m in pattern.finditer(text, cursor, limit):
            preceded = bool(_PRECEDED_OK.search(text[max(0, m.start() - 12):m.start()]))
            followed = bool(_FOLLOWED_OK.match(text, m.end()))
            tier = 1 if preceded and followed else 2 if preceded else 3 if followed else None
            if tier is None:
                continue
            current = best[tier]
            if current is None or m.start() < current[0]:
                best[tier] = (m.start(), key, tier)
    for tier in (1, 2, 3):
        if best[tier] is not None:
            return best[tier]
    return None


def split_text(
    text: str,
    toc: Sequence[TocEntry],
    heading_aliases: Optional[Dict[int, Sequence[str]]] = None,
    max_words: int = DEFAULT_MAX_WORDS,
    min_words: int = DEFAULT_MIN_WORDS,
) -> List[Chapter]:
    """Split ``text`` into one Chapter per toc entry.

    Raises:
        SplitError: a heading was not found, or a chapter's size is outside
            [min_words, max_words] (the symptom of a missed boundary).
    """
    log.debug("split_text called with %d chars, %d toc entries", len(text), len(toc))
    aliases = heading_aliases or {}
    starts = []  # (entry, heading_start, key, tier)
    cursor = 0
    missing = []
    for entry in toc:
        found = _find_heading(text, heading_candidates(entry, aliases), cursor, len(text))
        if found is None:
            log.debug("chapter %d %r: no heading after offset %d", entry.number, entry.title, cursor)
            missing.append(entry)
            continue
        start, key, tier = found
        log.debug("chapter %d %r: heading %r at %d tier=%d", entry.number, entry.title, key, start, tier)
        starts.append((entry, start, key, tier))
        cursor = start + len(key)

    if missing:
        names = ", ".join(f"{e.number} {e.title!r}" for e in missing)
        log.error("split_text: %d headings not found: %s", len(missing), names)
        raise SplitError(f"{len(missing)} chapter headings not found (add heading_aliases): {names}")

    tier_notes = {2: "body opens with capitals (verify boundary)",
                  3: "heading not preceded by a sentence end (verify boundary)"}
    chapters: List[Chapter] = []
    for i, (entry, start, key, tier) in enumerate(starts):
        heading_end = start + len(key)
        end = starts[i + 1][1] if i + 1 < len(starts) else len(text)
        body = text[heading_end:end]
        body = _TRAILING_CHAPTER_NUMBER.sub("", body).strip()
        debris = _TRAILING_DEBRIS.search(body)
        if debris:
            log.debug("chapter %d: stripped trailing debris %r", entry.number, debris.group(1))
            body = body[:debris.start()].rstrip()
        body_start = text.index(body[:50], heading_end) if body else heading_end
        ch = Chapter(entry=entry, heading=text[start:heading_end], heading_start=start,
                     body_start=body_start, body_end=body_start + len(body), text=body)
        if debris:
            ch.warnings.append(f"stripped trailing debris {debris.group(1)!r}")
        if tier in tier_notes:
            ch.warnings.append(f"{tier_notes[tier]}: {text[max(0, start - 30):heading_end + 30]!r}")
        chapters.append(ch)

    if starts and starts[0][1] > 0:
        log.warning("split_text: %d chars of front matter before chapter 1 ignored", starts[0][1])

    bad = [c for c in chapters if not (min_words <= c.words <= max_words)]
    if bad:
        detail = "; ".join(f"{c.entry.number} {c.entry.title!r}={c.words} words" for c in bad)
        log.error("split_text: chapters outside [%d, %d] words: %s", min_words, max_words, detail)
        raise SplitError(f"chapters outside [{min_words}, {max_words}] words (missed boundary?): {detail}")

    log.debug("split_text returning %d chapters", len(chapters))
    return chapters


_UNSAFE_FILENAME = re.compile(r"[<>:\"/\\|?*'\u2018\u2019\-]")


def chapter_filename(ch: Chapter) -> str:
    """``<book>_<NNN>_<Title>.txt`` — the naming the downstream phases expect."""
    safe = _UNSAFE_FILENAME.sub("_", ch.entry.title).strip()
    return f"{ch.entry.book}_{ch.entry.number:03d}_{safe}.txt"
