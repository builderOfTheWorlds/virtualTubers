"""
Scan-and-assign pass for the 3-layer offline content generator.

The existing batch generator allocates take numbers lazily: `batch_generate._next_take_number`
globs the directory for `*.yaml` and returns `max(...) + 1`. That is correct for one sequential
writer and broken for a pool of eight: two workers both see `002.yaml` as the highest,
both claim take 3, and one silently overwrites the other's work.

The fix is NOT a lock around the allocator. It is to stop allocating lazily.
Scan the output tree once, compute every `(segment, slot, take)` that is not
yet on disk, assign each one its deterministic path up front, and emit an
ordered list. Four things fall out of that single change:

  - race-freedom: every path is decided before any thread starts;
  - idempotent resume: re-running the scan IS the resume, because a take
    already on disk is simply not planned again;
  - a hole in the middle gets refilled — lazy allocation would see `003` as
    the max and plan take 4, leaving the missing `002` unfilled forever;
  - an exact work count and ETA before an operator commits to a two-day run.

This module is nearly pure: it reads the output tree (existence checks) and,
in `build_worklist`, reads each segment's `brief.yaml`. No LLM, no network,
no writes.
"""
import logging
import pathlib
import yaml
from dataclasses import dataclass
from typing import Dict, List, Optional, Union

log = logging.getLogger(__name__)


class WorklistError(ValueError):
    """Raised for a malformed brief, a missing brief file, or a nonsensical estimate argument."""
    pass


@dataclass(frozen=True)
class WorkUnit:
    """A unit of work for the 3-layer generation pipeline."""
    segment_id: str
    slot_id: str
    take: int                    # 1-based
    conditions: dict             # {} means the neutral take
    path: pathlib.Path           # where this take will be written
    slot: dict                   # the whole slot from the brief


def take_path(segment_root: pathlib.Path, slot_id: str, take: int) -> pathlib.Path:
    """Returns `segment_root / "slots" / slot_id / f"{take:03d}.yaml"`."""
    return segment_root / "slots" / slot_id / f"{take:03d}.yaml"


def conditions_for(slot: dict, take: int, config: dict) -> dict:
    """
    The condition mapping for a 1-based `take` on one ambient slot.
    
    Rules, in order:
      - `take <= neutral_takes`  -> `{}`. Always. No exceptions.
      - slot["sensitivity"] == "none"  -> `{}` for every take. The line
        reads the same in any state; conditioning it would only shrink
        the pool the runtime can draw from.
      - otherwise build an ordered CANDIDATE list and index into it with
        `(take - neutral_takes - 1) % len(candidates)`:
          * sensitivity "tone": one candidate per declared mood, in
            config order — `{"mood": m}` for each m in
            config["state"]["moods"].
          * sensitivity "flags": for each flag in slot["depends_on"], IN
            ORDER, two candidates: `{flag: True}` then `{flag: False}`.
            Both polarities of the first flag come before the second
            flag's — with the usual budget of two conditioned takes and
            one dependency, the useful split is true/false.
      - if the candidate list is EMPTY (e.g. sensitivity "flags" with an
        empty `depends_on`, or "tone" with no declared moods), return
        `{}`. Nothing to condition on; a neutral take is still a useful
        take.

    The modulo means more takes than distinct conditions is NOT an error
    — a second take under the same condition is ordinary variety.
    """
    # Default neutral_takes to 1 if not specified
    neutral_takes = config.get("dialogue", {}).get("neutral_takes", 1)
    
    # Take 1 is ALWAYS neutral
    if take <= neutral_takes:
        return {}
    
    # No conditioning if sensitivity is "none"
    if slot.get("sensitivity") == "none":
        return {}
    
    candidates = []
    
    # Build candidate list based on sensitivity
    if slot.get("sensitivity") == "tone":
        moods = config.get("state", {}).get("moods", [])
        candidates = [{"mood": mood} for mood in moods]
    elif slot.get("sensitivity") == "flags":
        depends_on = slot.get("depends_on", [])
        for flag in depends_on:
            candidates.extend([{flag: True}, {flag: False}])
    
    # If no candidates, return neutral
    if not candidates:
        return {}
    
    # Index into candidates with modulo
    index = (take - neutral_takes - 1) % len(candidates)
    return candidates[index]


def units_for_segment(segment_id: str, brief: dict, segment_root: pathlib.Path,
                      config: dict) -> List[WorkUnit]:
    """
    Plan one segment. `brief` is the loaded brief mapping; `segment_root`
    is the directory that segment's output lives under.
    
      - `brief["slots"]` is required. A brief with no "slots" key raises
        WorklistError — an empty worklist and a malformed brief look
        identical from the outside, and a two-day run must not exit in
        three seconds reporting success.
      - Slots with `kind == "spine"` produce NO units: spine dialogue is
        authored in the campaign pack, and Layer 3 only fills ambient.
      - Every other slot must have a "slot_id"; a slot without one raises
        WorklistError naming the segment.
      - For each ambient slot, for take in 1..config["dialogue"]["takes_per_slot"]:
        SKIP the take if `take_path(...)` already exists on disk;
        otherwise emit a WorkUnit.
    Order: slots in brief order, takes ascending within a slot. Draining
    in that order means stopping the run at any point leaves a contiguous
    usable prefix of airtime rather than a scattered fraction.

    Condition assignment keys off the TAKE NUMBER, never off position in
    the returned list. If take 1 already exists, the first planned unit is
    take 2 and it must still carry take 2's condition.
    """
    log.debug("units_for_segment called with segment_id=%s", segment_id)
    
    if "slots" not in brief:
        msg = f"brief for segment {segment_id} has no 'slots' key"
        log.error(msg)
        raise WorklistError(msg)
    
    takes_per_slot = config.get("dialogue", {}).get("takes_per_slot", 3)
    
    units = []
    planned = 0
    skipped = 0
    
    for slot in brief["slots"]:
        # Skip spine slots
        if slot.get("kind") == "spine":
            continue
            
        # Validate slot has an ID
        if "slot_id" not in slot:
            msg = f"segment {segment_id} has a slot without 'slot_id'"
            log.error(msg)
            raise WorklistError(msg)
            
        slot_id = slot["slot_id"]
        slot_root = segment_root / "slots" / slot_id
        
        # For each take, check if it exists and plan accordingly
        for take in range(1, takes_per_slot + 1):
            path = take_path(segment_root, slot_id, take)
            
            # If the file already exists, skip it
            if path.exists():
                skipped += 1
                continue
                
            conditions = conditions_for(slot, take, config)
            unit = WorkUnit(
                segment_id=segment_id,
                slot_id=slot_id,
                take=take,
                conditions=conditions,
                path=path,
                slot=slot
            )
            units.append(unit)
            planned += 1
    
    log.debug("segment %s: planned %d, skipped %d", segment_id, planned, skipped)
    return units


def build_worklist(output_root: pathlib.Path, segment_ids: List[str],
                   config: dict) -> List[WorkUnit]:
    """
    The whole-run pass. For each segment id IN THE GIVEN ORDER, read
    `output_root / "segments" / <segment_id> / "brief.yaml"` with
    `yaml.safe_load`, then delegate to `units_for_segment` with
    `segment_root = output_root / "segments" / <segment_id>`.
    Concatenate.

    A missing or unreadable brief raises WorklistError naming the
    segment, logged at ERROR first.
    """
    log.debug("build_worklist called with output_root=%s, segment_ids=%s", output_root, segment_ids)
    
    units = []
    
    for segment_id in segment_ids:
        segment_root = output_root / "segments" / segment_id
        brief_path = segment_root / "brief.yaml"
        
        try:
            with open(brief_path, "r", encoding="utf-8") as f:
                brief = yaml.safe_load(f)
        except OSError as exc:
            msg = f"could not read brief for segment {segment_id}: {exc}"
            log.error(msg)
            raise WorklistError(msg) from exc
        except yaml.YAMLError as exc:
            msg = f"malformed YAML in brief for segment {segment_id}: {exc}"
            log.error(msg)
            raise WorklistError(msg) from exc
            
        segment_units = units_for_segment(segment_id, brief, segment_root, config)
        units.extend(segment_units)
    
    log.info("build_worklist completed: %d total units", len(units))
    return units


def estimate_seconds(units: List[WorkUnit], seconds_per_take: float,
                     concurrency: int) -> float:
    """
    `len(units) * seconds_per_take / concurrency`. An empty list is 0.
    A `concurrency` less than 1 raises WorklistError — a divide-by-zero
    traceback is a poor way to learn the config is wrong.
    """
    log.debug("estimate_seconds called with units=%d, seconds_per_take=%f, concurrency=%d",
              len(units), seconds_per_take, concurrency)
    
    if concurrency < 1:
        msg = f"concurrency must be at least 1, got {concurrency}"
        log.error(msg)
        raise WorklistError(msg)
        
    return len(units) * seconds_per_take / concurrency
