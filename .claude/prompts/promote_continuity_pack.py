"""Phase 1.5 — promote the 15 staged continuity contracts into the pack.

Reads .claude/prompts/continuity_backfill_v2.yaml (15 spine scenes, each with
continuity_in / continuity_out prose) and appends those two fields to the
matching scene YAML files under campaigns/ashiorid/scenes/.

Deliberately a TEXT-level append (not a full re-serialization): the diff a
human reviews is exactly the two new keys, nothing else. Each scene file is
backed up to /tmp before touching, and the pack must reload cleanly afterwards.
"""

import pathlib
import shutil
import sys
import yaml

REPO = pathlib.Path("/home/secus/codeProjects/virtualTubers")
STAGED = REPO / ".claude/prompts/continuity_backfill_v2.yaml"
SCENES = REPO / "campaigns/ashiorid/scenes"
BACKUP = pathlib.Path("/tmp/promote_continuity_backup")


def block_yaml(key: str, text: str) -> str:
    """Render one key to the exact YAML we append (block scalar, 2-space
    indent at call site — handled by caller), using PyYAML's own emitter so
    quotes/escapes are correct."""
    dumped = yaml.safe_dump({key: text}, sort_keys=False, default_flow_style=False,
                            width=100)
    # safe_dump wraps `key: >-` + indented lines; strip nothing, return as-is.
    return dumped.rstrip("\n")


def main() -> int:
    staged = yaml.safe_load(STAGED.read_text())
    scenes = staged["scenes"]
    if len(scenes) != 15:
        print(f"ABORT: expected 15 staged scenes, found {len(scenes)}")
        return 1

    BACKUP.mkdir(parents=True, exist_ok=True)
    failures = []
    promoted = 0

    for scene_id, entry in scenes.items():
        cont_in = entry.get("continuity_in")
        cont_out = entry.get("continuity_out")
        if not isinstance(cont_in, str) or not isinstance(cont_out, str) or not cont_in.strip() or not cont_out.strip():
            failures.append((scene_id, "missing/empty continuity text"))
            continue

        # find the scene file by its `id:` field, not filename
        target = None
        for f in SCENES.glob("*.yaml"):
            data = yaml.safe_load(f.read_text())
            if isinstance(data, dict) and data.get("id") == scene_id:
                target = f
                break
        if target is None:
            failures.append((scene_id, "no scene file with this id"))
            continue

        existing = yaml.safe_load(target.read_text())
        if "continuity_in" in existing or "continuity_out" in existing:
            print(f"  {scene_id}: already carries continuity fields — SKIPPED (no overwrite)")
            promoted += 1
            continue

        (BACKUP / target.name).write_text(target.read_text())

        snippet = block_yaml("continuity_in", cont_in) + "\n"
        snippet += block_yaml("continuity_out", cont_out) + "\n"
        # re-indent to sit at top level (safe_dump already emits at col 0)
        with target.open("a") as fh:
            fh.write("\n")
            fh.write(snippet)

        promoted += 1
        print(f"  {scene_id}: +continuity_in/out appended")

    print(f"\npromoted {promoted}/15, {len(failures)} failures")
    for scene_id, why in failures:
        print(f"  FAIL {scene_id}: {why}")
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
