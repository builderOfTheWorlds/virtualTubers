"""Pure filters that remove repeated scenes from the live table's scene list.

The generator's segment planner writes sibling leaves that retell the same events
with slightly different words. The table played all of them, so the story looped.
Exact-string dedupe misses these. This module provides near-duplicate detection
for scene goals and leaf-level collapse for redundant sibling leaves.
"""
import logging
import re
from typing import List, Tuple, Dict, Any

log = logging.getLogger(__name__)

STOPWORDS: frozenset = frozenset({
    "a", "an", "the", "and", "or", "but", "of", "to",
    "in", "on", "at", "for", "from", "with", "as", "by",
    "up", "is", "are", "was", "be", "their", "they", "them",
    "its", "it", "this", "that", "then", "into", "while", "so"
})


def goal_words(text) -> frozenset[str]:
    """Extract non-stopword tokens from text.

    Lowercase; tokens = re.findall(r"[a-z0-9']+", text.lower()); drop STOPWORDS.
    None/"" -> frozenset().
    """
    if not text:
        return frozenset()
    tokens = re.findall(r"[a-z0-9']+", text.lower())
    return frozenset(w for w in tokens if w not in STOPWORDS)


def jaccard(a, b) -> float:
    """Jaccard similarity between two sets.

    |a & b| / |a | b|; 0.0 if either set is empty.
    """
    if not a or not b:
        return 0.0
    intersection = len(a & b)
    union = len(a | b)
    return intersection / union


def similarity(goal_a, goal_b) -> float:
    """Jaccard similarity between the goal_words of two texts."""
    return jaccard(goal_words(goal_a), goal_words(goal_b))


def dedupe_near(payloads, *, threshold=0.6, window=8) -> Tuple[List[Dict[str, Any]], List[Tuple[str, str, float]]]:
    """Walk in order. Drop near-duplicate scenes within a sliding window.

    A payload whose goal_words is empty is always kept and never used for comparison.
    Otherwise compare against the goals of the last `window` KEPT payloads (that have
    non-empty words); if the best similarity >= threshold, drop it and record
    (dropped_scene_id, best_matching_kept_scene_id, score). Else keep.
    dropped is a list of tuples in drop order. Empty input -> ([], []).
    """
    if not payloads:
        return ([], [])

    kept: List[Dict[str, Any]] = []
    dropped: List[Tuple[str, str, float]] = []

    # Track kept payloads with non-empty goal words for comparison
    # Store as list of (scene_id, goal_words_set)
    recent_kept: List[Tuple[str, frozenset]] = []

    for payload in payloads:
        scene_id = payload.get("scene_id", "")
        canon_goal = payload.get("canon_goal", "")
        words = goal_words(canon_goal)

        if not words:
            # Empty goal: always keep, never used for comparison
            kept.append(payload)
            # Don't add to recent_kept for comparison purposes
            continue

        # Compare against recent kept payloads with non-empty words
        best_score = 0.0
        best_match_id = None

        # Look back at most `window` recent kept payloads
        start_idx = max(0, len(recent_kept) - window)
        for i in range(start_idx, len(recent_kept)):
            kept_id, kept_words = recent_kept[i]
            score = jaccard(words, kept_words)
            if score >= threshold and score > best_score:
                best_score = score
                best_match_id = kept_id

        if best_score >= threshold:
            # Drop this payload
            dropped.append((scene_id, best_match_id, best_score))
            log.info("dedupe_near: dropped scene_id=%s matched with scene_id=%s score=%.4f",
                     scene_id, best_match_id, best_score)
        else:
            # Keep this payload
            kept.append(payload)
            recent_kept.append((scene_id, words))

    log.debug("dedupe_near: kept=%d dropped=%d", len(kept), len(dropped))
    return kept, dropped


def collapse_leaves(payloads, *, threshold=0.5, min_share=0.5) -> Tuple[List[Dict[str, Any]], List[Tuple[str, str, float]]]:
    """Group CONSECUTIVE payloads by (segment_id, leaf_id) into leaves.

    Within one segment_id, the first leaf is always kept. For each later leaf of the
    same segment: a slot is "repeated" when its best similarity against any slot of the
    KEPT leaves of that segment is >= threshold. share = repeated / slots in the leaf.
    If share >= min_share drop the whole leaf and record
    (leaf_id, leaf_id_of_the_kept_leaf_holding_the_most_matched_slots, share);
    ties -> the earliest kept leaf. Leaves in a different segment_id are never compared.
    Empty input -> ([], []).
    """
    if not payloads:
        return ([], [])

    # Group consecutive payloads by (segment_id, leaf_id)
    # We need to preserve order and group consecutive same-leaf payloads
    leaves: List[List[Dict[str, Any]]] = []
    current_leaf: List[Dict[str, Any]] = []
    current_key: Tuple[str, str] = None

    for payload in payloads:
        seg_id = payload.get("segment_id", "")
        leaf_id = payload.get("leaf_id", "")
        key = (seg_id, leaf_id)

        if key != current_key:
            if current_leaf:
                leaves.append(current_leaf)
            current_leaf = [payload]
            current_key = key
        else:
            current_leaf.append(payload)

    if current_leaf:
        leaves.append(current_leaf)

    kept: List[Dict[str, Any]] = []
    dropped: List[Tuple[str, str, float]] = []

    # Track kept leaves per segment for comparison
    # kept_leaves_by_segment: segment_id -> list of (leaf_id, list of goal_words sets)
    kept_leaves_by_segment: Dict[str, List[Tuple[str, List[frozenset]]]] = {}

    for leaf_payloads in leaves:
        if not leaf_payloads:
            continue

        seg_id = leaf_payloads[0].get("segment_id", "")
        leaf_id = leaf_payloads[0].get("leaf_id", "")

        # Compute goal words for each slot in this leaf
        slot_words = [goal_words(p.get("canon_goal", "")) for p in leaf_payloads]

        # First leaf of a segment is always kept
        if seg_id not in kept_leaves_by_segment:
            kept_leaves_by_segment[seg_id] = []
            kept.extend(leaf_payloads)
            kept_leaves_by_segment[seg_id].append((leaf_id, slot_words))
            continue

        # For later leaves in the same segment, check for repetition
        segment_kept_leaves = kept_leaves_by_segment[seg_id]

        # Count repeated slots, and per kept leaf how many of them it holds (tie-break).
        kept_leaf_match_counts: Dict[str, int] = {}
        repeated_count = 0

        for slot_w in slot_words:
            if not slot_w:
                continue

            best_score = 0.0
            best_match_kept_leaf_id = None

            for kept_leaf_id, kept_slot_words_list in segment_kept_leaves:
                for kept_slot_w in kept_slot_words_list:
                    if not kept_slot_w:
                        continue
                    score = jaccard(slot_w, kept_slot_w)
                    if score >= threshold and score > best_score:
                        best_score = score
                        best_match_kept_leaf_id = kept_leaf_id

            if best_match_kept_leaf_id is not None:
                repeated_count += 1
                kept_leaf_match_counts[best_match_kept_leaf_id] = kept_leaf_match_counts.get(best_match_kept_leaf_id, 0) + 1

        total_slots = len(slot_words)
        share = repeated_count / total_slots if total_slots > 0 else 0.0

        if share >= min_share:
            # Drop the whole leaf
            # Find the kept leaf with the most matches; ties -> earliest kept leaf
            if kept_leaf_match_counts:
                # Find max matches
                max_matches = max(kept_leaf_match_counts.values())
                # Among those with max matches, find the earliest (first in segment_kept_leaves)
                best_kept_leaf_id = None
                for kept_leaf_id, _ in segment_kept_leaves:
                    if kept_leaf_match_counts.get(kept_leaf_id, 0) == max_matches:
                        best_kept_leaf_id = kept_leaf_id
                        break
            else:
                # No matches but share >= min_share? This shouldn't happen if repeated_count > 0
                # But if total_slots > 0 and repeated_count == 0, share would be 0
                # So this case means repeated_count > 0 but kept_leaf_match_counts is empty, which is impossible
                best_kept_leaf_id = segment_kept_leaves[0][0] if segment_kept_leaves else leaf_id

            dropped.append((leaf_id, best_kept_leaf_id, share))
            log.info("collapse_leaves: dropped leaf_id=%s matched with leaf_id=%s share=%.4f",
                     leaf_id, best_kept_leaf_id, share)
        else:
            # Keep the leaf
            kept.extend(leaf_payloads)
            kept_leaves_by_segment[seg_id].append((leaf_id, slot_words))

    log.debug("collapse_leaves: kept=%d dropped=%d", len(kept), len(dropped))
    return kept, dropped
