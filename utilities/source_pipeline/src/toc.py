"""Table-of-contents loading for the source pipeline (stage 0).

A source work's chapter list is the one piece of per-source knowledge the
splitter cannot infer, so every source must supply one. It is normalised
into an ordered list of TocEntry regardless of the file format it came in.

Supported formats:

  json              A JSON list of objects with at least ``number`` and
                    ``title``; optional ``book``, ``book_chapter``, ``date``.
  tsv_book_headers  The Wikipedia-style table used for Harry Potter: a
                    ``<Book title> (<date range>)`` header line per book, a
                    column-header line, then tab-separated rows of
                    ``overall  in_book  title  date [end_date]``.
"""
import json
import logging
import pathlib
from dataclasses import dataclass
from typing import List, Optional, Union

log = logging.getLogger(__name__)


class TocError(Exception):
    """Raised when a table of contents is missing, malformed, or inconsistent."""


@dataclass(frozen=True)
class TocEntry:
    """One chapter of a source work, in reading order."""

    number: int                 # overall chapter number, 1-based, unique
    title: str
    book: int = 1
    book_chapter: Optional[int] = None
    date: Optional[str] = None  # in-story date at chapter start, free text


def _parse_tsv_book_headers(text: str) -> List[TocEntry]:
    log.debug("_parse_tsv_book_headers called with %d chars", len(text))
    entries: List[TocEntry] = []
    book = 0
    for line_no, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        fields = [f.strip() for f in raw.split("\t")]
        if len(fields) >= 3 and fields[0].isdigit():
            if book == 0:
                raise TocError(f"line {line_no}: chapter row before any book header")
            entries.append(TocEntry(
                number=int(fields[0]),
                book_chapter=int(fields[1]) if fields[1].isdigit() else None,
                title=fields[2],
                book=book,
                date=fields[3] if len(fields) > 3 and fields[3] else None,
            ))
        elif line.lower().startswith("chapters overall"):
            continue  # column header
        else:
            book += 1
            log.debug("book %d header: %s", book, line)
    log.debug("_parse_tsv_book_headers returning %d entries", len(entries))
    return entries


def _parse_json(text: str) -> List[TocEntry]:
    log.debug("_parse_json called with %d chars", len(text))
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise TocError(f"malformed JSON table of contents: {exc}") from exc
    entries = []
    for i, item in enumerate(data):
        try:
            entries.append(TocEntry(
                number=int(item.get("number", item.get("chapter_number"))),
                title=str(item["title"]),
                book=int(item.get("book", item.get("book_number", 1))),
                book_chapter=item.get("book_chapter"),
                date=item.get("date"),
            ))
        except (KeyError, TypeError, ValueError) as exc:
            raise TocError(f"entry {i}: {exc!r} in {item!r}") from exc
    return entries


PARSERS = {"json": _parse_json, "tsv_book_headers": _parse_tsv_book_headers}


def load_toc(path: Union[str, pathlib.Path], fmt: str) -> List[TocEntry]:
    """Load and validate a table of contents.

    Raises:
        TocError: unknown format, unreadable file, or chapter numbers that
            are not exactly 1..N in order.
    """
    log.debug("load_toc called with path=%s fmt=%s", path, fmt)
    if fmt not in PARSERS:
        raise TocError(f"unknown toc format {fmt!r}; expected one of {sorted(PARSERS)}")
    path = pathlib.Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        log.error("could not read toc %s: %s", path, exc)
        raise TocError(f"could not read toc {path}: {exc}") from exc

    entries = PARSERS[fmt](text)
    if not entries:
        raise TocError(f"no chapters parsed from {path}")
    numbers = [e.number for e in entries]
    if numbers != list(range(1, len(entries) + 1)):
        raise TocError(f"chapter numbers must be 1..{len(entries)} in order; got {numbers[:10]}...")
    log.info("toc loaded path=%s chapters=%d books=%d", path, len(entries), len({e.book for e in entries}))
    return entries
