"""Character v4 configuration: config/character.yaml -> a frozen dataclass tree.

Plan docs/charcterProfileGenerationNotes/character_generator_updater_v4.md §11.
Every v4 job, the ingest consumer and the live driver start with `load()`.
Validation fails early with `ConfigError(key, reason)` naming the dotted key
(without the leading "character.").

Secrets: the database password comes ONLY from the environment
(CHARACTER_DB_PASSWORD / CHARACTER_INGEST_DB_PASSWORD) and is excluded from
every repr. A missing password is not a load error; `db.connect()` raises
when a job actually needs it.
"""
from __future__ import annotations

import logging
import math
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

log = logging.getLogger(__name__)

DEFAULT_PATH = Path(__file__).resolve().parents[2] / "config" / "character.yaml"

_SLUG_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_REFRESH_MODES = ("push", "none")
_WEIGHT_TOLERANCE = 1e-6
_SUNDAY = 6
_MISSING = object()


class ConfigError(Exception):
    """A config validation failure; `key` is the dotted path (no "character.")."""

    def __init__(self, key: str, reason: str):
        self.key = key
        self.reason = reason
        super().__init__(f"{key}: {reason}")

    def __str__(self) -> str:
        return f"{self.key}: {self.reason}"


# ── dataclasses ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class DbConfig:
    host: str
    port: int
    dbname: str
    user: str
    password: str | None = field(default=None, repr=False)


@dataclass(frozen=True)
class LoopConfig:
    epoch: date
    fragments_per_week: int


@dataclass(frozen=True)
class Position:
    book: int
    chapter: int


@dataclass(frozen=True)
class SourceConfig:
    id: str
    config: str
    books: tuple[int, ...]
    story_start: Position
    story_end: Position


@dataclass(frozen=True)
class CharacterEntry:
    slug: str
    retains_fragments: bool = False
    is_main: bool = False
    accent_color: str | None = None
    entry_pos: Position | None = None


@dataclass(frozen=True)
class LLMProfile:
    model: str
    temperature: float
    num_ctx: int
    timeout_s: int
    think: bool


@dataclass(frozen=True)
class LLMConfig:
    base_url: str
    profiles: dict[str, LLMProfile]
    max_retries: int


@dataclass(frozen=True)
class IngestConfig:
    kafka_topic: str
    consumer_group: str
    types: tuple[str, ...]
    batch_max: int
    batch_wait_s: float
    catchup_wait_s: int
    max_lag_messages: int


@dataclass(frozen=True)
class CompactionConfig:
    noisy_types: tuple[str, ...]
    noisy_keep_hours: int
    hot_keep_days: int
    move_batch: int
    partitions_ahead_days: int


@dataclass(frozen=True)
class BriefConfig:
    max_chars: int
    cache_ttl_s: int
    week_knowledge_max: int


@dataclass(frozen=True)
class EmbeddingsConfig:
    base_url: str
    model: str
    dim: int


@dataclass(frozen=True)
class RecallWeights:
    hooks: float
    trajectory: float
    gist: float


@dataclass(frozen=True)
class RecallThresholds:
    unease: float
    surface: float


@dataclass(frozen=True)
class RecallConfig:
    embeddings: EmbeddingsConfig
    lead_up_beats: int
    window_beats: int
    bias: float
    ref_cosine: float
    gap: float
    weights: RecallWeights
    thresholds: RecallThresholds
    decay: float
    spread: float
    cooldown_beats: int


@dataclass(frozen=True)
class ResetConfig:
    refresh_mode: str


@dataclass(frozen=True)
class TestctlConfig:
    __test__ = False  # not a pytest test class

    enabled: bool


@dataclass(frozen=True)
class CharacterConfig:
    campaign: str
    timezone: str
    loop: LoopConfig
    db: DbConfig
    ingest_db: DbConfig
    messages_db_env_prefix: str
    source: SourceConfig | None
    pack: str
    characters: dict[str, CharacterEntry]
    llm: LLMConfig
    ingest: IngestConfig
    compaction: CompactionConfig
    brief: BriefConfig
    recall: RecallConfig
    reset: ResetConfig
    testctl: TestctlConfig


# ── typed readers ────────────────────────────────────────────────────────


def _fail(key: str, reason: str) -> ConfigError:
    log.error("character config invalid key=%s reason=%s", key, reason)
    return ConfigError(key, reason)


def _join(prefix: str, name: str) -> str:
    return f"{prefix}.{name}" if prefix else name


def _get(node: Mapping, prefix: str, name: str, default=_MISSING):
    key = _join(prefix, name)
    if not isinstance(node, Mapping):
        raise _fail(prefix or "character", f"expected mapping, got {type(node).__name__}")
    if name not in node or node[name] is None:
        if default is _MISSING:
            raise _fail(key, "missing")
        return default
    return node[name]


def _str(node, prefix, name, default=_MISSING):
    value = _get(node, prefix, name, default)
    if value is default and default is not _MISSING:
        return value
    if not isinstance(value, str):
        raise _fail(_join(prefix, name), f"expected str, got {type(value).__name__}")
    return value


def _int(node, prefix, name, default=_MISSING):
    value = _get(node, prefix, name, default)
    if value is default and default is not _MISSING:
        return value
    if isinstance(value, bool) or not isinstance(value, int):
        raise _fail(_join(prefix, name), f"expected int, got {type(value).__name__}")
    return value


def _float(node, prefix, name, default=_MISSING):
    value = _get(node, prefix, name, default)
    if value is default and default is not _MISSING:
        return value
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _fail(_join(prefix, name), f"expected float, got {type(value).__name__}")
    if not math.isfinite(value):
        raise _fail(_join(prefix, name), "expected a finite number")
    return float(value)


def _bool(node, prefix, name, default=_MISSING):
    value = _get(node, prefix, name, default)
    if value is default and default is not _MISSING:
        return value
    if not isinstance(value, bool):
        raise _fail(_join(prefix, name), f"expected bool, got {type(value).__name__}")
    return value


def _map(node, prefix, name, default=_MISSING):
    value = _get(node, prefix, name, default)
    if value is default and default is not _MISSING:
        return value
    if not isinstance(value, Mapping):
        raise _fail(_join(prefix, name), f"expected mapping, got {type(value).__name__}")
    return value


def _list(node, prefix, name, item_type):
    key = _join(prefix, name)
    value = _get(node, prefix, name)
    if not isinstance(value, list):
        raise _fail(key, f"expected list, got {type(value).__name__}")
    for index, item in enumerate(value):
        bad = isinstance(item, bool) and item_type is int
        if bad or not isinstance(item, item_type):
            raise _fail(f"{key}[{index}]",
                        f"expected {item_type.__name__}, got {type(item).__name__}")
    return tuple(value)


# ── sections ─────────────────────────────────────────────────────────────


def _position(node, prefix) -> Position:
    return Position(book=_int(node, prefix, "book"), chapter=_int(node, prefix, "chapter"))


def _loop(root) -> LoopConfig:
    loop = _map(root, "", "loop")
    raw = _get(loop, "loop", "epoch")
    if isinstance(raw, datetime):
        raise _fail("loop.epoch", "expected a date, got a datetime")
    if isinstance(raw, date):
        epoch = raw
    elif isinstance(raw, str):
        try:
            epoch = date.fromisoformat(raw)
        except ValueError:
            raise _fail("loop.epoch", f"not an ISO date (YYYY-MM-DD): {raw!r}") from None
    else:
        raise _fail("loop.epoch", f"expected date, got {type(raw).__name__}")
    if epoch.weekday() != _SUNDAY:
        raise _fail("loop.epoch", f"must be a Sunday, got {epoch} ({epoch:%A})")
    per_week = _int(loop, "loop", "fragments_per_week")
    if per_week < 0:
        raise _fail("loop.fragments_per_week", f"must be >= 0, got {per_week}")
    return LoopConfig(epoch=epoch, fragments_per_week=per_week)


def _env(env: Mapping, name: str) -> str | None:
    value = env.get(name)
    return value if value else None  # empty string counts as unset


def _env_port(env: Mapping, name: str, key: str) -> int | None:
    raw = _env(env, name)
    if raw is None:
        return None
    try:
        return int(raw.strip())
    except ValueError:
        raise _fail(key, f"{name} is not an int") from None


def _db(root, env: Mapping) -> tuple[DbConfig, DbConfig]:
    node = _map(root, "", "db")
    host = _str(node, "db", "host")
    port = _int(node, "db", "port")
    dbname = _str(node, "db", "dbname")
    user = _str(node, "db", "user")
    password = None
    overrides = {}
    for env_name, attr in (("CHARACTER_DB_HOST", "host"), ("CHARACTER_DB_NAME", "dbname"),
                           ("CHARACTER_DB_USER", "user"), ("CHARACTER_DB_PASSWORD", "password")):
        value = _env(env, env_name)
        if value is not None:
            overrides[attr] = value
            log.debug("character config override db.%s from %s", attr, env_name)
    env_port = _env_port(env, "CHARACTER_DB_PORT", "db.port")
    if env_port is not None:
        port = env_port
        log.debug("character config override db.port from CHARACTER_DB_PORT")
    main = DbConfig(host=overrides.get("host", host), port=port,
                    dbname=overrides.get("dbname", dbname), user=overrides.get("user", user),
                    password=overrides.get("password", password))

    ingest = {}
    for env_name, attr in (("CHARACTER_INGEST_DB_HOST", "host"),
                           ("CHARACTER_INGEST_DB_NAME", "dbname"),
                           ("CHARACTER_INGEST_DB_USER", "user"),
                           ("CHARACTER_INGEST_DB_PASSWORD", "password")):
        value = _env(env, env_name)
        if value is not None:
            ingest[attr] = value
            log.debug("character config override ingest_db.%s from %s", attr, env_name)
    ingest_port = _env_port(env, "CHARACTER_INGEST_DB_PORT", "ingest_db.port")
    if ingest_port is not None:
        log.debug("character config override ingest_db.port from CHARACTER_INGEST_DB_PORT")
    ingest_db = DbConfig(host=ingest.get("host", main.host),
                         port=ingest_port if ingest_port is not None else main.port,
                         dbname=ingest.get("dbname", main.dbname),
                         user=ingest.get("user", main.user),
                         password=ingest.get("password", main.password))
    return main, ingest_db


def _source(root) -> SourceConfig | None:
    node = _map(root, "", "source", None)
    if node is None:
        return None
    scope = _map(node, "source", "scope")
    return SourceConfig(
        id=_str(node, "source", "id"),
        config=_str(node, "source", "config"),
        books=_list(scope, "source.scope", "books", int),
        story_start=_position(_map(node, "source", "story_start"), "source.story_start"),
        story_end=_position(_map(node, "source", "story_end"), "source.story_end"),
    )


def _characters(root) -> dict[str, CharacterEntry]:
    node = _get(root, "", "characters")
    if not isinstance(node, Mapping) or not node:
        raise _fail("characters", "must be a non-empty mapping of slug -> settings")
    entries = {}
    for slug, settings in node.items():
        if not isinstance(slug, str) or not _SLUG_RE.match(slug):
            raise _fail("characters", f"bad slug {slug!r} (must match {_SLUG_RE.pattern})")
        prefix = f"characters.{slug}"
        settings = settings if settings is not None else {}
        if not isinstance(settings, Mapping):
            raise _fail(prefix, f"expected mapping, got {type(settings).__name__}")
        entry_node = _map(settings, prefix, "entry_pos", None)
        entries[slug] = CharacterEntry(
            slug=slug,
            retains_fragments=_bool(settings, prefix, "retains_fragments", False),
            is_main=_bool(settings, prefix, "is_main", False),
            accent_color=_str(settings, prefix, "accent_color", None),
            entry_pos=(_position(entry_node, f"{prefix}.entry_pos")
                       if entry_node is not None else None),
        )
    return entries


def _llm(root) -> LLMConfig:
    node = _map(root, "", "llm")
    raw_profiles = _get(node, "llm", "profiles")
    if not isinstance(raw_profiles, Mapping) or not raw_profiles:
        raise _fail("llm.profiles", "must be a non-empty mapping of name -> profile")
    profiles = {}
    for name, prof in raw_profiles.items():
        prefix = f"llm.profiles.{name}"
        if not isinstance(prof, Mapping):
            raise _fail(prefix, f"expected mapping, got {type(prof).__name__}")
        for required in ("model", "temperature", "num_ctx", "timeout_s", "think"):
            if required not in prof or prof[required] is None:
                raise _fail(f"{prefix}.{required}", "missing")
        profiles[str(name)] = LLMProfile(
            model=_str(prof, prefix, "model"),
            temperature=_float(prof, prefix, "temperature"),
            num_ctx=_int(prof, prefix, "num_ctx"),
            timeout_s=_int(prof, prefix, "timeout_s"),
            think=_bool(prof, prefix, "think"),
        )
    return LLMConfig(base_url=_str(node, "llm", "base_url"), profiles=profiles,
                     max_retries=_int(node, "llm", "max_retries"))


def _ingest(root) -> IngestConfig:
    node, p = _map(root, "", "ingest"), "ingest"
    return IngestConfig(
        kafka_topic=_str(node, p, "kafka_topic"),
        consumer_group=_str(node, p, "consumer_group"),
        types=_list(node, p, "types", str),
        batch_max=_int(node, p, "batch_max"),
        batch_wait_s=_float(node, p, "batch_wait_s"),
        catchup_wait_s=_int(node, p, "catchup_wait_s"),
        max_lag_messages=_int(node, p, "max_lag_messages"),
    )


def _compaction(root) -> CompactionConfig:
    node, p = _map(root, "", "compaction"), "compaction"
    return CompactionConfig(
        noisy_types=_list(node, p, "noisy_types", str),
        noisy_keep_hours=_int(node, p, "noisy_keep_hours"),
        hot_keep_days=_int(node, p, "hot_keep_days"),
        move_batch=_int(node, p, "move_batch"),
        partitions_ahead_days=_int(node, p, "partitions_ahead_days"),
    )


def _brief(root) -> BriefConfig:
    node, p = _map(root, "", "brief"), "brief"
    return BriefConfig(max_chars=_int(node, p, "max_chars"),
                       cache_ttl_s=_int(node, p, "cache_ttl_s"),
                       week_knowledge_max=_int(node, p, "week_knowledge_max"))


def _recall(root) -> RecallConfig:
    node, p = _map(root, "", "recall"), "recall"
    emb = _map(node, p, "embeddings")
    w_node = _map(node, p, "weights")
    weights = RecallWeights(hooks=_float(w_node, "recall.weights", "hooks"),
                            trajectory=_float(w_node, "recall.weights", "trajectory"),
                            gist=_float(w_node, "recall.weights", "gist"))
    total = weights.hooks + weights.trajectory + weights.gist
    if abs(total - 1.0) > _WEIGHT_TOLERANCE:
        raise _fail("recall.weights", f"hooks + trajectory + gist must sum to 1.0, got {total:.6f}")
    t_node = _map(node, p, "thresholds")
    thresholds = RecallThresholds(unease=_float(t_node, "recall.thresholds", "unease"),
                                  surface=_float(t_node, "recall.thresholds", "surface"))
    if not 0 < thresholds.unease < thresholds.surface <= 1:
        raise _fail("recall.thresholds",
                    f"need 0 < unease < surface <= 1, got unease={thresholds.unease} "
                    f"surface={thresholds.surface}")
    return RecallConfig(
        embeddings=EmbeddingsConfig(base_url=_str(emb, "recall.embeddings", "base_url"),
                                    model=_str(emb, "recall.embeddings", "model"),
                                    dim=_int(emb, "recall.embeddings", "dim")),
        lead_up_beats=_int(node, p, "lead_up_beats"),
        window_beats=_int(node, p, "window_beats"),
        bias=_float(node, p, "bias"),
        ref_cosine=_float(node, p, "ref_cosine"),
        gap=_float(node, p, "gap"),
        weights=weights,
        thresholds=thresholds,
        decay=_float(node, p, "decay"),
        spread=_float(node, p, "spread"),
        cooldown_beats=_int(node, p, "cooldown_beats"),
    )


def _reset(root) -> ResetConfig:
    node = _map(root, "", "reset")
    mode = _str(node, "reset", "refresh_mode")
    if mode not in _REFRESH_MODES:
        raise _fail("reset.refresh_mode", f"must be one of {list(_REFRESH_MODES)}, got {mode!r}")
    return ResetConfig(refresh_mode=mode)


def _timezone(root) -> str:
    name = _str(root, "", "timezone")
    try:
        ZoneInfo(name)
    except Exception:  # ZoneInfoNotFoundError, ValueError on malformed keys
        raise _fail("timezone", f"unknown time zone {name!r}") from None
    return name


# ── entry point ──────────────────────────────────────────────────────────


def _read_document(path: Path) -> Mapping:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise _fail("character", f"cannot read {path}: {exc.strerror or exc}") from None
    try:
        doc = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise _fail("character", f"bad YAML in {path}: {exc}") from None
    if not isinstance(doc, Mapping) or not isinstance(doc.get("character"), Mapping):
        raise _fail("character", f"{path} has no top-level 'character' mapping")
    return doc["character"]


def load(path=None, env: Mapping = os.environ) -> CharacterConfig:
    """Load and validate config/character.yaml (or `path`), applying env overrides."""
    path = Path(path) if path is not None else DEFAULT_PATH
    log.debug("loading character config path=%s", path)
    root = _read_document(path)
    db, ingest_db = _db(root, env)
    cfg = CharacterConfig(
        campaign=_str(root, "", "campaign"),
        timezone=_timezone(root),
        loop=_loop(root),
        db=db,
        ingest_db=ingest_db,
        messages_db_env_prefix=_str(root, "", "messages_db_env_prefix"),
        source=_source(root),
        pack=_str(root, "", "pack"),
        characters=_characters(root),
        llm=_llm(root),
        ingest=_ingest(root),
        compaction=_compaction(root),
        brief=_brief(root),
        recall=_recall(root),
        reset=_reset(root),
        testctl=TestctlConfig(enabled=_bool(_map(root, "", "testctl"), "testctl", "enabled")),
    )
    log.debug("loaded character config path=%s campaign=%s characters=%d",
              path, cfg.campaign, len(cfg.characters))
    return cfg
