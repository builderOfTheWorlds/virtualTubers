"""Repo-root pytest glue: per-suite isolation of colliding flat module names.

Several suites import their code as bare leaf modules by putting a directory
on sys.path (their directory names aren't valid package names). Two of those
names exist twice in the repo:

    config.py  utilities/3LayersWeeklyGeneration/src  (YAML generation config)
               utilities/benchmarker/lib              (TOML benchmark config)
    runner.py  services/3layer-generator              (job runner)
               utilities/benchmarker/lib              (benchmark runner)

pytest loads every testpaths conftest up front, so whichever conftest ran
last owned sys.path[0], and sys.modules then cached that one module for the
whole session. A bare `pytest` therefore fed the wrong `config`/`runner` to
the other suites (e.g. `module 'config' has no attribute 'ConfigError'`,
`module 'runner' has no attribute 'Context'`), while each suite passed alone.

Before each suite collects a module or runs a test, this puts that suite's
dirs first on sys.path and swaps in that suite's copy of each colliding module.
It reuses the already-imported copy when there is one, so class identity
(e.g. ConfigError) stays stable within a suite.
"""
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent
_3LWG_SRC = REPO / "utilities" / "3LayersWeeklyGeneration" / "src"
_SERVICE = REPO / "services" / "3layer-generator"
_BENCH_LIB = REPO / "utilities" / "benchmarker" / "lib"

# test dir -> import dirs in priority order (first wins)
SUITES = {
    REPO / "utilities" / "3LayersWeeklyGeneration" / "tests": [_3LWG_SRC],
    _SERVICE / "tests": [_SERVICE, _3LWG_SRC],
    REPO / "utilities" / "tests" / "benchmark": [_BENCH_LIB],
}
COLLIDING = ("config", "runner")

_CACHE = {}  # resolved source file -> module object


def _suite_for(path):
    if path is None:
        return None
    path = Path(path).resolve()
    for test_dir, dirs in SUITES.items():
        if path == test_dir or test_dir in path.parents:
            return dirs
    return None


def _module_file(module):
    file = getattr(module, "__file__", None)
    return Path(file).resolve() if file else None


def _activate(path):
    dirs = _suite_for(path)
    if dirs is None:
        return
    for d in reversed(dirs):
        s = str(d)
        if s in sys.path:
            sys.path.remove(s)
        sys.path.insert(0, s)
    for name in COLLIDING:
        want = next((d / f"{name}.py" for d in dirs if (d / f"{name}.py").is_file()), None)
        current = sys.modules.get(name)
        current_file = _module_file(current) if current is not None else None
        if current_file is not None:
            _CACHE[current_file] = current
        if want is None or current_file == want.resolve():
            continue
        if want.resolve() in _CACHE:
            sys.modules[name] = _CACHE[want.resolve()]
        else:
            sys.modules.pop(name, None)


@pytest.hookimpl(tryfirst=True)
def pytest_collectstart(collector):
    _activate(getattr(collector, "path", None))


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_setup(item):
    _activate(item.path)
