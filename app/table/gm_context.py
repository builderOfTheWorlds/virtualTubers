"""GM agent context builder for the live agent table (build plan P2.7, decision U7).

`build_gm_context(...)` renders the GM's context from the GM-only profile blocks
(`character_baselines.gm_blocks`), every player sheet, the current arc segment
and the scene contract. The GM is one more agent on the shared model; it differs
from the players only by its context.

The rule that must hold: adding a GM block is data, not code. The builder
renders whatever blocks exist: first the ones named in `blocks_order` (in that
order, skipping absent ones), then every other block alphabetically. Nothing in
the code names a specific block except through `blocks_order`.

The DB read is NOT here: callers use
`character.store.characters.gm_blocks(conn, slug)`.
"""
from __future__ import annotations

import logging
from typing import Mapping, Optional, Sequence

log = logging.getLogger(__name__)

PLAYERS_HEADING = "# The players (every sheet; only you see all of them)"
SEGMENT_HEADING = "# The current arc segment"
CONTRACT_HEADING = "# This scene's contract"


def block_heading(name) -> str:
    """The heading line for a single GM block."""
    return f"# GM block: {name}"


def _render_scalar(value) -> str:
    if isinstance(value, str):
        return value.strip()
    return str(value)


def _render_dict(d, indent) -> str:
    pad = "  " * indent
    lines = []
    for key, value in d.items():
        if isinstance(value, dict):
            lines.append(f"{pad}{key}:")
            lines.append(_render_dict(value, indent + 1))
        elif isinstance(value, (list, tuple)):
            lines.append(f"{pad}{key}:")
            lines.append(_render_list(value, indent + 1))
        else:
            lines.append(f"{pad}{key}: {_render_scalar(value)}")
    return "\n".join(lines)


def _render_list(items, indent) -> str:
    pad = "  " * indent
    lines = []
    for item in items:
        if isinstance(item, dict):
            lines.append(f"{pad}-")
            lines.append(_render_dict(item, indent + 1))
        else:
            lines.append(f"{pad}- {_render_scalar(item)}")
    return "\n".join(lines)


def render_value(value, indent=0) -> str:
    """Human/LLM-readable text for a block value.

    str as-is (stripped); a dict as "key: value" lines (nested values indented
    by 2 spaces per level); a list as "- item" lines (dict items render their
    key: value lines under the dash). Never Python repr.
    """
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        return _render_dict(value, indent)
    if isinstance(value, (list, tuple)):
        return _render_list(value, indent)
    return str(value)


def _truncate_words(text, limit):
    if len(text) <= limit:
        return text
    if limit <= 1:
        return ""
    cut = text[:limit - 1].rsplit(" ", 1)[0]
    return cut + "…"


def _block_section(name, value) -> str:
    return block_heading(name) + "\n" + render_value(value)


def _assemble(gm_identity, ordered_sections, extra_sections, player_sheets,
              segment, contract) -> str:
    sections = [gm_identity.strip()]
    sections.extend(ordered_sections)
    sections.extend(extra_sections)
    sections.append(PLAYERS_HEADING + "\n" + "\n\n".join(
        f"## {seat}\n{sheet}" for seat, sheet in sorted(player_sheets.items())))
    if segment:
        sections.append(SEGMENT_HEADING + "\n" + render_value(segment))
    if contract:
        sections.append(CONTRACT_HEADING + "\n" + render_value(contract))
    return "\n\n".join(sections)


def build_gm_context(*, gm_blocks, gm_identity, player_sheets, segment, contract,
                     blocks_order, max_chars=None) -> str:
    """The GM's context (<= max_chars when given).

    Sections joined by a blank line, in this order:
      1. gm_identity (stripped)
      2. for name in blocks_order if name in gm_blocks: block_heading + render_value
      3. for name in sorted(set(gm_blocks) - set(blocks_order)): same
      4. PLAYERS_HEADING, then per seat in sorted(player_sheets)
      5. if segment: SEGMENT_HEADING + render_value(segment)
      6. if contract: CONTRACT_HEADING + render_value(contract)

    max_chars: when the result is longer, shorten the player sheet bodies (each
    to an equal share, cut on a word boundary with "…") until it fits; blocks,
    segment and contract are never cut. If it still does not fit, raise
    ValueError. Never mutates its inputs.
    """
    gm_blocks = dict(gm_blocks)
    player_sheets = dict(player_sheets)
    blocks_order = list(blocks_order)

    ordered_sections = [_block_section(name, gm_blocks[name])
                        for name in blocks_order if name in gm_blocks]
    extra_names = sorted(set(gm_blocks) - set(blocks_order))
    extra_sections = [_block_section(name, gm_blocks[name]) for name in extra_names]

    text = _assemble(gm_identity, ordered_sections, extra_sections, player_sheets,
                     segment, contract)
    if max_chars is None or len(text) <= max_chars:
        log.debug("gm context blocks=%d ordered=%d extra=%d sheets=%d chars=%d",
                  len(gm_blocks), len(ordered_sections), len(extra_sections),
                  len(player_sheets), len(text))
        return text

    # Shorten the player sheet bodies (each to an equal share) until it fits.
    seats = sorted(player_sheets)
    if not seats:   # nothing to shorten (orchestrator review fix: avoided a ZeroDivisionError)
        log.error("gm context cannot fit max_chars=%d without player sheets", max_chars)
        raise ValueError(f"gm context cannot fit in {max_chars} chars")
    originals = {seat: player_sheets[seat] for seat in seats}
    fixed_len = len(_assemble(gm_identity, ordered_sections, extra_sections, {},
                              segment, contract))
    overhead = len(text) - sum(len(v) for v in originals.values())
    budget = max_chars - overhead
    if budget < len(seats):
        log.error("gm context cannot fit max_chars=%d (fixed=%d overhead=%d)",
                  max_chars, fixed_len, overhead)
        raise ValueError(f"gm context cannot fit in {max_chars} chars")
    share = budget // len(seats)
    shortened = {seat: _truncate_words(originals[seat], share) for seat in seats}
    text = _assemble(gm_identity, ordered_sections, extra_sections, shortened,
                     segment, contract)
    if len(text) > max_chars:
        log.error("gm context cannot fit max_chars=%d (len=%d)", max_chars, len(text))
        raise ValueError(f"gm context cannot fit in {max_chars} chars")
    log.debug("gm context capped to %d chars (share=%d)", len(text), share)
    return text
