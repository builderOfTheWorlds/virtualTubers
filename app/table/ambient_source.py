"""Ambient (plot-free) office scenes: the filler that plays between story blocks' spines.

Golive plan D1 + "Seam: ambient office scene". A pack ambient scene is a normal pack scene YAML
(``id, title, ambient: true, prompt, lore, source``) plus three office keys that pack.py ignores:

    day_phase: off_hours | morning | workday | evening      (a list, or ``any``, also works)
    intensity: quiet | normal                               (default: quiet in off_hours)
    seats: [cast ids]                                       (who is present)

Sources: the generator Postgres ``pack_scenes(pack_name, scene_id, scene_yaml)`` (production)
and a YAML directory (tests / staging, e.g. .claude/prompts/office_ambient_stage). Both can be
merged; Postgres wins on an id clash.

An ambient scene becomes a ``SceneContract`` flagged ``ambient`` (canon_goal = the prompt,
nothing to resolve, seats = its ``seats`` intersected with the ACTIVE seats). The runtime
never lets an ambient scene move the spine position or write story state.
"""
from __future__ import annotations

import dataclasses
import logging
import random
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import yaml

from table import block_clock
from table.contract import SceneContract, _norm, safe_id

log = logging.getLogger(__name__)

QUIET, NORMAL = "quiet", "normal"
DEFAULT_NO_REPEAT_S = 48 * 3600
DEFAULT_ROUNDS = {NORMAL: 2, QUIET: 1}


@dataclasses.dataclass(frozen=True)
class AmbientScene:
    id: str
    title: str
    prompt: str
    day_phases: Tuple[str, ...]
    intensity: str
    seats: Tuple[str, ...]          # cast ids
    lore: Tuple[str, ...] = ()
    source: str = ""                # where it was loaded from (dir path / "postgres")


def parse_scene(doc: Any, source: str = "") -> Optional[AmbientScene]:
    """An AmbientScene from a parsed scene YAML, or None when it is not an ambient scene.
    Raises ValueError for an ambient scene that is malformed (callers log and skip it)."""
    if not isinstance(doc, Mapping) or not doc.get("ambient"):
        return None
    sid = str(doc.get("id") or "").strip()
    prompt = str(doc.get("prompt") or "").strip()
    if not sid or not prompt:
        raise ValueError(f"ambient scene needs id and prompt (id={sid!r})")
    raw_phase = doc.get("day_phase")
    if raw_phase in (None, "", "any", "all"):
        phases = block_clock.PHASES if raw_phase in ("any", "all") else ()
    else:
        phases = tuple(str(p) for p in (raw_phase if isinstance(raw_phase, (list, tuple)) else [raw_phase]))
    bad = [p for p in phases if p not in block_clock.PHASES]
    if bad or not phases:
        raise ValueError(f"ambient scene {sid!r}: bad day_phase {raw_phase!r}")
    intensity = str(doc.get("intensity") or (QUIET if phases == (block_clock.OFF_HOURS,) else NORMAL))
    if intensity not in (QUIET, NORMAL):
        raise ValueError(f"ambient scene {sid!r}: bad intensity {intensity!r}")
    seats = doc.get("seats") or []
    if not isinstance(seats, (list, tuple)) or not all(isinstance(s, str) for s in seats):
        raise ValueError(f"ambient scene {sid!r}: seats must be a list of cast ids")
    lore = doc.get("lore") or []
    return AmbientScene(
        id=sid, title=str(doc.get("title") or sid), prompt=prompt, day_phases=phases,
        intensity=intensity, seats=tuple(seats),
        lore=tuple(str(x) for x in lore) if isinstance(lore, (list, tuple)) else (), source=source)


def parse_yaml_text(text: str, source: str = "") -> List[AmbientScene]:
    """Every ambient scene in a YAML text (one doc, several docs, or a list of scenes)."""
    out: List[AmbientScene] = []
    try:
        docs = list(yaml.safe_load_all(text))
    except yaml.YAMLError as exc:       # one bad row must not cost the whole pool
        log.warning("ambient scene skipped (%s): unreadable YAML: %s", source, exc)
        return out
    for doc in docs:
        for item in (doc if isinstance(doc, list) else [doc]):
            try:
                scene = parse_scene(item, source)
            except ValueError as exc:
                log.warning("ambient scene skipped (%s): %s", source, exc)
                continue
            if scene is not None:
                out.append(scene)
    return out


def load_dir(path) -> List[AmbientScene]:
    """Ambient scenes from every *.yaml / *.yml under `path` (recursive); [] when missing."""
    root = Path(path)
    if not root.is_dir():
        log.warning("ambient dir not found: %s", root)
        return []
    out: List[AmbientScene] = []
    for f in sorted([*root.rglob("*.yaml"), *root.rglob("*.yml")]):
        try:
            out.extend(parse_yaml_text(f.read_text(encoding="utf-8"), str(f)))
        except (OSError, yaml.YAMLError) as exc:
            log.warning("ambient file unreadable %s: %s", f, exc)
    return out


def load_postgres(pack: str, connect=None) -> List[AmbientScene]:
    """Ambient scenes of `pack` from pack_scenes (generator DB, read-only). Raises on a
    connection problem so the caller can keep its previous pool."""
    if connect is None:
        from table import arc_source
        connect = arc_source.connect_generator
    conn = connect()
    try:
        cur = conn.cursor()
        try:
            cur.execute("SELECT scene_id, scene_yaml FROM pack_scenes WHERE pack_name = %s "
                        "ORDER BY scene_id", (pack,))
            rows = cur.fetchall()
        finally:
            cur.close()
            try:
                conn.rollback()
            except Exception:  # pragma: no cover - connection already gone
                pass
    finally:
        try:
            conn.close()
        except Exception:  # pragma: no cover
            pass
    out: List[AmbientScene] = []
    for scene_id, text in rows:
        out.extend(parse_yaml_text(text, f"postgres:{pack}/{scene_id}"))
    return out


def load_pool(cfg: Mapping[str, Any], default_pack: str = "", connect=None) -> "AmbientPool":
    """Pool from the ``ambient:`` config: ``postgres`` (default on, pack = ambient.pack or the
    table pack) and/or ``dir``. A Postgres failure with a dir configured falls back to the dir
    and is logged; with neither source the error propagates."""
    scenes: Dict[str, AmbientScene] = {}
    errors = []
    if cfg.get("dir"):
        for s in load_dir(cfg["dir"]):
            scenes[s.id] = s
    if cfg.get("postgres", True):
        pack = str(cfg.get("pack") or default_pack)
        try:
            for s in load_postgres(pack, connect):
                scenes[s.id] = s
        except Exception as exc:  # noqa: BLE001 - DB down must not stop the table
            errors.append(exc)
            log.warning("ambient pool: postgres load failed (pack=%s): %s", pack, exc)
    if errors and not scenes and not cfg.get("dir"):
        raise errors[0]
    return AmbientPool(list(scenes.values()))


def participants_for(scene: AmbientScene, seat_of: Mapping[str, str],
                     active_seats: Iterable[str]) -> List[str]:
    """The scene's cast ids as seats, kept only when ACTIVE (the GM seat, the silent observer
    and unseated cast drop out). Order follows the scene's `seats`."""
    lookup: Dict[str, str] = {}
    for key, seat in seat_of.items():
        lookup.setdefault(str(key), seat)
        lookup.setdefault(_norm(key), seat)
    active = set(active_seats)
    out: List[str] = []
    for cast_id in scene.seats:
        seat = lookup.get(cast_id) or lookup.get(_norm(cast_id))
        if seat in active and seat not in out:
            out.append(seat)
    return out


def effective_intensity(scene: AmbientScene, phase: str) -> str:
    return QUIET if (scene.intensity == QUIET or phase == block_clock.OFF_HOURS) else NORMAL


def to_contract(scene: AmbientScene, participants: Sequence[str], *, phase: str, run_id: str,
                scene_id: str, rounds: Optional[Mapping[str, int]] = None,
                max_retries_per_turn: int = 2) -> SceneContract:
    """The ambient SceneContract: canon_goal = the prompt, nothing to resolve."""
    rounds = {**DEFAULT_ROUNDS, **(rounds or {})}
    intensity = effective_intensity(scene, phase)
    return SceneContract(
        scene_id=scene_id, run_id=run_id, segment_id="ambient", leaf_id=phase,
        slot_id=safe_id(scene.id), order=-1, canon_goal=scene.prompt,
        participants=tuple(participants), lore=scene.lore, continuity_in="", continuity_out="",
        must_resolve=(), expected_beats=(), max_rounds=max(1, int(rounds[intensity])),
        max_retries_per_turn=max_retries_per_turn, scene_ref="",
        ambient=True, ambient_id=scene.id, day_phase=phase, intensity=intensity)


@dataclasses.dataclass(frozen=True)
class Pick:
    scene: AmbientScene
    participants: Tuple[str, ...]
    fell_back: bool = False         # no scene for this phase; used another phase's
    relaxed: bool = False           # every candidate was inside the no-repeat window


class AmbientPool:
    def __init__(self, scenes: Iterable[AmbientScene] = ()):
        self.scenes: List[AmbientScene] = list(scenes)

    def __len__(self) -> int:
        return len(self.scenes)

    def for_phase(self, phase: str) -> List[AmbientScene]:
        return [s for s in self.scenes if phase in s.day_phases]

    def pick(self, phase: str, *, now_ts: float, history: Sequence[Mapping[str, Any]],
             seat_of: Mapping[str, str], active_seats: Iterable[str],
             last_speakers: Iterable[str] = (), no_repeat_s: float = DEFAULT_NO_REPEAT_S,
             rng: Optional[random.Random] = None) -> Optional[Pick]:
        """The next ambient scene for `phase`, or None when nothing can be played.

        Eligible: phase match with at least one ACTIVE seat present. Order of preference:
          1. not aired within `no_repeat_s` (only relaxed when the pool is exhausted),
          2. seats that did NOT just speak (fewest overlap with `last_speakers`),
          3. least recently used (never aired = oldest), then random."""
        rng = rng or random.Random()
        active = list(active_seats)
        fell_back = False
        cands = self.for_phase(phase)
        if not cands:
            # An empty phase must not stall the run: borrow from the other phases, but never
            # air a daytime scene in the dead of night (off_hours takes quiet scenes only).
            if phase == block_clock.OFF_HOURS:
                cands = [s for s in self.scenes if s.intensity == QUIET]
            else:
                cands = [s for s in self.scenes if block_clock.OFF_HOURS not in s.day_phases
                         or len(s.day_phases) > 1]
            fell_back = bool(cands)
        located = []
        for s in cands:
            parts = participants_for(s, seat_of, active)
            if parts:
                located.append((s, tuple(parts)))
        if not located:
            return None
        last_used: Dict[str, float] = {}
        for h in history or ():
            hid = str(h.get("id"))
            last_used[hid] = max(last_used.get(hid, float("-inf")), float(h.get("at") or 0))
        fresh = [(s, p) for s, p in located
                 if now_ts - last_used.get(s.id, float("-inf")) >= no_repeat_s]
        relaxed = not fresh
        pool = fresh or located
        speakers = set(last_speakers or ())
        scored = [((len(speakers & set(p)), last_used.get(s.id, float("-inf")), rng.random()), s, p)
                  for s, p in pool]
        scored.sort(key=lambda x: x[0])
        _, scene, parts = scored[0]
        return Pick(scene, parts, fell_back=fell_back, relaxed=relaxed)
