#!/usr/bin/env python3
"""Source pipeline CLI — stage 0: split a source work into chapters.

    python utilities/source_pipeline/split.py utilities/source_pipeline/sources/harry_potter.yaml
    python utilities/source_pipeline/split.py <source.yaml> --dry-run   # report only, write nothing

Writes ``<book>_<NNN>_<Title>.txt`` per chapter plus ``manifest.json``
(offsets into the original text, word counts, sha256, warnings) to the
source config's ``split.output_dir``. Refuses to overwrite a non-empty
output directory unless ``--force`` is given.
"""
import argparse
import json
import logging
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE / "src"))

from source_config import SourceConfigError, load_source_config  # noqa: E402
from splitter import SplitError, chapter_filename, split_text  # noqa: E402
from toc import TocError, load_toc  # noqa: E402

log = logging.getLogger("source_pipeline.split")


def build_manifest(cfg, chapters):
    return {
        "source_id": cfg["source"]["id"],
        "source_text": str(cfg["source"]["text"]),
        "chapters": [
            {
                "number": c.entry.number,
                "book": c.entry.book,
                "book_chapter": c.entry.book_chapter,
                "title": c.entry.title,
                "date": c.entry.date,
                "heading_in_source": c.heading,
                "heading_offset": c.heading_start,
                "body_offset_start": c.body_start,
                "body_offset_end": c.body_end,
                "words": c.words,
                "sha256": c.sha256,
                "filename": chapter_filename(c),
                "warnings": c.warnings,
            }
            for c in chapters
        ],
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("source_config")
    parser.add_argument("--dry-run", action="store_true", help="split and report, write nothing")
    parser.add_argument("--force", action="store_true", help="overwrite a non-empty output dir")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(levelname)s %(name)s %(message)s")

    try:
        cfg = load_source_config(args.source_config, REPO_ROOT)
        toc = load_toc(cfg["toc"]["path"], cfg["toc"]["format"])
        text = cfg["source"]["text"].read_text(encoding=cfg["source"].get("encoding", "utf-8"), errors="replace")
        split_cfg = cfg["split"]
        chapters = split_text(text, toc, split_cfg["heading_aliases"],
                              max_words=split_cfg.get("max_words", 25000),
                              min_words=split_cfg.get("min_words", 500))
    except (SourceConfigError, TocError, SplitError, OSError) as exc:
        log.error("split failed: %s", exc)
        return 1

    words = [c.words for c in chapters]
    print(f"{len(chapters)}/{len(toc)} chapters  words total={sum(words):,} "
          f"min={min(words):,} max={max(words):,}")
    for c in chapters:
        for w in c.warnings:
            print(f"  WARN {c.entry.number:3d} {c.entry.title}: {w}")

    if args.dry_run:
        print("dry run: nothing written")
        return 0

    out = split_cfg["output_dir"]
    if out.exists() and any(out.iterdir()) and not args.force:
        log.error("output dir %s is not empty; use --force to overwrite", out)
        return 1
    out.mkdir(parents=True, exist_ok=True)
    for c in chapters:
        (out / chapter_filename(c)).write_text(c.text, encoding="utf-8")
    (out / "manifest.json").write_text(json.dumps(build_manifest(cfg, chapters), indent=2, ensure_ascii=False),
                                       encoding="utf-8")
    log.info("split complete source=%s chapters=%d output_dir=%s", cfg["source"]["id"], len(chapters), out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
