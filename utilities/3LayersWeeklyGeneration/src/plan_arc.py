"""
Layer 1 orchestrator for the 3-layer offline content generator.

This module owns resume, batching, retry/skip, and writing for the arc planner.
It coordinates with arc_schema.py for parsing, validation, and prompt construction,
and (when `config["ring"]["enabled"]` is true) with ring.py for ring-composition
structure (ring_composition_spec.md v3.3, plot_0 only in this build — see
ring.py's module docstring for the scope note).
"""
import logging
import yaml
from pathlib import Path
from typing import Dict, List, Optional, Union

from arc_schema import (SYSTEM_PROMPT, ArcPlanError, build_context,
                        build_prompt, n_segments, normalize_segment,
                        parse_reply, validate_batch)
from ring import build_plot0_plan, generation_phases

log = logging.getLogger(__name__)


def _apply_ring_metadata(plan_segments: List[Dict], ring_roles: Dict[int, Dict]) -> None:
    """Stamp `plot_path` and resolve `mirror_of` (order numbers -> ids) onto
    every segment in `plan_segments` that has a ring role. Mutates segments
    in place. Idempotent and safe to call after every batch, including on
    resume, since it is purely a function of `order` and the current plan.

    `mirror_of` entries whose partner order is not yet planned are silently
    omitted rather than left as bare order numbers — spec section 5.1 defines
    `mirror_of` as a list of ids, and a partially-resolved list would be
    ambiguous with a genuinely-empty one (legal for keystones).
    """
    by_order = {seg["order"]: seg for seg in plan_segments}
    for order, seg in by_order.items():
        info = ring_roles.get(order)
        if info is None:
            continue
        seg["plot_path"] = [{
            "plot": "plot_0",
            "ring": "plot_0.0",
            "role": info["role"],
            "polarity": "forward" if info["polarity"] == 1 else "reflected",
            "u_lo": info["u_lo"],
            "u_hi": info["u_hi"],
        }]
        seg["mirror_of"] = [
            by_order[m]["id"] for m in info["mirror_of"] if m in by_order
        ]


def _build_mirror_briefs(batch_orders: List[int], ring_roles: Dict[int, Dict],
                          plan_segments: List[Dict]) -> Dict[int, List[Dict]]:
    """For every ascent order in `batch_orders`, collect the mirror brief
    (spec section 6.4: id, synopsis, continuity_out, carry_out) of each
    already-planned mirror partner. An ascent whose partner(s) are not yet
    planned (e.g. a partial resume) simply gets no brief for this call —
    build_prompt then has nothing to render for that order, which is honest:
    there is nothing yet to be informed by.
    """
    by_order = {seg["order"]: seg for seg in plan_segments}
    briefs: Dict[int, List[Dict]] = {}
    for order in batch_orders:
        info = ring_roles.get(order)
        if not info or info["role"] != "ascent":
            continue
        partners = [by_order[m] for m in info["mirror_of"] if m in by_order]
        if partners:
            briefs[order] = [
                {
                    "id": p["id"],
                    "synopsis": p["synopsis"],
                    "continuity_out": p.get("continuity_out", ""),
                    "carry_out": p.get("carry_out", {}),
                }
                for p in partners
            ]
    return briefs


def _dedupe_colliding_ids(segments: List[Dict], known_ids: set) -> List[Dict]:
    """Resolve any segment id in `segments` that collides with `known_ids` or
    with an earlier segment in this same batch, by appending a deterministic
    `-altN` suffix. Mutates and returns `segments`.

    The model is told explicitly (build_prompt's known_ids section) to suffix
    a repeat-visit id instead of reusing the bare one, but in practice a
    30B-class local model does not reliably follow that instruction — the
    observed failure mode against a real run was an otherwise well-formed
    batch (correct roles, valid content) discarded entirely because of a
    trivial reused id string. The id is an arbitrary label with no narrative
    meaning; resolving the collision here recovers the content instead of
    wasting a full retry cycle on it. Content-level checks (spine scenes,
    carry keys, mirror_transform, etc.) still run in validate_batch
    afterward, unaffected by this rename.
    """
    seen = set(known_ids)
    for seg in segments:
        base = seg["id"]
        candidate = base
        n = 2
        while candidate in seen:
            candidate = f"{base}-alt{n}"
            n += 1
        if candidate != base:
            log.info("resolved segment id collision: %r -> %r", base, candidate)
            seg["id"] = candidate
        seen.add(candidate)
    return segments


def _run_batch(batch_orders, context, plan_segments, config, vocab,
               spine_scene_ids, llm, on_llm_progress, max_attempts,
               ring_roles=None) -> None:
    """Run one batch's retry loop. Appends successful segments to
    `plan_segments` in place; logs and leaves the batch's orders un-planned
    (a hole) if every attempt fails or fails to validate.
    """
    by_order = {seg["order"]: seg for seg in plan_segments}
    prior = by_order.get(batch_orders[0] - 1)
    previous_continuity = prior["continuity_out"] if prior else ""

    batch_ring_roles = None
    mirror_briefs = None
    if ring_roles:
        batch_ring_roles = {o: ring_roles[o] for o in batch_orders if o in ring_roles}
        mirror_briefs = _build_mirror_briefs(batch_orders, ring_roles, plan_segments) or None

    problems = None
    attempt = 0
    while attempt < max_attempts:
        attempt += 1
        try:
            prompt = build_prompt(
                context=context,
                expected_orders=batch_orders,
                previous_continuity=previous_continuity,
                config=config,
                problems=problems,
                spine_scene_ids=spine_scene_ids,
                known_ids={seg['id'] for seg in plan_segments},
                ring_roles=batch_ring_roles,
                mirror_briefs=mirror_briefs,
            )

            messages = [{"role": "user", "content": prompt}]
            if on_llm_progress is not None and hasattr(llm, "complete_streaming"):
                response = llm.complete_streaming(SYSTEM_PROMPT, messages,
                                                   on_progress=on_llm_progress)
            else:
                response = llm.complete(SYSTEM_PROMPT, messages)

            segments = parse_reply(response)
            normalized_segments = [normalize_segment(seg) for seg in segments]
            _dedupe_colliding_ids(
                normalized_segments, {seg['id'] for seg in plan_segments}
            )

            batch_problems = validate_batch(
                normalized_segments,
                batch_orders,
                {seg['id'] for seg in plan_segments},
                vocab,
                config,
                ring_roles=batch_ring_roles,
            )

            if not batch_problems:
                plan_segments.extend(normalized_segments)
                return

            problems = batch_problems
            log.warning("Batch validation failed (attempt %d/%d): %s",
                       attempt, max_attempts, "; ".join(batch_problems))

        except Exception as exc:
            log.warning("LLM call failed (attempt %d/%d): %s",
                       attempt, max_attempts, str(exc))

        if attempt >= max_attempts:
            log.warning("Skipping batch for orders %s after %d attempts",
                       batch_orders, max_attempts)
            return


def plan_arc(pack, config, llm, vocab, out_path, on_llm_progress=None) -> dict:
    """
    Plan an arc by generating segments in batches.
    
    Args:
        pack: The campaign pack
        config: The configuration dictionary
        llm: Object with complete(system_prompt, messages) method
        vocab: Vocabulary object for validation
        out_path: pathlib.Path to write the plan YAML
        on_llm_progress: Optional callback for live token-decode progress
            during the LLM call — forwarded to llm.complete_streaming's
            on_progress when the llm object supports streaming (see
            concurrent_llm.PooledOllamaClient.complete_streaming). Ignored
            (falls back to llm.complete) for any llm object that doesn't
            expose complete_streaming — this keeps every existing test's
            plain-.complete() fake llm working unchanged.
        
    Returns:
        Dictionary mapping {"segments": [...]} sorted by order

    Raises:
        ArcPlanError: on an unreadable existing plan, OR — when
            `config["ring"]["enabled"]` is true — when the plot_0 keystone
            could not be planned after exhausting retries. Per
            ring_composition_spec.md section 8.3: "A skipped plot_0 keystone
            is fatal: raise rather than continue. An arc without its centre
            is not degraded, it is structureless." Every other batch may
            still exhaust attempts and skip with a warning.
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
    
    # Build context once
    context = build_context(pack, config)

    # Closed list of legal spine_scenes ids — same "non-ambient" filter
    # build_context uses, computed once so every batch's prompt states the
    # exact vocabulary validate_batch will check against.
    spine_scene_ids = [
        scene_id for scene_id, scene in pack.scenes.items() if not scene.ambient
    ]
    
    # Initialize plan with existing segments
    plan_segments = list(existing_segments)
    
    batch_size = config["arc"]["batch_size"]
    max_attempts = config["arc"]["max_attempts"]

    # Ring composition (ring_composition_spec.md v3.3, plot_0 only — see
    # ring.py). Gated on config so every existing caller/config that omits
    # `ring` gets byte-identical behavior to before this feature existed:
    # one phase, batched in ascending order, no ring vocabulary in prompts.
    ring_cfg = config.get("ring", {})
    ring_enabled = bool(ring_cfg.get("enabled", False))
    ring_roles: Dict[int, Dict] = {}
    if ring_enabled:
        parts = tuple(ring_cfg["parts"])
        ring_plan = build_plot0_plan(n_segs, parts)
        ring_roles = {s["order"]: s for s in ring_plan}
        keystone_orders, descent_orders, ascent_orders = generation_phases(ring_plan)
        phases = [("keystone", keystone_orders),
                  ("descent", descent_orders),
                  ("ascent", ascent_orders)]
    else:
        phases = [("all", expected_orders)]

    def write_plan():
        _apply_ring_metadata(plan_segments, ring_roles)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, 'w', encoding='utf-8') as f:
            yaml.dump({"segments": sorted(plan_segments, key=lambda s: s['order'])},
                     f, allow_unicode=True, default_flow_style=False)

    for phase_name, phase_orders in phases:
        phase_missing = sorted(o for o in phase_orders if o in missing_orders)
        i = 0
        while i < len(phase_missing):
            batch_orders = phase_missing[i:i + batch_size]
            _run_batch(batch_orders, context, plan_segments, config, vocab,
                      spine_scene_ids, llm, on_llm_progress, max_attempts,
                      ring_roles=ring_roles if ring_enabled else None)
            write_plan()
            i += len(batch_orders)

        if ring_enabled and phase_name == "keystone":
            planned_orders = {seg["order"] for seg in plan_segments}
            still_missing = [o for o in phase_orders if o not in planned_orders]
            if still_missing:
                raise ArcPlanError(
                    f"plot_0 keystone order(s) {still_missing} could not be "
                    f"planned after {max_attempts} attempts each — an arc "
                    f"without its keystone is structureless, not degraded "
                    f"(ring_composition_spec.md section 8.3)"
                )

    # Final sort and return
    segments = sorted(plan_segments, key=lambda s: s['order'])
    log.info("Planned %d segments, skipped %d", 
             len([s for s in segments if s['order'] in expected_orders]), 
             len(expected_orders) - len(segments))
    
    return {"segments": segments}
