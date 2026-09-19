"""Ashiorid 168h week — build out the spine + ambient, seeded from canon.

Continues the pack's existing spine AFTER malvakar-riddle (the pack's only
open spine endpoint) with the real continuation stories, then authors a batch
of ambient filler scenes. Everything is STAGED (never written into the tracked
pack) and gated through load_pack() + validate_pack(); promotion is a separate,
explicit human step (plan §1.4 — no auto-write into campaigns/).

Design invariants honoured:
  * §9.1.1 — ONE 70b call in flight at a time; fully sequential.
  * neighbour-committed-in-context — the FIRST continuation scene is authored
    with malvakar-riddle's committed output in its prompt; each following scene
    gets the previous NEW scene's committed output. Independent per-item LLM
    calls cannot produce a coherent chain — they hallucinate identity.
  * closed vocabulary — speakers from registered cast only, lore stems from
    lore/ only. Enforced locally in author_scenes AND by the gate.
  * loop-closure is the OPERATOR's choice at promotion — the staged chain ends
    OPEN (last scene default_next=None); only the forward splice (malvakar-
    riddle -> first new scene) is proposed. (matches spine_chain's design and
    the plan's "on promotion the operator decides loop-closure vs open-end")
"""
import json
import logging
import pathlib
import shutil
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parents[2]
for _p in (REPO / "app", REPO / "utilities" / "3LayersWeeklyGeneration" / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import concurrent_llm             # noqa: E402
import source_adapter as sa       # noqa: E402
import scene_writer as sw         # noqa: E402
import campaign.pack as cp        # noqa: E402
import yaml                       # noqa: E402
from author_scenes import (       # noqa: E402
    author_ambient, author_spine, known_vocabs, lore_stems,
    pack_primitives, AuthoringError)
from spine_chain import build_previous_scene_context, link_spines  # noqa: E402
from pack_gate import check_stage  # noqa: E402

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)7s %(name)s: %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("ashiorid_week")

BASE = REPO / "campaigns" / "ashiorid"
STAGE = REPO / ".claude" / "prompts" / "ashiorid_week_stage"
STAGE_SPINE = STAGE / "spine"     # flat — _overlay globs non-recursively
STAGE_AMB = STAGE / "ambient"     # flat
SOURCE = (REPO.parent / "ashioridCampaign" / "DnD Campaign")

# Spine continuation — the real top-level campaign stories in narrative order,
# continuing FROM the pack's open endpoint (malvakar-riddle). Side quests and
# locations are ambient filler, deliberately NOT spine. Names are the exact
# note stems the adapter emits (verified against sa.load_source output).
CONTINUATION = ["Shewolf of Idra", "Lighthouse Campaign", "Mutant History - Alien World"]
SEED_SCENE_FILE = "scenes/15-malvakar-riddle.yaml"

# Ambient filler — notes that advance nothing; variety without touching story.
AMBIENT_NOTES = [
    "Plots/Side Quests/Sarah's Simple Inn.md",
    "Plots/Side Quests/The Mage Hole.md",
    "Plots/Side Quests/Mystery Boy and Poison Ring.md",
    "Plots/Side Quests/Dwarf Related Issues.md",
    "Plots/Side Quests/WerePomeranian.md",
    "Plots/Side Quests/Re-Adventure - Unclaimed Loot.md",
    "Plots/Side Quests/Falling Man.md",
    "Locations/Henderson.md",
    "Locations/Losira.md",
    "Locations/Malmont Town Profile.md",
    "NPCs/Azra.md",
    "NPCs/Grovley.md",
]

MODEL = "hermes3:70b"
MAX_TOKENS = 4096
MAX_RETRIES = 3


def _scene_stats(scene):
    beats = scene.get("beats") or []
    spoken = len((scene.get("enter_narration") or "").split())
    pools = speakers = 0
    for b in beats:
        if not isinstance(b, dict):
            continue
        t = b.get("text")
        if isinstance(t, list):
            spoken += max((len(x.split()) for x in t if isinstance(x, str)), default=0)
            if len(t) >= 2:
                pools += 1
        elif isinstance(t, str):
            spoken += len(t.split())
        if b.get("speaker"):
            speakers += 1
    return {
        "id": scene["id"],
        "n_beats": len(beats),
        "spoken_words": spoken,
        "variant_pools": pools,
        "approx_min_at_150wpm": round(spoken / 150.0, 2),
        "speakers": sorted({str(b["speaker"]) for b in beats if isinstance(b, dict) and b.get("speaker")}),
        "default_next": scene.get("default_next"),
    }


def build_spine(client, notes, run_id):
    log.info("=== SPINE CONTINUATION (seeded from %s) ===", SEED_SCENE_FILE)
    seed = yaml.safe_load((BASE / SEED_SCENE_FILE).read_text())
    prev_ctx = build_previous_scene_context(seed)
    log.info("seed context from %s (%d words)", seed["id"], len(prev_ctx.split()))

    cast_ids = sorted(known_vocabs(BASE))
    prims = sorted(pack_primitives(BASE))
    log.info("closed vocab  cast=%s  lore=%s  primitives=%s",
             cast_ids, sorted(lore_stems(BASE)), prims)
    # Tell the model the enabled action primitives up front — the local
    # filter will drop action beats it invents anyway, but a first-pass hit
    # costs two less dropped beats per scene.
    prim_hint = (
        "\n\nIf (and only if) you write an action beat, its `primitive` MUST "
        f"be one of: {prims}. Do not invent other primitive names; "
        "prefer narration and dialogue beats."
    )

    # by_name keyed on exact note stem (adapter-emitted); CONTINUATION names
    # are the exact stems — verified against sa.load_source output.
    by_name = {}
    for n in notes:
        stem = n.rel_path.rsplit("/", 1)[-1]
        if stem.endswith(".md"):
            stem = stem[:-3]
        by_name.setdefault(stem, n)
    scenes = []
    picked = 0
    for i, name in enumerate(CONTINUATION):
        note = by_name.get(name)
        if note is None:
            log.warning("continuation note not found: %s — skipped", name)
            continue
        picked += 1
        log.info("[%d/%d] authoring spine from %s (%d words)",
                 picked, len(CONTINUATION), note.rel_path, len(note.text.split()))
        scene = author_spine(
            client, note, base_pack_dir=BASE, run_id=run_id,
            batch=f"2.3.spine.{i}", model=MODEL, cast_ids=cast_ids,
            max_retries=MAX_RETRIES, system_extra=prev_ctx + prim_hint)
        scenes.append(scene)
        prev_ctx = build_previous_scene_context(scene)
        st = _scene_stats(scene)
        log.info("  -> %s | %d beats | %d spoken words (~%.1fm) | speakers=%s",
                 st["id"], st["n_beats"], st["spoken_words"], st["approx_min_at_150wpm"],
                 ",".join(st["speakers"]))

    if scenes:
        # Link the new chain; LAST scene ends OPEN (None) — closure is the
        # operator's promotion decision, not baked into staged output.
        link_spines(scenes)
    for i, scene in enumerate(scenes, start=1):
        sw.write_scene(STAGE_SPINE, f"{i:03d}-{scene['id']}.yaml", scene)

    if scenes:
        # Proposal lives at the STAGE root — NEVER inside a gated staging
        # dir: _overlay copies every *.yaml in the proposed dir as a scene,
        # and a proposal has no `id:` (the very failure the gate caught on
        # run 1).
        proposal = {
            "forward_splice": {"scene": seed["id"], "default_next": scenes[0]["id"]},
            "chain": [seed["id"]] + [s["id"] for s in scenes],
            "new_last_scene": scenes[-1]["id"],
            "loop_closure_operator_choice": (
                {"scene": scenes[-1]["id"], "default_next": seed["id"]}
                if len(scenes) else None),
            "note": ("Forward splice only is proposed. Loop-closure (new last -> seed) "
                     "is LEFT TO THE OPERATOR at promotion, per the ring. The staged "
                     "chain currently ends OPEN."),
            "run_id": run_id,
        }
        (STAGE / "splice.proposal.yaml").write_text(
            yaml.safe_dump(proposal, sort_keys=False, allow_unicode=True))
    log.info("spine staged %d scenes under %s (proposal at %s)",
             len(scenes), STAGE_SPINE, STAGE / "splice.proposal.yaml")
    return scenes


def build_ambient(client, notes, run_id):
    log.info("=== AMBIENT BATCH (filler, independent) ===")
    by_ref = {n.rel_path: n for n in notes}
    out, fail = [], 0
    for i, ref in enumerate(AMBIENT_NOTES):
        note = by_ref.get(ref)
        if note is None:
            log.warning("ambient note not found: %s — skipped", ref)
            fail += 1
            continue
        log.info("[%d/%d] authoring ambient from %s", i + 1, len(AMBIENT_NOTES), ref)
        try:
            scene = author_ambient(
                client, note, base_pack_dir=BASE, run_id=run_id,
                batch=f"2.3.ambient.{i}", model=MODEL, max_retries=MAX_RETRIES)
            fname = sw.next_scene_filename(STAGE_AMB, prefix="a") + f"-{scene['id']}.yaml"
            sw.write_scene(STAGE_AMB, fname, scene)
            out.append(scene)
            log.info("  -> %s | prompt ~%d words", scene["id"],
                     len((scene.get("prompt") or "").split()))
        except AuthoringError as exc:
            fail += 1
            log.warning("  ambient %s FAILED: %s", ref, exc)
    log.info("ambient done: %d authored, %d failed", len(out), fail)
    return out


def gate_both():
    """check_stage per flat staged dir; then a COMBINED overlay must load_pack."""
    g_spine = check_stage(BASE, STAGE_SPINE)
    g_amb = check_stage(BASE, STAGE_AMB)
    log.info("GATE spine dir: %s | errors=%s warnings=%s",
             g_spine.summary(), list(g_spine.errors), list(g_spine.warnings))
    log.info("GATE ambient dir: %s | errors=%s warnings=%s",
             g_amb.summary(), list(g_amb.errors), list(g_amb.warnings))
    with tempfile.TemporaryDirectory(prefix="ash_week_combine_") as td:
        tmp = shutil.copytree(BASE, pathlib.Path(td) / "both",
                              ignore=shutil.ignore_patterns("generated", "__pycache__"))
        (tmp / "scenes").mkdir(exist_ok=True)
        for d in (STAGE_SPINE, STAGE_AMB):
            for f in sorted(d.glob("*.yaml")) + sorted(d.glob("*.yml")):
                # Scene files only — never a proposal/meta file (which has
                # no `id:` and would fail load_pack in the overlay).
                if f.name in ("splice.proposal.yaml",):
                    continue
                shutil.copy2(f, tmp / "scenes" / f.name)
        lp = cp.load_pack(tmp)
        log.info("COMBINED overlay load_pack: %d scenes, %d cast — CLEAN",
                 len(lp.scenes), len(lp.cast))
    return g_spine, g_amb


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--spine-only", action="store_true",
                    help="author spine continuation, gate it, exit (skip ambient)")
    ap.add_argument("--ambient-only", action="store_true",
                    help="author the ambient filler batch, gate it, exit (skip spine)")
    args = ap.parse_args()
    STAGE.mkdir(parents=True, exist_ok=True)
    STAGE_SPINE.mkdir(parents=True, exist_ok=True)
    STAGE_AMB.mkdir(parents=True, exist_ok=True)
    run_id = sw.fresh_run_id("ashiorid_week")
    log.info("run_id=%s model=%s", run_id, MODEL)
    notes = sa.load_source(SOURCE)
    log.info("source notes loaded: %d", len(notes))

    client = concurrent_llm.from_profile({
        "provider": "ollama", "base_url": "http://127.0.0.1:11434",
        "model": MODEL, "temperature": 0.7, "max_tokens": MAX_TOKENS,
        "timeout_s": 900, "num_ctx": None,
    })
    try:
        spine = [] if args.ambient_only else build_spine(client, notes, run_id)
        ambient = [] if args.spine_only else build_ambient(client, notes, run_id)
        g_spine, g_amb = gate_both()
        gate_ok = (args.ambient_only or g_spine.ok) and (args.spine_only or g_amb.ok)

        summary = {
            "run_id": run_id,
            "spine": [_scene_stats(s) for s in spine],
            "ambient": [
                {"id": a["id"], "ambient": a.get("ambient"),
                 "prompt_words": len((a.get("prompt") or "").split()),
                 "n_beats": len(a.get("beats") or []),
                 "default_next": a.get("default_next")} for a in ambient],
            "gate_spine": {"ok": g_spine.ok, "errors": list(g_spine.errors),
                           "warnings": list(g_spine.warnings)},
            "gate_ambient": {"ok": g_amb.ok, "errors": list(g_amb.errors),
                             "warnings": list(g_amb.warnings)},
            "staged_under": str(STAGE),
        }
        (STAGE / "build_summary.json").write_text(
            json.dumps(summary, indent=2, ensure_ascii=False))

        print("\n" + "=" * 72)
        print(f"ASHIORID WEEK BUILD | run_id={run_id}")
        print("=" * 72)
        total = sum(s["spoken_words"] for s in summary["spine"])
        for s in summary["spine"]:
            print(f"  SPINE  {s['id']:34} {s['n_beats']:>2} beats  {s['spoken_words']:>4}w  "
                  f"~{s['approx_min_at_150wpm']}m  pools={s['variant_pools']}  "
                  f"spk={','.join(s['speakers'])}")
        print(f"  SPINE TOTAL: {len(summary['spine'])} scenes, {total} spoken words "
              f"({round(total / 9000, 2)} h at 150wpm)")
        aw = sum(a["prompt_words"] for a in summary["ambient"])
        print(f"  AMBIENT   : {len(summary['ambient'])} definitions, {aw} prompt words")
        print(f"  GATE      : spine={'PASS' if g_spine.ok else 'FAIL'}  "
              f"ambient={'PASS' if g_amb.ok else 'FAIL'}")
        print(f"  STAGED    : {STAGE}  (NOT promoted — that is the explicit human step)")
        return 0 if gate_ok else 1
    finally:
        client.close()


if __name__ == "__main__":
    sys.exit(main())
