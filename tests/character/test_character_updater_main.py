"""WP-06 tests for services/character-updater/main.py, the one CLI for every job.

Frozen test list (playbook §4 WP-06, items 1 and 7). Plan §5: every job is a
subcommand of `python services/character-updater/main.py <job> [...]`; main.py
inserts app/ into sys.path itself (D-23), so it works from any cwd. These run
main.py as a subprocess and need no database.
"""
import os
import subprocess
import sys
from pathlib import Path

from pending import skip_if_pending

REPO_ROOT = Path(__file__).resolve().parents[2]
MAIN = REPO_ROOT / "services" / "character-updater" / "main.py"

skip_if_pending("services/character-updater/main.py")
if not MAIN.is_file():  # only reachable with CHARACTER_V4_STRICT=1
    raise ImportError(f"services/character-updater/main.py not found: {MAIN}")


def _run(args, cwd):
    env = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
    return subprocess.run([sys.executable, str(MAIN), *args], cwd=cwd, env=env,
                          capture_output=True, text=True, timeout=120)


# T06.1
def test_unknown_job_exits_argparse_2_with_usage(tmp_path):
    result = _run(["no-such-job"], tmp_path)
    assert result.returncode == 2
    assert "usage" in result.stderr.lower()
    assert "no-such-job" in result.stderr


# T06.7
def test_main_works_from_any_cwd(tmp_path):
    for cwd in (tmp_path, REPO_ROOT / "services", Path(MAIN).parent):
        result = _run(["--help"], cwd)
        assert result.returncode == 0, result.stderr
        assert "status" in result.stdout
    result = _run(["status", "--help"], tmp_path)
    assert result.returncode == 0, result.stderr
    assert "--dry-run" in result.stdout and "--at" in result.stdout
