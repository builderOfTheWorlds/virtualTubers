"""Pack-agnostic profile loader for the live agent table (build plan P2.4, P2.7, U7).

Reads campaigns/<pack>/profiles/*.yaml (same schema as the office pack, see
campaigns/ashiorid/profiles/_SCHEMA.md) and writes them with the SAME write
path as the office loader (office_profiles.load_profiles): characters, a
versioned baseline, the believed + truth backstory layers, agent ids, the
active pointer. Idempotent: an unchanged file (same sha256) writes nothing.

Differences from the office reader:
- identity.age / identity.home may be null ("not in the sources");
- table extension fields (table_role, seat, turn_order_pos, wants, fears,
  speech, relationships, knowledge) are allowed and kept in the profile;
- a GM profile (table_role: gm) may carry GM-ONLY blocks: every top-level key
  that is not part of the player schema. They are stripped from the stored
  profile JSON and written to character_baselines.gm_blocks together with
  `truth` (backstory.truth). Adding a block is data, not code.
Players never get gm_blocks; their truth lives only in the truth layer, which
the player brief never reads.
"""
from __future__ import annotations

import copy
import logging

from character.generator import office_profiles
from character.generator.office_profiles import ProfileError  # noqa: F401  (re-export)
from character import shapes
from character.store import characters

log = logging.getLogger(__name__)

#: top-level keys of the player schema (office PROFILE_SHAPE + table extension fields)
PLAYER_KEYS = frozenset(office_profiles.PROFILE_SHAPE["properties"]) | frozenset({
    "table_role", "office_role", "seat", "turn_order_pos", "wants", "fears", "speech",
    "relationships", "knowledge",
})

TABLE_PROFILE_SHAPE = copy.deepcopy(office_profiles.PROFILE_SHAPE)
_identity = TABLE_PROFILE_SHAPE["properties"]["identity"]
_identity["required"] = [k for k in _identity["required"] if k != "age"]
_identity["properties"].pop("age")
_identity["properties"].pop("home")


def validate_table_profile(doc, expected_id=None) -> list[str]:
    """office validate_profile rules, but identity.age/home may be null."""
    errors = [e for e in office_profiles.validate_profile(doc, expected_id)
              if not e.startswith(("$.identity.age:", "$.identity.home:"))]
    if not isinstance(doc, dict):
        return errors
    identity = doc.get("identity") if isinstance(doc.get("identity"), dict) else {}
    if "age" not in identity:
        errors.append("$.identity.age: missing required field (use null when unknown)")
    else:
        age = identity["age"]
        if age is not None and (not isinstance(age, int) or isinstance(age, bool)):
            errors.append(f"$.identity.age: expected int or null, got {type(age).__name__}")
    home = identity.get("home")
    if home is not None and not isinstance(home, str):
        errors.append(f"$.identity.home: expected str or null, got {type(home).__name__}")
    errors += [e for e in shapes.validate(doc, TABLE_PROFILE_SHAPE)
               if e not in errors and not e.startswith(("$.identity.age", "$.identity.home"))]
    return sorted(set(errors), key=errors.index)


def gm_blocks_of(doc) -> dict | None:
    """The GM-only blocks of a parsed profile, or None for a non-GM profile."""
    if not isinstance(doc, dict) or doc.get("table_role") != "gm":
        return None
    blocks = {k: v for k, v in doc.items() if k not in PLAYER_KEYS}
    truth = (doc.get("backstory") or {}).get("truth")
    if truth:
        blocks = {"truth": truth, **blocks}
    return blocks


def _strip_gm_blocks(doc):
    if not isinstance(doc, dict) or doc.get("table_role") != "gm":
        return doc
    return {k: v for k, v in doc.items() if k in PLAYER_KEYS}


def read_pack_profiles(profiles_dir, cast_dir=None, accent_colors=None):
    """Read + validate every profile (all errors collected), GM blocks stripped."""
    return office_profiles.read_profiles(profiles_dir, cast_dir, accent_colors,
                                         validator=validate_table_profile,
                                         transform=_strip_gm_blocks)


def _gm_docs(profiles_dir) -> dict[str, dict]:
    docs = {}
    for path in office_profiles.profile_files(profiles_dir):
        doc = office_profiles._read_yaml(path)
        blocks = gm_blocks_of(doc)
        if blocks is not None:
            docs[path.stem] = blocks
    return docs


def load_pack(conn, profiles_dir, cast_dir=None, *, campaign, accent_colors=None,
              dry_run=False, activate=True) -> office_profiles.LoadReport:
    """Load a pack's profiles (never commits). GM blocks are (re)written when the
    GM baseline was created/updated, or when the active baseline has none."""
    records = read_pack_profiles(profiles_dir, cast_dir, accent_colors)
    report = office_profiles.load_profiles(conn, profiles_dir, cast_dir, campaign=campaign,
                                           dry_run=dry_run, activate=activate, records=records)
    if dry_run:
        return report
    for slug, blocks in _gm_docs(profiles_dir).items():
        row = characters.get_character(conn, slug)
        if row is None:
            continue
        version = report.versions[slug]
        if report.actions.get(slug) in ("created", "updated") or characters.gm_blocks(conn, slug) is None:
            characters.set_gm_blocks(conn, row["id"], version, blocks)
            log.info("gm blocks written slug=%s version=%s blocks=%s", slug, version, sorted(blocks))
    return report
