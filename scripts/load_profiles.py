#!/usr/bin/env python3
"""scripts/load_profiles.py: load any pack's character profiles (build plan P2.4).

Pack-agnostic generalisation of scripts/load_office_profiles.py (which stays,
for its frozen tests). Reads campaigns/<pack>/profiles/*.yaml, validates every
file BEFORE connecting, then loads in one transaction (commit on success,
rollback on any error). GM-only blocks of a `table_role: gm` profile go to
character_baselines.gm_blocks (needs migration 002_gm_blocks). Never migrates.

Usage:
    .venv/bin/python scripts/load_profiles.py --pack ashiorid [--campaign SLUG]
        [--pack-dir DIR] [--config PATH] [--dry-run] [--no-activate] [-v]

Exit codes: 0 loaded (or would be), 1 failed, 2 nothing changed,
3 database not migrated (no characters table, or no gm_blocks column).
"""
import argparse
import logging
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "app"))

from character import config, db  # noqa: E402
from character.generator import office_profiles, pack_profiles  # noqa: E402

log = logging.getLogger("load_profiles")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="load_profiles.py",
                                     description="Load a pack's character profiles into the "
                                                 "character_profile database.")
    where = parser.add_mutually_exclusive_group(required=True)
    where.add_argument("--pack", help="pack name under campaigns/ (e.g. ashiorid, ashiorid_office)")
    where.add_argument("--pack-dir", type=Path, help="pack directory (contains profiles/ and cast/)")
    parser.add_argument("--campaign", default=None,
                        help="campaign slug for characters.campaign (default: the pack name)")
    parser.add_argument("--config", default=None, help="character config (default: config/character.yaml)")
    parser.add_argument("--dry-run", action="store_true", help="validate and plan, write nothing")
    parser.add_argument("--no-activate", action="store_true",
                        help="write new baseline versions but do not move the active pointer")
    parser.add_argument("-v", "--verbose", action="store_true", help="DEBUG logging")
    return parser


def _err(msg):
    print(msg, file=sys.stderr)


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, stream=sys.stderr)
    pack_dir = Path(args.pack_dir) if args.pack_dir else REPO_ROOT / "campaigns" / args.pack
    profiles_dir, cast_dir = pack_dir / "profiles", pack_dir / "cast"
    campaign = args.campaign or (args.pack or pack_dir.name)

    try:
        pack_profiles.read_pack_profiles(profiles_dir, cast_dir)
    except office_profiles.ProfileError as exc:
        log.error("invalid profiles: %d errors", len(exc.errors))
        _err("invalid profiles:")
        for error in exc.errors:
            _err(f"  {error}")
        return 1

    try:
        cfg = config.load(args.config)
        conn = db.connect(cfg)
    except Exception as exc:  # noqa: BLE001 - config or connect failure is exit 1
        first = str(exc).strip().splitlines()[0] if str(exc).strip() else ""
        log.error("database connection failed: %s", type(exc).__name__)
        _err(f"error: could not connect to the character database ({type(exc).__name__}: {first})")
        return 1

    try:
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass('public.characters') IS NULL, NOT EXISTS ("
                        "SELECT 1 FROM information_schema.columns WHERE table_name = "
                        "'character_baselines' AND column_name = 'gm_blocks')")
            no_table, no_column = cur.fetchone()
        conn.rollback()
        if no_table or no_column:
            _err("database is not migrated (run character.db.migrate: need 001_init + 002_gm_blocks)")
            return 3
        try:
            report = pack_profiles.load_pack(conn, profiles_dir, cast_dir, campaign=campaign,
                                             dry_run=args.dry_run, activate=not args.no_activate)
            conn.rollback() if args.dry_run else conn.commit()
        except Exception as exc:  # noqa: BLE001 - roll back, report, exit 1
            conn.rollback()
            first = str(exc).strip().splitlines()[0] if str(exc).strip() else ""
            log.error("load failed: %s: %s", type(exc).__name__, first)
            _err(f"error: load failed ({type(exc).__name__}: {first})")
            return 1
    finally:
        conn.close()

    if args.dry_run:
        print("DRY RUN: nothing written")
    for slug in sorted(report.actions):
        print(f"{slug}: {report.actions[slug]} v{report.versions[slug]}")
    log.info("load_profiles done pack=%s dry_run=%s actions=%s", campaign, args.dry_run,
             dict(sorted(Counter(report.actions.values()).items())))
    return 0 if report.changed else 2


if __name__ == "__main__":
    sys.exit(main())
