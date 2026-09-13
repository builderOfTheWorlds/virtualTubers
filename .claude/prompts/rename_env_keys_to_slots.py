#!/usr/bin/env python3
"""
rename_env_keys_to_slots.py
Rename the per-worker ENV VAR KEY NAMES from role-based to slot-based
(roundtable_stream_design.md v1.1 §2.1 / §2.2 step 2), across .env,
.env.example, docker-compose.yml and README.md.

SCOPE — read this before running
================================
This script renames **env var KEY NAMES ONLY**. It deliberately does NOT touch:

  * env var VALUES (your real Twitch stream keys are preserved byte-for-byte),
  * WORKER IDs (`WORKER_ID: coder`), compose SERVICE names (`worker-coder`),
  * `TWITCH_CHANNEL_MAP` (its values are worker ids, not key names),
  * `config/workers/*.yaml` filenames or their `voice.speakers` keys,
  * episode `speaker` values in Postgres.

Why the split matters: an episode's `speaker` values ARE worker ids. All three
episodes currently in the library speak as `coder` / `tester` / `coder-native` /
`coder-opencode` / `coder-aider`. Renaming a worker id without migrating those
episodes means the speaker no longer matches any `voice.speakers` entry, so the
line silently falls through to the base voice — every character collapses onto
ONE voice with no error raised anywhere. That is why the full identity migration
is WP-7, gated on WP-6 being verified live.

Key renames (§2.1 mapping):
    CODER_*          -> TUBER1_*      (was KODI-7)
    CODER_NATIVE_*   -> TUBER2_*      (was NYX-1)
    CODER_OPENCODE_* -> TUBER3_*      (was OKO-2)
    CODER_AIDER_*    -> TUBER4_*      (was ADA-3)
    TESTER_*         -> TUBER5_*      (was TESS-3)
    MANAGER_*        -> TUBER6_*      (was MAX-1)

Order is load-bearing: CODER_NATIVE_ must be rewritten before CODER_, or the
CODER_ rule would match the prefix of CODER_NATIVE_STREAM_KEY first and produce
TUBER1_NATIVE_STREAM_KEY. The patterns below are applied longest-prefix-first
and every rename is word-anchored.

Usage:
    python3 .claude/prompts/rename_env_keys_to_slots.py --dry-run   # preview
    python3 .claude/prompts/rename_env_keys_to_slots.py            # apply
"""
import argparse
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

# Longest prefix FIRST — see the docstring note about CODER_NATIVE_ vs CODER_.
RENAMES = [
    ("CODER_NATIVE_", "TUBER2_"),
    ("CODER_OPENCODE_", "TUBER3_"),
    ("CODER_AIDER_", "TUBER4_"),
    ("CODER_", "TUBER1_"),
    ("TESTER_", "TUBER5_"),
    ("MANAGER_", "TUBER6_"),
]

# Only these suffixes are per-worker knobs. Anything else sharing a prefix (e.g.
# a hypothetical CODER_SOMETHING_ELSE) is left alone rather than guessed at.
SUFFIXES = ("STREAM_KEY", "LAYOUT_PRESET", "AVATAR_PROVIDER")

TARGETS = [".env", ".env.example", "docker-compose.yml", "README.md"]


def build_patterns():
    """(compiled_regex, replacement) pairs, word-anchored so a key name is only
    rewritten when it is a whole token — never inside a longer identifier."""
    patterns = []
    for old, new in RENAMES:
        for suffix in SUFFIXES:
            old_key = f"{old}{suffix}"
            new_key = f"{new}{suffix}"
            patterns.append((re.compile(rf"\b{re.escape(old_key)}\b"), new_key))
    return patterns


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true",
                        help="report what would change without writing")
    args = parser.parse_args()

    patterns = build_patterns()
    total = 0

    for rel in TARGETS:
        path = REPO / rel
        if not path.is_file():
            print(f"  SKIP {rel} (not found)")
            continue

        original = path.read_text(encoding="utf-8")
        updated = original
        per_file = []
        for pattern, replacement in patterns:
            updated, n = pattern.subn(replacement, updated)
            if n:
                per_file.append(f"{pattern.pattern.strip(chr(92) + 'b')} -> {replacement} x{n}")
                total += n

        if updated == original:
            print(f"  --   {rel}: no change")
            continue

        print(f"  {'WOULD EDIT' if args.dry_run else 'EDITED'} {rel}:")
        for line in per_file:
            print(f"       {line}")
        if not args.dry_run:
            path.write_text(updated, encoding="utf-8")

    print(f"\n{total} key occurrence(s) {'would be' if args.dry_run else ''} renamed.")
    if args.dry_run:
        print("Re-run without --dry-run to apply.")
    else:
        print("Values, WORKER_IDs, service names and TWITCH_CHANNEL_MAP untouched.")
        print("Next: `docker compose config --quiet` to confirm the stack still parses.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
