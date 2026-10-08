#!/usr/bin/env python3
"""scripts/load_office_profiles.py
Load the ashiorid_office character profiles (campaigns/ashiorid_office/profiles/*.yaml)
into the character_profile database (OB-41; replaces the v4 book source stages).

Every profile is validated BEFORE connecting to the database; the load is one
transaction (commit on success, rollback on any error). --dry-run validates
and plans, prints what would happen, and rolls back. The script never migrates
the database. DB settings come from config/character.yaml plus CHARACTER_DB_*.

Usage:
    .venv/bin/python scripts/load_office_profiles.py [--profiles-dir DIR] [--cast-dir DIR]
        [--campaign SLUG] [--config PATH] [--dry-run] [--no-activate] [-v]

Exit codes:
    0  something was loaded (or, with --dry-run, would be)
    1  failed: invalid profile, config error, database error
    2  nothing to do: every profile unchanged
    3  precondition: the database is not migrated (no `characters` table)
"""
import argparse
import logging
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "app"))

from character import config, db  # noqa: E402
from character.generator import office_profiles  # noqa: E402

log = logging.getLogger("load_office_profiles")

DEFAULT_PROFILES_DIR = REPO_ROOT / "campaigns" / "ashiorid_office" / "profiles"


def _push_loki(operation, message, level="info", extra=None):
    """Ship an operational log line to the shared Loki stack (best-effort, never raises)."""
    try:
        loki_src = REPO_ROOT.parent / "projectManager" / "src"
        if not loki_src.is_dir():
            log.debug("loki push skipped: projectManager not present")
            return
        if str(loki_src) not in sys.path:
            sys.path.insert(0, str(loki_src))
        import loki_push

        loki_push.push({"app": "character", "operation": operation},
                       message, level=level, extra=extra or {})
    except Exception as exc:  # noqa: BLE001 - logging must never break the run
        log.debug("loki push skipped: %s", exc)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="load_office_profiles.py",
        description="Load the office character profiles into the character_profile database.")
    parser.add_argument("--profiles-dir", type=Path, default=DEFAULT_PROFILES_DIR,
                        help=f"directory of <slug>.yaml profiles (default: {DEFAULT_PROFILES_DIR})")
    parser.add_argument("--cast-dir", type=Path, default=None,
                        help="directory of cast/<slug>.yaml files for avatar params "
                             "(default: <profiles-dir>/../cast)")
    parser.add_argument("--campaign", default=None,
                        help="campaign slug written to characters.campaign "
                             "(default: the config's campaign)")
    parser.add_argument("--config", default=None,
                        help="path to the character config (default: config/character.yaml)")
    parser.add_argument("--dry-run", action="store_true",
                        help="validate and plan, write nothing")
    parser.add_argument("--no-activate", action="store_true",
                        help="write new baseline versions but do not move active_baseline_version")
    parser.add_argument("-v", "--verbose", action="store_true", help="DEBUG logging")
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, stream=sys.stderr)

    try:
        cfg = config.load(args.config)
    except config.ConfigError as exc:
        log.error("config error: %s", exc)
        print(f"config error: {exc}", file=sys.stderr)
        return 1

    profiles_dir = Path(args.profiles_dir)
    cast_dir = Path(args.cast_dir) if args.cast_dir is not None else profiles_dir.parent / "cast"
    accent = {slug: entry.accent_color for slug, entry in cfg.characters.items()}

    try:
        office_profiles.read_profiles(profiles_dir, cast_dir, accent)
    except office_profiles.ProfileError as exc:
        log.error("invalid profiles: %d errors", len(exc.errors))
        print("invalid profiles:", file=sys.stderr)
        for error in exc.errors:
            print(f"  {error}", file=sys.stderr)
        return 1

    try:
        conn = db.connect(cfg)
    except Exception as exc:  # noqa: BLE001 - any connect failure is exit 1
        log.error("database connection failed: %s", type(exc).__name__)
        print(f"error: could not connect to the character database "
              f"({type(exc).__name__}: {str(exc).strip().splitlines()[0] if str(exc).strip() else ''})",
              file=sys.stderr)
        return 1

    try:
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT to_regclass('public.characters') IS NULL")
                unmigrated = cur.fetchone()[0]
            conn.rollback()
        except Exception as exc:  # noqa: BLE001
            conn.rollback()
            log.error("precondition check failed: %s", type(exc).__name__)
            print(f"error: precondition check failed ({type(exc).__name__})", file=sys.stderr)
            return 1
        if unmigrated:
            log.error("database is not migrated")
            print("database is not migrated (no characters table)", file=sys.stderr)
            return 3

        try:
            report = office_profiles.load_profiles(
                conn, profiles_dir, cast_dir, campaign=args.campaign or cfg.campaign,
                accent_colors=accent, dry_run=args.dry_run, activate=not args.no_activate)
            if args.dry_run:
                conn.rollback()
            else:
                conn.commit()
        except Exception as exc:  # noqa: BLE001 - roll back, report, exit 1
            conn.rollback()
            first_line = str(exc).strip().splitlines()[0] if str(exc).strip() else ""
            log.error("load failed: %s: %s", type(exc).__name__, first_line)
            print(f"error: load failed ({type(exc).__name__}: {first_line})", file=sys.stderr)
            return 1
    finally:
        conn.close()

    if args.dry_run:
        print("DRY RUN: nothing written")
    for slug in sorted(report.actions):
        print(f"{slug}: {report.actions[slug]} v{report.versions[slug]}")
    counts = dict(sorted(Counter(report.actions.values()).items()))
    log.info("load_office_profiles done dry_run=%s actions=%s", args.dry_run, counts)
    if not args.dry_run:
        _push_loki("load_office_profiles", f"loaded office profiles: {counts}",
                   extra={"actions": counts, "changed": list(report.changed)})
    return 0 if report.changed else 2


if __name__ == "__main__":
    raise SystemExit(main())
