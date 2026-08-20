"""
Layer 2 orchestrator for the 3-layer offline content generator.

This module owns resume, the 2a→2b fan-out, retry/skip, and writing
`brief.yaml`. Everything it validates or renders it imports from
`segment_schema`; nothing pure is reimplemented here.
"""
import concurrent.futures
import logging
import yaml
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from segment_schema import (SYSTEM_PROMPT_CHAPTERS, SYSTEM_PROMPT_SLOTS,
                            SegmentPlanError, parse_chapters, parse_slots,
                            validate_chapters, validate_slots,
                            check_sensitivity_budget, build_chapter_prompt,
                            build_slot_prompt, merge_brief)

log = logging.getLogger(__name__)


def plan_segment(pack, arc_segment, config, llm, vocab, out_path) -> Optional[Dict]:
    """
    Plan a segment by generating chapters and slots.
    
    Args:
        pack: The campaign pack
        arc_segment: Segment dictionary
        config: Configuration dictionary
        llm: Object with complete(system_prompt, messages) method
        vocab: Vocabulary object for validation
        out_path: pathlib.Path to write the brief YAML
        
    Returns:
        Dictionary containing the brief, or None if no valid chapters were produced
    """
    # Step 1 - Resume
    existing = None
    if out_path.exists():
        try:
            existing = yaml.safe_load(out_path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise SegmentPlanError(
                f"could not load existing brief {out_path}: {exc}") from exc

    chapters = existing.get("chapters") or [] if existing else []
    slots_by_chapter = {}
    if existing and existing.get("slots"):
        for slot in existing["slots"]:
            slots_by_chapter.setdefault(slot["chapter_id"], []).append(slot)

    # Step 2 - The 2a pass (skip entirely when chapters is already non-empty)
    if not chapters:
        max_attempts = config["segment"]["max_attempts"]
        problems = None
        chapters = []
        for attempt in range(max_attempts):
            prompt = build_chapter_prompt(pack, arc_segment, config, problems)
            try:
                reply = llm.complete(SYSTEM_PROMPT_CHAPTERS,
                                     [{"role": "user", "content": prompt}])
                candidate = parse_chapters(reply)
            except Exception as exc:
                log.warning("segment %s chapter pass attempt %d failed: %s",
                            arc_segment["id"], attempt + 1, exc)
                problems = [str(exc)]
                continue
            problems = validate_chapters(candidate, config)
            if not problems:
                chapters = candidate
                break
            log.warning("segment %s chapter pass attempt %d failed validation: %s",
                        arc_segment["id"], attempt + 1, "; ".join(problems))
        else:
            log.error("segment %s produced no valid chapters after %d attempts",
                      arc_segment["id"], max_attempts)
            return None

    # Step 3 - The 2b fan-out
    # Determine which chapters need to be planned (those without slots)
    planned_chapter_ids = set(slots_by_chapter.keys())
    missing_chapters = [ch for ch in chapters if ch["chapter_id"] not in planned_chapter_ids]
    
    if not missing_chapters:
        # All chapters are already planned
        log.debug("All chapters already planned for segment %s", arc_segment["id"])
    else:
        log.debug("Planning %d missing chapters for segment %s", 
                 len(missing_chapters), arc_segment["id"])

    def _plan_chapter(pack, arc_segment, chapter, config, llm, vocab):
        problems = None
        for attempt in range(config["segment"]["max_attempts"]):
            prompt = build_slot_prompt(pack, arc_segment, chapter, config,
                                       problems)
            try:
                reply = llm.complete(SYSTEM_PROMPT_SLOTS,
                                     [{"role": "user", "content": prompt}])
                slots = parse_slots(reply)
            except Exception as exc:
                log.warning("segment %s chapter %s attempt %d failed: %s",
                            arc_segment["id"], chapter["chapter_id"], attempt + 1, exc)
                problems = [str(exc)]
                continue
            problems = validate_slots(slots, pack, vocab, config)
            if not problems:
                return chapter["chapter_id"], slots
            log.warning("segment %s chapter %s attempt %d failed validation: %s",
                        arc_segment["id"], chapter["chapter_id"], attempt + 1, "; ".join(problems))
        log.error("segment %s chapter %s produced no valid slots after %d "
                  "attempts", arc_segment["id"], chapter["chapter_id"],
                  config["segment"]["max_attempts"])
        return chapter["chapter_id"], None

    # Plan missing chapters concurrently
    skipped = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=config["segment"]["concurrency"]) as executor:
        # Submit all tasks
        future_to_chapter = {
            executor.submit(_plan_chapter, pack, arc_segment, chapter, config, llm, vocab): chapter 
            for chapter in missing_chapters
        }
        
        # Process completed futures as they finish
        for future in concurrent.futures.as_completed(future_to_chapter):
            chapter_id, slots = future.result()
            
            if slots is None:
                skipped += 1
                continue
            
            # Check sensitivity budget (do not retry on this)
            message = check_sensitivity_budget(slots, config)
            if message:
                log.warning("segment %s chapter %s: %s", arc_segment["id"], chapter_id, message)
            
            # Prefix slot IDs to make them unique
            for slot in slots:
                if not slot["slot_id"].startswith(f"{chapter_id}-"):
                    slot["slot_id"] = f"{chapter_id}-{slot['slot_id']}"
            
            # Store the slots
            slots_by_chapter[chapter_id] = slots
            
            # Write brief as chapters land
            out_path.parent.mkdir(parents=True, exist_ok=True)
            brief = merge_brief(arc_segment, chapters, slots_by_chapter, config)
            with open(out_path, "w", encoding="utf-8") as fh:
                yaml.dump(brief, fh, sort_keys=False, allow_unicode=True)

    # Step 4 - Final validation
    brief = merge_brief(arc_segment, chapters, slots_by_chapter, config)
    
    # Check density
    target = config["segment"]["target_slots"]
    floor = config["segment"]["density_floor"]
    if target and len(brief["slots"]) < target * floor:
        log.warning("segment %s density %.2f is below the floor of %.2f "
                    "(%d slots against a target of %d)", 
                    arc_segment["id"], 
                    len(brief["slots"]) / target, 
                    floor,
                    len(brief["slots"]), 
                    target)

    log.info("segment %s planned: %d chapters, %d slots, %d chapters "
             "skipped", arc_segment["id"], len(chapters),
             len(brief["slots"]), skipped)
    
    return brief
