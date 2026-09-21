"""Prompt builder for the D&D-agents benchmark.

Reads the real `campaigns/ashiorid` pack (cast sheets + lore + scenes) and
produces the *same shape* of prompt the live character agents will send,
just with the `lore` / `transcript` content sized to a token target. We
don't need the actual story to be coherent to measure tok/s — we need it
to be realistic in length and distribution.
"""
from __future__ import annotations

import json
import pathlib
from dataclasses import dataclass
from typing import Any, TypedDict

import yaml

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[3]
DEFAULT_PACK = "ashiorid"


class PromptBundle(TypedDict, total=False):
    """Return shape of `build_character_prompt` / `build_gm_prompt`.
    `prompt_tokens_estimate` is a *sizing* estimate (char/3.4) used to
    decide whether to pad the lore block; the *actual* prompt token
    count is what the host reports (`prompt_tokens`), not this number."""
    system: str
    user: str
    prompt_tokens_estimate: int


@dataclass
class CastSheet:
    member_id: str
    name: str
    archetype: str
    system_prompt: str


@dataclass
class SceneContext:
    scene_id: str
    canon_goal: str
    expected_beats: list[str]
    direction_text: str


def _pack_dir(pack_name: str = DEFAULT_PACK) -> pathlib.Path:
    return PROJECT_ROOT / "campaigns" / pack_name


def list_cast(pack_name: str = DEFAULT_PACK) -> list[str]:
    cast_dir = _pack_dir(pack_name) / "cast"
    if not cast_dir.is_dir():
        return []
    return sorted(p.stem for p in cast_dir.glob("*.yaml") if p.stem != "gm")


def load_cast_sheet(member_id: str, pack_name: str = DEFAULT_PACK) -> CastSheet:
    p = _pack_dir(pack_name) / "cast" / f"{member_id}.yaml"
    data = yaml.safe_load(p.read_text(encoding="utf-8"))
    return CastSheet(
        member_id=member_id,
        name=data["name"],
        archetype=data.get("archetype", ""),
        system_prompt=data.get("system_prompt", ""),
    )


def list_lore_stems(pack_name: str = DEFAULT_PACK) -> list[str]:
    lore_dir = _pack_dir(pack_name) / "lore"
    if not lore_dir.is_dir():
        return []
    return sorted(p.stem for p in lore_dir.glob("*.md"))


def load_lore(stem: str, pack_name: str = DEFAULT_PACK, limit_words: int | None = None
              ) -> str:
    p = _pack_dir(pack_name) / "lore" / f"{stem}.md"
    text = p.read_text(encoding="utf-8").strip()
    if limit_words:
        words = text.split()
        if len(words) > limit_words:
            text = " ".join(words[:limit_words])
    return text


def list_scenes(pack_name: str = DEFAULT_PACK) -> list[str]:
    """Find scenes/<scene_id>.yaml files in the pack."""
    scenes_dir = _pack_dir(pack_name) / "scenes"
    if not scenes_dir.is_dir():
        return []
    return sorted(p.stem for p in scenes_dir.glob("*.yaml"))


def load_scene(scene_id: str, pack_name: str = DEFAULT_PACK) -> dict:
    p = _pack_dir(pack_name) / "scenes" / f"{scene_id}.yaml"
    return yaml.safe_load(p.read_text(encoding="utf-8"))


def _words_to_tokens_estimate(words: int) -> int:
    """Rough word→token ratio for English prose. We use this only to
    *size* the filler blocks so 8k / 16k targets are approximate — the
    benchmark will measure the *actual* prompt token count from Ollama's
    response, not this estimate, so the estimate accuracy doesn't matter
    for the metric, only for the shape of the prompt."""
    return int(words * 1.35)


def _estimate_tokens(text: str) -> int:
    """Very rough char→token estimate for sizing prompts. Not used for
    reporting — the host's `prompt_tokens` field is the source of truth."""
    return int(len(text) / 3.4)


# Real lore stems — we don't need the actual story to be coherent; we
# need to fill a target token budget with the same *kind* of prose we
# actually use. The list below is a small pool of real proper nouns
# from the pack (Ashiorid, Malmont, Malvakar, Bahadur...) so the
# filler looks plausible without claiming it's real canon.
def _synthetic_lore_block(target_tokens: int) -> str:
    """Deterministic synthetic lore text sized roughly to the target so
    the benchmark prompt hits the token budget the design doc cares
    about (8k / 16k). Token mix mirrors real pack prose: short
    sentences, proper nouns, em-dashes; deterministic so a repeat run
    is byte-identical and therefore comparable week-over-week."""
    stems = [
        "Ashiorid", "Malmont", "Malvakar", "Bahadur", "Ord",
        "Muad'Malik", "the door home", "the microfracture", "the Event",
        "the Begene Program", "the Begene vault", "the Vault of the Broken Sigil",
        "the Broken Sigil", "the Amulet of Wonder", "Malmont's tower",
        "the Moonwell", "the Moonwell gate", "the anchor line",
        "the anchor line that was broken",
    ]
    phrases = [
        "is a thing of such long standing that its name has outlived the people who named it.",
        "was cut into the stone by hands that are now dust.",
        "has not opened since the Event closed behind the Bahadur.",
        "carries a weight no living being can set down.",
        "has been misread, misrecorded, and mistranslated more often than it has spoken truly.",
        "bears a mark no one in Ashiorid can date with confidence.",
        "bore, in the days before the sealing, a light no one now believes was natural.",
        "is not, despite every account, what the first record-setter thought it was.",
        "has survived the last three of the vault's openings intact, though each opener did not.",
    ]
    sentences: list[str] = []
    approx_tokens = 0
    i = 0
    while approx_tokens < target_tokens:
        subject = stems[i % len(stems)]
        phrase = phrases[(i // len(stems)) % len(phrases)]
        s = f"{subject} {phrase}"
        sentences.append(s)
        approx_tokens += _estimate_tokens(s)
        i += 1
    return " ".join(sentences)


def build_character_prompt(*, sheet: CastSheet, scene_direction: str,
                           committed_transcript_lines: list[str],
                           lore_stems: list[str] | None = None,
                           target_context_tokens: int | None = 8192,
                           pack_name: str = DEFAULT_PACK,
                           ) -> "PromptBundle":
    """Assemble the full per-character-agent LLM call the live §4
    protocol would send:

        system:  sheet + standing instruction
        user:    scene contract (framed as fiction) + direction + lore +
                 committed transcript + instruction

    The returned `prompt_tokens_estimate` is a rough char/3.4 count for
    pre-sizing; the real value is what the host reports (`prompt_tokens`).
    """
    system = f"You are {sheet.name}, {sheet.archetype}.\n{sheet.system_prompt}"
    system += ("\n\nYou reply with exactly one spoken line, in character, "
               "with no name label, no quotation marks and no stage "
               "directions. Stay in your character. Yes-and the GM's "
               "direction — commit to what the GM describes, add your "
               "character's true angle to it. Never speak for another "
               "character. Never break meta.")

    lore_parts = []
    if lore_stems:
        for stem in lore_stems:
            try:
                lore_parts.append(f"### {stem}\n{load_lore(stem, pack_name)}")
            except FileNotFoundError:
                continue
    lore_block = "\n\n".join(lore_parts)

    # If the real lore is short of the target, pad with synthetic lore so
    # the prompt hits the target token budget the design doc cares about.
    if target_context_tokens:
        current_estimate = _estimate_tokens(system + lore_block + scene_direction
                                            + "\n".join(committed_transcript_lines))
        gap = target_context_tokens - current_estimate
        if gap > 800:  # only pad if there's a real gap
            lore_block = (lore_block + "\n\n"
                          + _synthetic_lore_block(gap) if lore_block
                          else _synthetic_lore_block(gap))
    transcript = "\n".join(committed_transcript_lines) or "(none yet)"

    user = (
        "=== SCENE DIRECTION (from the GM) ===\n"
        f"{scene_direction}\n\n"
        "=== UNLOCKED LORE (you may reference this, nothing else) ===\n"
        f"{lore_block or '(none unlocked yet)'}\n\n"
        "=== COMMITTED TRANSCRIPT (in order — you may reference any line above) ===\n"
        f"{transcript}\n\n"
        "=== YOUR TASK ===\n"
        "React as your character to the GM's direction. You have "
        "committed to what the GM describes and now add your own true "
        "angle. Reply with exactly one spoken line, in character, no "
        "name label, no quotation marks, no stage directions."
    )

    return {
        "system": system,
        "user": user,
        "prompt_tokens_estimate": (
            _estimate_tokens(system + user)
        ),
    }


def build_gm_prompt(*, canon_goal: str, expected_beats: list[str],
                    committed_transcript_lines: list[str],
                    cast_members: list[str] | None = None,
                    target_context_tokens: int | None = 16000,
                    pack_name: str = DEFAULT_PACK,
                    ) -> "PromptBundle":
    """GM-agent prompt — the one with the *full* world state (full pack
    lore, all six sheets, the arc carry, the committed transcript). We
    don't model the arc carry here because it's per-run data the live
    system would carry; the size proxy is the pack lore + all cast
    sheets + the committed transcript + the scene contract, which is the
    dominant cost in the GM's context window."""
    cast_names = cast_members or list_cast(pack_name)
    sheet_lines: list[str] = []
    for mid in cast_names:
        try:
            s = load_cast_sheet(mid, pack_name)
            sheet_lines.append(f"### {s.name}\n{s.system_prompt}")
        except FileNotFoundError:
            continue
    cast_block = "\n\n".join(sheet_lines)

    lore_parts = [f"### {stem}\n{load_lore(stem, pack_name, limit_words=220)}"
                  for stem in list_lore_stems(pack_name)]
    lore_block = "\n\n".join(lore_parts)

    transcript = "\n".join(committed_transcript_lines) or "(scene just started)"

    # If the real pack is still short of the target, pad as above.
    if target_context_tokens:
        body_estimate = _estimate_tokens(
            "\n".join(sheet_lines) + lore_block + transcript + canon_goal
            + "\n".join(expected_beats))
        gap = target_context_tokens - body_estimate
        if gap > 800:
            extra = _synthetic_lore_block(gap)
            lore_block = (lore_block + "\n\n" + extra) if lore_block else extra

    system = (
        "You are the Game Master. You hold the whole campaign world, "
        "every cast member's sheet, every unlocked lore entry, the arc "
        "carry, and every committed transcript line up to this moment. "
        "You narrate, direct beats, adjudicate replies against the "
        "scene contract, and issue retakes with a specific reason. You "
        "are the only seat that may reference world-state no one else "
        "knows. Reply with exactly one GM block: a short scene "
        "direction followed by a comma-separated beat plan naming the "
        "seats you expect to act. Do not narrate the characters' speech "
        "yourself."
    )
    user = (
        f"=== SCENE CONTRACT ===\n"
        f"canon_goal: {canon_goal}\n"
        f"must_resolve beats:\n"
        + "\n".join(f"  - {b}" for b in expected_beats)
        + "\n\n"
        f"=== THE PARTY (every sheet; you alone hold all of them) ===\n"
        f"{cast_block}\n\n"
        f"=== WORLD LORE (unlocked only to you) ===\n"
        f"{lore_block}\n\n"
        f"=== COMMITTED TRANSCRIPT ===\n"
        f"{transcript}\n\n"
        "=== YOUR TASK ===\n"
        "Write the next GM direction. Steer the scene toward the "
        "canonical goal. Name the seats you are directing by id, and "
        "state the must_resolve beat you expect them to hit this round."
    )

    return {
        "system": system,
        "user": user,
        "prompt_tokens_estimate": (
            _estimate_tokens(system + user)
        ),
    }


def load_transcript_example_lines(n_lines: int = 8,
                                  pack_name: str = DEFAULT_PACK
                                  ) -> list[str]:
    """Generate a small plausible committed-transcript sample. Used only
    for padding when the real in-scene transcript would otherwise be
    empty. Format: `<speaker>: <line>` — same as the live system's
    `observe()` append."""
    cast_names = [c for c in list_cast(pack_name)] or ["Chadwick", "Leena"]
    if len(cast_names) < 2:
        cast_names = cast_names + ["Leena"]
    templates = [
        "We don't open a sealed door until someone tells us why it was sealed.",
        "The lock's a Begene cut. Someone's been here before, and they "
        "didn't want us to follow.",
        "I can open it. The question's whether we should.",
        "There's a sigil above the latch that isn't in any record I "
        "trust. Leave it be.",
        "The record says this door has broken every hand that's turned "
        "it. I'm not betting mine.",
        "Open it, or let me. One of us has to be the one who turns it.",
        "I'm not the one who opens it. Someone with less to lose has to "
        "be.",
    ]
    out: list[str] = []
    for i in range(n_lines):
        who = "GM" if i % 4 == 0 else cast_names[i % len(cast_names)]
        line = templates[i % len(templates)]
        out.append(f"{who}: {line}")
    return out
