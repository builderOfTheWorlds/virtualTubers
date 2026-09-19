"""Layer 3' — scene authoring. The replacement for `generate_segment_dialogue`.

Given a `SourceNote` from `source_adapter` and an Ollama client, produce ONE
scene dict, gate it against the target pack, and write it to a STAGING
directory. Nothing is written to `campaigns/` by this module; promotion is
an explicit, separate step (`pack_gate.promote`), owned by the run.

Concurrency cap (§9.1.1): at most ONE 70b-class local model in flight at a
time. The loop in `author_scenes` is therefore SEQUENTIAL by design. A
caller that wants multiple in-flight generations can build their own pool
on top of this module's single-scene functions, but must enforce the cap
themselves — this module never fans out.

Usage:

    import concurrent_llm, source_adapter, scene_writer as sw
    from author_scenes import author_scenes

    client = concurrent_llm.from_profile({"provider": "ollama", "model": "hermes3:70b",
                                          "base_url": "http://127.0.0.1:11434",
                                          "temperature": 0.7, "max_tokens": 1500,
                                          "timeout_s": 900, "num_ctx": None})
    notes = source_adapter.load_source("~/codeProjects/ashioridCampaign/DnD Campaign")
    run_id = sw.fresh_run_id("ashiorid")
    results = author_scenes(client, notes, base_pack_dir="campaigns/ashiorid_1",
                            staging_dir="out/proposed_scenes", run_id=run_id,
                            batch="2.3", max_scenes=3)
"""
from __future__ import annotations

import logging
import pathlib
import re

import yaml

from typing import Any, Callable

import scene_writer as sw
import pack_gate
from source_adapter import SourceNote

log = logging.getLogger(__name__)


class AuthoringError(ValueError):
    """Raised when a scene cannot be parsed from a model reply, when a pack
    reference check fails, or when the gate rejects the scene."""


def _strip_fences(text: str) -> str:
    m = re.search(r"```(?:ya?ml)?\s*\n(.*?)```", text, re.DOTALL)
    if m:
        return m.group(1)
    return text.strip()


def _parse_scene_reply(reply: str) -> dict:
    """Parse a model reply into a scene dict. Tolerates:
    - ```yaml fenced content
    - leading prose
    - an outer quoted YAML scalar (the pattern `backfill_continuity_chain.py`
      had to handle — the model wraps the YAML in quotes and the parser
      must un-quote it before feeding to `yaml.safe_load`)
    """
    text = _strip_fences(reply.strip())
    candidates: list[str] = [text]
    if text.startswith('"') and text.endswith('"'):
        candidates.insert(0, text[1:-1].replace('\\"', '"').replace("\\n", "\n"))
    # Also try a block starting at the first line that begins with 'id:'
    m = re.search(r"^\s*(?:(id|title|ambient|beats|enter_narration|prompt)\s*:).*",
                  text, re.MULTILINE)
    if m:
        candidates.append(text[m.start():])

    for cand in candidates:
        if not cand.strip():
            continue
        try:
            parsed = yaml.safe_load(cand)
        except yaml.YAMLError:
            continue
        if isinstance(parsed, dict) and "id" in parsed:
            return parsed
    raise AuthoringError(
        f"could not parse a scene (id required) from model reply:\n{reply[:600]}...")


def known_vocabs(base_pack_dir: str | pathlib.Path) -> set[str]:
    """The set of speaker ids the *gate* will accept for `base_pack_dir`,
    plus every lore stem the gate knows.

    Two sources are intersected:
      * The loaded pack's cast set from `campaign.yaml`'s `gm` + `players:`
        (this is the source of truth for the validator — see
        app/campaign/pack.py:210-214).
      * The file stems under `cast/*.yaml` (a pack that files a
        member but never registers them is malformed; the intersection is
        safe: the gate will reject anything not in both).

    This helper is the shared voice for the local filter — it is where
    `author_scenes` and `spine_chain` get their `allow_speakers` instead
    of each globbing independently (which can drift from the gate).
    """
    import campaign.pack as cp
    base = pathlib.Path(base_pack_dir)
    cast_dir = base / "cast"
    if cast_dir.is_dir():
        file_stems = set(f.stem for f in cast_dir.glob("*.yaml"))
    else:
        file_stems = set()
    try:
        pack = cp.load_pack(base)
        registered = set(pack.cast.keys())
    except Exception:
        # A broken base pack cannot be validated anyway; fall back to the
        # file stems (the old behaviour) and let the gate find the real
        # error.
        registered = file_stems
    return registered & file_stems


def lore_stems(base_pack_dir: str | pathlib.Path) -> set[str]:
    base = pathlib.Path(base_pack_dir)
    lore_dir = base / "lore"
    if not lore_dir.is_dir():
        return set()
    return set(f.stem for f in lore_dir.glob("*.md"))


def pack_primitives(base_pack_dir: str | pathlib.Path) -> set[str]:
    """The action-beat `primitive` ids the gate will accept for
    `base_pack_dir` — the `primitives:` list in campaign.yaml, the same
    source of truth validator.py enforces (an action beat naming a
    disabled primitive is always an error). Shared with the local
    pre-filter so the gate is never the first place a bad primitive is
    discovered (the §9.1.3 failure class, same as speakers/stems)."""
    import campaign.pack as cp
    try:
        pack = cp.load_pack(base_pack_dir)
        return set(pack.primitives)
    except Exception:
        return set()


def _scene_speakers_and_stems(scene: dict) -> tuple[set[str], set[str]]:
    """Extract the set of speakers in beats and the set of lore stems
    declared at the top level — pure read, used for diagnostics."""
    speakers: set[str] = set()
    stems: set[str] = set()
    for beat in scene.get("beats") or []:
        if isinstance(beat, dict) and beat.get("speaker"):
            speakers.add(str(beat["speaker"]))
    for stem in scene.get("lore") or []:
        if isinstance(stem, str):
            stems.add(stem)
    return speakers, stems


def _validate_scene_refs(scene: dict, base_pack_dir: pathlib.Path) -> list[str]:
    """Check speakers and lore stems against the base pack's *registered*
    cast (the same source the validator uses) and its lore notes. Cheap:
    no LLM, no disk writes. Returns a list of problem strings (empty=clean)."""
    problems: list[str] = []
    speakers, stems = _scene_speakers_and_stems(scene)
    known_speakers = known_vocabs(base_pack_dir)
    known = lore_stems(base_pack_dir)
    for s in speakers:
        if s not in known_speakers:
            problems.append(f"speaker {s!r} not in the registered cast "
                            f"(known: {sorted(known_speakers)})")
    for stem in stems:
        if stem not in known:
            problems.append(f"lore stem {stem!r} not in lore/ "
                            f"(known: {sorted(known)})")
    return problems


def _slug_for(scene: dict, index: int) -> str:
    """Derive a scene slug from the LLM's `id` or `title`. Falls back to
    the index when neither is present or invalid."""
    raw = scene.get("id") or scene.get("title") or None
    if raw:
        try:
            return sw.slugify(raw)
        except sw.SceneWriterError:
            pass
    return f"scene-{index}"


def author_ambient(client, note: SourceNote, *,
                   base_pack_dir: str | pathlib.Path,
                   run_id: str, batch: str, model: str,
                   max_retries: int = 2,
                   system_extra: str = "") -> dict:
    """Ask the LLM to turn `note` into an AMBIENT scene (prompt-only, no
    beats, not linked into the graph). Returns a scene dict with the
    provenance block already attached. Raises AuthoringError."""
    system = (
        "You are writing an AMBIENT scene for a game-show TTS pipeline. "
        "Ambient scenes are filler injected between authored spine scenes; "
        "they decide NOTHING and never move the story. They carry `ambient: true` "
        "and a `prompt` only — no `beats`, no `default_next`. The `prompt` must "
        "be a short scene description (a few sentences) that a separate runtime "
        "improviser will expand into a few short spoken lines. Write the prompt "
        "for the ear — this show's TTS voice reads your words verbatim, so "
        "sentences must be short and self-contained. Reply with a complete "
        "YAML mapping containing: `id` (lowercase hyphenated slug), `title` "
        "(human-readable), `ambient: true`, `prompt` (a block scalar), "
        "`lore` (a list of lore stems from the target pack, empty if none). "
        "Do NOT invent lore stems. No other keys. No backticks, no prose."
        + (f"\n\n{system_extra}" if system_extra else "")
    )
    user = (
        f"Source note (title: {note.title}, kind: {note.kind}, path: {note.rel_path})\n"
        f"---\n{note.text}\n---\n\n"
        "Author one ambient scene from this note. It must be injectable anywhere in "
        "the arc: it must not resolve a question, reveal a secret, or move the "
        "party. Reply with the YAML only."
    )

    last_error: Exception | None = None
    for attempt in range(1, max_retries + 1):
        try:
            reply = client.complete(system, [{"role": "user", "content": user}])
            scene = _parse_scene_reply(reply)
            scene["id"] = _slug_for(scene, 1)
            scene.setdefault("ambient", True)
            # Enforce the ambient invariant: no beats, no default_next.
            scene.pop("beats", None)
            scene.pop("default_next", None)
            # Enforce the closed-lore vocabulary locally (shared source of
            # truth, so the gate can never disagree with the filter).
            allow_stems = lore_stems(base_pack_dir)
            scene["lore"] = [s for s in (scene.get("lore") or []) if isinstance(s, str) and s in allow_stems]
            scene["source"] = sw.provenance_block(
                run_id=run_id, batch=batch, model=model,
                base_hash=note.hash, version=f"{scene['id']}@{attempt}")
            return scene
        except AuthoringError as exc:
            last_error = exc
            log.warning("author_ambient attempt %d failed: %s", attempt, exc)
    raise AuthoringError(f"author_ambient gave up after {max_retries} attempts: {last_error}")


def author_spine(client, note: SourceNote, *,
                 base_pack_dir: str | pathlib.Path,
                 run_id: str, batch: str, model: str,
                 cast_ids: list[str],
                 max_retries: int = 2,
                 system_extra: str = "") -> dict:
    """Ask the LLM to turn `note` into a SPINE scene (beats + enter_narration,
    variant pools, `improv: true` where wording is not load-bearing).
    Returns a scene dict with provenance. Raises AuthoringError."""
    system = (
        "You are writing a SPINE scene for a game-show TTS pipeline. The show "
        "replays the spine hundreds of times; the renderer cycles a `text` "
        "LIST (a 'variant pool') on each spoken beat so the same beat sounds "
        "different every airing. Write for the ear — your words are read "
        "verbatim by TTS. Reply with a complete YAML mapping containing: "
        "`id` (lowercase hyphenated slug), `title`, `enter_narration` (a "
        "short spoken-on-entry line), `beats` (a list; each beat has `type` "
        "in {narration, dialogue}, `speaker` which MUST be one of the cast "
        f"ids {cast_ids}, and `text` — a 2-3 entry list of alternate phrasings "
        "for the same content), and `lore` (a list of known lore stems; empty "
        "if none). Do NOT invent speakers, lore stems, or beat types. No "
        "backticks, no prose."
        + (f"\n\n{system_extra}" if system_extra else "")
    )
    base = pathlib.Path(base_pack_dir)
    last_error: Exception | None = None
    for attempt in range(1, max_retries + 1):
        try:
            reply = client.complete(system, [{"role": "user", "content": (
                f"Source note (title: {note.title}, kind: {note.kind}, "
                f"path: {note.rel_path})\n---\n{note.text}\n---\n\n"
                "Author one spine scene from this note — it advances the "
                "story. Every beat's speaker must be one of "
                f"{cast_ids}, and it must carry a 2-3 entry variant pool "
                "under `text` (a list of strings). Reply with the YAML only."
            )}])
            scene = _parse_scene_reply(reply)
            scene["id"] = _slug_for(scene, 1)
            scene.pop("ambient", None)
            # Enforce the closed vocabulary LOCALLY, against the same
            # source the gate uses, so the gate can never be the FIRST
            # place an invented speaker / lore stem is discovered: the
            # §9.1.3 failure class.
            allow_speakers = known_vocabs(base_pack_dir)
            allow_stems = lore_stems(base_pack_dir)
            allow_primitives = pack_primitives(base_pack_dir)
            scene["lore"] = [s for s in (scene.get("lore") or []) if isinstance(s, str) and s in allow_stems]
            def _beat_speakers_ok(beat):
                sp = beat.get("speaker") if isinstance(beat, dict) else None
                if sp is None:
                    return True
                return sp in allow_speakers
            def _beat_primitive_ok(beat):
                # validator.py: an action beat whose primitive is not
                # enabled in the campaign is always an error. Drop it
                # locally (with a warning) instead of letting the gate be
                # the first place an invented primitive is discovered.
                if not isinstance(beat, dict) or beat.get("type") != "action":
                    return True
                return beat.get("primitive") in allow_primitives
            beats = scene.get("beats") or []
            kept = [b for b in beats if _beat_speakers_ok(b) and _beat_primitive_ok(b)]
            if len(kept) != len(beats):
                log.warning("author_spine: dropped %d beat(s) with unknown "
                            "speakers/primitives (registered cast: %s, "
                            "enabled primitives: %s)", len(beats) - len(kept),
                            sorted(allow_speakers), sorted(allow_primitives))
            scene["beats"] = kept

            # Normalize `text:` single-string to list (the variant pool).
            beats = scene.get("beats")
            if isinstance(beats, list):
                for beat in beats:
                    if not isinstance(beat, dict):
                        continue
                    text = beat.get("text")
                    if isinstance(text, str):
                        # Keep a single-variant pool, not a bare string —
                        # matches the existing authored scenes.
                        beat["text"] = [text]
                    elif isinstance(text, list):
                        beat["text"] = [t for t in text if isinstance(t, str) and t.strip()]
                        if not beat["text"]:
                            raise AuthoringError(f"empty variant pool on beat {beat}")

            # Local pre-checks before the gate (cheaper, better error text).
            problems = _validate_scene_refs(scene, base)
            if problems:
                raise AuthoringError("; ".join(problems))

            scene["source"] = sw.provenance_block(
                run_id=run_id, batch=batch, model=model,
                base_hash=note.hash, version=f"{scene['id']}@{attempt}")
            return scene
        except AuthoringError as exc:
            last_error = exc
            log.warning("author_spine attempt %d failed: %s", attempt, exc)
    raise AuthoringError(f"author_spine gave up after {max_retries} attempts: {last_error}")


def author_scenes(client, notes: list[SourceNote], *,
                  base_pack_dir: str | pathlib.Path,
                  staging_dir: str | pathlib.Path,
                  run_id: str, batch: str, model: str,
                  cast_ids: list[str] | None = None,
                  kind_ambience: Callable[[SourceNote], bool] | None = None,
                  max_scenes: int | None = None,
                  verbose: bool = True) -> list[dict]:
    """The sequential loop. One note at a time; never more than one LLM call
    in flight at any moment (the §9.1.1 hard limit).

    Returns a list of scene dicts (the staged ones, in input order). Each is
    already on disk in `staging_dir` and has a `source:` provenance block.
    Notes that fail authoring are skipped and logged at WARNING — the loop
    does NOT halt the run on one bad note unless `max_scenes` is 0 (i.e. the
    operator asks to stop immediately)."""
    base = pathlib.Path(base_pack_dir)
    staging = pathlib.Path(staging_dir)
    staging.mkdir(parents=True, exist_ok=True)

    if cast_ids is None:
        # Default: the pack's registered cast — not a glob — so the model is
        # never *told* to speak as a member who will be rejected by the gate.
        cast_ids = sorted(known_vocabs(base))

    if kind_ambience is None:
        # Default: plot/character/lore notes -> spine; npc/location/item ->
        # ambient filler. Unclassified goes to ambient to be safe (ambience
        # decides nothing, so a wrong guess is cheap).
        _SPINE_KINDS = {"plot", "character", "lore"}
        kind_ambience = lambda note: note.kind not in _SPINE_KINDS

    results: list[dict] = []
    for i, note in enumerate(notes):
        if max_scenes is not None and len(results) >= max_scenes:
            break
        try:
            if kind_ambience(note):
                scene = author_ambient(client, note, base_pack_dir=base,
                                       run_id=run_id, batch=batch, model=model)
                prefix = "a"
            else:
                scene = author_spine(client, note, base_pack_dir=base,
                                     run_id=run_id, batch=batch, model=model,
                                     cast_ids=cast_ids)
                prefix = ""
            prefix_num = sw.next_scene_filename(staging, prefix=prefix)
            filename = f"{prefix_num}-{scene['id']}.yaml"
            sw.write_scene(staging, filename, scene)
            results.append(scene)
            if verbose:
                log.info("authored %s (kind=%s, rel=%s) -> %s/%s",
                         scene["id"], note.kind, note.rel_path, staging, filename)
        except AuthoringError as exc:
            log.warning("skipping note %s (%s): %s", note.id, note.rel_path, exc)
    return results
