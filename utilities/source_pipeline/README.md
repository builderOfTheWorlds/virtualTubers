# Source Pipeline

Turns a source work (novel series, campaign notes, scripts) into the data the
character system needs: clean chapters, a ranked cast, a timeline, two-layer
backstories, and versioned baseline profiles in Postgres. It is
**source-agnostic**: everything specific to one work lives in a small YAML
file under `sources/`, and the same code runs on every source. Harry Potter
(`sources/harry_potter.yaml`) is the calibration corpus. Tune the process there,
then point it at the next work.

Design: `docs/charcterProfileGenerationNotes/character_generator_updater_v3.md` §1.

## Prerequisites

- Python 3.11 in the repo `.venv` (`pip install -r requirements.txt`; only PyYAML is needed for stage 0)
- The source text plus a table of contents for it (see *Adding a new source*)

## Stages

| # | Stage | Status | Output |
|---|---|---|---|
| 0 | Split into chapters (`split.py`) | **built** | `<book>_<NNN>_<Title>.txt` + `manifest.json` |
| 1 | Clean text (deterministic + guarded LLM restore) | in progress (`sourceworks/phase*.py`) | cleaned chapters |
| 2 | Speaker and scene tagging | planned | per-scene speakers, presence, location |
| 3 | Cast ranking | planned | top-N characters + alias sets |
| 4 | Timeline | planned | events with story position |
| 5 | Two-layer backstory | planned | believed-at-start / GM truth |
| 6 | Baseline profile | planned | `character_baselines` rows |
| 7 | Cast export | planned | cast YAML from the DB |

Every stage is idempotent, reads the previous stage's output, and never edits
the original source file. The original is ground truth, and every output keeps
offsets back into it.

## Usage

```bash
# Report only: how many chapters were found, sizes, boundary warnings
.venv/Scripts/python utilities/source_pipeline/split.py utilities/source_pipeline/sources/harry_potter.yaml --dry-run

# Write chapters + manifest.json to split.output_dir (refuses a non-empty dir without --force)
.venv/Scripts/python utilities/source_pipeline/split.py utilities/source_pipeline/sources/harry_potter.yaml

# Tests (unit + a real-corpus check that skips if the HP text is absent)
.venv/Scripts/python -m pytest utilities/source_pipeline/tests -q
```

Expected output for Harry Potter:

```
199/199 chapters  words total=1,087,064 min=1,630 max=8,954
```

## How the splitter finds chapters

- Headings are searched **in order**: chapter N is only looked for after
  chapter N-1's heading. This stops a headline, sign, or shouted line that
  repeats a later title from being mistaken for that chapter's start.
- A match is ranked by whether it *looks* like a heading: preceded by a
  sentence end and followed by body text is best. Weaker matches are used only
  when no better one exists, and each is written to the manifest as a warning.
- Every chapter ends where the next begins. Nothing runs to end-of-file except
  the last chapter.
- The run **fails** if any heading is missing or any chapter falls outside
  `[min_words, max_words]`. That is the signature of a missed boundary, and
  it is better to stop than to write bloated files.

## Adding a new source

1. Put the text somewhere under `sourceworks/`.
2. Write a table of contents: `json` (`[{number, title, book?, date?}]`) or the
   `tsv_book_headers` format (see `src/toc.py`).
3. Copy `sources/harry_potter.yaml` to `sources/<id>.yaml` and edit the paths.
   Start with an empty `heading_aliases`.
4. Run with `--dry-run`. For each heading reported *not found*, search the
   text for how it is really spelled and add the **literal** source text as an
   alias. Repeat until 0 are missing and no chapter trips the size bounds.
5. Read every `WARN` line and confirm the boundary is right, then run for real.

## Configuration (`sources/<id>.yaml`)

| Key | Meaning |
|---|---|
| `source.id` | Short id used in the DB and file names |
| `source.text` | Repo-relative path to the flat source text |
| `toc.path`, `toc.format` | Table of contents and its format |
| `split.output_dir` | Where chapters + `manifest.json` are written |
| `split.max_words` / `min_words` | Chapter size sanity bounds |
| `split.heading_aliases` | `{chapter_number: [literal heading text, ...]}` for misspelled headings |

## Project structure

```
utilities/source_pipeline/
  split.py              stage 0 CLI
  sources/<id>.yaml     per-source config (the only per-source code)
  src/toc.py            table-of-contents loaders -> [TocEntry]
  src/splitter.py       in-order heading search, boundaries, sanity bounds
  src/source_config.py  config loading + path resolution
  tests/                pytest suite
```
