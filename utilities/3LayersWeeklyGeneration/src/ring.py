"""
Ring-composition math for the arc planner.

Implements the core, campaign-agnostic mechanics from
`.claude/prompts/ring_composition_spec.md` (v3.3): partition-based polarity
(LAW B), the asymmetric mirror-pairing rule (section 2), and the
keystone-first/descent/ascent generation phase order (section 8.3).

**Scope (MVP):** a single top-level ring covering the whole loop —
`plot_0` only. The spec's multi-layer (`plot_1..n`), braided-mode,
layer-level-mirroring, and connective-tether machinery (sections 4.2-4.6)
is intentionally NOT implemented here. Everything in this module is
written so that scope can be added later without breaking the shape
(`plot_path` is already a list of entries, ready for more layers).

All functions are pure; nothing here talks to an LLM.
"""
import logging
from fractions import Fraction
from typing import Dict, List, Tuple

log = logging.getLogger(__name__)

# LAW B: polarity is the product of the path. A descent preserves the
# parent's direction; a keystone and an ascent both invert it.
SIGN = {"descent": 1, "keystone": -1, "ascent": -1}

# Closed vocabulary for `mirror_transform` (spec section 6.5).
MIRROR_TRANSFORMS = [
    "knowledge_gained",
    "stakes_raised",
    "role_reversed",
    "bond_proven",
    "bond_broken",
    "debt_paid",
    "promise_betrayed",
    "cost_revealed",
]

# The subset of mirror_transform values legal on the loop-closing segment
# (spec section 6.6, rule 3) — not enforced by build_plot0_plan/validate_ring_arc
# in this MVP (no loop-seam handling yet), kept here so it is defined once.
SEAM_CLOSING_TRANSFORMS = {"debt_paid", "bond_proven", "bond_broken", "cost_revealed"}


class RingConfigError(ValueError):
    """Raised for a ring partition that cannot produce a valid plan."""
    pass


def child_polarity(parent_polarity: int, child_role: str) -> int:
    """LAW B: polarity(node) = polarity(parent) * SIGN[role(node)]."""
    return parent_polarity * SIGN[child_role]


def traversal(polarity: int) -> Tuple[str, str, str]:
    """Order in which a ring's parts run, given its own polarity."""
    return ("descent", "keystone", "ascent") if polarity == 1 \
        else ("ascent", "keystone", "descent")


def mirror_partners(d: int, a: int) -> Dict[int, List[int]]:
    """Map each descent index (0..d-1) to the ascent indices (0..a-1) it
    mirrors, via keystone-distance (`u`) interval overlap (spec section 2.2).

    Verified properties (spec section 2.2, exhaustively for d,a in 1..12):
    symmetric, total (nothing orphaned), and reduces to exactly
    `mirror(i) = d-1-i` when `d == a`.

    Uses exact Fraction arithmetic — floats produce boundary-touching
    false positives at partition sizes that share factors.
    """
    if d < 1 or a < 1:
        raise RingConfigError(f"d and a must each be >= 1, got d={d}, a={a}")
    out: Dict[int, List[int]] = {}
    for i in range(d):
        lo, hi = Fraction(d - i - 1, d), Fraction(d - i, d)
        out[i] = [
            j for j in range(a)
            if min(hi, Fraction(j + 1, a)) > max(lo, Fraction(j, a))
        ]
    return out


def build_plot0_plan(n_segments: int, parts: Tuple[int, int, int]) -> List[Dict]:
    """Build the plot_0 ring plan: one top-level ring `[descent | keystone |
    ascent]` covering orders `[0, n_segments)`.

    Returns one dict per order, sorted by order:
        {order, role, polarity, u_lo, u_hi, mirror_of}

    `polarity` is +1 (forward) or -1 (reflected). `u_lo`/`u_hi` are exact
    fractions rendered as strings ("3/4"), never floats. `mirror_of` is the
    list of absolute order numbers this segment mirrors — populated on
    both the descent and ascent side of a pair (so validation can be run
    from either), empty for keystone orders (a keystone has no mirror,
    per spec section 2 — the u-interval pairing is defined only between
    descent and ascent).

    Raises RingConfigError if `parts` does not sum to `n_segments` or any
    part is < 1.
    """
    d, k, a = parts
    if d + k + a != n_segments:
        raise RingConfigError(
            f"parts {parts} sum to {d + k + a}, expected n_segments={n_segments}"
        )
    if d < 1 or k < 1 or a < 1:
        raise RingConfigError(f"parts must each be >= 1, got {parts}")

    partners = mirror_partners(d, a)  # descent_index -> [ascent_index, ...]
    reverse: Dict[int, List[int]] = {j: [] for j in range(a)}
    for i, js in partners.items():
        for j in js:
            reverse[j].append(i)

    plan: List[Dict] = []

    for i in range(d):
        order = i
        u_lo, u_hi = Fraction(d - i - 1, d), Fraction(d - i, d)
        mirror_orders = sorted(d + k + j for j in partners[i])
        plan.append({
            "order": order, "role": "descent", "polarity": 1,
            "u_lo": str(u_lo), "u_hi": str(u_hi), "mirror_of": mirror_orders,
        })

    for m in range(k):
        order = d + m
        plan.append({
            "order": order, "role": "keystone", "polarity": -1,
            "u_lo": "0", "u_hi": "0", "mirror_of": [],
        })

    for j in range(a):
        order = d + k + j
        u_lo, u_hi = Fraction(j, a), Fraction(j + 1, a)
        mirror_orders = sorted(reverse[j])
        plan.append({
            "order": order, "role": "ascent", "polarity": -1,
            "u_lo": str(u_lo), "u_hi": str(u_hi), "mirror_of": mirror_orders,
        })

    plan.sort(key=lambda s: s["order"])
    log.debug("build_plot0_plan(n_segments=%d, parts=%s) -> %d entries",
              n_segments, parts, len(plan))
    return plan


def generation_phases(ring_plan: List[Dict]) -> Tuple[List[int], List[int], List[int]]:
    """Split a ring plan into the three generation phases required by spec
    section 8.3: all keystones first, then descent orders ascending, then
    ascent orders ascending. Each list is sorted ascending.
    """
    keystone = sorted(s["order"] for s in ring_plan if s["role"] == "keystone")
    descent = sorted(s["order"] for s in ring_plan if s["role"] == "descent")
    ascent = sorted(s["order"] for s in ring_plan if s["role"] == "ascent")
    return keystone, descent, ascent
