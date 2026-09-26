"""Path setup for the source_pipeline test suite (mirrors utilities/3LayersWeeklyGeneration/tests)."""
import pathlib
import sys

UTILITY_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(UTILITY_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(UTILITY_ROOT / "src"))
