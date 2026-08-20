"""Path setup for the 3LayersWeeklyGeneration test suite.

Mirrors the repo-root `tests/conftest.py` pattern. Two insertions are needed:
`app/` so the utility's modules can import the campaign code they reuse
(`campaign.pack`, `campaign.improviser`, `campaign.batch_generate`,
`llm_client`), and this utility's own `src/` so the leaf module names import
directly (`from config import ...`) — the utility directory name is not a
valid Python identifier, so it is never imported as a dotted package.
"""
import pathlib
import sys

UTILITY_ROOT = pathlib.Path(__file__).resolve().parents[1]
REPO_ROOT = UTILITY_ROOT.parents[1]

for path in (REPO_ROOT / "app", UTILITY_ROOT / "src"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))
