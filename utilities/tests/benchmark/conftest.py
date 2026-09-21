"""Path setup for the benchmarker test suite.

Mirrors `utilities/3LayersWeeklyGeneration/tests/conftest.py`: the modules
are imported as *flat leaf modules* (`runner`, `prompts`, `host_base`,
`ollama_host`, `vllm_host`, `config`) rather than a dotted package, so we
just put the utility's `lib/` dir on `sys.path` (plus `app/` in case a
test reuses campaign code the prompt builder depends on).

Layout from this file (utilities/tests/benchmark/conftest.py):
    parents[0]  utilities/tests/benchmark
    parents[1]  utilities/tests
    parents[2]  utilities
    parents[3]  <repo>
The benchmarker lib lives at  utilities/benchmarker/lib  (a *sibling* of
tests), so we reach it via parents[2] / "benchmarker" / "lib".
"""
import pathlib
import sys

BENCHMARKER_LIB = pathlib.Path(__file__).resolve().parents[2] / "benchmarker" / "lib"
REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]

for path in (BENCHMARKER_LIB, REPO_ROOT / "app"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))
