"""
Layer 1 orchestrator for the 3-layer offline content generator.

This module owns resume, batching, retry/skip, and writing for the arc planner.
It coordinates with arc_schema.py for parsing, validation, and prompt construction.
"""
import logging
import yaml
from pathlib import Path
from typing import Dict, List, Optional, Union

from arc_schema import (SYSTEM_PROMPT, ArcPlanError, build_context,
                        build_prompt, n_segments, normalize_segment,
                        parse_reply, validate_batch)

log = logging.getLogger(__name__)


def plan_arc(pack, config, llm, vocab, out_path) -> dict:
    """
    Plan an arc by generating segments in batches.
    
    Args:
        pack: The campaign pack
        config: The configuration dictionary
        llm: Object with complete(system_prompt, messages) method
        vocab: Vocabulary object for validation
        out_path: pathlib.Path to write the plan YAML
        
    Returns:
        Dictionary mapping {"segments": [...]} sorted by order
    """
    # Compute expected segment count and orders
    n_segs = n_segments(config)
    expected_orders = list(range(n_segs))
    
    # Load existing plan if it exists
    existing_segments = []
    try:
        if out_path.exists():
            with open(out_path, 'r', encoding='utf-8') as f:
                data = yaml.safe_load(f)
            if data and 'segments' in data:
                existing_segments = data['segments']
    except Exception as exc:
        # The one place we raise rather than skip
        raise ArcPlanError(f"failed to load existing arc_plan.yaml: {exc}") from exc
    
    # Determine which orders are already planned
    known_orders = {seg['order'] for seg in existing_segments}
    missing_orders = set(expected_orders) - known_orders
    
    # If all orders are already planned, return early
    if not missing_orders:
        log.debug("All %d segments already planned, returning existing plan", n_segs)
        return {"segments": sorted(existing_segments, key=lambda s: s['order'])}
    
    # Sort missing orders to process them in order
    missing_orders = sorted(missing_orders)
    
    # Build context once
    context = build_context(pack, config)
    
    # Initialize plan with existing segments
    plan_segments = list(existing_segments)
    
    # Process batches
    batch_size = config["arc"]["segment_hours"]
    max_attempts = config["arc"]["max_attempts"]
    
    i = 0
    while i < len(missing_orders):
        # Determine batch orders
        batch_orders = missing_orders[i:i + batch_size]
        
        # Get continuity from previous batch if available
        previous_continuity = ""
        if plan_segments:
            last_segment = plan_segments[-1]
            previous_continuity = last_segment.get("continuity_out", "")
        
        # Track problems for retry prompt
        problems = None
        
        # Try up to max_attempts times
        attempt = 0
        while attempt < max_attempts:
            attempt += 1
            
            try:
                # Build prompt
                prompt = build_prompt(
                    context=context,
                    expected_orders=batch_orders,
                    previous_continuity=previous_continuity,
                    config=config,
                    problems=problems
                )
                
                # Call LLM
                messages = [{"role": "user", "content": prompt}]
                response = llm.complete(SYSTEM_PROMPT, messages)
                
                # Parse reply
                segments = parse_reply(response)
                
                # Normalize segments
                normalized_segments = [normalize_segment(seg) for seg in segments]
                
                # Validate batch
                batch_problems = validate_batch(
                    normalized_segments, 
                    batch_orders, 
                    {seg['id'] for seg in plan_segments}, 
                    vocab, 
                    config
                )
                
                if not batch_problems:
                    # Valid batch - add to plan
                    plan_segments.extend(normalized_segments)
                    break  # Success, exit retry loop
                    
                else:
                    # Validation failed, prepare for retry
                    problems = batch_problems
                    log.warning("Batch validation failed (attempt %d/%d): %s", 
                               attempt, max_attempts, "; ".join(batch_problems))
                    
            except Exception as exc:
                # Log error and continue to next attempt
                log.warning("LLM call failed (attempt %d/%d): %s", 
                           attempt, max_attempts, str(exc))
                
            # If we've reached max attempts, skip this batch
            if attempt >= max_attempts:
                log.warning("Skipping batch for orders %s after %d attempts", 
                           batch_orders, max_attempts)
                break
                
        # Write plan after each batch (even skipped ones)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, 'w', encoding='utf-8') as f:
            yaml.dump({"segments": sorted(plan_segments, key=lambda s: s['order'])}, 
                     f, allow_unicode=True, default_flow_style=False)
        
        # Move to next batch
        i += len(batch_orders)
    
    # Final sort and return
    segments = sorted(plan_segments, key=lambda s: s['order'])
    log.info("Planned %d segments, skipped %d", 
             len([s for s in segments if s['order'] in expected_orders]), 
             len(expected_orders) - len(segments))
    
    return {"segments": segments}
