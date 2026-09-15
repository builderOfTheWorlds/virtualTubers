#!/usr/bin/env python3
"""
Redeploy the vtube-worker stack (rebuild image + recreate worker containers).

The worker containers all run the same vtube-worker:latest image, and the
Dockerfile COPYs app/ and config/layouts/ (and other config) INTO the image
-- they are not bind-mounted. That means any edit under app/ or
config/layouts/ (etc.) requires an image rebuild before `docker compose up`
or `docker restart` will actually pick it up.

This script is OS-agnostic (Windows/Linux/macOS): it shells out to `docker`
directly via subprocess with argument lists (no shell=True, no bash-isms),
and resolves the repo root from the script's own location so it can be run
from any working directory.

Usage:
    python3 scripts/redeploy_workers.py
    python3 scripts/redeploy_workers.py --skip-build
    python3 scripts/redeploy_workers.py --services worker-gm worker-manager
    python3 scripts/redeploy_workers.py --no-verify

On Windows, invoke the same way via `py` or `python`:
    py scripts\\redeploy_workers.py
"""

import argparse
import subprocess
import sys
import time
from pathlib import Path

# All worker services share the vtube-worker:latest image (see docker-compose.yml).
DEFAULT_SERVICES = [
    "worker-manager",
    "worker-tester",
    "worker-coder",
    "worker-coder-native",
    "worker-coder-opencode",
    "worker-coder-aider",
    "worker-gm",
]

IMAGE_TAG = "vtube-worker:latest"

# Container name = virtualtubers-<service>-1 under the default compose project name.
CONTAINER_PREFIX = "virtualtubers-"
CONTAINER_SUFFIX = "-1"


def repo_root() -> Path:
    """Resolve the repo root as the parent of this script's scripts/ dir."""
    return Path(__file__).resolve().parent.parent


def run(cmd, cwd, check=True):
    """Run a subprocess with a printed command line; raise on nonzero exit if check=True."""
    print(f"$ {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=str(cwd))
    if check and result.returncode != 0:
        print(f"ERROR: command failed with exit code {result.returncode}: {' '.join(cmd)}", file=sys.stderr)
        sys.exit(result.returncode)
    return result.returncode


def build_image(root: Path):
    print("== Rebuilding vtube-worker image ==")
    run(["docker", "build", "-t", IMAGE_TAG, "."], cwd=root)


def recreate_services(root: Path, services):
    print(f"== Recreating {len(services)} worker service(s) ==")
    cmd = ["docker", "compose", "up", "-d", "--no-deps", "--force-recreate"] + services
    run(cmd, cwd=root)


def verify_services(services, wait_seconds=10):
    """Confirm each recreated container is Up and its ffmpeg publisher process is alive."""
    print(f"== Verifying containers (waiting {wait_seconds}s for boot) ==")
    time.sleep(wait_seconds)

    ok = True
    for service in services:
        container = f"{CONTAINER_PREFIX}{service}{CONTAINER_SUFFIX}"

        status = subprocess.run(
            ["docker", "inspect", "-f", "{{.State.Status}}", container],
            capture_output=True, text=True,
        )
        state = status.stdout.strip() if status.returncode == 0 else "unknown"
        if state != "running":
            print(f"  [FAIL] {container}: state={state}")
            ok = False
            continue

        ffmpeg_check = subprocess.run(
            ["docker", "exec", container, "sh", "-c",
             "ps -eo etimes,args | grep '[f]fmpeg' | head -1"],
            capture_output=True, text=True,
        )
        ffmpeg_line = ffmpeg_check.stdout.strip()
        if ffmpeg_line:
            print(f"  [OK]   {container}: running, ffmpeg alive ({ffmpeg_line.split()[0]}s)")
        else:
            print(f"  [WARN] {container}: running, but no ffmpeg publisher process found yet")
            ok = False

    if not ok:
        print(
            "\nSome services did not verify cleanly. A container/process check is NOT proof of "
            "a real stream -- pull a rendered frame (see the virtualtubers-stream-ops skill, "
            "Rule 1) before declaring the redeploy successful.",
            file=sys.stderr,
        )
    return ok


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--services", nargs="+", default=DEFAULT_SERVICES,
        help="compose service names to redeploy (default: all worker-* services)",
    )
    parser.add_argument("--skip-build", action="store_true", help="skip `docker build`, only recreate containers")
    parser.add_argument("--no-verify", action="store_true", help="skip the post-deploy container/ffmpeg check")
    parser.add_argument(
        "--verify-wait", type=int, default=10,
        help="seconds to wait after recreate before verifying (default: 10)",
    )
    args = parser.parse_args()

    root = repo_root()
    print(f"Repo root: {root}")
    print(f"Services:  {', '.join(args.services)}")

    if not args.skip_build:
        build_image(root)
    else:
        print("== Skipping image build (--skip-build) ==")

    recreate_services(root, args.services)

    if not args.no_verify:
        ok = verify_services(args.services, wait_seconds=args.verify_wait)
        sys.exit(0 if ok else 1)

    print("Done. (verification skipped)")


if __name__ == "__main__":
    main()
