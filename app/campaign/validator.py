"""Validate a campaign pack for semantic consistency.

This module performs semantic validation of a loaded campaign pack,
ensuring internal consistency beyond structural soundness. It catches
problems that load cleanly but only explode mid-show — branch targets
pointing at scenes that do not exist, beats spoken by cast members who
were never defined, action beats naming primitives the campaign never
enabled.

The defining behaviour is to collect EVERY problem into a report instead
of raising on the first one. An operator fixing a campaign pack wants
the whole list in one pass, not a fix-rerun-fix-rerun loop.
"""
import logging
from collections import deque
from dataclasses import dataclass, field

from campaign.pack import CampaignPack, MOODS, RING_TONES, Scene
from campaign.scene_graph import WEIGHT_KEY

log = logging.getLogger(__name__)

VALID_BEAT_KINDS = frozenset({"narration", "dialogue", "action", "pane"})


class CampaignInvalid(ValueError):
    """Raised by ValidationReport.raise_if_invalid() when errors exist."""
    pass


@dataclass
class ValidationReport:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def __bool__(self) -> bool:
        return self.ok

    def raise_if_invalid(self) -> None:
        if not self.ok:
            raise CampaignInvalid("; ".join(self.errors))


def validate_pack(pack: CampaignPack) -> ValidationReport:
    """Validate a campaign pack for semantic consistency.

    Returns a report containing all errors and warnings found. Never raises.
    """
    report = ValidationReport()
    
    # Check start scene exists
    if pack.start_scene not in pack.scenes:
        report.errors.append(f"start scene {pack.start_scene!r} not found among scenes")
    
    # Find reachable scenes from start
    reachable = _reachable_scene_ids(pack)
    
    # Validate each scene
    for scene in sorted(pack.scenes.values(), key=lambda s: s.id):
        log.debug("validating scene %s", scene.id)
        _check_branches(scene, pack, report)
        _check_beats(scene, pack, report)

        # Ambient scenes are deliberately not linked into the graph — that
        # is the whole design of the AmbientScheduler, so suppress the
        # "unreachable" warning for them.
        if scene.id not in reachable and scene.id != pack.start_scene and not scene.ambient:
            report.warnings.append(f"scene {scene.id!r} is unreachable")

        # Ambient scenes generate their content at runtime from `prompt` and
        # do not need authored beats, so suppress the "no beats" warning.
        if not scene.beats and not scene.ambient:
            report.warnings.append(f"scene {scene.id!r} has no beats")

        # Ambient scene must have a prompt OR beats — nothing to play and
        # nothing to generate from means it airs as silence.
        if scene.ambient:
            has_prompt = scene.prompt is not None and bool(scene.prompt.strip())
            if not has_prompt and not scene.beats:
                report.errors.append(
                    f"scene {scene.id!r}: ambient scene has neither a prompt "
                    f"nor beats")

        _check_ring_tone_and_mood(scene, report)

    # Check lore stems — silently dropping a missing stem starves the
    # improviser of context and produces a take with no lore context.
    pack_lore = pack.lore or {}
    for scene in sorted(pack.scenes.values(), key=lambda s: s.id):
        for stem in scene.lore:
            if stem not in pack_lore:
                report.errors.append(
                    f"scene {scene.id!r}: references unknown lore stem {stem!r}")

    # Check ambient pool entries — pool must name scenes that exist and are
    # ambient; a non-ambient scene in the pool would air it as filler, and a
    # missing scene would crash the scheduler at runtime.
    for entry_id in pack.ambient_pool:
        if entry_id not in pack.scenes:
            report.errors.append(
                f"ambient pool references unknown scene {entry_id!r}")
        elif not pack.scenes[entry_id].ambient:
            report.errors.append(
                f"ambient pool references non-ambient scene {entry_id!r}")

    # Check cast usage
    spoken = set()
    for scene in pack.scenes.values():
        for beat in scene.beats:
            if beat.speaker is not None:
                spoken.add(beat.speaker)
    _check_cast_usage(pack, spoken, report)
    
    log.info("validated campaign pack %s: %d errors, %d warnings",
             pack.name, len(report.errors), len(report.warnings))
    
    return report


def _reachable_scene_ids(pack: CampaignPack) -> set[str]:
    """Find all scenes reachable from start_scene using BFS."""
    visited = set()
    to_visit = deque([pack.start_scene])

    while to_visit:
        scene_id = to_visit.popleft()
        if scene_id in visited:
            continue
            
        visited.add(scene_id)
        
        # Skip if scene doesn't exist (already caught by start_scene check)
        if scene_id not in pack.scenes:
            continue
            
        scene = pack.scenes[scene_id]
        
        # Add default_next if it exists and points to a valid scene
        if scene.default_next and scene.default_next in pack.scenes:
            to_visit.append(scene.default_next)
            
        # Add branch targets
        for branch in scene.branches:
            if branch.next in pack.scenes:
                to_visit.append(branch.next)
    
    return visited


def _check_branches(scene: Scene, pack: CampaignPack, report: ValidationReport) -> None:
    """Check branch integrity within a scene."""
    branch_ids = set()
    for branch in scene.branches:
        if branch.id in branch_ids:
            report.errors.append(f"scene {scene.id!r}: duplicate branch id {branch.id!r}")
        else:
            branch_ids.add(branch.id)

        if branch.next not in pack.scenes:
            report.errors.append(
                f"scene {scene.id!r}: branch target {branch.next!r} not found among scenes")

        # `weight` is reserved: scene_graph.WeightedRandomSelector compares it
        # numerically, so a stringy YAML value would only fail mid-show.
        if WEIGHT_KEY in branch.when:
            weight = branch.when[WEIGHT_KEY]
            if isinstance(weight, bool) or not isinstance(weight, (int, float)):
                report.errors.append(
                    f"scene {scene.id!r}: branch {branch.id!r} has non-numeric "
                    f"weight {weight!r}")
            elif weight < 0:
                report.errors.append(
                    f"scene {scene.id!r}: branch {branch.id!r} has negative "
                    f"weight {weight!r}")


    # Check default_next exists if set
    if scene.default_next and scene.default_next not in pack.scenes:
        report.errors.append(
            f"scene {scene.id!r}: default_next {scene.default_next!r} not found among scenes")
    
    # An ambient scene linked into the graph plays as plot and lets the spine
    # advance into filler. Catch both the branch target and the default_next
    # case.
    if scene.default_next:
        target = pack.scenes.get(scene.default_next)
        if target is not None and target.ambient:
            report.errors.append(
                f"scene {scene.id!r}: default_next {scene.default_next!r} is "
                f"an ambient scene — it must not be linked into the graph")
    for branch in scene.branches:
        target = pack.scenes.get(branch.next)
        if target is not None and target.ambient:
            report.errors.append(
                f"scene {scene.id!r}: branch {branch.id!r} targets "
                f"{branch.next!r}, an ambient scene — it must not be linked "
                f"into the graph")


def _check_beats(scene: Scene, pack: CampaignPack, report: ValidationReport) -> None:
    """Check beat integrity within a scene."""
    for beat in scene.beats:
        # Check beat kind
        if beat.kind not in VALID_BEAT_KINDS:
            report.errors.append(
                f"scene {scene.id!r}: unknown beat kind {beat.kind!r}")
        
        # Check speaker exists (unless it's a pane beat without speaker)
        if beat.speaker is not None and beat.speaker not in pack.cast:
            report.errors.append(
                f"scene {scene.id!r}: unknown speaker {beat.speaker!r}")
        
        # Check action beats
        if beat.kind == "action":
            if beat.primitive is None:
                report.errors.append(
                    f"scene {scene.id!r}: action beat has no primitive")
            elif beat.primitive not in pack.primitives:
                report.errors.append(
                    f"scene {scene.id!r}: action beat references disabled primitive {beat.primitive!r}")
        
        # Check narration/dialogue beats have text
        if beat.kind in ("narration", "dialogue"):
            if not beat.text:
                report.errors.append(
                    f"scene {scene.id!r}: {beat.kind} beat has no text")
            # A variant pool is a list of alternate phrasings cycled at render
            # time. A blank variant reaches TTS as silence mid-scene, and only
            # on the run where the cycle happens to land on it — so each
            # variant must be non-blank. Action and pane beats legitimately
            # carry no text at all, so they are never checked here.
            for variant in beat.texts:
                if not isinstance(variant, str) or not variant.strip():
                    report.errors.append(
                        f"scene {scene.id!r}: {beat.kind} beat has a blank "
                        f"variant in its pool ({variant!r})")
        
        # Check pane beats have show
        if beat.kind == "pane":
            if not beat.show:
                report.errors.append(
                    f"scene {scene.id!r}: pane beat has no show")


def _check_cast_usage(pack: CampaignPack, spoken: set[str], report: ValidationReport) -> None:
    """Check that all cast members are used."""
    for member_id in pack.cast:
        if member_id not in spoken:
            report.warnings.append(f"cast member {member_id!r} never speaks")


def _check_ring_tone_and_mood(scene: Scene, report: ValidationReport) -> None:
    """Check ring_tone/mood values come from their closed vocabularies
    (campaign.pack.RING_TONES / MOODS). Invalid values are errors, not
    warnings: an unknown tag silently drops a scene out of every selection
    filter that checks it, which is a scene going permanently unused, not a
    cosmetic issue."""
    for value in scene.ring_tone:
        if value not in RING_TONES:
            report.errors.append(
                f"scene {scene.id!r}: unknown ring_tone {value!r} "
                f"(expected one of {sorted(RING_TONES)})")
    for value in scene.mood:
        if value not in MOODS:
            report.errors.append(
                f"scene {scene.id!r}: unknown mood {value!r} "
                f"(expected one of {sorted(MOODS)})")
