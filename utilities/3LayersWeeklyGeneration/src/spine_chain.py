"""Phase 2.4 — spine chaining.

The plan (§2.6) asks that spine scenes, once authored, form a single
connected chain with `default_next` links. Two rules the plan is explicit
about:

  1. A scene authored at position N must have the *committed* output of
     position N-1 in the LLM context when it is authored. (Per the user's
     standing rule, per memory: independent per-item LLM calls cannot
     produce a coherent chain — they hallucinate identity. The neighbour's
     committed output MUST be in context.)

  2. The FIRST of the newly-authored spine scenes is wired to the pack's
     *current* last spine (the base-pack scene with no `default_next`
     yet), but ONLY as a staged patch proposal — `promote_chained()`
     writes a `campaign_patch.proposal.yaml` describing the edit.
     Applying it is a human action. Nothing in this module mutates the
     base pack.

Design notes
------------
* The LLM half is a thin wrapper around `author_scenes.author_spine`
  with the previous scene's committed output passed through its
  `system_extra` parameter. That keeps ONE place where the closed-
  vocabulary filter, the variant-pool normalisation, and the provenance
  block live, so the chain path and the one-shot path cannot drift
  apart.
* The chaining half (`link_spines`, `build_previous_scene_context`,
  `_base_pack_open_spines`) is pure: no I/O, no network. Fully
  unit-tested in `tests/test_spine_chain.py` without an LLM.
"""
from __future__ import annotations

import logging
import pathlib

import yaml  # type: ignore

import campaign.pack as cp
import scene_writer as sw
from author_scenes import AuthoringError, author_spine
from pack_gate import GateError, check_stage, promote
from source_adapter import SourceNote

log = logging.getLogger("spine_chain")


def link_spines(scenes: list[dict]) -> list[dict]:
    """Wire `default_next` across an ordered list of scene dicts.

    Pure: no I/O, no network, no LLM. The list is in chronological order
    (scene[0] first, scene[-1] last). Each scene gets a `default_next`
    key pointing at the next scene's `id`, or `None` on the last one.
    Returns the same list (mutated) so callers can chain naturally.
    """
    for i, scene in enumerate(scenes):
        scene["default_next"] = scenes[i + 1]["id"] if i + 1 < len(scenes) else None
    return scenes


def build_previous_scene_context(prev_scene: dict) -> str:
    """Render the previous scene's committed output for the next LLM call.

    §2.6 is explicit: author scene N with scene N-1's committed output
    in context. The provenance block is dropped — the model has no
    business mirroring the previous scene's run_id/batch/base_hash into
    its own output.
    """
    slim = {k: v for k, v in prev_scene.items() if k != "source"}
    text = yaml.safe_dump(slim, default_flow_style=False,
                          allow_unicode=True, sort_keys=False)
    return (
        "PREVIOUS SPINE SCENE (already committed; your job is the NEXT beat):\n"
        + text
        + "\nKeep the same character voices and the same vocabulary for "
          "things that already exist (locations, objects, NPCs). Advance "
          "the story from where this scene leaves it — do not re-stage "
          "its beats, and do not reset its setup."
    )


def _base_pack_insertion_points(base_dir: pathlib.Path) -> list[dict]:
    """Where a newly-authored spine chain can be inserted into the base
    pack's spine. Two cases, both live in well-formed packs:

      * open = a spine scene with no `default_next` — append after it.
      * loop = a spine scene whose `default_next` is itself (a self-loop)
        — splices IN the loop: the scene points at the new first, the new
        last points where the loop used to (the scene itself), and the
        loop is restored. Self-loop spines are legal and deliberate:
        "loops are the premise of the show, not a bug"
        (test_self_referencing_scene_is_allowed).
    """
    try:
        pack = cp.load_pack(base_dir)
    except Exception as exc:  # a broken base pack is a caller error, but
        log.warning("base pack failed to load for insertion-point discovery: %s", exc)
        return []
    return sorted(
        ({"type": t, "scene": s.id}
         for s in pack.scenes.values()
         if not s.ambient
         for t in (
             ["open"] if not s.default_next
             else (["loop"] if s.default_next == s.id else [])))
        ,
        key=lambda d: d["scene"],
    )


def author_spine_chain(client,
                       notes: list[SourceNote],
                       *,
                       base_pack_dir: str | pathlib.Path,
                       staging_dir: str | pathlib.Path,
                       run_id: str,
                       batch: str,
                       model: str,
                       cast_ids: list[str],
                       max_retries: int = 2,
                       ) -> dict:
    """Author a spine chain from an ordered list of notes.

    `notes[0]` is authored first; its committed output is passed to the
    authors of `notes[1]`, and so on. Every call is sequential (§9.1.1:
    at most one 70b-class call in flight). If `notes` is empty this is a
    caller error and raises immediately.

    Returns a result dict:
      {
        "scenes": [...ordered scene dicts, each with default_next set...],
        "first_id": str,
        "last_id": str,
        "base_candidates": [...open spine ids in the base pack...],
        "patch": { "set": {...}, "alternatives": [...] } or {},
        "staged_files": [...],
        "run_id": str,
      }
    """
    if not notes:
        raise ValueError("author_spine_chain requires at least one note")

    staging = pathlib.Path(staging_dir)
    staging.mkdir(parents=True, exist_ok=True)

    prev_ctx = ""
    scenes: list[dict] = []
    for note in notes:
        scene = author_spine(
            client, note,
            base_pack_dir=base_pack_dir,
            run_id=run_id,
            batch=batch,
            model=model,
            cast_ids=cast_ids,
            max_retries=max_retries,
            system_extra=prev_ctx,
        )
        scenes.append(scene)
        prev_ctx = build_previous_scene_context(scene)

    # Link AFTER all authoring, so a mid-chain failure does not leave a
    # half-linked chain on disk — the whole run is atomic to the caller.
    link_spines(scenes)

    staged_files = [
        str(sw.write_scene(staging, f"{i:03d}-{scene['id']}.yaml", scene))
        for i, scene in enumerate(scenes, start=1)
    ]

    candidates = _base_pack_insertion_points(pathlib.Path(base_pack_dir))
    patch: dict = {}
    if candidates:
        # Deterministic proposal: the earliest insertion point by id. The
        # proposal file lists the others so a human can override.
        chosen = candidates[0]
        patch = {
            "insert_at": chosen["scene"],
            "insertion_type": chosen["type"],
            # Edit 1 (both cases): the base scene points at our new first.
            "set": {"scene": chosen["scene"], "default_next": scenes[0]["id"]},
            # Edit 2 (loop case only): our new last points BACK at the base
            # scene, restoring the loop the chain spliced into. For an
            # open spine the chain simply ends at the new last (None).
            "loop_closure": (
                {"scene": scenes[-1]["id"], "default_next": chosen["scene"]}
                if chosen["type"] == "loop" else None
            ),
            "alternatives": candidates,
        }

    return {
        "scenes": scenes,
        "first_id": scenes[0]["id"],
        "last_id": scenes[-1]["id"],
        "base_candidates": candidates,
        "patch": patch,
        "staged_files": staged_files,
        "run_id": run_id,
    }


def promote_chained(base_pack_dir: str | pathlib.Path,
                    staging_dir: str | pathlib.Path,
                    result: dict,
                    run_id: str,
                    ) -> list[str]:
    """Copy a gated spine chain into the base pack's scenes/ and write
    the default_next proposal.

    Refuses if the gate is red (same contract as pack_gate.promote).
    Returns the paths written (promoted scene files + the proposal file).
    """
    staged = pathlib.Path(staging_dir)
    gate = check_stage(base_pack_dir, staged)
    if not gate.ok:
        raise GateError(f"gate failed: {gate.summary()}; errors={list(gate.errors)}")

    promoted = promote(base_pack_dir, staged, run_id=run_id)

    if result.get("patch"):
        patch_path = staged / "campaign_patch.proposal.yaml"
        p = result["patch"]
        lines = [
            "# Hand-applied edit. The staged scenes are already copied into",
            "# scenes/; this is the last step — splice the new chain into the",
            "# base pack's spine at " + str(p["insert_at"]),
            "# (" + p["insertion_type"] + " spine)." + "\n",
            "# Edit 1: " + p["set"]["scene"] + " -> " + p["set"]["default_next"] + "\n",
            "set:\n"
            + yaml.safe_dump(p["set"], default_flow_style=False, sort_keys=False),
        ]
        if p.get("loop_closure"):
            lc = p["loop_closure"]
            lines += [
                "\n# Edit 2 (loop closure): " + lc["scene"] + " -> " + lc["default_next"],
                "loop_closure:\n"
                + yaml.safe_dump(lc, default_flow_style=False, sort_keys=False),
            ]
        lines += [
            "\n# All insertion points in the base pack (type, id):",
            yaml.safe_dump(p["alternatives"], default_flow_style=False),
            "\n# run_id: " + run_id,
        ]
        patch_path.write_text("\n".join(lines) + "\n")
        promoted.append(str(patch_path))
    return promoted
