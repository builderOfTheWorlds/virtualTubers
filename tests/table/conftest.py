"""Live agent table tests (build plan Phase 3). Reuse the character v4
pending-code guard: frozen tests SKIP until their target module exists, and
CHARACTER_V4_STRICT=1 turns a missing module into a failure."""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "character"))
