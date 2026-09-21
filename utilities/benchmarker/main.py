"""Main entry point for the D&D-agents benchmark.

This is the file `bin/runBenchmark.sh` executes:

    ./.venv/bin/python main.py [flags]

`main()` lives in `lib/runner.py`; this file just:
  1. puts `lib/` on the import path so `from runner import main` works
  2. hands `sys.argv` to `main()`
  3. exits with `main()`'s return code

Keeping it flat (no package `__init__.py`) follows the `3LayersWeeklyGeneration`
sibling convention: `lib/` holds flat leaf modules, `tests/conftest.py`
sets up `sys.path`, and `main.py` is the launchpad.
"""
from __future__ import annotations

import sys
from pathlib import Path

# `lib/` lives next to this file.
LIB_DIR = (Path(__file__).resolve().parent / "lib")
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from runner import main  # noqa: E402  (LIB_DIR added to sys.path above)
# ^ Pyright can't evaluate the sys.path mutation above; runtime import is
#   verified by the test suite. Silence the static-only noise.

if __name__ == "__main__":
    raise SystemExit(main())
