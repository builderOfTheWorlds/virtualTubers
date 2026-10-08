"""Reduced seat brief for the live agent table (build plan P2.6, U3, U6).

`build_table_brief(conn, slug, *, lore, unlocked=(), max_chars=6000)` renders a
player's context from the character_profile DB:

    # Who you are           identity, personality, speech
    # What you remember     the BELIEVED backstory layer (first person)
    # What you want         objectives wants / fears (+ table wants/fears)
    # What you know         lore text of the character's knowledge stems + the
                            session unlocks the arbiter passes in; relationships
    # How you play          the profile's behaviour contract + the table contract

This is deliberately NOT app/character/brief.py: that module is the full v4
weekly-loop brief (WP-22, frozen tests, Phase 8). This one reads only the
active baseline's profile JSON and the believed layer. It never selects the
truth layer or gm_blocks (the *_SQL constants are checked by a test), so a
player's context cannot contain the truth by construction, not by prompt.

Cap, in order: (1) shorten the believed backstory on a word boundary down to
BELIEVED_FLOOR chars; (2) drop knowledge lore oldest-first (start knowledge
before session unlocks, which are the newest and most scene-relevant);
(3) shorten the believed text further; (4) drop the relationships list. The
behaviour contract is never cut. (The D&D believed texts are ~3k chars, so
without step 1 every lore block would be dropped and "What you know" would be
empty: found when testing against the P2.5 drafts.)
"""
from __future__ import annotations

import logging
from typing import Mapping, Sequence

log = logging.getLogger(__name__)

BELIEVED_FLOOR = 1500

SECTION_HEADINGS = ("# Who you are", "# What you remember", "# What you want",
                    "# What you know", "# How you play")

TABLE_CONTRACT = (
    "Stay in character, first person. Never speak for another character.",
    "\"Yes, and\" the GM: accept what the GM describes and add your own angle.",
    "Spoken words only: no *actions*, no (asides), no narration of what you do.",
    "One short line at a time, the way you would say it out loud at the table.",
)

PROFILE_SQL = (
    "SELECT c.id, c.name, b.version, b.profile FROM characters c "
    "JOIN character_baselines b ON b.character_id = c.id "
    "AND b.version = c.active_baseline_version WHERE c.slug = %s")
BELIEVED_SQL = (
    "SELECT content FROM character_backstories "
    "WHERE character_id = %s AND version = %s AND layer = 'believed'")


def _load(conn, slug):
    with conn.cursor() as cur:
        cur.execute(PROFILE_SQL, (slug,))
        row = cur.fetchone()
        if row is None:
            raise LookupError(f"no active baseline for character {slug!r}")
        character_id, name, version, profile = row
        cur.execute(BELIEVED_SQL, (character_id, version))
        believed_row = cur.fetchone()
    believed = ((believed_row[0] or {}).get("text") if believed_row else "") or ""
    return name, profile or {}, believed


def _lines(items):
    return "\n".join(f"- {item}" for item in items if item)


def _who(name, profile):
    ident = profile.get("identity") or {}
    bits = [f"You are {ident.get('full_name') or name}"]
    if ident.get("title"):
        bits[0] += f", {ident['title']}"
    if isinstance(ident.get("age"), int):
        bits.append(f"Age {ident['age']}.")
    if ident.get("pronouns"):
        bits.append(f"Pronouns: {ident['pronouns']}.")
    if ident.get("home"):
        bits.append(f"Home: {ident['home']}.")
    out = [" ".join([bits[0] + "."] + bits[1:])]
    pers = profile.get("personality") or {}
    if pers.get("traits"):
        out.append("Traits: " + ", ".join(pers["traits"]) + ".")
    if pers.get("stress_response"):
        out.append("Under stress: " + pers["stress_response"])
    if profile.get("speech"):
        out.append("How you talk: " + " ".join(str(profile["speech"]).split()))
    if pers.get("speech_tics"):
        out.append("Tics: " + "; ".join(pers["speech_tics"]) + ".")
    return "\n".join(out)


def _wants(profile):
    obj = profile.get("objectives") or {}
    wants = list(dict.fromkeys((obj.get("wants") or []) + (profile.get("wants") or [])))
    fears = list(dict.fromkeys((obj.get("fears") or []) + (profile.get("fears") or [])))
    parts = []
    if wants:
        parts.append("You want:\n" + _lines(wants))
    if fears:
        parts.append("You fear:\n" + _lines(fears))
    if obj.get("secrets"):
        parts.append("You keep to yourself:\n" + _lines(obj["secrets"]))
    return "\n".join(parts)


def _relationships(profile):
    rel = profile.get("relationships") or {}
    return _lines(f"{who}: {view}" for who, view in rel.items()) if rel else ""


def _behaviour(profile):
    return _lines(list(profile.get("behaviour_contract") or []) + list(TABLE_CONTRACT))


def _truncate_words(text, limit):
    if len(text) <= limit:
        return text
    if limit <= 1:
        return ""
    cut = text[:limit - 1].rsplit(" ", 1)[0]
    return cut + "…"


def _render(who, believed, wants, lore_blocks, rel, behaviour):
    know = "\n\n".join(lore_blocks)
    if rel:
        know = (know + "\n\nThe people at the table:\n" + rel) if know else (
            "The people at the table:\n" + rel)
    sections = [who, believed, wants, know or "(nothing beyond what you remember)", behaviour]
    return "\n\n".join(f"{head}\n{body}" for head, body in zip(SECTION_HEADINGS, sections))


def build_table_brief(conn, slug, *, lore: Mapping[str, str], unlocked: Sequence[str] = (),
                      max_chars: int = 6000) -> str:
    """The seat's context (<= max_chars). LookupError: unknown slug. ValueError: the GM."""
    name, profile, believed = _load(conn, slug)
    if profile.get("table_role") == "gm":
        raise ValueError("the GM has no seat brief; use table.gm_context.build_gm_context")
    start = [s for s in (profile.get("knowledge") or []) if isinstance(s, str)]
    extra = [s for s in unlocked if s not in start]
    missing = [s for s in start + extra if s not in lore]
    if missing:
        log.debug("seat brief slug=%s lore stems without text: %s", slug, missing)
    # oldest first, so trimming from the front drops start knowledge before unlocks
    blocks = [f"## {s}\n{lore[s].strip()}" for s in start + extra if s in lore]

    who, wants, rel, behaviour = _who(name, profile), _wants(profile), _relationships(profile), \
        _behaviour(profile)
    believed = " ".join(believed.split()) if believed else "(you do not dwell on your past)"

    text = _render(who, believed, wants, blocks, rel, behaviour)
    if len(text) > max_chars and len(believed) > BELIEVED_FLOOR:
        over = len(text) - max_chars
        believed = _truncate_words(believed, max(BELIEVED_FLOOR, len(believed) - over))
        text = _render(who, believed, wants, blocks, rel, behaviour)
    while len(text) > max_chars and blocks:
        blocks.pop(0)
        text = _render(who, believed, wants, blocks, rel, behaviour)
    if len(text) > max_chars:
        fixed = len(_render(who, "", wants, blocks, rel, behaviour))
        believed = _truncate_words(believed, max(0, max_chars - fixed))
        text = _render(who, believed, wants, blocks, rel, behaviour)
    if len(text) > max_chars:
        rel = ""
        text = _render(who, believed, wants, blocks, rel, behaviour)
    if len(text) > max_chars:
        log.error("seat brief slug=%s cannot fit max_chars=%d (len=%d)", slug, max_chars, len(text))
        raise ValueError(f"seat brief for {slug!r} cannot fit in {max_chars} chars")
    log.debug("seat brief slug=%s chars=%d lore=%d unlocked=%d", slug, len(text), len(blocks),
              len(extra))
    return text
