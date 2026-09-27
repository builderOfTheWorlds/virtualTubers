"""
office/brief_stub.py
The E6 stub brief for live office agents: the persona prompt every office
handler sends to the LLM is built here, from three parts only:

  1. the cast `system_prompt`       campaigns/ashiorid_office/cast/<id>.yaml
  2. the `backstory.believed` text  campaigns/ashiorid_office/profiles/<id>.yaml
  3. today's directive              (passed in by the caller)

It is replaced by the v4 `brief.py` once the memory DB (WS-F) lands; keep
the one public entry point, `build_persona_prompt`, stable so that swap is a
one-import change in app/agent_handlers/office.py.

The pack directory is injectable (argument > env OFFICE_PACK_DIR > the
repo-relative default) so tests and containers never depend on where the
repo is checked out. Pure apart from reading those two YAML files.
See docs/brief_stub.md.
"""
import logging
import os
from pathlib import Path

import yaml

from office.roles import as_role

log = logging.getLogger(__name__)
TRACE = 5
logging.addLevelName(TRACE, "TRACE")

PACK_DIR_ENV = "OFFICE_PACK_DIR"
#: <repo>/campaigns/ashiorid_office (this file is <repo>/app/office/brief_stub.py).
DEFAULT_PACK_DIR = Path(__file__).resolve().parents[2] / "campaigns" / "ashiorid_office"

BACKSTORY_HEADING = "## Your life, as you remember it"
DIRECTIVE_HEADING = "## Today's directive"
NO_DIRECTIVE_TEXT = "The CEO has not issued a directive yet today."


def _trace(msg, *args):
    if log.isEnabledFor(TRACE):
        log.log(TRACE, msg, *args)


class BriefError(RuntimeError):
    """The persona prompt cannot be built (missing/unreadable cast file,
    or a cast file with no system_prompt)."""


def resolve_pack_dir(pack_dir=None):
    """Argument > env OFFICE_PACK_DIR > DEFAULT_PACK_DIR, as a Path."""
    chosen = pack_dir or os.environ.get(PACK_DIR_ENV) or DEFAULT_PACK_DIR
    path = Path(chosen)
    _trace("resolve_pack_dir exit pack_dir=%s", path)
    return path


def character_id(role):
    """The cast/profile file id for `role` (an OfficeRole, role value or seat).
    Ids are the OfficeRole values: 'tech_lead' -> cast/tech_lead.yaml."""
    return as_role(role).value


def _read_yaml(path, required):
    log.debug("brief_stub read event=enter path=%s required=%s", path, required)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
    except FileNotFoundError:
        if required:
            log.error("brief_stub read event=missing path=%s", path)
            raise BriefError(f"character file not found: {path}") from None
        log.warning("brief_stub read event=missing_optional path=%s", path)
        return {}
    except (OSError, yaml.YAMLError) as exc:
        log.error("brief_stub read event=unreadable path=%s error=%s", path, exc)
        if required:
            raise BriefError(f"character file unreadable: {path}: {exc}") from exc
        return {}
    if data is None:
        data = {}
    if not isinstance(data, dict):
        log.error("brief_stub read event=not_a_mapping path=%s", path)
        if required:
            raise BriefError(f"character file is not a mapping: {path}")
        return {}
    return data


def load_character(role, pack_dir=None):
    """{"id", "cast", "profile"} for `role`. The cast file is required; a
    missing profile degrades to {} (the brief then has no backstory)."""
    _trace("load_character enter role=%r pack_dir=%r", role, pack_dir)
    cid = character_id(role)
    root = resolve_pack_dir(pack_dir)
    cast = _read_yaml(root / "cast" / f"{cid}.yaml", required=True)
    profile = _read_yaml(root / "profiles" / f"{cid}.yaml", required=False)
    log.debug("brief_stub load_character event=done id=%s cast_keys=%d profile_keys=%d",
              cid, len(cast), len(profile))
    return {"id": cid, "cast": cast, "profile": profile}


def _believed(profile):
    backstory = profile.get("backstory") if isinstance(profile, dict) else None
    text = backstory.get("believed") if isinstance(backstory, dict) else None
    return text.strip() if isinstance(text, str) and text.strip() else None


def build_persona_prompt(role, directive=None, pack_dir=None, max_backstory_chars=None):
    """The persona system prompt for `role`:

        <cast system_prompt>

        ## Your life, as you remember it
        <profile backstory.believed>          (omitted when absent)

        ## Today's directive
        <directive, or NO_DIRECTIVE_TEXT>

    `directive` is a string or a dict with "text" (and optional "title" /
    "issue"). `max_backstory_chars` truncates the believed backstory on a
    word boundary (None = whole text). Raises BriefError when the cast file
    is missing or has no system_prompt; the Party Member gets a brief too
    (the observer handlers simply never send it to an LLM).
    """
    _trace("build_persona_prompt enter role=%r has_directive=%s", role, bool(directive))
    character = load_character(role, pack_dir)
    system_prompt = character["cast"].get("system_prompt")
    if not isinstance(system_prompt, str) or not system_prompt.strip():
        log.error("brief_stub event=no_system_prompt id=%s", character["id"])
        raise BriefError(f"cast/{character['id']}.yaml has no system_prompt")

    parts = [system_prompt.strip()]
    believed = _believed(character["profile"])
    if believed:
        if max_backstory_chars and len(believed) > max_backstory_chars:
            cut = believed[:max_backstory_chars].rsplit(" ", 1)[0]
            log.debug("brief_stub event=backstory_truncated id=%s from=%d to=%d",
                      character["id"], len(believed), len(cut))
            believed = cut + " ..."
        parts.append(f"{BACKSTORY_HEADING}\n{believed}")
    else:
        log.debug("brief_stub event=no_backstory id=%s", character["id"])

    parts.append(f"{DIRECTIVE_HEADING}\n{_directive_text(directive)}")
    prompt = "\n\n".join(parts)
    log.info("brief_stub event=built id=%s chars=%d backstory=%s directive=%s",
             character["id"], len(prompt), bool(believed), bool(directive))
    return prompt


def _directive_text(directive):
    if isinstance(directive, dict):
        text = (directive.get("text") or "").strip()
        if not text:
            return NO_DIRECTIVE_TEXT
        header = []
        if directive.get("title"):
            header.append(str(directive["title"]).strip())
        if directive.get("issue") is not None:
            header.append(f"(issue #{directive['issue']})")
        return (" ".join(header) + "\n" + text) if header else text
    if isinstance(directive, str) and directive.strip():
        return directive.strip()
    return NO_DIRECTIVE_TEXT


__all__ = [
    "BriefError", "DEFAULT_PACK_DIR", "PACK_DIR_ENV",
    "build_persona_prompt", "character_id", "load_character", "resolve_pack_dir",
]
