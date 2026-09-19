"""End-to-end source-shape test: run the retargeted scene generator against
TWO different input sources and record how each one's output looks.

  Source A: ashioridCampaign  (structured Obsidian vault; folder = kind hints)
            gated against the ashiorid_1 pack (real cast + lore vocabulary)
  Source B: Harry_Potter_all_books_preprocessed.txt  (flat 1.09M-word .txt)
            gated against the minimal hp_test_pack (HP trio cast + 2 lore notes)

For each source we author ONE spine scene and ONE ambient scene, so the test
shows the two axes that matter:
  * source shape  ->  structured vault vs. flat monolithic text
  * scene kind    ->  spine (beats + variant pools) vs. ambient (prompt only)

Concurrency: SEQUENTIAL (the §9.1.1 hard limit) — one model in flight, the
next only after the previous returns. Output is captured to
`.claude/prompts/hp_source_shape_test/comparison.json`.
"""
import json
import logging
import pathlib
import sys

# Wire `app/` + generator `src/` onto sys.path BEFORE importing the modules.
REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
UTILITY_ROOT = REPO_ROOT / "utilities" / "3LayersWeeklyGeneration"
for path in (REPO_ROOT / "app", UTILITY_ROOT / "src"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import concurrent_llm            # noqa: E402
import scene_writer as sw        # noqa: E402
import source_adapter as sa      # noqa: E402
from author_scenes import author_spine, author_ambient  # noqa: E402
from pack_gate import check_stage                    # noqa: E402
import campaign.pack as cp                           # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)7s %(name)s: %(message)s")
log = logging.getLogger("shapetest")

ASHIORID_BASE = REPO_ROOT / "campaigns" / "ashiorid_1"
HP_BASE       = REPO_ROOT / ".claude" / "prompts" / "hp_test_pack"
OUT_ROOT      = REPO_ROOT / ".claude" / "prompts" / "hp_source_shape_test"
OUT_ROOT.mkdir(parents=True, exist_ok=True)

ASH_SOURCE = pathlib.Path("/home/secus/codeProjects/ashioridCampaign/DnD Campaign")
HP_SOURCE  = REPO_ROOT / "sourceworks" / "Harry_Potter_all_books_preprocessed.txt"

MODEL = "hermes3:70b"


def cast_ids(base: pathlib.Path) -> list[str]:
    return sorted(f.stem for f in (base / "cast").glob("*.yaml"))


def shape_of(scene: dict) -> dict:
    beats = scene.get("beats") or []
    return {
        "id": scene.get("id"),
        "ambient": bool(scene.get("ambient")),
        "has_enter_narration": bool(scene.get("enter_narration")),
        "n_beats": len(beats),
        "variant_pools": sum(1 for b in beats
                             if isinstance(b, dict) and isinstance(b.get("text"), list)),
        "speakers": sorted({b.get("speaker") for b in beats if isinstance(b, dict) and b.get("speaker")}),
        "lore": scene.get("lore"),
        "source": scene.get("source"),
    }


def author_one(client, note, mode, base, run_id, batch):
    cast = cast_ids(base)
    if mode == "spine":
        return author_spine(client, note, base_pack_dir=base, run_id=run_id,
                            batch=batch, model=MODEL, cast_ids=cast)
    return author_ambient(client, note, base_pack_dir=base, run_id=run_id,
                          batch=batch, model=MODEL)


def run_source(tag, source, base, notes_all, spine_note, ambient_note,
               client, run_id):
    staging = OUT_ROOT / tag / "proposed"
    staging.mkdir(parents=True, exist_ok=True)
    batch_ = "2.3." + tag

    results = {}
    for mode, note in (("spine", spine_note), ("ambient", ambient_note)):
        try:
            scene = author_one(client, note, mode, base, run_id, batch_)
            path = sw.write_scene(staging, f"{tag}-{mode}-{scene['id']}.yaml", scene)
            results[mode] = {
                "status": "authored",
                "note_kind": note.kind,
                "note_rel_path": note.rel_path,
                "note_chars": len(note.text),
                "scene": shape_of(scene),
                "written_to": str(path),
            }
            log.info("[%s/%s] AUTHORED %s from %s (%s)",
                     tag, mode, scene["id"], note.rel_path, note.kind)
        except Exception as exc:
            results[mode] = {"status": f"FAILED: {exc}"}
            log.error("[%s/%s] FAILED: %s", tag, mode, exc)

    gate = check_stage(base, staging)
    # Full gate detail for the report.
    return {
        "tag": tag,
        "source_path": str(source),
        "base_pack": str(base),
        "base_pack_scenes": len(list((base / "scenes").glob("*.yaml"))),
        "base_cast": sorted(f.stem for f in (base / "cast").glob("*.yaml")),
        "base_lore": sorted(f.stem for f in (base / "lore").glob("*.md")) if (base / "lore").is_dir() else [],
        "n_source_notes": len(notes_all),
        "scenes": results,
        "gate": gate.summary(),
        "gate_ok": gate.ok,
        "gate_errors": list(gate.errors),
        "gate_warnings": list(gate.warnings),
    }


def main():
    client = concurrent_llm.from_profile({
        "provider": "ollama",
        "base_url": "http://127.0.0.1:11434",
        "model": MODEL,
        "temperature": 0.7,
        "max_tokens": 1500,
        "timeout_s": 900,
        "num_ctx": None,
    })
    run_id = sw.fresh_run_id("source_shape_test")
    log.info("run_id=%s  model=%s", run_id, MODEL)

    # ── Source A: ashiorid ────────────────────────────────────────────
    ash_notes = sa.load_source(ASH_SOURCE)
    from collections import Counter
    ash_kinds = Counter(n.kind for n in ash_notes)
    # One spine from real plot material, one ambient from a location.
    ash_spine_note = next((n for n in ash_notes if n.kind == "plot" and n.id.startswith("plots-age-of-war")), ash_notes[0])
    ash_amb_note = next((n for n in ash_notes if n.kind == "location"), ash_notes[0])
    record_ash = run_source("ashiorid", ASH_SOURCE, ASHIORID_BASE, ash_notes,
                            ash_spine_note, ash_amb_note, client, run_id)
    record_ash["note_kind_distribution"] = ash_kinds.most_common()
    record_ash["chosen_spine_note"] = ash_spine_note.rel_path
    record_ash["chosen_ambient_note"] = ash_amb_note.rel_path

    # ── Source B: Harry Potter ────────────────────────────────────────
    hp_notes = sa.load_source(HP_SOURCE, target_chars=4000)
    hp_spine_note = hp_notes[0]            # the very first block of Book 1
    hp_amb_note = hp_notes[min(40, len(hp_notes) - 1)]  # a later, distinct block
    record_hp = run_source("hp", HP_SOURCE, HP_BASE, hp_notes,
                           hp_spine_note, hp_amb_note, client, run_id)
    record_hp["note_kind_distribution"] = Counter(n.kind for n in hp_notes).most_common()
    record_hp["chosen_spine_note"] = hp_spine_note.rel_path + f"#{hp_spine_note.id}"
    record_hp["chosen_ambient_note"] = hp_amb_note.rel_path + f"#{hp_amb_note.id}"

    record = {"run_id": run_id, "model": MODEL, "sources": [record_ash, record_hp]}

    out = OUT_ROOT / "comparison.json"
    out.write_text(json.dumps(record, indent=2, ensure_ascii=False))
    log.info("wrote %s", out)

    client.close()


if __name__ == "__main__":
    main()
