"""Characters, bus agent ids, versioned baselines/backstories and the
active-baseline pointer (plan §4, §6).

Baselines are versioned and never modified by the loop: a new generation
writes version N+1 and "revert" only moves `characters.active_baseline_version`.
`set_active_baseline` refuses a version that does not exist, and
`upsert_character` never touches the pointer (nor status on conflict).

Store convention: functions never commit or roll back; the caller owns the
transaction.
"""
from __future__ import annotations

import logging
import uuid

from psycopg2.extras import Json

log = logging.getLogger(__name__)


def _row(cur) -> dict | None:
    row = cur.fetchone()
    if row is None:
        return None
    return {col.name: value for col, value in zip(cur.description, row)}


def upsert_character(conn, *, slug, name, campaign, retains_fragments=False, is_main=False,
                     aliases=(), avatar_params=None, status="draft") -> str:
    """Insert or update a character by slug; return its id (stable across upserts)."""
    log.debug("upsert_character slug=%s campaign=%s", slug, campaign)
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO characters (id, slug, name, campaign, status, retains_fragments, "
            "is_main, aliases, avatar_params) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) "
            "ON CONFLICT (slug) DO UPDATE SET name = EXCLUDED.name, "
            "campaign = EXCLUDED.campaign, retains_fragments = EXCLUDED.retains_fragments, "
            "is_main = EXCLUDED.is_main, aliases = EXCLUDED.aliases, "
            "avatar_params = EXCLUDED.avatar_params, updated_at = now() "
            "RETURNING id",
            (str(uuid.uuid4()), slug, name, campaign, status, bool(retains_fragments),
             bool(is_main), list(aliases),
             Json(avatar_params) if avatar_params is not None else None))
        character_id = cur.fetchone()[0]
    log.debug("upsert_character slug=%s id=%s", slug, character_id)
    return character_id


def get_character(conn, slug) -> dict | None:
    """Every column of the characters row for `slug`, or None."""
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM characters WHERE slug = %s", (slug,))
        row = _row(cur)
    log.debug("get_character slug=%s found=%s", slug, row is not None)
    return row


def add_agent(conn, agent_id, character_id) -> None:
    """Map a bus agent id to a character (re-adding is harmless)."""
    log.debug("add_agent agent_id=%s character_id=%s", agent_id, character_id)
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO character_agents (agent_id, character_id) VALUES (%s, %s) "
            "ON CONFLICT (agent_id) DO UPDATE SET character_id = EXCLUDED.character_id",
            (agent_id, character_id))


def agents_map(conn) -> dict[str, str]:
    """{agent_id: character_id} for every character_agents row."""
    with conn.cursor() as cur:
        cur.execute("SELECT agent_id, character_id FROM character_agents")
        mapping = {agent_id: character_id for agent_id, character_id in cur.fetchall()}
    log.debug("agents_map count=%d", len(mapping))
    return mapping


def insert_baseline(conn, character_id, *, profile, baseline_book, baseline_chapter,
                    backstory_nodes=(), source_id=None, change_notes=None,
                    created_by="generator", llm_model=None, raw_response=None) -> int:
    """Write baseline version N+1 for the character; return N+1. Pointer unchanged."""
    log.debug("insert_baseline character_id=%s", character_id)
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO character_baselines (character_id, version, profile, "
            "backstory_nodes, baseline_book, baseline_chapter, source_id, change_notes, "
            "created_by, llm_model, raw_response) "
            "VALUES (%s, (SELECT coalesce(max(version), 0) + 1 FROM character_baselines "
            "WHERE character_id = %s), %s, %s, %s, %s, %s, %s, %s, %s, %s) "
            "RETURNING version",
            (character_id, character_id, Json(profile), Json(list(backstory_nodes)),
             baseline_book, baseline_chapter, source_id, change_notes, created_by,
             llm_model, raw_response))
        version = cur.fetchone()[0]
    log.debug("insert_baseline character_id=%s version=%s", character_id, version)
    return version


def insert_backstory(conn, character_id, version, layer, content, evidence=(),
                     llm_model=None) -> None:
    """Insert one backstory layer ('believed' | 'truth'); duplicates raise IntegrityError."""
    log.debug("insert_backstory character_id=%s version=%s layer=%s",
              character_id, version, layer)
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO character_backstories (character_id, version, layer, content, "
            "evidence, llm_model) VALUES (%s, %s, %s, %s, %s, %s)",
            (character_id, version, layer, Json(content), Json(list(evidence)), llm_model))


def set_active_baseline(conn, slug, version) -> None:
    """Move the pointer to an existing baseline version; LookupError otherwise."""
    log.debug("set_active_baseline slug=%s version=%s", slug, version)
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE characters c SET active_baseline_version = %s, updated_at = now() "
            "WHERE c.slug = %s AND EXISTS (SELECT 1 FROM character_baselines b "
            "WHERE b.character_id = c.id AND b.version = %s)",
            (version, slug, version))
        updated = cur.rowcount
    if updated == 0:
        log.error("set_active_baseline: no baseline version=%s for slug=%s", version, slug)
        raise LookupError(f"no baseline version {version} for {slug!r}")
    log.debug("set_active_baseline slug=%s version=%s done", slug, version)


def active_baseline(conn, slug) -> dict | None:
    """The character_baselines row the pointer names, or None."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT b.* FROM characters c JOIN character_baselines b "
            "ON b.character_id = c.id AND b.version = c.active_baseline_version "
            "WHERE c.slug = %s", (slug,))
        row = _row(cur)
    log.debug("active_baseline slug=%s found=%s", slug, row is not None)
    return row
