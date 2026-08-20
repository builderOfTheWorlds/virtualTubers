"""
Closed-set vocabulary validator for the 3-layer offline content generator.

Three of the pipeline's layers are driven by a local LLM that is asked to emit
structured YAML. A model asked for "lore stems relevant to this scene" will
cheerfully invent one. Today an invented stem is silently DROPPED —
`LLMImproviser` looks it up in the pack's lore dict, misses, and moves on
without a word, so a slot that was supposed to carry context quietly carries
none. The same failure mode applies to story-state keys: a slot conditioned on
a state key nothing in the runtime can ever set produces a take that can never
air.

Both are the same bug — an open set where a closed one was intended — so one
module catches both, and every layer validates through it.
"""
import logging
from typing import Dict, List, Optional, Set, Union

log = logging.getLogger(__name__)


class VocabularyError(ValueError):
    """Raised when vocabulary validation fails."""
    pass


class Vocabulary:
    """Holds the five closed sets for validation."""

    def __init__(self, lore_stems: Set[str], scene_ids: Set[str], flags: Set[str],
                 moods: Set[str], carry_keys: Set[str]):
        self.lore_stems = frozenset(lore_stems)
        self.scene_ids = frozenset(scene_ids)
        self.flags = frozenset(flags)
        self.moods = frozenset(moods)
        self.carry_keys = frozenset(carry_keys)

    @classmethod
    def from_config_and_pack(cls, config: Dict, pack) -> "Vocabulary":
        """
        Build from a loaded generation.yaml mapping and a CampaignPack.

        Reads config["state"]["flags"], ["moods"], ["carry_keys"], and
        `pack.lore` / `pack.scenes` (both dicts — take their keys).

        Raises VocabularyError when config has no "state" block, and when
        `carry_keys` is not a subset of `flags` (the config documents it
        as a subset; an out-of-set carry key would survive a loop reset
        as a flag no slot can ever name). The message must name the
        offending key.
        """
        log.debug("Vocabulary.from_config_and_pack called with config=%s, pack=%s", config, pack)
        if "state" not in config:
            msg = "config missing 'state' block"
            log.error(msg)
            raise VocabularyError(msg)

        state = config["state"]
        flags = set(state.get("flags", []))
        moods = set(state.get("moods", []))
        carry_keys = set(state.get("carry_keys", []))

        if not carry_keys.issubset(flags):
            bad_key = next(iter(carry_keys - flags))
            msg = f"carry_keys contains '{bad_key}' which is not in flags"
            log.error(msg)
            raise VocabularyError(msg)

        lore_stems = set(pack.lore.keys())
        scene_ids = set(pack.scenes.keys())

        return cls(lore_stems, scene_ids, flags, moods, carry_keys)

    def unknown_lore(self, stems: Optional[List[str]]) -> List[str]:
        """The subset of `stems` not in lore_stems, IN INPUT ORDER."""
        log.debug("unknown_lore called with stems=%s", stems)
        if stems is None:
            return []
        result = [stem for stem in stems if stem not in self.lore_stems]
        log.debug("unknown_lore returning %s", result)
        return result

    def unknown_state_keys(self, keys: Optional[List[str]]) -> List[str]:
        """The subset of `keys` that is neither a declared flag nor the literal string 'mood'."""
        log.debug("unknown_state_keys called with keys=%s", keys)
        if keys is None:
            return []
        result = [key for key in keys if key != "mood" and key not in self.flags]
        log.debug("unknown_state_keys returning %s", result)
        return result

    def unknown_carry_keys(self, keys: Optional[List[str]]) -> List[str]:
        """The subset of `keys` not in carry_keys."""
        log.debug("unknown_carry_keys called with keys=%s", keys)
        if keys is None:
            return []
        result = [key for key in keys if key not in self.carry_keys]
        log.debug("unknown_carry_keys returning %s", result)
        return result

    def unknown_scene_refs(self, scene_ids: Optional[List[str]]) -> List[str]:
        """The subset not in scene_ids."""
        log.debug("unknown_scene_refs called with scene_ids=%s", scene_ids)
        if scene_ids is None:
            return []
        result = [scene_id for scene_id in scene_ids if scene_id not in self.scene_ids]
        log.debug("unknown_scene_refs returning %s", result)
        return result

    def validate_condition(self, condition: Dict[str, Union[bool, str]]) -> List[str]:
        """
        Validate one take's `conditions:` mapping, returning a list of problem strings.

        Rules:
          - An empty mapping is the NEUTRAL condition. Always valid.
          - Key "mood": the value must be in `moods`.
          - Key that is a declared flag: the value must be a bool.
            Note `isinstance(True, int)` is True in Python — check for
            bool specifically, so 1 and "yes" are rejected.
          - Any other key: unknown.
        Each problem string must contain the offending key or value.
        """
        log.debug("validate_condition called with condition=%s", condition)
        problems = []

        if not condition:
            log.debug("validate_condition returning empty list for neutral condition")
            return []

        for key, value in condition.items():
            if key == "mood":
                if value not in self.moods:
                    known_moods = sorted(self.moods)
                    problems.append(
                        f"unknown mood value '{value}' (known: {', '.join(known_moods)})"
                    )
            elif key in self.flags:
                if not isinstance(value, bool):
                    problems.append(
                        f"flag '{key}' must have a boolean value, not {type(value).__name__}"
                    )
            else:
                problems.append(f"unknown condition key '{key}'")

        log.debug("validate_condition returning %s", problems)
        return problems

    def validate_slot(self, slot: Dict) -> List[str]:
        """
        The whole-slot pass every Layer 2 caller uses.

        Returns a list of problem strings, [] when the slot is clean.
        """
        log.debug("validate_slot called with slot=%s", slot)
        problems = []

        kind = slot.get("kind")
        if kind != "spine" and kind != "ambient":
            problems.append(f"unknown slot kind '{kind}' (must be 'spine' or 'ambient')")
            log.debug("validate_slot returning %s", problems)
            return problems

        if kind == "spine":
            scene_ref = slot.get("scene_ref")
            if scene_ref is not None and scene_ref not in self.scene_ids:
                known_scenes = sorted(self.scene_ids)
                problems.append(
                    f"unknown scene reference '{scene_ref}' (known: {', '.join(known_scenes)})"
                )

        if kind == "ambient":
            # Check lore stems
            lore_stems = slot.get("lore", [])
            unknown_lore = self.unknown_lore(lore_stems)
            for stem in unknown_lore:
                known_lore = sorted(self.lore_stems)
                problems.append(
                    f"unknown lore stem '{stem}' (pack knows: {', '.join(known_lore)})"
                )

            # Check sensitivity
            sensitivity = slot.get("sensitivity")
            if sensitivity is not None and sensitivity not in {"none", "tone", "flags"}:
                problems.append(f"unknown sensitivity value '{sensitivity}'")

            # Check depends_on
            depends_on = slot.get("depends_on", [])
            unknown_state = self.unknown_state_keys(depends_on)
            for key in unknown_state:
                problems.append(f"unknown state key '{key}' in depends_on")

            # Check that depends_on is empty unless sensitivity is "flags"
            if depends_on and sensitivity != "flags":
                problems.append(
                    f"depends_on must be empty when sensitivity is not 'flags' "
                    f"(sensitivity: '{sensitivity}')"
                )

        log.debug("validate_slot returning %s", problems)
        return problems


def check(problems: List[str], where: str) -> None:
    """
    Module-level. `problems` is a list of strings from any of the above.

    Empty -> return None, no logging. Non-empty -> log at ERROR, then
    raise VocabularyError whose message contains BOTH `where` (the
    caller's location string, e.g. "seg-001 slot-004") and every problem
    string.
    """
    log.debug("check called with problems=%s, where=%s", problems, where)
    if not problems:
        return

    msg = f"{where}: {'; '.join(problems)}"
    log.error(msg)
    raise VocabularyError(msg)
