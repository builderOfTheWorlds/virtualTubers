"""Runs deploy/character-profile-db/tests/run_tests.sh as part of the pytest suite.

The harness exercises scripts/install.sh and scripts/uninstall.sh against
mocked pct/zfs/id/stat/chown/timeout, so it needs bash but no root and no
Proxmox host. It changes nothing outside its own temp directories.
"""
import logging
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

log = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[1]
HARNESS = REPO_ROOT / "deploy" / "character-profile-db" / "tests" / "run_tests.sh"


def _find_bash():
    """Git for Windows' bash on Windows (System32\\bash.exe is the WSL
    launcher, which fails when no distro is installed), else bash on PATH.

    git.exe can sit in Git\\cmd, Git\\bin or Git\\mingw64\\bin depending on
    the PATH entry, so walk up from it until a bin\\bash.exe turns up."""
    log.debug("find_bash platform=%s", sys.platform)
    if sys.platform == "win32":
        git = shutil.which("git")
        if git:
            for root in Path(git).resolve().parents:
                for candidate in (root / "bin" / "bash.exe", root / "usr" / "bin" / "bash.exe"):
                    if candidate.is_file():
                        log.debug("find_bash using git-bash path=%s", candidate)
                        return str(candidate)
        log.debug("find_bash no git-bash found near git=%s", git)
        return None
    return shutil.which("bash")


@pytest.mark.integration
def test_install_and_uninstall_scripts_pass_mocked_checks():
    bash = _find_bash()
    if bash is None:
        pytest.skip("bash not available")
    result = subprocess.run(
        [bash, HARNESS.as_posix()],
        capture_output=True, text=True, timeout=300,
    )
    tail = "\n".join(result.stdout.splitlines()[-25:])
    assert result.returncode == 0, f"harness failed:\n{tail}\n{result.stderr[-2000:]}"
    assert "failed: 0" in result.stdout, tail
