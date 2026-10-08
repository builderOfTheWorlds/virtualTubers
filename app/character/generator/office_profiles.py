"""Office profile loader (OB-41): campaigns/ashiorid_office/profiles/*.yaml ->
characters, character_baselines, character_backstories, character_agents.

The office cast has no book source: its backstories are authored in
profiles/<slug>.yaml (contract: profiles/_SCHEMA.md). This module replaces the
book stages of plan §1 for that cast. For each profile it writes the character
row, baseline version N (profile minus backstory, plus backstory_nodes, book /
chapter 0/0), one `believed` and one `truth` backstory row, and the agent ids
`char:<slug>` and the seat worker id `tuber_N`.

Rules:
- GM-only `truth` text goes into exactly one place: the `truth` backstory row.
  It never reaches the profile, the nodes or the believed row.
- Baselines are versioned and never modified: a changed profile (new sha256)
  adds version N+1 for that character only; unchanged profiles write nothing.
- Every profile is validated before any SQL; `load_profiles` never commits.
"""
from __future__ import annotations

import logging
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import yaml

from character import db, node_names, shapes
from character.store import characters
from character_schema import resolve_params

log = logging.getLogger(__name__)

SOURCE_PREFIX = "office_profile:"
CREATED_BY = "load_office_profiles"
BASELINE_BOOK = 0
BASELINE_CHAPTER = 0
SEAT_RE = re.compile(r"^tuber_[0-9]+$")

_STR = {"type": "str"}
_STR_LIST = {"type": "list", "items": _STR}

PROFILE_SHAPE = {
    "type": "object",
    "required": ["id", "retains_fragments", "is_main", "identity", "appearance",
                 "personality", "objectives", "backstory", "backstory_nodes",
                 "behaviour_contract"],
    "properties": {
        "id": _STR,
        "retains_fragments": {"type": "bool"},
        "is_main": {"type": "bool"},
        "appearance": _STR,
        "identity": {
            "type": "object",
            "required": ["full_name", "age", "pronouns", "title", "seat"],
            "properties": {
                "full_name": _STR,
                "age": {"type": "int"},
                "pronouns": _STR,
                "title": _STR,
                "seat": _STR,
                "tenure_months": {"type": "int"},
                "home": _STR,
                "commute": _STR,
                "hobbies": _STR_LIST,
            },
        },
        "personality": {
            "type": "object",
            "required": ["traits"],
            "properties": {
                "traits": _STR_LIST,
                "speech_tics": _STR_LIST,
                "work_style": _STR,
                "stress_response": _STR,
            },
        },
        "objectives": {
            "type": "object",
            "required": ["wants", "fears"],
            "properties": {"wants": _STR_LIST, "fears": _STR_LIST, "secrets": _STR_LIST},
        },
        "backstory": {
            "type": "object",
            "required": ["believed", "truth"],
            "properties": {"believed": _STR, "truth": _STR},
        },
        "backstory_nodes": {
            "type": "list",
            "items": {
                "type": "object",
                "required": ["name", "statement"],
                "properties": {"name": _STR, "statement": _STR},
            },
        },
        "behaviour_contract": _STR_LIST,
    },
}


class ProfileError(ValueError):
    """One or more invalid profiles; `.errors` lists every problem."""

    def __init__(self, errors):
        self.errors = list(errors)
        super().__init__("\n".join(self.errors))

    def __str__(self) -> str:
        return "\n".join(self.errors)


@dataclass(frozen=True)
class OfficeProfile:
    slug: str
    path: Path
    sha256: str
    name: str
    retains_fragments: bool
    is_main: bool
    seat: str
    aliases: tuple[str, ...]
    avatar_params: dict
    profile: dict
    backstory_nodes: tuple[dict, ...]
    believed: str
    truth: str

    def __repr__(self) -> str:  # keep backstory text (truth!) out of logs and reprs
        return (f"OfficeProfile(slug={self.slug!r}, path={str(self.path)!r}, "
                f"sha256={self.sha256[:12]!r}, seat={self.seat!r})")

    @property
    def source_id(self) -> str:
        return SOURCE_PREFIX + self.sha256

    @property
    def agent_ids(self) -> tuple[str, str]:
        return (f"char:{self.slug}", self.seat)


@dataclass(frozen=True)
class LoadReport:
    actions: dict[str, str]
    versions: dict[str, int]
    dry_run: bool

    @property
    def changed(self) -> tuple[str, ...]:
        return tuple(sorted(slug for slug, action in self.actions.items()
                            if action != "unchanged"))


def profile_files(profiles_dir) -> list[Path]:
    """The profile YAML files, sorted, skipping names that start with "_"."""
    return [p for p in sorted(Path(profiles_dir).glob("*.yaml")) if not p.name.startswith("_")]


def profile_sha256(path) -> str:
    """Hex sha256 of the profile file (CRLF normalised to LF)."""
    return db.file_sha256(path)


def validate_profile(doc, expected_id=None) -> list[str]:
    """Every problem with a parsed profile as "<$.path>: <reason>"; [] when valid."""
    errors = shapes.validate(doc, PROFILE_SHAPE)
    if not isinstance(doc, dict):
        return errors
    doc_id = doc.get("id")
    if expected_id is not None and isinstance(doc_id, str) and doc_id != expected_id:
        errors.append(f"$.id: {doc_id!r} does not match the file name {expected_id!r}")
    identity = doc.get("identity")
    if isinstance(identity, dict):
        seat = identity.get("seat")
        if isinstance(seat, str) and not SEAT_RE.match(seat):
            errors.append(f"$.identity.seat: {seat!r} is not a seat worker id "
                          f"(must match {SEAT_RE.pattern})")
    backstory = doc.get("backstory")
    if isinstance(backstory, dict):
        believed = backstory.get("believed")
        if isinstance(believed, str) and not believed.strip():
            errors.append("$.backstory.believed: must not be empty")
    nodes = doc.get("backstory_nodes")
    if isinstance(nodes, list):
        seen = set()
        for index, node in enumerate(nodes):
            if not isinstance(node, dict) or not isinstance(node.get("name"), str):
                continue
            name = node["name"]
            for reason in node_names.check(name):
                errors.append(f"$.backstory_nodes[{index}].name: {reason}")
            if name in seen:
                errors.append(f"$.backstory_nodes[{index}].name: duplicate node name {name!r}")
            seen.add(name)
    return errors


def avatar_params(cast_doc=None, accent_color=None) -> dict:
    """OB-20 cast character_params when present, else defaults plus accent colour."""
    if isinstance(cast_doc, dict) and isinstance(cast_doc.get("character_params"), dict):
        return resolve_params(cast_doc["character_params"])
    params = dict(resolve_params(None))
    if accent_color is not None:
        params["accent_color"] = accent_color
    return params


def aliases_for(doc) -> tuple[str, ...]:
    """identity.title, then each word of identity.full_name not ending with "."."""
    identity = doc.get("identity") or {}
    candidates = [identity.get("title")]
    candidates += [word for word in str(identity.get("full_name") or "").split()
                   if not word.endswith(".")]
    aliases: list[str] = []
    for alias in candidates:
        if isinstance(alias, str) and alias and alias not in aliases:
            aliases.append(alias)
    return tuple(aliases)


def _read_yaml(path: Path):
    try:
        with open(path, encoding="utf-8") as handle:
            return yaml.safe_load(handle)
    except (OSError, yaml.YAMLError) as exc:
        log.error("office profile unreadable file=%s error=%s", path.name, type(exc).__name__)
        raise ProfileError([f"{path.name}: {exc}"]) from exc


def read_profile(path, cast_dir=None, accent_color=None) -> OfficeProfile:
    """Parse and validate one profile file; ProfileError lists every problem."""
    path = Path(path)
    doc = _read_yaml(path)
    errors = validate_profile(doc, expected_id=path.stem)
    if errors:
        log.error("office profile invalid file=%s errors=%d", path.name, len(errors))
        raise ProfileError([f"{path.name}: {error}" for error in errors])
    cast_doc = None
    if cast_dir is not None:
        cast_path = Path(cast_dir) / path.name
        if cast_path.is_file():
            cast_doc = _read_yaml(cast_path)
    identity, backstory = doc["identity"], doc["backstory"]
    record = OfficeProfile(
        slug=doc["id"],
        path=path,
        sha256=profile_sha256(path),
        name=identity["full_name"],
        retains_fragments=doc["retains_fragments"],
        is_main=doc["is_main"],
        seat=identity["seat"],
        aliases=aliases_for(doc),
        avatar_params=avatar_params(cast_doc, accent_color),
        profile={k: v for k, v in doc.items() if k not in ("backstory", "backstory_nodes")},
        backstory_nodes=tuple(doc["backstory_nodes"]),
        believed=backstory["believed"],
        truth=backstory["truth"],
    )
    log.debug("office profile read slug=%s sha256=%s", record.slug, record.sha256[:12])
    return record


def read_profiles(profiles_dir, cast_dir=None, accent_colors=None) -> list[OfficeProfile]:
    """Read every profile; collect the errors of ALL bad files, then raise."""
    files = profile_files(profiles_dir)
    if not files:
        log.error("office profiles: none found in %s", profiles_dir)
        raise ProfileError([f"no profiles in {profiles_dir}"])
    records, errors = [], []
    for path in files:
        try:
            records.append(read_profile(path, cast_dir, (accent_colors or {}).get(path.stem)))
        except ProfileError as exc:
            errors.extend(exc.errors)
    if errors:
        log.error("office profiles invalid: %d errors in %s", len(errors), profiles_dir)
        raise ProfileError(errors)
    return sorted(records, key=lambda record: record.slug)


def _latest_baseline(conn, character_id):
    with conn.cursor() as cur:
        cur.execute("SELECT version, source_id FROM character_baselines "
                    "WHERE character_id = %s ORDER BY version DESC LIMIT 1", (character_id,))
        return cur.fetchone()


def _upsert(conn, record: OfficeProfile, campaign: str) -> str:
    return characters.upsert_character(
        conn, slug=record.slug, name=record.name, campaign=campaign,
        retains_fragments=record.retains_fragments, is_main=record.is_main,
        aliases=record.aliases, avatar_params=record.avatar_params, status="active")


def _decide(conn, record: OfficeProfile, activate: bool):
    row = characters.get_character(conn, record.slug)
    if row is None:
        return "created", 1, None
    latest = _latest_baseline(conn, row["id"])
    if latest is None or latest[1] != record.source_id:
        return "updated", (latest[0] + 1 if latest else 1), latest
    if activate and row["active_baseline_version"] != latest[0]:
        return "activated", latest[0], latest
    if row["avatar_params"] != record.avatar_params:
        return "avatar", latest[0], latest
    return "unchanged", latest[0], latest


def load_profiles(conn, profiles_dir, cast_dir=None, *, campaign, accent_colors=None,
                  dry_run=False, activate=True) -> LoadReport:
    """Load the office profiles into the database (never commits)."""
    records = read_profiles(profiles_dir, cast_dir, accent_colors)
    actions: dict[str, str] = {}
    versions: dict[str, int] = {}
    for record in records:
        action, version, _ = _decide(conn, record, activate)
        log.debug("office profile decision slug=%s action=%s version=%s dry_run=%s",
                  record.slug, action, version, dry_run)
        if not dry_run:
            if action in ("created", "updated"):
                cid = _upsert(conn, record, campaign)
                version = characters.insert_baseline(
                    conn, cid, profile=record.profile, baseline_book=BASELINE_BOOK,
                    baseline_chapter=BASELINE_CHAPTER,
                    backstory_nodes=list(record.backstory_nodes),
                    source_id=record.source_id, created_by=CREATED_BY)
                characters.insert_backstory(conn, cid, version, "believed",
                                            {"text": record.believed})
                characters.insert_backstory(conn, cid, version, "truth", {"text": record.truth})
                for agent_id in record.agent_ids:
                    characters.add_agent(conn, agent_id, cid)
                if activate:
                    characters.set_active_baseline(conn, record.slug, version)
            elif action == "activated":
                characters.set_active_baseline(conn, record.slug, version)
            elif action == "avatar":
                _upsert(conn, record, campaign)
        actions[record.slug] = action
        versions[record.slug] = version
    counts = Counter(actions.values())
    log.info("office profiles loaded campaign=%s dry_run=%s actions=%s",
             campaign, dry_run, dict(sorted(counts.items())))
    return LoadReport(actions=actions, versions=versions, dry_run=dry_run)
