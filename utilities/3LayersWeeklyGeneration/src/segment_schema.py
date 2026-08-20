"""
Schema, parser, validator and prompt builder for the segment planner.

This module handles the pure half of Layer 2 of the 3-layer offline content
generator. It provides functions to parse model replies, validate chapters
and slots, build prompts for the segment planner, and merge the results into
brief.yaml format.
"""
import logging
import re
import yaml
from typing import Dict, List, Optional, Union

log = logging.getLogger(__name__)

SYSTEM_PROMPT_CHAPTERS = (
    "You are a story architect planning a long-running serialized arc for a live "
    "virtual-streamer campaign. Your task is to break a 6-hour segment into "
    "chapters that will guide the generation of authored slots. Each chapter "
    "should be a self-contained unit with a clear beginning, middle, and end. "
    "The chapters must cover the entire segment's time span without gaps or overlaps."
)

SYSTEM_PROMPT_SLOTS = (
    "You are a story architect planning a long-running serialized arc for a live "
    "virtual-streamer campaign. Your task is to generate authored slots for one "
    "chapter of a 6-hour segment. Each slot should be a self-contained beat that "
    "can be generated as dialogue or narration, with clear participants and "
    "context. The slots must fit within the chapter's summary and contribute to "
    "the overall narrative arc."
)

class SegmentPlanError(ValueError):
    """Raised ONLY for a model reply that cannot be parsed into a list."""
    pass


def _parse_reply(reply: str, key: str) -> list:
    """
    Parse a model reply into a list of dictionaries.
    
    Implements the exact algorithm described in the specification:
      1. If the text contains a ``` fence, take the text BETWEEN the first
         fence and the next fence. Strip an optional language word
         ("yaml", "yml", "json") off the front of the first line.
      2. Otherwise use the whole text.
      3. `yaml.safe_load` it.
      4. If the result is a dict, return `result[key]` when that key is
         present and is a list.
      5. If the result is itself a list, return it.
      6. Otherwise raise SegmentPlanError.

    Args:
        reply: The raw model reply text
        key: The key to extract from the parsed dict (e.g., "chapters", "slots")

    Returns:
        A list of dictionaries

    Raises:
        SegmentPlanError: If parsing fails or result is not a list
    """
    log.debug("Parsing reply for key '%s'", key)
    text = reply.strip()

    # 1. If the text contains a ``` fence, take the text BETWEEN the first
    #    fence and the next fence. Strip an optional language word.
    m = re.search(r"```(?:yaml|yml|json)?\s*\n(.*?)```", text, re.DOTALL)
    if m:
        text = m.group(1)
        # Strip optional language from first line
        lines = text.splitlines()
        if lines and lines[0].strip() in ("yaml", "yml", "json"):
            text = "\n".join(lines[1:])

    # 2. Otherwise use the whole text (already done above)

    # 3. yaml.safe_load it
    try:
        parsed = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        log.debug("YAML parsing failed: %s", exc)
        raise SegmentPlanError(f"Failed to parse YAML: {exc}")

    # 4. If the result is a dict, return result[key] when that key is present and is a list
    if isinstance(parsed, dict):
        if key not in parsed:
            raise SegmentPlanError(f"Reply does not contain '{key}' key")
        result = parsed[key]
        if not isinstance(result, list):
            raise SegmentPlanError(f"'{key}' must be a list, got {type(result).__name__}")
        # Check that all elements are dicts
        for i, item in enumerate(result):
            if not isinstance(item, dict):
                raise SegmentPlanError(f"Element {i} of '{key}' is not a dictionary")
        return result

    # 5. If the result is itself a list, return it
    if isinstance(parsed, list):
        # Check that all elements are dicts
        for i, item in enumerate(parsed):
            if not isinstance(item, dict):
                raise SegmentPlanError(f"Element {i} of reply is not a dictionary")
        return parsed

    # 6. Otherwise raise SegmentPlanError
    raise SegmentPlanError(f"Reply could not be parsed into a list: {type(parsed).__name__}")


def parse_chapters(reply) -> list:
    """Parse chapters from a model reply."""
    return _parse_reply(reply, "chapters")


def parse_slots(reply) -> list:
    """Parse slots from a model reply."""
    return _parse_reply(reply, "slots")


def validate_chapters(chapters, config) -> List[str]:
    """
    Validate a list of chapters.
    
    Returns a list of problem strings, [] when clean. Never raises.
    
    Args:
        chapters: List of chapter dictionaries
        config: Configuration dictionary
        
    Returns:
        List of problem strings
    """
    log.debug("Validating %d chapters", len(chapters))
    problems = []
    
    # Required keys check
    required = ["chapter_id", "order", "title", "summary",
                "continuity_in", "continuity_out"]
    
    for ch in chapters:
        for key in required:
            if key not in ch:
                problems.append(f"chapter is missing required key '{key}'")
    
    if problems:
        return problems
    
    # Length check
    expected_chapters = config["segment"]["chapters_per_segment"]
    if len(chapters) != expected_chapters:
        problems.append(
            f"expected {expected_chapters} chapters, got {len(chapters)}"
        )
    
    # Order values check
    orders = [ch["order"] for ch in chapters]
    expected_orders = set(range(len(chapters)))
    actual_orders = set(orders)
    if actual_orders != expected_orders:
        problems.append(
            f"chapter order values must be exactly {sorted(expected_orders)}, "
            f"got {sorted(actual_orders)}"
        )
    
    # Chapter_id uniqueness check
    chapter_ids = [ch["chapter_id"] for ch in chapters]
    if len(chapter_ids) != len(set(chapter_ids)):
        problems.append("chapter_id values must be unique")
    
    # Summary emptiness check
    for ch in chapters:
        if not isinstance(ch["summary"], str) or not ch["summary"].strip():
            problems.append(
                f"chapter '{ch['chapter_id']}' summary must be a non-empty string"
            )
    
    return problems


def validate_slots(slots, pack, vocab, config) -> List[str]:
    """
    Validate a list of slots.
    
    Returns a list of problem strings, [] when clean. Never raises.
    
    Args:
        slots: List of slot dictionaries
        pack: CampaignPack object
        vocab: Vocabulary object
        config: Configuration dictionary
        
    Returns:
        List of problem strings
    """
    log.debug("Validating %d slots", len(slots))
    problems = []
    
    # Required keys check first, early return
    for slot in slots:
        kind = slot.get("kind")
        if kind == "ambient":
            required = ["slot_id", "kind", "prompt", "lore", "participants", 
                        "sensitivity", "depends_on"]
        elif kind == "spine":
            required = ["slot_id", "kind", "scene_ref", "participants"]
        else:
            problems.append(
                f"slot '{slot.get('slot_id', '<no slot_id>')}' has unknown kind '{kind}'"
            )
            continue
            
        for key in required:
            if key not in slot:
                problems.append(
                    f"slot '{slot.get('slot_id', '<no slot_id>')}' "
                    f"is missing required key '{key}'"
                )
    
    if problems:
        return problems
    
    # Check each slot
    slot_ids = []
    for slot in slots:
        slot_id = slot["slot_id"]
        slot_ids.append(slot_id)
        
        # Delegate to vocab validation
        vocab_problems = vocab.validate_slot(slot)
        problems.extend(vocab_problems)
        
        # Skip further checks if this slot has vocab problems
        if any(p.startswith(f"slot '{slot_id}'") for p in vocab_problems):
            continue
            
        # Check participants
        participants = slot.get("participants")
        if not isinstance(participants, list):
            problems.append(
                f"slot '{slot_id}' participants must be a list"
            )
        else:
            if len(participants) == 0:
                problems.append(
                    f"slot '{slot_id}' participants must be a non-empty list"
                )
            else:
                for participant in participants:
                    if participant not in pack.cast:
                        problems.append(
                            f"slot '{slot_id}' has unknown participant '{participant}'"
                        )
        
        # Check kind-specific requirements
        if slot["kind"] == "ambient":
            # Check scene_ref is not present
            if "scene_ref" in slot:
                problems.append(
                    f"slot '{slot_id}' cannot have a scene_ref key (only spine slots can)"
                )
            
            # Check prompt
            prompt = slot.get("prompt")
            if not isinstance(prompt, str) or not prompt.strip():
                problems.append(
                    f"slot '{slot_id}' prompt must be a non-empty string"
                )
                
            # Check depends_on is list
            depends_on = slot.get("depends_on")
            if not isinstance(depends_on, list):
                problems.append(
                    f"slot '{slot_id}' depends_on must be a list"
                )
            else:
                # Check sensitivity and depends_on consistency
                sensitivity = slot.get("sensitivity")
                if sensitivity == "flags" and len(depends_on) == 0:
                    problems.append(
                        f"slot '{slot_id}' has sensitivity 'flags' but empty depends_on"
                    )
                
                # Check budget
                takes = config["dialogue"]["takes_per_slot"]
                neutral = config["dialogue"]["neutral_takes"]
                conditioned = takes - neutral
                if len(depends_on) * 2 > conditioned:
                    problems.append(
                        f"slot '{slot_id}' declares {len(depends_on)} dependencies "
                        f"but only {conditioned} conditioned takes available"
                    )
        
        elif slot["kind"] == "spine":
            # Check scene_ref exists in pack
            scene_ref = slot.get("scene_ref")
            if scene_ref is not None and scene_ref not in pack.scenes:
                problems.append(
                    f"slot '{slot_id}' references unknown scene '{scene_ref}'"
                )
    
    # Duplicate slot_id check
    if len(slot_ids) != len(set(slot_ids)):
        seen = set()
        for sid in slot_ids:
            if sid in seen:
                problems.append(f"duplicate slot_id '{sid}' found")
            else:
                seen.add(sid)
    
    return problems


def sensitivity_mix(slots) -> Dict[str, int]:
    """
    Count ambient slots by sensitivity type.
    
    Returns `{"none": n, "tone": n, "flags": n}` counting AMBIENT slots only.
    A slot with no `sensitivity` key is not counted at all.
    
    Args:
        slots: List of slot dictionaries
        
    Returns:
        Dictionary mapping sensitivity types to counts
    """
    mix = {"none": 0, "tone": 0, "flags": 0}
    
    for slot in slots:
        if slot.get("kind") == "ambient":
            sensitivity = slot.get("sensitivity")
            if sensitivity in mix:
                mix[sensitivity] += 1
    
    return mix


def check_sensitivity_budget(slots, config) -> Optional[str]:
    """
    Check if the fraction of flags slots exceeds the budget.
    
    Returns a message string when the fraction of `flags` slots exceeds
    `config["segment"]["sensitivity_budget"]`, otherwise None.
    
    Args:
        slots: List of slot dictionaries
        config: Configuration dictionary
        
    Returns:
        Message string or None
    """
    mix = sensitivity_mix(slots)
    total = sum(mix.values())
    
    if total == 0:
        return None
    
    fraction = mix["flags"] / total
    budget = config["segment"]["sensitivity_budget"]
    
    if fraction <= budget:
        return None
    
    log.warning(
        "Flags slots (%d) exceed budget (%.2f) in fraction (%.2f)",
        mix["flags"], budget, fraction
    )
    return (f"{mix['flags']} of {total} slots are state-sensitive "
            f"({fraction:.2f}), over the budget of {budget}")


def build_chapter_prompt(pack, arc_segment, config, problems) -> str:
    """
    Build the 2a user prompt for chapter planning.
    
    Args:
        pack: CampaignPack object
        arc_segment: Segment dictionary
        config: Configuration dictionary
        problems: List of problem strings from previous attempt, or None
        
    Returns:
        The complete prompt string
    """
    chapters_per_segment = config["segment"]["chapters_per_segment"]
    order_range = f"0 to {chapters_per_segment - 1}"
    
    lines = [
        f"{arc_segment['synopsis']}",
        "",
        f"Continuity in: {arc_segment['continuity_in']}",
        f"Continuity out: {arc_segment['continuity_out']}",
        "",
        f"Plan {chapters_per_segment} chapters.",
        f"Chapter order must be exactly the integers {order_range}.",
        "",
        "Reply with ONLY YAML under a 'chapters:' key."
    ]
    
    if problems:
        lines.append("")
        lines.append("Previous attempt had these problems:")
        for problem in problems:
            lines.append(f"- {problem}")
    
    return "\n".join(lines)


def build_slot_prompt(pack, arc_segment, chapter, config, problems) -> str:
    """
    Build the 2b user prompt for slot planning.
    
    Args:
        pack: CampaignPack object
        arc_segment: Segment dictionary
        chapter: Chapter dictionary
        config: Configuration dictionary
        problems: List of problem strings from previous attempt, or None
        
    Returns:
        The complete prompt string
    """
    target_slots = round(config["segment"]["target_slots"] / 
                         config["segment"]["chapters_per_segment"])
    
    lines = [
        f"Chapter summary: {chapter['summary']}",
        "",
        f"Plan {target_slots} slots for this chapter.",
        "",
        "Legal state keys (for depends_on):",
    ]
    
    for flag in config["state"]["flags"]:
        lines.append(f"- {flag}")
    
    lines.append("")
    lines.append("Cast:")
    for player_id in pack.player_ids:
        member = pack.cast[player_id]
        lines.append(f"- {player_id}: {member.name}")
    
    lines.append("")
    lines.append("Spine ground truth (for the ear):")
    for scene_id in arc_segment["spine_scenes"]:
        if scene_id in pack.scenes:
            scene = pack.scenes[scene_id]
            if scene.enter_narration:
                lines.append(f"- {scene_id}: {scene.enter_narration}")
    
    lines.append("")
    lines.append("Each slot's prompt must be written for the ear.")
    lines.append("A slot may declare at most one dependency in depends_on.")
    lines.append("")
    lines.append("Reply with ONLY YAML under a 'slots:' key.")
    
    if problems:
        lines.append("")
        lines.append("Previous attempt had these problems:")
        for problem in problems:
            lines.append(f"- {problem}")
    
    return "\n".join(lines)


def merge_brief(arc_segment, chapters, slots_by_chapter, config) -> Dict:
    """
    Merge chapters and slots into a brief dictionary.
    
    Args:
        arc_segment: Segment dictionary
        chapters: List of chapter dictionaries
        slots_by_chapter: Dictionary mapping chapter_id to list of slot dicts
        config: Configuration dictionary
        
    Returns:
        The merged brief dictionary
    """
    out_slots = []
    for ch in sorted(chapters, key=lambda c: c["order"]):
        for slot in slots_by_chapter.get(ch["chapter_id"], []):
            slot = dict(slot)
            slot["chapter_id"] = ch["chapter_id"]
            out_slots.append(slot)

    return {
        "segment_id": arc_segment["id"],
        "order": arc_segment["order"],
        "loop": arc_segment["loop"],
        "hours": arc_segment["hours"],
        "synopsis": arc_segment["synopsis"],
        "continuity_in": arc_segment["continuity_in"],
        "continuity_out": arc_segment["continuity_out"],
        "carry_in": arc_segment.get("carry_in", {}),
        "carry_out": arc_segment.get("carry_out", {}),
        "chapters": sorted(chapters, key=lambda c: c["order"]),
        "slots": out_slots,
    }
