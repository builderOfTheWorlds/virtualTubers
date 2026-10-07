#!/usr/bin/env python3
"""Resumable driver for batch-generating cyber_police ambient content up to
a target total word count, following the recipe in the
virtualtubers-campaign-content skill (references/reaching_target_hours.md).

Each invocation:
  1. Sums word_count across campaigns/cyber_police/generated/manifest.jsonl
     (if it exists) plus the fixed spine word count.
  2. If already at/above target, no-ops (prints status, exits 0).
  3. Otherwise runs ONE bounded chunk of app/campaign/batch_generate.py
     (sized to stay well under the ~3600s cron SIGKILL ceiling) and exits.

Safe to invoke repeatedly (cron `every 1h`, or by hand) until it reports
target met. Each take is written to disk immediately by batch_generate.py,
so a chunk that's killed mid-run loses zero completed work.
"""
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PACK = REPO / "campaigns" / "cyber_police"
MANIFEST = PACK / "generated" / "manifest.jsonl"
CONFIG = REPO / "config" / "campaigns" / "cyber_police_batch_fast.yaml"

# The fixed, authored spine (7 scenes, measured by rendering the chain with
# Pacer(enabled=False) — see session notes). Counted once: the spine plays
# once per on-air day, ambient takes fill the rest of the 12-hour window.
SPINE_WORDS = 1473

# Target: cyber_police's own documented on-air day is 07:00-19:00 = 12h
# (lore/rituals.md). Word rate is this project's documented generation
# target (references/reaching_target_hours.md): 8929 words/hour.
TARGET_HOURS = 12
WORDS_PER_HOUR = 8929
TARGET_WORDS = TARGET_HOURS * WORDS_PER_HOUR  # ~107,148

# Chunk size: measured ~6s/take on llama3.1:8b locally. 400 takes/scene-set
# * 10 scenes would be 4000 takes in one call — too big. Chunk by
# takes-per-scene instead; 40 takes/scene * 10 scenes = 400 takes/chunk,
# ~2400s at the measured rate — safely under the 3600s cron ceiling.
TAKES_PER_SCENE_PER_CHUNK = 40
CHUNK_TIMEOUT_S = 3300  # a bit below the 3600s cron SIGKILL ceiling


def ambient_word_total() -> int:
    if not MANIFEST.exists():
        return 0
    total = 0
    with open(MANIFEST, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            total += json.loads(line)["word_count"]
    return total


def main() -> int:
    ambient_words = ambient_word_total()
    total_words = SPINE_WORDS + ambient_words
    print(f"spine: {SPINE_WORDS} words (fixed)")
    print(f"ambient so far: {ambient_words} words")
    print(f"total so far: {total_words} / {TARGET_WORDS} words "
          f"({total_words / TARGET_WORDS:.1%} of {TARGET_HOURS}h target)")

    if total_words >= TARGET_WORDS:
        print("TARGET MET — no-op.")
        return 0

    cmd = [
        str(REPO / ".venv" / "bin" / "python"),
        str(REPO / "app" / "campaign" / "batch_generate.py"),
        "--pack", str(PACK),
        "--config", str(CONFIG),
        "--takes-per-scene", str(TAKES_PER_SCENE_PER_CHUNK),
    ]
    print("running chunk:", " ".join(cmd))
    try:
        result = subprocess.run(
            cmd, cwd=str(REPO), env={"PYTHONPATH": "app", "PATH": "/usr/bin:/bin"},
            capture_output=True, text=True, timeout=CHUNK_TIMEOUT_S,
        )
        print(result.stdout[-2000:])
        if result.returncode != 0:
            print("chunk FAILED, stderr tail:", result.stderr[-2000:], file=sys.stderr)
            return 1
    except subprocess.TimeoutExpired:
        print(f"chunk STALLED past {CHUNK_TIMEOUT_S}s — lower "
              f"TAKES_PER_SCENE_PER_CHUNK. Partial progress is still on disk "
              f"(each take writes immediately); re-run to resume.",
              file=sys.stderr)
        print(f"accumulated before stall: {ambient_word_total() + SPINE_WORDS} words")
        return 1

    new_total = SPINE_WORDS + ambient_word_total()
    print(f"after this chunk: {new_total} / {TARGET_WORDS} words "
          f"({new_total / TARGET_WORDS:.1%})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
