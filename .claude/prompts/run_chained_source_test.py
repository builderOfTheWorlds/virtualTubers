"""Live chained-spine run on both test sources (Phase 2.4).

Two spine scenes per source, authored SEQUENTIALLY by hermes3:70b.
Scene 2 is authored WITH scene 1's committed output in context.
Staged only. Not promoted.
"""
import json
import logging
import pathlib
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
UTILITY_ROOT = REPO_ROOT / "utilities" / "3LayersWeeklyGeneration"
for _p in (REPO_ROOT / "app", UTILITY_ROOT / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import concurrent_llm                       # noqa: E402
import source_adapter as sa                 # noqa: E402
import scene_writer as sw                   # noqa: E402
from author_scenes import known_vocabs      # noqa: E402
from spine_chain import author_spine_chain  # noqa: E402
from pack_gate import check_stage           # noqa: E402

logging.basicConfig(level=logging.INFO,
                    format="%(levelname)7s %(name)s: %(message)s")
log = logging.getLogger("chainrun")

ASH_BASE = REPO_ROOT / "campaigns" / "ashiorid_1"
HP_BASE = REPO_ROOT / ".claude" / "prompts" / "hp_test_pack"
OUT = REPO_ROOT / ".claude" / "prompts" / "hp_source_shape_test"
ASH_SOURCE = REPO_ROOT.parent / "ashioridCampaign" / "DnD Campaign"
HP_SOURCE = REPO_ROOT / "sourceworks" / "Harry_Potter_all_books_preprocessed.txt"
MODEL = "hermes3:70b"


def shape(scene):
    beats = scene.get("beats") or []
    speakers = set()
    for b in beats:
        if isinstance(b, dict) and b.get("speaker"):
            speakers.add(str(b["speaker"]))
    return {
        "id": scene["id"],
        "default_next": scene.get("default_next"),
        "n_beats": len(beats),
        "variant_pools": sum(1 for b in beats
                             if isinstance(b, dict) and isinstance(b.get("text"), list)),
        "speakers": sorted(speakers),
        "lore": scene.get("lore"),
        "source_version": (scene.get("source") or {}).get("version"),
    }


def run_one(tag, source, base, notes, client, run_id):
    staging = OUT / tag / "chain" / "proposed"
    staging.mkdir(parents=True, exist_ok=True)
    cast_ids = sorted(known_vocabs(base))
    log.info("[%s] registered cast: %s", tag, cast_ids)
    res = author_spine_chain(
        client, notes,
        base_pack_dir=base, staging_dir=staging,
        run_id=run_id, batch="2.4." + tag, model=MODEL,
        cast_ids=cast_ids, max_retries=3,
    )
    gate = check_stage(base, staging)
    return {
        "tag": tag,
        "source_path": str(source),
        "base_pack": str(base),
        "chosen_notes": [n.rel_path for n in notes],
        "base_open_spines": res["base_candidates"],
        "patch_proposal": res["patch"],
        "scenes": [shape(s) for s in res["scenes"]],
        "staged_files": res["staged_files"],
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
        "max_tokens": 4096,
        "timeout_s": 900,
        "num_ctx": None,
    })
    run_id = sw.fresh_run_id("chained_shape_test")
    log.info("run_id=%s model=%s", run_id, MODEL)

    ash_notes_all = sa.load_source(ASH_SOURCE)
    ash_plot = [n for n in ash_notes_all if n.kind == "plot"]
    ash_spine = ash_plot[:2]

    hp_notes = sa.load_source(HP_SOURCE, target_chars=4000)
    hp_spine = [hp_notes[0], hp_notes[min(200, len(hp_notes) - 1)]]

    records = {
        "run_id": run_id,
        "model": MODEL,
        "sources": [
            run_one("ashiorid", ASH_SOURCE, ASH_BASE, ash_spine, client, run_id),
            run_one("hp", HP_SOURCE, HP_BASE, hp_spine, client, run_id),
        ],
    }
    out = OUT / "chained_comparison.json"
    out.write_text(json.dumps(records, indent=2, ensure_ascii=False))
    log.info("wrote %s", out)

    for src in records["sources"]:
        print("\n=== " + src["tag"] + " ===")
        print("  notes: " + " | ".join(src["chosen_notes"]))
        for s in src["scenes"]:
            print("  scene " + s["id"]
                  + " -> default_next=" + str(s["default_next"])
                  + "  beats=" + str(s["n_beats"])
                  + "  pools=" + str(s["variant_pools"])
                  + "  cast=" + str(s["speakers"]))
        print("  base open spines: " + str(src["base_open_spines"]))
        print("  gate: " + src["gate"])
        for e in src["gate_errors"]:
            print("    ERR: " + e)

    client.close()


if __name__ == "__main__":
    main()
