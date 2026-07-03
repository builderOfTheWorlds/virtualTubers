# Make the sandbox modules importable from tests/ without packaging:
# pytest adds conftest.py's directory to sys.path (rootdir-based), so
# `from calculator import ...` works when the suite runs from any cwd.
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
