#!/usr/bin/env python3
"""One-time import: campaigns/<pack>/* on disk -> Postgres pack_* tables.

This is the ONE place the read-only campaigns/:ro host mount is read from
directly — a one-shot, idempotent (safe to re-run) import, NOT a runtime
dependency. After this runs successfully for a pack, Postgres is
authoritative for that pack and the host files become a stale backup, not a
live source (see decision 5 in .hermes/plans/2026-09-17_010237-
unified-manager-gui.md).

Idempotency: every write is an upsert, so a partial failure partway through
a pack is safe to just re-run. It does NOT delete scenes/cast/lore rows that
are no longer on disk — an import only ever adds/updates (a row removed on
disk stays in Postgres, where an operator must delete it explicitly through
the Pack Editor, so a bad re-run can't silently wipe a scene).

worker_id backfill (decision 3): the old converters hardcoded a
SPEAKER_TO_WORKER dict. We retire that by seeding pack_cast.worker_id HERE,
once, from the same known mapping — so the episode builder (Phase 3) reads
the mapping from Postgres and an operator can change it in the GUI without a
code deploy. A cast member whose name isn't in the map keeps worker_id NULL
(the 'falls through to GM narration' state) and is listed in the summary so
an operator can fill it in by hand.

Usage (from a shell with the pack root + POSTGRES_* env, or the generator
container which has both):
    python scripts/import_packs_to_postgres.py                 # every pack
    python scripts/import_packs_to_postgres.py ashiorid_1      # one pack
    PACK_ROOT=/path/to/campaigns python scripts/... ashiorid_1
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import generation_store as store  # noqa: E402
import yaml  # noqa: E402

# The one known speaker->worker mapping, seeded once into
# pack_cast.worker_id (the decision-3 replacement for the old hardcoded
# SPEAKER_TO_WORKER dicts in the build_*_episode.py scripts). Keyed by the
# lowercase cast member NAME (not the filename), so it survives file casing.
KNOWN_WORKER_MAP = {
    "ashiorid": "manager",   # gm / Ashiorid
    "gm": "manager",         # the authored-pack gm cast id
    "chadwick": "coder",
    "leena": "tester",
    "vigil": "coder-native",
    "sodacan bob": "coder-opencode",
    "sodacan_bob": "coder-opencode",
}


def _yaml_file_stems(pack_dir: Path, sub: str) -> list:
    d = pack_dir / sub
    if not d.is_dir():
        return []
    return sorted(list(d.glob("*.yaml")) + list(d.glob("*.yml")),
                  key=lambda p: p.name.lower())


def import_pack(pack_name: str) -> dict:
    pack_dir = PACK_ROOT / pack_name
    if not pack_dir.is_dir():
        raise SystemExit(f"pack directory not found: {pack_dir}")

    campaign_yaml = (pack_dir / "campaign.yaml").read_text(encoding="utf-8")
    store.upsert_campaign(pack_name, campaign_yaml)

    cast_members = 0
    cast_names = {}  # member_id -> member name (for worker mapping)
    for path in _yaml_file_stems(pack_dir, "cast"):
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        name = (data.get("name") or "").strip()
        worker = KNOWN_WORKER_MAP.get(name.lower())
        store.upsert_cast_member(pack_name, path.stem,
                                 path.read_text(encoding="utf-8"), worker)
        cast_members += 1
        cast_names[path.stem] = (name, worker)

    scene_count = 0
    for path in _yaml_file_stems(pack_dir, "scenes"):
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        scene_id = data.get("id")
        if not scene_id:
            # Skip, loudly — a scene file with no `id:` is not importable
            # (load_pack would reject it) and silently naming it the filename
            # would create a phantom row.
            print(f"  !! skipped scene with no id: {path.name}", file=sys.stderr)
            continue
        store.upsert_scene(pack_name, scene_id, path.read_text(encoding="utf-8"))
        scene_count += 1

    lore_count = 0
    lore_dir = pack_dir / "lore"
    if lore_dir.is_dir():
        for path in sorted(lore_dir.glob("*.md")):
            store.upsert_lore(pack_name, path.stem,
                              path.read_text(encoding="utf-8"))
            lore_count += 1

    unmapped = sorted(mid for mid, (nm, w) in cast_names.items() if w is None)
    return {
        "pack": pack_name,
        "cast": cast_members,
        "scenes": scene_count,
        "lore": lore_count,
        "unmapped_cast": unmapped,
    }


if __name__ == "__main__":
    PACK_ROOT = Path(os.environ.get("PACK_ROOT", "/data/packs"))
    names = sys.argv[1:] or sorted(p.name for p in PACK_ROOT.iterdir() if p.is_dir())
    if not names:
        raise SystemExit(f"no pack directories found under {PACK_ROOT}")

    for name in names:
        summary = import_pack(name)
        print(f"imported {summary['pack']}: {summary['cast']} cast, "
              f"{summary['scenes']} scenes, {summary['lore']} lore notes")
        if summary["unmapped_cast"]:
            print(f"  note: cast with NO worker_id set (will be GM narration): "
                  f"{', '.join(summary['unmapped_cast'])} — set via Pack Editor")
