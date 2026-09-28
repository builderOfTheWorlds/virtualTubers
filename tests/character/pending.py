"""Pending-code guard for the character v4 tests (OB-41, playbook §1.2).

The v4 test files are written and frozen BEFORE their modules exist: the
modules are generated later by the qwen harness (`tools/qwen_worker/runner.py
run <spec>`). Until a module is promoted, its tests must not turn the full
suite red, and once it exists they must run for real.

    from pending import require
    config = require("character.config", "app/character/config.py")

Behaviour:

- `CHARACTER_V4_STRICT=1`: import the module normally, so a missing module is
  a plain ImportError. Use this to check a frozen test file fails "for the
  right reason" (playbook §2 step 3).
- otherwise, when the target file (repo-relative) does not exist: skip the
  whole test module with "v4 <WP>: code pending (qwen harness)".
- otherwise: import normally, so real import errors in a promoted module
  surface as errors, never as skips.

The harness sandbox (`tools/qwen_worker/sandbox.py`) copies `app/` and
`tests/` and overlays the staged target files before running pytest, so inside
the sandbox the target exists and the tests run for real. REPO_ROOT is taken
from this file's location, which makes that hold in the sandbox copy too.
"""
import importlib
import logging
import os
from pathlib import Path

import pytest

log = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
STRICT_ENV = "CHARACTER_V4_STRICT"

#: target file -> work package, for the skip reason when `wp` is not given.
TARGET_WP = {
    "deploy/character-profile-db/scripts/backup.sh": "WP-02",
    "app/character/config.py": "WP-03",
    "app/character/clock.py": "WP-03",
    "app/character/shapes.py": "WP-03",
    "app/character/db.py": "WP-04",
    "app/character/store/characters.py": "WP-05",
    "app/character/store/weeks.py": "WP-05",
    "app/character/store/events.py": "WP-05",
    "app/character/store/knowledge.py": "WP-05",
    "app/character/store/fragments.py": "WP-05",
    "app/character/store/jobs.py": "WP-05",
    "app/character/jobs.py": "WP-06",
    "services/character-updater/main.py": "WP-06",
}


def strict():
    """True when CHARACTER_V4_STRICT=1 (never skip; missing code is an error)."""
    return os.environ.get(STRICT_ENV) == "1"


def pending(target_path):
    """True when the target file does not exist yet and strict mode is off."""
    missing = not (REPO_ROOT / target_path).is_file()
    log.debug("pending check target=%s missing=%s strict=%s", target_path, missing, strict())
    return missing and not strict()


def skip_reason(target_path, wp=None):
    wp = wp or TARGET_WP.get(target_path, "WP-??")
    return f"v4 {wp}: code pending (qwen harness)"


def skip_if_pending(target_path, wp=None):
    """Skip (module level or inside a test) when `target_path` is pending."""
    if pending(target_path):
        pytest.skip(skip_reason(target_path, wp), allow_module_level=True)


def require(module_name, target_path, wp=None):
    """Import `module_name`, or skip when its target file is still pending."""
    skip_if_pending(target_path, wp)
    log.debug("importing %s for target %s", module_name, target_path)
    return importlib.import_module(module_name)
