"""Tests for stage 0 of the source pipeline: toc loading and chapter splitting."""
import json
import pathlib

import pytest

from source_config import load_source_config
from splitter import SplitError, chapter_filename, normalize_heading, split_text
from toc import TocEntry, TocError, load_toc

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
HP_CONFIG = REPO_ROOT / "utilities" / "source_pipeline" / "sources" / "harry_potter.yaml"


def body(word, n=20):
    return " ".join([word] * n) + " ."


def toc(*titles):
    return [TocEntry(number=i, title=t) for i, t in enumerate(titles, start=1)]


def split(text, entries, aliases=None):
    return split_text(text, entries, aliases, max_words=1000, min_words=5)


# -- toc ----------------------------------------------------------------------

def test_load_toc_tsv_book_headers_assigns_books_and_dates(tmp_path):
    p = tmp_path / "toc.txt"
    p.write_text(
        "Book One (1990-1991)\n"
        "Chapters overall \tChapters in book \tChapter title \tDate at the start of chapter\n"
        "1 \t1 \tThe Start \t1 May 1990\n"
        "2 \t2 \tThe Middle \t2 May 1990\n"
        "Book Two (1992)\n"
        "Chapters overall \tChapters in book \tChapter title \tDate\n"
        "3 \t1 \tThe End \t3 May 1992 \t4 May 1992\n",
        encoding="utf-8",
    )
    entries = load_toc(p, "tsv_book_headers")
    assert [(e.number, e.book, e.book_chapter, e.title) for e in entries] == [
        (1, 1, 1, "The Start"), (2, 1, 2, "The Middle"), (3, 2, 1, "The End")]
    assert entries[0].date == "1 May 1990"


def test_load_toc_json_accepts_legacy_keys(tmp_path):
    p = tmp_path / "toc.json"
    p.write_text(json.dumps([{"chapter_number": 1, "title": "A"}, {"number": 2, "title": "B", "book": 2}]))
    assert [(e.number, e.book) for e in load_toc(p, "json")] == [(1, 1), (2, 2)]


@pytest.mark.parametrize("content,fmt,match", [
    ('[{"number": 1, "title": "A"}, {"number": 3, "title": "B"}]', "json", "1..2"),
    ("not json", "json", "malformed"),
    ("1 \t1 \tOrphan row\n", "tsv_book_headers", "before any book header"),
    ("[]", "yaml", "unknown toc format"),
])
def test_load_toc_rejects_bad_input(tmp_path, content, fmt, match):
    p = tmp_path / "toc"
    p.write_text(content, encoding="utf-8")
    with pytest.raises(TocError, match=match):
        load_toc(p, fmt)


# -- splitter -----------------------------------------------------------------

def test_normalize_heading_drops_punctuation_the_source_loses():
    assert normalize_heading("Cat, Rat, and Dog") == "CAT RAT AND DOG"
    assert normalize_heading("Hallowe'en") == "HALLOWEEN"
    assert normalize_heading("Nine and Three-Quarters") == "NINE AND THREEQUARTERS"


def test_split_ends_each_chapter_at_next_heading_not_end_of_file():
    text = f"ALPHA One {body('a')} BETA Two {body('b')} GAMMA Three {body('c')}"
    chapters = split(text, toc("Alpha", "Beta", "Gamma"))
    assert [c.text.split()[0] for c in chapters] == ["One", "Two", "Three"]
    assert "Two" not in chapters[0].text and "Three" not in chapters[1].text


def test_split_skips_mention_of_later_title_before_its_real_heading():
    # "THE VAULT" appears as a sign before chapter 1's own heading; the
    # in-order search must not let it start chapter 2.
    text = (f"Sign read THE VAULT KEEP OUT . THE START Hello {body('a')}"
            f" THE VAULT Inside {body('b')}")
    chapters = split(text, toc("The Start", "The Vault"))
    assert chapters[0].text.startswith("Hello")
    assert chapters[1].text.startswith("Inside")


def test_split_prefers_real_heading_over_earlier_in_text_mention():
    text = (f"FIRST Once {body('a')} It said SECOND COMING SOON on the wall ."
            f" SECOND Then {body('b')}")
    chapters = split(text, toc("First", "Second"))
    assert chapters[1].text.startswith("Then")
    assert "COMING SOON" in chapters[0].text


def test_split_uses_literal_alias_for_misspelled_heading():
    text = f"ONE Start {body('a')} THE VANASHIG GLASS Nearly {body('b')}"
    chapters = split(text, toc("One", "The Vanishing Glass"), {2: ["THE VANASHIG GLASS"]})
    assert chapters[1].heading == "THE VANASHIG GLASS"
    assert chapters[1].text.startswith("Nearly")


def test_split_falls_back_to_heading_whose_body_opens_with_capitals():
    text = f"ONE Start {body('a')} THE KEYS BOOM .They knocked {body('b')}"
    chapters = split(text, toc("One", "The Keys"))
    assert chapters[1].text.startswith("BOOM")
    assert any("capitals" in w for w in chapters[1].warnings)


def test_split_raises_listing_every_missing_heading():
    text = f"ONE Start {body('a')}"
    with pytest.raises(SplitError, match=r"2 chapter headings not found.*2 'Two'.*3 'Three'"):
        split(text, toc("One", "Two", "Three"))


def test_split_raises_when_a_chapter_is_implausibly_long():
    text = f"ONE Start {body('a', 2000)} TWO Next {body('b')}"
    with pytest.raises(SplitError, match="outside"):
        split(text, toc("One", "Two"))


def test_split_strips_trailing_chapter_number_debris():
    text = f"ONE Start {body('a')}3 TWO Next {body('b')}"
    chapters = split(text, toc("One", "Two"))
    assert chapters[0].text.endswith(".")
    assert any("debris" in w for w in chapters[0].warnings)


def test_body_offsets_point_back_into_source():
    text = f"ONE Start {body('a')} TWO Next {body('b')}"
    for c in split(text, toc("One", "Two")):
        assert text[c.body_start:c.body_end] == c.text


def test_chapter_filename_matches_downstream_convention():
    ch = split(f"KINGS CROSS He {body('a')}", [TocEntry(number=197, title="King's Cross", book=7)])[0]
    assert chapter_filename(ch) == "7_197_King_s Cross.txt"


# -- real corpus --------------------------------------------------------------

HP_TEXT = REPO_ROOT / "sourceworks" / "Harry_Potter_all_books_preprocessed.txt"


@pytest.mark.integration
@pytest.mark.skipif(not HP_TEXT.exists(), reason="Harry Potter source text not present")
def test_harry_potter_splits_into_199_sane_chapters():
    cfg = load_source_config(HP_CONFIG, REPO_ROOT)
    text = cfg["source"]["text"].read_text(encoding="utf-8", errors="replace")
    chapters = split_text(text, load_toc(cfg["toc"]["path"], cfg["toc"]["format"]),
                          cfg["split"]["heading_aliases"])
    assert len(chapters) == 199
    assert {b: sum(c.entry.book == b for c in chapters) for b in range(1, 8)} == {
        1: 17, 2: 18, 3: 22, 4: 37, 5: 38, 6: 30, 7: 37}
    assert max(c.words for c in chapters) < 12000
    # the old splitter's failure modes, pinned
    assert chapters[0].text.endswith("To Harry Potter the boy who lived !")
    assert chapters[22].text.startswith("The next day however")      # 23 Gilderoy Lockhart
    assert chapters[187].text.startswith("Their plans were made")    # 188 Gringotts
    assert chapters[198].text.endswith("All was well .")             # 199 Epilogue
    # no source body text lost or duplicated
    assert sum(c.words for c in chapters) > 0.999 * len(text.split())
