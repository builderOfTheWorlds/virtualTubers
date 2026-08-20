"""
Schema, parser, validator and prompt builder for the arc planner.

This module handles the pure half of Layer 1 of the 3-layer offline content
generator. It provides functions to compute segment counts, build context
strings, parse model replies, normalize segments, validate batches, and
build prompts for the arc planner.

All functions are pure — they take arguments and return values without
side effects.
"""
import logging
import math
import re
import yaml
from typing import Dict, List, Optional, Union

from vocabulary import Vocabulary

log = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You are a story architect planning a long-running serialized arc for a live "
    "virtual-streamer campaign. Your task is to create a detailed arc plan that "
    "guides the generation of 28 segments, each 6 hours long, for a total of 168 "
    "hours of content. The arc must be coherent and follow a specific structure "
    "that allows for seamless continuation across all segments."
)

class ArcPlanError(ValueError):
    """Raised for a config that cannot produce a segment count, a model reply that cannot be parsed 
    into a segment list, and an existing arc_plan.yaml that will not load."""
    pass


def n_segments(config) -> int:
    """
    Compute the number of segments in the arc.
    
    Args:
        config: The configuration dictionary
        
    Returns:
        The number of segments (ceiling of total hours / segment hours)
        
    Raises:
        ArcPlanError: If segment_hours is zero or less
    """
    log.debug("n_segments called with config=%s", config)
    segment_hours = config["arc"]["segment_hours"]
    if segment_hours <= 0:
        raise ArcPlanError(f"segment_hours must be positive, got {segment_hours}")
    
    total_hours = config["arc"]["hours_total"]
    segments = math.ceil(total_hours / segment_hours)
    log.debug("n_segments returning %d", segments)
    return segments


def build_context(pack, config) -> str:
    """
    Build the context string to be given to the model.
    
    Args:
        pack: The campaign pack
        config: The configuration dictionary
        
    Returns:
        A deterministic context string containing campaign info, spine scenes,
        lore notes, and ambient scene ids.
    """
    log.debug("build_context called with pack=%s, config=%s", pack, config)
    
    # Campaign identity
    lines = [
        f"{pack.title}",
        f"{pack.genre}"
    ]
    
    # Spine scenes (in pack order, with narration)
    spine_scenes = []
    for scene_id, scene in pack.scenes.items():
        if not scene.ambient:
            spine_scenes.append((scene_id, scene))
    
    for scene_id, scene in spine_scenes:
        lines.append(f"{scene_id}: {scene.title}")
        if scene.enter_narration:
            lines.append(scene.enter_narration)
    
    # Lore notes
    for name, text in sorted(pack.lore.items()):
        lines.append(f"lore: {name} - {text}")
    
    # Ambient scene ids
    ambient_ids = pack.ambient_scene_ids()
    lines.append("ambient scenes:")
    for sid in sorted(ambient_ids):
        lines.append(f"- {sid}")
    
    context = "\n".join(lines)
    log.debug("build_context returning %d-character string", len(context))
    return context


def parse_reply(reply: str) -> List[Dict]:
    """
    Extract the segment list from a raw model reply.
    
    Args:
        reply: The raw model reply
        
    Returns:
        A list of segment dictionaries
        
    Raises:
        ArcPlanError: If nothing parses or if segments is not a list
    """
    log.debug("parse_reply called with reply=%r", reply[:100] + ("..." if len(reply) > 100 else ""))
    
    text = reply.strip()

    # 1. A fence, if there is one, wins outright.
    m = re.search(r"```(?:yaml|yml)?\s*\n(.*?)```", text, re.DOTALL)
    if m:
        candidates = [m.group(1)]
    else:
        # 2. The whole reply, then the tail starting at the first
        #    line that looks like the start of the document.
        candidates = [text]
        lines = text.splitlines()
        for i, line in enumerate(lines):
            if line.startswith("segments:") or line.startswith("- "):
                candidates.append("\n".join(lines[i:]))
                break

    for candidate in candidates:
        try:
            parsed = yaml.safe_load(candidate)
        except yaml.YAMLError:
            continue
            
        # Check if it's a mapping with segments key
        if isinstance(parsed, dict) and "segments" in parsed:
            segments = parsed["segments"]
            if not isinstance(segments, list):
                raise ArcPlanError("segments must be a list")
            # Validate each segment is a mapping
            for seg in segments:
                if not isinstance(seg, dict):
                    raise ArcPlanError("each segment must be a mapping")
            return segments
            
        # Check if it's a bare top-level list
        if isinstance(parsed, list):
            # Validate each item is a mapping
            for seg in parsed:
                if not isinstance(seg, dict):
                    raise ArcPlanError("each segment must be a mapping")
            return parsed
    
    raise ArcPlanError("could not parse reply into a segment list")


def normalize_segment(seg: Dict) -> Dict:
    """
    Return a copy with the deferred keys filled in.
    
    Args:
        seg: The segment dictionary
        
    Returns:
        A normalized segment dictionary
    """
    log.debug("normalize_segment called with seg=%s", seg)
    result = dict(seg)
    
    # Fill in defaults for deferred keys
    if "fork" not in result:
        result["fork"] = None
    if "event_windows" not in result:
        result["event_windows"] = []
        
    log.debug("normalize_segment returning %s", result)
    return result


def validate_batch(segments, expected_orders, known_ids, vocab, config) -> List[str]:
    """
    Validate a batch of segments.
    
    Args:
        segments: List of segment dictionaries
        expected_orders: List of expected order numbers
        known_ids: Set of already-planned segment ids
        vocab: Vocabulary object for validation
        config: Configuration dictionary
        
    Returns:
        List of problem strings; empty list means valid
    """
    log.debug("validate_batch called with segments=%s, expected_orders=%s, known_ids=%s", 
              [seg.get('id') for seg in segments], expected_orders, known_ids)
    
    problems = []
    
    # Check required keys first
    REQUIRED_KEYS = [
        "id", "order", "loop", "hours", "spine_scenes", "ambient_focus",
        "synopsis", "continuity_in", "continuity_out", "carry_in", "carry_out"
    ]
    
    for index, seg in enumerate(segments):
        if not isinstance(seg, dict):
            problems.append(f"segment {index!r} is not a mapping")
            continue
            
        for key in REQUIRED_KEYS:
            if key not in seg:
                problems.append(f"segment {seg.get('id', index)!r} is missing required key {key!r}")
    
    if problems:
        return problems
    
    # Check segment count and order
    if len(segments) != len(expected_orders):
        problems.append(f"expected {len(expected_orders)} segments, got {len(segments)}")
        return problems
        
    for i, (seg, expected_order) in enumerate(zip(segments, expected_orders)):
        if seg["order"] != expected_order:
            problems.append(f"segment {seg['id']!r} order {seg['order']} does not match expected {expected_order}")
    
    # Check for duplicate or known ids
    seen_ids = set()
    for seg in segments:
        seg_id = seg["id"]
        if seg_id in known_ids:
            problems.append(f"segment {seg_id!r} collides with already-planned segment")
        elif seg_id in seen_ids:
            problems.append(f"segment {seg_id!r} is duplicated within batch")
        else:
            seen_ids.add(seg_id)
    
    # Check carry_in and carry_out types
    for seg in segments:
        # carry_in check
        value = seg["carry_in"]
        if not isinstance(value, dict):
            problems.append(f"segment {seg['id']!r} carry_in must be a mapping, got {type(value).__name__}")
        else:
            unknown = vocab.unknown_carry_keys(list(value.keys()))
            for key in unknown:
                problems.append(f"segment {seg['id']!r} carry_in has unknown key {key!r}")
        
        # carry_out check
        value = seg["carry_out"]
        if not isinstance(value, dict):
            problems.append(f"segment {seg['id']!r} carry_out must be a mapping, got {type(value).__name__}")
        else:
            unknown = vocab.unknown_carry_keys(list(value.keys()))
            for key in unknown:
                problems.append(f"segment {seg['id']!r} carry_out has unknown key {key!r}")
    
    # Check spine scenes
    for seg in segments:
        unknown = vocab.unknown_scene_refs(seg["spine_scenes"])
        for scene_id in unknown:
            problems.append(f"segment {seg['id']!r} spine_scenes references unknown scene {scene_id!r}")
    
    # Check hours
    segment_hours = config["arc"]["segment_hours"]
    for seg in segments:
        if seg["hours"] != segment_hours:
            problems.append(f"segment {seg['id']!r} has {seg['hours']} hours, expected {segment_hours}")
    
    # Check synopsis
    for seg in segments:
        if not isinstance(seg["synopsis"], str) or not seg["synopsis"].strip():
            problems.append(f"segment {seg['id']!r} synopsis must be a non-empty string")
    
    # Check loop
    for seg in segments:
        loop = seg["loop"]
        if type(loop) is not int or loop < 0:
            problems.append(f"segment {seg['id']!r} loop must be a non-negative integer, got {type(loop).__name__}")
    
    return problems


def build_prompt(context, expected_orders, previous_continuity,
                config, problems) -> str:
    """
    Build the user prompt for ONE attempt.
    
    Args:
        context: The campaign context string
        expected_orders: List of order numbers to generate
        previous_continuity: Continuity from previous batch or empty string
        config: Configuration dictionary
        problems: List of problem strings from previous attempt, or None
        
    Returns:
        The complete prompt string
    """
    log.debug("build_prompt called with context=%s, expected_orders=%s, previous_continuity=%s, "
              "problems=%s", context[:50] + ("..." if len(context) > 50 else ""), 
              expected_orders, previous_continuity, problems)
    
    prompt_lines = [
        context,
        "",
        f"Generate segments for orders {expected_orders[0]} through {expected_orders[-1]}.",
        ""
    ]
    
    if previous_continuity:
        prompt_lines.append(f"Previous continuity: {previous_continuity}")
    else:
        prompt_lines.append("Start of the arc.")
    
    prompt_lines.append("")
    prompt_lines.append("Required keys for each segment:")
    required_keys = [
        "id", "order", "loop", "hours", "spine_scenes", "ambient_focus",
        "synopsis", "continuity_in", "continuity_out", "carry_in", "carry_out"
    ]
    for key in required_keys:
        prompt_lines.append(f"- {key}")
    
    prompt_lines.append("")
    prompt_lines.append("Legal carry keys:")
    carry_keys = sorted(config["state"]["carry_keys"])
    for key in carry_keys:
        prompt_lines.append(f"- {key}")
    
    prompt_lines.append("")
    prompt_lines.append("Reply with ONLY YAML under a 'segments:' key.")
    
    if problems:
        prompt_lines.append("")
        prompt_lines.append("Previous attempt had these problems:")
        for problem in problems:
            prompt_lines.append(f"- {problem}")
    
    result = "\n".join(prompt_lines)
    log.debug("build_prompt returning %d-character string", len(result))
    return result
