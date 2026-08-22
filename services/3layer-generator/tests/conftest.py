"""Path setup for the 3layer-generator service test suite.

Mirrors utilities/3LayersWeeklyGeneration/tests/conftest.py. Three insertions:
the service's own directory (its module names are imported bare — the directory
name starts with a digit and contains a hyphen, so it is never importable as a
package), the utility's `src/` for the layer modules the service drives, and
`app/` for the campaign code those modules reuse.
"""
import pathlib
import sys

SERVICE_ROOT = pathlib.Path(__file__).resolve().parents[1]
REPO_ROOT = SERVICE_ROOT.parents[1]

for path in (REPO_ROOT / "app",
             REPO_ROOT / "utilities" / "3LayersWeeklyGeneration" / "src",
             SERVICE_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))
