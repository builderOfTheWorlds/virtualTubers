#!/usr/bin/env python3
"""
merge_env_example_into_env.py
Merge keys that exist in .env.example but are missing from .env, and set the
GM/roundtable stream key.

Why a script and not hand-editing: .env holds six real Twitch stream keys and a
Postgres password. A script is auditable, idempotent (safe to re-run), supports
--dry-run, and never prints a secret value — it reports key names and
value fingerprints (length + sha256 prefix) only.

Behaviour
---------
* Only keys MISSING from .env are appended. An existing key is NEVER overwritten
  (your local values win) — except TUBER0_STREAM_KEY when --tuber0-key is given,
  which is set explicitly because it is a brand-new channel credential.
* Local-only keys in .env (GENERATOR_*) are left untouched.
* A timestamped backup is taken before writing.
* Appended entries carry their .env.example comment block, so the merged file
  stays self-documenting.

Usage
-----
    python3 .claude/prompts/merge_env_example_into_env.py --dry-run
    python3 .claude/prompts/merge_env_example_into_env.py --tuber0-key 'live_...'
"""
import argparse
import hashlib
import shutil
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
ENV = REPO / ".env"
EXAMPLE = REPO / ".env.example"


def fingerprint(value):
    """Describe a value without revealing it."""
    if value == "":
        return "(empty)"
    return f"len={len(value)} sha={hashlib.sha256(value.encode()).hexdigest()[:8]}"


def parse_keys(path):
    """Ordered list of (key, value) for non-comment assignments."""
    pairs = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key, _, value = stripped.partition("=")
            pairs.append((key.strip(), value))
    return pairs


def blocks_from_example(path):
    """Map key -> (comment_lines, assignment_line) from .env.example, so a merged
    key arrives with the documentation that explains it."""
    blocks = {}
    pending = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            # A section header (── … ──) resets context rather than attaching to
            # whatever key happens to come next.
            pending = [] if "──" in stripped else pending + [line]
        elif stripped and "=" in stripped:
            key = stripped.partition("=")[0].strip()
            blocks[key] = (pending, line)
            pending = []
        else:
            pending = []
    return blocks


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--tuber0-key", default=None,
                        help="value for TUBER0_STREAM_KEY (the GM/roundtable channel)")
    args = parser.parse_args()

    if not ENV.is_file() or not EXAMPLE.is_file():
        print("ERROR: .env and/or .env.example not found", file=sys.stderr)
        return 1

    env_pairs = parse_keys(ENV)
    env_keys = {k for k, _ in env_pairs}
    example_blocks = blocks_from_example(EXAMPLE)

    missing = [k for k in example_blocks if k not in env_keys]
    print(f".env: {len(env_keys)} keys | .env.example: {len(example_blocks)} keys")
    print(f"missing from .env: {len(missing)}")
    for key in missing:
        print(f"   + {key}")

    text = ENV.read_text(encoding="utf-8")
    appended = []

    if missing:
        chunk = [
            "",
            "# ── Merged from .env.example ───────────────────────────────────────────────────",
            "# Keys present in the template but absent here. Appended by",
            "# .claude/prompts/merge_env_example_into_env.py — existing values were never",
            "# overwritten. Roundtable/GM entries relate to the 7th channel",
            "# (roundtable_stream_design.md v1.1 §9).",
        ]
        for key in missing:
            comments, assignment = example_blocks[key]
            if comments:
                chunk.append("")
                chunk.extend(comments)
            chunk.append(assignment)
            appended.append(key)
        text = text.rstrip("\n") + "\n" + "\n".join(chunk) + "\n"

    # Set the GM stream key explicitly (the one value we DO overwrite, since a
    # freshly-merged TUBER0_STREAM_KEY is an empty placeholder).
    if args.tuber0_key:
        lines = text.splitlines()
        for i, line in enumerate(lines):
            if line.strip().startswith("TUBER0_STREAM_KEY="):
                lines[i] = f"TUBER0_STREAM_KEY={args.tuber0_key}"
                break
        else:
            lines.append(f"TUBER0_STREAM_KEY={args.tuber0_key}")
        text = "\n".join(lines) + "\n"
        print(f"\nTUBER0_STREAM_KEY set -> {fingerprint(args.tuber0_key)}")

    if args.dry_run:
        print("\n--dry-run: nothing written.")
        return 0

    backup = ENV.with_suffix(f".bak.{time.strftime('%Y%m%d_%H%M%S')}")
    shutil.copy2(ENV, backup)
    ENV.write_text(text, encoding="utf-8")
    print(f"\nbackup: {backup.name}")
    print(f"appended {len(appended)} key(s); wrote {ENV.name}")

    # Post-write integrity: every pre-existing key must still hold its old value.
    after = dict(parse_keys(ENV))
    changed = [k for k, v in env_pairs
               if k in after and after[k] != v and k != "TUBER0_STREAM_KEY"]
    print("pre-existing values preserved:" , "YES" if not changed else f"NO -> {changed}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
